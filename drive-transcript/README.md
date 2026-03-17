# Google 雲端硬碟影片 → 逐字稿

從 Google Drive 讀取影片並用 **Whisper** 產生逐字稿（支援中文、英文等）。

## 前置需求

- **Python 3.10+**
- **ffmpeg**（Whisper 解影片用）
  - macOS: `brew install ffmpeg`
  - Windows: 從 [ffmpeg.org](https://ffmpeg.org/download.html) 下載並加入 PATH

## 一、Google Cloud 設定（取得 Drive 存取權）

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)。
2. 建立新專案或選現有專案。
3. **啟用 API**：左側「API 和服務」→「程式庫」→ 搜尋 **Google Drive API** → 啟用。
4. **建立 OAuth 憑證**：
   - 「API 和服務」→「憑證」→「建立憑證」→「OAuth 用戶端 ID」。
   - 若尚未設定 OAuth 同意畫面：選「外部」、填應用程式名稱與支援電子郵件後儲存。
   - 應用程式類型選 **「桌上版應用程式」**，名稱自訂。
   - 建立後下載 JSON，重新命名為 **`credentials.json`**，放到本專案目錄（與 `drive_transcript.py` 同層）。

## 二、安裝依賴

```bash
cd drive-transcript
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 三、設定（選用）

複製 `config.example.json` 為 `config.json`，可修改：

- **drive_folder_id**：只處理某個雲端資料夾時，填該資料夾 ID（網址中 `folders/` 後面那串）。
- **output_dir**：逐字稿輸出目錄，預設 `transcripts`。
- **language**：Whisper 語言，例如 `zh`（中文）、`en`（英文），留空則自動偵測。
- **whisper_model**：`tiny` / `base` / `small` / `medium` / `large`，越大越準但越慢。

## 四、如何指定要處理的資料夾

不指定時會處理「我的雲端硬碟」裡所有影片。若只要處理**某一個資料夾**：

1. 用瀏覽器打開 [Google 雲端硬碟](https://drive.google.com/)。
2. 點進那個資料夾。
3. 看網址列，會長這樣：  
   `https://drive.google.com/drive/folders/1A2B3C4D5E6F7G8H9I0J`  
   **`folders/` 後面那一串**（例如 `1A2B3C4D5E6F7G8H9I0J`）就是**資料夾 ID**。
4. 用下面兩種方式擇一：
   - **指令列**：`python drive_transcript.py --folder "1A2B3C4D5E6F7G8H9I0J"`
   - **設定檔**：複製 `config.example.json` 為 `config.json`，把 `drive_folder_id` 改成 `"1A2B3C4D5E6F7G8H9I0J"`，之後直接執行 `python drive_transcript.py` 就會只處理該資料夾。

## 五、使用方式

### 方式 A：網頁介面（推薦）

在專案目錄執行：

```bash
pip install -r requirements.txt   # 若尚未安裝
python app.py
```

瀏覽器打開 **http://127.0.0.1:5000**，在欄位貼上資料夾 ID，可選「最多處理 N 支影片」（0 = 全部），點「讀取並產生逐字稿」。處理完會自動下載一個合併的 **逐字稿.txt**。

（首次使用前請至少用指令版完成一次 Google 登入，見下方。）

### 方式 B：指令列

```bash
# 第一次執行會開啟瀏覽器登入 Google，授權後會產生 token.json
python drive_transcript.py

# 只列出影片，不下載、不轉錄
python drive_transcript.py --list-only

# 只處理某個資料夾（把下面換成你的資料夾 ID）
python drive_transcript.py --folder "1A2B3C4D5E6F7G8H9I0J"

# 指定 Whisper 模型與只處理前 2 支影片
python drive_transcript.py --model small --limit 2
```

- 逐字稿會存成 `transcripts/影片檔名.txt`，內容含**時間戳**（`[HH:MM:SS.mmm]`）、**標點**與**斷句**（每段一行）。
- 網頁版處理完後會**自動上傳**一份「逐字稿.txt」到該雲端資料夾；本機也會下載一份。
- 若某支影片的 `.txt` 已存在，會自動略過該支。
- **若曾授權過**：程式已加入「上傳到雲端」權限，請**刪除 `token.json`** 後重新執行一次並再次登入 Google，才能上傳成功。

## 六、常見問題

- **網頁版處理很久沒反應**：影片多時會較久（每支都要下載＋轉錄），可先在「最多處理」填 1 測試。
- **SSL certificate verify failed**：已用 certifi 修正，請執行 `pip install -r requirements.txt` 更新依賴後再跑一次。
- **「未找到影片」**：確認 Drive 裡有影片，且 OAuth 登入的帳號有權限。若只處理某資料夾，請填對 `--folder` 或 `config.json` 的 `drive_folder_id`。
- **Whisper 很慢**：先用 `--model tiny` 或 `base` 試跑，再依需要改用 `small` / `medium`。
- **記憶體不足**：改用 `tiny` 或 `base`，或一次用 `--limit 1` 處理一支。

### 如何提升處理速度

1. **faster-whisper（預設）**：已改為優先使用 **faster-whisper**（CTranslate2），同一模型下通常比 openai-whisper 快約 **2～4 倍**。請執行 `pip install -r requirements.txt` 確保已安裝。
2. **選較小的模型**：網頁上選 `tiny`（最快）或 `base`（平衡）；`small` / `medium` 更準但更慢。
3. **有 NVIDIA 顯卡**：faster-whisper 會自動用 GPU（float16）；openai-whisper 則用 fp16，轉錄會快很多。
4. **下載與轉錄重疊**：會預先下載下一支（甚至下下一支），轉錄時不必等網路。

## 七、檔案說明

| 檔案 | 說明 |
|------|------|
| `drive_transcript.py` | 主程式：Drive 列出/下載 + Whisper 轉錄 |
| `app.py` | 網頁版：啟動後在瀏覽器打開 http://127.0.0.1:5000 |
| `credentials.json` | 你從 Google Cloud 下載的 OAuth 憑證（勿提交到 Git） |
| `token.json` | 授權後自動產生，之後會自動沿用 |
| `config.json` | 選用設定（可從 config.example.json 複製） |
| `transcripts/` | 預設逐字稿輸出目錄 |
