#!/usr/bin/env python3
"""
從 Google 雲端硬碟讀取影片並產生逐字稿。
使用 Google Drive API + OpenAI Whisper。
"""

import os
import sys
import ssl

# 解決 Mac 上 Python 下載時的 SSL 憑證錯誤（Whisper 下載模型會用到）
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except Exception:
    pass
import io
import json
import tempfile
import argparse
import threading
import queue
from pathlib import Path

# Google Drive API
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# 優先使用 faster-whisper（約 2–4x 較快），無則用 openai-whisper
USE_FASTER_WHISPER = False
try:
    from faster_whisper import WhisperModel as FasterWhisperModel
    USE_FASTER_WHISPER = True
except Exception:
    pass
if not USE_FASTER_WHISPER:
    print("正在載入 Whisper 模組（首次較慢）...", flush=True)
    import whisper

# 若未安裝上述套件，請執行: pip install -r requirements.txt

# 單一 scope 可讀＋上傳，避免 invalid_scope
SCOPES = ["https://www.googleapis.com/auth/drive"]
DEFAULT_CONFIG = {
    "drive_folder_id": None,
    "video_mime_types": [
        "video/mp4",
        "video/webm",
        "video/quicktime",
        "video/x-msvideo",
        "video/x-ms-wmv",
        "video/x-matroska",
        "video/mpeg",
        "video/ogg",
        "video/3gpp",
        "video/3gpp2",
    ],
    "output_dir": "transcripts",
    "language": "zh",
    "whisper_model": "base",
}


def load_config(path="config.json"):
    """載入 config.json，若不存在則用預設值。"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG.copy()


def get_drive_service(credentials_path="credentials.json", token_path="token.json"):
    """取得已授權的 Google Drive API 服務。"""
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("正在更新授權...", flush=True)
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                print(
                    "請先到 Google Cloud Console 建立 OAuth 2.0 憑證，\n"
                    "下載為 credentials.json 放在此目錄。詳見 README.md。"
                )
                sys.exit(1)
            print("即將開啟瀏覽器，請登入 Google 並點「允許」授權本程式。", flush=True)
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print("授權成功。", flush=True)
    return build("drive", "v3", credentials=creds)


def list_video_files(service, folder_id=None, mime_types=None):
    """列出雲端硬碟中的影片檔案。"""
    mime_types = mime_types or DEFAULT_CONFIG["video_mime_types"]
    mime_query = " or ".join(f"mimeType='{m}'" for m in mime_types)
    q = f"({mime_query}) and trashed=false"
    if folder_id:
        q += f" and '{folder_id}' in parents"
    results = (
        service.files()
        .list(
            q=q,
            pageSize=100,
            fields="nextPageToken, files(id, name, mimeType, size)",
            orderBy="name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return results.get("files", [])


def list_folder_any_files(service, folder_id, page_size=50):
    """列出資料夾內「任何」檔案（不限類型），用來檢查權限與實際內容。"""
    q = f"trashed=false and '{folder_id}' in parents"
    results = (
        service.files()
        .list(
            q=q,
            pageSize=page_size,
            fields="nextPageToken, files(id, name, mimeType)",
            orderBy="name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return results.get("files", [])


def download_file(service, file_id, dest_path):
    """下載單一檔案到本地。"""
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"  下載進度: {int(status.progress() * 100)}%")


def upload_text_to_folder(service, folder_id, content, filename="逐字稿.txt"):
    """將文字內容上傳到指定雲端資料夾。"""
    body = {"name": filename, "parents": [folder_id]}
    if isinstance(content, str):
        content = content.encode("utf-8")
    media = MediaIoBaseUpload(
        io.BytesIO(content),
        mimetype="text/plain; charset=utf-8",
        resumable=True,
    )
    service.files().create(
        body=body,
        media_body=media,
        fields="id, name",
        supportsAllDrives=True,
    ).execute()


def _use_cuda():
    """有 NVIDIA GPU 時回傳 True（faster-whisper / openai-whisper 加速用）。"""
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _sec_to_timestamp(sec):
    """秒數 → [HH:MM:SS.mmm] 格式。"""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"[{h:02d}:{m:02d}:{int(s):02d}.{int((s % 1) * 1000):03d}]"


def load_transcribe_model(model_name="base"):
    """載入轉錄用模型。回傳 (backend, model)，backend 為 'faster' 或 'openai'。"""
    if USE_FASTER_WHISPER:
        device = "cuda" if _use_cuda() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        model = FasterWhisperModel(model_name, device=device, compute_type=compute_type)
        return ("faster", model)
    import whisper
    model = whisper.load_model(model_name)
    return ("openai", model)


def _transcribe_faster(audio_path, model, language="zh"):
    """使用 faster-whisper 轉錄，回傳 (帶時間戳與斷句的文字, None)。"""
    lang = language if language else None
    segments_gen, _ = model.transcribe(audio_path, language=lang, beam_size=1, vad_filter=True)
    lines = []
    for seg in segments_gen:
        text = (seg.text or "").strip()
        if text:
            lines.append(f"{_sec_to_timestamp(seg.start)} {text}")
    return ("\n".join(lines) if lines else "", None)


def _transcribe_openai(audio_path, model, language="zh", with_segments=False):
    """使用 openai-whisper 轉錄。"""
    import whisper
    use_fp16 = _use_cuda()
    result = model.transcribe(
        audio_path,
        language=language if language else None,
        fp16=use_fp16,
        word_timestamps=False,
    )
    text = result["text"].strip()
    segments = result.get("segments") or []
    if not with_segments or not segments:
        return (text, None)
    lines = []
    for seg in segments:
        start = seg.get("start", 0)
        seg_text = (seg.get("text") or "").strip()
        if seg_text:
            lines.append(f"{_sec_to_timestamp(start)} {seg_text}")
    return ("\n".join(lines) if lines else text, segments)


def transcribe_with_whisper(audio_path, model_name="base", language="zh", model=None, backend=None, with_segments=True):
    """
    轉錄單一檔案。優先使用 faster-whisper（較快），否則 openai-whisper。
    回傳 (帶時間戳與斷句的文字, _)。
    """
    if model is None or backend is None:
        b, m = load_transcribe_model(model_name)
        backend = backend or b
        model = model or m
    if backend == "faster":
        return _transcribe_faster(audio_path, model, language)
    return _transcribe_openai(audio_path, model, language, with_segments=with_segments)


def process_folder_to_text(
    folder_id,
    model_name="base",
    language="zh",
    limit=None,
    config_path="config.json",
    progress_callback=None,
):
    """
    處理指定資料夾內影片，回傳合併後的逐字稿文字。
    progress_callback(current_1based, total, filename) 每處理完一支會呼叫一次。
    """
    config = load_config(config_path)
    mime_types = config.get("video_mime_types", DEFAULT_CONFIG["video_mime_types"])
    service = get_drive_service()
    files = list_video_files(service, folder_id=folder_id, mime_types=mime_types)
    if not files:
        return "", 0
    if limit and limit > 0:
        files = files[:limit]
    total = len(files)
    if progress_callback:
        progress_callback(0, total, "")
    backend, model = load_transcribe_model(model_name)
    lines = []
    # 用一個 thread 預先下載下一支，轉錄時與下載重疊以省時間
    download_in = queue.Queue()
    download_out = queue.Queue()

    def worker():
        while True:
            item = download_in.get()
            if item is None:
                break
            file_id, ext = item
            tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
            path = tmp.name
            tmp.close()
            try:
                download_file(service, file_id, path)
            except Exception:
                os.unlink(path)
                download_out.put((None, None))
                continue
            download_out.put((path, file_id))

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    try:
        # 預先排入前 2 支，讓「下載」與「轉錄」重疊，減少等待
        for j in range(min(2, len(files))):
            fi = files[j]
            download_in.put((fi["id"], Path(fi["name"]).suffix or ".mp4"))
        for i, file_info in enumerate(files):
            name = file_info["name"]
            path, _ = download_out.get()
            if path is None:
                continue
            if i + 2 < len(files):
                next_info = files[i + 2]
                download_in.put((next_info["id"], Path(next_info["name"]).suffix or ".mp4"))
            try:
                text, _ = transcribe_with_whisper(
                    path, model_name=model_name, language=language, model=model, backend=backend, with_segments=True
                )
                lines.append(f"=== {name} ===\n\n{text}\n\n")
                if progress_callback:
                    progress_callback(i + 1, total, name)
            finally:
                try:
                    os.unlink(path)
                except Exception:
                    pass
    finally:
        download_in.put(None)
        worker_thread.join()
    full_text = "".join(lines)
    # 上傳逐字稿到同一個雲端資料夾
    try:
        upload_text_to_folder(service, folder_id, full_text, "逐字稿.txt")
    except Exception as e:
        print(f"上傳至雲端失敗: {e}", flush=True)
    return full_text, total


def main():
    parser = argparse.ArgumentParser(description="從 Google Drive 影片產生逐字稿")
    parser.add_argument("--config", default="config.json", help="設定檔路徑")
    parser.add_argument("--folder", help="雲端硬碟資料夾 ID（覆蓋 config）")
    parser.add_argument("--model", default="base", help="Whisper 模型: tiny, base, small, medium, large")
    parser.add_argument("--list-only", action="store_true", help="只列出影片，不下載與轉錄")
    parser.add_argument("--limit", type=int, default=0, help="最多處理幾支影片（0=全部）")
    args = parser.parse_args()

    config = load_config(args.config)
    folder_id = args.folder or config.get("drive_folder_id")
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    print("正在連接 Google 雲端硬碟...", flush=True)
    service = get_drive_service()
    print("正在列出影片檔案...", flush=True)
    files = list_video_files(
        service,
        folder_id=folder_id,
        mime_types=config.get("video_mime_types"),
    )

    if not files:
        print("未找到影片檔案。")
        return
    print(f"找到 {len(files)} 個影片。")

    if args.list_only:
        for f in files:
            print(f"  - {f['name']} (ID: {f['id']})")
        return

    limit = args.limit if args.limit > 0 else len(files)
    model_name = args.model or config.get("whisper_model", "base")
    language = config.get("language", "zh")
    backend, model = load_transcribe_model(model_name)
    if USE_FASTER_WHISPER:
        print("  使用 faster-whisper 引擎（較快）", flush=True)

    for i, file_info in enumerate(files[:limit]):
        name = file_info["name"]
        file_id = file_info["id"]
        ext = Path(name).suffix or ".mp4"
        out_name = Path(name).stem + ".txt"
        out_path = output_dir / out_name
        if out_path.exists():
            print(f"[{i+1}/{limit}] 已存在逐字稿，略過: {name}")
            continue

        print(f"[{i+1}/{limit}] 處理: {name}")
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            try:
                download_file(service, file_id, tmp.name)
                print("  轉錄中...")
                text, _ = transcribe_with_whisper(
                    tmp.name, model_name=model_name, language=language, model=model, backend=backend, with_segments=True
                )
                out_path.write_text(text, encoding="utf-8")
                print(f"  已儲存: {out_path}")
            finally:
                os.unlink(tmp.name)

    print("完成。")


if __name__ == "__main__":
    main()
