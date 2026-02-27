import os
import uuid
import json as _json
import hashlib
import hmac
import base64
import traceback
from datetime import datetime, date, timezone, timedelta

import requests as http_requests
from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

# ── Config ──

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_TARGET_ID = os.getenv("LINE_TARGET_ID", "")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ── App ──

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
CORS(app)
db = SQLAlchemy(app)

# ── LINE Bot (direct REST API, no SDK) ──

LINE_API_REPLY = "https://api.line.me/v2/bot/message/reply"
LINE_API_PUSH = "https://api.line.me/v2/bot/message/push"

TZ_TW = timezone(timedelta(hours=8))

STATUSES = ['待分配', '剪輯中', '初稿修改中', '客戶確認中', '已完成', '已上傳雲端', '已上傳影片']
DONE_STATUSES = ['初稿修改中', '客戶確認中', '已完成', '已上傳雲端', '已上傳影片']

DEFAULT_CLIENTS = ['大可為', 'JGB', '婕絲', '台中市政府數位發展局', '吃喝玩樂', '多德仕', '和居', '恩友友', '萬華街區', '橙果創意', '底迪']
DEFAULT_EDITORS = ['李宥儀', '邱麟晴', '翁薏惠', '陳思妤', '高偉翔', '楊淳惠', '楊斯涵', '王彥鈞', '賴宇柔', '李依珊', '顏佳祐', '賴彥辰', '黃睿妤', '劉恩伶', '鄭樺薇', '胡禎妮', '郭佳柔', '王晨羽']


# ── Models ──

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
    notes = db.Column(db.Text, default="")
    remarks = db.Column(db.Text, default="")
    created_at = db.Column(db.String(30), nullable=False)
    updated_at = db.Column(db.String(30), nullable=False)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)

    def to_dict(self):
        return {"id": self.id, "name": self.name}


class Editor(db.Model):
    __tablename__ = "editors"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)

    def to_dict(self):
        return {"id": self.id, "name": self.name}


class ClientProfile(db.Model):
    __tablename__ = "client_profiles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    current_followers = db.Column(db.Integer, default=0)
    target_followers = db.Column(db.Integer, default=0)
    reminders = db.Column(db.Text, default="")
    social_ig = db.Column(db.Text, default="")
    social_threads = db.Column(db.Text, default="")
    social_tiktok = db.Column(db.Text, default="")
    social_fb = db.Column(db.Text, default="")
    social_line_voom = db.Column(db.Text, default="")
    social_youtube = db.Column(db.Text, default="")

    def to_dict(self):
        return {
            "current_followers": self.current_followers,
            "target_followers": self.target_followers,
            "reminders": self.reminders,
            "social_ig": self.social_ig or "",
            "social_threads": self.social_threads or "",
            "social_tiktok": self.social_tiktok or "",
            "social_fb": self.social_fb or "",
            "social_line_voom": self.social_line_voom or "",
            "social_youtube": self.social_youtube or "",
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

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


def seed_defaults():
    for name in DEFAULT_CLIENTS:
        if not Client.query.filter_by(name=name).first():
            db.session.add(Client(name=name))
    for name in DEFAULT_EDITORS:
        if not Editor.query.filter_by(name=name).first():
            db.session.add(Editor(name=name))
    db.session.commit()


# ── API: Serve frontend ──

@app.route("/")
def index():
    return send_file("dashboard.html")


@app.route("/logo.png")
def logo():
    return send_file("logo.png", mimetype="image/png")


# ── API: Videos ──

@app.route("/api/videos", methods=["GET"])
def list_videos():
    rows = Video.query.order_by(Video.draft_date.asc()).all()
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
                 "editor", "status", "material_link", "script_link", "view_count", "notes", "remarks"]:
        if key in d:
            setattr(v, key, d[key])
    v.updated_at = datetime.now().isoformat()
    db.session.commit()
    return jsonify({"message": "updated"})


@app.route("/api/videos/<vid>", methods=["DELETE"])
def delete_video(vid):
    v = Video.query.get(vid)
    if v:
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
    db.session.add(Client(name=name))
    db.session.commit()
    return jsonify({"message": "added"}), 201


@app.route("/api/clients/<int:cid>", methods=["DELETE"])
def delete_client(cid):
    c = Client.query.get(cid)
    if c:
        db.session.delete(c)
        db.session.commit()
    return jsonify({"message": "deleted"})


# ── API: Editors ──

@app.route("/api/editors", methods=["GET"])
def list_editors():
    rows = Editor.query.order_by(Editor.id).all()
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/editors", methods=["POST"])
def add_editor():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"error": "名稱不可為空"}), 400
    if Editor.query.filter_by(name=name).first():
        return jsonify({"error": "此人員已存在"}), 409
    db.session.add(Editor(name=name))
    db.session.commit()
    return jsonify({"message": "added"}), 201


@app.route("/api/editors/<int:eid>", methods=["DELETE"])
def delete_editor(eid):
    e = Editor.query.get(eid)
    if e:
        db.session.delete(e)
        db.session.commit()
    return jsonify({"message": "deleted"})


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
    row.reminders = d.get("reminders", "")
    row.social_ig = d.get("social_ig", "")
    row.social_threads = d.get("social_threads", "")
    row.social_tiktok = d.get("social_tiktok", "")
    row.social_fb = d.get("social_fb", "")
    row.social_line_voom = d.get("social_line_voom", "")
    row.social_youtube = d.get("social_youtube", "")
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
        ))
    db.session.commit()
    return jsonify({"message": "saved"})


# ── Overdue logic ──

def get_overdue_items():
    today = datetime.now(TZ_TW).date().isoformat()
    return Video.query.filter(
        Video.status.notin_(DONE_STATUSES),
        Video.draft_date < today
    ).order_by(Video.draft_date.asc()).all()


def build_overdue_message(items):
    if not items:
        return None
    today = datetime.now(TZ_TW).date()
    msg = f"⚠️ 短影片初稿逾期提醒（{len(items)} 筆）\n{'─' * 20}\n\n"
    for i, d in enumerate(items, 1):
        draft = datetime.strptime(d.draft_date, "%Y-%m-%d").date()
        overdue_days = (today - draft).days
        msg += f"{i}. {d.client_name}｜{d.video_id}\n"
        msg += f"   主題：{d.topic}\n"
        msg += f"   剪輯：{d.editor}\n"
        msg += f"   初稿日：{d.draft_date}（已逾期 {overdue_days} 天）\n"
        msg += f"   狀態：{d.status}\n\n"
    msg += "請盡速處理！\n回覆「指令」查看可用操作。"
    return msg


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
    """Calculate total revenue matching dashboard logic."""
    client_rev = 0
    uploaded = Video.query.filter_by(status='已上傳影片').all()
    for v in uploaded:
        price = CLIENT_PRICES.get(v.client_name, 0)
        if price == -1:
            if (v.view_count or 0) >= 5500:
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
        Video.status.notin_(['已完成', '已上傳雲端', '已上傳影片']),
        Video.upload_date <= deadline,
        Video.upload_date >= today_str,
    ).order_by(Video.upload_date.asc()).all()


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


def scheduled_daily_update():
    with app.app_context():
        msg = build_daily_update()
        ok = send_line_push(msg)
        print(f"[Scheduler] Daily update sent, success={ok}")


def scheduled_overdue_check():
    with app.app_context():
        items = get_overdue_items()
        msg = build_overdue_message(items)
        if msg:
            ok = send_line_push(msg)
            print(f"[Scheduler] Sent overdue alert: {len(items)} items, success={ok}")
        else:
            print("[Scheduler] No overdue items.")


@app.route("/api/check-overdue", methods=["POST"])
def api_check_overdue():
    items = get_overdue_items()
    msg = build_overdue_message(items)
    if not msg:
        return jsonify({"message": "沒有逾期任務", "count": 0, "sent": False})
    ok = send_line_push(msg)
    return jsonify({
        "message": f"已發送 {len(items)} 筆逾期提醒" if ok else "LINE 未設定或傳送失敗",
        "count": len(items),
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
        items = get_overdue_items()
        if not items:
            reply_line(reply_token, "目前沒有逾期任務 👍")
        else:
            reply_line(reply_token, build_overdue_message(items))
    else:
        msg = build_daily_update()
        reply_line(reply_token, msg)

@app.route("/api/line-test", methods=["POST"])
def api_line_test():
    """Send a test message to verify LINE API connectivity."""
    ok = send_line_push("LINE 機器人連線測試成功！")
    return jsonify({"success": ok})


@app.route("/api/daily-update", methods=["POST"])
def api_daily_update():
    """Manually trigger the daily LINE update."""
    msg = build_daily_update()
    ok = send_line_push(msg)
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
                db.session.commit()

        if "client_profiles" in tables:
            cols = [c["name"] for c in inspector.get_columns("client_profiles")]
            social_cols = ["social_ig", "social_threads", "social_tiktok", "social_fb", "social_line_voom", "social_youtube"]
            for col in social_cols:
                if col not in cols:
                    db.session.execute(text(f"ALTER TABLE client_profiles ADD COLUMN {col} TEXT DEFAULT ''"))
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[migrate_db] {e}")


with app.app_context():
    try:
        db.create_all()
        migrate_db()
        seed_defaults()
        print("[Startup] Database initialized successfully.")
    except Exception as e:
        print(f"[Startup ERROR] {e}")


# Scheduler — runs in both gunicorn and standalone mode
_scheduler = BackgroundScheduler(timezone="Asia/Taipei")
_check_hour = int(os.getenv("CHECK_HOUR", "9"))
_check_minute = int(os.getenv("CHECK_MINUTE", "0"))
_scheduler.add_job(scheduled_overdue_check, "cron", hour=_check_hour, minute=_check_minute)
_scheduler.add_job(scheduled_daily_update, "cron", hour=_check_hour, minute=_check_minute + 1)
_scheduler.start()
print(f"[Scheduler] Daily overdue check at {_check_hour:02d}:{_check_minute:02d}, daily update at {_check_hour:02d}:{_check_minute + 1:02d}")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"Dashboard running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
