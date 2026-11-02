# -*- coding: utf-8 -*-
"""
Smart Study Desk Cloud (Vercel + Firebase RTDB) — cổng điều khiển từ xa cho nhiều phụ huynh / nhiều máy.

Luồng dữ liệu (máy học sinh luôn GỌI RA, không mở cổng vào):
  SmartDeskLauncher (agent)  --heartbeat 10s-->  /api/agent/heartbeat  <--lệnh--  Phụ huynh (web / Telegram)
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
import os
import secrets
import time
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import db, fb_key

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "dev-only-" + hashlib.sha256(b"smartdesk").hexdigest()
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
HEARTBEAT_S = int(os.environ.get("AGENT_HEARTBEAT_S", "30"))
FAST_HEARTBEAT_S = int(os.environ.get("AGENT_FAST_HEARTBEAT_S", "5"))
FAST_WINDOW_S = int(os.environ.get("AGENT_FAST_WINDOW_S", "120"))
ONLINE_GRACE_S = HEARTBEAT_S * 2 + 15
PAIR_TTL_S = 15 * 60
SNAPSHOT_TTL_S = 5 * 60
ALERT_COOLDOWN_S = 60
COMMAND_TYPES = {
    "lock": "🔒 Khóa màn hình",
    "reset_ai": "♻️ Đặt lại giờ dùng AI",
    "ai_on": "📷 Bật Camera AI",
    "ai_off": "⏸️ Tắt Camera AI",
    "recalibrate": "🔄 Hiệu chỉnh lại tư thế",
    "snapshot": "📸 Chụp 1 ảnh camera (gửi qua Telegram)",
    "live_on": "📺 Bật xem trực tiếp",
    "live_off": "⏹️ Tắt xem trực tiếp",
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


def device_auth() -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Xác thực máy học sinh: header Authorization: Device <device_id>:<device_token>."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Device ") or ":" not in auth:
        return None, None
    did, token = auth[7:].split(":", 1)
    did = fb_key(did)
    dev = db.get(f"devices/{did}")
    if not dev or not hmac.compare_digest(dev.get("token_hash", ""), sha256(token)):
        return None, None
    return did, dev


def is_online(dev: Dict[str, Any]) -> bool:
    return now() - int(dev.get("last_seen", 0) or 0) <= ONLINE_GRACE_S


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


def queue_command(did: str, ctype: str, by: str, chat_id: Optional[str] = None) -> str:
    mark_watching(did, force=True)
    return db.push(f"commands/{did}", {"type": ctype, "by": by, "ts": now(), "chat_id": chat_id or ""})


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
<title>Bàn Học Thông Minh - Đăng nhập</title><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#2563eb"><link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});</script>
<div style="text-align:center;margin-top:6vh"><img src="/static/icon-192.png" width="72" style="border-radius:18px" alt=""></div>
<div class="wrap" style="max-width:420px;margin-top:2vh">
 <div class="row" style="justify-content:flex-end"><button id="themeBtn" onclick="toggleTheme()">🌙 Nền tối</button></div>
 <div class="card"><h2>🛡️ Bàn Học Thông Minh</h2><p class="mut">Đăng nhập dành cho phụ huynh</p>
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
<title>Bàn Học Thông Minh</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#2563eb"><link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png"><meta name="mobile-web-app-capable" content="yes">
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
.tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;color:#fff}
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
 .cols{display:grid;grid-template-columns:1.15fr 1fr;gap:16px;align-items:start}
 .toast{bottom:24px}
}
</style>
<div class="app">
 <nav class="side">
  <div class="brand" style="padding:6px 10px 16px"><img src="/static/icon-192.png" alt="">Bàn Học</div>
  <a data-go="home"><i>🏠</i>Tổng quan</a><a data-go="camera"><i>📺</i>Camera</a>
  <a data-go="report"><i>📊</i>Báo cáo</a><a data-go="rules"><i>🛡️</i>Luật chặn</a><a data-go="settings"><i>⚙️</i>Cài đặt</a>
 </nav>
 <div class="main">
  <div class="top"><div class="brand"><img src="/static/icon-192.png" alt="" class="m-only">Bàn Học Thông Minh</div>
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
   </div><div>
    <div class="card"><h3>🔔 Cảnh báo gần đây</h3><div id="alerts" class="mut">Chưa có cảnh báo.</div></div>
    <div class="card"><h3>🕒 Thay đổi gần đây</h3><p class="mut" style="margin-top:-4px">Ứng dụng vừa mở, trang web vừa vào — AI tự nhận ra web game / giải trí.</p><div id="evbox" class="mut">Chưa có.</div></div>
    <div class="card"><h3>📌 Đang dùng gì</h3><div id="nowbox" class="mut">—</div><div id="openbox"></div>
     <div class="bar" id="h-bar" style="margin-top:12px"></div><div class="mut" id="h-legend" style="margin-top:6px"></div></div>
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
   <div class="card"><h3>▶️ YouTube</h3><p class="mut">Bài giảng vẫn xem bình thường. Máy chỉ đóng tab khi <b>từ khóa tìm kiếm</b> hoặc <b>tên video</b> chứa từ khóa giải trí.</p>
    <div class="seg" id="yt-mode"><button type="button" data-m="keyword" onclick="ytMode(this)">🔎 Chặn theo từ khóa</button><button type="button" data-m="block" onclick="ytMode(this)">⛔ Chặn toàn bộ</button><button type="button" data-m="allow" onclick="ytMode(this)">✅ Cho phép hết</button></div>
    <div id="yt-kw-box"><label class="mut">Từ khóa cấm khi tìm / xem (cách nhau dấu phẩy)</label><textarea id="yt-kw" oninput="rulesTouched=true"></textarea>
    <div class="sw"><div><div class="t">Chặn YouTube Shorts</div><div class="d">Video ngắn lướt liên tục, dễ nghiện</div></div>
     <label class="tg"><input type="checkbox" id="yt-shorts" checked onchange="rulesTouched=true"><span></span></label></div></div></div>
   <div class="card"><h3>🔔 Báo khi học sinh đổi việc</h3><p class="mut">Mỗi lần mở ứng dụng mới / vào trang web mới, hệ thống tự phân tích trang đó là học tập, game, mạng xã hội...</p>
    <div class="seg" id="nv-mode"><button type="button" data-m="risky" onclick="nvMode(this)">⚠️ Chỉ trang đáng ngờ</button><button type="button" data-m="all" onclick="nvMode(this)">📣 Mọi thay đổi</button><button type="button" data-m="off" onclick="nvMode(this)">🔕 Tắt</button></div>
    <div class="sw"><div><div class="t">Tự chặn web game mới phát hiện</div><div class="d">Trang được nhận ra là game sẽ tự thêm vào luật chặn và đóng tab</div></div>
     <label class="tg"><input type="checkbox" id="opt-autogame" onchange="rulesTouched=true"><span></span></label></div></div>
   <div class="card"><h3>⚙️ Cách giám sát</h3>
    <div class="sw"><div><div class="t">Giám sát cả khi học sinh chưa đăng nhập</div><div class="d">Bật: chặn & báo mọi lúc máy bật. Tắt: chỉ khi học sinh vào chế độ học.</div></div>
     <label class="tg"><input type="checkbox" id="opt-always"><span></span></label></div>
    <div class="sw"><div><div class="t">Chặn tận gốc trang web (DNS)</div><div class="d">Trình duyệt nào cũng không vào được. Cần app SmartDesk chạy quyền Admin.</div></div>
     <label class="tg"><input type="checkbox" id="opt-dns" checked><span></span></label></div>
    <p><button class="bigbtn pri" style="width:100%" onclick="saveRules()">💾 Lưu & áp dụng</button></p><p class="mut" id="rmsg"></p></div>
  </section>

  <section data-view="settings">
   <div class="card"><h3>➕ Thêm máy học sinh</h3><p class="mut">Mã 6 số hiện trong app SmartDesk → tab “Kết nối phụ huynh”.</p>
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
 <a data-go="report"><i>📊</i>Báo cáo</a><a data-go="rules"><i>🛡️</i>Luật chặn</a><a data-go="settings"><i>⚙️</i>Cài đặt</a></nav>
<div class="toast" id="toast"></div>
<script>
const CSRF=document.querySelector('meta[name=csrf-token]').content;
const $=id=>document.getElementById(id);
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
 if(v==='report')report();if(v==='rules')fillRules(true);if(v==='camera')showLive();window.scrollTo(0,0);}
document.querySelectorAll('[data-go]').forEach(a=>a.onclick=()=>go(a.dataset.go));
const fmtMin=s=>{s=Math.round((s||0)/60);return s>=60?Math.floor(s/60)+'g'+String(s%60).padStart(2,'0'):s+'′';};
const CAT={Hoc_tap:['Học tập','#16a34a','📘'],Giai_tri:['Giải trí','#dc2626','🎮'],Khac:['Khác','#94a3b8','🗂️']};
function appIcon(app,title){const t=(app+' '+title).toLowerCase();
 const M=[['youtube','▶️'],['facebook','📘'],['tiktok','🎵'],['zalo','💬'],['chrome','🌐'],['msedge','🌐'],['firefox','🌐'],['coccoc','🌐'],['winword','📝'],['word','📝'],
  ['excel','📊'],['powerpnt','📽️'],['zoom','🎥'],['teams','🎥'],['classin','🎥'],['code','💻'],['python','💻'],['notepad','🗒️'],['calc','🧮'],['geogebra','📐'],
  ['steam','🎮'],['roblox','🎮'],['game','🎮'],['spotify','🎧'],['discord','💬'],['telegram','💬'],['explorer','📁'],['pdf','📕'],['acrobat','📕']];
 for(const [k,i] of M) if(t.includes(k)) return i; return '🖥️';}
function statusOf(v){const s=v.status||{},p=s.posture||{},n=v.now||{};
 if(!v.online) return ['off','⚪ Máy đang tắt / mất mạng','Cập nhật lần cuối '+Math.round(v.ago/60)+' phút trước'];
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
function pick(id){CUR=id;try{localStorage.setItem('sd_dev',id)}catch(e){};renderAll();if(VIEW==='report')report();if(VIEW==='rules')fillRules(true);}
function renderAll(){const v=cur();renderChips();renderSettings();
 if(!v){$('h-name').textContent='Chưa có máy nào';$('h-st').textContent='➕ Ghép máy trong Cài đặt';$('hero').className='hero off';return;}
 const [cls,st,sub]=statusOf(v),s=v.status||{},p=s.posture||{},t=v.today||{};
 $('hero').className='hero '+cls;$('h-name').textContent=v.name+(s.role==='student'?' · đang ở chế độ học sinh':'');
 $('h-st').textContent=st;$('h-sub').textContent=sub||'';
 $('h-cfi').textContent=p.camera&&p.cfi!=null?Math.round(p.cfi)+'%':'—';
 $('h-study').textContent=fmtMin(t.Hoc_tap);$('h-fun').textContent=fmtMin(t.Giai_tri);
 $('h-ai').textContent=Math.round((s.ai_seconds||0)/60)+'/'+(s.max_ai_minutes||'?')+'′';
 const sum=(t.Hoc_tap||0)+(t.Giai_tri||0)+(t.Khac||0);
 $('h-bar').innerHTML=sum?['Hoc_tap','Giai_tri','Khac'].map(k=>`<span style="width:${100*(t[k]||0)/sum}%;background:${CAT[k][1]}"></span>`).join(''):'';
 $('h-legend').textContent=sum?`Hôm nay: ${fmtMin(t.Hoc_tap)} học · ${fmtMin(t.Giai_tri)} giải trí · ${fmtMin(t.Khac)} khác`:'Chưa có dữ liệu hôm nay';
 const n=v.now||{};$('nowbox').innerHTML=n.title?`<div class="app-row"><div class="ic">${appIcon(n.app||'',n.title)}</div><div class="nm"><div><b>${esc(n.app||'')}</b></div><div class="mut">${esc(n.title)}</div></div>
  ${CAT[n.category]?`<span class="tag" style="background:${CAT[n.category][1]}">${CAT[n.category][0]}</span>`:''}</div>`:(n.category==='Idle'?'<span class="mut">💤 Đang rời máy</span>':'—');
 renderOpen(n.open||[],p);renderEvents(v);
 $('alerts').innerHTML=(v.alerts||[]).slice(0,6).map(a=>`<div class="alert-row"><time>${esc(a.time)}</time><div>${esc(a.message)}</div></div>`).join('')||'Không có cảnh báo. 👍';
 const acks=v.acks||[];if(acks.length){$('acks').style.display='block';$('acks').innerHTML='<h3>✅ Kết quả lệnh</h3>'+acks.slice(0,3).map(a=>`<div class="${a.ok?'':'err'}" style="font-size:14px;padding:3px 0">${a.ok?'✅':'⚠️'} ${esc(a.time)} ${esc(a.msg)}</div>`).join('');
  const k=acks[0].time+acks[0].msg;if(lastAck&&k!==lastAck)toast((acks[0].ok?'✅ ':'⚠️ ')+acks[0].msg);lastAck=k;}
 if(VIEW==='camera')showLive();}
function renderOpen(list,p){const box=$('openbox');let h='';
 if(p&&p.held_object)h+=`<div class="alert-row" style="margin-top:8px"><time>📷 AI</time><div>Đang cầm <b>${esc(p.held_object)}</b></div></div>`;
 if(p&&p.action&&p.action.vi)h+=`<div class="alert-row"><time>🧠 AI</time><div>Hành động: <b>${esc(p.action.vi)}</b> (${Math.round((p.action.score||0)*100)}%)</div></div>`;
 if(list.length){const tag=c=>CAT[c]?`<span class="tag" style="background:${CAT[c][1]}">${CAT[c][0]}</span>`:'';
  h+=`<div class="mut" style="margin:12px 0 4px;font-size:13px;font-weight:700">ĐANG MỞ · ${list.length} ứng dụng</div>`+list.map(a=>{
   const items=(a.tabs.length?a.tabs:a.windows);const sub=a.tabs.length?`${a.tabs.length} tab`:(a.windows[0]?a.windows[0].title:'');
   return `<details class="open-app"${a.focused?' open':''}><summary><span class="ic">${appIcon(a.exe,(a.windows[0]||{}).title||'')}</span>
    <span class="nm"><b>${esc(a.exe)}</b>${a.focused?' <span class="pill on" style="font-size:11px">đang dùng</span>':''}<br><span class="mut">${esc(sub)}</span></span>${tag(a.category)}</summary>
    ${items.length>1||a.tabs.length?items.map(t=>`<div class="tab-row">${a.tabs.length?'🗂️':'🪟'} <span class="tt">${esc(t.title)}</span>${t.category!=='Khac'?tag(t.category):''}</div>`).join(''):''}</details>`}).join('');}
 box.innerHTML=h;}
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
const KCOL={game:'#dc2626',social:'#db2777',video:'#ea580c',shopping:'#ca8a04',comics:'#9333ea',study:'#16a34a',ai:'#2563eb',app:'#64748b',other:'#94a3b8'};
function renderEvents(v){const ev=v.events||[];if(!ev.length){$('evbox').textContent='Chưa có thay đổi nào.';return;}
 $('evbox').innerHTML=ev.slice(0,15).map(e=>{const tm=new Date((e.t||0)*1000).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'});
  const name=e.kind==='web'?e.domain:e.exe;const ic=e.kind==='web'?({game:'🎮',social:'💬',video:'🎬',shopping:'🛒',comics:'📚',study:'📘',ai:'🤖'}[e.cls]||'🌐'):'🪟';
  const risky=['game','social','video','shopping','comics'].includes(e.cls);
  const btn=e.kind==='web'&&!e.blocked&&e.cls!=='study'&&e.cls!=='ai'?`<button class="${risky?'danger':''}" style="padding:4px 10px;font-size:12px" onclick="blockDom('${esc(e.domain)}')">Chặn</button>`:(e.blocked?'<span class="pill">đã chặn</span>':'');
  return `<div class="app-row"><div class="ic">${ic}</div><div class="nm"><div><b>${esc(name||'')}</b> <span class="tag" style="background:${KCOL[e.cls]||KCOL.other}">${esc(e.vi||'')}</span></div>
   <div class="mut">${tm}${e.title?' · '+esc(e.title):''}${e.why?' · '+esc(e.why):''}</div></div>${btn}</div>`;}).join('');}
async function blockDom(d){const v=cur();if(!v||!confirm('Chặn trang '+d+' trên máy này?'))return;const r=await api(`/api/parent/devices/${v.id}/block`,{domain:d});toast(r.message||r.error);load();}
function ytMode(b){document.querySelectorAll('#yt-mode button').forEach(x=>x.classList.toggle('act',x===b));$('yt-kw-box').style.display=b.dataset.m==='keyword'?'block':'none';rulesTouched=true;}
function ytSet(m){const b=document.querySelector(`#yt-mode button[data-m="${m}"]`)||document.querySelector('#yt-mode button');ytMode(b);}
function fillRules(force){const v=cur();if(!v||(rulesTouched&&!force))return;const st=(v.status||{}),r=st.rules||{},srv=(v.rules||{});
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
 const nb=document.querySelector(`#nv-mode button[data-m="${srv.notify_env||'risky'}"]`);if(nb)nvMode(nb);$('opt-autogame').checked=!!srv.auto_block_games;$('yt-shorts').checked=(srv.youtube_shorts??r.youtube_shorts)!==false;rulesTouched=false;
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
  auto_block_games:$('opt-autogame').checked});$('rmsg').textContent=r.message||r.error;toast(r.message||r.error);rulesTouched=false;}
// ---------- PWA ----------
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
let deferredPrompt=null;addEventListener('beforeinstallprompt',e=>{e.preventDefault();deferredPrompt=e;$('installBtn').style.display='inline-block';});
async function installApp(){if(deferredPrompt){deferredPrompt.prompt();deferredPrompt=null;$('installBtn').style.display='none';}}
$('themeBtn').textContent=document.documentElement.getAttribute('data-theme')==='dark'?'☀️ Nền sáng':'🌙 Nền tối';
const qs=new URLSearchParams(location.search);go({camera:'camera',report:'report'}[qs.get('tab')]||'home');
load();nstat();setInterval(load,6000);setInterval(()=>{if(VIEW==='report'&&!document.hidden)report();},15000);
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
@app.route("/api/parent/devices")
@login_required
def api_parent_devices():
    uid = current_uid()
    out = []
    for did, dev in devices_of_parent(uid):
        mark_watching(did, dev=dev)  # phụ huynh đang xem -> máy báo nhanh 5s
        out.append({
            "id": did,
            "name": dev.get("name") or did,
            "online": is_online(dev),
            "ago": now() - int(dev.get("last_seen", 0) or 0),
            "status": dev.get("status") or {},
            "alerts": (dev.get("recent_alerts") or [])[:10],
            "events": (dev.get("events") or [])[:25],
            "acks": (dev.get("acks") or [])[:5],
            "now": live_category(dev),
            "rules": {k: v for k, v in (dev.get("rules") or {}).items() if k != "by"},
            "today": ((db.get(f"activity_sum/{did}/{vn_date()}/totals")) or {}),
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
    ctype = str((request.get_json(silent=True) or {}).get("type", ""))
    if ctype not in COMMAND_TYPES:
        return jsonify({"error": "Lệnh không hợp lệ."}), 400
    queue_command(did, ctype, by=uid)
    dev = db.get(f"devices/{did}") or {}
    note = "" if is_online(dev) else " (máy đang ngoại tuyến, lệnh sẽ chạy khi máy kết nối lại)"
    return jsonify({"ok": True, "message": f"Đã gửi lệnh: {COMMAND_TYPES[ctype]} — máy nhận trong ≤{HEARTBEAT_S}s "
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

    rules = {"apps": clean(data.get("apps")), "domains": clean(data.get("domains")),
             "keywords": clean(data.get("keywords")), "ver": int(time.time() * 1000), "by": uid,
             "monitor_always": bool(data.get("monitor_always", False)), "dns_block": bool(data.get("dns_block", True)),
             "groups": [str(g)[:30] for g in (data.get("groups") or [])][:20],
             "youtube_mode": data.get("youtube_mode") if data.get("youtube_mode") in ("keyword", "block", "allow") else "keyword",
             "youtube_keywords": clean(data.get("youtube_keywords")),
             "youtube_shorts": bool(data.get("youtube_shorts", True)),
             "notify_env": data.get("notify_env") if data.get("notify_env") in ("risky", "all", "off") else "risky",
             "auto_block_games": bool(data.get("auto_block_games", False))}
    db.set(f"devices/{did}/rules", rules)
    mark_watching(did, force=True)
    return jsonify({"ok": True, "message": "Đã lưu. Máy sẽ áp dụng trong ≤30 giây."})


@app.route("/api/parent/devices/<did>/block", methods=["POST"])
@login_required
def api_parent_block(did):
    """Chặn nhanh 1 tên miền từ nhật ký thay đổi (thêm vào luật, máy áp dụng ở heartbeat kế tiếp)."""
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    dom = str((request.get_json(silent=True) or {}).get("domain", "")).strip().lower()[:80]
    if not dom or "." not in dom or "/" in dom or " " in dom:
        return jsonify({"error": "Tên miền không hợp lệ"}), 400
    rules = dict(db.get(f"devices/{did}/rules") or {})
    rules["domains"] = sorted(set(rules.get("domains") or []) | {dom})[:200]
    rules["ver"] = int(time.time() * 1000)
    rules["by"] = uid
    db.set(f"devices/{did}/rules", rules)
    mark_watching(did, force=True)
    return jsonify({"ok": True, "message": f"Đã chặn {dom}. Máy áp dụng trong vài giây."})


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
           "comics": "Truyện tranh", "study": "Học tập", "ai": "Chatbot AI", "other": "Khác"}
KIND_CAT = {"game": "Giai_tri", "social": "Giai_tri", "video": "Giai_tri", "shopping": "Giai_tri", "comics": "Giai_tri",
            "study": "Hoc_tap", "ai": "Hoc_tap", "other": "Khac"}
RISKY_KINDS = ("game", "social", "video", "shopping", "comics")
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


def _handle_events(did: str, dev: Dict[str, Any], events: List[Dict[str, Any]]) -> None:
    """Sự kiện thay đổi môi trường (mở app / vào trang web mới): phân loại, lưu nhật ký, báo phụ huynh, tự chặn game."""
    rules = dict(dev.get("rules") or {})
    mode = rules.get("notify_env", "risky")          # risky | all | off
    out, notify = [], []
    blocked_new: List[str] = []
    for ev in events[:30]:
        kind = str(ev.get("kind", ""))
        title = str(ev.get("title", ""))[:120]
        rec = {"t": int(ev.get("t", 0) or now()), "kind": kind, "exe": str(ev.get("exe", ""))[:60], "title": title}
        if kind == "web":
            dom = str(ev.get("domain", ""))[:80].lower()
            c = classify_domain(dom, title)
            rec.update(domain=dom, cls=c["kind"], vi=c["vi"], category=c["category"], why=c.get("why", ""))
            already = any(dom.endswith(x) for x in (rules.get("domains") or []))
            rec["blocked"] = already
            if c["kind"] == "game" and rules.get("auto_block_games") and not already:
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
    if blocked_new:
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


def live_category(dev: Dict[str, Any]) -> Dict[str, Any]:
    """Cửa sổ đang FOCUS + danh sách ứng dụng / tab đang mở (phân loại theo luật) cho thẻ "Đang dùng gì"."""
    st = dev.get("status") or {}
    nw = st.get("now") or {}
    opened = []
    for a in st.get("open_apps") or []:
        tabs = [{"title": t, "category": classify_rules(a.get("exe", ""), t)[0]} for t in a.get("tabs") or []]
        wins = [{"title": t, "category": classify_rules(a.get("exe", ""), t)[0]} for t in a.get("windows") or []]
        cats = [x["category"] for x in tabs + wins]
        top = "Giai_tri" if "Giai_tri" in cats else ("Hoc_tap" if "Hoc_tap" in cats else "Khac")
        opened.append({"exe": a.get("exe", ""), "focused": bool(a.get("focused")), "windows": wins, "tabs": tabs,
                       "category": top})
    if not nw or nw.get("idle"):
        return {"category": "Idle", "app": "", "title": nw.get("title", ""), "open": opened}
    cat, _ = classify_rules(str(nw.get("exe", "")), str(nw.get("title", "")))
    return {"category": cat, "app": nw.get("exe", ""), "title": str(nw.get("title", ""))[:90],
            "age": now() - int(nw.get("t", 0) or 0), "open": opened}


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


@app.route("/api/agent/heartbeat", methods=["POST"])
def api_agent_heartbeat():
    did, dev = device_auth()
    if not did:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    status = data.get("status") or {}
    if isinstance(status, dict) and isinstance(status.get("open_apps"), list):
        status["open_apps"] = _sanitize_open_apps(status["open_apps"])
    new_alerts = [a for a in (data.get("alerts") or []) if isinstance(a, dict)][:10]
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
            dev = db.get(f"devices/{did}") or dev
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
            out.append({"id": cid, "type": c.get("type"), "chat_id": c.get("chat_id", "")})
        db.delete(f"commands/{did}/{cid}")
    fast = int(dev.get("watch_until", 0) or 0) > now() or bool(out)
    resp: Dict[str, Any] = {"commands": out, "heartbeat_s": FAST_HEARTBEAT_S if fast else HEARTBEAT_S,
                            "paired": bool(dev.get("owners"))}
    rules = dev.get("rules") or {}
    if rules and int(rules.get("ver", 0) or 0) != int(status.get("rules_ver", 0) or 0):
        resp["rules"] = rules
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
        # Nghỉ vệ sinh luôn báo (sự kiện bắt đầu / kết thúc), các loại khác cách nhau ALERT_COOLDOWN_S
        if typ.lower() != "break" and (now() - int(cooldowns.get(typ, 0) or 0) < ALERT_COOLDOWN_S or typ in sent_types):
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
    n = web_push_owners([current_uid()], "🔔 Bàn Học Thông Minh", "Thông báo thử: điện thoại này sẽ nhận cảnh báo.",
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
    "🤖 <b>Bot Bàn Học Thông Minh</b>\n"
    "/may — Danh sách máy bạn quản lý\n"
    "/status [số] — Tình trạng học tập\n"
    "/lock [số] — Khóa màn hình\n"
    "/reset_ai [số] — Đặt lại giờ dùng AI\n"
    "/anh [số] — Chụp 1 ảnh camera\n"
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
    elif cmd in ("/lock", "/reset_ai", "/anh"):
        ctype = {"/lock": "lock", "/reset_ai": "reset_ai", "/anh": "snapshot"}[cmd]
        queue_command(did, ctype, by=uid, chat_id=chat_id)
        tg_send(chat_id, f"📨 Đã gửi lệnh <b>{COMMAND_TYPES[ctype]}</b> tới {name} (≤{HEARTBEAT_S}s).")
    if cmd in ("/status", "/lock", "/reset_ai", "/anh"):
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
    lines.append(f"🤖 AI: {round((s.get('ai_seconds') or 0) / 60)}/{s.get('max_ai_minutes', '?')} phút")
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
    "name": "Bàn Học Thông Minh - Phụ huynh", "short_name": "Bàn Học", "lang": "vi",
    "description": "Theo dõi việc học, xem camera, khóa máy và nhận cảnh báo từ máy học sinh.",
    "start_url": "/dashboard?src=pwa", "scope": "/", "display": "standalone", "orientation": "portrait",
    "background_color": "#f4f6fb", "theme_color": "#2563eb",
    "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
              {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    "shortcuts": [{"name": "Xem camera", "url": "/dashboard?tab=camera"},
                  {"name": "Báo cáo hôm nay", "url": "/dashboard?tab=report"}],
}

SW_JS = """
const CACHE='banhoc-v2';
const SHELL=['/offline','/static/icon-192.png','/static/icon-512.png','/manifest.webmanifest'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim();});
self.addEventListener('fetch',e=>{
  const r=e.request; if(r.method!=='GET')return;
  const u=new URL(r.url); if(u.origin!==location.origin)return;
  if(u.pathname.startsWith('/static/')){e.respondWith(caches.match(r).then(m=>m||fetch(r)));return;}
  if(r.mode==='navigate'){e.respondWith(fetch(r).catch(()=>caches.match('/offline')));}
});
self.addEventListener('push',e=>{let d={};try{d=e.data.json();}catch(_){d={body:e.data?e.data.text():''};}
  e.waitUntil(self.registration.showNotification(d.title||'Bàn Học Thông Minh',{body:d.body||'',icon:'/static/icon-192.png',
   badge:'/static/icon-192.png',tag:d.tag||'sd',renotify:true,vibrate:[200,100,200],data:{url:d.url||'/dashboard'}}));});
self.addEventListener('notificationclick',e=>{e.notification.close();const url=(e.notification.data||{}).url||'/dashboard';
  e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>{for(const c of cs){if(c.url.includes('/dashboard'))return c.focus();}
   return clients.openWindow(url);}));});
"""

OFFLINE_HTML = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Mất kết nối</title><body style="font-family:system-ui;background:#f4f6fb;color:#0f172a;display:grid;place-items:center;min-height:90vh;margin:0">
<div style="text-align:center;padding:24px"><img src="/static/icon-192.png" width=96 style="border-radius:22px"><h2>Mất kết nối mạng</h2>
<p style="color:#64748b">Điện thoại đang offline. Ứng dụng sẽ tự tải lại khi có mạng.</p>
<button onclick="location.reload()" style="font:inherit;padding:12px 20px;border-radius:12px;border:0;background:#2563eb;color:#fff">Thử lại</button></div>
<script>addEventListener('online',()=>location.reload())</script></body>"""


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
                    "password_login": ALLOW_PASSWORD_LOGIN})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
