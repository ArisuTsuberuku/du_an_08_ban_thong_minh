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
<div class="wrap" style="max-width:420px;margin-top:8vh">
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
<title>Bàn Học Thông Minh</title><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{ csrf }}">
<style>
.btns button{flex:1 1 auto}.ack{font-size:13px;margin:6px 0}.ok{color:#16a34a}.no{color:var(--err)}
.bar{height:12px;border-radius:6px;background:var(--pill);overflow:hidden;display:flex}
.bar span{display:block;height:100%}.c-h{background:#22c55e}.c-g{background:#ef4444}.c-k{background:#94a3b8}
details{border-top:1px solid var(--line);margin-top:10px;padding-top:8px}summary{cursor:pointer;font-weight:600}
textarea{width:100%;min-height:54px;font:inherit;border-radius:10px;border:1px solid var(--line);padding:8px;background:var(--input);color:var(--txt)}
img.live{width:100%;border-radius:10px;background:#000;margin-top:8px;display:none}
</style>
<div class="wrap">
 <div class="row" style="justify-content:space-between"><h2>🛡️ Bàn Học Thông Minh</h2>
  <div class="row">{% if picture %}<img class="avatar" src="{{ picture }}" alt="" referrerpolicy="no-referrer">{% endif %}
   <span class="mut">{{ name or email }}</span>
   <button id="themeBtn" onclick="toggleTheme()">🌙 Nền tối</button>
   <form method="post" action="/logout"><input type="hidden" name="csrf" value="{{ csrf }}"><button>Đăng xuất</button></form></div></div>
 <div class="grid">
  <div class="card"><b>➕ Thêm máy học sinh</b><p class="mut">Mã ghép 6 số hiện trên máy học sinh (http://127.0.0.1:5000/ket-noi-cloud).</p>
   <div class="row"><input id="pcode" placeholder="Mã 6 số" maxlength="6" style="width:110px"><input id="pname" placeholder="Tên máy (vd: Máy của Minh)">
   <button onclick="pair()">Ghép</button></div><p id="pmsg" class="mut"></p></div>
  <div class="card"><b>💬 Telegram</b><p class="mut">Mỗi phụ huynh liên kết Telegram riêng. Bot gửi cảnh báo của <i>các máy bạn quản lý</i>; gõ /anh để nhận ảnh.</p>
   <div id="chats" class="mut"></div><button onclick="tglink()">Kết nối Telegram</button> <span id="tgmsg" class="mut"></span></div>
 </div>
 <div id="devices"></div>
</div>
<script>
const CSRF=document.querySelector('meta[name=csrf-token]').content;
document.getElementById('themeBtn').textContent=document.documentElement.getAttribute('data-theme')==='dark'?'☀️ Nền sáng':'🌙 Nền tối';
async function api(url, body){const r=await fetch(url,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},body:body?JSON.stringify(body):undefined});
 if(r.status===401){location.href='/login';return {};} return r.json();}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
const $=id=>document.getElementById(id);
const STATE={};  // trạng thái mới nhất mỗi máy
async function pair(){const r=await api('/api/parent/pair',{code:pcode.value.trim(),name:pname.value.trim()});pmsg.textContent=r.message||r.error;load();}
async function tglink(){const r=await api('/api/parent/telegram/link',{});if(r.url){window.open(r.url,'_blank');tgmsg.textContent='Bấm Start trong Telegram để hoàn tất.';}else tgmsg.textContent=r.error;}
async function cmd(did,t){const r=await api(`/api/parent/devices/${did}/command`,{type:t});$('msg-'+did).textContent=r.message||r.error||'';}
async function unlink(did){if(confirm('Bỏ quản lý máy này?')){await api(`/api/parent/devices/${did}/unlink`,{});location.reload();}}
function liveUrls(v){const L=(v.status||{}).live||{};if(!L.active)return null;
 const pub=L.public_url||'';return {pub, video: pub?pub.replace('/live?','/video_feed?')+'&fps=5':'', snap: pub?pub.replace('/live?','/snapshot.jpg?'):'', lan:L.lan_url||'', note:L.note||''};}
function showLive(did){const v=STATE[did];const u=v&&liveUrls(v);const img=$('live-'+did);
 if(u&&u.video){if(img.dataset.src!==u.video){img.src=u.video;img.dataset.src=u.video;}img.style.display='block';}
 else{img.removeAttribute('src');img.dataset.src='';img.style.display='none';}}
async function liveOn(did){await cmd(did,'live_on');$('msg-'+did).textContent+=' — đang mở đường truyền trực tiếp (5–30 giây)…';}
async function liveOff(did){await cmd(did,'live_off');const img=$('live-'+did);img.removeAttribute('src');img.dataset.src='';img.style.display='none';}
function snap(did){const u=STATE[did]&&liveUrls(STATE[did]);
 if(u&&u.snap){window.open(u.snap+'&ts='+Date.now(),'_blank');}
 else if(u&&u.lan){window.open(u.lan.replace('/live?','/snapshot.jpg?'),'_blank');$('msg-'+did).textContent='Mở ảnh qua Wi-Fi nhà (chỉ được khi điện thoại cùng mạng với máy học sinh).';}
 else{$('msg-'+did).textContent='Bấm "📺 Xem trực tiếp" trước, hoặc gõ /anh trên Telegram để nhận ảnh.';}}
function lanOpen(did){const u=STATE[did]&&liveUrls(STATE[did]);if(u&&u.lan)window.open(u.lan,'_blank');else $('msg-'+did).textContent='Bấm "📺 Xem trực tiếp" trước.';}
function cardSkeleton(v){return `<div class="card" id="card-${v.id}">
 <div class="row" style="justify-content:space-between"><b id="name-${v.id}"></b><span id="online-${v.id}" class="pill"></span></div>
 <p class="mut" id="meta-${v.id}"></p><div id="stat-${v.id}"></div>
 <div class="row btns" style="margin-top:10px">
  <button onclick="cmd('${v.id}','lock')">🔒 Khóa màn hình</button>
  <button onclick="cmd('${v.id}','reset_ai')">♻️ Đặt lại giờ AI</button>
  <button onclick="cmd('${v.id}','ai_on')">📷 Bật Camera AI</button>
  <button onclick="cmd('${v.id}','ai_off')">⏸️ Tắt Camera AI</button>
  <button onclick="cmd('${v.id}','recalibrate')">🔄 Hiệu chỉnh tư thế</button></div>
 <div class="row btns" style="margin-top:8px">
  <button onclick="liveOn('${v.id}')">📺 Xem trực tiếp</button>
  <button onclick="liveOff('${v.id}')">⏹️ Tắt xem</button>
  <button onclick="snap('${v.id}')">📸 Chụp ảnh</button>
  <button onclick="lanOpen('${v.id}')">🏠 Mở trong Wi-Fi nhà</button></div>
 <p id="msg-${v.id}" class="mut"></p><div id="ack-${v.id}" class="ack"></div>
 <img id="live-${v.id}" class="live" alt="Camera trực tiếp" data-src="">
 <details id="rep-${v.id}" ontoggle="if(this.open)report('${v.id}')"><summary>📊 Báo cáo hoạt động</summary>
  <div class="row" style="margin:8px 0"><input type="date" id="date-${v.id}" onchange="report('${v.id}')"><span class="mut" id="tot-${v.id}"></span></div>
  <div class="bar" id="bar-${v.id}"></div><div class="mut" id="legend-${v.id}" style="margin:6px 0"></div>
  <table id="act-${v.id}"></table></details>
 <details><summary>🔒 Cấu hình chặn từ xa</summary>
  <p class="mut">Mỗi mục cách nhau dấu phẩy. Máy áp dụng trong ≤30 giây.</p>
  <label class="mut">Ứng dụng / game (tên .exe hoặc một phần tên)</label><textarea id="rapps-${v.id}"></textarea>
  <label class="mut">Trang web (tên miền)</label><textarea id="rdom-${v.id}"></textarea>
  <label class="mut">Từ khóa tiêu đề cửa sổ</label><textarea id="rkw-${v.id}"></textarea>
  <p><button onclick="saveRules('${v.id}')">💾 Lưu & áp dụng</button> <span class="mut" id="rmsg-${v.id}"></span></p></details>
 <details><summary>🚨 Cảnh báo gần đây</summary><table id="alerts-${v.id}"></table></details>
 <p><button class="danger" onclick="unlink('${v.id}')">Bỏ quản lý máy này</button></p></div>`;}
function todayVN(){return new Date(Date.now()+7*3600e3).toISOString().slice(0,10);}
function fillRules(v){const r=(v.status||{}).rules||{};for(const [k,id] of [['apps','rapps'],['domains','rdom'],['keywords','rkw']]){
 const el=$(id+'-'+v.id);if(document.activeElement!==el&&!el.dataset.touched)el.value=(r[k]||[]).join(', ');el.oninput=()=>el.dataset.touched=1;}}
async function saveRules(did){const sp=id=>$(id+'-'+did).value.split(',').map(x=>x.trim()).filter(Boolean);
 const r=await api(`/api/parent/devices/${did}/rules`,{apps:sp('rapps'),domains:sp('rdom'),keywords:sp('rkw')});
 $('rmsg-'+did).textContent=r.message||r.error;['rapps','rdom','rkw'].forEach(id=>delete $(id+'-'+did).dataset.touched);}
const CAT={Hoc_tap:['Học tập','c-h'],Giai_tri:['Giải trí','c-g'],Khac:['Khác','c-k']};
async function report(did){const di=$('date-'+did);if(!di.value)di.value=todayVN();
 const r=await api(`/api/parent/devices/${did}/activity?date=${di.value}`);const t=r.totals||{};const sum=Object.values(t).reduce((a,b)=>a+b,0);
 $('tot-'+did).textContent=sum?('Tổng '+Math.round(sum/60)+' phút dùng máy'):'Chưa có dữ liệu ngày này';
 $('bar-'+did).innerHTML=sum?Object.entries(CAT).map(([k,[n,c]])=>`<span class="${c}" style="width:${100*(t[k]||0)/sum}%"></span>`).join(''):'';
 $('legend-'+did).innerHTML=Object.entries(CAT).map(([k,[n]])=>`${n}: <b>${Math.round((t[k]||0)/60)}</b> phút (${sum?Math.round(100*(t[k]||0)/sum):0}%)`).join(' · ');
 $('act-'+did).innerHTML=(r.items||[]).map(i=>`<tr><td><span class="pill ${CAT[i.category]?CAT[i.category][1]:'c-k'}" style="color:#fff">${esc((CAT[i.category]||['Khác'])[0])}</span></td>
  <td><b>${esc(i.app)}</b><div class="mut">${esc(i.title)}</div><div class="mut">${esc(i.reason||'')}</div></td><td style="text-align:right">${Math.max(1,Math.round(i.seconds/60))}′</td></tr>`).join('');}
async function load(){
 const d=await api('/api/parent/devices');if(!d.devices)return;
 chats.innerHTML=(d.chats||[]).length?('Đã liên kết: '+d.chats.map(c=>'<span class="pill on">'+esc(c)+'</span>').join(' ')):'Chưa liên kết Telegram.';
 const box=$('devices');if(!d.devices.length){box.innerHTML='<div class="card mut">Chưa có máy nào. Nhập mã ghép ở trên.</div>';return;}
 for(const v of d.devices){STATE[v.id]=v;if(!$('card-'+v.id)){box.insertAdjacentHTML('beforeend',cardSkeleton(v));}
  const s=v.status||{},p=s.posture||{};
  $('name-'+v.id).textContent=v.name;const on=$('online-'+v.id);on.className='pill '+(v.online?'on':'off');on.textContent=v.online?'● Trực tuyến':'○ Ngoại tuyến';
  $('meta-'+v.id).textContent=`${s.hostname||''} · cập nhật ${v.ago}s trước · vai trò: ${s.role||'?'}`;
  $('stat-'+v.id).innerHTML=`<div class="row"><div><div class="big">${p.camera&&p.cfi!=null?Math.round(p.cfi)+'%':'—'}</div><div class="mut">Điểm tập trung CFI</div></div>
   <div style="margin-left:18px"><div>${esc(p.camera?(p.state_vi||'Đang phân tích…'):'Camera AI đang tắt')}</div>
   <div class="mut">${p.distance_cm?('Khoảng cách '+Math.round(p.distance_cm)+'cm · '):''}AI: ${Math.round((s.ai_seconds||0)/60)}/${s.max_ai_minutes||'?'} phút</div></div></div>`;
  $('ack-'+v.id).innerHTML=(v.acks||[]).slice(0,3).map(a=>`<div class="${a.ok?'ok':'no'}">${a.ok?'✅':'⚠️'} ${esc(a.time)} ${esc(a.msg)}</div>`).join('');
  $('alerts-'+v.id).innerHTML=(v.alerts||[]).map(a=>`<tr><td class="mut">${esc(a.time)}</td><td>${esc(a.message)}</td></tr>`).join('')||'<tr><td class="mut">Chưa có cảnh báo.</td></tr>';
  const u=liveUrls(v);if(u&&!u.pub&&u.note)$('msg-'+v.id).textContent='📺 '+u.note;
  showLive(v.id);fillRules(v);}
}
load();setInterval(load,6000);
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
                                  cmds=list(COMMAND_TYPES.items()))


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
            "acks": (dev.get("acks") or [])[:5],
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
             "keywords": clean(data.get("keywords")), "ver": int(time.time() * 1000), "by": uid}
    db.set(f"devices/{did}/rules", rules)
    mark_watching(did, force=True)
    return jsonify({"ok": True, "message": "Đã lưu. Máy sẽ áp dụng trong ≤30 giây."})


@app.route("/api/parent/devices/<did>/activity")
@login_required
def api_parent_activity(did):
    """Báo cáo hoạt động 1 ngày: tổng giây theo nhóm + danh sách cửa sổ gộp (giống DuAnAI)."""
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return jsonify({"error": "Không có quyền với máy này."}), 403
    date = "".join(ch for ch in request.args.get("date", "") if ch.isdigit() or ch == "-")[:10] or vn_date()
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
    new_alerts = [a for a in (data.get("alerts") or []) if isinstance(a, dict)][:10]
    updates: Dict[str, Any] = {"last_seen": now(), "status": status}
    acks = [a for a in (data.get("acks") or []) if isinstance(a, dict)][:10]
    if acks:
        updates["acks"] = (list(reversed(acks)) + list(dev.get("acks") or []))[:10]
    batch = data.get("activity")
    if isinstance(batch, dict) and batch.get("items"):
        items = classify_items([i for i in batch["items"] if isinstance(i, dict)][:25])
        db.push(f"activity/{did}/{vn_date(batch.get('end'))}",
                {"start": int(batch.get("start", 0) or 0), "end": int(batch.get("end", 0) or 0), "items": items})
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
    chats = chats_of_device(did)
    if not chats:
        return
    cooldowns = dev.get("tg_cooldown") or {}
    name = html.escape(dev.get("name") or did)
    sent_types = {}
    for a in alerts:
        typ = fb_key(str(a.get("type", "GENERIC")))[:30] or "generic"
        if now() - int(cooldowns.get(typ, 0) or 0) < ALERT_COOLDOWN_S or typ in sent_types:
            continue
        sent_types[typ] = now()
        msg = f"🚨 <b>{name}</b>\n{html.escape(str(a.get('message', '')))}\n<i>{html.escape(str(a.get('time', '')))}</i>"
        for chat in chats:
            tg_send(chat, msg)
    if sent_types:
        db.update(f"devices/{did}/tg_cooldown", sent_types)


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
    if chat_id and chat_id in chats_of_device(did):
        tg_api("sendPhoto", chat_id=chat_id, caption=f"📸 {dev.get('name') or did}", photo_bytes=base64.b64decode(b64))
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


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "db": "firebase" if os.environ.get("FIREBASE_DB_URL") else "local-json",
                    "telegram": bool(TG_TOKEN), "google_login": bool(GOOGLE_CLIENT_ID),
                    "password_login": ALLOW_PASSWORD_LOGIN})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
