#!/usr/bin/env python3
"""
網頁版：輸入雲端硬碟資料夾 ID，產生合併逐字稿並下載為 .txt
執行後在瀏覽器打開 http://127.0.0.1:5000
"""

import os
import tempfile
import uuid
import threading
from flask import Flask, request, jsonify, send_file, render_template_string

# 確保在專案目錄執行，才能找到 credentials.json / token.json
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 先設定 SSL 再載入 drive_transcript（會用到網路）
import ssl
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except Exception:
    pass

from drive_transcript import (
    process_folder_to_text,
    load_config,
    get_drive_service,
    list_video_files,
    list_folder_any_files,
)

app = Flask(__name__)
jobs = {}  # job_id -> { status, current, total, filename, path?, error? }


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>雲端硬碟影片 → 逐字稿</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: "Segoe UI", "PingFang TC", "Microsoft JhengHei", sans-serif;
      max-width: 560px;
      margin: 0 auto;
      padding: 2rem 1.5rem;
      background: #0f0f12;
      color: #e4e4e7;
      min-height: 100vh;
    }
    h1 {
      font-size: 1.35rem;
      font-weight: 600;
      margin-bottom: 0.5rem;
      color: #fafafa;
    }
    .sub {
      font-size: 0.9rem;
      color: #71717a;
      margin-bottom: 1.75rem;
    }
    label {
      display: block;
      font-size: 0.85rem;
      font-weight: 500;
      color: #a1a1aa;
      margin-bottom: 0.35rem;
    }
    input[type="text"] {
      width: 100%;
      padding: 0.65rem 0.85rem;
      font-size: 0.95rem;
      border: 1px solid #3f3f46;
      border-radius: 8px;
      background: #18181b;
      color: #fafafa;
      margin-bottom: 0.5rem;
    }
    input[type="text"]::placeholder { color: #52525b; }
    input[type="text"]:focus {
      outline: none;
      border-color: #6366f1;
      box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2);
    }
    .hint {
      font-size: 0.8rem;
      color: #71717a;
      margin-bottom: 1.25rem;
    }
    .hint a { color: #818cf8; text-decoration: none; }
    .hint a:hover { text-decoration: underline; }
    button {
      width: 100%;
      padding: 0.75rem 1rem;
      font-size: 1rem;
      font-weight: 500;
      border: none;
      border-radius: 8px;
      background: #6366f1;
      color: white;
      cursor: pointer;
      transition: background 0.15s;
    }
    button:hover:not(:disabled) { background: #4f46e5; }
    button:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
    .limit-row {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      margin-bottom: 1.25rem;
    }
    .limit-row input {
      width: 5rem;
      padding: 0.5rem;
      font-size: 0.9rem;
      border: 1px solid #3f3f46;
      border-radius: 6px;
      background: #18181b;
      color: #fafafa;
    }
    .limit-row label { margin-bottom: 0; }
    #status {
      margin-top: 1.25rem;
      font-size: 0.9rem;
      color: #a1a1aa;
      min-height: 1.5em;
    }
    #status.error { color: #f87171; }
    #status.success { color: #34d399; }
    .progress-wrap {
      margin-top: 1rem;
      display: none;
    }
    .progress-wrap.visible { display: block; }
    .progress-bar {
      height: 8px;
      background: #27272a;
      border-radius: 4px;
      overflow: hidden;
      margin-bottom: 0.5rem;
    }
    .progress-bar .fill {
      height: 100%;
      background: #6366f1;
      border-radius: 4px;
      transition: width 0.2s ease;
    }
    .progress-text { font-size: 0.85rem; color: #a1a1aa; }
    .where-saved {
      margin-top: 1rem;
      font-size: 0.8rem;
      color: #71717a;
      padding: 0.6rem 0.75rem;
      background: #18181b;
      border-radius: 6px;
      border-left: 3px solid #6366f1;
    }
    .speed-hint { font-size: 0.75rem; color: #71717a; margin-top: 0.25rem; }
  </style>
</head>
<body>
  <h1>雲端硬碟影片 → 逐字稿</h1>
  <p class="sub">貼上資料夾 ID，一鍵產生合併文字檔</p>

  <form id="form">
    <label for="folder">資料夾 ID</label>
    <input
      type="text"
      id="folder"
      name="folder"
      placeholder="例：1A2B3C4D5E6F7G8H9I0J"
      required
      autocomplete="off"
    />
    <p class="hint">
      在 <a href="https://drive.google.com" target="_blank" rel="noopener">Google 雲端硬碟</a> 點進資料夾，網址 <code>folders/</code> 後面那串即為 ID。
    </p>
    <div class="limit-row">
      <label for="limit">最多處理</label>
      <input type="number" id="limit" name="limit" min="0" value="0" title="0 = 全部" />
      <span>支影片（0 = 全部）</span>
    </div>
    <div class="limit-row">
      <label for="model">Whisper 模型</label>
      <select id="model" name="model" style="padding:0.5rem; font-size:0.9rem; border-radius:6px; background:#18181b; color:#fafafa; border:1px solid #3f3f46;">
        <option value="tiny">tiny（最快，準度較低）</option>
        <option value="base" selected>base（平衡）</option>
        <option value="small">small（較準，較慢）</option>
        <option value="medium">medium（更準，很慢）</option>
      </select>
      <span class="speed-hint">想加速可選 tiny</span>
    </div>
    <button type="submit" id="btn">讀取並產生逐字稿</button>
  </form>

  <div id="progressWrap" class="progress-wrap" aria-live="polite">
    <div class="progress-bar" role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100">
      <div class="fill" id="progressFill" style="width: 0%;"></div>
    </div>
    <p class="progress-text" id="progressText"></p>
  </div>
  <p id="status" role="status"></p>
  <p id="whereSaved" class="where-saved" style="display:none;">
    完成後檔案會存到：<strong>您電腦的「下載」資料夾</strong>，檔名為「逐字稿.txt」。
  </p>

  <script>
    const form = document.getElementById('form');
    const btn = document.getElementById('btn');
    const status = document.getElementById('status');

    function setStatus(msg, type) {
      status.textContent = msg;
      status.className = type || '';
    }

    const progressWrap = document.getElementById('progressWrap');
    const progressFill = document.getElementById('progressFill');
    const progressText = document.getElementById('progressText');
    const whereSaved = document.getElementById('whereSaved');

    function setProgress(pct, text) {
      progressFill.style.width = pct + '%';
      progressWrap.querySelector('[role="progressbar"]').setAttribute('aria-valuenow', Math.round(pct));
      progressText.textContent = text || '';
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const folderId = document.getElementById('folder').value.trim();
      const limit = parseInt(document.getElementById('limit').value, 10) || 0;
      const model = document.getElementById('model').value || 'base';
      if (!folderId) {
        setStatus('請輸入資料夾 ID', 'error');
        return;
      }
      btn.disabled = true;
      whereSaved.style.display = 'none';
      progressWrap.classList.add('visible');
      setProgress(0, '準備中…');
      setStatus('');
      try {
        const res = await fetch('/process', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ folder_id: folderId, limit: limit, model: model })
        });
        const data = await res.json();
        if (!res.ok) {
          setStatus(data.error || '處理失敗', 'error');
          progressWrap.classList.remove('visible');
          btn.disabled = false;
          return;
        }
        if (!data.job_id) {
          setStatus(data.error || data.message || '未取得任務 ID', 'error');
          progressWrap.classList.remove('visible');
          btn.disabled = false;
          return;
        }
        const jobId = data.job_id;
        setProgress(0, '處理中…');
        const poll = setInterval(async () => {
          try {
            const s = await fetch('/status/' + jobId).then(r => r.json());
            if (s.status === 'processing') {
              const cur = s.current || 0;
              const tot = s.total || 0;
              const pct = tot ? Math.round((cur / tot) * 100) : 0;
              const text = tot ? (cur === 0 ? '準備中（共 ' + tot + ' 支）…' : '第 ' + cur + ' / ' + tot + ' 支：' + (s.filename || '')) : '處理中…';
              setProgress(pct, text);
            } else if (s.status === 'done') {
              clearInterval(poll);
              setProgress(100, '完成');
              setStatus('完成！正在下載…', 'success');
              whereSaved.style.display = 'block';
              const a = document.createElement('a');
              a.href = s.download_url;
              a.download = '逐字稿.txt';
              a.click();
              setStatus('已產生 ' + (s.count || 0) + ' 支影片的逐字稿。', 'success');
              progressWrap.classList.remove('visible');
              btn.disabled = false;
            } else if (s.status === 'error') {
              clearInterval(poll);
              setStatus(s.error || '處理失敗', 'error');
              progressWrap.classList.remove('visible');
              btn.disabled = false;
            }
          } catch (err) {
            clearInterval(poll);
            setStatus('查詢進度失敗：' + err.message, 'error');
            progressWrap.classList.remove('visible');
            btn.disabled = false;
          }
        }, 800);
      } catch (err) {
        setStatus('連線錯誤：' + err.message, 'error');
        progressWrap.classList.remove('visible');
        btn.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


def run_job(job_id, folder_id, limit, model_name, language):
    try:
        def on_progress(current, total, filename):
            jobs[job_id].update(status="processing", current=current, total=total, filename=filename)
        text, count = process_folder_to_text(
            folder_id,
            model_name=model_name,
            language=language,
            limit=limit,
            progress_callback=on_progress,
        )
        if count == 0:
            jobs[job_id].update(status="error", error="沒有產生任何逐字稿。")
            return
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="transcripts_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        jobs[job_id].update(status="done", path=path, count=count, download_url=f"/download/{job_id}")
    except Exception as e:
        jobs[job_id].update(status="error", error=str(e))


@app.route("/process", methods=["POST"])
def process():
    try:
        data = request.get_json() or {}
        folder_id = (data.get("folder_id") or "").strip()
        if not folder_id:
            return jsonify({"error": "請提供資料夾 ID（folder_id）"}), 400
        limit = data.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = None
        config = load_config()
        model_name = (data.get("model") or config.get("whisper_model") or "base").strip()
        if model_name not in ("tiny", "base", "small", "medium", "large"):
            model_name = "base"
        language = config.get("language", "zh")
        service = get_drive_service()
        video_files = list_video_files(service, folder_id=folder_id)
        if not video_files:
            any_files = list_folder_any_files(service, folder_id)
            if not any_files:
                return jsonify({
                    "error": "無法讀取該資料夾。請確認：\n"
                    "1. 資料夾 ID 是否正確（網址中 folders/ 後的那串）\n"
                    "2. 是否已用「擁有」或「編輯」該資料夾的 Google 帳號完成授權",
                    "count": 0,
                }), 400
            mimes = set(f.get("mimeType", "") for f in any_files)
            sample = ", ".join(mimes) if len(mimes) <= 5 else f"{len(mimes)} 種類型"
            return jsonify({
                "error": f"該資料夾內有 {len(any_files)} 個檔案，但沒有偵測到支援的影片格式。\n"
                f"目前支援：mp4, webm, mov, avi, wmv, mkv, mpeg, ogg 等。\n"
                f"資料夾內的檔案類型範例：{sample}",
                "count": 0,
            }), 400
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"status": "processing", "current": 0, "total": 0, "filename": ""}
        thread = threading.Thread(
            target=run_job,
            args=(job_id, folder_id, limit, model_name, language),
        )
        thread.daemon = True
        thread.start()
        return jsonify({"job_id": job_id})
    except FileNotFoundError as e:
        if "credentials.json" in str(e) or "token.json" in str(e):
            return jsonify({
                "error": "尚未設定 Google 授權。請先在終端機執行一次：python drive_transcript.py，完成登入後再使用本頁面。",
            }), 500
        raise
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/status/<job_id>")
def status(job_id):
    if job_id not in jobs:
        return jsonify({"status": "unknown"}), 404
    j = jobs[job_id]
    out = {
        "status": j["status"],
        "current": j.get("current", 0),
        "total": j.get("total", 0),
        "filename": j.get("filename", ""),
    }
    if j.get("status") == "done":
        out["download_url"] = j.get("download_url", "")
        out["count"] = j.get("count", 0)
    if j.get("status") == "error":
        out["error"] = j.get("error", "")
    return jsonify(out)


@app.route("/download/<job_id>")
def download(job_id):
    if job_id not in jobs or jobs[job_id].get("status") != "done":
        return "Not found or not ready", 404
    path = jobs[job_id].get("path")
    if not path or not os.path.isfile(path):
        return "File not found or expired", 404
    return send_file(
        path,
        as_attachment=True,
        download_name="逐字稿.txt",
        mimetype="text/plain; charset=utf-8",
    )


if __name__ == "__main__":
    port = 5001  # 避免與 macOS AirPlay 接收（port 5000）衝突
    print(f"請在瀏覽器打開: http://127.0.0.1:{port}")
    print(f"或: http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True)