<div align="center">

简体中文 | [繁體中文](README.zh-TW.md) | [English](README.en.md) | [日本語](README.ja.md) | [Русский](README.ru.md)

<img src="https://raw.githubusercontent.com/ErisPulse/ErisPulse/main/.github/assets/ErisPulseLogo.png" width="180" alt="QvQMemes" />

<img src="https://raw.githubusercontent.com/ErisPulse/.github/main/assets/badges/module.svg" alt="ErisPulse Module" />

</div>

# QvQMemes

**给 [QvQChat](https://github.com/wsu2059q/ErisPulse-QvQChat) 智能体用的表情包系统：旁听群聊自动收录、视觉模型自动打标签，何时发、发哪张由 AI 自己决定。**

市面上的"斗图插件"多半是台老虎机：消息一来掷个骰子，从文件夹里随机抽出一张，抽到什么和正在聊什么毫无关系。这个模块反其道而行。表情包在这里被当作智能体表达的一部分，离开库有两条途径：`send_sticker` 工具——它经 QvQChat 生态桥注册成一个普通的 AI 工具；以及回复正文里的内联标记 `[表情:关键词]`——QvQChat 4.6.7 起由投递层直接换成真实表情包，模型不用为此调用工具。此刻该不该发表情包——接个梗、给个情绪、回敬一句——完全是模型的判断，唯一的引导是工具描述里那句"适合轻松/调侃/情绪表达场景，正式任务慎用"。整条链路没有定时器、没有骰子，这正是它与"随机斗图"类插件的本质区别，而且是刻意为之。

"发哪张"的解决方式相同。模型给出语义查询（"得意的猫""委婉地感谢"），模块对库内每张图的描述和标签做确定性的词面匹配打分——标签全等记 4 分、标签子串 2 分、描述命中 3 分，逐词累加——然后把分数最高的一张发出去。收录同样不劳人手：群聊里流过的图片经 QvQChat 的 `qvq.observe` 事件被顺路收下，视觉模型逐张看过，写一句内容与情绪的描述，再给三到六个适用场景标签，全程没人动手。模块对排序做过的唯一调整是新鲜度降权：`recency_hours` 窗口内发过的图检索分数减半，免得智能体一星期都在刷同一张图。这是确定性调度，不是概率——近期用过的降权、库满了按最久未用淘汰，除此之外没有任何东西会打乱顺序。

<p>

  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10+-FFD43B?style=for-the-badge&logo=python&logoColor=blue" alt="Python"></a>

  <a href="https://github.com/ErisPulse/ErisPulse"><img src="https://img.shields.io/badge/ErisPulse-2.8.0+-00A3FF?style=for-the-badge" alt="ErisPulse"></a>

  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge" alt="License"></a>

</p>

## 安装

需要 [QvQChat](https://github.com/wsu2059q/ErisPulse-QvQChat) 4.0.4 及以上（本模块对接的 `qvq.observe` 事件与插件设置页均自该版本引入），基于 ErisPulse 2.8.0+。

```bash
epsdk install ErisPulse-QvQMemes
# 或
pip install ErisPulse-QvQMemes
```

## 启用

不需要启用。模块装上后，生态桥自动把它的服务转成 AI 工具：智能体随即获得 `send_sticker`，连同 `sticker_search`、`sticker_stats`、`sticker_add`；卸载时这些工具同样悄无声息地消失。配置项则出现在 QvQChat Dashboard 设置页的插件分区。

表情包库默认在 `data/QvQMemes/stickers`。从聊天里收录的图片会下载到这里，以内容哈希的前 16 位十六进制命名；每次启动时模块还会扫描该目录，把你手动放进去的图片一并入库——直接丢一个文件夹的表情包进来铺底，完全是正当用法。

## 工作方式

收录走的是旁路，绝不阻塞聊天主链路。QvQChat 广播 `qvq.observe`（它看到的每条群消息，每条最多带 3 张图）时，模块快速校验载荷后就把活儿交给后台任务。后台任务先按 URL 和内容哈希双指纹去重，再过单会话每日上限，然后串行下载落盘——下载失败也会保留 URL 引用，图照样能发——最后排队等视觉打标签。打标签经 QvQChat 的 `describe_image` 完成；视觉模型缺失或调用失败时，条目暂记「未分类」，仍可凭描述参与检索。这条链路上没有哪一环是致命的。所有条目以 `{id, path, url, desc, tags, source, uses, last_used, added}` 的形式存在框架的键值存储里。

发送走平台适配器的 SendDSL，框架支持的平台上表现一致：

```python
# 模型调用 send_sticker 工具时，生态桥实际执行的就是：
await sdk.module.call("QvQMemes", "send_sticker",
                      query="得意的猫 嘚瑟",
                      platform="qqbot",
                      target_id="123456",
                      target_type="group")
# -> "已发送：一只翻着白眼的得意白猫｜得意 调侃 反击"
```

没有匹配时，返回值会说明情况并列出库里的高频标签，模型可以换词重试，而不是无声地失败。

## 配置

配置项在本模块的 ConfigClass 中声明，QvQChat 设置页自动渲染成表单；运行值在加载时经 `QvQChat.plugin_config` 拉回，失败则静默沿用默认。

| 键 | 默认值 | 说明 |
|------|---------|-------------|
| `library_dir` | `data/QvQMemes/stickers` | 库目录：下载的图片落在这里，启动时扫描收录已有图片 |
| `max_library` | `500` | 库容量上限；超出按最久未用淘汰（从未用过的按入库时间计） |
| `recency_hours` | `72` | 新鲜度降权窗口（小时）：窗口内发过的图检索分数减半 |
| `send_method` | `Image` | 发送所用的 SendDSL 方法（图片 URL 或本地路径） |
| `auto_collect` | `True` | 旁听 `qvq.observe` 自动收录图片（每条消息最多 3 张） |
| `collect_per_chat_daily` | `20` | 单会话每日自动收录上限；≤0 表示不限 |
| `tag_via_vision` | `True` | 收录后用 QvQChat 视觉模型打标签；不可用时暂记「未分类」 |

## 服务

五个服务全部在 `get_meta().services` 白名单中。它们只返回 str 或 dict，绝不抛异常——内部错误一律转成可读文本或 `error` 字段，模型拿到的永远是一个能接着用的答复。

| 服务 | 签名 | 说明 |
|---------|-----------|-------------|
| `send_sticker` | `(query: str, platform: str, target_id: str, target_type: str = "group") -> str` | 按语义查询选最优一张并发送；成功返回「已发送：\<desc\>」，无匹配返回可用标签提示，失败返回错误文本 |
| `sticker_search` | `(query: str, top_n: int = 5) -> dict` | 只检索不发图：`{query, count, results: [{id, desc, tags, score, url, path, uses}], tag_hints}` |
| `sticker_stats` | `() -> dict` | 库统计：`{total, max_library, untagged, added_today, tag_distribution, top_used}` |
| `sticker_add` | `(url: str = '', path: str = '', desc: str = '', tags: str = '') -> str` | 手动收录（URL 或本地路径）；desc/tags 省略时走视觉打标签 |
| `qvq_settings` | `() -> dict` | 归属标记 `{"title": ...}`；声明该服务的模块才会出现在 QvQChat 设置页插件分区 |

## 许可证

MIT
