"""QvQMemes：ErisPulse 表情包系统插件（QvQChat 伴侣模块）。

设计哲学——零概率、全智能：
- 表情包"何时发、发哪个"完全由 AI 模型经 ``send_sticker`` 语义选图工具自行判断，
  整条链路没有任何 random 概率触发；
- 收录打标签由 QvQChat 视觉模型（``describe_image`` 服务）完成。

零 QvQChat 包依赖，仅经 sdk.lifecycle / sdk.module.call / sdk.storage /
sdk.adapter 交互。
"""

from .Main import Main, __version__

__all__ = ["Main", "__version__"]
