"""对外五服务（ServiceMixin，混入 Main）：send_sticker / sticker_search /
sticker_stats / sticker_add / qvq_settings。

约定（生态桥自动把服务注册为 QvQChat AI 工具）：
- 所有服务输出 str/dict，**绝不抛异常**——任何内部错误都转成可读文本/错误
  字段返回给模型；
- ``send_sticker`` 的服务 description 即「何时发」的唯一引导：语义选图工具，
  判断权完全交给模型，本模块不做任何概率触发；
- ``qvq_settings`` 只作归属标记（返回 ``{"title": ...}``）：配置项由本模块
  ConfigClass（_desc 声明式）声明，QvQChat 端经框架 resolve_config_schema
  自动渲染表单；值合并读取走 ``QvQChat.plugin_config``（Main.on_load 拉取，
  失败静默用默认）。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from . import texts
from .library import UNTAGGED

# 图片扩展名 → HTTP mime（Dashboard 图片代理响应头用）
_IMAGE_MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


def _image_mime(suffix: str) -> str:
    return _IMAGE_MIMES.get(str(suffix or "").lower(), "image/png")


class ServiceMixin:
    """五服务实现（依赖宿主 Main 提供 sdk/logger/_cfg/_library/_collector/_setting/_spawn）。"""

    # ==================== send_sticker（AI 语义选图工具） ====================

    async def send_sticker(self, query: str = "", platform: str = "",
                           target_id: str = "", target_type: str = "group", **_) -> str:
        """根据语义描述从表情包库挑选最优一张并发送。

        search 最优 → ``adapter.Send.To(...).<send_method>(ref)`` → mark_used →
        「已发送：<desc>」；无匹配返回可用标签提示模型换词；任何失败返回错误文本。
        """
        try:
            query = str(query or "").strip()
            platform = str(platform or "").strip()
            target_id = str(target_id or "").strip()
            target_type = str(target_type or "group").strip() or "group"
            if not query:
                return texts.t("send_need_query")
            if not platform or not target_id:
                return texts.t("send_need_target")

            lib = self._library()
            hits = lib.search(query, top_n=1)
            if not hits:
                hints = "、".join(lib.tag_cloud(6))
                if not hints:
                    return texts.t("library_empty")
                return texts.t("send_no_match", query=query, tags=hints)
            best = hits[0]

# 发送前确保有可用的本地文件：URL-only 条目按需下载（适配器的 Image/Voice
# 只接受本地文件或 base64，直接把 http URL 传进去会被当成文件路径打开而失败）
            ref = await self._ensure_local(best)
            if not ref:
                return texts.t("send_failed", error="sticker has neither path nor url")

            adapter = None
            try:
                adapter = self.sdk.adapter.get(platform)
            except Exception:
                adapter = None
            if adapter is None:
                return texts.t("adapter_not_found", platform=platform)

            method = str(self._setting("send_method", "Image") or "Image")
            chain = adapter.Send.To(target_type, target_id)
            send_call = getattr(chain, method, None)
            if not callable(send_call):
                return texts.t("send_failed",
                               error=f"SendDSL method {method} not supported on {platform}")
            result = await send_call(ref)
# 平台返回 failed 不能当成功（此前会标记已用并回「已发送」，实际什么都没发出去）
            if isinstance(result, dict) and str(result.get("status") or "") == "failed":
                error = str(result.get("message") or result.get("wording") or result)[:200]
                self.logger.warning(f"send_sticker failed on {platform}/{target_type}/{target_id}: {error}")
                return texts.t("send_failed", error=error)
            lib.mark_used(str(best.get("id")))
            lib.evict_to_limit()

            desc = str(best.get("desc") or "") or "/".join(str(t) for t in (best.get("tags") or []))
            body = texts.t("send_sent", desc=desc)
            try:
                tail = str(result or "").strip()
            except Exception:
                tail = ""
            if tail and tail != body:
                body = f"{body}\n{tail[:200]}"
            return body
        except Exception as e:
            return texts.t("send_failed", error=e)

    async def _ensure_local(self, entry: dict) -> str:
        """发送引用：本地文件优先；URL-only 条目按需下载落盘并回写 path。

        适配器（OneBot 系）的 Image/Voice 只接受本地文件 / base64 / URL 透传，
        而多数实现会把 http URL 当文件路径打开——所以这里先落地成本地文件。
        下载失败时回退原始引用，让上层返回明确的发送错误而不是静默丢失。
        """
        path = str(entry.get("path") or "")
        if path:
            try:
                if Path(path).is_file():
                    return path
            except Exception:
                pass
        url = str(entry.get("url") or "")
        lowered = url.lower()
        if not lowered.startswith(("http://", "https://", "file://")):
            return url
        try:
            from .collect import MAX_DOWNLOAD_BYTES, _ext_of, _fetch_bytes

            data = await asyncio.to_thread(_fetch_bytes, url)
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise ValueError("image too large")
            root = Path(str(self._setting("library_dir", "data/QvQMemes/stickers")
                            or "data/QvQMemes/stickers"))
            root.mkdir(parents=True, exist_ok=True)
            target = root / (hashlib.sha256(data).hexdigest()[:16] + _ext_of(url))
            await asyncio.to_thread(target.write_bytes, data)
            try:
                self._library().update(str(entry.get("id")), path=target.as_posix())
            except Exception:
                pass
            return target.as_posix()
        except Exception as e:
            self.logger.info(texts.t("collect_download_failed", url=url, error=e))
            return url

    # ==================== sticker_search ====================

    async def sticker_search(self, query: str = "", top_n: int = 5, **_) -> dict:
        """语义检索（不发图）：按相关度返回 top_n 条目，供模型/编排预览选图。"""
        try:
            query = str(query or "").strip()
            try:
                top_n = max(1, int(top_n or 5))
            except (TypeError, ValueError):
                top_n = 5
            lib = self._library()
            if not query:
                return {"query": "", "count": 0, "results": [],
                        "tag_hints": lib.tag_cloud(8)}
            hits = lib.search(query, top_n=top_n)
            return {
                "query": query,
                "count": len(hits),
                "results": [
                    {
                        "id": h.get("id"),
                        "desc": h.get("desc"),
                        "tags": h.get("tags"),
                        "score": h.get("score"),
                        "url": h.get("url"),
                        "path": h.get("path"),
                        "uses": h.get("uses"),
                    }
                    for h in hits
                ],
                "tag_hints": lib.tag_cloud(8) if not hits else [],
            }
        except Exception as e:
            return {"query": str(query or ""), "count": 0, "results": [], "error": str(e)}

    # ==================== sticker_stats ====================

    async def sticker_stats(self, **_) -> dict:
        """库统计：总量/上限/未分类数/今日新增/标签分布/最常用。"""
        try:
            return self._library().stats()
        except Exception as e:
            return {"error": str(e)}

    # ==================== sticker_add ====================

    async def sticker_add(self, url: str = "", path: str = "", desc: str = "",
                          tags: str = "", **_) -> str:
        """手动收录（URL 或本地路径；desc/tags 可省——省则走视觉打标签）。"""
        try:
            url = str(url or "").strip()
            path = str(path or "").strip()
            desc = str(desc or "").strip()
            tags = str(tags or "").strip()
            ref = url or path
            if not ref:
                return texts.t("sticker_add_failed", error="url or path required")

            collector = self._collector()
            result = await collector.collect_url(ref, source="manual")
            entry = result.get("entry")
            if not result.get("added") and entry is not None:
                old_desc = str(entry.get("desc") or "") or UNTAGGED
                return texts.t("sticker_duplicated", id=entry.get("id"), desc=old_desc)
            if entry is None:
                return texts.t("sticker_add_failed",
                               error=str(result.get("error") or "collect failed"))

            entry_id = str(entry.get("id"))
            if desc or tags:
                fields: dict = {}
                if desc:
                    fields["desc"] = desc
                if tags:
                    fields["tags"] = tags
                updated = self._library().update(entry_id, **fields) or entry
                return texts.t("sticker_added_static", id=entry_id,
                               desc=updated.get("desc") or UNTAGGED,
                               tags=" ".join(updated.get("tags") or [UNTAGGED]))
            if self._setting("tag_via_vision", True):
                collector._after_added(entry)  # spawn 视觉打标签
                return texts.t("sticker_added", id=entry_id, source="manual")
            return texts.t("sticker_added_static", id=entry_id, desc=UNTAGGED,
                           tags=UNTAGGED)
        except Exception as e:
            return texts.t("sticker_add_failed", error=e)

    # ==================== qvq_settings（归属标记，QvQChat 约定） ====================

    async def qvq_settings(self, **_) -> dict:
        """归属标记：只有返回该服务的模块才出现在 QvQChat 设置页插件分区。

        配置项由本模块 ConfigClass 声明（框架 schema 自动呈现），不再逐条导出 items。
        """
        return {"title": texts.t("settings_title")}

    # ==================== qvq_schemas（参数级 schema，QvQChat 生态桥约定） ====================

    async def qvq_schemas(self, **_) -> dict:
        """返回服务的参数级 schema：生态桥优先用它覆盖「从签名推导」的弱schema。"""
        from .Main import SCHEMAS

        return {name: dict(schema) for name, schema in SCHEMAS.items()}

    # ==================== qvq_access（访问级别自声明，QvQChat 生态桥约定） ====================

    async def qvq_access(self, **_) -> dict:
        """返回各服务的访问级别：safe=所有成员可用（发表情/检索/统计），admin=仅管理员（写库）。"""
        from .Main import ACCESS

        return dict(ACCESS)

    # ==================== qvq_extensions（统一扩展入口，QvQChat 约定） ====================

    async def qvq_extensions(self, **_) -> dict:
        """向 QvQChat 注入表情包使用建议（提示词），AI 自行判断是否发送。"""
        from .Main import EXTENSION_PROMPTS

        return {"prompts": [dict(item) for item in EXTENSION_PROMPTS]}
    # ==================== 库管理服务（QvQChat Dashboard 表情包视窗代理调用） ====================

    async def sticker_list(self, query: str = "", limit: int = 300, **_) -> dict:
        """全量/检索式列表（管理视窗）：query 空则全量（库序），非空走语义检索。"""
        try:
            try:
                limit = max(1, min(int(limit or 300), 1000))
            except (TypeError, ValueError):
                limit = 300
            lib = self._library()
            query = str(query or "").strip()
            items = lib.search(query, top_n=limit) if query else lib.list(limit=limit)
            return {"count": len(items), "items": items}
        except Exception as e:
            return {"count": 0, "items": [], "error": str(e)}

    async def sticker_update(self, entry_id: str = "", desc: str = "", tags: str = "", **_) -> dict:
        """编辑条目（desc/tags）。"""
        try:
            entry_id = str(entry_id or "").strip()
            if not entry_id:
                return {"ok": False, "error": "entry_id required"}
            fields: dict = {}
            if desc is not None:
                fields["desc"] = str(desc or "").strip()
            if tags is not None:
                fields["tags"] = str(tags or "").strip()
            updated = self._library().update(entry_id, **fields)
            if updated is None:
                return {"ok": False, "error": "not found"}
            return {"ok": True, "entry": updated}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def sticker_remove(self, entry_id: str = "", delete_file: bool = True, **_) -> dict:
        """删除条目（默认连带删除本地文件）。"""
        try:
            removed = self._library().remove(str(entry_id or "").strip(),
                                             delete_file=bool(delete_file))
            if removed is None:
                return {"ok": False, "error": "not found"}
            return {"ok": True, "removed": removed.get("id")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def sticker_image(self, entry_id: str = "", **_) -> dict:
        """图片载荷（Dashboard 代理展示用）：本地文件转 base64；URL 条目回跳地址。"""
        import base64
        from pathlib import Path

        try:
            entry = self._library().get(str(entry_id or "").strip())
            if entry is None:
                return {"ok": False, "error": "not found"}
            path = str(entry.get("path") or "").strip()
            url = str(entry.get("url") or "").strip()
            if path and not path.startswith(("http://", "https://")):
                file = Path(path).expanduser()
                if file.is_file():
                    return {"ok": True, "mime": _image_mime(file.suffix),
                            "data": base64.b64encode(file.read_bytes()).decode("ascii")}
            if url:
                return {"ok": True, "url": url}
            return {"ok": False, "error": "no image source"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

