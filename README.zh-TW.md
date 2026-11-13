<div align="center">
[简体中文](README.md) | **繁體中文** | [English](README.en.md) | [日本語](README.ja.md) | [Русский](README.ru.md)

<img src="https://raw.githubusercontent.com/ErisPulse/ErisPulse/main/.github/assets/ErisPulseLogo.png" width="180" alt="QvQMemes" />

<img src="https://raw.githubusercontent.com/ErisPulse/.github/main/assets/badges/module.svg" alt="ErisPulse Module" />
</div>

# QvQMemes

**給 [QvQChat](https://github.com/wsu2059q/ErisPulse-QvQChat) 智能體用的表情包系統：旁聽群聊自動收錄、視覺模型自動下標籤，何時發、發哪張由 AI 自己決定。**

市面上大部分的「鬥圖插件」像台老虎機：訊息一來擲個骰子，從資料夾隨機抽出一張圖，抽到什麼和正在聊什麼毫無關係。這個模組反其道而行。表情包在這裡被視為智能體表達的一部分，離開圖庫有兩條途徑：`send_sticker` 工具——它經 QvQChat 生態橋註冊成一個普通的 AI 工具；以及回覆正文裡的內聯標記 `[表情:關鍵詞]`——QvQChat 4.6.7 起由投遞層直接換成真實表情包，模型不用為此呼叫工具。此刻該不該發一張表情包——接個梗、給個情緒、回敬一句——完全是模型的判斷，唯一的引導是工具描述裡那句「適合輕鬆/調侃/情緒表達場景，正式任務慎用」。整條鏈路沒有計時器、沒有骰子，這正是它與「隨機鬥圖」類插件的本質差異，而且是刻意為之。

「發哪張」的解法相同。模型給出語意查詢（「得意的貓」「委婉地感謝」），模組對圖庫裡每張圖的描述和標籤做確定性的字面比對計分——標籤完全相符記 4 分、標籤子字串 2 分、描述命中 3 分，逐詞累加——再把分數最高的一張發出去。收錄同樣不勞人手：群組裡流過的圖片經 QvQChat 的 `qvq.observe` 事件被順路收下，視覺模型逐張看過，寫一句內容與情緒的描述，再給三到六個適用場景標籤，全程沒人動手。模組對排序做過的唯一調整是新鮮度降權：`recency_hours` 視窗內發過的圖檢索分數減半，免得智能體一整個星期都在重複同一張圖。這是確定性調度，不是機率——近期用過的降權、圖庫滿了按最久未用淘汰，除此之外沒有任何東西會打亂順序。

## 安裝

需要 [QvQChat](https://github.com/wsu2059q/ErisPulse-QvQChat) 4.0.4 以上（本模組對接的 `qvq.observe` 事件與插件設定頁均自該版本導入），基於 ErisPulse 2.8.0+。

```bash
epsdk install ErisPulse-QvQMemes
# 或
pip install ErisPulse-QvQMemes
```

## 啟用

不需要特別啟用。模組裝上後，生態橋自動把它的服務轉成 AI 工具：智能體隨即獲得 `send_sticker`，連同 `sticker_search`、`sticker_stats`、`sticker_add`；解除安裝時這些工具同樣悄無聲息地消失。設定項則出現在 QvQChat Dashboard 設定頁的插件區。

表情包圖庫預設在 `data/QvQMemes/stickers`。從聊天收錄的圖片會下載到這裡，以內容雜湊前 16 位十六進位命名；每次啟動時模組還會掃描該目錄，把你手動放進去的圖片一併入庫——直接丟一個資料夾的表情包進來鋪底，完全是正當用法。

## 工作方式

收錄走的是旁路，絕不阻塞聊天主流程。QvQChat 廣播 `qvq.observe`（它看到的每則群組訊息，每則最多帶 3 張圖）時，模組快速驗證內容後就把工作交給背景任務。背景任務先按 URL 和內容雜湊雙指紋去重，再檢查單會話每日上限，然後序列下載落盤——下載失敗也會保留 URL 參考，圖照樣能發——最後排隊等視覺模型下標籤。標籤經 QvQChat 的 `describe_image` 完成；視覺模型缺失或呼叫失敗時，條目暫記「未分類」，仍可憑描述參與檢索。這條鏈路上沒有哪一環是致命的。所有條目以 `{id, path, url, desc, tags, source, uses, last_used, added}` 的形式存放在框架的鍵值儲存裡。

發送走平台適配器的 SendDSL，框架支援的平台上表現一致：

```python
# 模型呼叫 send_sticker 工具時，生態橋實際執行的就是：
await sdk.module.call("QvQMemes", "send_sticker",
                      query="得意的貓 嘚瑟",
                      platform="qqbot",
                      target_id="123456",
                      target_type="group")
# -> "已發送：一隻翻著白眼的得意白貓｜得意 調侃 反擊"
```

沒有相符結果時，回傳值會說明情況並列出圖庫裡的高頻標籤，模型可以換個說法重試，而不是無聲地失敗。

## 設定

設定項在模組的 ConfigClass 中宣告，QvQChat 設定頁自動渲染成表單；執行值在載入時經 `QvQChat.plugin_config` 拉回，失敗則靜默沿用預設。

| 鍵 | 預設值 | 說明 |
|------|---------|-------------|
| `library_dir` | `data/QvQMemes/stickers` | 圖庫目錄：下載的圖片落在這裡，啟動時掃描收錄已有圖片 |
| `max_library` | `500` | 圖庫容量上限；超出按最久未用淘汰（從未用過的按入庫時間計） |
| `recency_hours` | `72` | 新鮮度降權視窗（小時）：視窗內發過的圖檢索分數減半 |
| `send_method` | `Image` | 發送所用的 SendDSL 方法（圖片 URL 或本機路徑） |
| `auto_collect` | `True` | 旁聽 `qvq.observe` 自動收錄圖片（每則訊息最多 3 張） |
| `collect_per_chat_daily` | `20` | 單會話每日自動收錄上限；≤0 表示不限 |
| `tag_via_vision` | `True` | 收錄後用 QvQChat 視覺模型下標籤；不可用時暫記「未分類」 |

## 服務

五個服務全部在 `get_meta().services` 白名單中。它們只回傳 str 或 dict，絕不拋出例外——內部錯誤一律轉成可讀文字或 `error` 欄位，模型拿到的永遠是一個能接著用的答覆。

| 服務 | 簽名 | 說明 |
|---------|-----------|-------------|
| `send_sticker` | `(query: str, platform: str, target_id: str, target_type: str = "group") -> str` | 按語意查詢選最佳一張並發送；成功回傳「已發送：\<desc\>」，無相符結果時回傳可用標籤提示，失敗回傳錯誤文字 |
| `sticker_search` | `(query: str, top_n: int = 5) -> dict` | 只檢索不發圖：`{query, count, results: [{id, desc, tags, score, url, path, uses}], tag_hints}` |
| `sticker_stats` | `() -> dict` | 圖庫統計：`{total, max_library, untagged, added_today, tag_distribution, top_used}` |
| `sticker_add` | `(url: str = '', path: str = '', desc: str = '', tags: str = '') -> str` | 手動收錄（URL 或本機路徑）；desc/tags 省略時走視覺下標籤 |
| `qvq_settings` | `() -> dict` | 歸屬標記 `{"title": ...}`；宣告該服務的模組才會出現在 QvQChat 設定頁插件區 |

## 授權條款

MIT
