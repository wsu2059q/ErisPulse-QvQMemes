"""QvQMemes 双语文案（zh-CN / en）。

组织方式对照 QvQStalker/texts.py，完全独立：
- ``STRINGS``：key → {lang → 带占位符模板}
- ``t(key, **kwargs)``：取当前语言模板并格式化；ErisPulse i18n
  可用时优先走框架翻译（register_texts 注册），否则退回本地表。
"""

from __future__ import annotations

DOMAIN = "QvQMemes"

STRINGS: dict[str, dict[str, str]] = {
    # ---- 生命周期 ----
    "observe_registered": {
        "zh-CN": "已订阅 {event} 事件（若 QvQChat 尚未实现该事件，将在其落地后自动生效）",
        "en": "Subscribed to {event} (takes effect automatically once QvQChat emits it)",
    },
    "observe_register_failed": {
        "zh-CN": "订阅 {event} 失败（框架 lifecycle 不可用）：{error}",
        "en": "Failed to subscribe {event} (lifecycle unavailable): {error}",
    },
    "scan_done": {
        "zh-CN": "启动目录扫描完成：新增 {added} 张，库内共 {total} 张",
        "en": "Startup directory scan done: {added} added, {total} in library",
    },
    "scan_failed": {
        "zh-CN": "启动目录扫描失败（{dir}）：{error}",
        "en": "Startup directory scan failed ({dir}): {error}",
    },
    "config_merged": {
        "zh-CN": "已从 QvQChat.plugin_config 拉取并合并 {n} 项设置：{keys}",
        "en": "Merged {n} setting(s) from QvQChat.plugin_config: {keys}",
    },
    "config_pull_failed": {
        "zh-CN": "拉取 plugin_config 失败（服务缺失或不可达），沿用默认配置：{error}",
        "en": "plugin_config pull failed (service missing or unreachable); defaults kept: {error}",
    },
    # ---- 自动收录 ----
    "collect_disabled": {
        "zh-CN": "自动收录已关闭（auto_collect=false）",
        "en": "Auto collect disabled (auto_collect=false)",
    },
    "collect_duplicated": {
        "zh-CN": "重复图片（{fp}），跳过收录",
        "en": "Duplicate image ({fp}); skipped",
    },
    "collect_daily_limit": {
        "zh-CN": "会话 {chat} 今日自动收录已达上限 {limit}，跳过 {url}",
        "en": "Daily collect limit {limit} reached for {chat}; skipped {url}",
    },
    "collect_added": {
        "zh-CN": "已收录表情包 {id}（来源 {source}），视觉打标签进行中",
        "en": "Sticker {id} collected (source {source}); vision tagging in progress",
    },
    "collect_added_no_tag": {
        "zh-CN": "已收录表情包 {id}（来源 {source}，未开视觉打标签，暂记「未分类」）",
        "en": "Sticker {id} collected (source {source}; vision tagging off, filed as untagged)",
    },
    "collect_download_failed": {
        "zh-CN": "图片下载失败，保留 URL 引用：{url}（{error}）",
        "en": "Image download failed; keeping URL reference: {url} ({error})",
    },
    "tag_done": {
        "zh-CN": "视觉打标签完成：{id} → {desc}｜{tags}",
        "en": "Vision tagging done: {id} -> {desc} | {tags}",
    },
    "tag_failed": {
        "zh-CN": "视觉打标签失败（describe_image 不可用或无视觉模型），{id} 暂记「未分类」",
        "en": "Vision tagging failed (describe_image unavailable or no vision model); {id} filed as untagged",
    },
    "collect_error": {
        "zh-CN": "收录流程异常（不影响观察回调）：{error}",
        "en": "Collect pipeline error (observe callback unaffected): {error}",
    },
    # ---- 服务 ----
    "send_sent": {
        "zh-CN": "已发送：{desc}",
        "en": "Sent: {desc}",
    },
    "send_no_match": {
        "zh-CN": "表情包库里没有匹配「{query}」的图。可用标签参考：{tags}",
        "en": "No sticker matches \"{query}\". Available tags: {tags}",
    },
    "send_need_query": {
        "zh-CN": "缺少语义描述（query），无法选图",
        "en": "Missing semantic description (query); cannot pick a sticker",
    },
    "send_need_target": {
        "zh-CN": "缺少发送目标（platform / target_id）",
        "en": "Missing send target (platform / target_id)",
    },
    "adapter_not_found": {
        "zh-CN": "平台 {platform} 适配器不可用",
        "en": "Adapter for platform {platform} is unavailable",
    },
    "send_failed": {
        "zh-CN": "表情包发送失败：{error}",
        "en": "Sticker send failed: {error}",
    },
    "search_header": {
        "zh-CN": "「{query}」匹配 {n} 张（按相关度）：",
        "en": "{n} match(es) for \"{query}\" (by relevance):",
    },
    "search_empty": {
        "zh-CN": "没有找到匹配「{query}」的表情包。可用标签参考：{tags}",
        "en": "No sticker matches \"{query}\". Available tags: {tags}",
    },
    "sticker_added": {
        "zh-CN": "已手动收录表情包 {id}（来源 {source}），视觉打标签进行中",
        "en": "Sticker {id} added manually (source {source}); vision tagging in progress",
    },
    "sticker_added_static": {
        "zh-CN": "已手动收录表情包 {id}：{desc}｜{tags}",
        "en": "Sticker {id} added: {desc} | {tags}",
    },
    "sticker_duplicated": {
        "zh-CN": "该图片已在库中（{id}：{desc}），无需重复收录",
        "en": "Already in library ({id}: {desc}); skipped",
    },
    "sticker_add_failed": {
        "zh-CN": "收录失败：{error}",
        "en": "Add failed: {error}",
    },
    "library_empty": {
        "zh-CN": "表情包库为空（还没有收录任何图片）",
        "en": "Sticker library is empty (nothing collected yet)",
    },
    # ---- 视觉打标签 prompt（喂给 describe_image） ----
    "vision_prompt": {
        "zh-CN": "判断这张图是不是表情包（meme/表情图/梗图：卡通角色、动物萌宠、搞笑人物，表达情绪可复用）。"
                 "如果不是——游戏截图、聊天记录、网页/新闻截图、长图文章、普通照片、二维码等——只在第一行输出 NOT_STICKER。"
                 "如果是：第一行用不超过20字描述内容与情绪，第二行给3-6个空格分隔的适用场景标签（如：开心 调侃 感谢 疑惑 撒娇 嘲讽）",
        "en": "Decide whether this image is a meme/sticker (cartoon, cute animal, funny face - reusable emotion expressions). "
              "If NOT - game screenshots, chat logs, web/news screenshots, long-form articles, plain photos, QR codes - "
              "output exactly NOT_STICKER on the first line. "
              "If it is: first line describes content and emotion in at most 20 words, second line gives 3-6 space-separated usage tags (e.g. happy teasing thanks confused cute sarcastic)",
    },
    "vision_rejected": {
        "zh-CN": "已拒收非表情包图片（{desc}）",
        "en": "Rejected non-sticker image ({desc})",
    },
    # ---- qvq_settings（插件设置注册，plugin_config 约定） ----
    "settings_title": {
        "zh-CN": "QvQMemes 表情包",
        "en": "QvQMemes Stickers",
    },
}

DEFAULT_LANG = "zh-CN"
_lang = DEFAULT_LANG


def set_language(lang: str) -> None:
    """设置默认语言（zh-CN / en）。"""
    global _lang
    if lang in (DEFAULT_LANG, "en"):
        _lang = lang


def register_texts() -> None:
    """把文案注册进 ErisPulse i18n（框架可用时）；失败静默（本地表兜底）。"""
    try:
        from ErisPulse.Core import i18n

        for lang, table in _by_lang().items():
            i18n.register(lang, {f"{DOMAIN}.{key}": text for key, text in table.items()}, domain=DOMAIN)
    except Exception:
        pass


def _by_lang() -> dict[str, dict[str, str]]:
    langs: dict[str, dict[str, str]] = {}
    for key, translations in STRINGS.items():
        for lang, text in translations.items():
            langs.setdefault(lang, {})[key] = text
    return langs


def t(key: str, default: str | None = None, lang: str | None = None, **kwargs) -> str:
    """取文案并格式化占位符。

    优先走 ErisPulse i18n（已注册时），失败/未安装时退回本地 STRINGS。
    """
    table = STRINGS.get(key, {})
    text = None
    if default is None:
        default = table.get("en", key)
    try:
        from ErisPulse.Core import i18n

        text = i18n.t(f"{DOMAIN}.{key}", default=default, **kwargs)
    except Exception:
        text = None
    if not text or text == default:
        # i18n 未注册/不可用时回退本地表
        text = table.get(lang or _lang) or table.get(DEFAULT_LANG) or default
    if kwargs and "{" in text:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return text
