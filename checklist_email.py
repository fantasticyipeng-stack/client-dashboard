"""Daily checklist email builder and sender."""
import os
import smtplib
from datetime import datetime, timezone, timedelta
from email.header import Header
from email.mime.text import MIMEText

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "fantasticyipeng@gmail.com")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
CHECKLIST_EMAIL_TO = os.getenv("CHECKLIST_EMAIL_TO", "fantasticyipeng@gmail.com")

CATEGORY_LABELS = {
    "work": "📋 工作",
    "health": "💪 健康",
    "relationships": "❤️ 人際關係",
}

CATEGORY_ORDER = ["work", "health", "relationships"]

TZ_TW = timezone(timedelta(hours=8))


def send_checklist_email(subject: str, body: str, to: str | None = None) -> bool:
    """Send plain-text checklist email via Gmail SMTP."""
    if not SMTP_PASS:
        print("[Checklist email] SMTP_PASS not configured, skip sending.")
        return False

    recipient = to or CHECKLIST_EMAIL_TO
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = EMAIL_FROM
    msg["To"] = recipient

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, [recipient], msg.as_string())
        print(f"[Checklist email] Sent to {recipient}: {subject}")
        return True
    except Exception as e:
        print(f"[Checklist email] Send failed: {e}")
        return False


def _group_items_by_category(items):
    grouped = {cat: [] for cat in CATEGORY_ORDER}
    for item in items:
        cat = item.get("category") or "work"
        if cat in grouped:
            grouped[cat].append(item)
    return grouped


def build_morning_email(items, dashboard_url: str) -> tuple[str, str]:
    """Build morning checklist email subject and body."""
    today = datetime.now(TZ_TW)
    date_str = today.strftime("%Y/%m/%d")
    subject = f"☀️ 今日清單 — {date_str}"

    lines = ["早安！以下是今天的待辦清單：", ""]
    grouped = _group_items_by_category(items)

    for cat in CATEGORY_ORDER:
        cat_items = grouped.get(cat) or []
        if not cat_items:
            continue
        lines.append(CATEGORY_LABELS.get(cat, cat))
        for item in cat_items:
            title = item.get("title", "")
            suffix = "（每週）" if item.get("frequency") == "weekly" else ""
            lines.append(f"  ☐ {title}{suffix}")
        lines.append("")

    lines.append(f"👉 在 dashboard 勾選完成：{dashboard_url}")
    return subject, "\n".join(lines)


def build_evening_email(items, completed_ids: set, dashboard_url: str) -> tuple[str, str]:
    """Build evening review email with progress summary."""
    today = datetime.now(TZ_TW)
    date_str = today.strftime("%Y/%m/%d")
    total = len(items)
    done_count = sum(1 for item in items if item.get("id") in completed_ids)
    pct = round(done_count / total * 100) if total else 0
    subject = f"🌙 今日回顧 — {done_count}/{total} 完成"

    lines = [
        f"今日進度：{done_count} / {total}（{pct}%）",
        f"日期：{date_str}",
        "",
    ]

    done_items = [item for item in items if item.get("id") in completed_ids]
    pending_items = [item for item in items if item.get("id") not in completed_ids]

    if done_items:
        lines.append("✅ 已完成")
        for item in done_items:
            lines.append(f"  · {item.get('title', '')}")
        lines.append("")

    if pending_items:
        lines.append("⬜ 尚未完成")
        for item in pending_items:
            suffix = "（每週）" if item.get("frequency") == "weekly" else ""
            lines.append(f"  · {item.get('title', '')}{suffix}")
        lines.append("")

    if not done_items and not pending_items:
        lines.append("今日沒有待辦項目。")
        lines.append("")

    lines.append(f"👉 補勾選：{dashboard_url}")
    return subject, "\n".join(lines)
