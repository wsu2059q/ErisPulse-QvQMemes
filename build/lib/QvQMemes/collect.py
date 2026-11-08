"""自动收录（Collector）：旁听 ``qvq.observe`` 的 images，去重下载入库并异步视觉打标签。

链路（零概率、全智能——收录是旁路观察，不产生任何概率行为）：
1. ``on_observe``：只读载荷 ``images``（每条消息最多取 3 张），快速校验后
   spawn 后台任务串行处理，绝不阻塞观察回调、绝不 event.done、绝不抛错；
2. 指纹去重：URL 字符串 + 内容 sha256 双指纹（KV ``QvQMemes:seen``，
   FIFO 容量上限 ``SEEN_CAP``）；
3. 每会话每日收录上限（``collect_per_chat_daily``，KV ``QvQMemes:daily``）；
4. http(s)/file URL 串行下载入库（失败保留 URL 引用，仍可参与发送与打标签）；
5. ``tag_via_vision`` 开启时 spawn 异步打标签：经
   ``QvQChat.describe_image(url, prompt)``——服务缺失/无视觉模型/解析失败
   一律暂记「未分类」，不影响收录与发送。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from . import texts
from .library import IMAGE_EXTS, SEEN_CAP, UNTAGGED, NullLogger

SEEN_KEY = "QvQMemes:seen"
DAILY_KEY = "QvQMemes:daily"
DOWNLOAD_SCHEMES = ("http://", "https://", "file://")
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
FETCH_TIMEOUT = 15.0
MAX_IMAGES_PER_MESSAGE = 3  # 契约：qvq.observe 载荷 images ≤3
TAG_LIMIT = 6


def _fetch_bytes(url: str, timeout: float = FETCH_TIMEOUT) -> bytes:
    """同步抓取图片字节（阻塞，调用方经 asyncio.to_thread 串行执行）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "ErisPulse-QvQMemes/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(MAX_DOWNLOAD_BYTES + 1)


def _ext_of(url: str) -> str:
    try:
        suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    except Exception:
        suffix = ""
    return suffix if suffix in IMAGE_EXTS else ".png"


def parse_vision_result(text: str) -> tuple[str, list[str]]:
    """解析视觉模型输出：首行=描述，次行=空格分隔标签（确定性规则，非概率）。

    :return: (desc, tags)——tags 为空表示解析失败（调用方保持「未分类」）
    """
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip().strip(" \t·•:：-—|")
        line = re.sub(r"^(描述|desc|标签|tags?)[\s:：]*", "", line, flags=re.I).strip()
        if line:
            lines.append(line)
    if not lines:
        return "", []
    desc = lines[0][:60]
    tokens: list[str] = []
    for line in lines[1:]:
        tokens += [t for t in re.split(r"[\s,，、;；/|]+", line) if t]
    return desc, tokens[:TAG_LIMIT]


class Collector:
    """自动收录器：观察回调（快路径）+ 后台串行下载入库与异步打标签。

    :param sdk: 框架 sdk（storage / module.call；均可缺失，内存/跳级兜底）
    :param cfg: 配置获取 callable（读 auto_collect / collect_per_chat_daily /
                tag_via_vision / library_dir）
    :param library: StickerLibrary 实例
    :param logger: 可选 logger
    :param spawner: 任务派发 callable(coro)（优先 BaseModule.spawn），默认 ensure_future
    """

    def __init__(self, sdk, cfg, library, logger=None, spawner=None):
        self.sdk = sdk
        self._cfg = cfg
        self.library = library
        self.logger = logger if logger is not None else NullLogger()
        self._spawner = spawner or asyncio.ensure_future
        self.tasks: set[asyncio.Task] = set()

    # ==================== 配置读取 ====================

    def _setting(self, key: str, default):
        try:
            value = getattr(self._cfg(), key)
            return default if value is None else value
        except Exception:
            return default

    def _storage(self):
        try:
            return getattr(self.sdk, "storage", None)
        except Exception:
            return None

    # ==================== 观察回调（qvq.observe，快路径） ====================

    async def on_observe(self, data) -> None:
        """qvq.observe 回调：抽取 images 后立即 spawn，绝不抛错、绝不阻塞。"""
        try:
            if not self._setting("auto_collect", True):
                return
            if not isinstance(data, dict):
                return
            images = data.get("images")
            if not isinstance(images, (list, tuple)):
                return
            urls = [str(u).strip() for u in list(images)[:MAX_IMAGES_PER_MESSAGE]
                    if str(u or "").strip()]
            if not urls:
                return
            chat = str(data.get("chat_key") or "") or "unknown"
            self._spawn(self._collect_images(urls, chat))
        except Exception as e:
            self.logger.debug(texts.t("collect_error", error=e))

    def _spawn(self, coro):
        """派发后台任务并登记（on_unload 统一取消）；返回任务。"""
        try:
            task = self._spawner(coro)
        except Exception:
            task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def shutdown(self) -> None:
        """取消所有后台任务（on_unload 调用）。"""
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        self.tasks.clear()

    # ==================== 指纹去重（URL + sha256） ====================

    def _seen_list(self) -> list[str]:
        st = self._storage()
        if st is None:
            return []
        try:
            raw = st.get(SEEN_KEY, [])
        except Exception:
            raw = []
        return [str(x) for x in raw if x] if isinstance(raw, list) else []

    def _mark_seen(self, fingerprints: list[str]) -> None:
        st = self._storage()
        if st is None or not fingerprints:
            return
        try:
            seen = self._seen_list()
            known = set(seen)
            for fp in fingerprints:
                if fp and fp not in known:
                    seen.append(fp)
                    known.add(fp)
            st.set(SEEN_KEY, seen[-SEEN_CAP:])  # FIFO 容量上限
        except Exception as e:
            self.logger.debug(f"mark_seen failed: {e}")

    def _is_seen(self, fp: str) -> bool:
        return fp in set(self._seen_list())

    # ==================== 每会话每日上限 ====================

    def _daily_count(self, chat: str, today: str) -> int:
        st = self._storage()
        if st is None:
            return getattr(self, "_memory_daily", {}).get(f"{chat}|{today}", 0)
        try:
            data = st.get(DAILY_KEY, {})
            return int((data or {}).get(f"{chat}|{today}", 0)) if isinstance(data, dict) else 0
        except Exception:
            return 0

    def _daily_incr(self, chat: str, today: str) -> None:
        key = f"{chat}|{today}"
        st = self._storage()
        if st is None:
            mem = getattr(self, "_memory_daily", None)
            if mem is None:
                mem = self._memory_daily = {}
            mem[key] = mem.get(key, 0) + 1
            return
        try:
            data = st.get(DAILY_KEY, {})
            data = {k: v for k, v in (data or {}).items()
                    if isinstance(k, str) and k.split("|")[-1] == today} if isinstance(data, dict) else {}
            data[key] = int(data.get(key, 0)) + 1
            st.set(DAILY_KEY, data)
        except Exception as e:
            self.logger.debug(f"daily incr failed: {e}")

    def _daily_ok(self, chat: str) -> bool:
        limit = int(self._setting("collect_per_chat_daily", 20) or 0)
        if limit <= 0:
            return True  # 上限关闭（≤0 = 不限）
        today = time.strftime("%Y%m%d")
        return self._daily_count(chat, today) < limit

    # ==================== 收录主流程（后台串行） ====================

    async def _collect_images(self, urls: list[str], chat: str) -> None:
        """串行处理一条消息的 ≤3 张图片（下载串行防 burst）。"""
        for url in urls:
            try:
                if not self._daily_ok(chat):
                    self.logger.info(texts.t("collect_daily_limit",
                                             chat=chat,
                                             limit=int(self._setting("collect_per_chat_daily", 20) or 0),
                                             url=url))
                    return
                if self._is_seen(url):
                    self.logger.debug(texts.t("collect_duplicated", fp=url))
                    continue
                result = await self.collect_url(url, source=chat)
                if result.get("added"):
                    self._daily_incr(chat, time.strftime("%Y%m%d"))
            except Exception as e:
                self.logger.debug(texts.t("collect_error", error=e))

    async def collect_url(self, url_or_path: str, source: str = "manual") -> dict:
        """收录单个 URL/路径（手动收录与自动收录共用入口）。

        - http(s)/file URL：下载落盘后入库（sha256 内容指纹去重；下载失败保留 URL 引用）；
        - 其他字符串：本地路径存在则直接入库，否则按 URL 引用收录（交给视觉端自行取图）。

        :return: ``{"added": bool, "entry": dict|None, "error": str|None}``
        """
        ref = str(url_or_path or "").strip()
        if not ref:
            return {"added": False, "entry": None, "error": "url or path required"}
        lowered = ref.lower()
        if lowered.startswith(DOWNLOAD_SCHEMES):
            try:
                data = await asyncio.to_thread(_fetch_bytes, ref)
                if len(data) > MAX_DOWNLOAD_BYTES:
                    raise ValueError("image too large")
                sha = hashlib.sha256(data).hexdigest()
                if self._is_seen(sha):
                    self._mark_seen([ref])
                    self.logger.debug(texts.t("collect_duplicated", fp=f"sha:{sha[:16]}"))
                    return {"added": False, "entry": None, "error": "duplicate"}
                path = await asyncio.to_thread(self._write_file, data, _ext_of(ref))
                result = self.library.add(path=path, url=ref, source=source)
                if result.get("added"):
                    self._mark_seen([ref, sha])
                    self._after_added(result["entry"])
                return result
            except Exception as e:
                # 下载失败：保留 URL 引用入库（发送端可用 URL 直发；打标签仍可尝试）
                self.logger.info(texts.t("collect_download_failed", url=ref, error=e))
                result = self.library.add(url=ref, source=source)
                if result.get("added"):
                    self._mark_seen([ref])
                    self._after_added(result["entry"])
                return result
        # 非下载协议：本地路径存在则按路径收录（统一 posix 形式，与扫描/落盘一致），否则按 URL 引用收录
        try:
            p = Path(ref)
            is_file = p.is_file()
        except Exception:
            is_file = False
        if is_file:
            result = self.library.add(path=p.as_posix(), source=source)
        else:
            result = self.library.add(url=ref, source=source)
        if result.get("added"):
            self._mark_seen([ref])
            self._after_added(result["entry"])
        return result

    def _write_file(self, data: bytes, ext: str) -> str:
        """落盘到库目录（sha256 前 16 位命名，幂等）。"""
        name = hashlib.sha256(data).hexdigest()[:16] + ext
        root = Path(str(self._setting("library_dir", "data/QvQMemes/stickers")
                        or "data/QvQMemes/stickers"))
        root.mkdir(parents=True, exist_ok=True)
        target = root / name
        target.write_bytes(data)
        return target.as_posix()

    # ==================== 异步视觉打标签 ====================

    def _after_added(self, entry: dict) -> None:
        """收录成功后的打标签调度：开视觉则 spawn，否则记「未分类」。"""
        if not self._setting("tag_via_vision", True):
            self.logger.debug(texts.t("collect_added_no_tag",
                                      id=entry.get("id"), source=entry.get("source")))
            return
        self.logger.debug(texts.t("collect_added",
                                  id=entry.get("id"), source=entry.get("source")))
        self._spawn(self._tag_entry(str(entry.get("id"))))

    async def _tag_entry(self, entry_id: str) -> None:
        """视觉打标签：QvQChat.describe_image 缺失/失败 → 保持「未分类」。"""
        entry = self.library.get(entry_id)
        if entry is None:
            return
        ref = str(entry.get("url") or "") or str(entry.get("path") or "")
        if not ref:
            return
        prompt = texts.t("vision_prompt")
        try:
            text = await self.sdk.module.call("QvQChat", "describe_image",
                                              url=ref, prompt=prompt)
        except Exception as e:
            self.logger.info(texts.t("tag_failed", id=entry_id))
            self.logger.debug(f"describe_image error: {e}")
            return
        desc, tags = parse_vision_result(text)
        if not desc and not tags:
            self.logger.info(texts.t("tag_failed", id=entry_id))
            return
        fields: dict = {}
        if desc:
            fields["desc"] = desc
        if tags:
            fields["tags"] = tags
        updated = self.library.update(entry_id, **fields)
        if updated is None:
            return
        display_tags = " ".join(updated.get("tags") or [UNTAGGED])
        self.logger.info(texts.t("tag_done", id=entry_id,
                                 desc=updated.get("desc") or "", tags=display_tags))
