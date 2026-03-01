# LINE 定時發送設定

## 為什麼 8:00 沒收到訊息？

**Render 免費方案**在約 15 分鐘沒有流量時會讓服務**休眠**。休眠時程式不會執行，所以**程式內的排程（APScheduler）不會在 8:00 / 12:00 / 20:00 觸發**，LINE 就不會發送。

## 解法：用「外部 Cron」呼叫 API

由外部定時打你的網址，喚醒服務並觸發發送：

1. 使用 **cron-job.org**（免費）或 **Render Cron Jobs**（付費）、**GitHub Actions** 等。
2. 設定每 day 在 **08:00、12:00、20:00（台灣時間）** 各打一次下面網址（GET 即可）：

   ```
   https://video-dashboard-bsj9.onrender.com/api/daily-update
   ```

3. 若 Cron 服務用 **UTC**，台灣時間 = UTC+8，請打：
   - **0:00 UTC** = 8:00 台灣
   - **4:00 UTC** = 12:00 台灣
   - **12:00 UTC** = 20:00 台灣

打一次就會喚醒 Render、執行 `api_daily_update()` 並把每日戰情推到 LINE。

## 環境變數確認

發送前請確認 Render 後台已設定：

| 變數 | 說明 |
|------|------|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API 的 Channel access token |
| `LINE_TARGET_ID` | 要收到訊息的 User ID 或 Group ID（可從 webhook 的 follow/join 事件取得） |

未設定 `LINE_TARGET_ID` 時，程式會印 `[LINE push] No target ID` 且不會發送。

## 其他排程

- **逾期提醒**：程式內排程為每天 `CHECK_HOUR:CHECK_MINUTE`（預設 9:00）。服務休眠時同樣不會執行；若要固定發送，可新增一個 GET 端點觸發逾期檢查，再由外部 cron 呼叫。
- **今天要上傳的影片（16:00）**：程式內 16:00 排程；服務休眠時不會執行，可改由外部 cron 在 16:00 打對應 API（若之後有提供）。
