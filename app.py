"""Giftie - chatbot tư vấn quà tặng trên Facebook Messenger, dùng Gemini API (gói miễn phí)."""
import csv
import hashlib
import hmac
import logging
import os
import threading
import time
from collections import OrderedDict, defaultdict

import requests
from dotenv import load_dotenv
from flask import Flask, abort, request
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

load_dotenv()

VERIFY_TOKEN = os.environ["VERIFY_TOKEN"]            # chuỗi bạn tự đặt, dùng khi cấu hình webhook
PAGE_ACCESS_TOKEN = os.environ["PAGE_ACCESS_TOKEN"]  # token của Fanpage
APP_SECRET = os.environ["APP_SECRET"]                # App Secret trong Facebook App
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")  # có trong gói miễn phí
# Khi mô hình chính quá tải (503) hoặc hết hạn mức (429), tự chuyển sang các mô hình này (cũng miễn phí)
FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash").split(",")
    if m.strip()
]
RETRYABLE_CODES = (429, 500, 503, 504)
GRAPH_URL = "https://graph.facebook.com/v21.0/me/messages"

MAX_HISTORY = 20                 # số tin nhắn gần nhất giữ lại cho mỗi khách
HANDOFF_PAUSE_SECONDS = 12 * 3600  # bot im lặng bao lâu sau khi chuyển nhân viên
HANDOFF_MARKER = "[CHUYEN_NHAN_VIEN]"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("giftie")
app = Flask(__name__)
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_system_prompt() -> str:
    with open(os.path.join(BASE_DIR, "system_prompt.txt"), encoding="utf-8") as f:
        prompt = f.read()
    return prompt.replace("{{CATALOG}}", build_catalog_text())


def is_available(status: str) -> bool:
    return status.strip().lower() in ("còn", "còn hàng", "con", "con hang", "active")


def money(value: str) -> str:
    """250000 -> '250.000đ'; chuỗi khoảng giá như '200.000–600.000' giữ nguyên."""
    value = (value or "").strip()
    if value.isdigit():
        return f"{int(value):,}".replace(",", ".") + "đ"
    return value + "đ" if value else "liên hệ"


def read_csv(name: str) -> list:
    with open(os.path.join(BASE_DIR, name), encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_catalog_text() -> str:
    """Gộp sản phẩm (catalog.csv) và biến thể (variants.csv) thành văn bản cho AI."""
    variants = defaultdict(list)
    for v in read_csv("variants.csv"):
        stock = (v.get("Stock") or "0").strip()
        if is_available(v.get("Status", "")) and stock.isdigit() and int(stock) > 0:
            variants[v["Product_ID"].strip()].append(v)
    has_any_variant = {v["Product_ID"].strip() for v in read_csv("variants.csv")}

    lines = []
    for p in read_csv("catalog.csv"):
        pid = p["Product_ID"].strip()
        if not is_available(p.get("Status", "")):
            continue
        if pid in has_any_variant and not variants[pid]:
            continue  # mọi biến thể đều hết hàng
        lines.append(
            f"- {pid} | {p['Product_Name'].strip()} | {p['Category']} | Giá: {money(p['Price_VND'])} | "
            f"Người nhận: {p['Recipient']} | Dịp: {p['Occasion']} | Sở thích: {p['Interest']} | "
            f"Phong cách: {p['Style']} | {p['Description'].strip()}"
        )
        for v in variants[pid]:
            attrs = [
                f"{label}: {v[key].strip()}"
                for label, key in (("Loại", "Type"), ("Size", "Size"), ("Màu", "Color"), ("Dung tích", "Capacity"))
                if v.get(key, "").strip() not in ("", "–", "-")
            ]
            lines.append(f"    + Biến thể {v['Variant_ID']}: {', '.join(attrs)} | Giá: {money(v['Price_VND'])}")
    return "\n".join(lines)


SYSTEM_PROMPT = load_system_prompt()

# Bộ nhớ trong RAM (mất khi khởi động lại; muốn bền vững hãy dùng Redis/Google Sheets)
history = defaultdict(list)
paused_until = {}
seen_mids = OrderedDict()
lock = threading.Lock()


def verify_signature(raw_body: bytes, header: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def call_graph(payload: dict) -> None:
    r = requests.post(
        GRAPH_URL, params={"access_token": PAGE_ACCESS_TOKEN}, json=payload, timeout=15
    )
    if not r.ok:
        log.error("Graph API lỗi %s: %s", r.status_code, r.text)


def send_action(psid: str, action: str) -> None:
    call_graph({"recipient": {"id": psid}, "sender_action": action})


def send_text(psid: str, text: str) -> None:
    # Messenger giới hạn 2000 ký tự mỗi tin nhắn
    chunks = [text[i : i + 1900] for i in range(0, len(text), 1900)] or [""]
    for chunk in chunks:
        call_graph(
            {
                "recipient": {"id": psid},
                "messaging_type": "RESPONSE",
                "message": {"text": chunk},
            }
        )


def ask_ai(psid: str, user_text: str) -> str:
    with lock:
        history[psid].append({"role": "user", "content": user_text})
        history[psid] = history[psid][-MAX_HISTORY:]
        # Lịch sử phải bắt đầu bằng tin nhắn của user
        while history[psid] and history[psid][0]["role"] != "user":
            history[psid].pop(0)
        messages = list(history[psid])

    # Gemini dùng vai trò "user" và "model" (không phải "assistant")
    contents = [
        types.Content(
            role="user" if m["role"] == "user" else "model",
            parts=[types.Part(text=m["content"])],
        )
        for m in messages
    ]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        max_output_tokens=2048,  # chừa chỗ cho phần "suy nghĩ" của mô hình
    )
    try:
        resp = generate_with_fallback(contents, config)
    except Exception:
        # Bỏ tin nhắn vừa thêm để lịch sử không bị lệch lượt user/model
        with lock:
            if history[psid] and history[psid][-1]["role"] == "user":
                history[psid].pop()
        raise
    reply = (resp.text or "").strip()

    with lock:
        history[psid].append({"role": "assistant", "content": reply})
    return reply


def generate_with_fallback(contents, config):
    """Gọi Gemini; nếu quá tải/hết hạn mức thì thử lại, rồi chuyển sang mô hình dự phòng."""
    last_error = None
    for model in [MODEL] + FALLBACK_MODELS:
        for attempt in range(2):
            try:
                return client.models.generate_content(model=model, contents=contents, config=config)
            except genai_errors.APIError as e:
                last_error = e
                log.warning("Gemini lỗi %s với %s (lần %d): %s", e.code, model, attempt + 1, e.message)
                if e.code in RETRYABLE_CODES and attempt == 0:
                    time.sleep(2)  # thử lại 1 lần với cùng mô hình
                    continue
                break  # sang mô hình tiếp theo
    raise last_error


def handle_message(psid: str, text: str) -> None:
    if paused_until.get(psid, 0) > time.time():
        return  # đang chờ nhân viên, bot không trả lời
    try:
        send_action(psid, "typing_on")
        reply = ask_ai(psid, text)
        if HANDOFF_MARKER in reply:
            reply = reply.replace(HANDOFF_MARKER, "").strip()
            paused_until[psid] = time.time() + HANDOFF_PAUSE_SECONDS
            log.info("CHUYỂN NHÂN VIÊN: psid=%s, tin nhắn cuối: %s", psid, text)
        send_text(psid, reply or "Giftie xin lỗi, bạn nhắn lại giúp mình nhé!")
    except Exception:
        log.exception("Lỗi khi xử lý tin nhắn")
        send_text(psid, "Giftie đang gặp chút trục trặc, bạn thử nhắn lại sau ít phút nhé 🙏")


@app.get("/")
def health():
    return "Giftie bot is running"


@app.get("/webhook")
def webhook_verify():
    if (
        request.args.get("hub.mode") == "subscribe"
        and request.args.get("hub.verify_token") == VERIFY_TOKEN
    ):
        return request.args.get("hub.challenge", ""), 200
    abort(403)


@app.post("/webhook")
def webhook_receive():
    if not verify_signature(request.get_data(), request.headers.get("X-Hub-Signature-256")):
        abort(403)
    data = request.get_json(silent=True) or {}
    if data.get("object") != "page":
        return "ignored", 200

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            msg = event.get("message")
            if not msg or msg.get("is_echo"):
                continue
            mid = msg.get("mid")
            with lock:
                if mid in seen_mids:
                    continue
                seen_mids[mid] = True
                while len(seen_mids) > 1000:
                    seen_mids.popitem(last=False)
            psid = event["sender"]["id"]
            text = msg.get("text")
            if not text:
                send_text(psid, "Giftie hiện chỉ đọc được tin nhắn chữ thôi, bạn nhắn giúp mình nhé 😊")
                continue
            # Trả lời 200 ngay cho Facebook, xử lý AI ở luồng nền
            threading.Thread(target=handle_message, args=(psid, text), daemon=True).start()
    return "EVENT_RECEIVED", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))