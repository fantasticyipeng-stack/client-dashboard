# AI RSS ➜ Gemini ➜ Threads 爆款貼文

這個專案提供一個 Python 腳本，可以：

- 使用 `feedparser` 抓取指定的 AI 新聞 RSS
- 使用 `BeautifulSoup` 從文章頁面擷取正文
- 呼叫 Gemini API，將文章改寫成適合 Threads 的爆款貼文
- 將結果輸出為 Markdown 檔案

## 安裝

在專案根目錄執行：

```bash
pip install -r requirements.txt
```

並在系統環境變數中設定：

- `GEMINI_API_KEY`：你的 Gemini API 金鑰

## 使用方式

預設會抓取 MIT Technology Review 的 AI 主題 RSS，並產生 `threads_posts.md`。

```bash
python generate_threads_posts.py
```

如需修改：

- 要抓取的 RSS：編輯 `generate_threads_posts.py` 中的 `RSS_FEED_URL`
- 文章數量上限：編輯 `MAX_ARTICLES`
- 輸出檔案名稱：編輯 `OUTPUT_MD_PATH`

