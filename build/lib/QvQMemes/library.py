"""表情包库（StickerLibrary）：KV 存储 + 语义检索 + 新鲜度调度 + 容量淘汰。

存储：``QvQMemes:lib`` → ``[{id, path, url, desc, tags, source, uses, last_used, added}, ...]``

设计哲学（零概率、全智能）：
- ``search`` 只做确定性的词面匹配打分（desc / tags），"何时发、发哪个"的
  判断交给调用方（QvQChat AI 经 send_sticker 工具）；
- 唯一的排序调整是「新鲜度降权」——``recency_hours`` 窗口内用过的表情包
  分数 ×0.5，这是确定性调度（避免短期重复刷图），不是概率触发；
- 容量超限时按「最久未用」（从未用过的按入库时间）确定性淘汰。
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

LIB_KEY = "QvQMemes:lib"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
UNTAGGED = "未分类"
SEEN_CAP = 5000  # collect.py 共用指纹库容量上限


class NullLogger:
    """logger 不可用时的静默兜底。"""

    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _as_tags(tags) -> list[str]:
    """归一化标签列表：str 拆分 / list 过滤去重；空 → ["未分类"]。"""
    if tags is None:
        return [UNTAGGED]
    if isinstance(tags, str):
        tokens = [t for t in re.split(r"[\s,，、;；/|]+", tags.strip()) if t]
    elif isinstance(tags, (list, tuple, set)):
        tokens = [str(t).strip() for t in tags if str(t).strip()]
    else:
        tokens = []
    seen: set[str] = set()
    ordered = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered or [UNTAGGED]


def _terms(query: str) -> list[str]:
    """把查询拆成词面匹配项（空白/常见标点分隔；整段无分隔时保留原词做子串匹配）。"""
    tokens = [t for t in re.split(r"[\s,，、;；/|·]+", str(query or "").strip()) if t]
    return tokens


def _today_start(now: float) -> float:
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


class StickerLibrary:
    """表情包库：路径/URL 收录、词面检索、使用记账与容量淘汰。

    :param sdk: 框架 sdk（只使用 sdk.storage，缺失时内存兜底）
    :param cfg: 配置获取 callable（返回配置对象，读 library_dir/max_library/recency_hours）
    :param logger: 可选 logger
    """

    def __init__(self, sdk, cfg, logger=None):
        self.sdk = sdk
        self._cfg = cfg
        self.logger = logger if logger is not None else NullLogger()
        self._lock = threading.Lock()
        self._memory: list[dict] | None = None  # storage 不可用时的内存兜底

    # ==================== 配置读取 ====================

    def _setting(self, key: str, default):
        try:
            value = getattr(self._cfg(), key)
            return default if value is None else value
        except Exception:
            return default

    def _library_dir(self) -> str:
        return str(self._setting("library_dir", "data/QvQMemes/stickers") or "data/QvQMemes/stickers")

    # ==================== 持久化（storage 优先，内存兜底） ====================

    def _storage(self):
        try:
            st = getattr(self.sdk, "storage", None)
        except Exception:
            st = None
        return st

    def _read(self) -> list[dict]:
        st = self._storage()
        if st is None:
            return [dict(e) for e in (self._memory or [])]
        try:
            raw = st.get(LIB_KEY, [])
        except Exception:
            raw = []
        if not isinstance(raw, list):
            raw = []
        return [dict(e) for e in raw if isinstance(e, dict)]

    def _write(self, items: list[dict]) -> None:
        st = self._storage()
        if st is None:
            self._memory = [dict(e) for e in items]
            return
        try:
            st.set(LIB_KEY, items)
        except Exception as e:
            self.logger.debug(f"library persist failed, falling back to memory: {e}")
            self._memory = [dict(e) for e in items]

    @staticmethod
    def _next_id(items: list[dict]) -> str:
        seq = 0
        for entry in items:
            eid = str(entry.get("id") or "")
            digits = eid[1:] if eid[:1] in ("m", "M") else ""
            if digits.isdigit():
                seq = max(seq, int(digits))
        return f"m{seq + 1:06d}"

    # ==================== 收录 / 删除 ====================

    def add(self, path: str = "", url: str = "", desc: str = "", tags=None,
            source: str = "", now: float | None = None) -> dict:
        """收录一张表情包（按 path/url 幂等）。

        :return: ``{"added": bool, "entry": {...}}``——重复时 added=False 且 entry 为已有条目
        """
        path = str(path or "").strip()
        url = str(url or "").strip()
        if not path and not url:
            return {"added": False, "entry": None, "error": "path or url required"}
        ts = float(now) if now else time.time()
        with self._lock:
            items = self._read()
            for entry in items:
                if (path and str(entry.get("path") or "") == path) or \
                        (url and str(entry.get("url") or "") == url):
                    return {"added": False, "entry": dict(entry)}
            record = {
                "id": self._next_id(items),
                "path": path,
                "url": url,
                "desc": str(desc or ""),
                "tags": _as_tags(tags),
                "source": str(source or ""),
                "uses": 0,
                "last_used": 0.0,
                "added": ts,
            }
            items.append(record)
            self._write(items)
            return {"added": True, "entry": dict(record)}

    def update(self, entry_id: str, **fields) -> dict | None:
        """更新条目字段（白名单 desc/tags/url/path/source）；返回更新后的条目。"""
        with self._lock:
            items = self._read()
            for entry in items:
                if str(entry.get("id")) == str(entry_id):
                    if "desc" in fields:
                        entry["desc"] = str(fields["desc"] or "")
                    if "tags" in fields:
                        entry["tags"] = _as_tags(fields["tags"])
                    for key in ("url", "path", "source"):
                        if key in fields and fields[key]:
                            entry[key] = str(fields[key])
                    self._write(items)
                    return dict(entry)
        return None

    def remove(self, entry_id: str, delete_file: bool = True) -> dict | None:
        """移除条目；库目录内的文件一并删除（外部路径不删）。"""
        with self._lock:
            items = self._read()
            kept = []
            removed = None
            for entry in items:
                if str(entry.get("id")) == str(entry_id):
                    removed = entry
                else:
                    kept.append(entry)
            if removed is not None:
                self._write(kept)
        if removed is not None and delete_file:
            self._try_delete_file(removed)
        return dict(removed) if removed is not None else None

    def _try_delete_file(self, entry: dict) -> None:
        path = str(entry.get("path") or "")
        if not path:
            return
        try:
            lib_dir = Path(self._library_dir())
            p = Path(path)
            if p.is_relative_to(lib_dir) and p.is_file():
                p.unlink()
        except Exception as e:
            self.logger.debug(f"evicted file delete failed ({path}): {e}")

    # ==================== 查询 ====================

    def get(self, entry_id: str) -> dict | None:
        with self._lock:
            for entry in self._read():
                if str(entry.get("id")) == str(entry_id):
                    return dict(entry)
        return None

    def list(self, limit: int | None = None) -> list[dict]:
        """全部条目拷贝（新→旧）。"""
        with self._lock:
            items = sorted(self._read(), key=lambda e: -float(e.get("added") or 0))
        return [dict(e) for e in (items[:limit] if limit else items)]

    @staticmethod
    def _score(entry: dict, terms: list[str]) -> float:
        desc = str(entry.get("desc") or "").lower()
        tags = [str(t).lower() for t in (entry.get("tags") or [])]
        score = 0.0
        for term in terms:
            tl = term.lower()
            if not tl:
                continue
            if tl in tags:               # 标签全等权重最高
                score += 4.0
            elif any(tl in g for g in tags):  # 标签子串次之
                score += 2.0
            if tl in desc:               # 描述命中
                score += 3.0
        return score

    def search(self, query: str, top_n: int = 5, now: float | None = None) -> list[dict]:
        """语义选图检索：词面匹配打分 + 新鲜度确定性降权（非概率）。

        :return: 按分数降序的条目拷贝列表（带 ``score`` 字段），无匹配返回 []
        """
        terms = _terms(query)
        if not terms:
            return []
        ts = float(now) if now else time.time()
        recency = max(0.0, float(self._setting("recency_hours", 72) or 0)) * 3600.0
        with self._lock:
            items = self._read()
        scored: list[tuple[float, dict]] = []
        for entry in items:
            score = self._score(entry, terms)
            if score <= 0:
                continue
            last_used = float(entry.get("last_used") or 0)
            if recency > 0 and last_used > 0 and (ts - last_used) < recency:
                score *= 0.5  # 新鲜度调度：近期用过的确定性降权
            scored.append((score, entry))
        scored.sort(key=lambda pair: (-pair[0], -float(pair[1].get("added") or 0)))
        top = max(1, int(top_n or 1))
        return [{**dict(entry), "score": round(score, 4)} for score, entry in scored[:top]]

    def mark_used(self, entry_id: str, now: float | None = None) -> bool:
        with self._lock:
            items = self._read()
            for entry in items:
                if str(entry.get("id")) == str(entry_id):
                    entry["uses"] = int(entry.get("uses") or 0) + 1
                    entry["last_used"] = float(now) if now else time.time()
                    self._write(items)
                    return True
        return False

    # ==================== 容量淘汰（最久未用，确定性） ====================

    def evict_to_limit(self, now: float | None = None) -> int:
        """超出 max_library 时按最久未用淘汰（从未用过的按入库时间计），返回淘汰数。"""
        limit = int(self._setting("max_library", 500) or 0)
        if limit <= 0:
            return 0
        with self._lock:
            items = self._read()
            if len(items) <= limit:
                return 0

            def activity(entry: dict) -> float:
                last = float(entry.get("last_used") or 0)
                return last if last > 0 else float(entry.get("added") or 0)

            items.sort(key=activity)  # 旧 → 新
            evicted, kept = items[: len(items) - limit], items[len(items) - limit:]
            self._write(kept)
        for entry in evicted:
            self._try_delete_file(entry)
        return len(evicted)

    # ==================== 目录扫描（启动收录已存在文件） ====================

    def scan_directory(self, directory: str | None = None) -> list[dict]:
        """扫描库目录，把尚未入库的图片文件收进来（按 path 幂等，source="scan"）。"""
        directory = str(directory or self._library_dir())
        root = Path(directory)
        try:
            if not root.is_dir():
                return []
            candidates = [f for f in sorted(root.rglob("*"))
                          if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
        except Exception as e:
            self.logger.warning(f"scan directory failed ({directory}): {e}")
            return []
        if not candidates:
            return []
        ts = time.time()
        added: list[dict] = []
        with self._lock:
            items = self._read()
            known = {str(e.get("path") or "") for e in items}
            for f in candidates:
                path = f.as_posix()
                if path in known:
                    continue
                record = {
                    "id": self._next_id(items),
                    "path": path,
                    "url": "",
                    "desc": "",
                    "tags": [UNTAGGED],
                    "source": "scan",
                    "uses": 0,
                    "last_used": 0.0,
                    "added": ts,
                }
                items.append(record)
                known.add(path)
                added.append(dict(record))
            if added:
                self._write(items)
        return added

    # ==================== 统计 ====================

    def stats(self, now: float | None = None) -> dict:
        ts = float(now) if now else time.time()
        with self._lock:
            items = self._read()
        tag_counts: dict[str, int] = {}
        untagged = 0
        for entry in items:
            tags = [str(t) for t in (entry.get("tags") or [])]
            if not tags or tags == [UNTAGGED]:
                untagged += 1
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        day_start = _today_start(ts)
        added_today = sum(1 for e in items if float(e.get("added") or 0) >= day_start)
        top_used = sorted(
            (e for e in items if int(e.get("uses") or 0) > 0),
            key=lambda e: -int(e.get("uses") or 0),
        )[:5]
        return {
            "total": len(items),
            "max_library": int(self._setting("max_library", 500) or 0),
            "untagged": untagged,
            "added_today": added_today,
            "tag_distribution": dict(sorted(tag_counts.items(), key=lambda kv: -kv[1])),
            "top_used": [
                {"id": e.get("id"), "desc": e.get("desc"), "uses": int(e.get("uses") or 0)}
                for e in top_used
            ],
        }

    def tag_cloud(self, top_n: int = 8) -> list[str]:
        """高频标签（提示"库里有什么"，排除「未分类」占位）。"""
        with self._lock:
            items = self._read()
        counts: dict[str, int] = {}
        for entry in items:
            for tag in (entry.get("tags") or []):
                tag = str(tag)
                if tag and tag != UNTAGGED:
                    counts[tag] = counts.get(tag, 0) + 1
        ranked = [tag for tag, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
        return ranked[:max(1, int(top_n or 1))]

    def pending_tag_ids(self) -> list[str]:
        """待打标签条目（desc 为空且仍是「未分类」）。"""
        with self._lock:
            items = self._read()
        return [
            str(e.get("id"))
            for e in items
            if not str(e.get("desc") or "") and [str(t) for t in (e.get("tags") or [])] == [UNTAGGED]
        ]
