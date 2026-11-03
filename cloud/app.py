# -*- coding: utf-8 -*-
"""
Smart Study Desk Cloud (Vercel + Firebase RTDB) — cổng điều khiển từ xa cho nhiều phụ huynh / nhiều máy.

Luồng dữ liệu (máy học sinh luôn GỌI RA, không mở cổng vào):
  FocusDeskLauncher (agent)  --heartbeat 10s-->  /api/agent/heartbeat  <--lệnh--  Phụ huynh (web / Telegram)
  Telegram  --webhook-->  /api/telegram/webhook   (1 bot duy nhất, token chỉ nằm trong env Vercel)

Biến môi trường (Vercel -> Settings -> Environment Variables):
  SECRET_KEY                 chuỗi ngẫu nhiên dài (ký cookie đăng nhập)
  FIREBASE_DB_URL            URL Realtime Database
  FIREBASE_SERVICE_ACCOUNT   JSON service account (hoặc base64)
  TELEGRAM_BOT_TOKEN         token BotFather
  TELEGRAM_WEBHOOK_SECRET    chuỗi ngẫu nhiên (xác thực webhook + khóa trang /api/telegram/setup)
"""

import base64
import hashlib
import hmac
import html
import re
import os
import secrets
import time
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import db, fb_key
import study

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "dev-only-" + hashlib.sha256(b"focusdesk").hexdigest()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")),
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
# Đăng nhập Google (Google Identity Services): chỉ cần Client ID, không cần client secret.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
# Mật khẩu email chỉ bật khi ALLOW_PASSWORD_LOGIN=1 (mặc định TẮT nếu đã có Google) -> giảm bề mặt tấn công.
ALLOW_PASSWORD_LOGIN = os.environ.get("ALLOW_PASSWORD_LOGIN", "0" if GOOGLE_CLIENT_ID else "1") == "1"
# Giới hạn ai được đăng nhập (tùy chọn): "a@gmail.com,b@gmail.com" hoặc tên miền "@truong.edu.vn"
ALLOWED_EMAILS = [e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()]
TG_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
# Tần suất gọi server LINH HOẠT (tiết kiệm lượt gọi gói Vercel Hobby):
#  - Bình thường: máy báo 30s/lần.
#  - Có sự kiện đặc biệt (cảnh báo mới) -> máy báo NGAY, không chờ chu kỳ.
#  - Phụ huynh đang mở dashboard / vừa gửi lệnh -> server bảo máy chuyển chế độ nhanh 5s trong 2 phút.
# Máy báo về: 15 s bình thường, 2 s khi phụ huynh đang mở web / vừa có lệnh (cảnh báo & sự kiện vẫn gửi NGAY, không chờ).
# Đã tính cho gói miễn phí (Vercel Hobby + Firebase Spark) — xem bảng ngân sách trong HUONG_DAN_DEPLOY.md.
HEARTBEAT_S = int(os.environ.get("AGENT_HEARTBEAT_S", "15"))
FAST_HEARTBEAT_S = int(os.environ.get("AGENT_FAST_HEARTBEAT_S", "2"))
FAST_WINDOW_S = int(os.environ.get("AGENT_FAST_WINDOW_S", "120"))
ONLINE_GRACE_S = HEARTBEAT_S * 2 + 15
PAIR_TTL_S = 15 * 60
SNAPSHOT_TTL_S = 5 * 60
ALERT_COOLDOWN_S = 60
COMMAND_TYPES = {
    "lock": "🔒 Khóa màn hình",
    "reset_ai": "♻️ Đặt lại giờ dùng AI",
    "reset_stats": "🧹 Xóa cảnh báo & bộ đếm trên máy",
    "ai_on": "📷 Bật Camera AI",
    "ai_off": "⏸️ Tắt Camera AI",
    "recalibrate": "🔄 Hiệu chỉnh lại tư thế",
    "snapshot": "📸 Chụp 1 ảnh camera (gửi qua Telegram)",
    "live_on": "📺 Bật xem trực tiếp",
    "live_off": "⏹️ Tắt xem trực tiếp",
    "box_unlock_30": "🔓 Mở hộp điện thoại 30 phút",
    "box_unlock": "🔓 Mở hộp điện thoại (tới khi khóa lại)",
    "box_unlock_min": "🔓 Mở hộp điện thoại theo số phút",
    "box_lock": "🔒 Khóa hộp điện thoại",
    "water_plant": "🌱 Tưới cây (mô-đun ESP32)",
    "box_reset_key": "📱 Ghép điện thoại mới với tủ ESP32",
}
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# Web Push (thông báo trên điện thoại kể cả khi đã đóng web): tạo khóa bằng cloud/tao_khoa_vapid.py
VAPID_PUBLIC = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@banhocthongminh.vercel.app").strip()
GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODEL", "gemini-flash-lite-latest,gemini-flash-latest,gemini-2.0-flash").split(",") if m.strip()]
ACTIVITY_AI = os.environ.get("ACTIVITY_AI", "1") == "1"
TZ_OFFSET_S = int(float(os.environ.get("TZ_OFFSET_H", "7")) * 3600)


# =====================================================================
# Tiện ích
# =====================================================================
def now() -> int:
    return int(time.time())


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_code(n: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))


def current_uid() -> Optional[str]:
    return session.get("uid")


def email_allowed(email: str) -> bool:
    if not ALLOWED_EMAILS:
        return True
    email = email.lower()
    return any(email == a or (a.startswith("@") and email.endswith(a)) for a in ALLOWED_EMAILS)


def start_session(uid: str, email: str, name: str = "", picture: str = "") -> None:
    session.clear()  # chống session fixation
    session.permanent = True
    session.update(uid=uid, email=email, name=name, picture=picture, csrf=secrets.token_urlsafe(16))


def find_or_create_parent(email: str, google_sub: str = "", name: str = "") -> str:
    """Tìm phụ huynh theo Google sub (ổn định) -> theo email (liên kết tài khoản cũ) -> tạo mới."""
    if google_sub:
        uid = db.get(f"google_subs/{fb_key(google_sub)}")
        if uid and db.get(f"parents/{uid}"):
            return uid
    uid = db.get(f"emails/{fb_key(email)}")
    if not uid:
        uid = "u_" + secrets.token_hex(8)
        db.set(f"parents/{uid}", {"email": email, "name": name, "created": now()})
        db.set(f"emails/{fb_key(email)}", uid)
    if google_sub:
        db.set(f"google_subs/{fb_key(google_sub)}", uid)
        db.update(f"parents/{uid}", {"google_sub": google_sub, "name": name or "", "last_login": now()})
    return uid


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    # Google Sign-In cần popup cùng nguồn gốc để trả kết quả
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
    if os.environ.get("VERCEL"):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


@app.before_request
def csrf_protect():
    """Mọi POST /api/parent/* phải kèm header X-CSRF-Token khớp phiên (chống CSRF ngoài SameSite)."""
    if request.method == "POST" and request.path.startswith("/api/parent/"):
        if not session.get("csrf") or not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session["csrf"]):
            return jsonify({"error": "Phiên hết hạn, tải lại trang."}), 403
    return None


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_uid():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Chưa đăng nhập"}), 401
            return redirect("/login")
        return fn(*a, **kw)
    return wrapper


def parent_owns(uid: str, did: str) -> bool:
    return bool(db.get(f"devices/{did}/owners/{uid}"))


def device_auth(light: bool = False) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Xác thực máy học sinh: header Authorization: Device <device_id>:<device_token>.
    light=True: chỉ đọc token_hash (~100 byte) thay vì cả hồ sơ máy (vài chục KB) — dùng cho heartbeat."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Device ") or ":" not in auth:
        return None, None
    did, token = auth[7:].split(":", 1)
    did = fb_key(did)
    if not did:
        return None, None
    if light:
        th = db.get(f"devices/{did}/token_hash")
        if not isinstance(th, str) or not hmac.compare_digest(th, sha256(token)):
            return None, None
        return did, {"token_hash": th}
    dev = db.get(f"devices/{did}")
    if not dev or not hmac.compare_digest(dev.get("token_hash", ""), sha256(token)):
        return None, None
    return did, dev


def is_online(dev: Dict[str, Any]) -> bool:
    last = int(dev.get("last_seen", 0) or 0)
    if int(dev.get("shutdown_at", 0) or 0) >= last > 0:   # máy vừa báo "đã tắt FocusDesk"
        return False
    return now() - last <= ONLINE_GRACE_S


# =====================================================================
# Telegram (1 bot cho mọi phụ huynh)
# =====================================================================
def tg_api(method: str, **payload) -> Optional[Dict[str, Any]]:
    if not TG_TOKEN:
        print("[TG-MOCK]", method, {k: v for k, v in payload.items() if k != "photo_bytes"}, flush=True)
        return None
    try:
        files = None
        if "photo_bytes" in payload:
            files = {"photo": ("snapshot.jpg", payload.pop("photo_bytes"), "image/jpeg")}
            r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", data=payload, files=files, timeout=10)
        else:
            r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", json=payload, timeout=8)
        return r.json()
    except Exception as exc:
        app.logger.warning("Telegram lỗi: %s", exc)
        return None


def tg_send(chat_id: str, text: str) -> None:
    tg_api("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)


_bot_username_cache: Dict[str, str] = {}


def bot_username() -> str:
    if "u" not in _bot_username_cache:
        info = tg_api("getMe") or {}
        _bot_username_cache["u"] = ((info.get("result") or {}).get("username")) or os.environ.get(
            "TELEGRAM_BOT_USERNAME", "")
    return _bot_username_cache["u"]


def chats_of_device(did: str) -> List[str]:
    chats: List[str] = []
    for uid in (db.get(f"devices/{did}/owners") or {}).keys():
        chats.extend((db.get(f"parents/{uid}/chats") or {}).keys())
    return sorted(set(chats))


def devices_of_parent(uid: str) -> List[Tuple[str, Dict[str, Any]]]:
    out = []
    for did in sorted((db.get(f"parents/{uid}/devices") or {}).keys()):
        dev = db.get(f"devices/{did}")
        if dev:
            out.append((did, dev))
    return out


def queue_command(did: str, ctype: str, by: str, chat_id: Optional[str] = None, minutes: int = 0, ms: int = 0,
                  code: str = "") -> str:
    mark_watching(did, force=True)
    item = {"type": ctype, "by": by, "ts": now(), "chat_id": chat_id or ""}
    if code:
        item["code"] = str(code)[:6]
    if minutes:
        item["minutes"] = int(minutes)
    if ms:
        item["ms"] = int(ms)
    return db.push(f"commands/{did}", item)


def mark_watching(did: str, force: bool = False, dev: Optional[Dict[str, Any]] = None) -> None:
    """Báo máy chuyển sang chế độ nhanh trong FAST_WINDOW_S giây (ghi DB tối đa ~1 lần/phút/máy)."""
    cur = int(((dev or {}).get("watch_until")) or 0) if dev is not None else int(db.get(f"devices/{did}/watch_until") or 0)
    if force or cur - now() < FAST_WINDOW_S - 60:
        db.set(f"devices/{did}/watch_until", now() + FAST_WINDOW_S)


# =====================================================================
# Trang web phụ huynh
# =====================================================================
BASE_CSS = """
<script>/* Áp theme trước khi vẽ trang để không bị nháy */
(function(){var t="light";try{t=localStorage.getItem("sd_theme")||t}catch(e){}document.documentElement.setAttribute("data-theme",t)})();
function toggleTheme(){var r=document.documentElement,t=r.getAttribute("data-theme")==="dark"?"light":"dark";r.setAttribute("data-theme",t);
try{localStorage.setItem("sd_theme",t)}catch(e){}var b=document.getElementById("themeBtn");if(b)b.textContent=t==="dark"?"☀️ Nền sáng":"🌙 Nền tối";}
</script>
<style>
:root,[data-theme=light]{--bg:#f4f6fb;--card:#ffffff;--line:#e2e8f0;--txt:#0f172a;--mut:#64748b;--acc:#2563eb;
 --btn:#eef2ff;--btn-txt:#1e3a8a;--btn-line:#c7d2fe;--input:#ffffff;--danger:#fee2e2;--danger-txt:#991b1b;
 --pill:#f1f5f9;--on:#dcfce7;--on-txt:#166534;--off:#e5e7eb;--off-txt:#374151;--err:#dc2626;--shadow:0 1px 3px rgba(15,23,42,.08)}
[data-theme=dark]{--bg:#0b1220;--card:#131c2e;--line:#22304a;--txt:#e2e8f0;--mut:#94a3b8;--acc:#38bdf8;
 --btn:#1e3a5f;--btn-txt:#e2e8f0;--btn-line:#22304a;--input:#0f172a;--danger:#5f1e1e;--danger-txt:#fecaca;
 --pill:#1f2937;--on:#14532d;--on-txt:#bbf7d0;--off:#3f3f46;--off-txt:#e4e4e7;--err:#f87171;--shadow:none}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--txt)}
.wrap{max-width:1100px;margin:0 auto;padding:16px}a{color:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:var(--shadow)}
input,button{font:inherit;border-radius:10px;border:1px solid var(--line);padding:10px 12px;background:var(--input);color:var(--txt)}
button{background:var(--btn);color:var(--btn-txt);border-color:var(--btn-line);cursor:pointer}button:hover{filter:brightness(.96)}
button.danger{background:var(--danger);color:var(--danger-txt)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.mut{color:var(--mut);font-size:13px}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;background:var(--pill)}
.on{background:var(--on);color:var(--on-txt)}.off{background:var(--off);color:var(--off-txt)}.big{font-size:34px;font-weight:700}
.err{color:var(--err)}.avatar{width:28px;height:28px;border-radius:50%}
table{width:100%;border-collapse:collapse;font-size:13px}td{padding:4px 0;border-bottom:1px solid var(--line)}
img.snap{width:100%;border-radius:10px;margin-top:8px}
@media (max-width:480px){.grid{grid-template-columns:1fr}.wrap{padding:12px}}
</style>"""

AUTH_HTML = BASE_CSS + """
<title>FocusDesk - Đăng nhập</title><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#2563eb"><link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png"><link rel="icon" href="/static/favicon.svg" type="image/svg+xml"><link rel="icon" href="/favicon.ico" sizes="any">
<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});</script>
<div style="text-align:center;margin-top:6vh"><img src="/static/icon-192.png" width="72" style="border-radius:18px" alt=""></div>
<div class="wrap" style="max-width:420px;margin-top:2vh">
 <div class="row" style="justify-content:flex-end"><button id="themeBtn" onclick="toggleTheme()">🌙 Nền tối</button></div>
 <div class="card"><h2>🛡️ FocusDesk</h2><p class="mut">Bàn học thông minh · Đăng nhập dành cho phụ huynh</p>
 <p id="err" class="err">{{ error }}</p>
 {% if google_client_id %}
  <script src="https://accounts.google.com/gsi/client" async defer></script>
  <div id="g_id_onload" data-client_id="{{ google_client_id }}" data-callback="onGoogle" data-auto_prompt="false"
       data-ux_mode="popup" data-context="signin" data-itp_support="true"></div>
  <div class="g_id_signin" data-type="standard" data-size="large" data-theme="outline" data-text="signin_with"
       data-shape="pill" data-logo_alignment="left" data-width="300" data-locale="vi"></div>
  <p class="mut">Đăng nhập bằng tài khoản Google — không cần tạo mật khẩu riêng. Bật xác minh 2 bước cho tài khoản Google để an toàn nhất.</p>
  <script>
  async function onGoogle(resp){
    const r = await fetch('/auth/google', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({credential: resp.credential})});
    const d = await r.json();
    if (d.ok) location.href = '/dashboard'; else document.getElementById('err').textContent = d.error || 'Đăng nhập thất bại';
  }
  </script>
 {% endif %}
 {% if allow_password %}
  <hr style="border:none;border-top:1px solid var(--line);margin:16px 0">
  <form method="post" action="/login"><div class="row" style="flex-direction:column;align-items:stretch">
   <input name="email" type="email" placeholder="Email" required>
   <input name="password" type="password" placeholder="Mật khẩu (≥ 8 ký tự)" required minlength="8">
   <div class="row"><button type="submit" name="mode" value="login">Đăng nhập</button>
   <button type="submit" name="mode" value="register">Đăng ký</button></div></div></form>
 {% endif %}
 {% if not google_client_id and not allow_password %}<p class="err">Server chưa cấu hình GOOGLE_CLIENT_ID.</p>{% endif %}
 </div></div>
<script>document.getElementById('themeBtn').textContent=document.documentElement.getAttribute('data-theme')==='dark'?'☀️ Nền sáng':'🌙 Nền tối'</script>"""

DASH_HTML = BASE_CSS + """
<title>FocusDesk</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#2563eb"><link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png"><link rel="icon" href="/static/favicon.svg" type="image/svg+xml"><link rel="icon" href="/favicon.ico" sizes="any"><meta name="mobile-web-app-capable" content="yes">
<meta name="csrf-token" content="{{ csrf }}">
<style>
body{padding-bottom:calc(76px + env(safe-area-inset-bottom))}
.app{display:flex;min-height:100vh}
.side{display:none}
.main{flex:1;min-width:0;max-width:1100px;margin:0 auto;padding:12px 14px}
.top{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px}.brand img{width:34px;height:34px;border-radius:9px}
.bnav{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);display:flex;
 padding:6px 4px calc(6px + env(safe-area-inset-bottom));z-index:20;box-shadow:0 -2px 10px rgba(15,23,42,.06)}
.bnav a,.side a{flex:1;text-align:center;text-decoration:none;color:var(--mut);font-size:11.5px;padding:4px 0;border-radius:12px;cursor:pointer}
.bnav a i,.side a i{display:block;font-style:normal;font-size:22px;line-height:1.2}
.bnav a.act,.side a.act{color:var(--acc);font-weight:700}
.chips{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px;margin-bottom:10px}
.chip{white-space:nowrap;border:1px solid var(--line);background:var(--card);padding:8px 14px;border-radius:999px;cursor:pointer;font-size:14px}
.chip.act{background:var(--acc);color:#fff;border-color:var(--acc)}
.seg{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 12px}.seg button{flex:1;min-width:120px}
.seg button.act{background:var(--acc);color:#fff;border-color:var(--acc)}
.hero{border-radius:18px;padding:18px;color:#fff;margin-bottom:12px}
.hero.ok{background:linear-gradient(135deg,#16a34a,#22c55e)}.hero.warn{background:linear-gradient(135deg,#d97706,#f59e0b)}
.hero.bad{background:linear-gradient(135deg,#dc2626,#f97316)}.hero.off{background:linear-gradient(135deg,#475569,#94a3b8)}
.hero .st{font-size:24px;font-weight:800;margin:2px 0}.hero .sub{opacity:.92;font-size:14px;word-break:break-word}
.hero .meta{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px;font-size:13px;opacity:.95}
.hero .meta b{font-size:20px;display:block}
.acts{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.bigbtn{padding:16px;font-size:16px;font-weight:700;border-radius:14px;min-height:56px}
.bigbtn.pri{background:var(--acc);color:#fff;border-color:var(--acc)}
.more{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:8px}
.more button{min-height:48px}
.sum3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:8px 0}
.sum3 div{background:var(--pill);border-radius:12px;padding:10px;text-align:center}.sum3 b{display:block;font-size:20px}
.bar{height:12px;border-radius:6px;background:var(--pill);overflow:hidden;display:flex}.bar span{display:block;height:100%}
.c-h{background:#22c55e}.c-g{background:#ef4444}.c-k{background:#94a3b8}
.hours{display:flex;align-items:stretch;gap:3px;height:120px;margin:10px 0 2px}
.hours div{flex:1;display:flex;flex-direction:column-reverse;min-width:6px;border-radius:4px 4px 0 0;overflow:hidden;background:var(--pill)}
.hours span{display:block;width:100%}
.hlab{display:flex;gap:3px;font-size:10px;color:var(--mut)}.hlab span{flex:1;text-align:center}
.app-row{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)}
.app-row .ic{font-size:22px;width:30px;text-align:center}.app-row .nm{flex:1;min-width:0}
.app-row .nm div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.app-row .mn{font-weight:700}
.cols>div,.card{min-width:0}.card{overflow-wrap:anywhere}
.app-row .nm div.sub2{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;font-size:13px;line-height:1.35}
.app-row .acts2{flex:none;display:flex;flex-direction:column;gap:3px;align-items:flex-end}
.ev-more{margin-top:8px;font-size:13px}.reset-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px}
.tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;color:#fff}
.sdays{display:flex;gap:3px;flex-wrap:wrap;margin:10px 0 4px}.sdays span{width:15px;height:15px;border-radius:4px;background:var(--line)}
.sdays span.s1{background:#86efac}.sdays span.s2{background:#22c55e}.sdays span.s3{background:#15803d}
.sess{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:4px 10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}
.sess .why{grid-column:2/4;font-size:12.5px;color:var(--mut)}
.frm{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px 10px;margin:8px 0}
.frm label{font-size:12.5px;color:var(--mut);display:flex;flex-direction:column;gap:3px}.frm input{width:100%}
.rw{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--line)}.rw .nm{flex:1;min-width:0}
.lmsurl{display:block;font-size:12.5px;word-break:break-all;background:var(--pill);padding:6px 8px;border-radius:8px;margin:4px 0 8px}
.open-app{border-bottom:1px solid var(--line);padding:6px 0}.open-app summary{display:flex;align-items:center;gap:10px;cursor:pointer;list-style:none}
.open-app summary::-webkit-details-marker{display:none}.open-app .ic{font-size:20px;width:28px;text-align:center}
.open-app .nm{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:14px}
.tab-row{display:flex;gap:6px;align-items:center;font-size:13px;padding:4px 0 4px 38px;color:var(--txt)}
.tab-row .tt{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sw{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:1px solid var(--line)}
.sw .t{font-weight:600}.sw .d{font-size:12.5px;color:var(--mut)}
.tg{position:relative;width:50px;height:28px;flex:none}.tg input{opacity:0;width:0;height:0}
.tg span{position:absolute;inset:0;background:#cbd5e1;border-radius:999px;transition:.2s;cursor:pointer}
.tg span:before{content:"";position:absolute;width:22px;height:22px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s}
.tg input:checked+span{background:var(--acc)}.tg input:checked+span:before{transform:translateX(22px)}
textarea{width:100%;min-height:60px;font:inherit;border-radius:10px;border:1px solid var(--line);padding:8px;background:var(--input);color:var(--txt)}
.livebox{position:relative;aspect-ratio:16/9;max-height:70vh;width:100%;background:#0f172a;border-radius:14px;overflow:hidden}
.livebox img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:none}
.livebox .ph{position:absolute;inset:0;display:grid;place-items:center;color:#cbd5e1;text-align:center;padding:16px}
.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(86px + env(safe-area-inset-bottom));background:#0f172a;color:#fff;
 padding:10px 16px;border-radius:12px;font-size:14px;z-index:30;max-width:92vw;display:none}
.alert-row{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}.alert-row time{color:var(--mut);flex:none}
[data-view]{display:none}[data-view].show{display:block}
h3{margin:4px 0 10px}
@media (min-width:900px){
 body{padding-bottom:0}.bnav{display:none}
 .side{display:flex;flex-direction:column;gap:4px;width:220px;flex:none;padding:16px 10px;background:var(--card);
  border-right:1px solid var(--line);position:sticky;top:0;height:100vh}
 .side a{display:flex;align-items:center;gap:12px;text-align:left;font-size:15px;padding:12px 14px;flex:none}
 .side a i{display:inline;font-size:20px}.side a.act{background:#eff6ff}
 [data-theme=dark] .side a.act{background:#1e293b}
 .main{padding:22px 28px}
 .cols{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:16px;align-items:start}
 .main{max-width:1320px}
 .toast{bottom:24px}
}
</style>
<div class="app">
 <nav class="side">
  <div class="brand" style="padding:6px 10px 16px"><img src="/static/icon-192.png" alt="">FocusDesk</div>
  <a data-go="home"><i>🏠</i>Tổng quan</a><a data-go="camera"><i>📺</i>Camera</a>
  <a data-go="report"><i>📊</i>Báo cáo</a><a data-go="rules"><i>🛡️</i>Luật chặn</a><a data-go="reward"><i>🏆</i>Thưởng</a><a data-go="settings"><i>⚙️</i>Cài đặt</a>
 </nav>
 <div class="main">
  <div class="top"><div class="brand"><img src="/static/icon-192.png" alt="" class="m-only">FocusDesk</div>
   <div class="row">{% if picture %}<img class="avatar" src="{{ picture }}" alt="" referrerpolicy="no-referrer">{% endif %}</div></div>
  <div class="chips" id="devchips"></div>

  <section data-view="home">
   <div class="cols"><div>
    <div class="hero off" id="hero"><div class="sub" id="h-name">Đang tải…</div><div class="st" id="h-st">—</div>
     <div class="sub" id="h-sub"></div>
     <div class="meta"><div><b id="h-cfi">—</b>Mức tập trung</div><div><b id="h-study">—</b>Học hôm nay</div>
      <div><b id="h-fun">—</b>Giải trí hôm nay</div><div><b id="h-ai">—</b>Dùng AI</div></div></div>
    <div class="acts"><button class="bigbtn pri" onclick="go('camera');liveOn()">📺 Xem camera</button>
     <button class="bigbtn" onclick="lockNow()">🔒 Khóa máy</button></div>
    <details class="card"><summary>⋯ Thao tác khác</summary><div class="more">
     <button onclick="cmd('reset_ai')">♻️ Đặt lại giờ AI</button><button onclick="cmd('ai_on')">📷 Bật Camera AI</button>
     <button onclick="cmd('ai_off')">⏸️ Tắt Camera AI</button><button onclick="cmd('recalibrate')">🧍 Đo lại tư thế ngồi</button>
     <button onclick="go('rules')">🛡️ Sửa luật chặn</button><button onclick="go('report')">📊 Xem báo cáo</button></div></details>
    <div id="acks" class="card" style="display:none"></div>
    <div class="card"><h3>📌 Đang dùng gì</h3><div id="nowbox" class="mut">—</div><div id="openbox"></div>
     <div class="bar" id="h-bar" style="margin-top:12px"></div><div class="mut" id="h-legend" style="margin-top:6px"></div></div>
    <div class="card"><div class="row" style="justify-content:space-between;align-items:center"><h3 style="margin:4px 0">🕒 Thay đổi gần đây</h3>
     <button style="padding:4px 10px;font-size:12px" onclick="resetData('events')">🧹 Xóa</button></div>
     <p class="mut" style="margin:2px 0 6px;font-size:13px">AI phân loại từng app / trang vừa mở và cho biết đã chặn hay chưa. Chặn bằng nút ở thẻ <b>Đang dùng gì</b>.</p><div id="evbox" class="mut">Chưa có.</div></div>
   </div><div>
    <div class="card"><div class="row" style="justify-content:space-between;align-items:center"><h3 style="margin:4px 0">🔔 Cảnh báo gần đây</h3>
     <button style="padding:4px 10px;font-size:12px" onclick="resetData('alerts')">🧹 Xóa</button></div><div id="alerts" class="mut">Chưa có cảnh báo.</div></div>
    <div class="card" style="cursor:pointer" onclick="go('reward')"><div class="row" style="justify-content:space-between;align-items:center"><h3 style="margin:4px 0">🌱 Ca học & tưới cây thưởng</h3><span class="mut" style="font-size:13px">Xem ›</span></div>
     <div class="sum3"><div><b id="hs-pts">0</b>điểm</div><div><b id="hs-today">0</b>ca đạt hôm nay</div><div><b id="hs-water">0</b>lần tưới cây</div></div>
     <div id="hs-live" style="margin:6px 0 8px"></div>
     <div class="mut" id="hs-last" style="font-size:13px">Chưa có ca học nào.</div></div>
    <div class="card" id="boxcard"><h3 id="boxtitle">📦 Hộp điện thoại</h3><div id="boxst" class="mut">—</div>
     <div id="box2" class="row" style="display:none;margin-top:10px;flex-wrap:wrap;gap:6px"><button class="box2btn" onclick="boxPair()">📱 Ghép điện thoại mới</button><button class="box2btn" onclick="waterNow()">🌱 Tưới cây ngay</button></div>
     <div id="box2note" class="mut" style="display:none;margin-top:6px;font-size:12px">Cất điện thoại vào tủ, cắm sạc, đóng cửa → tủ tự bắt đầu ca học. Thời gian ca & ngưỡng đèn/còi: thẻ <b>🏆 Thưởng → Tiêu chí</b>.</div>
     <div id="box1" class="row" style="margin-top:10px;flex-wrap:wrap;gap:6px"><button class="boxbtn" onclick="boxMin(15)">🔓 Mở 15′</button><button class="boxbtn" onclick="boxMin(30)">🔓 Mở 30′</button><button class="boxbtn" onclick="boxMin(60)">🔓 Mở 60′</button><button class="boxbtn" onclick="boxMinAsk()">🔓 Mở … phút</button><button class="boxbtn" onclick="boxCmd('box_unlock')">🔓 Mở tới khi khóa</button><button class="boxbtn" onclick="cmd('box_lock')">🔒 Khóa hộp</button></div>
     <div id="box1note" class="mut" style="margin-top:6px;font-size:12px">Hết giờ, hộp tự khóa lại khi nắp đã đóng. Telegram: /mohop · /khoahop</div></div>
    <div class="card" id="aicard"><h3>🤖 AI hôm nay (ứng dụng + web)</h3><div id="aibox" class="mut">—</div></div>
   </div></div>
  </section>

  <section data-view="camera">
   <div class="card"><h3>📺 Camera bàn học</h3>
    <p class="mut" id="cam-note">Hình đi thẳng từ máy học sinh tới điện thoại, không lưu trên máy chủ. Link tự hết hạn sau 15 phút.</p>
    <div class="livebox"><div class="ph" id="live-ph">📷 Bấm “Bật xem” để xem trực tiếp</div><img id="live" alt=""></div>
    <div class="more" style="margin-top:10px"><button class="pri bigbtn" style="background:var(--acc);color:#fff" onclick="liveOn()">▶️ Bật xem</button>
     <button class="bigbtn" onclick="liveOff()">⏹️ Tắt xem</button><button class="bigbtn" onclick="snap()">📸 Chụp ảnh nét</button>
     <button class="bigbtn" onclick="lanOpen()">🏠 Xem trong Wi-Fi nhà</button></div></div>
  </section>

  <section data-view="report">
   <div class="card"><div class="row" style="justify-content:space-between"><h3>📊 Báo cáo hoạt động</h3>
    <div class="row"><button onclick="setDay(0)">Hôm nay</button><button onclick="setDay(-1)">Hôm qua</button>
     <input type="date" id="rdate" onchange="report()"></div></div>
    <div class="sum3"><div><b id="r-h" style="color:#16a34a">0</b>phút học</div><div><b id="r-g" style="color:#dc2626">0</b>phút giải trí</div>
     <div><b id="r-k">0</b>phút khác</div></div>
    <div class="bar" id="r-bar"></div><div class="mut" id="r-cmp" style="margin-top:6px"></div>
    <div class="hours" id="r-hours"></div><div class="hlab" id="r-hlab"></div>
    <p class="mut" id="r-upd"></p></div>
   <div class="card"><h3>🧩 Ứng dụng & trang web đã dùng</h3><div id="r-apps" class="mut">Chưa có dữ liệu.</div></div>
  </section>

  <section data-view="rules">
   <div class="card"><h3>🛡️ Chặn theo nhóm</h3><p class="mut">Bật nhóm nào thì máy tự đóng ứng dụng/trang web thuộc nhóm đó và báo bạn ngay.</p>
    <div id="groups"></div>
    <details style="margin-top:10px"><summary>✏️ Tùy chỉnh thêm (nâng cao)</summary>
     <label class="mut">Ứng dụng / game (tên .exe, cách nhau dấu phẩy)</label><textarea id="x-apps"></textarea>
     <label class="mut">Trang web (tên miền)</label><textarea id="x-dom"></textarea>
     <label class="mut">Từ khóa tiêu đề cửa sổ</label><textarea id="x-kw"></textarea></details></div>
   <div class="card"><h3>✅ Từ khóa an toàn</h3><p class="mut">Trang có tên miền hoặc tiêu đề chứa 1 trong các từ này sẽ <b>không bị tự chặn</b>: không bị AI tự thêm vào luật chặn theo phân loại, không bị đóng vì từ khóa giải trí, YouTube theo từ khóa hay “nhờ AI làm game”. Link / web bạn tự bấm chặn thì vẫn bị chặn.</p>
    <label class="mut">Cách nhau dấu phẩy, vd: bài giảng, toán, lớp 8, olm.vn, vietjack, hoc247, khan academy</label><textarea id="safe-kw" oninput="rulesTouched=true"></textarea>
    <div class="row" style="margin-top:8px"><button class="pri" style="background:var(--acc);color:#fff" onclick="saveRules()">💾 Lưu</button></div></div>
   <div class="card"><h3>🔗 Link đã chặn riêng</h3><p class="mut">Chỉ đúng video / trang này bị chặn, các trang khác cùng web vẫn vào được. Thêm bằng nút “Chặn link” / “Chặn” ở thẻ <b>Đang dùng gì</b> (trang chủ), hoặc tự thêm khi AI xét tiêu đề thấy video/trang thuộc loại bạn chọn tự chặn.</p><div id="pagebox"></div></div>
   <div class="card"><h3>▶️ YouTube</h3><p class="mut">Bài giảng vẫn xem bình thường. Máy chỉ đóng tab khi <b>từ khóa tìm kiếm</b> hoặc <b>tên video</b> chứa từ khóa giải trí.</p>
    <div class="seg" id="yt-mode"><button type="button" data-m="keyword" onclick="ytMode(this)">🔎 Chặn theo từ khóa</button><button type="button" data-m="block" onclick="ytMode(this)">⛔ Chặn toàn bộ</button><button type="button" data-m="allow" onclick="ytMode(this)">✅ Cho phép hết</button></div>
    <div id="yt-kw-box"><label class="mut">Từ khóa cấm khi tìm / xem (cách nhau dấu phẩy)</label><textarea id="yt-kw" oninput="rulesTouched=true"></textarea>
    <div class="sw"><div><div class="t">Chặn YouTube Shorts</div><div class="d">Video ngắn lướt liên tục, dễ nghiện</div></div>
     <label class="tg"><input type="checkbox" id="yt-shorts" checked onchange="rulesTouched=true"><span></span></label></div></div></div>
   <div class="card"><h3>🔔 Báo khi học sinh đổi việc</h3><p class="mut">Mỗi lần mở ứng dụng mới / vào trang web mới, hệ thống tự phân tích trang đó là học tập, game, mạng xã hội...</p>
    <div class="seg" id="nv-mode"><button type="button" data-m="risky" onclick="nvMode(this)">⚠️ Chỉ trang đáng ngờ</button><button type="button" data-m="all" onclick="nvMode(this)">📣 Mọi thay đổi</button><button type="button" data-m="off" onclick="nvMode(this)">🔕 Tắt</button></div>
    <div class="t" style="margin:12px 0 4px">Tự chặn theo phân loại</div>
    <div class="d mut" style="margin-bottom:6px">Web chỉ có 1 loại nội dung (web game, mạng xã hội…) thuộc loại đã chọn: chặn cả web. YouTube, Google tìm kiếm: AI xét <b>tiêu đề từng video / từ khóa tìm</b> — chỉ chặn đúng video giải trí đó, video bài giảng vẫn xem được. Trang đã vào gần đây cũng được xét lại khi Lưu.</div>
    <div id="bk" style="display:flex;flex-wrap:wrap;gap:8px">
     <label class="pill"><input type="checkbox" data-k="game" onchange="rulesTouched=true"> 🎮 Web game</label>
     <label class="pill"><input type="checkbox" data-k="social" onchange="rulesTouched=true"> 💬 Mạng xã hội</label>
     <label class="pill"><input type="checkbox" data-k="video" onchange="rulesTouched=true"> 🎬 Video / phim</label>
     <label class="pill"><input type="checkbox" data-k="shopping" onchange="rulesTouched=true"> 🛒 Mua sắm</label>
     <label class="pill"><input type="checkbox" data-k="comics" onchange="rulesTouched=true"> 📚 Truyện tranh</label></div></div>
   <div class="card"><h3>🤖 Giới hạn dùng AI</h3><p class="mut">Tính chung ChatGPT, Gemini, Claude, Copilot… trên web và ứng dụng AI cài trên máy. Chỉ tính lúc cửa sổ AI ở trên cùng và học sinh đang thao tác.</p>
    <div class="sw"><div><div class="t">Số phút mỗi ngày</div><div class="d">0 = không cho dùng AI. Đếm lại từ 0 mỗi ngày.</div></div>
     <input type="number" id="ai-min" min="0" max="240" step="5" style="width:90px" oninput="rulesTouched=true"></div>
    <div class="t" style="margin:10px 0 6px">Khi hết giờ</div>
    <div class="seg" id="ai-over"><button type="button" data-m="block" onclick="segPick(this)">⛔ Đóng tab / app AI</button><button type="button" data-m="warn" onclick="segPick(this)">🔔 Chỉ báo phụ huynh</button></div>
    <div class="t" style="margin:12px 0 6px">Nhờ AI làm game rồi chơi (Gemini Canvas, Claude Artifact, websim…)</div>
    <div class="seg" id="ai-game"><button type="button" data-m="block" onclick="segPick(this)">⛔ Chặn + báo</button><button type="button" data-m="warn" onclick="segPick(this)">🔔 Chỉ báo</button><button type="button" data-m="off" onclick="segPick(this)">Tắt</button></div>
    <p class="mut" style="margin-top:8px">Nhận ra qua tên cuộc trò chuyện (vd “Tạo game rắn săn mồi”), trang chạy ứng dụng do AI tạo, và kiểu bấm phím (mũi tên/WASD/Space liên tục khi đang mở AI).</p></div>
   <div class="card"><h3>⚙️ Cách giám sát</h3>
    <div class="sw"><div><div class="t">Giám sát cả khi học sinh chưa đăng nhập</div><div class="d">Bật: chặn & báo mọi lúc máy bật. Tắt: chỉ khi học sinh vào chế độ học.</div></div>
     <label class="tg"><input type="checkbox" id="opt-always"><span></span></label></div>
    <div class="sw"><div><div class="t">Chặn tận gốc trang web (DNS)</div><div class="d">Trình duyệt nào cũng không vào được. Cần app FocusDesk chạy quyền Admin.</div></div>
     <label class="tg"><input type="checkbox" id="opt-dns" checked><span></span></label></div>
    <p><button class="bigbtn pri" style="width:100%" onclick="saveRules()">💾 Lưu & áp dụng</button></p><p class="mut" id="rmsg"></p></div>
  </section>

  <section data-view="reward">
   <div class="cols"><div>
    <div class="card"><div class="row" style="justify-content:space-between;align-items:center"><h3 style="margin:4px 0">🌱 Ca học thành công → tưới cây</h3>
     <button style="padding:4px 10px;font-size:12px" onclick="waterNow()">🌱 Tưới cây ngay</button></div>
     <div class="sum3"><div><b id="w-pts" style="color:#b45309">0</b>điểm hiện có</div><div><b id="w-water" style="color:#16a34a">0</b>🌱 lần tưới cây</div><div><b id="w-30" style="color:#16a34a">0</b>ca đạt / 30 ngày</div></div>
     <div id="w-line" class="mut"></div><div class="sdays" id="w-days"></div><div class="mut" style="font-size:12px">30 ngày gần nhất · ô càng đậm = càng nhiều ca thành công</div>
     <p class="mut" id="w-crit" style="font-size:13px"></p></div>
    <div class="card"><div class="row" style="justify-content:space-between;align-items:center"><h3 style="margin:4px 0">📋 Các ca học gần đây</h3>
     <button style="padding:4px 10px;font-size:12px" onclick="sessCsv()">⬇️ Tải CSV</button></div><div id="w-list" class="mut">Chưa có ca học nào.</div></div>
   </div><div>
    <div class="card"><h3>🎁 Điểm thưởng & đổi quà (tùy chọn)</h3><p class="mut" style="font-size:13px">Con đủ điểm thì bấm “Đổi”: điểm bị trừ và ghi vào lịch sử để hai bên cùng theo dõi.</p><div id="w-rewards"></div><div id="w-redeemed" class="mut" style="margin-top:8px;font-size:13px"></div></div>
    <div class="card"><h3>🏫 Kết nối LMS của trường · tuyên dương</h3>
     <p class="mut" style="font-size:13px">Tạo 1 link <b>chỉ-đọc</b> gửi giáo viên chủ nhiệm: trang <b>Ghi nhận nỗ lực học tập</b> in được, file CSV cho Google Sheets / Excel / Moodle / K12Online và JSON cho phần mềm của trường. Link chỉ chứa họ tên, lớp, mã HS và số liệu ca học — không có ảnh camera, không có lịch sử web. Thu hồi bất cứ lúc nào.</p>
     <div id="w-lms"></div></div>
    <div class="card"><h3>⚙️ Tiêu chí ca học thành công</h3>
     <div class="frm">
      <label>Thời gian 1 ca học (phút)<input id="s-min" type="number" min="5" max="240"></label>
      <label>Vi phạm tối đa / ca<input id="s-viol" type="number" min="0" max="20"></label>
      <label>Tập trung ≥ (% thời gian học)<input id="s-focus" type="number" min="0" max="100"></label>
      <label>Tưới cây mỗi ca đạt (giây bơm)<input id="s-wsec" type="number" min="0.5" max="10" step="0.5"></label>
      <label>Điểm mỗi ca đạt<input id="s-pts" type="number" min="1" max="1000"></label>
      <label>Nhân điểm tối đa (khối)<input id="s-blocks" type="number" min="1" max="10"></label>
      <label>Tủ ESP32: nháy đèn khi CFI &lt;<input id="s-led" type="number" min="0" max="100"></label>
      <label>Tủ ESP32: kêu còi khi CFI &lt;<input id="s-buzz" type="number" min="0" max="100"></label></div>
     <label class="row" style="gap:8px;margin:4px 0 6px;align-items:center"><input type="checkbox" id="s-water"> <span><b>🌱 Tưới cây khi ca học đạt chuẩn</b> <span class="mut" style="font-size:12.5px">(mô-đun ESP32 + bơm mini trên bàn học)</span></span></label>
     <p class="mut" style="font-size:12.5px">Mặc định: ca học ≥ 25 phút, không vi phạm và <b>tập trung trên 2/3 (67%) thời gian học</b> → máy tự bật bơm tưới chậu cây. Tập trung = khoảng thời gian camera AI thấy học sinh nhìn bài, ngồi đúng (điểm CFI ≥ 60%); chỉ xét khi máy có camera (đặt 0 = bỏ qua). Vi phạm = mở game / web giải trí, nhờ AI làm game, quá giờ AI, cầm điện thoại. Thời gian xin đi vệ sinh không tính. Điểm thưởng: ca dài gấp đôi/ba thời gian tối thiểu được nhân điểm. <b>Có tủ điện thoại ESP32:</b> đóng cửa tủ + cắm sạc là bắt đầu ca, tủ đếm ngược đúng “Thời gian 1 ca học”; trong ca mở cửa / rút sạc = vi phạm, hết giờ rút ra không báo nữa; đèn đỏ nháy / còi kêu khi mức tập trung (CFI) dưới ngưỡng.</p>
     <h3 style="margin-top:14px">👤 Thông tin cho giáo viên</h3>
     <div class="frm">
      <label>Họ tên học sinh<input id="s-name" maxlength="60"></label><label>Lớp<input id="s-class" maxlength="30"></label>
      <label>Mã học sinh (LMS)<input id="s-code" maxlength="40"></label><label>Trường<input id="s-school" maxlength="80"></label></div>
     <h3 style="margin-top:14px">🎁 Phần thưởng</h3>
     <p class="mut" style="font-size:12.5px">Mỗi dòng: <b>tên phần thưởng | số điểm</b></p>
     <textarea id="s-rewards" rows="4" style="width:100%"></textarea>
     <details style="margin-top:8px"><summary class="mut">Nâng cao: webhook LMS</summary>
      <p class="mut" style="font-size:12.5px">URL https (vd Google Apps Script Web App của trường) — mỗi ca thành công web gửi JSON {student_code, student_name, class_name, day, minutes, focus, points, streak}.</p>
      <input id="s-hook" placeholder="https://script.google.com/macros/s/…/exec" style="width:100%"></details>
     <button class="pri" style="margin-top:10px;background:var(--acc);color:#fff" onclick="saveStudy()">💾 Lưu</button></div>
   </div></div>
  </section>

  <section data-view="settings">
   <div class="card"><h3>♻️ Đặt lại dữ liệu máy đang chọn</h3><p class="mut">Làm lại từ đầu mà không cần ra máy học sinh. Máy áp dụng trong vài giây (phần trên máy cần bản 1.19.7).</p>
    <div class="reset-grid">
     <button onclick="resetData('events')">🧹 Xóa lịch sử Thay đổi gần đây</button>
     <button onclick="resetData('alerts')">🧹 Xóa cảnh báo (web + máy)</button>
     <button onclick="resetData('report_today')">🧹 Xóa báo cáo hôm nay</button>
     <button onclick="cmd('reset_ai')">♻️ Đặt lại giờ dùng AI</button>
     <button onclick="resetData('blocked_links')">🔗 Bỏ chặn tất cả link riêng</button>
     <button onclick="resetData('study')">🏆 Xóa lịch sử ca học & điểm</button>
     <button class="danger" onclick="resetData('rules_all')">⛔ Xóa toàn bộ luật chặn</button></div></div>
   <div class="card"><h3>➕ Thêm máy học sinh</h3><p class="mut">Mã 6 số hiện trong app FocusDesk → tab “Kết nối phụ huynh”.</p>
    <div class="row"><input id="pcode" placeholder="Mã 6 số" inputmode="numeric" maxlength="6" style="width:120px">
     <input id="pname" placeholder="Tên máy (vd: Máy của Minh)" style="flex:1"><button class="pri" style="background:var(--acc);color:#fff" onclick="pair()">Ghép</button></div>
    <p id="pmsg" class="mut"></p></div>
   <div class="card"><h3>🔔 Thông báo trên máy này</h3><p class="mut">Hiện cảnh báo ngay trên điện thoại / máy tính kể cả khi không mở web (Android Chrome, Edge, PC).</p>
    <div class="row"><button class="pri" style="background:var(--acc);color:#fff" onclick="enableNotify()">🔔 Bật thông báo</button>
     <button onclick="testNotify()">Gửi thử</button></div><p id="nmsg" class="mut"></p></div>
   <div class="card"><h3>💬 Telegram</h3><p class="mut">Nhận cảnh báo tức thì; gõ /status /lock /anh để điều khiển.</p>
    <div id="chats" class="mut"></div><p><button onclick="tglink()">Kết nối Telegram</button> <span id="tgmsg" class="mut"></span></p></div>
   <div class="card"><h3>🖥️ Máy đang quản lý</h3><div id="devlist"></div></div>
   <div class="card"><h3>🎨 Ứng dụng</h3>
    <div class="row"><button id="installBtn" style="display:none" onclick="installApp()">📲 Cài vào điện thoại</button>
     <button id="themeBtn" onclick="toggleTheme()">🌙 Nền tối</button></div>
    <p class="mut">Android: menu Chrome ⋮ → “Thêm vào màn hình chính” nếu nút cài không hiện.</p>
    <p class="mut">Đăng nhập: {{ name or email }}</p>
    <form method="post" action="/logout"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="danger">Đăng xuất</button></form></div>
  </section>
 </div>
</div>
<nav class="bnav"><a data-go="home"><i>🏠</i>Tổng quan</a><a data-go="camera"><i>📺</i>Camera</a>
 <a data-go="report"><i>📊</i>Báo cáo</a><a data-go="rules"><i>🛡️</i>Luật chặn</a><a data-go="reward"><i>🏆</i>Thưởng</a><a data-go="settings"><i>⚙️</i>Cài đặt</a></nav>
<div class="toast" id="toast"></div>
<script>
const CSRF=document.querySelector('meta[name=csrf-token]').content;
const $=id=>document.getElementById(id);
function ct(t){ // tiêu đề cửa sổ gọn: bỏ "(50) ", "and 2 more pages", tên hồ sơ + tên trình duyệt
 t=String(t||'').replace(/[\\u200b\\u200e\\u200f]/g,'').replace(/^\\(\\d+\\+?\\)\\s*/,'').replace(/ and \\d+ more pages?/,'');
 return t.replace(/( - [^-]{1,30})? - (Microsoft ?Edge|Google Chrome|Cốc Cốc|Mozilla Firefox|Brave|Opera)$/,'');}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function api(url,body){const r=await fetch(url,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},body:body?JSON.stringify(body):undefined});
 if(r.status===401){location.href='/login';return {};}return r.json();}
const VAPID='{{ vapid }}';let seenAlerts=null;
function b64u(s){const p='='.repeat((4-s.length%4)%4),b=atob((s+p).replace(/-/g,'+').replace(/_/g,'/'));return Uint8Array.from([...b].map(c=>c.charCodeAt(0)));}
function nstat(){const e=$('nmsg');if(!e)return;if(!('Notification' in window)){e.textContent='Trình duyệt này không hỗ trợ thông báo.';return;}
 e.textContent=Notification.permission==='granted'?('✅ Đã bật'+(VAPID?' · nhận cả khi đóng web':' · chỉ khi web đang mở')):(Notification.permission==='denied'?'⛔ Đã chặn — mở cài đặt trang để cho phép':'Chưa bật');}
async function enableNotify(){if(!('Notification' in window))return nstat();const p=await Notification.requestPermission();
 if(p==='granted'&&VAPID&&'serviceWorker' in navigator&&'PushManager' in window){try{const reg=await navigator.serviceWorker.ready;
  let sub=await reg.pushManager.getSubscription();if(!sub)sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64u(VAPID)});
  const r=await api('/api/parent/push',sub.toJSON());if(r.error)toast(r.error);}catch(e){toast('Không đăng ký được Web Push: '+e.message);}}
 nstat();}
async function testNotify(){if(VAPID){const r=await api('/api/parent/push/test',{});toast(r.message||r.error);}else showNote('🔔 Thông báo thử','Cảnh báo sẽ hiện như thế này.');}
async function showNote(title,body){try{if('Notification' in window&&Notification.permission==='granted'){const reg=await navigator.serviceWorker.ready;
 reg.showNotification(title,{body,icon:'/static/icon-192.png',tag:'live-'+Date.now(),vibrate:[200,100,200]});}}catch(e){}}
function checkNewAlerts(list){const keys=[];for(const d of list)for(const a of (d.alerts||[]))keys.push([d.id+'|'+a.time+'|'+a.message,d,a]);
 if(seenAlerts===null){seenAlerts=new Set(keys.map(k=>k[0]));return;}
 const fresh=keys.filter(k=>!seenAlerts.has(k[0]));fresh.forEach(k=>seenAlerts.add(k[0]));
 for(const [,d,a] of fresh.slice(0,3)){toast('🚨 '+d.name+': '+a.message);if(navigator.vibrate)navigator.vibrate([150,80,150]);
  if(document.hidden&&!VAPID)showNote('🖥️ '+d.name,a.message);}}
function toast(t){const e=$('toast');e.textContent=t;e.style.display='block';clearTimeout(e._t);e._t=setTimeout(()=>e.style.display='none',3500);}
let DEV=[],CUR=null,VIEW='home',lastAck='';
try{CUR=localStorage.getItem('sd_dev')}catch(e){}
const cur=()=>DEV.find(d=>d.id===CUR)||DEV[0];
function go(v){VIEW=v;document.querySelectorAll('[data-view]').forEach(s=>s.classList.toggle('show',s.dataset.view===v));
 document.querySelectorAll('[data-go]').forEach(a=>a.classList.toggle('act',a.dataset.go===v));
 if(v==='report')report();if(v==='rules')fillRules(true);if(v==='camera')showLive();if(v==='reward')loadStudy(true);window.scrollTo(0,0);}
document.querySelectorAll('[data-go]').forEach(a=>a.onclick=()=>go(a.dataset.go));
const fmtMin=s=>{s=Math.round((s||0)/60);return s>=60?Math.floor(s/60)+'g'+String(s%60).padStart(2,'0'):s+'′';};
const CAT={Hoc_tap:['Học tập','#16a34a','📘'],Giai_tri:['Giải trí','#dc2626','🎮'],Khac:['Khác','#94a3b8','🗂️']};
function appIcon(app,title){const t=(app+' '+title).toLowerCase();
 const M=[['youtube','▶️'],['facebook','📘'],['tiktok','🎵'],['zalo','💬'],['chrome','🌐'],['msedge','🌐'],['firefox','🌐'],['coccoc','🌐'],['winword','📝'],['word','📝'],
  ['excel','📊'],['powerpnt','📽️'],['zoom','🎥'],['teams','🎥'],['classin','🎥'],['code','💻'],['python','💻'],['notepad','🗒️'],['calc','🧮'],['geogebra','📐'],
  ['steam','🎮'],['roblox','🎮'],['game','🎮'],['spotify','🎧'],['discord','💬'],['telegram','💬'],['explorer','📁'],['pdf','📕'],['acrobat','📕']];
 for(const [k,i] of M) if(t.includes(k)) return i; return '🖥️';}
function statusOf(v){const s=v.status||{},p=s.posture||{},n=v.now||{};
 if(!v.online){if(v.shutdown_at&&v.shutdown_at>=Date.now()/1000-v.ago-2){const tm=new Date(v.shutdown_at*1000).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'});
   return ['off','⏻ FocusDesk đã được tắt lúc '+tm,v.shutdown_reason||'Thoát phần mềm'];}
  return ['off','⚪ Máy đang tắt / mất mạng','Cập nhật lần cuối '+Math.round(v.ago/60)+' phút trước'];}
 if(p.camera&&p.state==='ABSENT') return ['warn','🟠 Học sinh đang vắng mặt','Không thấy ai trước camera'];
 const br=s.break||{};if(br.active){const ref=br.left_t||br.start;const mins=ref?Math.max(0,Math.round((Date.now()/1000-ref)/60)):0;
  return ['warn','🚻 Đang đi vệ sinh',(br.left_t?'Rời bàn lúc '+new Date(br.left_t*1000).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'}):'Vừa xin phép')+' · '+mins+' phút'];}
 if(p.camera&&p.state==='PLAYING') return ['bad','🔴 '+(p.state_vi||'Nghịch đồ'),p.held_object?('Đang cầm '+p.held_object):(n.title||'')];
 if(n.category==='Giai_tri') return ['bad','🔴 Đang giải trí',n.title];
 if(p.camera&&['LOOK_AWAY','DROWSY','IDLE'].includes(p.state)) return ['warn','🟡 '+(p.state_vi||'Mất tập trung'),n.title||''];
 if(n.category==='Hoc_tap') return ['ok','🟢 Đang học',n.title];
 if(n.category==='Idle') return ['off','💤 Không dùng máy',n.title||''];
 return ['ok','🟢 Đang dùng máy',n.title||(s.role==='student'?'Chế độ học sinh':'')];}
function renderChips(){$('devchips').innerHTML=DEV.length>1?DEV.map(d=>`<span class="chip ${d.id===cur().id?'act':''}" onclick="pick('${d.id}')">${d.online?'🟢':'⚪'} ${esc(d.name)}</span>`).join(''):'';}
function pick(id){CUR=id;try{localStorage.setItem('sd_dev',id)}catch(e){};renderAll();if(VIEW==='report')report();if(VIEW==='rules')fillRules(true);if(VIEW==='reward')loadStudy(true);}
function renderAll(){const v=cur();renderChips();renderSettings();
 if(!v){$('h-name').textContent='Chưa có máy nào';$('h-st').textContent='➕ Ghép máy trong Cài đặt';$('hero').className='hero off';return;}
 const [cls,st,sub]=statusOf(v),s=v.status||{},p=s.posture||{},t=v.today||{};
 $('hero').className='hero '+cls;$('h-name').textContent=v.name+(s.role==='student'?' · đang ở chế độ học sinh':'');
 $('h-st').textContent=st;$('h-sub').textContent=ct(sub||'');
 $('h-cfi').textContent=p.camera&&p.cfi!=null?Math.round(p.cfi)+'%':'—';
 $('h-study').textContent=fmtMin(t.Hoc_tap);$('h-fun').textContent=fmtMin(t.Giai_tri);
 $('h-ai').textContent=Math.round((s.ai_seconds||0)/60)+'/'+(s.max_ai_minutes||'?')+'′';
 const sum=(t.Hoc_tap||0)+(t.Giai_tri||0)+(t.Khac||0);
 $('h-bar').innerHTML=sum?['Hoc_tap','Giai_tri','Khac'].map(k=>`<span style="width:${100*(t[k]||0)/sum}%;background:${CAT[k][1]}"></span>`).join(''):'';
 $('h-legend').textContent=sum?`Hôm nay: ${fmtMin(t.Hoc_tap)} học · ${fmtMin(t.Giai_tri)} giải trí · ${fmtMin(t.Khac)} khác`:'Chưa có dữ liệu hôm nay';
 const n=v.now||{};NOW=n;$('nowbox').innerHTML=n.title?`<div class="app-row"><div class="ic">${appIcon(n.app||'',n.title)}</div><div class="nm"><div><b>${esc(n.domain||n.app||'')}</b>${n.domain?` <span class="mut">${esc(n.app||'')}</span>`:''}</div><div class="mut sub2">${esc(ct(n.title))}</div></div>
  ${CAT[n.category]?`<span class="tag" style="background:${CAT[n.category][1]}">${CAT[n.category][0]}</span>`:''}${blockBtns(n)}</div>`:(n.category==='Idle'?'<span class="mut">💤 Đang rời máy</span>':'—');
 renderOpen(n.open||[],p);renderEvents(v);renderBox(v);renderAI(v);renderStudyHome(v);
 $('alerts').innerHTML=(v.alerts||[]).slice(0,6).map(a=>`<div class="alert-row"><time>${esc(a.time)}</time><div>${esc(a.message)}</div></div>`).join('')||'Không có cảnh báo. 👍';
 const acks=v.acks||[];if(acks.length){$('acks').style.display='block';$('acks').innerHTML='<h3>✅ Kết quả lệnh</h3>'+acks.slice(0,3).map(a=>`<div class="${a.ok?'':'err'}" style="font-size:14px;padding:3px 0">${a.ok?'✅':'⚠️'} ${esc(a.time)} ${esc(a.msg)}</div>`).join('');
  const k=acks[0].time+acks[0].msg;if(lastAck&&k!==lastAck)toast((acks[0].ok?'✅ ':'⚠️ ')+acks[0].msg);lastAck=k;}
 if(VIEW==='camera')showLive();}
let NOW={},OPEN=[];
function blockBtns(x,ti){ // x = cửa sổ đang dùng (ti undefined) hoặc 1 tab (ti = "a,b")
 if(x.blocked)return ` <span class="pill">⛔ đã chặn</span>`;
 const b=[];const s='padding:3px 9px;font-size:12px';
 if(ti===undefined){if(x.domain){if(x.page_ok)b.push(`<button class="danger" style="${s}" onclick="blockNow('page')">Chặn link</button>`);
   b.push(`<a href="#" class="mut" style="font-size:11px;white-space:nowrap" onclick="event.preventDefault();blockDom('${esc(x.domain)}')">chặn cả web</a>`);}
  else if(x.app_ok)b.push(`<button class="danger" style="${s}" onclick="blockApp('${esc(x.app||'')}')">Chặn app</button>`);}
 else if(x.page_ok)b.push(`<button class="danger" style="${s}" onclick="blockTab('${ti}')">Chặn</button>`);
 return b.length?`<span style="display:inline-flex;flex-direction:column;gap:3px;align-items:flex-end;margin-left:8px">${b.join('')}</span>`:'';}
async function blockNow(){const v=cur(),n=NOW;if(!v||!n.domain)return;if(!confirm('Chỉ chặn link đang mở: '+n.title+' ?'))return;
 const r=await api(`/api/parent/devices/${v.id}/block`,{domain:n.domain,scope:'page',title:n.title,page:n.page||''});toast(r.message||r.error);load();}
async function blockTab(k){const v=cur();const [a,t]=k.split(',').map(Number);const tab=((OPEN[a]||{}).tabs||[])[t];if(!v||!tab)return;
 if(!confirm('Chặn tab: '+tab.title+' ?'))return;
 const r=await api(`/api/parent/devices/${v.id}/block`,{domain:tab.domain||'',scope:'page',title:tab.title,page:''});toast(r.message||r.error);load();}
async function blockApp(exe){const v=cur();if(!v||!exe||!confirm('Chặn ứng dụng '+exe+'? Máy sẽ tự tắt khi học sinh mở.'))return;
 const r=await api(`/api/parent/devices/${v.id}/block`,{scope:'app',exe});toast(r.message||r.error);load();}
function renderOpen(list,p){const box=$('openbox');let h='';OPEN=list;
 if(p&&p.held_object)h+=`<div class="alert-row" style="margin-top:8px"><time>📷 AI</time><div>Đang cầm <b>${esc(p.held_object)}</b></div></div>`;
 if(p&&p.action&&p.action.vi)h+=`<div class="alert-row"><time>🧠 AI</time><div>Hành động: <b>${esc(p.action.vi)}</b> (${Math.round((p.action.score||0)*100)}%)</div></div>`;
 if(list.length){const tag=c=>CAT[c]?`<span class="tag" style="background:${CAT[c][1]}">${CAT[c][0]}</span>`:'';
  h+=`<div class="mut" style="margin:12px 0 4px;font-size:13px;font-weight:700">ĐANG MỞ · ${list.length} ứng dụng</div>`+list.map((a,ai)=>{
   const items=(a.tabs.length?a.tabs:a.windows);const sub=a.tabs.length?`${a.tabs.length} tab`:(a.windows[0]?ct(a.windows[0].title):'');
   return `<details class="open-app"${a.focused?' open':''}><summary><span class="ic">${appIcon(a.exe,(a.windows[0]||{}).title||'')}</span>
    <span class="nm"><b>${esc(a.exe)}</b>${a.focused?' <span class="pill on" style="font-size:11px">đang dùng</span>':''}<br><span class="mut">${esc(sub)}</span></span>${tag(a.category)}${a.blocked?' <span class="pill">⛔ đã chặn</span>':(a.app_ok&&!a.tabs.length?` <button class="danger" style="padding:3px 9px;font-size:12px" onclick="event.preventDefault();blockApp('${esc(a.exe)}')">Chặn app</button>`:'')}</summary>
    ${items.length>1||a.tabs.length?items.map((t,ti)=>`<div class="tab-row">${a.tabs.length?'🗂️':'🪟'} <span class="tt" title="${esc(t.title)}">${esc(ct(t.title))}</span>${t.category!=='Khac'?tag(t.category):''}${a.tabs.length?blockBtns(t,ai+','+ti):''}</div>`).join(''):''}</details>`}).join('');}
 box.innerHTML=h;}
function segPick(b){b.parentElement.querySelectorAll('button').forEach(x=>x.classList.toggle('act',x===b));rulesTouched=true;}
function segSet(id,m){const b=document.querySelector(`#${id} button[data-m="${m}"]`);if(b)b.parentElement.querySelectorAll('button').forEach(x=>x.classList.toggle('act',x===b));}
function segGet(id,def){return (document.querySelector(`#${id} button.act`)||{}).dataset?.m||def;}
function renderSettings(){$('devlist').innerHTML=DEV.map(d=>`<div class="app-row"><div class="ic">${d.online?'🟢':'⚪'}</div><div class="nm"><div><b>${esc(d.name)}</b></div>
 <div class="mut">${esc((d.status||{}).hostname||'')} · ${d.online?'trực tuyến':'ngoại tuyến'}</div></div><button class="danger" onclick="unlink('${d.id}')">Bỏ</button></div>`).join('')||'<span class="mut">Chưa có máy.</span>';}
async function load(){const d=await api('/api/parent/devices');if(!d.devices)return;DEV=d.devices;
 if(!DEV.find(x=>x.id===CUR)&&DEV.length)CUR=DEV[0].id;
 $('chats').innerHTML=(d.chats||[]).length?'Đã liên kết: '+d.chats.map(c=>'<span class="pill on">'+esc(c)+'</span>').join(' '):'Chưa liên kết Telegram.';
 RULES_SRV=d.rules||{};checkNewAlerts(DEV);renderAll();}
let RULES_SRV={};
async function cmd(t){const v=cur();if(!v)return;const r=await api(`/api/parent/devices/${v.id}/command`,{type:t});toast(r.message||r.error||'');}
function lockNow(){if(confirm('Khóa màn hình máy học sinh ngay?'))cmd('lock');}
async function pair(){const r=await api('/api/parent/pair',{code:$('pcode').value.trim(),name:$('pname').value.trim()});$('pmsg').textContent=r.message||r.error;load();}
async function tglink(){const r=await api('/api/parent/telegram/link',{});if(r.url){window.open(r.url,'_blank');$('tgmsg').textContent='Bấm Start trong Telegram để hoàn tất.';}else $('tgmsg').textContent=r.error;}
async function unlink(id){if(confirm('Bỏ quản lý máy này?')){await api(`/api/parent/devices/${id}/unlink`,{});load();}}
// ---------- Camera ----------
function liveUrls(v){const L=((v||{}).status||{}).live||{};if(!L.active)return null;const pub=L.public_url||'';
 return {pub,video:pub?pub.replace('/live?','/video_feed?')+'&fps=8&raw=1':'',snap:pub?pub.replace('/live?','/snapshot.jpg?'):'',lan:L.lan_url||'',note:L.note||''};}
function showLive(){const u=liveUrls(cur()),img=$('live'),ph=$('live-ph');
 if(u&&u.video){if(img.dataset.src!==u.video){img.src=u.video;img.dataset.src=u.video;}img.style.display='block';ph.style.display='none';
  $('cam-note').textContent='🟢 Đang xem trực tiếp (hình gốc, không lưu trên máy chủ).';}
 else{img.removeAttribute('src');img.dataset.src='';img.style.display='none';ph.style.display='grid';
  ph.textContent=u&&u.lan?'🏠 Chỉ xem được trong Wi-Fi nhà — bấm “Xem trong Wi-Fi nhà”':'📷 Bấm “Bật xem” để xem trực tiếp';
  $('cam-note').textContent=u&&u.note?('📶 '+u.note):'Hình đi thẳng từ máy học sinh tới điện thoại, không lưu trên máy chủ.';}}
async function liveOn(){await cmd('live_on');$('cam-note').textContent='⏳ Đang mở camera…';}
async function liveOff(){await cmd('live_off');const i=$('live');i.removeAttribute('src');i.dataset.src='';i.style.display='none';$('live-ph').style.display='grid';}
function snap(){const u=liveUrls(cur());if(u&&u.snap)window.open(u.snap+'&ts='+Date.now(),'_blank');
 else if(u&&u.lan)window.open(u.lan.replace('/live?','/snapshot.jpg?'),'_blank');else toast('Bấm “Bật xem” trước, hoặc gõ /anh trong Telegram.');}
function lanOpen(){const u=liveUrls(cur());if(u&&u.lan)window.open(u.lan,'_blank');else toast('Bấm “Bật xem” trước.');}
// ---------- Báo cáo ----------
const vnDay=off=>new Date(Date.now()+7*3600e3+off*864e5).toISOString().slice(0,10);
function setDay(off){$('rdate').value=vnDay(off);report();}
async function report(){const v=cur();if(!v)return;if(!$('rdate').value)$('rdate').value=vnDay(0);const day=$('rdate').value;
 const [r,y]=await Promise.all([api(`/api/parent/devices/${v.id}/activity?date=${day}`),
  api(`/api/parent/devices/${v.id}/activity?date=${new Date(new Date(day+'T00:00:00Z').getTime()-864e5).toISOString().slice(0,10)}`)]);
 const t=r.totals||{},ty=y.totals||{},sum=(t.Hoc_tap||0)+(t.Giai_tri||0)+(t.Khac||0);
 $('r-h').textContent=Math.round((t.Hoc_tap||0)/60);$('r-g').textContent=Math.round((t.Giai_tri||0)/60);$('r-k').textContent=Math.round((t.Khac||0)/60);
 $('r-bar').innerHTML=sum?['Hoc_tap','Giai_tri','Khac'].map(k=>`<span style="width:${100*(t[k]||0)/sum}%;background:${CAT[k][1]}"></span>`).join(''):'';
 const d=Math.round(((t.Hoc_tap||0)-(ty.Hoc_tap||0))/60),g=Math.round(((t.Giai_tri||0)-(ty.Giai_tri||0))/60);
 $('r-cmp').textContent=`So với hôm trước: học ${d>=0?'+':''}${d} phút · giải trí ${g>=0?'+':''}${g} phút`;
 const H=r.hours||{};let mx=1;for(let h=0;h<24;h++){const o=H[String(h).padStart(2,'0')]||{};mx=Math.max(mx,(o.Hoc_tap||0)+(o.Giai_tri||0)+(o.Khac||0));}
 let bars='',labs='';for(let h=6;h<24;h++){const o=H[String(h).padStart(2,'0')]||{};
  bars+=`<div title="${h}h">${['Hoc_tap','Giai_tri','Khac'].map(k=>`<span style="height:${100*(o[k]||0)/mx}%;background:${CAT[k][1]}"></span>`).join('')}</div>`;labs+=`<span>${h%3===0?h:''}</span>`;}
 $('r-hours').innerHTML=bars;$('r-hlab').innerHTML=labs;
 $('r-upd').textContent=r.updated?('Cập nhật lúc '+new Date(r.updated*1000).toLocaleTimeString('vi-VN')):'';
 $('r-apps').innerHTML=(r.items||[]).map(i=>{const c=CAT[i.category]||CAT.Khac;return `<div class="app-row"><div class="ic">${appIcon(i.app,i.title)}</div>
  <div class="nm"><div><b>${esc(i.app)}</b> <span class="tag" style="background:${c[1]}">${c[0]}</span></div><div class="mut">${esc(i.title)}</div>
  ${i.reason?`<div class="mut" style="font-size:12px">${esc(i.reason)}</div>`:''}</div><div class="mn">${fmtMin(i.seconds)}</div></div>`}).join('')||'Chưa có dữ liệu ngày này.';}
// ---------- Luật chặn theo nhóm ----------
const GROUPS={
 social:{n:'📱 Mạng xã hội',d:'Facebook, Instagram, TikTok, Threads, X',dom:['facebook.com','instagram.com','tiktok.com','threads.net','x.com','twitter.com','snapchat.com'],kw:['facebook','instagram','tiktok'],apps:[]},
 game:{n:'🎮 Game',d:'Roblox, Steam, Riot, Genshin, Minecraft, web game',dom:['poki.com','y8.com','roblox.com','friv.com','crazygames.com','miniclip.com'],kw:[],apps:['roblox','steam','epicgameslauncher','riotclient','valorant','leagueclient','genshin','minecraft','garena','fc_online']},
 shortvideo:{n:'🎬 Video ngắn & phim',d:'YouTube Shorts, Netflix, web phim',dom:['netflix.com','fptplay.vn','tv360.vn','phimmoichill.net','motchilli.vip'],kw:['shorts','phim'],apps:[]},
 shopping:{n:'🛒 Mua sắm',d:'Shopee, Lazada, Tiki, Temu',dom:['shopee.vn','lazada.vn','tiki.vn','sendo.vn','temu.com'],kw:['shopee'],apps:[]},
 chat:{n:'💬 Chat & Discord',d:'Discord, Messenger web',dom:['discord.com','messenger.com'],kw:['discord'],apps:['discord']},
 comics:{n:'📚 Truyện tranh',d:'Các web truyện tranh phổ biến',dom:['truyenqqto.com','nettruyenviet.com','blogtruyenmoi.com'],kw:['truyện tranh','manga'],apps:[]}};
let rulesTouched=false;
const YT_DEFAULT=['free fire','liên quân','minecraft','roblox','pubg','genshin','gameplay','game','mukbang','prank','asmr','tiktok','review phim','phim','anime','nhạc remix','livestream game'];
function nvMode(b){document.querySelectorAll('#nv-mode button').forEach(x=>x.classList.toggle('act',x===b));rulesTouched=true;}
const KCOL={ai_game:'#dc2626',game:'#dc2626',social:'#db2777',video:'#ea580c',shopping:'#ca8a04',comics:'#9333ea',study:'#16a34a',ai:'#2563eb',app:'#64748b',other:'#94a3b8'};
function renderBox(v){const b=((v.status||{}).phone_box)||{};const dis=!b.enabled||!b.connected||!v.online;const v2=(b.proto||1)>=2;
 document.querySelectorAll('.boxbtn,.box2btn').forEach(x=>{x.disabled=dis;x.style.opacity=dis?.5:1;});
 $('box1').style.display=$('box1note').style.display=v2?'none':'';$('box2').style.display=$('box2note').style.display=v2?'':'none';
 $('boxtitle').textContent=v2?'📦 Tủ điện thoại & tưới cây (ESP32)':'📦 Hộp điện thoại';
 if(!v.online){$('boxst').innerHTML='⚪ Máy học sinh đang ngoại tuyến';return;}
 if(!b.enabled){$('boxst').innerHTML='⚪ Chưa bật hộp trên máy — vào app: <b>Admin → 📦 Hộp điện thoại</b> → Bật';return;}
 if(!b.connected){$('boxst').innerHTML='🔌 <b>Mất kết nối hộp</b> — kiểm tra cáp USB';return;}
 if(v2){const ph=b.phase==='active'?`📚 <b>Đang trong ca học</b> · còn ${Math.ceil((b.left_s||0)/60)} phút${b.viol?' · ⚠️ '+b.viol+' vi phạm':''}`:b.phase==='done'?'✅ <b>Hết giờ học</b> — lấy điện thoại không còn bị báo':'⏸️ <b>Chờ bắt đầu</b> (đóng cửa tủ + cắm sạc)';
  $('boxst').innerHTML=`${ph}<br>${b.door==='closed'?'🚪 cửa đóng':'🚪 cửa mở'} · ${b.phone?'🔌 điện thoại đang sạc':'📭 chưa cắm sạc'} · ${b.paired?(b.verified?'🔑 đã xác thực điện thoại':'🔑 đã ghép điện thoại'):'🔑 chưa ghép điện thoại'} · ca ${Math.round((b.session_s||0)/60)} phút`
   +(b.key_fp?`<br>📱 Mã ghép điện thoại: <b style="font-size:16px;letter-spacing:1px">${fmtCode(b.key_fp)}</b> <span class="mut">(so với mã trên app của con)</span>`:'')
   +(b.ble&&b.phone&&b.paired&&!b.verified&&b.phase==='idle'?`<br>⚠️ <b>Điện thoại đang kết nối nhưng CHƯA xác thực</b> nên tủ chưa vào ca — thường do app mới / cài lại app nên khóa khác khóa tủ đã lưu${b.key_fp?' (mã '+fmtCode(b.key_fp)+')':''}. Bấm <b>📱 Ghép điện thoại mới</b>.`:'')
   +(b.pair_left>0?`<br>⏳ <b>Đang chờ điện thoại ghép</b>${b.pair_expect?' mã '+fmtCode(b.pair_expect):''} — còn ${Math.floor(b.pair_left/60)}:${String(b.pair_left%60).padStart(2,'0')}. Trên app bấm “Liên kết lại tủ”.`:'');return;}
 $('boxst').innerHTML=`${b.phone?'📱 <b>Điện thoại đang trong hộp</b>':'📭 <b>Hộp trống</b>'} · ${b.lock==='locked'?'🔒 đang khóa':'🔓 đang mở'} · nắp ${b.lid==='closed'?'đóng':'mở'}${b.unlock_left>0?' · tự khóa sau '+Math.ceil(b.unlock_left/60)+' phút':''}`;}
async function boxCmd(t){if(confirm(t==='box_unlock_30'?'Mở hộp điện thoại 30 phút rồi tự khóa lại?':'Mở hộp điện thoại cho tới khi bạn khóa lại?'))cmd(t);}
async function boxMin(m){const v=cur();if(!v||!confirm(`Mở hộp điện thoại ${m} phút rồi tự khóa lại?`))return;
 const r=await api(`/api/parent/devices/${v.id}/command`,{type:'box_unlock_min',minutes:m});toast(r.message||r.error||'');}
async function boxPair(){const v=cur();if(!v)return;
 const c=prompt('GHÉP ĐIỆN THOẠI MỚI VỚI TỦ\\n\\nNhập MÃ GHÉP 6 SỐ hiện trên app FocusDesk của con (để trống = nhận điện thoại đầu tiên kết nối).\\nSau đó trong 2 phút, trên điện thoại bấm “Liên kết lại tủ”.\\nKhóa điện thoại cũ sẽ bị xóa; không làm được khi đang trong ca học.','');
 if(c===null)return;const code=c.replace(/[^0-9]/g,'');if(code&&code.length!==6){toast('Mã ghép phải đủ 6 chữ số');return;}
 const r=await api(`/api/parent/devices/${v.id}/command`,{type:'box_reset_key',code});toast(r.message||r.error||'');}
function fmtCode(c){return c&&c.length===6?c.slice(0,3)+' '+c.slice(3):(c||'');}
function boxMinAsk(){const m=parseInt(prompt('Mở hộp điện thoại bao nhiêu phút? (1–240)','45')||'0',10);if(m>0&&m<=240)boxMin(m);}
function renderAI(v){const s=v.status||{},src=s.ai_sources||[],run=s.ai_running||[];const tot=Math.round((s.ai_seconds||0)/60);
 const ent=Array.isArray(src)?src.map(x=>[x.name||'',x.seconds||0]):Object.entries(src);
 const rows=ent.sort((a,b)=>b[1]-a[1]).slice(0,6).map(([k,sec])=>`<div class="app-row"><div class="nm"><b>${esc(k)}</b></div><span class="tag" style="background:${k.endsWith('(web)')?'#2563eb':'#7c3aed'}">${Math.max(1,Math.round(sec/60))}′</span></div>`).join('');
 $('aibox').innerHTML=`<div><b>${tot}</b> / ${s.max_ai_minutes||'?'} phút cho phép</div>`+(rows||'<div class="mut">Chưa dùng AI hôm nay.</div>')+
  (run.length?`<div class="mut" style="margin-top:6px">Ứng dụng AI đang chạy trên máy: ${run.map(esc).join(', ')}</div>`:'');}
let EVS=[],EV_SHOW=6;
function renderEvents(v){const ev=v.events||[];EVS=ev;if(!ev.length){$('evbox').textContent='Chưa có thay đổi nào.';return;}
 $('evbox').innerHTML=ev.slice(0,EV_SHOW).map(e=>{const tm=new Date((e.t||0)*1000).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'});
  const name=e.kind==='web'?e.domain:e.exe;const ic=e.kind==='web'?({game:'🎮',social:'💬',video:'🎬',shopping:'🛒',comics:'📚',study:'📘',ai:'🤖',ai_game:'🎮'}[e.cls]||'🌐'):'🪟';
  const risky=['game','social','video','shopping','comics'].includes(e.cls);
  const i=ev.indexOf(e);
  const btn=e.kind==='web'&&e.safe&&!e.blocked?`<span class="pill" style="background:#dcfce7;color:#166534" title="Từ khóa an toàn">✅ an toàn · ${esc(e.safe)}</span>`:e.kind==='web'?(e.blocked?'<span class="pill" style="background:#fee2e2;color:#991b1b">⛔ đã chặn</span>':'<span class="pill">chưa chặn</span>'):'';
  return `<div class="app-row"><div class="ic">${ic}</div><div class="nm"><div><b>${esc(name||'')}</b> <span class="tag" style="background:${KCOL[e.cls]||KCOL.other}">${esc(e.vi||'')}</span></div>
   <div class="mut sub2">${tm}${e.title?' · '+esc(ct(e.title)):''}${e.why?' · '+esc(e.why):''}</div></div><div class="acts2">${btn}</div></div>`;}).join('')
  +(ev.length>EV_SHOW?`<div class="ev-more"><a href="#" onclick="event.preventDefault();EV_SHOW=25;renderEvents(cur())">Xem thêm ${Math.min(ev.length,25)-EV_SHOW} mục</a></div>`:'');}
async function blockPage(i){const v=cur(),e=EVS[i];if(!v||!e)return;if(!confirm('Chỉ chặn link này: '+(e.title||e.domain)+' ?'))return;
 const r=await api(`/api/parent/devices/${v.id}/block`,{domain:e.domain,scope:'page',title:e.title||'',page:e.page||''});toast(r.message||r.error);load();}
async function unblockPage(i){const v=cur();if(!v)return;const r=await api(`/api/parent/devices/${v.id}/unblock_page`,{index:i});toast(r.message||r.error);load();}
function pageLink(p){const d=p.d||'mọi web',u=p.u||'';let href='';
 if(u.startsWith('v='))href='https://www.youtube.com/watch?'+u;else if(u.startsWith('shorts/'))href='https://www.youtube.com/'+u;
 else if(u.startsWith('search='))href='https://www.youtube.com/results?search_query='+encodeURIComponent(u.slice(7));
 else if(u.startsWith('q='))href='https://www.google.com/search?q='+encodeURIComponent(u.slice(2));
 else if(u.startsWith('/')&&p.d)href='https://'+p.d+u;
 return href?`<a href="${esc(href)}" target="_blank" rel="noopener">${esc(d)} · ${esc(u.length>40?u.slice(0,40)+'…':u)}</a>`:esc(d)+(u?' · '+esc(u):' · theo tiêu đề');}
function renderPages(v){const ps=((v.rules||{}).blocked_pages)||[];const el=$('pagebox');if(!el)return;
 el.innerHTML=ps.length?ps.slice(0,50).map((p,i)=>`<div class="app-row"><div class="ic">🔗</div><div class="nm"><div><b>${esc(ct(p.title||p.t||p.u||''))}</b></div><div class="mut">${pageLink(p)}${p.why?' · '+esc(p.why):''}</div></div><button style="padding:4px 10px;font-size:12px" onclick="unblockPage(${i})">Bỏ chặn</button></div>`).join(''):'<span class="mut">Chưa chặn link riêng nào. Bấm “Chặn link” ở mục Thay đổi gần đây.</span>';}
// ---------- Ca học thành công · phần thưởng · LMS ----------
let STUDY=null;const VIOL_VI={GAME:'game/giải trí',AI_GAME:'AI làm game',AI_OVERUSE:'quá giờ AI',PHONE:'điện thoại',POSTURE_PHONE:'cầm điện thoại'};
const hm=ts=>new Date(ts*1000).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'});
const dm=ts=>new Date(ts*1000).toLocaleDateString('vi-VN',{day:'2-digit',month:'2-digit'});
let LIVE_END=0;
function liveTick(){const el=document.getElementById('hs-cd');if(!el||!LIVE_END)return;const s=Math.max(0,Math.ceil((LIVE_END-Date.now())/1000));
 el.textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');}
setInterval(liveTick,250);
function renderLive(v){const el=$('hs-live'),s=v.status||{},b=s.phone_box||{},p=s.posture||{};const keep=LIVE_END;LIVE_END=0;
 if(!b.enabled||!b.connected||(b.proto||1)<2||!v.online){el.innerHTML='';return '';}
 const cfi=(p.camera&&typeof p.cfi==='number')?Math.round(p.cfi):null;
 const cfiTxt=cfi===null?'<span class="mut">camera chưa bật</span>':`<b style="color:${cfi>=75?'#16a34a':cfi>=50?'#ca8a04':'#dc2626'}">${cfi}%</b>`;
 if(b.phase==='active'){const rem=(typeof b.end_in==='number'?b.end_in:(b.left_s||0)+0.5)-(v.ago||0);
  const cand=Date.now()+Math.max(0,rem)*1000;LIVE_END=(keep&&Math.abs(cand-keep)<2500)?keep:cand;   // giữ mốc cũ nếu lệch ít -> không nhảy cóc
  el.innerHTML=`<div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:12px;padding:10px 12px">
   <div style="font-size:13px;color:#047857">${b.test?'🧪 CA THỬ NGHIỆM':'📚 ĐANG TRONG CA HỌC'} · ${Math.round((b.session_s||0)/60)} phút</div>
   <div style="font-size:30px;font-weight:800;color:#065f46;letter-spacing:1px">⏳ <span id="hs-cd">--:--</span></div>
   <div style="font-size:13px">🎯 Tập trung lúc này: ${cfiTxt} · ⚠️ Vi phạm: <b>${b.viol||0}</b> · đèn đỏ khi CFI dưới ngưỡng, còi khi rất thấp</div></div>`;liveTick();return 'active';}
 if(b.phase==='done'){el.innerHTML='<div style="background:#f0fdf4;border-radius:12px;padding:8px 12px;font-size:13px">✅ <b>Ca vừa kết thúc</b> — web đang chấm; đạt chuẩn thì tủ tự tưới cây. Mở rồi đóng lại cửa tủ để bắt đầu ca mới.</div>';return 'done';}
 const ok=x=>x?'✅':'⬜',items=[[s.role==='student','👩‍🎓 Con đang ở <b>chế độ Học sinh</b> trên máy tính'],[b.paired&&b.verified,'🔑 Điện thoại <b>đã ghép &amp; xác thực</b>'+(b.paired&&!b.verified&&b.ble?' <span style="color:#dc2626">(khóa khác — bấm Ghép điện thoại mới)</span>':'')],
  [b.phone,'🔌 Điện thoại <b>đang sạc</b> trong tủ'],[b.door==='closed','🚪 Cửa tủ <b>đã đóng</b>']];
 el.innerHTML=`<div style="background:#f8fafc;border:1px dashed #cbd5e1;border-radius:12px;padding:8px 12px;font-size:13px">
  <div style="margin-bottom:4px"><b>⏸️ Chưa vào ca</b> — đủ 4 điều kiện thì tủ tự bắt đầu ca <b>${Math.round((b.session_s||0)/60)} phút</b>, đếm ngược và camera AI bắt đầu tính mức tập trung:</div>
  ${items.map(([c,t])=>`<div>${ok(c)} ${t}</div>`).join('')}</div>`;return 'idle';}
function renderStudyHome(v){const st=v.study||{},l=st.last;const live=renderLive(v);$('hs-pts').textContent=st.points||0;$('hs-today').textContent=st.today_ok||0;
 $('hs-water').textContent=st.waterings||0;
 $('hs-last').innerHTML=l?`Ca gần nhất ${dm(l.start)} ${hm(l.start)}–${hm(l.end)} · ${l.minutes}′ · `+(l.success?`<b style="color:#16a34a">✅ đạt${l.watered?' · 🌱 đã tưới cây':''} · +${l.points} điểm</b>`:`<b style="color:#dc2626">chưa đạt</b>: ${esc((l.reasons||[]).join(', '))}`):(live?'Chưa có ca học nào.':'Chưa có ca học nào. Ca học = từ lúc con vào chế độ Học sinh tới lúc rời.');}
async function loadStudy(fill){const v=cur();if(!v)return;const d=await api(`/api/parent/devices/${v.id}/study?days=30`);if(d.error){toast(d.error);return;}
 STUDY=d;const st=d.stats||{},sm=d.summary||{},c=d.cfg||{};
 $('w-pts').textContent=st.points||0;$('w-water').textContent=st.waterings||0;$('w-30').textContent=(sm.d30||[0,0])[1];
 $('w-line').innerHTML=`Hôm nay <b>${(sm.today||[0,0])[1]}/${(sm.today||[0,0])[0]}</b> ca đạt · tuần này <b>${(sm.week||[0,0])[1]}/${(sm.week||[0,0])[0]}</b> · tổng <b>${st.total_success||0}</b> ca · chuỗi ${st.streak||0} ngày (dài nhất ${st.best_streak||0})`+(d.badge?` · <span class="pill on">${esc(d.badge)}</span>`:'');
 let h='';for(let i=29;i>=0;i--){const day=vnDay(-i),b=(sm.by_day||{})[day]||[0,0,0];h+=`<span class="${b[1]>=3?'s3':b[1]==2?'s2':b[1]==1?'s1':''}" title="${day}: ${b[1]}/${b[0]} ca đạt · ${b[2]}′"></span>`;}
 $('w-days').innerHTML=h;
 $('w-crit').textContent=`Ca thành công: học ≥ ${c.min_minutes} phút · vi phạm ≤ ${c.max_violations}`+(c.min_focus?` · tập trung trên ${c.min_focus}% thời gian học`:'')+(c.water_on_success?` → 🌱 tưới cây ${c.water_seconds} giây`:' (đang tắt tưới cây)')+` · ${c.points_per_session} điểm/ca.`;
 const ss=d.sessions||[];$('w-list').innerHTML=ss.length?ss.slice(0,30).map(r=>{const nv=Object.values(r.violations||{}).reduce((a,b)=>a+b,0);
  return `<div class="sess"><span>${r.success?'✅':'❌'}</span><span><b>${dm(r.start)}</b> ${hm(r.start)}–${hm(r.end)} · ${r.minutes}′${r.focus_ratio!=null?` · 🎯 ${r.focus_ratio}% thời gian`:(r.focus!=null?` · 🎯 ${r.focus}%`:'')}${r.watered?' · 🌱':''}${nv?` · ⚠️ ${nv}`:''}</span>
   <span>${r.success?`<b style="color:#b45309">+${r.points}</b>`:''}</span>${r.success?'':`<span class="why">Chưa đạt: ${esc((r.reasons||[]).join(' · '))}</span>`}</div>`;}).join(''):'Chưa có ca học nào. Ca học được ghi khi con vào chế độ Học sinh rồi rời chế độ đó (về màn hình khóa, phụ huynh đăng nhập, tắt máy…).';
 const pts=st.points||0;$('w-rewards').innerHTML=(c.rewards||[]).map((r,i)=>`<div class="rw"><span>🎁</span><span class="nm"><b>${esc(r.name)}</b><br><span class="mut">${r.points} điểm</span></span>
  <button ${pts>=r.points?'class="pri" style="background:var(--acc);color:#fff"':'disabled'} onclick="redeem(${i})">${pts>=r.points?'Đổi':'Thiếu '+(r.points-pts)}</button></div>`).join('')||'<span class="mut">Chưa có phần thưởng — thêm ở thẻ Tiêu chí bên dưới.</span>';
 $('w-redeemed').innerHTML=(st.redeemed||[]).length?'Đã đổi: '+st.redeemed.slice(0,5).map(x=>`${dm(x.ts)} ${esc(x.name)} (−${x.points})`).join(' · '):'';
 renderLms(d.lms||{});
 if(fill){$('s-min').value=c.min_minutes;$('s-viol').value=c.max_violations;$('s-focus').value=c.min_focus;$('s-pts').value=c.points_per_session;
  $('s-blocks').value=c.max_blocks;$('s-led').value=c.led_cfi;$('s-buzz').value=c.buzz_cfi;$('s-wsec').value=c.water_seconds;$('s-water').checked=!!c.water_on_success;$('s-name').value=c.student_name||'';$('s-class').value=c.class_name||'';$('s-code').value=c.student_code||'';
  $('s-school').value=c.school||'';$('s-hook').value=c.webhook_url||'';$('s-rewards').value=(c.rewards||[]).map(r=>r.name+' | '+r.points).join(String.fromCharCode(10));}}
function renderLms(l){const el=$('w-lms');if(!l.token){el.innerHTML='<button class="pri" style="background:var(--acc);color:#fff" onclick="lms(&quot;create&quot;)">🔗 Tạo link cho giáo viên</button>';return;}
 const f=`=IMPORTDATA("${l.csv}?only=success")`;
 el.innerHTML=`<div class="mut" style="font-size:12.5px">📄 Trang tuyên dương (in / lưu PDF):</div><a class="lmsurl" href="${esc(l.page)}" target="_blank">${esc(l.page)}</a>
  <div class="mut" style="font-size:12.5px">📊 Google Sheets — dán vào 1 ô:</div><code class="lmsurl">${esc(f)}</code>
  <div class="mut" style="font-size:12.5px">⬇️ CSV (Excel / Moodle / K12Online) · JSON (phần mềm của trường):</div><span class="lmsurl"><a href="${esc(l.csv)}" target="_blank">sessions.csv</a> · <a href="${esc(l.json)}" target="_blank">summary (JSON)</a></span>
  <div class="row" style="gap:6px;flex-wrap:wrap"><button onclick="copyTxt('${esc(l.page)}')">📋 Chép link trang</button><button onclick="copyTxt(${esc(JSON.stringify(f))})">📋 Chép công thức Sheets</button>
  <button onclick="lms('create')">🔄 Đổi link</button><button class="danger" onclick="lms('revoke')">Thu hồi</button></div>`;}
async function copyTxt(t){try{await navigator.clipboard.writeText(t);toast('Đã chép');}catch(e){prompt('Chép thủ công:',t);}}
async function lms(a){const v=cur();if(!v)return;if(a==='revoke'&&!confirm('Thu hồi link? Giáo viên sẽ không xem được nữa.'))return;
 if(a==='create'&&STUDY&&(STUDY.lms||{}).token&&!confirm('Tạo link mới? Link cũ sẽ không dùng được nữa.'))return;
 const r=await api(`/api/parent/devices/${v.id}/lms`,{action:a});toast(r.message||r.error);loadStudy(false);}
async function redeem(i){const v=cur(),r0=((STUDY||{}).cfg||{}).rewards[i];if(!v||!r0||!confirm(`Đổi “${r0.name}” (−${r0.points} điểm)?`))return;
 const r=await api(`/api/parent/devices/${v.id}/redeem`,{index:i});toast(r.message||r.error);loadStudy(false);load();}
async function saveStudy(){const v=cur();if(!v)return;const num=id=>Number($(id).value);
 const rewards=$('s-rewards').value.split(/\\r?\\n/).map(x=>x.split('|')).filter(x=>x[0].trim()).map(x=>({name:x[0].trim(),points:Number((x[1]||'').replace(/[^0-9]/g,''))||50}));
 const r=await api(`/api/parent/devices/${v.id}/study_cfg`,{min_minutes:num('s-min'),max_violations:num('s-viol'),min_focus:num('s-focus'),
  points_per_session:num('s-pts'),max_blocks:num('s-blocks'),water_on_success:$('s-water').checked,water_seconds:num('s-wsec'),led_cfi:num('s-led'),buzz_cfi:num('s-buzz'),student_name:$('s-name').value,class_name:$('s-class').value,student_code:$('s-code').value,
  school:$('s-school').value,webhook_url:$('s-hook').value.trim(),rewards});toast(r.message||r.error);loadStudy(true);}
async function waterNow(){if(confirm('Bật bơm tưới cây ngay trên máy học sinh?'))cmd('water_plant');}
function sessCsv(){const v=cur();if(v)location.href=`/api/parent/devices/${v.id}/sessions.csv?days=90`;}
const RESET_ASK={events:'Xóa lịch sử Thay đổi gần đây?',alerts:'Xóa toàn bộ cảnh báo (trên web và trên máy)?',
 report_today:'Xóa báo cáo hoạt động hôm nay?',blocked_links:'Bỏ chặn TẤT CẢ link đã chặn riêng?',
 rules_all:'Xóa TOÀN BỘ luật chặn (trang web, link, ứng dụng, từ khóa, tự chặn theo loại)? Không hoàn tác được.',
 study:'Xóa toàn bộ lịch sử ca học và đặt điểm thưởng về 0? (Giữ tiêu chí, phần thưởng, link LMS)'};
async function resetData(w){const v=cur();if(!v||!confirm(RESET_ASK[w]))return;
 const r=await api(`/api/parent/devices/${v.id}/reset`,{what:w});toast(r.message||r.error);rulesTouched=false;load();}
async function blockDom(d){const v=cur();if(!v||!confirm('Chặn TOÀN BỘ trang web '+d+' (mọi link của trang này)?'))return;const r=await api(`/api/parent/devices/${v.id}/block`,{domain:d});toast(r.message||r.error);load();}
function ytMode(b){document.querySelectorAll('#yt-mode button').forEach(x=>x.classList.toggle('act',x===b));$('yt-kw-box').style.display=b.dataset.m==='keyword'?'block':'none';rulesTouched=true;}
function ytSet(m){const b=document.querySelector(`#yt-mode button[data-m="${m}"]`)||document.querySelector('#yt-mode button');ytMode(b);}
function fillRules(force){const v=cur();if(v)renderPages(v);if(!v||(rulesTouched&&!force))return;const st=(v.status||{}),r=st.rules||{},srv=(v.rules||{});
 const sel=new Set(srv.groups||[]);const all=new Set([...(r.domains||[]),...(r.apps||[]),...(r.keywords||[])]);
 if(!srv.groups){for(const [k,g] of Object.entries(GROUPS)){if([...g.dom,...g.apps].some(x=>all.has(x)))sel.add(k);}}
 $('groups').innerHTML=Object.entries(GROUPS).map(([k,g])=>`<div class="sw"><div><div class="t">${g.n}</div><div class="d">${g.d}</div></div>
  <label class="tg"><input type="checkbox" data-g="${k}" ${sel.has(k)?'checked':''} onchange="rulesTouched=true"><span></span></label></div>`).join('');
 const inG=new Set();for(const k of sel){const g=GROUPS[k];[...g.dom,...g.kw,...g.apps].forEach(x=>inG.add(x));}
 $('x-apps').value=(r.apps||[]).filter(x=>!inG.has(x)).join(', ');$('x-dom').value=(r.domains||[]).filter(x=>!inG.has(x)).join(', ');
 $('x-kw').value=(r.keywords||[]).filter(x=>!inG.has(x)).join(', ');
 $('opt-always').checked=!!(srv.monitor_always??st.monitor_always);$('opt-dns').checked=srv.dns_block!==false;
 const legacyYt=(srv.groups||[]).includes('youtube');ytSet(srv.youtube_mode||r.youtube_mode||(legacyYt?'block':'keyword'));
 const ykw=(srv.youtube_keywords&&srv.youtube_keywords.length?srv.youtube_keywords:(r.youtube_keywords&&r.youtube_keywords.length?r.youtube_keywords:YT_DEFAULT));
 $('yt-kw').value=ykw.join(', ');
 const nb=document.querySelector(`#nv-mode button[data-m="${srv.notify_env||'risky'}"]`);if(nb)nvMode(nb);const bks=new Set(srv.block_kinds||[]);if(srv.auto_block_games)bks.add('game');document.querySelectorAll('#bk [data-k]').forEach(i=>i.checked=bks.has(i.dataset.k));
 $('ai-min').value=(srv.max_ai_minutes??st.max_ai_minutes??15);$('safe-kw').value=(srv.safe_keywords||[]).join(', ');segSet('ai-over',srv.ai_over_action||'block');segSet('ai-game',srv.ai_game_action||'block');$('yt-shorts').checked=(srv.youtube_shorts??r.youtube_shorts)!==false;rulesTouched=false;
 $('rmsg').textContent=st.dns_block?('Trên máy: '+st.dns_block):'';}
async function saveRules(){const v=cur();if(!v)return;const sp=id=>$(id).value.split(',').map(x=>x.trim().toLowerCase()).filter(Boolean);
 const groups=[...document.querySelectorAll('[data-g]')].filter(i=>i.checked).map(i=>i.dataset.g);
 const apps=new Set(sp('x-apps')),dom=new Set(sp('x-dom')),kw=new Set(sp('x-kw'));
 for(const k of groups){const g=GROUPS[k];g.apps.forEach(x=>apps.add(x));g.dom.forEach(x=>dom.add(x));g.kw.forEach(x=>kw.add(x));}
 const ym=(document.querySelector('#yt-mode button.act')||{}).dataset?.m||'keyword';
 ['youtube.com','youtu.be'].forEach(x=>{if(ym!=='block')dom.delete(x);else dom.add(x);});kw.delete('youtube');
 const r=await api(`/api/parent/devices/${v.id}/rules`,{apps:[...apps],domains:[...dom],keywords:[...kw],groups,
  monitor_always:$('opt-always').checked,dns_block:$('opt-dns').checked,youtube_mode:ym,youtube_keywords:sp('yt-kw'),
  youtube_shorts:$('yt-shorts').checked,notify_env:(document.querySelector('#nv-mode button.act')||{}).dataset?.m||'risky',
  block_kinds:[...document.querySelectorAll('#bk [data-k]')].filter(i=>i.checked).map(i=>i.dataset.k),safe_keywords:sp('safe-kw'),
  auto_block_games:!!document.querySelector('#bk [data-k="game"]:checked'),max_ai_minutes:parseInt($('ai-min').value||'15',10),
  ai_over_action:segGet('ai-over','block'),ai_game_action:segGet('ai-game','block')});$('rmsg').textContent=r.message||r.error;toast(r.message||r.error);rulesTouched=false;}
// ---------- PWA ----------
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
let deferredPrompt=null;addEventListener('beforeinstallprompt',e=>{e.preventDefault();deferredPrompt=e;$('installBtn').style.display='inline-block';});
async function installApp(){if(deferredPrompt){deferredPrompt.prompt();deferredPrompt=null;$('installBtn').style.display='none';}}
$('themeBtn').textContent=document.documentElement.getAttribute('data-theme')==='dark'?'☀️ Nền sáng':'🌙 Nền tối';
const qs=new URLSearchParams(location.search);go({camera:'camera',report:'report'}[qs.get('tab')]||'home');
load();nstat();setInterval(()=>{if(!document.hidden)load();},4000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});setInterval(()=>{if(VIEW==='report'&&!document.hidden)report();},15000);setInterval(()=>{if(VIEW==='reward'&&!document.hidden)loadStudy(false);},30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
</script>"""


@app.route("/")
def index():
    return redirect("/dashboard" if current_uid() else "/login")


def _auth_page(error: str = "", code: int = 200):
    return render_template_string(AUTH_HTML, error=error, google_client_id=GOOGLE_CLIENT_ID,
                                  allow_password=ALLOW_PASSWORD_LOGIN), code


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return _auth_page()
    if not ALLOW_PASSWORD_LOGIN:
        return _auth_page("Đăng nhập bằng mật khẩu đã tắt. Hãy dùng Google.", 403)
    email = (request.form.get("email") or "").strip().lower()
    pw = request.form.get("password") or ""
    if not email_allowed(email):
        return _auth_page("Email này không được phép truy cập.", 403)
    if request.form.get("mode") == "register":
        if len(pw) < 8 or "@" not in email:
            return _auth_page("Email không hợp lệ hoặc mật khẩu dưới 8 ký tự.", 400)
        if db.get(f"emails/{fb_key(email)}"):
            return _auth_page("Email đã được đăng ký.", 400)
        uid = find_or_create_parent(email)
        db.update(f"parents/{uid}", {"pw_hash": generate_password_hash(pw)})
        start_session(uid, email)
        return redirect("/dashboard")
    uid = db.get(f"emails/{fb_key(email)}")
    parent = db.get(f"parents/{uid}") if uid else None
    if parent and parent.get("pw_hash") and check_password_hash(parent["pw_hash"], pw):
        start_session(uid, email)
        return redirect("/dashboard")
    time.sleep(0.7)
    return _auth_page("Sai email hoặc mật khẩu.", 401)


@app.route("/register")
def register():
    return redirect("/login")


@app.route("/auth/google", methods=["POST"])
def auth_google():
    """Xác minh ID token Google (chữ ký RSA của Google, audience = Client ID, hạn, issuer) rồi mở phiên."""
    if not GOOGLE_CLIENT_ID:
        return jsonify({"ok": False, "error": "Server chưa cấu hình GOOGLE_CLIENT_ID"}), 400
    token = str((request.get_json(silent=True) or {}).get("credential", ""))
    try:
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token

        info = id_token.verify_oauth2_token(token, g_requests.Request(), GOOGLE_CLIENT_ID, clock_skew_in_seconds=10)
    except Exception:
        return jsonify({"ok": False, "error": "Token Google không hợp lệ."}), 401
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return jsonify({"ok": False, "error": "Nguồn token không hợp lệ."}), 401
    email = str(info.get("email", "")).lower()
    if not email or not info.get("email_verified"):
        return jsonify({"ok": False, "error": "Email Google chưa được xác minh."}), 403
    if not email_allowed(email):
        return jsonify({"ok": False, "error": "Email này không được phép truy cập."}), 403
    uid = find_or_create_parent(email, google_sub=str(info["sub"]), name=str(info.get("name", "")))
    start_session(uid, email, name=str(info.get("name", "")), picture=str(info.get("picture", "")))
    return jsonify({"ok": True})


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")


@app.route("/dashboard")
@login_required
def dashboard():
    if not session.get("csrf"):
        session["csrf"] = secrets.token_urlsafe(16)
    return render_template_string(DASH_HTML, email=session.get("email", ""), name=session.get("name", ""),
                                  picture=session.get("picture", ""), csrf=session["csrf"],
                                  cmds=list(COMMAND_TYPES.items()), vapid=VAPID_PUBLIC)


# =====================================================================
# API phụ huynh
# =====================================================================
def _study_home(dev: Dict[str, Any]) -> Dict[str, Any]:
    st = dev.get("study_stats") or {}
    td = dev.get("study_today") or {}
    return {"points": int(st.get("points", 0) or 0), "total": int(st.get("total_success", 0) or 0),
            "streak": study.live_streak(st, vn_date(), vn_date(now() - 86400)),
            "today_ok": int(td.get("ok", 0) or 0) if td.get("d") == vn_date() else 0,
            "waterings": int(st.get("waterings", 0) or 0),
            "last": dev.get("study_last") or None}


@app.route("/api/parent/devices")
@login_required
def api_parent_devices():
    uid = current_uid()
    out = []
    for did, dev in devices_of_parent(uid):
        mark_watching(did, dev=dev)  # phụ huynh đang xem -> máy báo nhanh 5s
        ru = dev.get("rules") or {}
        if ru.get("blocked_pages") and _clean_pages(ru["blocked_pages"]) != ru["blocked_pages"]:
            # Dọn luật "chặn trang chủ YouTube" tạo nhầm (chặn cả YouTube) -> máy nhận bộ luật mới ở lần báo kế tiếp
            ru = {**ru, "blocked_pages": _clean_pages(ru["blocked_pages"]), "ver": int(time.time() * 1000)}
            db.set(f"devices/{did}/rules", ru)
            dev["rules"] = ru
        out.append({
            "id": did,
            "name": dev.get("name") or did,
            "online": is_online(dev),
            "ago": now() - int(dev.get("last_seen", 0) or 0),
            "shutdown_at": int(dev.get("shutdown_at", 0) or 0),
            "shutdown_reason": str(dev.get("shutdown_reason", ""))[:120],
            "status": dev.get("status") or {},
            "alerts": (dev.get("recent_alerts") or [])[:10],
            "events": [{**e, "blocked": bool(block_state(dev.get("rules") or {}, str(e.get("domain", "")),
                                                         str(e.get("title", "")), str(e.get("page", "")))),
                        "safe": safe_kw(dev.get("rules") or {}, str(e.get("domain", "")), str(e.get("title", "")))}
                       for e in (dev.get("events") or [])[:25] if isinstance(e, dict)],
            "acks": (dev.get("acks") or [])[:5],
            "now": live_category(dev),
            "rules": {k: v for k, v in (dev.get("rules") or {}).items() if k != "by"},
            "today": ((db.get(f"activity_sum/{did}/{vn_date()}/totals")) or {}),
            "study": _study_home(dev),
        })
    chats = list((db.get(f"parents/{uid}/chats") or {}).keys())
    return jsonify({"devices": out, "chats": chats})


@app.route("/api/parent/pair", methods=["POST"])
@login_required
def api_parent_pair():
    uid = current_uid()
    data = request.get_json(silent=True) or {}
    code = "".join(ch for ch in str(data.get("code", "")) if ch.isdigit())
    rec = db.get(f"pair_codes/{code}") if len(code) == 6 else None
    if not rec or int(rec.get("exp", 0)) < now():
        return jsonify({"error": "Mã ghép sai hoặc đã hết hạn (15 phút)."}), 400
    did = rec["did"]
    name = str(data.get("name") or "").strip()[:40]
    updates = {f"owners/{uid}": True}
    if name:
        updates["name"] = name
    db.update(f"devices/{did}", updates)
    db.set(f"parents/{uid}/devices/{did}", True)
    db.delete(f"pair_codes/{code}")
    return jsonify({"ok": True, "message": "✅ Đã ghép máy thành công!"})


@app.route("/api/parent/devices/<did>/command", methods=["POST"])
@login_required
def api_parent_command(did):
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    body = request.get_json(silent=True) or {}
    ctype = str(body.get("type", ""))
    if ctype not in COMMAND_TYPES:
        return jsonify({"error": "Lệnh không hợp lệ."}), 400
    minutes = 0
    if ctype == "box_unlock_min":
        try:
            minutes = max(1, min(240, int(body.get("minutes", 0))))
        except (TypeError, ValueError):
            return jsonify({"error": "Số phút không hợp lệ."}), 400
    code = ""
    if ctype == "box_reset_key":
        code = "".join(ch for ch in str(body.get("code", "")) if ch.isdigit())
        if code and len(code) != 6:
            return jsonify({"error": "Mã ghép phải đủ 6 chữ số (xem trên app điện thoại)."}), 400
    queue_command(did, ctype, by=uid, minutes=minutes, code=code)
    dev = db.get(f"devices/{did}") or {}
    note = "" if is_online(dev) else " (máy đang ngoại tuyến, lệnh sẽ chạy khi máy kết nối lại)"
    label = f"🔓 Mở hộp điện thoại {minutes} phút" if minutes else COMMAND_TYPES[ctype]
    if code:
        label += f" (chỉ nhận điện thoại mã {code[:3]} {code[3:]}; mở app bấm “Liên kết lại tủ” trong 2 phút)"
    return jsonify({"ok": True, "message": f"Đã gửi lệnh: {label} — máy nhận trong ≤{HEARTBEAT_S}s "
                                          f"(≤{FAST_HEARTBEAT_S}s khi đang mở trang này){note}"})


@app.route("/api/parent/devices/<did>/rules", methods=["POST"])
@login_required
def api_parent_rules(did):
    """Cấu hình chặn từ xa (app / tên miền / từ khóa) -> máy nhận ở heartbeat kế tiếp."""
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    data = request.get_json(silent=True) or {}

    def clean(v):
        return [str(x).strip().lower()[:80] for x in (v or []) if str(x).strip()][:200]

    prev = dict(db.get(f"devices/{did}/rules") or {})
    rules = {"apps": clean(data.get("apps")), "domains": clean(data.get("domains")),
             "keywords": clean(data.get("keywords")), "ver": int(time.time() * 1000), "by": uid,
             "monitor_always": bool(data.get("monitor_always", False)), "dns_block": bool(data.get("dns_block", True)),
             "groups": [str(g)[:30] for g in (data.get("groups") or [])][:20],
             "youtube_mode": data.get("youtube_mode") if data.get("youtube_mode") in ("keyword", "block", "allow") else "keyword",
             "youtube_keywords": clean(data.get("youtube_keywords")),
             "youtube_shorts": bool(data.get("youtube_shorts", True)),
             "notify_env": data.get("notify_env") if data.get("notify_env") in ("risky", "all", "off") else "risky",
             "auto_block_games": bool(data.get("auto_block_games", False)),
             "ai_over_action": data.get("ai_over_action") if data.get("ai_over_action") in ("block", "warn")
             else prev.get("ai_over_action", "block"),
             "ai_game_action": data.get("ai_game_action") if data.get("ai_game_action") in ("block", "warn", "off")
             else prev.get("ai_game_action", "block"),
             "block_kinds": [k for k in (data.get("block_kinds") if isinstance(data.get("block_kinds"), list)
                                         else prev.get("block_kinds") or []) if k in BLOCKABLE_KINDS],
             "safe_keywords": clean(data.get("safe_keywords")) if isinstance(data.get("safe_keywords"), list)
             else list(prev.get("safe_keywords") or [])}
    # Trang web cũ (mở từ trước khi cập nhật) không gửi giờ AI -> GIỮ giá trị đang có, không tự về 15 phút
    try:
        rules["max_ai_minutes"] = max(0, min(240, int(data["max_ai_minutes"])))
    except (KeyError, TypeError, ValueError):
        if "max_ai_minutes" in prev:
            rules["max_ai_minutes"] = prev["max_ai_minutes"]
    if rules["auto_block_games"] and "game" not in rules["block_kinds"]:
        rules["block_kinds"].append("game")
    # Chặn hồi tố: trang đã vào gần đây thuộc loại vừa chọn chặn -> thêm vào luật luôn
    kinds = _block_kinds(rules)
    retro = []
    rules["blocked_pages"] = _clean_pages(prev.get("blocked_pages"))
    if kinds:
        evs = db.get(f"devices/{did}/events") or []
        for ev in (list(evs.values()) if isinstance(evs, dict) else list(evs))[:40]:
            dom = str((ev or {}).get("domain", ""))
            if not dom or ev.get("cls") not in kinds or safe_kw(rules, dom, str(ev.get("title", ""))):
                continue
            if is_content_domain(dom):      # YouTube / Google…: chỉ chặn đúng video / trang đó
                if _add_page_rule(rules, dom, ev.get("title", ""), ev.get("page", ""), ev.get("why") or ev.get("vi", "")):
                    retro.append(norm_title(ev.get("title", ""))[:40] or dom)
            elif dom not in rules["domains"]:
                rules["domains"].append(dom)
                retro.append(dom)
        rules["domains"] = sorted(set(rules["domains"]))[:200]
    db.set(f"devices/{did}/rules", rules)
    mark_watching(did, force=True)
    extra = f" Đã chặn thêm {len(retro)} trang vừa vào: {', '.join(retro[:5])}." if retro else ""
    return jsonify({"ok": True, "message": "Đã lưu. Máy sẽ áp dụng trong ≤30 giây." + extra})


@app.route("/api/parent/devices/<did>/block", methods=["POST"])
@login_required
def api_parent_block(did):
    """Chặn nhanh 1 tên miền từ nhật ký thay đổi (thêm vào luật, máy áp dụng ở heartbeat kế tiếp)."""
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    body = request.get_json(silent=True) or {}
    dom = str(body.get("domain", "")).strip().lower()[:80]
    rules = dict(db.get(f"devices/{did}/rules") or {})
    if body.get("scope") == "app":
        exe = str(body.get("exe", "")).strip().lower().split("\\")[-1][:60]
        if not exe.endswith(".exe") or exe in NEVER_BLOCK_APPS:
            return jsonify({"error": "Không chặn được ứng dụng này (trình duyệt / hệ thống). Hãy chặn từng link."}), 400
        rules["apps"] = sorted(set(rules.get("apps") or []) | {exe})[:200]
        rules["ver"], rules["by"] = int(time.time() * 1000), uid
        db.set(f"devices/{did}/rules", rules)
        mark_watching(did, force=True)
        return jsonify({"ok": True, "message": f"Đã chặn ứng dụng {exe}: máy tự tắt khi học sinh mở (chế độ học sinh)."})
    if body.get("scope") == "page" and not dom and len(norm_title(str(body.get("title", "")))) >= 12:
        dom = "*"                       # tab nền: chỉ biết tiêu đề -> chặn theo tiêu đề trên mọi web
    if not dom or (dom != "*" and ("." not in dom or "/" in dom or " " in dom)):
        return jsonify({"error": "Tên miền không hợp lệ"}), 400
    if body.get("scope") == "page":
        title = str(body.get("title", ""))[:160]
        if is_generic_page(dom, title, str(body.get("page", ""))):
            return jsonify({"error": f"Đây là trang chủ {dom} — chặn nó sẽ chặn cả {dom}. Hãy bấm Chặn link ở từng "
                                     "video / từ khóa tìm kiếm, hoặc dùng “chặn cả web” nếu muốn chặn hẳn."}), 400
        if not (norm_title(title) or body.get("page")):
            return jsonify({"error": "Trang này không có tiêu đề để chặn riêng — hãy chặn cả trang web."}), 400
        _add_page_rule(rules, "" if dom == "*" else dom, title, str(body.get("page", "")), "phụ huynh chặn")
        rules["ver"] = int(time.time() * 1000)
        rules["by"] = uid
        db.set(f"devices/{did}/rules", rules)
        mark_watching(did, force=True)
        where = "" if dom == "*" else f" (các trang khác của {dom} vẫn vào được)"
        return jsonify({"ok": True, "message": f"Đã chặn riêng link này{where}. "
                                               "Máy đóng tab khi học sinh đang mở / mở lại link này."})
    rules["domains"] = sorted(set(rules.get("domains") or []) | {dom})[:200]
    note = ""
    if _is_youtube(dom):
        # YouTube có chế độ riêng (chặn theo từ khóa để vẫn xem được bài giảng) -> phải chuyển sang "chặn hẳn"
        rules["youtube_mode"] = "block"
        rules["domains"] = sorted(set(rules["domains"]) | {"youtube.com", "youtu.be"})[:200]
        note = " Đã chuyển YouTube sang chế độ CHẶN HẲN (đổi lại ở Luật chặn → YouTube)."
    rules["ver"] = int(time.time() * 1000)
    rules["by"] = uid
    db.set(f"devices/{did}/rules", rules)
    mark_watching(did, force=True)
    dev = db.get(f"devices/{did}") or {}
    st = dev.get("status") or {}
    if not st.get("monitoring") and not rules.get("monitor_always"):
        note += (" Lưu ý: máy đang KHÔNG ở chế độ học sinh (phụ huynh/admin dùng máy tự do) — luật sẽ áp dụng ngay khi"
                 " học sinh vào học.")
    return jsonify({"ok": True, "message": f"Đã chặn {dom}. Máy áp dụng trong vài giây.{note}"})


RESET_WHAT = {
    "events": "Đã xóa lịch sử Thay đổi gần đây",
    "alerts": "Đã xóa cảnh báo (trên web và trên máy)",
    "report_today": "Đã xóa báo cáo hoạt động hôm nay",
    "blocked_links": "Đã bỏ chặn tất cả link riêng",
    "rules_all": "Đã xóa toàn bộ luật chặn (web, link, ứng dụng, từ khóa, tự chặn theo loại)",
    "study": "Đã xóa lịch sử ca học và đặt điểm thưởng về 0 (giữ tiêu chí, phần thưởng, link LMS)",
}


@app.route("/api/parent/devices/<did>/reset", methods=["POST"])
@login_required
def api_parent_reset(did):
    """Đặt lại dữ liệu để làm lại từ đầu (trước đây chỉ làm được trên máy học sinh)."""
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    what = str((request.get_json(silent=True) or {}).get("what", ""))
    if what not in RESET_WHAT:
        return jsonify({"error": "Không rõ cần đặt lại gì."}), 400
    if what == "events":
        db.delete(f"devices/{did}/events")
        db.delete(f"events/{did}/{vn_date()}")
    elif what == "alerts":
        db.delete(f"devices/{did}/recent_alerts")
        db.delete(f"devices/{did}/acks")
        queue_command(did, "reset_stats", by=uid)
    elif what == "study":
        db.delete(f"sessions/{did}")
        db.delete(f"devices/{did}/study_stats")
    elif what == "report_today":
        db.delete(f"activity_sum/{did}/{vn_date()}")
        db.delete(f"activity/{did}/{vn_date()}")
        queue_command(did, "reset_stats", by=uid)
    else:
        rules = dict(db.get(f"devices/{did}/rules") or {})
        rules["blocked_pages"] = []
        if what == "rules_all":
            rules.update(domains=[], apps=[], keywords=[], groups=[], block_kinds=[], auto_block_games=False,
                         youtube_mode="keyword")
        rules["ver"], rules["by"] = int(time.time() * 1000), uid
        db.set(f"devices/{did}/rules", rules)
    mark_watching(did, force=True)
    return jsonify({"ok": True, "message": RESET_WHAT[what] + ". Máy áp dụng trong vài giây."})


@app.route("/api/parent/devices/<did>/unblock_page", methods=["POST"])
@login_required
def api_parent_unblock_page(did):
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    i = (request.get_json(silent=True) or {}).get("index")
    rules = dict(db.get(f"devices/{did}/rules") or {})
    pages = [p for p in (rules.get("blocked_pages") or []) if isinstance(p, dict)]
    try:
        removed = pages.pop(int(i))
    except (TypeError, ValueError, IndexError):
        return jsonify({"error": "Không tìm thấy link."}), 400
    rules["blocked_pages"] = pages
    rules["ver"] = int(time.time() * 1000)
    db.set(f"devices/{did}/rules", rules)
    mark_watching(did, force=True)
    return jsonify({"ok": True, "message": f"Đã bỏ chặn: {removed.get('title') or removed.get('t') or removed.get('u')}"})


@app.route("/api/parent/devices/<did>/activity")
@login_required
def api_parent_activity(did):
    """Báo cáo hoạt động 1 ngày: tổng giây theo nhóm + danh sách cửa sổ gộp (giống DuAnAI)."""
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    date = "".join(ch for ch in request.args.get("date", "") if ch.isdigit() or ch == "-")[:10] or vn_date()
    summ = db.get(f"activity_sum/{did}/{date}")
    if summ:
        items = sorted((summ.get("apps") or {}).values(), key=lambda x: -int(x.get("seconds", 0)))[:40]
        return jsonify({"date": date, "totals": summ.get("totals") or {}, "hours": summ.get("hours") or {},
                        "items": items, "updated": summ.get("updated", 0)})
    batches = db.get(f"activity/{did}/{date}") or {}
    totals: Dict[str, int] = {"Hoc_tap": 0, "Giai_tri": 0, "Khac": 0}
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for b in batches.values():
        for it in b.get("items") or []:
            cat = it.get("category") if it.get("category") in totals else "Khac"
            sec = int(it.get("seconds", 0) or 0)
            totals[cat] += sec
            key = (it.get("app", ""), it.get("title", ""))
            m = merged.setdefault(key, {"app": key[0], "title": key[1], "category": cat, "seconds": 0,
                                        "reason": it.get("reason", "")})
            m["seconds"] += sec
    items = sorted(merged.values(), key=lambda x: -x["seconds"])[:40]
    return jsonify({"date": date, "totals": totals, "items": items})


@app.route("/api/parent/devices/<did>/unlink", methods=["POST"])
@login_required
def api_parent_unlink(did):
    uid, did = current_uid(), fb_key(did)
    db.delete(f"devices/{did}/owners/{uid}")
    db.delete(f"parents/{uid}/devices/{did}")
    return jsonify({"ok": True})


@app.route("/api/parent/telegram/link", methods=["POST"])
@login_required
def api_parent_tg_link():
    username = bot_username()
    if not username:
        return jsonify({"error": "Server chưa cấu hình TELEGRAM_BOT_TOKEN."}), 400
    code = secrets.token_hex(8)
    db.set(f"tg_links/{code}", {"uid": current_uid(), "exp": now() + 600})
    return jsonify({"url": f"https://t.me/{username}?start={code}"})


# =====================================================================
# Phân loại hoạt động (port từ DuAnAI: Gemini + luật dự phòng). Khóa Gemini CHỈ ở env Vercel.
# =====================================================================
def vn_date(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime((ts or time.time()) + TZ_OFFSET_S))


STUDY_HINTS = ("olm", "vietjack", "hocmai", "khan", "classroom", "classin", "zoom", "teams", "meet.google",
               "word", "excel", "powerpoint", "onenote", "acrobat", ".pdf", "docs.google", "sheets", "slides",
               "notepad", "code", "pycharm", "geogebra", "desmos", "wikipedia", "scholar", "quizlet", "duolingo",
               "bài", "toán", "vật lý", "vật lí", "hóa", "sinh", "tiếng anh", "ngữ văn", "lịch sử", "địa lý",
               "bài giảng", "luyện thi", "đề thi", "homework", "lesson", "chatgpt", "gemini", "claude")
FUN_HINTS = ("youtube", "facebook", "tiktok", "instagram", "netflix", "game", "steam", "roblox", "minecraft",
             "valorant", "league", "genshin", "liên quân", "free fire", "phim", "truyện", "manga", "anime",
             "shopee", "lazada", "discord", "spotify", "twitch", "poki", "zing mp3", "nhạc")


# ---------------- Phân tích tên miền: trang học / game / mạng xã hội / video / mua sắm / AI ----------------
DOMAIN_KINDS: Dict[str, Tuple[str, ...]] = {
    "game": ("poki.", "y8.com", "friv.", "crazygames.", "miniclip.", "roblox.", "coolmathgames.", "kizi.", "agame.",
             "armorgames.", "addictinggames.", "newgrounds.", "itch.io", "krunker.io", "slither.io", "agar.io",
             "zombs", "shellshock.io", "1v1.lol", "skribbl.io", "gartic", "chess.com", "lichess.org", "game24h.",
             "gamevui.", "vuigame.", "trochoi.", "gamebai", "garena.", "lienquan.", "steampowered.", "epicgames.",
             "minecraft.", "geometrydash", "subway-surfers", "gameplay", "playhop.", "yandex.com/games",
             "game.", "games.", "-game", "playgame", "html5games"),
    "social": ("facebook.", "fb.com", "messenger.", "instagram.", "tiktok.", "threads.net", "x.com", "twitter.",
               "snapchat.", "pinterest.", "reddit.", "discord.", "zalo.me", "chat.zalo"),
    "video": ("youtube.", "youtu.be", "netflix.", "fptplay.", "tv360.", "vieon.", "phimmoi", "motchill", "bilibili.",
              "twitch.tv", "dailymotion.", "animevietsub", "hhpanda", "anime"),
    "shopping": ("shopee.", "lazada.", "tiki.vn", "sendo.vn", "temu.", "aliexpress.", "amazon."),
    "comics": ("truyenqq", "nettruyen", "blogtruyen", "truyentranh", "manga", "webtoon"),
    "study": ("olm.vn", "vietjack.", "hocmai.", "loigiaihay.", "hoc247.", "khanacademy.", "quizlet.", "wikipedia.",
              "classroom.google", "docs.google", "drive.google", "scholar.google", "moet.gov", "vndoc.", "tuyensinh247",
              "duolingo.", "coursera.", "edx.org", "geogebra.", "desmos.", "violet.vn", "thuvienhoclieu", "azota.",
              "shub.edu", "k12online", "vnedu.", "sgk", "hoc.", "edu.vn", "w3schools.", "codelearn", "britannica."),
    "ai": ("chatgpt.com", "openai.com", "gemini.google", "claude.ai", "copilot.microsoft", "deepseek.", "perplexity.",
           "poe.com", "grok.com", "qwen."),
}
KIND_VI = {"game": "Web game", "social": "Mạng xã hội", "video": "Video / phim", "shopping": "Mua sắm",
           "comics": "Truyện tranh", "study": "Học tập", "ai": "Chatbot AI", "ai_game": "AI làm game", "other": "Khác"}
KIND_CAT = {"game": "Giai_tri", "social": "Giai_tri", "video": "Giai_tri", "shopping": "Giai_tri", "comics": "Giai_tri",
            "study": "Hoc_tap", "ai": "Hoc_tap", "ai_game": "Giai_tri", "other": "Khac"}
WEB_BUILD = "2026-11-03.18"   # đổi mỗi lần sửa web -> /api/health cho biết Vercel đã chạy bản mới chưa
RISKY_KINDS = ("game", "social", "video", "shopping", "comics", "ai_game")
BLOCKABLE_KINDS = ("game", "social", "video", "shopping", "comics")      # được tự chặn theo phân loại
YOUTUBE_DOMAINS = ("youtube.com", "youtu.be", "m.youtube.com", "www.youtube.com", "music.youtube.com")
AI_GAME_TITLE_HINTS = ("game", "trò chơi", "snake", "rắn săn mồi", "tetris", "flappy", "mario", "pacman", "2048",
                       "đua xe", "bắn súng", "minecraft")
GAME_TITLE_HINTS = ("game", "chơi", "play ", "online free", "unblocked", ".io", "level", "điểm cao", "bắn súng",
                    "đua xe", "minecraft", "roblox", "free fire", "liên quân")


def classify_domain(domain: str, title: str = "") -> Dict[str, str]:
    """
    Tên miền -> {kind, vi, category, why}. Thứ tự: danh sách dựng sẵn -> dấu hiệu tiêu đề/tên miền ->
    Gemini (nếu có khóa) -> 'other'. Kết quả lưu cache dùng chung ở domain_cat/<miền> nên mỗi trang chỉ phân tích 1 lần.
    """
    d = (domain or "").lower().strip()
    t = (title or "").lower()
    if not d:
        return {"kind": "other", "vi": KIND_VI["other"], "category": "Khac", "why": ""}
    for kind in ("study", "ai", "game", "social", "video", "shopping", "comics"):
        if any(h in d for h in DOMAIN_KINDS[kind]):
            if kind == "ai" and "game theory" not in t and any(h in t for h in AI_GAME_TITLE_HINTS):
                # Trang AI nhưng cuộc trò chuyện là làm game -> báo phụ huynh (máy học sinh tự chặn theo luật AI)
                return {"kind": "ai_game", "vi": KIND_VI["ai_game"], "category": "Giai_tri", "why": "tiêu đề"}
            return {"kind": kind, "vi": KIND_VI[kind], "category": KIND_CAT[kind], "why": "danh sách"}
    key = fb_key(d)[:120]
    cached = db.get(f"domain_cat/{key}")
    if isinstance(cached, dict) and cached.get("kind") in KIND_VI:
        return {**cached, "vi": KIND_VI[cached["kind"]], "category": KIND_CAT[cached["kind"]]}
    kind, why = "other", ""
    if d.endswith(".io") and any(h in t for h in ("game", "play", "io")) or any(h in t for h in GAME_TITLE_HINTS[:6]):
        kind, why = "game", "tiêu đề giống web game"
    if kind == "other" and GEMINI_API_KEY:
        kind, why = _gemini_domain_kind(d, title) or ("other", "")
    rec = {"kind": kind, "why": why[:80], "t": now()}
    try:
        db.set(f"domain_cat/{key}", rec)
    except Exception:
        pass
    return {**rec, "vi": KIND_VI[kind], "category": KIND_CAT[kind]}


def _gemini_domain_kind(domain: str, title: str) -> Optional[Tuple[str, str]]:
    import json as _json

    prompt = ("Một HỌC SINH vừa mở trang web. Xếp tên miền vào đúng 1 loại: study (học tập, tra cứu, tài liệu), "
              "game (chơi game online), social (mạng xã hội, chat), video (xem video/phim/nhạc), shopping, comics "
              "(truyện), ai (chatbot AI), other. Trả lời ngắn lý do tiếng Việt (≤10 từ).\n"
              f"Tên miền: {domain}\nTiêu đề tab: {title[:120]}")
    schema = {"type": "OBJECT", "properties": {"kind": {"type": "STRING", "enum": list(KIND_VI)},
                                               "reason": {"type": "STRING"}}, "required": ["kind", "reason"]}
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json", "responseSchema": schema}}
    for model in GEMINI_MODELS:
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              json=body, headers={"x-goog-api-key": GEMINI_API_KEY}, timeout=6)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            out = _json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            if out.get("kind") in KIND_VI:
                return out["kind"], "🤖 " + str(out.get("reason", ""))[:70]
        except Exception as exc:
            app.logger.warning("Gemini phân loại miền lỗi (%s): %s", model, exc)
            return None
    return None


# ---- Chặn theo TỪNG LINK (bản sao services/page_rules.py: sửa bên kia thì sửa cả ở đây) ----
CONTENT_DOMAINS = ("youtube.com", "youtu.be", "google.com", "google.com.vn", "bing.com", "coccoc.com",
                   "vimeo.com", "dailymotion.com")
GENERIC_PAGE_NAMES = ("youtube", "google", "google tìm kiếm", "bing", "cốc cốc", "coc coc", "vimeo", "dailymotion", "")
_BROWSER_SUFFIX = (" - microsoft edge", " - google chrome", " - cốc cốc", " - coc coc", " - mozilla firefox",
                   " — mozilla firefox", " - brave", " - opera", " - vivaldi")
STUDY_TITLE_HINTS = ("bài giảng", "bài ", "toán", "vật lý", "vật lí", "hóa học", "hoá học", "sinh học", "ngữ văn",
                     "tiếng anh", "lịch sử", "địa lý", "địa lí", "tin học", "lập trình", "khtn", "lớp ", "ôn thi",
                     "giải ", "luyện thi", "thpt", "thcs", "lesson", "lecture", "tutorial", "how to", "khan academy",
                     "math", "physics", "chemistry", "biology", "grammar", "học ", "kiến thức", "đề thi")
FUN_TITLE_HINTS = ("official music video", " mv", "lyric", "remix", "nhạc", "vlog", "funny", "hài", "trailer",
                   "tập ", "phim", "gameplay", "highlight", "reaction", "prank", "tiktok", "shorts", "livestream",
                   "stream", "anime", "liên quân", "free fire", "minecraft", "roblox")


def norm_title(title: str) -> str:
    t = str(title or "")
    for ch in ("\u200b", "\u200e", "\u200f", "\u202a", "\u202c"):
        t = t.replace(ch, "")
    t = t.lower().strip()
    t = re.sub(r"^\(\d+\+?\)\s*", "", t)
    t = re.sub(r"\s+and \d+ more pages?\b", "", t)
    t = re.sub(r"\s+và \d+ trang khác\b", "", t)
    for suf in _BROWSER_SUFFIX:
        i = t.find(suf)
        if i > 0:
            t = t[:i]
    return re.sub(r"\s+", " ", t).strip()[:100]


def is_content_domain(dom: str) -> bool:
    d = (dom or "").lower()
    return any(d == c or d.endswith("." + c) for c in CONTENT_DOMAINS)


def is_generic_page(dom: str, title: str, page: str = "") -> bool:
    """Trang chủ YouTube / Google… (tiêu đề chỉ là tên web, không có mã video / từ khóa) — chặn nó = chặn cả web."""
    if page or not is_content_domain(dom):
        return False
    return norm_title(title).split(" - ")[0].strip() in GENERIC_PAGE_NAMES


def _clean_pages(pages: Any) -> List[Dict[str, Any]]:
    """Bỏ luật tạo từ tiêu đề "trang chủ" ("(50) YouTube - Personal…"): hoặc là chặn cả web, hoặc là video được AI
    xếp loại khi tiêu đề CHƯA tải xong (chỉ thấy chữ "YouTube") -> không đáng tin."""
    out = []
    for p in pages or []:
        if not isinstance(p, dict):
            continue
        d = str(p.get("d", ""))
        if is_generic_page(d, str(p.get("t", ""))) or is_generic_page(d, str(p.get("title", ""))):
            continue
        out.append(p)
    return out


def classify_page(domain: str, title: str) -> Dict[str, str]:
    """Trang trên tên miền "nhiều nội dung" (YouTube, Google tìm kiếm…): phân loại THEO TIÊU ĐỀ trang (Gemini nếu có
    khóa, không thì dấu hiệu từ khóa). Lưu cache theo (miền, tiêu đề) nên mỗi video/từ khóa chỉ hỏi Gemini 1 lần."""
    d = (domain or "").lower()
    nt = norm_title(title)
    base = classify_domain(d, title)
    if not nt:
        return base
    if is_generic_page(d, title):
        # Chỉ thấy "YouTube" / "Google" (trang chủ, hoặc video chưa tải xong tiêu đề) -> chưa đủ để xếp học / giải trí
        return {"kind": "other", "vi": "Chưa rõ nội dung", "category": "Khac", "why": "trang chủ / tiêu đề chưa tải"}
    key = fb_key(d)[:60] + "|" + hashlib.sha1(nt.encode("utf-8")).hexdigest()[:16]
    cached = db.get(f"page_cat/{key}")
    if isinstance(cached, dict) and cached.get("kind") in KIND_VI:
        return {**cached, "vi": KIND_VI[cached["kind"]], "category": KIND_CAT[cached["kind"]]}
    kind, why = "", ""
    if GEMINI_API_KEY:
        got = _gemini_page_kind(d, title)
        if got:
            kind, why = got
    if not kind:
        if any(h in nt for h in STUDY_TITLE_HINTS):
            kind, why = "study", "tiêu đề giống bài học"
        elif any(h in nt for h in FUN_TITLE_HINTS):
            kind, why = ("game" if any(g in nt for g in ("gameplay", "liên quân", "free fire", "minecraft", "roblox"))
                         else "video"), "tiêu đề giống giải trí"
        else:
            kind, why = base["kind"], base.get("why", "")
    rec = {"kind": kind, "why": why[:80], "t": now()}
    try:
        db.set(f"page_cat/{key}", rec)
    except Exception:
        pass
    return {**rec, "vi": KIND_VI[kind], "category": KIND_CAT[kind]}


def _gemini_page_kind(domain: str, title: str) -> Optional[Tuple[str, str]]:
    import json as _json

    prompt = ("Một HỌC SINH đang mở 1 trang trên " + domain + ". Dựa vào TIÊU ĐỀ trang (tên video / từ khóa tìm kiếm), "
              "xếp NỘI DUNG trang vào đúng 1 loại: study (bài giảng, kiến thức, tài liệu, hướng dẫn học), "
              "video (ca nhạc, phim, giải trí, vlog, hài), game (chơi game, gameplay, stream game), social, shopping, "
              "comics, ai, other. Chỉ chọn study khi tiêu đề rõ ràng là học. Lý do tiếng Việt ≤10 từ.\n"
              f"Tiêu đề: {str(title)[:160]}")
    schema = {"type": "OBJECT", "properties": {"kind": {"type": "STRING", "enum": [k for k in KIND_VI if k != "ai_game"]},
                                               "reason": {"type": "STRING"}}, "required": ["kind", "reason"]}
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json", "responseSchema": schema}}
    for model in GEMINI_MODELS:
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              json=body, headers={"x-goog-api-key": GEMINI_API_KEY}, timeout=6)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            out = _json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            if out.get("kind") in KIND_VI:
                return out["kind"], "🤖 " + str(out.get("reason", ""))[:70]
        except Exception as exc:
            app.logger.warning("Gemini phân loại trang lỗi (%s): %s", model, exc)
            return None
    return None


def _add_page_rule(rules: Dict[str, Any], dom: str, title: str, page: str, why: str) -> bool:
    """Thêm luật chặn 1 link vào rules['blocked_pages'] (không trùng). True nếu là luật mới."""
    pages = _clean_pages(rules.get("blocked_pages"))
    nt, pk = norm_title(title), str(page or "")[:120]
    if (not nt and not pk) or is_generic_page(dom, title, pk):
        return False                    # trang chủ YouTube / Google: chặn riêng = chặn cả web -> không làm
    for p in pages:
        if p.get("d", "") == dom and ((pk and p.get("u") == pk) or (not pk and nt and p.get("t") == nt)):
            return False
    shown = str(title)[:100]
    if pk and is_generic_page(dom, title):
        # tiêu đề lúc đó chỉ là "YouTube" (chưa tải xong) -> chặn theo mã video, hiển thị bằng mã
        nt, shown = "", f"{dom} · {pk}"
    pages.insert(0, {"d": dom[:80], "t": nt, "u": pk, "why": str(why)[:60], "title": shown, "at": now()})
    rules["blocked_pages"] = pages[:300]
    return True


def safe_kw(rules: Dict[str, Any], dom: str, title: str) -> str:
    """Từ khóa an toàn (rules.safe_keywords) có trong tên miền / tiêu đề -> trang đó KHÔNG bị tự chặn."""
    text = f"{dom} {title}".lower()
    for k in rules.get("safe_keywords") or []:
        k = str(k).strip().lower()
        if k and k in text:
            return k
    return ""


def _block_kinds(rules: Dict[str, Any]) -> set:
    """Các loại trang phụ huynh chọn TỰ CHẶN (bản cũ chỉ có công tắc auto_block_games)."""
    kinds = {k for k in (rules.get("block_kinds") or []) if k in BLOCKABLE_KINDS}
    if rules.get("auto_block_games"):
        kinds.add("game")
    return kinds


def _is_youtube(dom: str) -> bool:
    return any(dom == y or dom.endswith("." + y) for y in YOUTUBE_DOMAINS)


def _handle_events(did: str, dev: Dict[str, Any], events: List[Dict[str, Any]]) -> None:
    """Sự kiện thay đổi môi trường (mở app / vào trang web mới): phân loại, lưu nhật ký, báo phụ huynh, tự chặn game."""
    rules = dict(dev.get("rules") or {})
    mode = rules.get("notify_env", "risky")          # risky | all | off
    out, notify = [], []
    blocked_new: List[str] = []
    pages_changed = False
    for ev in events[:30]:
        kind = str(ev.get("kind", ""))
        title = str(ev.get("title", ""))[:120]
        rec = {"t": int(ev.get("t", 0) or now()), "kind": kind, "exe": str(ev.get("exe", ""))[:60], "title": title}
        if kind == "web":
            dom = str(ev.get("domain", ""))[:80].lower()
            page = str(ev.get("page", ""))[:120]
            content = is_content_domain(dom)
            c = classify_page(dom, title) if content else classify_domain(dom, title)
            rec.update(domain=dom, cls=c["kind"], vi=c["vi"], category=c["category"], why=c.get("why", ""),
                       page=page, scope="page" if content else "domain",
                       page_ok=not is_generic_page(dom, title, page))
            already = any(dom.endswith(x) for x in (rules.get("domains") or []))
            rec["blocked"] = already
            safe = safe_kw(rules, dom, title)
            if safe:
                rec["safe"] = safe
            if c["kind"] in _block_kinds(rules) and not already and not safe:
                if content:
                    # YouTube / Google…: chỉ chặn ĐÚNG video / trang này, video bài giảng vẫn xem được
                    if _add_page_rule(rules, dom, title, page, c.get("why") or c["vi"]):
                        pages_changed = True
                else:
                    blocked_new.append(dom)
                rec["blocked"] = True
                rec["auto"] = True
        elif kind == "app":
            cat, _ = classify_rules(rec["exe"], title)
            rec.update(category=cat, cls="app", vi="Ứng dụng")
        else:
            continue
        out.append(rec)
        risky = rec.get("cls") in RISKY_KINDS or rec.get("category") == "Giai_tri"
        if mode == "all" or (mode == "risky" and risky and not (rec.get("blocked") and not rec.get("auto"))):
            notify.append(rec)
    if not out:
        return
    log = (list(reversed(out)) + list(dev.get("events") or []))[:40]
    upd: Dict[str, Any] = {"events": log}
    if blocked_new or pages_changed:
        rules["domains"] = sorted(set(rules.get("domains") or []) | set(blocked_new))[:200]
        rules["ver"] = int(time.time() * 1000)
        upd["rules"] = rules
    db.update(f"devices/{did}", upd)
    day = vn_date(out[-1]["t"])
    for rec in out:
        db.push(f"events/{did}/{day}", rec)
    if notify:
        chats = chats_of_device(did)
        owners = list((dev.get("owners") or {}).keys())
        name = dev.get("name") or did
        for rec in notify[:5]:
            if rec["kind"] == "web":
                icon = "🎮" if rec["cls"] == "game" else ("🚫" if rec.get("category") == "Giai_tri" else "🌐")
                msg = f"{icon} Vào trang {rec['domain']} — {rec['vi']}"
                if rec.get("auto"):
                    msg += " · ĐÃ TỰ CHẶN"
                if rec.get("title"):
                    msg += f"\n“{rec['title'][:80]}”"
            else:
                msg = f"🪟 Mở ứng dụng {rec['exe']}" + (f" — “{rec['title'][:60]}”" if rec.get("title") else "")
            for chat in chats:
                tg_send(chat, f"🖥️ <b>{html.escape(name)}</b>\n{html.escape(msg)}")
            web_push_owners(owners, f"🖥️ {name}", msg, tag=f"{did}-env")


def classify_rules(app_name: str, title: str) -> Tuple[str, str]:
    t = f"{app_name} {title}".lower()
    study = any(h in t for h in STUDY_HINTS)
    fun = any(h in t for h in FUN_HINTS)
    if fun and not study:
        return "Giai_tri", "Luật: từ khóa giải trí"
    if study:
        return "Hoc_tap", "Luật: từ khóa học tập"
    return "Khac", "Luật: không xác định"


def classify_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for it in items:
        cat, reason = classify_rules(str(it.get("exe", "")), str(it.get("title", "")))
        out.append({"app": str(it.get("exe", ""))[:60], "title": str(it.get("title", ""))[:120],
                    "seconds": int(it.get("seconds", 0) or 0), "category": cat, "reason": reason})
    if not (GEMINI_API_KEY and ACTIVITY_AI and out):
        return out
    listing = "\n".join(f"{i}. [{o['app']}] {o['title']}" for i, o in enumerate(out))
    prompt = ("Phân loại hoạt động máy tính của một HỌC SINH vào đúng 1 nhóm: Hoc_tap, Giai_tri hoặc Khac. "
              "YouTube/Facebook chỉ là Hoc_tap nếu tiêu đề rõ ràng là bài học. Trả lời tiếng Việt ngắn gọn "
              "ở trường reason (≤12 từ).\n" + listing)
    schema = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "i": {"type": "INTEGER"}, "category": {"type": "STRING", "enum": ["Hoc_tap", "Giai_tri", "Khac"]},
        "reason": {"type": "STRING"}}, "required": ["i", "category", "reason"]}}
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json", "responseSchema": schema}}
    import json as _json

    for model in GEMINI_MODELS:
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              json=body, headers={"x-goog-api-key": GEMINI_API_KEY}, timeout=7)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            for row in _json.loads(text):
                i = int(row.get("i", -1))
                if 0 <= i < len(out) and row.get("category") in ("Hoc_tap", "Giai_tri", "Khac"):
                    out[i]["category"] = row["category"]
                    out[i]["reason"] = "🤖 " + str(row.get("reason", ""))[:80]
            break
        except Exception as exc:
            app.logger.warning("Gemini lỗi (%s): %s", model, exc)
            break
    return out


def _add_to_summary(did: str, day: str, end_ts: int, items: List[Dict[str, Any]]) -> None:
    """
    Bảng tổng hợp theo ngày (nhỏ, đọc nhanh): tổng giây theo nhóm, theo giờ, theo ứng dụng.
    Dashboard chỉ đọc bảng này -> không phải tải toàn bộ lô 30s của cả ngày (tiết kiệm băng thông Firebase).
    """
    path = f"activity_sum/{did}/{day}"
    summ = db.get(path) or {}
    totals = summ.get("totals") or {"Hoc_tap": 0, "Giai_tri": 0, "Khac": 0}
    hours = summ.get("hours") or {}
    apps = summ.get("apps") or {}
    hh = time.strftime("%H", time.gmtime(end_ts + TZ_OFFSET_S))
    for it in items:
        cat = it.get("category") if it.get("category") in totals else "Khac"
        sec = int(it.get("seconds", 0) or 0)
        totals[cat] = int(totals.get(cat, 0)) + sec
        hrow = hours.get(hh) or {}
        hrow[cat] = int(hrow.get(cat, 0)) + sec
        hours[hh] = hrow
        key = fb_key(hashlib.md5(f"{it.get('app')}|{it.get('title')}".encode("utf-8")).hexdigest()[:16])
        a = apps.get(key) or {"app": it.get("app", ""), "title": it.get("title", ""), "category": cat,
                              "reason": it.get("reason", ""), "seconds": 0}
        a["seconds"] = int(a.get("seconds", 0)) + sec
        a["last"] = end_ts
        apps[key] = a
    if len(apps) > 150:  # giữ 150 mục nhiều thời gian nhất
        apps = dict(sorted(apps.items(), key=lambda kv: -int(kv[1].get("seconds", 0)))[:150])
    db.set(path, {"totals": totals, "hours": hours, "apps": apps, "updated": now()})


_FB_BAD_KEY_CHARS = str.maketrans({".": "·", "$": "_", "#": "_", "[": "(", "]": ")", "/": "∕"})


def _fb_safe(obj: Any, depth: int = 0) -> Any:
    """Firebase cấm khóa có . $ # [ ] / (vd 'chatgpt.com (web)') -> trả lỗi 400 và cả heartbeat thành 500.
    Lưới an toàn: đổi các ký tự đó trong MỌI khóa của dữ liệu máy gửi lên (giữ nguyên giá trị)."""
    if depth > 8:
        return None
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k).translate(_FB_BAD_KEY_CHARS).strip()[:200] or "_"
            out[key] = _fb_safe(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [_fb_safe(v, depth + 1) for v in obj]
    return obj


def _ai_sources_list(src: Any) -> List[Dict[str, Any]]:
    """ai_sources: bản cũ gửi dict {nguồn: giây}, bản mới gửi list [{name, seconds}] -> luôn lưu dạng list."""
    items: List[Tuple[str, int]] = []
    if isinstance(src, dict):
        items = [(str(k), v) for k, v in src.items()]
    elif isinstance(src, list):
        items = [(str(x.get("name", "")), x.get("seconds", 0)) for x in src if isinstance(x, dict)]
    out = []
    for name, sec in items:
        try:
            out.append({"name": name[:60], "seconds": int(float(sec or 0))})
        except (TypeError, ValueError):
            continue
    return sorted(out, key=lambda x: -x["seconds"])[:8]


def _ai_pairs(s: Dict[str, Any]) -> List[Tuple[str, int]]:
    return [(x["name"], x["seconds"]) for x in _ai_sources_list(s.get("ai_sources"))]


def _sanitize_open_apps(apps: List[Any]) -> List[Dict[str, Any]]:
    """Giới hạn kích thước danh sách ứng dụng/tab đang mở trước khi lưu (giữ Firebase nhẹ)."""
    out = []
    for a in apps[:12]:
        if not isinstance(a, dict):
            continue
        out.append({"exe": str(a.get("exe", ""))[:60], "focused": bool(a.get("focused")),
                    "windows": [str(w)[:90] for w in (a.get("windows") or [])[:4]],
                    "tabs": [str(t)[:90] for t in (a.get("tabs") or [])[:20]]})
    return out


BROWSER_EXES = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "coccoc.exe", "browser.exe",
                "vivaldi.exe")
NEVER_BLOCK_APPS = BROWSER_EXES + ("explorer.exe", "python.exe", "pythonw.exe", "focusdesklauncher.exe", "cmd.exe",
                                   "taskmgr.exe", "dwm.exe", "svchost.exe")


def _tab_domain(title: str) -> str:
    """Tiêu đề tab -> tên miền đoán được (tab nền chỉ có tiêu đề, không có URL)."""
    t = norm_title(title)
    for suf, dom in ((" - youtube", "youtube.com"), (" - tìm trên google", "google.com"), (" - google search", "google.com")):
        if t.endswith(suf) or (suf + " - ") in t:
            return dom
    return ""


def block_state(rules: Dict[str, Any], dom: str, title: str, page: str = "", exe: str = "") -> str:
    """'web' | 'page' | 'app' nếu đang bị chặn theo luật hiện tại, '' nếu chưa."""
    dom = (dom or "").lower()
    if exe and any(a and a in exe.lower() for a in (rules.get("apps") or [])):
        return "app"
    if dom and any(dom == d or dom.endswith("." + d) for d in (rules.get("domains") or [])):
        if not (_is_youtube(dom) and rules.get("youtube_mode", "keyword") != "block"):
            return "web"
    nt = norm_title(title)
    for p in _clean_pages(rules.get("blocked_pages")):
        pd = str(p.get("d", ""))
        if dom and pd and not (dom == pd or dom.endswith("." + pd)):
            continue
        if page and p.get("u") and p["u"] == page:
            return "page"
        if page and p.get("u"):
            continue
        pt = str(p.get("t", ""))
        if pt and nt and (pt == nt or nt.startswith(pt + " - ") or pt.startswith(nt + " - ")):
            return "page"
    return ""


def live_category(dev: Dict[str, Any]) -> Dict[str, Any]:
    """Cửa sổ đang FOCUS + danh sách ứng dụng / tab đang mở (phân loại theo luật) cho thẻ "Đang dùng gì"."""
    st = dev.get("status") or {}
    nw = st.get("now") or {}
    rules = dev.get("rules") or {}
    opened = []
    for a in st.get("open_apps") or []:
        tabs = []
        for t in a.get("tabs") or []:
            td = _tab_domain(t)
            tabs.append({"title": t, "category": classify_rules(a.get("exe", ""), t)[0], "domain": td,
                         "page_ok": len(norm_title(t)) >= 12 and not is_generic_page(td, t),
                         "blocked": block_state(rules, td, t)})
        wins = [{"title": t, "category": classify_rules(a.get("exe", ""), t)[0]} for t in a.get("windows") or []]
        cats = [x["category"] for x in tabs + wins]
        top = "Giai_tri" if "Giai_tri" in cats else ("Hoc_tap" if "Hoc_tap" in cats else "Khac")
        exe_l = str(a.get("exe", "")).lower()
        opened.append({"exe": a.get("exe", ""), "focused": bool(a.get("focused")), "windows": wins, "tabs": tabs,
                       "category": top, "app_ok": exe_l not in NEVER_BLOCK_APPS,
                       "blocked": block_state(rules, "", "", exe=exe_l)})
    if not nw or nw.get("idle"):
        return {"category": "Idle", "app": "", "title": nw.get("title", ""), "open": opened}
    cat, _ = classify_rules(str(nw.get("exe", "")), str(nw.get("title", "")))
    dom, page, title = str(nw.get("domain", "")), str(nw.get("page", "")), str(nw.get("title", ""))
    exe_l = str(nw.get("exe", "")).lower()
    return {"category": cat, "app": nw.get("exe", ""), "title": title[:160],
            "age": now() - int(nw.get("t", 0) or 0), "open": opened,
            "domain": dom, "page": page, "page_ok": bool(dom) and not is_generic_page(dom, title, page),
            "app_ok": exe_l not in NEVER_BLOCK_APPS and bool(exe_l),
            "blocked": block_state(rules, dom, title, page, exe=exe_l)}


# =====================================================================
# API cho máy học sinh (agent)
# =====================================================================
@app.route("/api/agent/register", methods=["POST"])
def api_agent_register():
    data = request.get_json(silent=True) or {}
    did = "d_" + secrets.token_hex(6)
    token = secrets.token_urlsafe(32)
    hostname = str(data.get("hostname", ""))[:60]
    db.set(f"devices/{did}", {"name": hostname or did, "token_hash": sha256(token), "created": now(),
                              "last_seen": now(), "status": {"hostname": hostname}})
    code, exp = _new_pair_code(did)
    return jsonify({"device_id": did, "device_token": token, "pair_code": code, "pair_expires": exp})


def _new_pair_code(did: str) -> Tuple[str, int]:
    for _ in range(10):
        code = new_code()
        if not db.get(f"pair_codes/{code}"):
            exp = now() + PAIR_TTL_S
            db.set(f"pair_codes/{code}", {"did": did, "exp": exp})
            return code, exp
    raise RuntimeError("Không tạo được mã ghép")


@app.route("/api/agent/pair_code", methods=["POST"])
def api_agent_pair_code():
    did, _dev = device_auth()
    if not did:
        return jsonify({"error": "unauthorized"}), 401
    code, exp = _new_pair_code(did)
    return jsonify({"pair_code": code, "pair_expires": exp})


# ---------------- Ca học thành công · điểm thưởng · LMS (logic thuần nằm ở study.py) ----------------
def study_cfg_of(dev: Dict[str, Any]) -> Dict[str, Any]:
    return study.clean_cfg(dev.get("study_cfg") or {})


def _handle_sessions(did: str, dev: Dict[str, Any], reps: List[Dict[str, Any]]) -> None:
    """Máy gửi bản tổng kết ca học -> chấm thành công, lưu, cộng điểm, báo phụ huynh, gửi webhook LMS (nếu có)."""
    cfg = study_cfg_of(dev)
    stats = dict(dev.get("study_stats") or {})
    changed = False
    watered_now = False
    for rep in sorted(reps, key=lambda r: int(r.get("start") or 0)):
        try:
            minutes = round(float(rep.get("minutes") or 0), 1)
        except (TypeError, ValueError):
            continue
        if minutes < study.MIN_RECORD_MIN or not rep.get("start"):
            continue
        key = study.session_key(rep)
        if db.get(f"sessions/{did}/{key}", shallow=True):
            continue                                     # đã nhận (máy gửi lại sau khi mất mạng)
        ok, reasons, pts = study.evaluate(rep, cfg)
        viol = {fb_key(str(k))[:20]: int(v) for k, v in (rep.get("violations") or {}).items()
                if isinstance(v, (int, float))}
        focus = rep.get("focus") if isinstance(rep.get("focus"), (int, float)) else None
        fratio = rep.get("focus_ratio") if isinstance(rep.get("focus_ratio"), (int, float)) else None
        # Tưới cây chỉ cho ca vừa kết thúc (máy gửi bù nhiều ca cũ sau khi mất mạng thì không tưới dồn), tối đa 1 lần/lượt
        water = bool(ok and cfg.get("water_on_success") and not watered_now
                     and now() - int(rep.get("end") or rep["start"]) < 1800)
        watered_now = watered_now or water
        rec = {"focus_ratio": fratio, "watered": water, "start": int(rep["start"]), "end": int(rep.get("end") or rep["start"]), "minutes": minutes,
               "break_min": round(float(rep.get("break_min") or 0), 1), "violations": viol, "focus": focus,
               "ai_min": round(float(rep.get("ai_min") or 0), 1), "end_reason": str(rep.get("end_reason", ""))[:80],
               "success": ok, "points": pts, "reasons": reasons,
               "source": "box" if rep.get("source") == "box" else "pc", "time_up": bool(rep.get("time_up")),
               "criteria": {k: cfg[k] for k in ("min_minutes", "max_violations", "min_focus")}}
        last = {k: rec.get(k) for k in ("start", "end", "minutes", "success", "points", "reasons", "focus",
                                        "focus_ratio", "watered")}
        db.set(f"sessions/{did}/{key}", dict(rec))       # bản sao: kho cục bộ/Firebase bỏ trường None
        d_end = vn_date(rec["end"])
        td = dev.get("study_today") or {}
        td = {"d": d_end, "n": (int(td.get("n", 0) or 0) if td.get("d") == d_end else 0) + 1,
              "ok": (int(td.get("ok", 0) or 0) if td.get("d") == d_end else 0) + (1 if ok else 0)}
        dev["study_today"] = td
        db.update(f"devices/{did}", {"study_last": last, "study_today": td})
        if not ok:
            continue
        day = vn_date(rec["end"])
        stats = study.update_stats(stats, day, vn_date(rec["end"] - 86400), pts, minutes)
        changed = True
        if water:   # thưởng xanh: máy học sinh bật bơm tưới cây qua mô-đun ESP32
            queue_command(did, "water_plant", by="study", ms=int(cfg["water_seconds"] * 1000))
            stats["waterings"] = int(stats.get("waterings", 0) or 0) + 1
            stats["last_water"] = rec["end"]
        name = dev.get("name") or did
        fshow = fratio if fratio is not None else focus
        msg = (f"🏆 {cfg['student_name'] or name} hoàn thành ca học {minutes:g} phút"
               + (f", tập trung {fshow:g}% thời gian" if fshow is not None else "")
               + (" · 🌱 đã tưới cây" if water else "") + f" · +{pts} điểm"
               + f" (đang có {stats['points']} điểm, chuỗi {stats.get('streak', 1)} ngày)")
        for chat in chats_of_device(did):
            tg_send(chat, html.escape(msg))
        web_push_owners(list((dev.get("owners") or {}).keys()), f"🏆 {name}", msg[:180], tag=f"{did}-study")
        if cfg.get("webhook_url"):
            try:   # LMS / Google Apps Script của trường nhận từng ca thành công
                requests.post(cfg["webhook_url"], timeout=3, json={
                    "event": "study_session_success", "student_code": cfg["student_code"],
                    "student_name": cfg["student_name"], "class_name": cfg["class_name"], "day": day,
                    "minutes": minutes, "focus": focus, "focus_ratio": fratio, "watered": water, "points": pts,
                    "total_success": stats["total_success"],
                    "streak": stats.get("streak", 1)})
            except Exception:
                pass
    if changed:
        db.set(f"devices/{did}/study_stats", stats)


def _study_records(did: str, days: int) -> Dict[str, Any]:
    since = now() - max(1, min(366, days)) * 86400
    return db.get_after(f"sessions/{did}", f"{since:010d}", limit=600)


def _study_payload(did: str, dev: Dict[str, Any], days: int = 30) -> Dict[str, Any]:
    cfg = study_cfg_of(dev)
    stats = dict(dev.get("study_stats") or {})
    today, yday = vn_date(), vn_date(now() - 86400)
    recs = _study_records(did, max(days, 30))
    wd = int(time.strftime("%w", time.gmtime(now() + TZ_OFFSET_S)))           # 0 = Chủ nhật
    week_start = vn_date(now() - ((wd - 1) % 7) * 86400)                      # thứ Hai tuần này
    summ = study.summarize(recs, lambda ts: vn_date(ts), today, week_start, vn_date(now() - 29 * 86400))
    stats["streak"] = study.live_streak(stats, today, yday)
    stats.setdefault("points", 0)
    stats.setdefault("total_success", 0)
    sessions = [dict(r, key=k) for k, r in sorted(recs.items(), reverse=True) if isinstance(r, dict)][:80]
    return {"cfg": cfg, "stats": stats, "summary": summ, "sessions": sessions,
            "badge": study.badge(summ["d30"][1])}


def _lms_urls(token: str) -> Dict[str, str]:
    base = request.host_url.rstrip("/")
    return {"json": f"{base}/api/lms/{token}/summary", "csv": f"{base}/api/lms/{token}/sessions.csv",
            "page": f"{base}/lms/{token}"}


@app.route("/api/parent/devices/<did>/study")
@login_required
def api_parent_study(did):
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    dev = db.get(f"devices/{did}") or {}
    out = _study_payload(did, dev, _int_arg("days", 30))
    tok = str(dev.get("lms_token") or "")
    out["lms"] = {"token": tok, **(_lms_urls(tok) if tok else {})}
    return jsonify(out)


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


@app.route("/api/parent/devices/<did>/study_cfg", methods=["POST"])
@login_required
def api_parent_study_cfg(did):
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    cfg = study.clean_cfg(request.get_json(silent=True) or {})
    db.set(f"devices/{did}/study_cfg", cfg)
    db.set(f"devices/{did}/study_box_ver", now())      # heartbeat so phiên bản -> gửi thời gian ca / ngưỡng CFI xuống tủ
    return jsonify({"ok": True, "cfg": cfg, "message": "Đã lưu. Áp dụng cho các ca học từ giờ trở đi."})


@app.route("/api/parent/devices/<did>/redeem", methods=["POST"])
@login_required
def api_parent_redeem(did):
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    body = request.get_json(silent=True) or {}
    dev_cfg = study.clean_cfg(db.get(f"devices/{did}/study_cfg") or {})
    stats = db.get(f"devices/{did}/study_stats") or {}
    try:
        idx = int(body.get("index", -1))
    except (TypeError, ValueError):
        idx = -1
    new, msg = study.redeem(stats, dev_cfg, idx, now())
    if new is None:
        return jsonify({"error": msg}), 400
    db.set(f"devices/{did}/study_stats", new)
    return jsonify({"ok": True, "message": msg})


@app.route("/api/parent/devices/<did>/sessions.csv")
@login_required
def api_parent_sessions_csv(did):
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    cfg = study.clean_cfg(db.get(f"devices/{did}/study_cfg") or {})
    body = study.to_csv(_study_records(did, _int_arg("days", 30)), cfg, TZ_OFFSET_S)
    return Response(body, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=ca_hoc_{vn_date()}.csv"})


@app.route("/api/parent/devices/<did>/lms", methods=["POST"])
@login_required
def api_parent_lms(did):
    """Tạo / thu hồi link công khai cho giáo viên (chỉ đọc: điểm, số ca thành công, CSV, bảng tuyên dương)."""
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    action = str((request.get_json(silent=True) or {}).get("action", "create"))
    old = str(db.get(f"devices/{did}/lms_token") or "")
    if old:
        db.delete(f"lms_tokens/{old}")
        db.delete(f"devices/{did}/lms_token")
    if action == "revoke":
        return jsonify({"ok": True, "message": "Đã thu hồi link. Link cũ không còn xem được."})
    tok = secrets.token_hex(16)
    db.set(f"lms_tokens/{tok}", {"did": did, "uid": uid, "created": now()})
    db.set(f"devices/{did}/lms_token", tok)
    return jsonify({"ok": True, "token": tok, **_lms_urls(tok),
                    "message": "Đã tạo link mới" + (" (link cũ đã bị thu hồi)." if old else ".")})


def _lms_device(token: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return None, None
    t = db.get(f"lms_tokens/{token}") or {}
    did = str(t.get("did") or "")
    if not did:
        return None, None
    return did, db.get(f"devices/{did}") or {}


def _lms_public(did: str, dev: Dict[str, Any]) -> Dict[str, Any]:
    p = _study_payload(did, dev, 30)
    cfg, st, sm = p["cfg"], p["stats"], p["summary"]
    return {"student_code": cfg["student_code"], "student_name": cfg["student_name"] or (dev.get("name") or ""),
            "class_name": cfg["class_name"], "school": cfg["school"], "generated_at": now(), "day": vn_date(),
            "criteria": {k: cfg[k] for k in ("min_minutes", "max_violations", "min_focus")},
            "points": int(st.get("points", 0) or 0), "earned_total": int(st.get("earned_total", 0) or 0),
            "total_success": int(st.get("total_success", 0) or 0), "streak": st["streak"],
            "best_streak": int(st.get("best_streak", 0) or 0),
            "success_today": sm["today"][1], "success_week": sm["week"][1], "success_30d": sm["d30"][1],
            "sessions_30d": sm["d30"][0], "minutes_30d": sm["minutes_30d"], "focus_30d": sm["focus_30d"],
            "water_30d": sm.get("water_30d", 0),
            "badge": p["badge"], "by_day": sm["by_day"],
            "days30": [[d, *(sm["by_day"].get(d) or [0, 0, 0])[:2]] for d in (vn_date(now() - i * 86400)
                                                                             for i in range(29, -1, -1))],
            "day_vi": time.strftime("%d/%m/%Y", time.gmtime(now() + TZ_OFFSET_S))}


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"      # LMS / Google Sheets đọc từ trang khác
    resp.headers["Cache-Control"] = "public, max-age=120"
    return resp


@app.route("/api/lms/<token>/summary")
def api_lms_summary(token):
    did, dev = _lms_device(token)
    if not did:
        return jsonify({"error": "Link không hợp lệ hoặc đã bị thu hồi."}), 404
    return _cors(jsonify(_lms_public(did, dev)))


@app.route("/api/lms/<token>/sessions.csv")
def api_lms_csv(token):
    did, dev = _lms_device(token)
    if not did:
        return Response("Link khong hop le hoac da bi thu hoi", status=404, mimetype="text/plain")
    recs = _study_records(did, _int_arg("days", 30))
    if request.args.get("only") == "success":
        recs = {k: r for k, r in recs.items() if isinstance(r, dict) and r.get("success")}
    return _cors(Response(study.to_csv(recs, study_cfg_of(dev), TZ_OFFSET_S), mimetype="text/csv; charset=utf-8"))


@app.route("/lms/<token>")
def lms_page(token):
    did, dev = _lms_device(token)
    if not did:
        return Response("<h3>Link không hợp lệ hoặc đã bị phụ huynh thu hồi.</h3>", status=404, mimetype="text/html")
    return render_template_string(LMS_HTML, d=_lms_public(did, dev))


@app.route("/api/agent/heartbeat", methods=["POST"])
def api_agent_heartbeat():
    did, dev = device_auth(light=True)
    if not did:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    # Heartbeat "nhẹ" (đa số): chỉ đọc vài trường nhỏ. Chỉ khi có cảnh báo / sự kiện / xác nhận lệnh / tắt máy mới đọc
    # cả hồ sơ máy — giữ băng thông Firebase Spark (10 GB tải xuống/tháng) đủ cho heartbeat dày hơn.
    heavy = bool(data.get("shutdown") or data.get("alerts") or data.get("events") or data.get("acks")
                 or data.get("sessions"))
    if heavy:
        dev = db.get(f"devices/{did}") or dev
    else:
        dev["watch_until"] = db.get(f"devices/{did}/watch_until") or 0
        dev["owners"] = db.get(f"devices/{did}/owners", shallow=True) or {}
    reps = [r for r in (data.get("sessions") or []) if isinstance(r, dict)][:30]
    if reps:
        try:
            _handle_sessions(did, dev, reps)
        except Exception as exc:
            app.logger.warning("Lỗi xử lý ca học: %s", exc)
    if data.get("shutdown"):
        # FocusDesk trên máy học sinh đang thoát: giữ trạng thái cũ, đánh dấu đã tắt + báo phụ huynh ngay
        reason = str(data.get("reason", ""))[:120]
        alerts = [_fb_safe(a) for a in (data.get("alerts") or []) if isinstance(a, dict)][:2]
        db.update(f"devices/{did}", {"last_seen": now(), "shutdown_at": now(), "shutdown_reason": reason,
                                     "recent_alerts": (alerts + list(dev.get("recent_alerts") or []))[:20]})
        if alerts:
            _notify_alerts(did, dev, alerts)
        return jsonify({"ok": True})
    status = data.get("status") or {}
    if not isinstance(status, dict):
        status = {}
    if isinstance(status.get("open_apps"), list):
        status["open_apps"] = _sanitize_open_apps(status["open_apps"])
    if "ai_sources" in status:
        status["ai_sources"] = _ai_sources_list(status["ai_sources"])
    status = _fb_safe(status)
    new_alerts = [_fb_safe(a) for a in (data.get("alerts") or []) if isinstance(a, dict)][:10]
    updates: Dict[str, Any] = {"last_seen": now(), "status": status}
    acks = [a for a in (data.get("acks") or []) if isinstance(a, dict)][:10]
    if acks:
        updates["acks"] = (list(reversed(acks)) + list(dev.get("acks") or []))[:10]
    batch = data.get("activity")
    if isinstance(batch, dict) and batch.get("items"):
        items = classify_items([i for i in batch["items"] if isinstance(i, dict)][:25])
        day = vn_date(batch.get("end"))
        db.push(f"activity/{did}/{day}",
                {"start": int(batch.get("start", 0) or 0), "end": int(batch.get("end", 0) or 0), "items": items})
        _add_to_summary(did, day, int(batch.get("end", 0) or now()), items)
    evs = [e for e in (data.get("events") or []) if isinstance(e, dict)][:30]
    if evs:
        try:
            _handle_events(did, dev, evs)
        except Exception as exc:
            app.logger.warning("Lỗi xử lý sự kiện: %s", exc)
    if new_alerts:
        recent = (new_alerts + list(dev.get("recent_alerts") or []))[:20]
        updates["recent_alerts"] = recent
        _notify_alerts(did, dev, new_alerts)
    db.update(f"devices/{did}", updates)

    cmds = db.get(f"commands/{did}") or {}
    out = []
    for cid, c in sorted(cmds.items()):
        if now() - int(c.get("ts", 0) or 0) < 24 * 3600:
            item = {"id": cid, "type": c.get("type"), "chat_id": c.get("chat_id", "")}
            if c.get("minutes"):
                item["minutes"] = int(c["minutes"])
            if c.get("ms"):
                item["ms"] = int(c["ms"])
            if c.get("code"):
                item["code"] = str(c["code"])[:6]
            out.append(item)
        db.delete(f"commands/{did}/{cid}")
    fast = int(dev.get("watch_until", 0) or 0) > now() or bool(out)
    resp: Dict[str, Any] = {"commands": out, "heartbeat_s": FAST_HEARTBEAT_S if fast else HEARTBEAT_S,
                            "paired": bool(dev.get("owners"))}
    # Luật chặn: chỉ đọc phiên bản (vài byte); khác bản máy đang có mới tải cả bộ luật
    rv = int(db.get(f"devices/{did}/rules/ver") or 0)
    if rv and rv != int(status.get("rules_ver", 0) or 0):
        resp["rules"] = db.get(f"devices/{did}/rules") or {}
    # Thời gian ca học + ngưỡng CFI cho tủ ESP32: cũng chỉ so phiên bản, khác mới đọc study_cfg
    sv = int(db.get(f"devices/{did}/study_box_ver") or 0)
    if sv and sv != int(status.get("study_box_ver", 0) or 0):
        resp["study_box"] = study.box_cfg(study.clean_cfg(db.get(f"devices/{did}/study_cfg") or {}), sv)
    return jsonify(resp)


def _notify_alerts(did: str, dev: Dict[str, Any], alerts: List[Dict[str, Any]]) -> None:
    """Cảnh báo mới -> Telegram của mọi phụ huynh + Web Push (thông báo điện thoại). Chống spam theo loại."""
    chats = chats_of_device(did)
    owners = list((dev.get("owners") or {}).keys())
    if not chats and not owners:
        return
    cooldowns = dev.get("tg_cooldown") or {}
    raw_name = dev.get("name") or did
    name = html.escape(raw_name)
    sent_types = {}
    for a in alerts:
        typ = fb_key(str(a.get("type", "GENERIC")))[:30] or "generic"
        # Nghỉ vệ sinh và bật/tắt phần mềm luôn báo (sự kiện bắt đầu / kết thúc), các loại khác cách nhau ALERT_COOLDOWN_S
        if typ.lower() not in ("break", "power") and (now() - int(cooldowns.get(typ, 0) or 0) < ALERT_COOLDOWN_S or typ in sent_types):
            continue
        sent_types[typ] = now()
        msg = f"🚨 <b>{name}</b>\n{html.escape(str(a.get('message', '')))}\n<i>{html.escape(str(a.get('time', '')))}</i>"
        for chat in chats:
            tg_send(chat, msg)
        web_push_owners(owners, f"🖥️ {raw_name}", str(a.get("message", ""))[:180], tag=f"{did}-{typ}")
    if sent_types:
        db.update(f"devices/{did}/tg_cooldown", sent_types)


def web_push_owners(uids: List[str], title: str, body: str, tag: str = "sd") -> int:
    """Gửi Web Push tới mọi trình duyệt phụ huynh đã bật thông báo. Đăng ký hết hạn (404/410) tự xóa."""
    if not (VAPID_PUBLIC and VAPID_PRIVATE):
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except Exception:
        return 0
    import json as _json

    sent = 0
    payload = _json.dumps({"title": title, "body": body, "tag": tag, "url": "/dashboard"}, ensure_ascii=False)
    for uid in uids:
        for key, rec in (db.get(f"parents/{fb_key(uid)}/push") or {}).items():
            sub = (rec or {}).get("sub")
            if not isinstance(sub, dict):
                continue
            try:
                webpush(subscription_info=sub, data=payload, vapid_private_key=VAPID_PRIVATE,
                        vapid_claims={"sub": VAPID_SUBJECT}, ttl=3600, timeout=6)
                sent += 1
            except WebPushException as exc:
                code = getattr(getattr(exc, "response", None), "status_code", 0)
                if code in (404, 410):
                    db.delete(f"parents/{fb_key(uid)}/push/{key}")
            except Exception:
                pass
    return sent


@app.route("/api/parent/push", methods=["POST"])
@login_required
def api_parent_push():
    """Lưu đăng ký Web Push của trình duyệt / app Android hiện tại."""
    uid = current_uid()
    sub = request.get_json(silent=True) or {}
    ep = str(sub.get("endpoint", ""))
    keys = sub.get("keys") or {}
    if not ep.startswith("https://") or len(ep) > 600 or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"error": "Đăng ký thông báo không hợp lệ"}), 400
    clean = {"endpoint": ep, "keys": {"p256dh": str(keys["p256dh"])[:200], "auth": str(keys["auth"])[:100]}}
    db.set(f"parents/{uid}/push/{sha256(ep)[:24]}", {"sub": clean, "t": now(),
                                                       "ua": request.headers.get("User-Agent", "")[:120]})
    return jsonify({"ok": True, "push": bool(VAPID_PUBLIC and VAPID_PRIVATE)})


@app.route("/api/parent/push/test", methods=["POST"])
@login_required
def api_parent_push_test():
    n = web_push_owners([current_uid()], "🔔 FocusDesk", "Thông báo thử: điện thoại này sẽ nhận cảnh báo.",
                        tag="test")
    return jsonify({"ok": True, "sent": n, "message": f"Đã gửi tới {n} thiết bị." if n else
                    "Chưa gửi được (máy chủ chưa có khóa VAPID hoặc thiết bị chưa bật)."})


@app.route("/api/agent/snapshot", methods=["POST"])
def api_agent_snapshot():
    did, dev = device_auth()
    if not did:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    b64 = str(data.get("jpg_b64", ""))
    if not b64 or len(b64) > 400_000:
        return jsonify({"error": "Ảnh trống hoặc quá lớn"}), 400
    # Không lưu ảnh vào Firebase: chỉ chuyển tiếp thẳng sang Telegram của phụ huynh đã yêu cầu
    chat_id = str(data.get("chat_id") or "")
    name = dev.get("name") or did
    if chat_id and chat_id in chats_of_device(did):
        tg_api("sendPhoto", chat_id=chat_id, caption=f"📸 {name}", photo_bytes=base64.b64decode(b64))
    elif data.get("broadcast"):
        # Ảnh bằng chứng tự động (Camera AI thấy cầm điện thoại/đồ chơi): giới hạn 1 ảnh / 5 phút / máy
        ts = int(time.time())
        if ts - int(dev.get("last_auto_photo", 0) or 0) < 280:
            return jsonify({"ok": True, "skipped": "rate_limited"})
        db.update(f"devices/{did}", {"last_auto_photo": ts})
        cap = f"{str(data.get('caption') or '📸 Ảnh tự động')[:200]}\n🖥️ {name}"
        photo = base64.b64decode(b64)
        for cid in chats_of_device(did):
            tg_api("sendPhoto", chat_id=cid, caption=cap, photo_bytes=photo)
    return jsonify({"ok": True})


# =====================================================================
# Telegram webhook
# =====================================================================
HELP_TG = (
    "🤖 <b>Bot FocusDesk</b>\n"
    "/may — Danh sách máy bạn quản lý\n"
    "/status [số] — Tình trạng học tập\n"
    "/lock [số] — Khóa màn hình\n"
    "/reset_ai [số] — Đặt lại giờ dùng AI\n"
    "/anh [số] — Chụp 1 ảnh camera\n"
    "/mohop [số] — Mở hộp điện thoại 30 phút · /khoahop — Khóa hộp\n"
    "[số] = số thứ tự máy trong /may (bỏ trống nếu chỉ có 1 máy)."
)


@app.route("/api/telegram/webhook", methods=["POST"])
def telegram_webhook():
    if not TG_SECRET or not hmac.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), TG_SECRET):
        return Response(status=403)
    upd = request.get_json(silent=True) or {}
    msg = upd.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    text = str(msg.get("text") or "").strip()
    if not chat_id or not text.startswith("/"):
        return jsonify({"ok": True})
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/start" and arg:
        link = db.get(f"tg_links/{fb_key(arg)}")
        if link and int(link.get("exp", 0)) >= now():
            db.set(f"parents/{link['uid']}/chats/{chat_id}", True)
            db.set(f"tg_chats/{chat_id}", link["uid"])
            db.delete(f"tg_links/{fb_key(arg)}")
            tg_send(chat_id, "✅ Đã liên kết Telegram với tài khoản phụ huynh!\n\n" + HELP_TG)
        else:
            tg_send(chat_id, "⚠️ Liên kết hết hạn. Vào web, bấm lại <b>Kết nối Telegram</b>.")
        return jsonify({"ok": True})

    uid = db.get(f"tg_chats/{chat_id}")
    if not uid:
        tg_send(chat_id, "👋 Chat này chưa liên kết. Đăng nhập web phụ huynh → <b>Kết nối Telegram</b>.")
        return jsonify({"ok": True})
    devs = devices_of_parent(uid)
    if cmd in ("/start", "/help"):
        tg_send(chat_id, HELP_TG)
        return jsonify({"ok": True})
    if cmd == "/may":
        lines = [f"{i + 1}. {'🟢' if is_online(d) else '⚪'} {html.escape(d.get('name') or did)}" for i, (did, d) in enumerate(devs)]
        tg_send(chat_id, "\n".join(lines) or "Chưa ghép máy nào.")
        return jsonify({"ok": True})

    target = _pick_device(devs, arg)
    if target is None:
        tg_send(chat_id, "Chọn máy bằng số thứ tự: dùng /may để xem danh sách." if devs else "Chưa ghép máy nào.")
        return jsonify({"ok": True})
    did, dev = target
    name = html.escape(dev.get("name") or did)
    if cmd == "/status":
        tg_send(chat_id, _status_text(name, dev))
    elif cmd in ("/lock", "/reset_ai", "/anh", "/mohop", "/khoahop"):
        ctype = {"/lock": "lock", "/reset_ai": "reset_ai", "/anh": "snapshot", "/mohop": "box_unlock_30",
                 "/khoahop": "box_lock"}[cmd]
        queue_command(did, ctype, by=uid, chat_id=chat_id)
        tg_send(chat_id, f"📨 Đã gửi lệnh <b>{COMMAND_TYPES[ctype]}</b> tới {name} (≤{HEARTBEAT_S}s).")
    if cmd in ("/status", "/lock", "/reset_ai", "/anh", "/mohop", "/khoahop"):
        mark_watching(did, force=True)
    else:
        tg_send(chat_id, HELP_TG)
    return jsonify({"ok": True})


def _pick_device(devs, arg: str):
    if not devs:
        return None
    if arg.isdigit() and 1 <= int(arg) <= len(devs):
        return devs[int(arg) - 1]
    return devs[0] if len(devs) == 1 else None


def _status_text(name: str, dev: Dict[str, Any]) -> str:
    s = dev.get("status") or {}
    p = s.get("posture") or {}
    lines = [f"📊 <b>{name}</b> — {'🟢 trực tuyến' if is_online(dev) else '⚪ ngoại tuyến'}",
             f"Vai trò: {html.escape(str(s.get('role', '?')))}"]
    if p.get("cfi") is not None:
        lines.append(f"🎯 CFI: <b>{round(float(p['cfi']))}%</b> — {html.escape(str(p.get('state_vi', '')))}")
    lines.append(f"🤖 AI (ứng dụng + web): {round((s.get('ai_seconds') or 0) / 60)}/{s.get('max_ai_minutes', '?')} phút")
    src = _ai_pairs(s)[:3]
    if src:
        lines.append("   " + ", ".join(f"{k} {max(1, round(v / 60))}′" for k, v in src))
    for a in (dev.get("recent_alerts") or [])[:3]:
        lines.append(f"• {html.escape(str(a.get('time', '')))} {html.escape(str(a.get('message', '')))}")
    return "\n".join(lines)


@app.route("/api/telegram/setup")
def telegram_setup():
    """Đăng ký webhook 1 lần sau khi deploy: mở /api/telegram/setup?key=<TELEGRAM_WEBHOOK_SECRET>."""
    if not TG_SECRET or not hmac.compare_digest(request.args.get("key", ""), TG_SECRET):
        return Response(status=403)
    url = request.url_root.replace("http://", "https://").rstrip("/") + "/api/telegram/webhook"
    res = tg_api("setWebhook", url=url, secret_token=TG_SECRET, allowed_updates=["message"])
    tg_api("setMyCommands", commands=[{"command": c, "description": d} for c, d in (
        ("may", "Danh sách máy"), ("status", "Tình trạng học tập"), ("lock", "Khóa màn hình"),
        ("reset_ai", "Đặt lại giờ AI"), ("anh", "Chụp ảnh camera"), ("help", "Hướng dẫn"))])
    return jsonify({"webhook": url, "telegram": res})


# =====================================================================
# Web App cài trên Android (PWA): manifest + service worker + trang ngoại tuyến
# =====================================================================
MANIFEST = {
    "name": "FocusDesk - Phụ huynh", "short_name": "FocusDesk", "lang": "vi",
    "description": "Theo dõi việc học, xem camera, khóa máy và nhận cảnh báo từ máy học sinh.",
    "start_url": "/dashboard?src=pwa", "scope": "/", "display": "standalone", "orientation": "portrait",
    "background_color": "#f4f6fb", "theme_color": "#2563eb",
    "icons": [{"src": "/static/icon-any-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
              {"src": "/static/favicon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
              {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
              {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    "shortcuts": [{"name": "Xem camera", "url": "/dashboard?tab=camera"},
                  {"name": "Báo cáo hôm nay", "url": "/dashboard?tab=report"}],
}

SW_JS = """
const CACHE='focusdesk-v3';
const SHELL=['/offline','/static/icon-192.png','/static/icon-512.png','/static/icon-any-512.png','/static/favicon.svg','/manifest.webmanifest'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim();});
self.addEventListener('fetch',e=>{
  const r=e.request; if(r.method!=='GET')return;
  const u=new URL(r.url); if(u.origin!==location.origin)return;
  if(u.pathname.startsWith('/static/')){e.respondWith(caches.match(r).then(m=>m||fetch(r)));return;}
  if(r.mode==='navigate'){e.respondWith(fetch(r).catch(()=>caches.match('/offline')));}
});
self.addEventListener('push',e=>{let d={};try{d=e.data.json();}catch(_){d={body:e.data?e.data.text():''};}
  e.waitUntil(self.registration.showNotification(d.title||'FocusDesk',{body:d.body||'',icon:'/static/icon-192.png',
   badge:'/static/badge-96.png',tag:d.tag||'sd',renotify:true,vibrate:[200,100,200],data:{url:d.url||'/dashboard'}}));});
self.addEventListener('notificationclick',e=>{e.notification.close();const url=(e.notification.data||{}).url||'/dashboard';
  e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>{for(const c of cs){if(c.url.includes('/dashboard'))return c.focus();}
   return clients.openWindow(url);}));});
"""

LMS_HTML = """<!doctype html><html lang="vi"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Ghi nhận học tập · {{ d.student_name }}</title>
<style>
:root{--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--acc:#b45309;--ok:#16a34a;--bg:#f8fafc;--card:#fff}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:var(--bg)}
.wrap{max-width:820px;margin:0 auto;padding:16px}
.cert{background:var(--card);border:3px double #d6a54a;border-radius:14px;padding:28px 22px;text-align:center}
.cert h1{margin:4px 0 2px;font-size:26px;letter-spacing:.5px;color:var(--acc)}.cert .sch{color:var(--mut);font-size:14px}
.name{font-size:30px;font-weight:800;margin:18px 0 4px}.cls{color:var(--mut)}
.big{font-size:56px;font-weight:800;color:var(--ok);line-height:1;margin:18px 0 4px}
.badge{display:inline-block;margin-top:10px;padding:6px 14px;border-radius:999px;background:#fef3c7;font-weight:700}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:20px 0 6px}
.grid div{border:1px solid var(--line);border-radius:10px;padding:10px}.grid b{display:block;font-size:22px}
.days{display:flex;gap:3px;justify-content:center;margin:16px 0 4px;flex-wrap:wrap}
.days span{width:16px;height:16px;border-radius:4px;background:#e2e8f0}.days span.s1{background:#86efac}.days span.s2{background:#22c55e}.days span.s3{background:#15803d}
.mut{color:var(--mut);font-size:13px}.tech{margin-top:16px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;font-size:14px}
code{background:#f1f5f9;padding:2px 6px;border-radius:6px;word-break:break-all}
button{font:inherit;padding:8px 16px;border-radius:10px;border:1px solid var(--line);background:#fff;cursor:pointer}
@media print{.noprint{display:none}body{background:#fff}.cert{border-width:4px}}
</style>
<div class="wrap">
 <div class="cert">
  <div class="sch">{{ d.school or "FocusDesk · Bàn học thông minh" }}</div>
  <h1>GHI NHẬN NỖ LỰC HỌC TẬP</h1>
  <div class="mut">30 ngày gần nhất · tính đến {{ d.day_vi }}</div>
  <div class="name">{{ d.student_name or "Học sinh" }}</div>
  <div class="cls">{% if d.class_name %}Lớp {{ d.class_name }}{% endif %}{% if d.student_code %} · Mã HS {{ d.student_code }}{% endif %}</div>
  <div class="big">{{ d.success_30d }}</div><div>ca học thành công</div>
  {% if d.badge %}<div class="badge">{{ d.badge }}</div>{% endif %}
  <div class="grid">
   <div><b>{{ d.streak }}</b>ngày liên tiếp</div><div><b>{{ d.best_streak }}</b>chuỗi dài nhất</div>
   <div><b>{{ (d.minutes_30d / 60) | round(1) }}</b>giờ học / 30 ngày</div>
   <div><b>{{ d.focus_30d if d.focus_30d is not none else "—" }}{% if d.focus_30d is not none %}%{% endif %}</b>thời gian tập trung TB</div>
   <div><b>{{ d.water_30d }}</b>lần tưới cây thưởng</div>
   <div><b>{{ d.total_success }}</b>tổng ca thành công</div><div><b>{{ d.earned_total }}</b>điểm đã đạt</div>
  </div>
  <div class="days" title="Mỗi ô = 1 ngày, màu đậm = nhiều ca thành công">
   {% for day, n, ok in d.days30 %}<span class="{{ 's3' if ok >= 3 else ('s2' if ok == 2 else ('s1' if ok == 1 else '')) }}" title="{{ day }}: {{ ok }}/{{ n }} ca thành công"></span>{% endfor %}
  </div>
  <div class="mut" style="font-size:12px">30 ngày gần nhất · ô càng đậm = càng nhiều ca thành công</div>
  <p class="mut">Ca học thành công = học liên tục ≥ {{ d.criteria.min_minutes }} phút, ≤ {{ d.criteria.max_violations }} lần vi phạm
   (mở game / web giải trí, nhờ AI làm game, dùng điện thoại…){% if d.criteria.min_focus %}, tập trung trên {{ d.criteria.min_focus }}% thời gian học (camera AI){% endif %}. Mỗi ca thành công được thưởng tưới chậu cây trên bàn học.
   Số liệu ghi tự động bởi phần mềm FocusDesk trên máy học sinh.</p>
 </div>
 <div class="tech noprint">
  <b>Dành cho giáo viên / hệ thống LMS</b>
  <p>Google Sheets: dán vào 1 ô <code>=IMPORTDATA("{{ request.url_root }}api/lms/{{ request.view_args.token }}/sessions.csv?only=success")</code></p>
  <p>CSV (Excel, Moodle, K12Online…): <a href="/api/lms/{{ request.view_args.token }}/sessions.csv?days=30">tải CSV 30 ngày</a> ·
   JSON (tích hợp phần mềm): <a href="/api/lms/{{ request.view_args.token }}/summary">/summary</a></p>
  <button onclick="print()">🖨️ In / lưu PDF</button>
 </div>
</div></html>"""


OFFLINE_HTML = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Mất kết nối</title><body style="font-family:system-ui;background:#f4f6fb;color:#0f172a;display:grid;place-items:center;min-height:90vh;margin:0">
<div style="text-align:center;padding:24px"><img src="/static/icon-192.png" width=96 style="border-radius:22px"><h2>Mất kết nối mạng</h2>
<p style="color:#64748b">Điện thoại đang offline. Ứng dụng sẽ tự tải lại khi có mạng.</p>
<button onclick="location.reload()" style="font:inherit;padding:12px 20px;border-radius:12px;border:0;background:#2563eb;color:#fff">Thử lại</button></div>
<script>addEventListener('online',()=>location.reload())</script></body>"""


@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("favicon.ico")


@app.route("/manifest.webmanifest")
def manifest():
    return Response(__import__("json").dumps(MANIFEST, ensure_ascii=False), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    return Response(SW_JS, mimetype="application/javascript", headers={"Cache-Control": "no-cache",
                                                                       "Service-Worker-Allowed": "/"})


@app.route("/offline")
def offline_page():
    return OFFLINE_HTML


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "db": "firebase" if os.environ.get("FIREBASE_DB_URL") else "local-json",
                    "telegram": bool(TG_TOKEN), "google_login": bool(GOOGLE_CLIENT_ID),
                    "password_login": ALLOW_PASSWORD_LOGIN,
                    # chỉ báo CÓ/KHÔNG để tự kiểm tra cấu hình — không bao giờ trả giá trị khóa
                    "web_push": bool(VAPID_PUBLIC and VAPID_PRIVATE), "gemini": bool(GEMINI_API_KEY),
                    "build": WEB_BUILD})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
