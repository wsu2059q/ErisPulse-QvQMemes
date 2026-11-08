"""QvQMemes 主模块（BaseModule 子类 + ServiceMixin 五服务）。

与 QvQChat 的契约（双方均防御式对接，零 QvQChat 包依赖）：
- 订阅 lifecycle 事件 ``qvq.observe``：读载荷 ``images: [url, ...]``（≤3）自动
  收录；事件尚不存在时本模块照常加载，落地后自动生效；
- ``QvQChat.describe_image``：收录后视觉打标签（服务缺失/无视觉模型时暂记
  「未分类」，不影响收录与发送）；
- ``QvQChat.plugin_config``：插件设置值合并（失败静默沿用自身默认）；
- 发送经 ``sdk.adapter`` 的 SendDSL（``Send.To(...).Image(ref)``），平台无关。

设计哲学（零概率、全智能）：
- 表情包「何时发、发哪个」完全由 AI 模型经 ``send_sticker`` 语义选图工具自行
  判断，整条链路没有任何 random 概率触发；
- 唯一的排序调整是「新鲜度降权」（recency_hours 窗口内用过的确定性 ×0.5），
  属确定性调度而非概率。
"""

from __future__ import annotations

import asyncio

try:
    from ErisPulse import sdk
except ImportError:  # 独立运行/测试环境兜底
    sdk = None

try:
    from ErisPulse.Core.Bases import BaseModule
except ImportError:  # pragma: no cover
    BaseModule = object  # type: ignore[assignment, misc]

try:
    from ErisPulse.Core.Bases import ModuleMeta
except ImportError:  # pragma: no cover
    ModuleMeta = None

try:
    from ErisPulse.loaders import ModuleLoadStrategy
except ImportError:  # pragma: no cover
    ModuleLoadStrategy = None

from . import texts
from .Config import QvQMemesConfig
from .collect import Collector
from .library import StickerLibrary
from .service import ServiceMixin

__version__ = "0.1.3"

OBSERVE_EVENT = "qvq.observe"

# services 白名单：框架 sdk.module.call 严格校验，未声明即拒绝——五个服务必须列全。
# send_sticker 的 description 会被生态桥注册为 AI 工具说明——「何时发」的判断
# 完全交给模型，本模块不做任何概率触发。
SERVICES = [
    {
        "name": "send_sticker",
        "description": "根据语义描述从表情包库挑选并发送表情包；适合轻松/调侃/情绪表达场景，正式任务慎用",
        "signature": "(query: str, platform: str, target_id: str, target_type: str = \"group\") -> str",
    },
    {
        "name": "sticker_search",
        "description": "语义检索表情包库（不发图）：按 query 词面匹配 desc/tags 打分排序，返回 top_n 条目与标签提示",
        "signature": "(query: str, top_n: int = 5) -> dict",
    },
    {
        "name": "sticker_stats",
        "description": "表情包库统计：总量/容量上限/未分类数/今日新增/标签分布/最常用榜",
        "signature": "() -> dict",
    },
    {
        "name": "sticker_add",
        "description": "手动收录表情包（URL 或本地路径）；desc/tags 可省，省则走视觉模型自动打标签",
        "signature": "(url: str = '', path: str = '', desc: str = '', tags: str = '') -> str",
    },
    {
        "name": "sticker_list",
        "description": "表情包库列表（QvQChat Dashboard 表情包视窗代理调用）：query 空=全量，非空=语义检索",
        "signature": "(query: str = '', limit: int = 300) -> dict",
    },
    {
        "name": "sticker_update",
        "description": "编辑表情包条目的描述与标签（Dashboard 管理视窗用）",
        "signature": "(entry_id: str = '', desc: str = '', tags: str = '') -> dict",
    },
    {
        "name": "sticker_remove",
        "description": "删除表情包条目（默认连带删除本地文件；Dashboard 管理视窗用）",
        "signature": "(entry_id: str = '', delete_file: bool = True) -> dict",
    },
    {
        "name": "sticker_image",
        "description": "表情包图片载荷（base64 或 URL；Dashboard 图片代理展示用）",
        "signature": "(entry_id: str = '') -> dict",
    },
    {
        "name": "qvq_settings",
        "description": "归属标记：声明本模块出现在 QvQChat 设置页插件分区（配置项由本模块 ConfigClass 声明，框架 schema 自动呈现）",
        "signature": "() -> dict",
    },
    {
        "name": "qvq_schemas",
        "description": "约定服务：给 QvQChat 生态桥提供本模块服务的参数级 schema（含每个参数的说明）",
        "signature": "() -> dict",
    },
    {
        "name": "qvq_access",
        "description": "约定服务：自声明本模块各服务的访问级别（safe=所有成员可用 / admin=仅管理员）",
        "signature": "() -> dict",
    },
    {
        "name": "qvq_extensions",
        "description": "约定服务：向 QvQChat 注入表情包使用建议（提示词），由 AI 自行判断何时发",
        "signature": "() -> dict",
    },
]

# 注入 QvQChat 系统提示的使用建议：只给判断依据，是否发送由 AI 决定
EXTENSION_PROMPTS = [
    {
        "text": (
            "发表情包（原生能力）：发不发由你自己判断——轻松、调侃、情绪表达、接梗、"
            "斗图氛围合适就可以发一张，让聊天更像活人；正式任务、长文说明、报错排查"
            "和需要精确信息的场合不要发；同一话题不要连发。"
            "发法一步：表情包桥接工具（eco_QvQMemes_ 开头，如 send_sticker），"
            "query 写情绪或画面（如「无语」「猫猫疑惑」「委婉地感谢」）；"
            "库为空或没有匹配就照实说，不要硬造结果，也不要改用别的发图方式凑数。"
        ),
        "priority": 4,
    },
]

# qvq_access：模块自声明访问级别——只读/表达类服务对所有人开放，写库类仅管理员。
# QvQChat 生态桥以此为准（运营方仍可在 eco_safe/eco_admin 里覆盖）。
ACCESS = {
    "send_sticker": "safe",
    "sticker_search": "safe",
    "sticker_stats": "safe",
    "sticker_add": "admin",
# 库管理服务：仅 Dashboard 管理视窗（QvQChat 代理）与管理员
    "sticker_list": "admin",
    "sticker_update": "admin",
    "sticker_remove": "admin",
    "sticker_image": "admin",
}

# qvq_schemas：参数级说明（仅靠签名推导时模型只知道参数名，不知道 query 该写什么）
SCHEMAS = {
    "send_sticker": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "想表达的情绪/场景/画面关键词（如「开心」「无语」「猫猫疑惑」）；按语义匹配表情包的描述与标签",
            },
            "platform": {"type": "string", "description": "目标平台名（如 onebot11）"},
            "target_id": {"type": "string", "description": "目标会话 id（群号或用户号）"},
            "target_type": {
                "type": "string",
                "enum": ["group", "user"],
                "description": "目标类型：群聊 group / 私聊 user",
                "default": "group",
            },
        },
        "required": ["query", "platform", "target_id"],
    },
    "sticker_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词（情绪/场景/画面）"},
            "top_n": {"type": "integer", "description": "返回条数上限", "default": 5},
        },
        "required": ["query"],
    },
    "sticker_add": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "表情包图片 URL"},
            "path": {"type": "string", "description": "本地文件路径（与 url 二选一）"},
            "desc": {"type": "string", "description": "描述（省略则自动视觉打标签）"},
            "tags": {"type": "string", "description": "空格分隔的场景标签（省略则自动生成）"},
        },
        "required": [],
    },
}


class _NullLogger:
    """sdk.logger 不可用时的静默兜底。"""

    def debug(self, *a, **k):
        pass

    info = warning = error = debug


class Main(BaseModule, ServiceMixin):

    ConfigClass = QvQMemesConfig

    def __init__(self):
        self.sdk = sdk
        self.logger = self._make_logger()
        self.library: StickerLibrary | None = None
        self.collector: Collector | None = None
        self._local_cfg: QvQMemesConfig | None = None  # 测试注入/框架合并后的配置
        self._observe_registered = False

    # ==================== 元信息 ====================

    @staticmethod
    def get_meta():
        info = dict(
            name="QvQMemes",
            description="ErisPulse 表情包系统插件：自动收录打标签、语义选图发送 —— 全智能零概率（QvQChat 伴侣模块）",
            version=__version__,
            group="智能",
            tags=["memes", "sticker", "vision", "qvq"],
            services=[dict(s) for s in SERVICES],
            min_sdk_version="2.8.0",
        )
        if ModuleMeta is not None:
            try:
                return ModuleMeta(**info)
            except Exception:
                return info
        return info

    @staticmethod
    def get_load_strategy():
        # 晚于 QvQChat（priority=50）加载：运行时依赖其 describe_image/plugin_config
        strategy = dict(lazy_load=False, priority=30)
        if ModuleLoadStrategy is not None:
            try:
                return ModuleLoadStrategy(**strategy)
            except Exception:
                pass
        return strategy

    # ==================== 生命周期 ====================

    async def on_load(self, event) -> bool:
        try:
            texts.register_texts()
        except Exception:
            pass
        cfg = self._cfg()
        self.library = StickerLibrary(self.sdk, self._cfg, logger=self.logger)
        self.collector = Collector(self.sdk, self._cfg, self.library,
                                   logger=self.logger, spawner=self._spawn)

        # 启动目录扫描：库目录里已存在的图片直接入库（幂等，失败不影响加载）
        try:
            added = self.library.scan_directory()
            total = len(self.library.list())
            self.logger.info(texts.t("scan_done", added=len(added), total=total))
        except Exception as e:
            self.logger.warning(texts.t("scan_failed",
                                        dir=str(getattr(cfg, "library_dir", "")), error=e))

        # 插件设置值合并：QvQChat.plugin_config（失败静默沿用自身默认）
        await self._merge_plugin_config()

        # 订阅 qvq.observe（事件可能尚不存在——订阅与回调全防御）
        try:
            self.sdk.lifecycle.register(OBSERVE_EVENT, self._on_observe)
            self._observe_registered = True
            self.logger.info(texts.t("observe_registered", event=OBSERVE_EVENT))
        except Exception as e:
            self.logger.warning(texts.t("observe_register_failed", event=OBSERVE_EVENT, error=e))

        self.library.evict_to_limit()

        # Dashboard 视窗自注册（宿主在才挂；uninstall 路径在 on_unload teardown）
        try:
            from .dashboard import DashboardIntegration

            self._dash = DashboardIntegration(self)
            if self._dash.setup():
                self.logger.info("dashboard view registered (group=qvq)")
        except Exception as e:
            self.logger.warning(f"dashboard setup skipped: {e}")
        return True

    async def on_unload(self, event) -> bool:
        if self._observe_registered:
            try:
                self.sdk.lifecycle.unregister(OBSERVE_EVENT, self._on_observe)
            except Exception:
                pass
            self._observe_registered = False
        dash = getattr(self, "_dash", None)
        if dash is not None:
            try:
                dash.teardown()
            except Exception:
                pass
        if self.collector is not None:
            try:
                await self.collector.shutdown()
            except Exception:
                pass
        return True

    async def on_config_update(self, old, new) -> None:
        return

    def _spawn(self, coro):
        """优先 BaseModule.spawn（任务登记到模块名下），失败退回 ensure_future。"""
        try:
            return self.spawn(coro)
        except Exception:
            return asyncio.ensure_future(coro)

    # ==================== 观察回调（qvq.observe → Collector） ====================

    async def _on_observe(self, data) -> None:
        """旁听收录：只读 images，全部逻辑与异常兜底在 Collector 内。"""
        if self.collector is None:
            return
        await self.collector.on_observe(data)

    # ==================== 插件设置值合并（plugin_config 约定） ====================

    async def _merge_plugin_config(self) -> None:
        """从 QvQChat.plugin_config 拉取本模块设置值并合并进 _local_cfg。

        服务缺失/调用失败静默沿用默认；只接受已知配置键，值按 dataclass 字段
        类型强转，转不动则忽略该键。
        """
        try:
            remote = await self.sdk.module.call("QvQChat", "plugin_config",
                                                module="QvQMemes")
        except Exception as e:
            self.logger.debug(texts.t("config_pull_failed", error=e))
            return
        values = self._extract_values(remote, module_name="QvQMemes")
        if not values:
            return
        fields = QvQMemesConfig.__dataclass_fields__
        merged: list[str] = []
        cfg = self._local_cfg if self._local_cfg is not None else QvQMemesConfig()
        for key, value in values.items():
            if key not in fields or key.startswith("_") or value is None:
                continue
            try:
                coerced = self._coerce(value, fields[key].type)
            except Exception:
                continue
            if coerced is None:
                continue
            setattr(cfg, key, coerced)
            merged.append(key)
        self._local_cfg = cfg
        if merged:
            self.logger.info(texts.t("config_merged", n=len(merged), keys=",".join(merged)))

    @staticmethod
    def _extract_values(remote, module_name: str = "") -> dict:
        """防御式提取 plugin_config 返回值。

        支持 QvQChat 实际返回的 {模块名: {key: v}} 嵌套形，
        以及 {key: v} / {"values": {...}} / [{"key","value"}] 等兼容形。
        """
        if isinstance(remote, dict):
            if module_name and isinstance(remote.get(module_name), dict) and remote[module_name]:
                inner = remote[module_name]
                return {str(k): v for k, v in inner.items()}
            for wrapper in ("values", "config", "settings"):
                inner = remote.get(wrapper)
                if isinstance(inner, dict) and inner:
                    return {str(k): v for k, v in inner.items()}
            return {str(k): v for k, v in remote.items() if not isinstance(v, (dict, list))}
        if isinstance(remote, (list, tuple)):
            values: dict = {}
            for item in remote:
                if isinstance(item, dict) and "key" in item:
                    values[str(item["key"])] = item.get("value")
            return values
        return {}

    @staticmethod
    def _coerce(value, typ: str):
        """按 dataclass 字段类型强转；转不动返回 None（忽略该键）。"""
        typ = str(typ)
        if "bool" in typ:
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in ("1", "true", "yes", "on"):
                return True
            if text in ("0", "false", "no", "off"):
                return False
            return None
        if "int" in typ:
            return int(value)
        if "float" in typ:
            return float(value)
        return str(value)

    # ==================== 辅助（ServiceMixin 依赖） ====================

    def _cfg(self) -> QvQMemesConfig:
        """配置获取：合并/注入的 _local_cfg > 框架实时 cfg > 兜底默认实例。"""
        if self._local_cfg is not None:
            return self._local_cfg
        try:
            cfg = getattr(self, "cfg")
            if cfg is not None:
                return cfg
        except Exception:
            pass
        self._local_cfg = QvQMemesConfig()
        return self._local_cfg

    def _setting(self, key: str, default):
        try:
            value = getattr(self._cfg(), key)
            return default if value is None else value
        except Exception:
            return default

    def _library(self) -> StickerLibrary:
        """库访问（服务在 on_load 前被调用时的懒加载兜底）。"""
        if self.library is None:
            self.library = StickerLibrary(self.sdk, self._cfg, logger=self.logger)
        return self.library

    def _collector(self) -> Collector:
        if self.collector is None:
            self.collector = Collector(self.sdk, self._cfg, self._library(),
                                       logger=self.logger, spawner=self._spawn)
        return self.collector

    def _make_logger(self):
        try:
            if self.sdk is not None and getattr(self.sdk, "logger", None) is not None:
                return self.sdk.logger.get_child("QvQMemes")
        except Exception:
            pass
        return _NullLogger()
