# Gmail 登入門禁設定

啟用後，只有通過 Google 登入且符合允許名單的帳號可以進入戰情版。

## 1. 環境變數（Render 後台）

| 變數 | 必填 | 說明 |
|------|------|------|
| `GOOGLE_CLIENT_ID` | 啟用時必填 | Google OAuth 2.0 用戶端 ID |
| `GOOGLE_CLIENT_SECRET` | 啟用時必填 | Google OAuth 2.0 用戶端密鑰 |
| `SESSION_SECRET_KEY` | 啟用時必填 | 任意長字串，用來簽署 session cookie（建議 32 字元以上） |
| `ALLOWED_EMAILS` | 啟用時必填其一 | 允許的 Gmail，逗號分隔，例如：`a@gmail.com,b@gmail.com` |
| `ALLOWED_DOMAIN` | 啟用時必填其一 | 允許的網域，例如：`company.com` 表示允許 `*@company.com` |

- **若未設定** `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`：不啟用登入，網站維持誰都能開。
- **啟用登入後**：必須設定 `ALLOWED_EMAILS` 或 `ALLOWED_DOMAIN` 至少一個，否則**不開放任何 Google 帳號**登入（僅指定帳號可 access）。

## 2. 取得 Google OAuth 憑證

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)
2. 建立或選擇專案 → **API 與服務** → **憑證** → **建立憑證** → **OAuth 用戶端 ID**
3. 應用程式類型選 **網頁應用程式**
4. **已授權的重新導向 URI** 新增：
   - 正式站：`https://你的網址.onrender.com/auth/callback`
   - 本機：`http://127.0.0.1:5000/auth/callback`
5. 建立後取得 **用戶端 ID**、**用戶端密鑰**，分別填入 `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`

## 3. 行為說明

- 未登入訪問首頁或任一 `/api/*` → 導向登入頁或回傳 401。
- `/webhook`（LINE）、`/api/line-test`、`/api/daily-update` 不檢查登入（給 LINE 與排程用）。
- 登入後會以 cookie 維持 session，右上有 **登出** 可真正清除並回到登入頁。
