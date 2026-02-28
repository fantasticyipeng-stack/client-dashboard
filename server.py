import os
import uuid
import json as _json
import hashlib
import hmac
import base64
import traceback
from datetime import datetime, date, timezone, timedelta

import requests as http_requests
from sqlalchemy import text, Integer
from sqlalchemy.types import TypeDecorator
from flask import Flask, request, jsonify, send_file, abort, redirect, session, url_for
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from urllib.parse import urlencode

load_dotenv()

# ── Config ──

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_TARGET_ID = os.getenv("LINE_TARGET_ID", "")

# Google 登入（Gmail）門禁
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", os.urandom(24).hex())
ALLOWED_EMAILS = [e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()]
ALLOWED_DOMAIN = os.getenv("ALLOWED_DOMAIN", "").strip().lower()
AUTH_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and SESSION_SECRET_KEY)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ── App ──

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
    "pool_size": 5,
    "max_overflow": 2,
}
app.config["SECRET_KEY"] = SESSION_SECRET_KEY
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "true").lower() in ("1", "true", "yes")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
CORS(app, supports_credentials=True)
db = SQLAlchemy(app)


def _email_allowed(email):
    if not email:
        return False
    email = email.strip().lower()
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        return False
    if ALLOWED_DOMAIN and not email.endswith("@" + ALLOWED_DOMAIN):
        return False
    return True


def _login_required():
    """若未啟用登入或已登入則不處理；否則 redirect 到登入頁或回傳 401。"""
    if not AUTH_ENABLED:
        return None
    if session.get("email"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "請先登入", "login_url": "/login"}), 401
    return redirect(url_for("login_page"))

# ── LINE Bot (direct REST API, no SDK) ──

LINE_API_REPLY = "https://api.line.me/v2/bot/message/reply"
LINE_API_PUSH = "https://api.line.me/v2/bot/message/push"

TZ_TW = timezone(timedelta(hours=8))

STATUSES = ['待分配', '剪輯中', '初稿修改中', '客戶確認中', '已完成', '已上傳雲端', '已上傳影片']
DONE_STATUSES = ['初稿修改中', '客戶確認中', '已完成', '已上傳雲端', '已上傳影片']

DEFAULT_CLIENTS = ['大可為', 'JGB', '婕絲', '台中市政府數位發展局', '吃喝玩樂', '多德仕', '和居', '恩友友', '萬華街區', '橙果創意', '底迪']
DEFAULT_EDITORS = ['李宥儀', '邱麟晴', '翁薏惠', '陳思妤', '高偉翔', '楊淳惠', '楊斯涵', '王彥鈞', '賴宇柔', '李依珊', '顏佳祐', '賴彥辰', '黃睿妤', '劉恩伶', '鄭樺薇', '胡禎妮', '郭佳柔', '王晨羽']


# ── Models ──
# PostgreSQL on Render has archived as INTEGER (0/1); map bool <-> int so ORM writes work
class BoolAsInteger(TypeDecorator):
    impl = Integer
    cache_ok = True
    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return 1 if value else 0
    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return bool(value)


class Video(db.Model):
    __tablename__ = "videos"
    id = db.Column(db.String(50), primary_key=True)
    client_name = db.Column(db.String(100), nullable=False)
    video_id = db.Column(db.String(50), nullable=False)
    topic = db.Column(db.String(200), nullable=False)
    draft_date = db.Column(db.String(10), nullable=False)
    upload_date = db.Column(db.String(10), nullable=False)
    editor = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    material_link = db.Column(db.Text, default="")
    script_link = db.Column(db.Text, default="")
    view_count = db.Column(db.Integer, default=0)
    platform_views = db.Column(db.Text, default="[]")  # JSON: [{link, platform, view_count}, ...]
    notes = db.Column(db.Text, default="")
    remarks = db.Column(db.Text, default="")
    created_at = db.Column(db.String(30), nullable=False)
    updated_at = db.Column(db.String(30), nullable=False)
    archived = db.Column(BoolAsInteger, default=False)

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return d


class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    archived = db.Column(BoolAsInteger, default=False)
    archive_reason = db.Column(db.String(20), default="")

    def to_dict(self):
        return {"id": self.id, "name": self.name, "archived": self.archived, "archive_reason": self.archive_reason or ""}


class Editor(db.Model):
    __tablename__ = "editors"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    archived = db.Column(BoolAsInteger, default=False)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "archived": self.archived}


class Staff(db.Model):
    __tablename__ = "staff"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

    def to_dict(self):
        return {"id": self.id, "name": self.name}


class ClientProfile(db.Model):
    __tablename__ = "client_profiles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    current_followers = db.Column(db.Integer, default=0)
    target_followers = db.Column(db.Integer, default=0)
    budget_per_video = db.Column(db.Integer, default=0)
    reminders = db.Column(db.Text, default="")
    social_ig = db.Column(db.Text, default="")
    social_threads = db.Column(db.Text, default="")
    social_tiktok = db.Column(db.Text, default="")
    social_fb = db.Column(db.Text, default="")
    social_line_voom = db.Column(db.Text, default="")
    social_youtube = db.Column(db.Text, default="")
    account_manager = db.Column(db.String(100), default="")
    pm = db.Column(db.String(100), default="")

    def to_dict(self):
        return {
            "current_followers": self.current_followers,
            "target_followers": self.target_followers,
            "budget_per_video": self.budget_per_video,
            "reminders": self.reminders,
            "social_ig": self.social_ig or "",
            "social_threads": self.social_threads or "",
            "social_tiktok": self.social_tiktok or "",
            "social_fb": self.social_fb or "",
            "social_line_voom": self.social_line_voom or "",
            "social_youtube": self.social_youtube or "",
            "account_manager": self.account_manager or "",
            "pm": self.pm or "",
        }


class EditorProfile(db.Model):
    __tablename__ = "editor_profiles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    work_status = db.Column(db.String(20), default="開工")
    return_date = db.Column(db.String(10), default="")
    pros = db.Column(db.Text, default="")
    cons = db.Column(db.Text, default="")
    suitable_clients = db.Column(db.Text, default="[]")
    notes = db.Column(db.Text, default="")

    def to_dict(self):
        import json
        return {
            "work_status": self.work_status,
            "return_date": self.return_date,
            "pros": self.pros,
            "cons": self.cons,
            "suitable_clients": json.loads(self.suitable_clients or "[]"),
            "notes": self.notes,
        }


class DidiMedia(db.Model):
    __tablename__ = "didi_media"
    id = db.Column(db.String(50), primary_key=True)
    title = db.Column(db.String(200), default="")
    date = db.Column(db.String(10), default="")
    platform = db.Column(db.String(20), default="IG")
    view_count = db.Column(db.Integer, default=0)
    sponsor_fee = db.Column(db.Float, default=0)
    remarks = db.Column(db.Text, default="")

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class DidiSpeech(db.Model):
    __tablename__ = "didi_speech"
    id = db.Column(db.String(50), primary_key=True)
    org = db.Column(db.String(200), default="")
    date = db.Column(db.String(10), default="")
    location = db.Column(db.String(200), default="")
    topic = db.Column(db.String(200), default="")
    photo_url = db.Column(db.Text, default="")
    feedback = db.Column(db.Text, default="")
    speaker_fee = db.Column(db.Float, default=0)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class DidiSoftware(db.Model):
    __tablename__ = "didi_software"
    id = db.Column(db.String(50), primary_key=True)
    client_name = db.Column(db.String(200), default="")
    monthly_fee = db.Column(db.Float, default=0)
    months = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="訂閱中")  # 訂閱中 / 已結束
    start_date = db.Column(db.String(10), default="")

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


def seed_defaults():
    """僅在資料庫為空時寫入預設客戶與剪輯；避免每次啟動都跑大量查詢。"""
    try:
        if Client.query.limit(1).first() is not None:
            return
        for name in DEFAULT_CLIENTS:
            db.session.add(Client(name=name))
        for name in DEFAULT_EDITORS:
            db.session.add(Editor(name=name))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[seed_defaults] {e}")


# ── API: Serve frontend ──

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>登入 - 小跟拍戰情版</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", sans-serif; background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
.card { background: #fff; border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,.2); padding: 40px; max-width: 380px; width: 100%; text-align: center; }
.card h1 { font-size: 20px; color: #1e293b; margin-bottom: 8px; }
.card p { color: #64748b; font-size: 14px; margin-bottom: 24px; }
.btn-google { display: inline-flex; align-items: center; justify-content: center; gap: 10px; width: 100%; padding: 12px 20px; background: #fff; border: 1px solid #dadce0; border-radius: 8px; font-size: 15px; font-weight: 500; color: #3c4043; cursor: pointer; text-decoration: none; transition: background .2s; }
.btn-google:hover { background: #f8f9fa; }
.btn-google svg { width: 20px; height: 20px; }
</style>
</head>
<body>
<div class="card">
  <h1>小跟拍戰情版</h1>
  <p>請使用 Google 帳號登入</p>
  <a href="/auth/google" class="btn-google">
    <svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>
    使用 Gmail 登入
  </a>
</div>
</body>
</html>
"""


@app.route("/login")
def login_page():
    if not AUTH_ENABLED:
        return redirect(url_for("index"))
    if session.get("email"):
        return redirect(url_for("index"))
    return LOGIN_HTML


@app.route("/auth/google")
def auth_google():
    if not AUTH_ENABLED:
        return redirect(url_for("index"))
    redirect_uri = request.host_url.rstrip("/") + "/auth/callback"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@app.route("/auth/callback")
def auth_callback():
    if not AUTH_ENABLED:
        return redirect(url_for("index"))
    code = request.args.get("code")
    if not code:
        return redirect(url_for("login_page"))
    redirect_uri = request.host_url.rstrip("/") + "/auth/callback"
    try:
        r = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        r.raise_for_status()
        token = r.json().get("access_token")
        if not token:
            return redirect(url_for("login_page"))
        user = http_requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": "Bearer " + token},
            timeout=10,
        )
        user.raise_for_status()
        data = user.json()
        email = (data.get("email") or "").strip().lower()
        if not _email_allowed(email):
            return (
                "<script>alert('此帳號沒有權限'); location.href='/login';</script>",
                403,
                {"Content-Type": "text/html; charset=utf-8"},
            )
        session["email"] = email
        session["name"] = (data.get("name") or email).strip()
        return redirect(url_for("index"))
    except Exception as e:
        print("[auth/callback] " + str(e))
        return redirect(url_for("login_page"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page") if AUTH_ENABLED else url_for("index"))


@app.before_request
def require_login():
    path = request.path
    if not AUTH_ENABLED:
        return None
    if path in ("/health", "/login", "/logout") or path.startswith("/auth/") or path == "/logo.png":
        return None
    if path == "/webhook":
        return None
    if path == "/api/line-test" or path == "/api/daily-update":
        return None
    if path == "/" or path.startswith("/api/"):
        return _login_required()
    return None


@app.route("/")
def index():
    if AUTH_ENABLED and not session.get("email"):
        return redirect(url_for("login_page"))
    return send_file("dashboard.html")


@app.route("/logo.png")
def logo():
    return send_file("logo.png", mimetype="image/png")


@app.route("/health")
def health():
    """輕量健康檢查，不查 DB，讓 Render 可快速判定服務已就緒。"""
    return "", 200


@app.route("/favicon.ico")
@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def favicon():
    """避免瀏覽器 / LINE 一直打這些網址造成 404 log。沒有圖就回 204 No Content。"""
    if os.path.isfile(os.path.join(os.path.dirname(__file__), "favicon.ico")):
        return send_file("favicon.ico", mimetype="image/x-icon")
    return "", 204


# ── API: Batch (一次取得戰情版所需資料，減少請求數) ──

@app.route("/api/dashboard", methods=["GET"])
def get_dashboard():
    """Single request that returns videos, clients, editors, profiles, staff, didi data."""
    # PostgreSQL may have archived as INTEGER; use text() to avoid integer=boolean error
    videos_active = Video.query.filter(text("videos.archived = 0")).order_by(Video.draft_date.asc()).all()
    videos_archived = Video.query.filter(text("videos.archived = 1")).order_by(Video.draft_date.asc()).all()
    clients_active = Client.query.filter(text("clients.archived = 0")).order_by(Client.id).all()
    clients_archived = Client.query.filter(text("clients.archived = 1")).order_by(Client.id).all()
    editors_active = Editor.query.filter(text("editors.archived = 0")).order_by(Editor.id).all()
    editors_archived = Editor.query.filter(text("editors.archived = 1")).order_by(Editor.id).all()
    client_profiles_rows = ClientProfile.query.all()
    editor_profiles_rows = EditorProfile.query.all()
    staff_rows = Staff.query.order_by(Staff.id).all()
    didi_media_rows = DidiMedia.query.all()
    didi_speech_rows = DidiSpeech.query.all()
    didi_software_rows = DidiSoftware.query.all()
    return jsonify({
        "videos": [r.to_dict() for r in videos_active],
        "videos_archived": [r.to_dict() for r in videos_archived],
        "clients": [r.to_dict() for r in clients_active],
        "archived_clients": [r.to_dict() for r in clients_archived],
        "editors": [r.to_dict() for r in editors_active],
        "archived_editors": [r.to_dict() for r in editors_archived],
        "client_profiles": {r.name: r.to_dict() for r in client_profiles_rows},
        "editor_profiles": {r.name: r.to_dict() for r in editor_profiles_rows},
        "staff": [r.to_dict() for r in staff_rows],
        "didi_media": [r.to_dict() for r in didi_media_rows],
        "didi_speech": [r.to_dict() for r in didi_speech_rows],
        "didi_software": [r.to_dict() for r in didi_software_rows],
    })


# ── API: Videos ──

@app.route("/api/videos", methods=["GET"])
def list_videos():
    show_archived = request.args.get("archived") == "1"
    q = Video.query.filter(text("videos.archived = 1" if show_archived else "videos.archived = 0"))
    rows = q.order_by(Video.draft_date.asc()).all()
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/videos", methods=["POST"])
def create_video():
    d = request.json
    now = datetime.now().isoformat()
    v = Video(
        id=uuid.uuid4().hex[:16],
        client_name=d["client_name"],
        video_id=d["video_id"],
        topic=d["topic"],
        draft_date=d["draft_date"],
        upload_date=d["upload_date"],
        editor=d["editor"],
        status=d["status"],
        material_link=d.get("material_link", ""),
        script_link=d.get("script_link", ""),
        view_count=d.get("view_count", 0),
        platform_views=_json.dumps(d.get("platform_views") or []),
        notes=d.get("notes", ""),
        remarks=d.get("remarks", ""),
        created_at=now,
        updated_at=now,
    )
    db.session.add(v)
    db.session.commit()
    return jsonify({"id": v.id, "message": "created"}), 201


@app.route("/api/videos/<vid>", methods=["PUT"])
def update_video(vid):
    v = Video.query.get(vid)
    if not v:
        abort(404)
    d = request.json
    for key in ["client_name", "video_id", "topic", "draft_date", "upload_date",
                 "editor", "status", "material_link", "script_link", "view_count", "notes", "remarks", "archived"]:
        if key in d:
            setattr(v, key, d[key])
    if "platform_views" in d:
        pv = d["platform_views"]
        v.platform_views = _json.dumps(pv) if isinstance(pv, list) else (pv or "[]")
    v.updated_at = datetime.now().isoformat()
    db.session.commit()
    return jsonify({"message": "updated"})


@app.route("/api/videos/<vid>", methods=["DELETE"])
def delete_video(vid):
    v = Video.query.get(vid)
    if v:
        v.archived = True
        v.updated_at = datetime.now().isoformat()
        db.session.commit()
    return jsonify({"message": "archived"})


@app.route("/api/videos/<vid>/archive", methods=["POST", "PUT", "PATCH"])
def archive_video(vid):
    v = Video.query.get(vid)
    if not v:
        abort(404)
    v.archived = True
    v.updated_at = datetime.now().isoformat()
    db.session.commit()
    return jsonify({"message": "archived"})


@app.route("/api/videos/<vid>/unarchive", methods=["POST", "PUT", "PATCH"])
def unarchive_video(vid):
    v = Video.query.get(vid)
    if not v:
        abort(404)
    v.archived = False
    v.updated_at = datetime.now().isoformat()
    db.session.commit()
    return jsonify({"message": "unarchived"})


@app.route("/api/videos/<vid>/delete_permanent", methods=["POST", "DELETE"])
def delete_video_permanent(vid):
    v = Video.query.get(vid)
    if not v:
        abort(404)
    db.session.delete(v)
    db.session.commit()
    return jsonify({"message": "deleted"})


# ── API: Clients ──

@app.route("/api/clients", methods=["GET"])
def list_clients():
    rows = Client.query.order_by(Client.id).all()
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/clients", methods=["POST"])
def add_client():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"error": "名稱不可為空"}), 400
    if Client.query.filter_by(name=name).first():
        return jsonify({"error": "此客戶已存在"}), 409
    c = Client(name=name)
    db.session.add(c)
    db.session.commit()
    return jsonify({"id": c.id, "name": c.name}), 201


@app.route("/api/clients/<int:cid>", methods=["DELETE"])
def delete_client(cid):
    c = Client.query.get(cid)
    if c:
        db.session.delete(c)
        db.session.commit()
    return jsonify({"message": "deleted"})


@app.route("/api/clients/<int:cid>/archive", methods=["POST"])
def archive_client(cid):
    c = Client.query.get(cid)
    if not c:
        abort(404)
    reason = (request.json or {}).get("reason", "").strip()
    if reason not in ("結案", "失敗"):
        return jsonify({"error": "reason 須為「結案」或「失敗」"}), 400
    c.archived = True
    c.archive_reason = reason
    db.session.commit()
    return jsonify({"message": "archived"})


@app.route("/api/clients/<int:cid>/unarchive", methods=["POST", "PUT", "PATCH"])
def unarchive_client(cid):
    c = Client.query.get(cid)
    if not c:
        abort(404)
    c.archived = False
    c.archive_reason = ""
    db.session.commit()
    return jsonify({"message": "unarchived"})


# ── API: Editors ──

@app.route("/api/editors", methods=["GET"])
def list_editors():
    show_archived = request.args.get("archived") == "1"
    q = Editor.query.filter(text("editors.archived = 1" if show_archived else "editors.archived = 0"))
    rows = q.order_by(Editor.id).all()
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/editors", methods=["POST"])
def add_editor():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"error": "名稱不可為空"}), 400
    if Editor.query.filter_by(name=name).first():
        return jsonify({"error": "此人員已存在"}), 409
    e = Editor(name=name)
    db.session.add(e)
    db.session.commit()
    return jsonify({"id": e.id, "name": e.name}), 201


@app.route("/api/editors/<int:eid>", methods=["DELETE"])
def delete_editor(eid):
    e = Editor.query.get(eid)
    if e:
        e.archived = True
        db.session.commit()
    return jsonify({"message": "archived"})


@app.route("/api/editors/<int:eid>/archive", methods=["POST"])
def archive_editor(eid):
    e = Editor.query.get(eid)
    if not e:
        abort(404)
    e.archived = True
    db.session.commit()
    return jsonify({"message": "archived"})


@app.route("/api/editors/<int:eid>/unarchive", methods=["POST", "PUT", "PATCH"])
def unarchive_editor(eid):
    e = Editor.query.get(eid)
    if not e:
        abort(404)
    e.archived = False
    db.session.commit()
    return jsonify({"message": "unarchived"})


@app.route("/api/editors/<int:eid>/delete_permanent", methods=["POST", "DELETE"])
def delete_editor_permanent(eid):
    e = Editor.query.get(eid)
    if not e:
        abort(404)
    db.session.delete(e)
    db.session.commit()
    return jsonify({"message": "deleted"})


# ── API: Staff (負責業務/PM 名單) ──

DEFAULT_STAFF_NAMES = ["范以芃", "葉思宏"]


@app.route("/api/staff", methods=["GET"])
def list_staff():
    rows = Staff.query.order_by(Staff.id).all()
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/staff", methods=["POST"])
def add_staff():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "名稱不可為空"}), 400
    existing = Staff.query.filter_by(name=name).first()
    if existing:
        return jsonify(existing.to_dict()), 201
    row = Staff(name=name)
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": row.id, "name": row.name}), 201


# ── API: Client Profiles ──

@app.route("/api/client-profiles", methods=["GET"])
def get_client_profiles():
    rows = ClientProfile.query.all()
    return jsonify({r.name: r.to_dict() for r in rows})


@app.route("/api/client-profiles", methods=["POST"])
def save_client_profile():
    d = request.json
    name = d.get("name", "").strip()
    if not name:
        return jsonify({"error": "名稱不可為空"}), 400
    row = ClientProfile.query.filter_by(name=name).first()
    if not row:
        row = ClientProfile(name=name)
        db.session.add(row)
    row.current_followers = d.get("current_followers", 0)
    row.target_followers = d.get("target_followers", 0)
    row.budget_per_video = d.get("budget_per_video", 0)
    row.reminders = d.get("reminders", "")
    row.social_ig = d.get("social_ig", "")
    row.social_threads = d.get("social_threads", "")
    row.social_tiktok = d.get("social_tiktok", "")
    row.social_fb = d.get("social_fb", "")
    row.social_line_voom = d.get("social_line_voom", "")
    row.social_youtube = d.get("social_youtube", "")
    row.account_manager = d.get("account_manager", "") or ""
    row.pm = d.get("pm", "") or ""
    db.session.commit()
    return jsonify({"message": "saved"})


# ── API: Editor Profiles ──

@app.route("/api/editor-profiles", methods=["GET"])
def get_editor_profiles():
    rows = EditorProfile.query.all()
    return jsonify({r.name: r.to_dict() for r in rows})


@app.route("/api/editor-profiles", methods=["POST"])
def save_editor_profile():
    import json as _json
    d = request.json
    name = d.get("name", "").strip()
    if not name:
        return jsonify({"error": "名稱不可為空"}), 400
    row = EditorProfile.query.filter_by(name=name).first()
    if not row:
        row = EditorProfile(name=name)
        db.session.add(row)
    row.work_status = d.get("work_status", "開工")
    row.return_date = d.get("return_date", "")
    row.pros = d.get("pros", "")
    row.cons = d.get("cons", "")
    row.suitable_clients = _json.dumps(d.get("suitable_clients", []), ensure_ascii=False)
    row.notes = d.get("notes", "")
    db.session.commit()
    return jsonify({"message": "saved"})


# ── API: Didi Media ──

@app.route("/api/didi-media", methods=["GET"])
def list_didi_media():
    rows = DidiMedia.query.order_by(DidiMedia.date.desc()).all()
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/didi-media", methods=["POST"])
def save_didi_media():
    items = request.json
    if not isinstance(items, list):
        return jsonify({"error": "expected array"}), 400
    DidiMedia.query.delete()
    for d in items:
        db.session.add(DidiMedia(
            id=d.get("id", uuid.uuid4().hex[:16]),
            title=d.get("title", ""),
            date=d.get("date", ""),
            platform=d.get("platform", "IG"),
            view_count=d.get("view_count", 0),
            sponsor_fee=d.get("sponsor_fee", 0),
            remarks=d.get("remarks", ""),
        ))
    db.session.commit()
    return jsonify({"message": "saved"})


# ── API: Didi Speech ──

@app.route("/api/didi-speech", methods=["GET"])
def list_didi_speech():
    rows = DidiSpeech.query.order_by(DidiSpeech.date.desc()).all()
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/didi-speech", methods=["POST"])
def save_didi_speech():
    items = request.json
    if not isinstance(items, list):
        return jsonify({"error": "expected array"}), 400
    DidiSpeech.query.delete()
    for d in items:
        db.session.add(DidiSpeech(
            id=d.get("id", uuid.uuid4().hex[:16]),
            org=d.get("org", ""),
            date=d.get("date", ""),
            location=d.get("location", ""),
            topic=d.get("topic", ""),
            photo_url=d.get("photo_url", ""),
            feedback=d.get("feedback", ""),
            speaker_fee=d.get("speaker_fee", 0),
        ))
    db.session.commit()
    return jsonify({"message": "saved"})


# ── API: Didi Software ──

@app.route("/api/didi-software", methods=["GET"])
def list_didi_software():
    rows = DidiSoftware.query.order_by(DidiSoftware.id).all()
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/didi-software", methods=["POST"])
def save_didi_software():
    items = request.json
    if not isinstance(items, list):
        return jsonify({"error": "expected array"}), 400
    DidiSoftware.query.delete()
    for d in items:
        db.session.add(DidiSoftware(
            id=d.get("id", uuid.uuid4().hex[:16]),
            client_name=d.get("client_name", ""),
            monthly_fee=d.get("monthly_fee", 0),
            months=d.get("months", 0),
            status=d.get("status", "訂閱中"),
            start_date=d.get("start_date", ""),
        ))
    db.session.commit()
    return jsonify({"message": "saved"})


# ── Overdue logic ──
# 1. 初稿繳交時間超過今天，但還沒把狀態從「剪輯中」改成「初稿修改中」
# 2. 影片預定上傳時間已經超過，但還沒把狀態改成「已上傳影片」

def get_overdue_items():
    today = datetime.now(TZ_TW).date().isoformat()
    base = Video.query.filter(text("videos.archived = 0"))
    draft_overdue = base.filter(
        Video.status == "剪輯中",
        Video.draft_date < today,
    ).order_by(Video.draft_date.asc()).all()
    upload_overdue = base.filter(
        Video.status != "已上傳影片",
        Video.upload_date < today,
    ).order_by(Video.upload_date.asc()).all()
    return draft_overdue, upload_overdue


def build_overdue_message(draft_overdue, upload_overdue):
    today = datetime.now(TZ_TW).date()
    parts = []
    if draft_overdue:
        parts.append(f"⚠️ 初稿逾期（{len(draft_overdue)} 筆）— 初稿日已過，狀態仍為「剪輯中」\n" + "─" * 20)
        for i, d in enumerate(draft_overdue, 1):
            draft = datetime.strptime(d.draft_date, "%Y-%m-%d").date()
            overdue_days = (today - draft).days
            parts.append(f"{i}. {d.client_name}｜{d.video_id}\n   主題：{d.topic}\n   剪輯：{d.editor}\n   初稿日：{d.draft_date}（已逾期 {overdue_days} 天）")
        parts.append("")
    if upload_overdue:
        parts.append(f"⚠️ 上片逾期（{len(upload_overdue)} 筆）— 預定上傳日已過，尚未改為「已上傳影片」\n" + "─" * 20)
        for i, d in enumerate(upload_overdue, 1):
            ud = datetime.strptime(d.upload_date, "%Y-%m-%d").date()
            overdue_days = (today - ud).days
            parts.append(f"{i}. {d.client_name}｜{d.video_id}\n   主題：{d.topic}\n   狀態：{d.status}\n   預定上片：{d.upload_date}（已逾期 {overdue_days} 天）")
        parts.append("")
    if not parts:
        return None
    return "\n".join(parts) + "請盡速處理！\n回覆「指令」查看可用操作。"


def _line_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }


def send_line_push(message, target_id=None):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[LINE push] No access token configured")
        return False
    tid = target_id or LINE_TARGET_ID
    if not tid:
        print("[LINE push] No target ID")
        return False
    try:
        resp = http_requests.post(LINE_API_PUSH, headers=_line_headers(), json={
            "to": tid,
            "messages": [{"type": "text", "text": message}],
        })
        print(f"[LINE push] status={resp.status_code} body={resp.text[:200]}")
        return resp.status_code == 200
    except Exception as e:
        print(f"[LINE push error] {e}")
        return False


CLIENT_PRICES = {
    '大可為': 4000, 'JGB': 2500, '婕絲': 2500,
    '台中市政府數位發展局': 10000, '吃喝玩樂': 4000, '多德仕': 4000,
    '和居': 10000, '恩友友': 10000, '萬華街區': 0,
    '橙果創意': -1, '底迪': 0,
}
REVENUE_TARGET = 5000000


def calc_total_revenue():
    """Calculate total revenue: prefer client profile budget_per_video, else CLIENT_PRICES."""
    client_rev = 0
    uploaded = Video.query.filter_by(status='已上傳影片').all()
    for v in uploaded:
        profile = ClientProfile.query.filter_by(name=v.client_name).first()
        if profile and (profile.budget_per_video or 0) > 0:
            client_rev += profile.budget_per_video
            continue
        price = CLIENT_PRICES.get(v.client_name, 0)
        if price == -1:
            # 橙果創意: IG 觀看數破 5500 才算 5500 元
            ig_views = 0
            try:
                pv = _json.loads(v.platform_views or "[]")
                for row in pv:
                    if isinstance(row, dict) and row.get("platform") == "IG":
                        ig_views += int(row.get("view_count") or 0)
            except Exception:
                ig_views = v.view_count or 0
            if ig_views >= 5500:
                client_rev += 5500
        elif price > 0:
            client_rev += price

    media_rev = sum(r.sponsor_fee or 0 for r in DidiMedia.query.all())
    speech_rev = sum(r.speaker_fee or 0 for r in DidiSpeech.query.all())
    software_rev = sum((r.monthly_fee or 0) * (r.months or 0) for r in DidiSoftware.query.all())
    didi_rev = media_rev + speech_rev + software_rev

    return client_rev + didi_rev


def get_urgent_videos():
    """Videos due for upload within 3 days that aren't done yet."""
    today = datetime.now(TZ_TW).date()
    deadline = (today + timedelta(days=3)).isoformat()
    today_str = today.isoformat()
    return Video.query.filter(
        text("videos.archived = 0"),
        Video.status.notin_(['已完成', '已上傳雲端', '已上傳影片']),
        Video.upload_date <= deadline,
        Video.upload_date >= today_str,
    ).order_by(Video.upload_date.asc()).all()


def get_today_upload_videos():
    """今天要上傳的影片：預定上傳日＝今天，且尚未改為已上傳影片。"""
    today = datetime.now(TZ_TW).date().isoformat()
    return Video.query.filter(
        text("videos.archived = 0"),
        Video.upload_date == today,
        Video.status != "已上傳影片",
    ).order_by(Video.client_name.asc()).all()


def build_today_upload_message():
    items = get_today_upload_videos()
    today = datetime.now(TZ_TW).date().isoformat()
    lines = [f"📅 今天要上傳的影片（{today}）", "─" * 20, ""]
    if not items:
        lines.append("目前沒有排定今天上傳的影片。")
    else:
        lines.append(f"共 {len(items)} 支：")
        for v in items:
            lines.append(f"  • {v.client_name}｜{v.video_id}｜{v.topic}｜{v.status}")
    return "\n".join(lines)


def build_daily_update():
    today = datetime.now(TZ_TW).date()
    year_end = date(today.year, 12, 31)
    days_left = (year_end - today).days or 1

    urgent = get_urgent_videos()
    total_rev = calc_total_revenue()
    remaining = REVENUE_TARGET - total_rev
    daily_needed = remaining / days_left if remaining > 0 else 0

    lines = [f"📊 每日戰情更新（{today.isoformat()}）", "─" * 20, ""]

    lines.append(f"🎬 三天內要上傳但還沒好的影片：{len(urgent)} 支")
    if urgent:
        for v in urgent:
            lines.append(f"  • {v.client_name}｜{v.video_id}｜上片日 {v.upload_date}｜{v.status}")
    lines.append("")

    lines.append(f"💰 年度目標：${REVENUE_TARGET:,.0f}")
    lines.append(f"💵 目前收入：${total_rev:,.0f}")
    if remaining > 0:
        lines.append(f"📉 還差：${remaining:,.0f}")
        lines.append(f"⏱ 剩餘 {days_left} 天，平均每天需賺 ${daily_needed:,.0f}")
    else:
        lines.append(f"🎉 已達標！超出 ${-remaining:,.0f}")

    return "\n".join(lines)


_daily_sent_date = None

def scheduled_daily_update():
    global _daily_sent_date
    with app.app_context():
        try:
            today = datetime.now(TZ_TW).date()
            if _daily_sent_date == today:
                print("[Scheduler] Daily update already sent today, skipping")
                return
            db.session.rollback()
            msg = build_daily_update()
            ok = send_line_push(msg)
            if ok:
                _daily_sent_date = today
            print(f"[Scheduler] Daily update sent, success={ok}")
        except Exception as e:
            db.session.rollback()
            print(f"[Scheduler] Daily update error: {e}")


def scheduled_today_upload():
    """每天 16:00 傳送「今天要上傳的影片」"""
    with app.app_context():
        try:
            db.session.rollback()
            msg = build_today_upload_message()
            ok = send_line_push(msg)
            print(f"[Scheduler] Today's upload list sent at 16:00, success={ok}")
        except Exception as e:
            db.session.rollback()
            print(f"[Scheduler] Today upload error: {e}")


def scheduled_overdue_check():
    with app.app_context():
        try:
            db.session.rollback()
            draft_overdue, upload_overdue = get_overdue_items()
            msg = build_overdue_message(draft_overdue, upload_overdue)
            if msg:
                total = len(draft_overdue) + len(upload_overdue)
                ok = send_line_push(msg)
                print(f"[Scheduler] Sent overdue alert: {total} items, success={ok}")
            else:
                print("[Scheduler] No overdue items.")
        except Exception as e:
            db.session.rollback()
            print(f"[Scheduler] Overdue check error: {e}")


@app.route("/api/check-overdue", methods=["POST"])
def api_check_overdue():
    draft_overdue, upload_overdue = get_overdue_items()
    msg = build_overdue_message(draft_overdue, upload_overdue)
    total = len(draft_overdue) + len(upload_overdue)
    if not msg:
        return jsonify({"message": "沒有逾期任務", "count": 0, "sent": False})
    ok = send_line_push(msg)
    return jsonify({
        "message": f"已發送 {total} 筆逾期提醒" if ok else "LINE 未設定或傳送失敗",
        "count": total,
        "sent": ok,
    })


# ── LINE Webhook ──

def _verify_signature(body_bytes, signature):
    if not LINE_CHANNEL_SECRET:
        print("[Signature] No channel secret configured")
        return False
    gen = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(gen).decode("utf-8")
    ok = hmac.compare_digest(signature, expected)
    if not ok:
        print(f"[Signature] Mismatch: got={signature[:20]}... expected={expected[:20]}...")
    return ok


@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return "OK", 200

    body_bytes = request.get_data()
    body_str = body_bytes.decode("utf-8")
    signature = request.headers.get("X-Line-Signature", "")
    print(f"[Webhook] POST received, body_len={len(body_bytes)}, sig={signature[:20]}...")

    sig_ok = _verify_signature(body_bytes, signature)
    if not sig_ok:
        print("[Webhook] Signature verification failed — processing anyway for debugging")

    try:
        data = _json.loads(body_str)
        events = data.get("events", [])
        print(f"[Webhook] {len(events)} event(s)")

        for event in events:
            etype = event.get("type")
            reply_token = event.get("replyToken")
            print(f"[Webhook] event type={etype}, replyToken={reply_token[:10] if reply_token else 'None'}...")

            if etype == "message" and event.get("message", {}).get("type") == "text":
                text = event["message"]["text"]
                print(f"[LINE] Message: '{text}'")
                handle_line_command(reply_token, text)

            elif etype == "follow":
                uid = event.get("source", {}).get("userId", "unknown")
                print(f"[LINE] User followed: {uid}")
                reply_line(reply_token, f"歡迎使用短影片管理機器人！\n\n你的 User ID：\n{uid}\n\n回覆「指令」查看所有操作。")

            elif etype == "join":
                gid = event.get("source", {}).get("groupId", "unknown")
                print(f"[LINE] Joined group: {gid}")
                reply_line(reply_token, f"已加入群組！\n\n群組 ID：\n{gid}\n\n請將此 ID 設為 LINE_TARGET_ID。\n回覆「指令」查看所有操作。")

    except Exception as e:
        print(f"[Webhook] Error: {e}")
        traceback.print_exc()

    return "OK", 200


def reply_line(reply_token, text):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[LINE reply] No access token")
        return
    if not reply_token:
        print("[LINE reply] No reply token")
        return
    try:
        resp = http_requests.post(LINE_API_REPLY, headers=_line_headers(), json={
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": text}],
        })
        print(f"[LINE reply] status={resp.status_code} body={resp.text[:200]}")
    except Exception as e:
        print(f"[LINE reply error] {e}")
        traceback.print_exc()


def handle_line_command(reply_token, text):
    """Respond to any message with the daily update."""
    text = text.strip()
    if text in ("戰情", "更新", "報告", "status"):
        msg = build_daily_update()
        reply_line(reply_token, msg)
    elif text == "逾期":
        draft_overdue, upload_overdue = get_overdue_items()
        msg = build_overdue_message(draft_overdue, upload_overdue)
        if not msg:
            reply_line(reply_token, "目前沒有逾期任務 👍")
        else:
            reply_line(reply_token, msg)
    else:
        msg = build_daily_update()
        reply_line(reply_token, msg)

@app.route("/api/line-test", methods=["POST"])
def api_line_test():
    """Send a test message to verify LINE API connectivity."""
    ok = send_line_push("LINE 機器人連線測試成功！")
    return jsonify({"success": ok})


@app.route("/api/daily-update", methods=["POST", "GET"])
def api_daily_update():
    """Manually trigger the daily LINE update. GET allowed for external cron."""
    global _daily_sent_date
    msg = build_daily_update()
    ok = send_line_push(msg)
    if ok:
        _daily_sent_date = datetime.now(TZ_TW).date()
    return jsonify({"success": ok, "message": msg})


# ── Startup ──

def migrate_db():
    """Add columns that may be missing from older database schemas."""
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()

        if "videos" in tables:
            cols = [c["name"] for c in inspector.get_columns("videos")]
            if "view_count" not in cols:
                db.session.execute(text("ALTER TABLE videos ADD COLUMN view_count INTEGER DEFAULT 0"))
            if "archived" not in cols:
                db.session.execute(text("ALTER TABLE videos ADD COLUMN archived INTEGER DEFAULT 0"))
            if "platform_views" not in cols:
                db.session.execute(text("ALTER TABLE videos ADD COLUMN platform_views TEXT DEFAULT '[]'"))
            db.session.commit()

        if "clients" in tables:
            cols = [c["name"] for c in inspector.get_columns("clients")]
            if "archived" not in cols:
                db.session.execute(text("ALTER TABLE clients ADD COLUMN archived INTEGER DEFAULT 0"))
            if "archive_reason" not in cols:
                db.session.execute(text("ALTER TABLE clients ADD COLUMN archive_reason VARCHAR(20) DEFAULT ''"))
            db.session.commit()

        if "editors" in tables:
            cols = [c["name"] for c in inspector.get_columns("editors")]
            if "archived" not in cols:
                db.session.execute(text("ALTER TABLE editors ADD COLUMN archived INTEGER DEFAULT 0"))
            db.session.commit()

        if "client_profiles" in tables:
            cols = [c["name"] for c in inspector.get_columns("client_profiles")]
            social_cols = ["social_ig", "social_threads", "social_tiktok", "social_fb", "social_line_voom", "social_youtube"]
            for col in social_cols:
                if col not in cols:
                    db.session.execute(text(f"ALTER TABLE client_profiles ADD COLUMN {col} TEXT DEFAULT ''"))
            if "budget_per_video" not in cols:
                db.session.execute(text("ALTER TABLE client_profiles ADD COLUMN budget_per_video INTEGER DEFAULT 0"))
            if "account_manager" not in cols:
                db.session.execute(text("ALTER TABLE client_profiles ADD COLUMN account_manager VARCHAR(100) DEFAULT ''"))
            if "pm" not in cols:
                db.session.execute(text("ALTER TABLE client_profiles ADD COLUMN pm VARCHAR(100) DEFAULT ''"))
            db.session.commit()

        if "staff" in tables:
            staff_count = db.session.execute(text("SELECT COUNT(*) FROM staff")).scalar()
            if staff_count == 0:
                for nm in DEFAULT_STAFF_NAMES:
                    db.session.add(Staff(name=nm))
                db.session.commit()

        if "didi_software" in tables:
            cols = [c["name"] for c in inspector.get_columns("didi_software")]
            if "status" not in cols:
                db.session.execute(text("ALTER TABLE didi_software ADD COLUMN status VARCHAR(20) DEFAULT '訂閱中'"))
            if "start_date" not in cols:
                db.session.execute(text("ALTER TABLE didi_software ADD COLUMN start_date VARCHAR(10) DEFAULT ''"))
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[migrate_db] {e}")


# Scheduler — runs in both gunicorn and standalone mode
_scheduler = BackgroundScheduler(timezone="Asia/Taipei")
_check_hour = int(os.getenv("CHECK_HOUR", "9"))
_check_minute = int(os.getenv("CHECK_MINUTE", "0"))
_scheduler.add_job(scheduled_overdue_check, "cron", hour=_check_hour, minute=_check_minute)
# LINE 每日戰情：08:00, 12:00, 20:00 發送
_scheduler.add_job(scheduled_daily_update, "cron", hour="8,12,20", minute=0)
# 每天 16:00 傳送「今天要上傳的影片」
_scheduler.add_job(scheduled_today_upload, "cron", hour=16, minute=0)


with app.app_context():
    try:
        db.create_all()
        migrate_db()
        seed_defaults()
        print("[Startup] Database initialized successfully.")
    except Exception as e:
        print(f"[Startup ERROR] {e}")


_scheduler.start()
print("[Scheduler] Overdue check at {:02d}:{:02d}, daily LINE at 08:00, 12:00, 20:00, today's upload at 16:00".format(_check_hour, _check_minute))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"Dashboard running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
