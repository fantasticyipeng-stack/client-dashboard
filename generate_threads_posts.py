import textwrap
import urllib.parse
import json
import random
import os
import smtplib
from datetime import datetime
from typing import Optional, List

import feedparser
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.header import Header

# === 🎯 搜尋設定（廣域生產力題庫） ===
BIG_TECH_TOPICS = [
    "Apple iOS useful new feature",
    "Google Workspace productivity update",
    "Microsoft Excel AI feature",
    "ChatGPT everyday use case"
]

STARTUP_TOPICS = [
    "productivity app startup launch",
    "innovative tech gadget life",
    "AI automation tool startup"
]

LIFE_HACK_TOPICS = [
    "digital productivity hacks and tips",
    "daily life automation workflows",
    "software hidden features productivity",
    "smart tech tips to save time",
    "best digital tools for efficiency"
]

SEARCH_TOPICS = BIG_TECH_TOPICS + STARTUP_TOPICS + LIFE_HACK_TOPICS  
MAX_ARTICLES = 3
OUTPUT_JSON_PATH = "threads_posts.json"

# === ✉️ Email 設定 ===
# ⚠️ 重要：SMTP_PASS 請填入 Google 帳號設定中的「16 位數應用程式密碼」
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "fantasticyipeng@gmail.com")
SMTP_PASS = os.getenv("SMTP_PASS", "issb vyxz prvb uybx")  # 👈 在這裡填入你的 16 位數密碼
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.getenv("EMAIL_TO", "fantasticyipeng@gmail.com")  
EMAIL_SUBJECT_PREFIX = "今日 AI／科技 Threads 靈感整理"

# === 🧠 Gemini 設定 ===
GEMINI_MODEL_NAME = "gemini-3-flash-preview"
MY_GEMINI_KEY = "AIzaSyCxNSP0hE1kz_auZZJMnzGapfaxceA3uqE" 

# 🕵️‍♂️ 真人瀏覽器偽裝面具
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

def fetch_article_html(url: str, timeout: int = 10) -> Optional[str]:
    try:
        resp = requests.get(url, timeout=timeout, headers=HEADERS)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None

def extract_main_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup(["script", "style", "nav", "footer", "header"]):
        script.extract()
    return soup.get_text(separator='\n', strip=True)

def build_gemini_prompt(title: str, article_text: str) -> str:
    instructions = f"""
你現在的人設是一個超懂 AI、喜歡研究最新科技如何改善生活、熱心分享的台灣「科技底迪」。
你的任務是把這篇國外的生硬科技新聞，轉化成一篇 Threads 上的優質科普分享文。

請嚴格遵守以下發文規則：

1. 語氣與用語要求：
   - 親切、熱情、單向分享新知和新發現。語言要極度白話，連小學生都能一聽就懂。
   - 稱呼讀者時，一律使用「你」這個單數對象，禁止用「大家」「各位」「朋友們」等集合稱呼。
   - 描述人類或使用者時，優先使用「我們」來強調一起面對、一起受影響的感覺。
   - 🚫【禁止模糊詞彙】：禁止使用「應該」「或許」「可能」「也許」「大概」「有點」這類保留態度的詞，請改用肯定、確信且有立場的說法。
   - 🇹🇼 【嚴格執行台灣用語】：使用道地的台灣習慣用語（如：軟體、影片、螢幕、預設），絕對避免大陸用語或中式英文。
   - 🚫【絕對禁止濫用引號】：不要用「」引號來強調字詞！專有名詞直接用一般文字加英文呈現（如：代理型人工智慧 Agentic AI）。

2. 內容架構（痛點前置法）：
   - 第一段（痛點共鳴開場）：用肯定且有立場的語氣直接指出一個「現在就正在發生且很關鍵」的痛點，引發共鳴。
   - 第二段（神級比喻與解方）：帶出新科技如何解決痛點。必須用日常生活中（如：樂高積木、萬用管家）來比喻技術原理。
   - 第三段（生活改善）：說明這個技術未來能怎樣讓我們的日子過得更好、更方便。
   - 結尾（俐落收尾）：用一句充滿期待或溫暖的短句自然結束（如：「未來的世界真的越來越酷了！」）。

3. 其他禁令：
   - 🚫 禁止在結尾拋出互動問題。
   - 🚫 禁止使用條列式（* 或 -），請用自然分段。
   - 🚫 禁止加入 Hashtags。
   - 🚫 禁止在開頭打招呼（如：嘿！嗨！）。

英文文章標題：
{title}

英文文章內容：
{article_text}
"""
    return textwrap.dedent(instructions).strip()

def rewrite_with_gemini(title: str, article_text: str) -> str:
    prompt = build_gemini_prompt(title, article_text)
    
    # 呼叫 Gemini 3 Flash Preview API
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_NAME}:generateContent?key={MY_GEMINI_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    # ✅ 修正後的 payload 格式
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ]
    }
    
    try:
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data['candidates'][0]['content']['parts'][0]['text'].strip()
    except Exception as e:
        print(f"[ERROR] Gemini API 發生錯誤: {e}")
        return ""

def send_email_with_posts(json_path: str) -> None:
    if not SMTP_PASS:
        print("[WARN] SMTP_PASS 未填寫，跳過寄信。")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        posts = json.load(f)

    if not posts: return

    lines = [f"📅 靈感日期：{datetime.now().strftime('%Y-%m-%d')}\n"]
    for idx, p in enumerate(posts, start=1):
        lines.append(f"【第 {idx} 篇】{p['original_title']}")
        lines.append(f"🔗 {p['url']}\n")
        lines.append(p["threads_content"])
        lines.append("\n" + "="*30 + "\n")

    body = "\n".join(lines)
    msg = MIMEText(body, "plain", "utf-8")
    date_str = datetime.now().strftime("%m/%d")
    msg["Subject"] = Header(f"{EMAIL_SUBJECT_PREFIX} ({date_str})", "utf-8")
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        print(f"[DONE] ✉️ 已寄出整理內容至：{EMAIL_TO}")
    except Exception as e:
        print(f"[ERROR] 寄信失敗：{e}")

def generate_threads_from_google_news():
    if not MY_GEMINI_KEY or "AIza" not in MY_GEMINI_KEY:
        print("[ERROR] 請填寫正確的 API Key！")
        return
        
    chosen_topic = random.choice(SEARCH_TOPICS)
    time_limited_query = f"{chosen_topic} when:7d"
    print(f"[INFO] 🎲 抽中主題：'{chosen_topic}'")
    
    encoded_query = urllib.parse.quote(time_limited_query)
    google_news_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
    
    try:
        rss_resp = requests.get(google_news_url, headers=HEADERS, timeout=10)
        feed = feedparser.parse(rss_resp.text)
    except Exception as e:
        print(f"[ERROR] RSS 錯誤: {e}"); return

    posts_data = []
    for idx, entry in enumerate(feed.entries[:MAX_ARTICLES], start=1):
        print(f"[INFO] 處理第 {idx} 篇：{entry.title}")
        html = fetch_article_html(entry.link)
        article_text = extract_main_text(html) if html else entry.summary
        
        threads_post = rewrite_with_gemini(entry.title, article_text)
        if threads_post:
            posts_data.append({
                "original_title": entry.title,
                "url": entry.link,
                "threads_content": threads_post
            })

    if posts_data:
        with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(posts_data, f, ensure_ascii=False, indent=4)
        print(f"[INFO] 已更新 {OUTPUT_JSON_PATH}")
        send_email_with_posts(OUTPUT_JSON_PATH)

if __name__ == "__main__":
    generate_threads_from_google_news()