# 每日 Checklist Email 通知設定

## 功能說明

系統會在每天早上 **08:00** 與晚上 **20:00**（台灣時間）寄送 Email 至 `fantasticyipeng@gmail.com`：

| 時段 | 內容 |
|------|------|
| 08:00 | 今日待辦清單（工作／健康／人際關係） |
| 20:00 | 今日完成進度摘要 + 未完成項目 |

可在 dashboard 的「每日清單」頁面勾選完成項目，晚上 Email 會顯示今日進度。

---

## 環境變數

部署前請確認已設定：

| 變數 | 說明 |
|------|------|
| `SMTP_HOST` | Gmail SMTP，預設 `smtp.gmail.com` |
| `SMTP_PORT` | 預設 `587` |
| `SMTP_USER` | Gmail 帳號 |
| `SMTP_PASS` | Gmail **應用程式密碼**（16 位，非登入密碼） |
| `EMAIL_FROM` | 寄件者，通常同 `SMTP_USER` |
| `CHECKLIST_EMAIL_TO` | 收件者信箱 |
| `DASHBOARD_PUBLIC_URL` | dashboard 公開網址（Email 內連結用） |

---

## Render 免費方案：外部 Cron 必備

Render 免費方案在約 15 分鐘沒有流量時會休眠，程式內 APScheduler 不會在休眠時執行。請用 **cron-job.org**（免費）設定外部定時呼叫：

### Job 1：早晨清單（08:00 台灣時間）

- **URL**：`https://video-dashboard-bsj9.onrender.com/api/checklist/morning`
- **方法**：GET
- **Cron（UTC）**：`0 0 * * *`（UTC 00:00 = 台灣 08:00）

### Job 2：晚上回顧（20:00 台灣時間）

- **URL**：`https://video-dashboard-bsj9.onrender.com/api/checklist/evening`
- **方法**：GET
- **Cron（UTC）**：`0 12 * * *`（UTC 12:00 = 台灣 20:00）

### cron-job.org 設定步驟

1. 前往 [https://cron-job.org](https://cron-job.org) 註冊並登入
2. 點「Create cronjob」
3. 填入上述 URL，選擇 GET
4. 時區選 **UTC**，填入對應 cron 表達式
5. 儲存並啟用

---

## 手動測試

本地或部署後可直接打 API 測試：

```bash
# 早晨清單
curl "http://localhost:5000/api/checklist/morning"

# 晚上回顧
curl "http://localhost:5000/api/checklist/evening"

# 取得今日清單（需登入）
curl -b cookies.txt "http://localhost:5000/api/checklist/today"
```

---

## 預設清單項目

首次啟動時會自動建立 15 項預設清單：

- **工作**（5 項）：確認今日最重要 3 件事、檢查逾期任務、回覆訊息、深度工作、整理明日優先順序
- **健康**（5 項）：喝水、運動、三餐、睡前不看螢幕、睡眠
- **人際關係**（5 項）：聯絡重要的人、表達感謝、陪伴家人、回覆訊息、每週深度對話

可在 dashboard「每日清單」頁面勾選追蹤完成進度。
