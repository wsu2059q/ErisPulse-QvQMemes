<div align="center">
[简体中文](README.md) | [繁體中文](README.zh-TW.md) | **English** | [日本語](README.ja.md) | [Русский](README.ru.md)

<img src="https://raw.githubusercontent.com/ErisPulse/ErisPulse/main/.github/assets/ErisPulseLogo.png" width="180" alt="QvQMemes" />

<img src="https://raw.githubusercontent.com/ErisPulse/.github/main/assets/badges/module.svg" alt="ErisPulse Module" />
</div>

# QvQMemes

**A sticker system for [QvQChat](https://github.com/wsu2059q/ErisPulse-QvQChat) agents: it picks up memes from group chats on its own, has a vision model tag them, and lets the AI decide when — and which one — to send.**

Most "meme plugins" work like a slot machine. A message arrives, a probability roll fires, a random image drops out of a folder, and the result has nothing to do with what was actually being said. This module takes the opposite position. A sticker here is part of the agent's expression, and a sticker leaves the library in one of two ways: the `send_sticker` tool, which QvQChat's ecosystem bridge registers as an ordinary AI tool, or an inline `[表情:keyword]` marker written straight into the reply — since QvQChat 4.6.7 the delivery layer swaps it for a real sticker, no tool call involved. Whether the moment calls for one — a bit of banter, an emotional beat, a comeback — is the model's judgment to make, guided by nothing more than the tool's description ("fits casual, teasing or emotional moments; think twice in serious tasks"). There is no timer and no dice anywhere in the pipeline; that is the essential difference from random-battle-image plugins, and it is deliberate.

"Which one" is settled the same way. The model formulates a semantic query ("smug cat", "thanks, but politely"), and the module scores it against every sticker's description and tags with deterministic literal matching — an exact tag match counts 4 points, a tag substring 2, a description hit 3, accumulated per term — then sends the best-scoring one. Collection is just as hands-off: images flowing through group chats are picked up via QvQChat's `qvq.observe` event, and a vision model looks at each new entry and writes a one-line description of its content and mood plus three to six usage tags. Nobody labels anything by hand. The only ranking adjustment the module ever makes is a freshness demotion: stickers sent within the `recency_hours` window have their search score halved, so the agent doesn't spam the same image all week. That is deterministic scheduling, not probability — recently used ones get demoted, the library evicts least-recently-used when full, and nothing else ever reshuffles the order.

## Installation

Requires [QvQChat](https://github.com/wsu2059q/ErisPulse-QvQChat) 4.0.4 or later — the `qvq.observe` event and the plugin settings page this module integrates with were both introduced there — on top of ErisPulse 2.8.0+.

```bash
epsdk install ErisPulse-QvQMemes
# or
pip install ErisPulse-QvQMemes
```

## Getting Started

There is nothing to enable. Once the module loads, the ecosystem bridge turns its services into AI tools automatically: the agent gains `send_sticker` along with `sticker_search`, `sticker_stats` and `sticker_add`, and loses them just as quietly on uninstall. Its configuration appears in the QvQChat Dashboard, in the plugins section of the settings page.

The sticker library lives in `data/QvQMemes/stickers` by default. Images collected from chats are downloaded there, named by the first 16 hex digits of their content hash; on every startup the module also scans the directory and adopts whatever images you dropped into it yourself. Seeding the library with a plain folder of memes is a perfectly valid way to start.

## How It Works

Collection runs as a side channel and never blocks the chat pipeline. When QvQChat broadcasts `qvq.observe` (every group message it sees, up to three images each), the module quickly validates the payload and hands the work to a background task. That task deduplicates by URL and by content hash, enforces a per-chat daily cap, downloads the survivors to disk — a failed download keeps the URL reference, so the sticker stays sendable — and then queues vision tagging. Tagging goes through QvQChat's `describe_image`; if the vision model is missing or the call fails, the entry is simply filed as untagged and remains searchable by description. No stage of the pipeline is fatal. Entries are stored as `{id, path, url, desc, tags, source, uses, last_used, added}` records in the framework's key-value storage.

Sending goes through the platform adapter's SendDSL, so it behaves the same on any platform the framework supports:

```python
# What the ecosystem bridge actually executes when the model calls the send_sticker tool:
await sdk.module.call("QvQMemes", "send_sticker",
                      query="smug cat, gloating",
                      platform="qqbot",
                      target_id="123456",
                      target_type="group")
# -> "Sent: a smug white cat rolling its eyes | smug teasing comeback"
```

When nothing matches, the return value says so and lists the library's most common tags, so the model can rephrase and try again instead of failing silently.

## Configuration

Declared in the module's ConfigClass and rendered as a form in the QvQChat settings page; values are pulled back through `QvQChat.plugin_config` at load time, and on failure the module quietly keeps its defaults.

| Key | Default | Description |
|------|---------|-------------|
| `library_dir` | `data/QvQMemes/stickers` | Library directory; downloads land here and existing images are scanned in at startup |
| `max_library` | `500` | Library size cap; beyond it the least-recently-used stickers are evicted (never-used ones count by added time) |
| `recency_hours` | `72` | Freshness window in hours; stickers sent within it have their search score halved |
| `send_method` | `Image` | SendDSL method used for sending (image URL or local path) |
| `auto_collect` | `True` | Collect images from `qvq.observe` payloads (up to 3 per message) |
| `collect_per_chat_daily` | `20` | Per-chat daily auto-collect cap; 0 or less disables the limit |
| `tag_via_vision` | `True` | Tag new stickers with the QvQChat vision model; unavailable entries are filed as untagged |

## Services

All five services are whitelisted in `get_meta().services`. They return `str` or `dict` and never raise — every internal error comes back as readable text or an `error` field, so the model always gets an answer it can act on.

| Service | Signature | Description |
|---------|-----------|-------------|
| `send_sticker` | `(query: str, platform: str, target_id: str, target_type: str = "group") -> str` | Pick the best match for a semantic query and send it; returns "Sent: \<desc\>", tag hints when nothing matches, or an error text |
| `sticker_search` | `(query: str, top_n: int = 5) -> dict` | Search without sending: `{query, count, results: [{id, desc, tags, score, url, path, uses}], tag_hints}` |
| `sticker_stats` | `() -> dict` | Library statistics: `{total, max_library, untagged, added_today, tag_distribution, top_used}` |
| `sticker_add` | `(url: str = '', path: str = '', desc: str = '', tags: str = '') -> str` | Add a sticker manually by URL or local path; omitted desc/tags go through vision tagging |
| `qvq_settings` | `() -> dict` | Ownership marker `{"title": ...}`; modules declaring it appear in the plugins section of the QvQChat settings page |

## License

MIT
