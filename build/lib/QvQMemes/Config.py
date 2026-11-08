"""QvQMemes 配置（dataclass + metadata，组织方式对照 QvQStalker/Config.py 独立实现）。

metadata 的 description.i18n 键形如 ``QvQMemes.<key>``，ui 提供
Dashboard 设置表单所需的 widget/group/order 信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:  # 独立运行/测试环境没有 ErisPulse 时仍可实例化配置
    from ErisPulse.Core.Bases import BaseConfig
except Exception:  # pragma: no cover - 环境兜底
    BaseConfig = object  # type: ignore[assignment, misc]


def _desc(key: str, text: str, group: str, order: int, widget: str = "text",
          options: list | None = None) -> dict:
    ui: dict = {"widget": widget, "group": group, "order": order}
    if options is not None:
        ui["options"] = options
    return {"description": {"i18n": f"QvQMemes.{key}", "default": text}, "ui": ui}


@dataclass
class QvQMemesConfig(BaseConfig):
    # ---- 库 ----
    library_dir: str = field(
        default="data/QvQMemes/stickers",
        metadata=_desc("library_dir", "表情包库存目录（下载/扫描的图片落盘位置）", "library", 1),
    )
    max_library: int = field(
        default=500,
        metadata=_desc("max_library", "库容量上限（超出按最久未用淘汰）", "library", 2, "number"),
    )
    recency_hours: float = field(
        default=72.0,
        metadata=_desc("recency_hours", "新鲜度调度窗口（小时）：该窗口内用过的表情包在选图时确定性降权（非概率），避免短期内重复刷同一张",
                       "library", 3, "number"),
    )
    send_method: str = field(
        default="Image",
        metadata=_desc("send_method", "发送方式（SendDSL 方法名），默认 Image（图片 URL 或本地路径）",
                       "library", 4),
    )

    # ---- 自动收录 ----
    auto_collect: bool = field(
        default=True,
        metadata=_desc("auto_collect", "自动收录：旁听 qvq.observe 载荷中的 images（每条消息最多 3 张）",
                       "collect", 1, "switch"),
    )
    collect_per_chat_daily: int = field(
        default=20,
        metadata=_desc("collect_per_chat_daily", "单会话每日自动收录上限（防刷屏式入库）", "collect", 2, "number"),
    )
    tag_via_vision: bool = field(
        default=True,
        metadata=_desc("tag_via_vision", "收录后用 QvQChat 视觉模型自动打标签（describe_image 服务；不可用时记为「未分类」，不影响收录）",
                       "collect", 3, "switch"),
    )
