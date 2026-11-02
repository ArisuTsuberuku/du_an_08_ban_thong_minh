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
    "snapshot": "📸 Chụp 1 ảnh camera",
}


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
<style>
:root{--bg:#0b1220;--card:#131c2e;--line:#22304a;--txt:#e2e8f0;--mut:#94a3b8;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444;--acc:#38bdf8}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--txt)}
.wrap{max-width:1100px;margin:0 auto;padding:16px}a{color:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}
input,button{font:inherit;border-radius:10px;border:1px solid var(--line);padding:10px 12px;background:#0f172a;color:var(--txt)}
button{background:#1e3a5f;cursor:pointer}button:hover{filter:brightness(1.2)}button.danger{background:#5f1e1e}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.mut{color:var(--mut);font-size:13px}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;background:#1f2937}
.on{background:#14532d}.off{background:#3f3f46}.big{font-size:34px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:13px}td{padding:4px 0;border-bottom:1px solid var(--line)}
img.snap{width:100%;border-radius:10px;margin-top:8px}
</style>"""

AUTH_HTML = BASE_CSS + """
<title>Bàn Học Thông Minh - {{ title }}</title><meta name="viewport" content="width=device-width,initial-scale=1">
<div class="wrap" style="max-width:420px;margin-top:8vh">
 <div class="card"><h2>🛡️ Bàn Học Thông Minh</h2><p class="mut">{{ title }} tài khoản phụ huynh</p>
 {% if error %}<p style="color:#f87171">{{ error }}</p>{% endif %}
 <form method="post"><div class="row" style="flex-direction:column;align-items:stretch">
  <input name="email" type="email" placeholder="Email" required>
  <input name="password" type="password" placeholder="Mật khẩu (≥ 8 ký tự)" required minlength="8">
  <button type="submit">{{ title }}</button></div></form>
 <p class="mut">{% if title == 'Đăng nhập' %}Chưa có tài khoản? <a href="/register">Đăng ký</a>{% else %}Đã có tài khoản? <a href="/login">Đăng nhập</a>{% endif %}</p>
 </div></div>"""

DASH_HTML = BASE_CSS + """
<title>Bàn Học Thông Minh</title><meta name="viewport" content="width=device-width,initial-scale=1">
<div class="wrap">
 <div class="row" style="justify-content:space-between"><h2>🛡️ Bàn Học Thông Minh</h2>
  <div class="row"><span class="mut">{{ email }}</span><form method="post" action="/logout"><button>Đăng xuất</button></form></div></div>
 <div class="grid">
  <div class="card"><b>➕ Thêm máy học sinh</b><p class="mut">Mã ghép 6 số hiện trên máy học sinh (app SmartDesk / web nội bộ :5000).</p>
   <div class="row"><input id="pcode" placeholder="Mã 6 số" maxlength="6" style="width:110px"><input id="pname" placeholder="Tên máy (vd: Máy của Minh)">
   <button onclick="pair()">Ghép</button></div><p id="pmsg" class="mut"></p></div>
  <div class="card"><b>💬 Telegram</b><p class="mut">Mỗi phụ huynh liên kết Telegram riêng. Bot gửi cảnh báo của <i>các máy bạn quản lý</i>.</p>
   <div id="chats" class="mut"></div><button onclick="tglink()">Kết nối Telegram</button> <span id="tgmsg" class="mut"></span></div>
 </div>
 <div id="devices" class="grid"></div>
</div>
<script>
const CMDS = Object.fromEntries({{ cmds|tojson }});
async function api(url, body){const r=await fetch(url,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return r.json();}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function pair(){const r=await api('/api/parent/pair',{code:pcode.value.trim(),name:pname.value.trim()});pmsg.textContent=r.message||r.error;load();}
async function tglink(){const r=await api('/api/parent/telegram/link',{});if(r.url){window.open(r.url,'_blank');tgmsg.textContent='Bấm Start trong Telegram để hoàn tất.';}else tgmsg.textContent=r.error;}
async function cmd(did,t){const r=await api(`/api/parent/devices/${did}/command`,{type:t});document.getElementById('msg-'+did).textContent=r.message||r.error;
 if(t==='snapshot'&&r.ok){waitSnap(did,Date.now());}}
async function waitSnap(did,t0){const img=document.getElementById('snap-'+did);
 for(let i=0;i<20;i++){await new Promise(r=>setTimeout(r,2000));const r=await fetch(`/api/parent/devices/${did}/snapshot?after=${Math.floor(t0/1000)-2}`);
  if(r.status===200){img.src=URL.createObjectURL(await r.blob());img.style.display='block';return;}}
 document.getElementById('msg-'+did).textContent='Máy chưa gửi ảnh (offline hoặc Camera AI đang tắt).';}
async function unlink(did){if(confirm('Bỏ quản lý máy này?')){await api(`/api/parent/devices/${did}/unlink`,{});load();}}
async function load(){
 const d=await api('/api/parent/devices');
 chats.innerHTML=(d.chats||[]).length?('Đã liên kết: '+d.chats.map(c=>'<span class="pill on">'+esc(c)+'</span>').join(' ')):'Chưa liên kết Telegram.';
 const box=document.getElementById('devices');
 if(!d.devices||!d.devices.length){box.innerHTML='<div class="card mut">Chưa có máy nào. Nhập mã ghép ở trên.</div>';return;}
 const keepImgs={};document.querySelectorAll('img.snap').forEach(i=>keepImgs[i.id]=i.src);
 box.innerHTML=d.devices.map(v=>{const s=v.status||{},p=s.posture||{};
  const al=(v.alerts||[]).map(a=>`<tr><td class="mut">${esc(a.time)}</td><td>${esc(a.message)}</td></tr>`).join('');
  return `<div class="card"><div class="row" style="justify-content:space-between"><b>${esc(v.name)}</b>
   <span class="pill ${v.online?'on':'off'}">${v.online?'● Trực tuyến':'○ Ngoại tuyến'}</span></div>
   <p class="mut">${esc(s.hostname||'')} · cập nhật ${v.ago}s trước · vai trò: ${esc(s.role||'?')}</p>
   <div class="row"><div><div class="big">${p.cfi!=null?Math.round(p.cfi)+'%':'—'}</div><div class="mut">Điểm tập trung CFI</div></div>
   <div style="margin-left:18px"><div>${esc(p.state_vi||'Camera AI chưa chạy')}</div>
   <div class="mut">${p.distance_cm?('Khoảng cách '+Math.round(p.distance_cm)+'cm · '):''}AI: ${Math.round((s.ai_seconds||0)/60)}/${s.max_ai_minutes||'?'} phút</div></div></div>
   <div class="row" style="margin-top:10px">${Object.entries(CMDS).map(([k,t])=>`<button onclick="cmd('${v.id}','${k}')">${t}</button>`).join('')}</div>
   <p id="msg-${v.id}" class="mut"></p><img id="snap-${v.id}" class="snap" style="display:${keepImgs['snap-'+v.id]?'block':'none'}" src="${keepImgs['snap-'+v.id]||''}">
   <table>${al||'<tr><td class="mut">Chưa có cảnh báo.</td></tr>'}</table>
   <p><button class="danger" onclick="unlink('${v.id}')">Bỏ quản lý</button></p></div>`}).join('');
}
load();setInterval(load,8000);
</script>"""


@app.route("/")
def index():
    return redirect("/dashboard" if current_uid() else "/login")


@app.route("/register", methods=["GET", "POST"])
def register():
    error = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""
        if len(pw) < 8 or "@" not in email:
            error = "Email không hợp lệ hoặc mật khẩu dưới 8 ký tự."
        elif db.get(f"emails/{fb_key(email)}"):
            error = "Email đã được đăng ký."
        else:
            uid = "u_" + secrets.token_hex(8)
            db.set(f"parents/{uid}", {"email": email, "pw_hash": generate_password_hash(pw), "created": now()})
            db.set(f"emails/{fb_key(email)}", uid)
            session.permanent = True
            session["uid"], session["email"] = uid, email
            return redirect("/dashboard")
    return render_template_string(AUTH_HTML, title="Đăng ký", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        uid = db.get(f"emails/{fb_key(email)}")
        parent = db.get(f"parents/{uid}") if uid else None
        if parent and check_password_hash(parent.get("pw_hash", ""), request.form.get("password") or ""):
            session.permanent = True
            session["uid"], session["email"] = uid, email
            return redirect("/dashboard")
        time.sleep(0.5)
        error = "Sai email hoặc mật khẩu."
    return render_template_string(AUTH_HTML, title="Đăng nhập", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template_string(DASH_HTML, email=session.get("email", ""), cmds=list(COMMAND_TYPES.items()))


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
            "alerts": (dev.get("recent_alerts") or [])[:6],
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


@app.route("/api/parent/devices/<did>/snapshot")
@login_required
def api_parent_snapshot(did):
    uid, did = current_uid(), fb_key(did)
    if not parent_owns(uid, did):
        return Response(status=403)
    snap = db.get(f"snapshots/{did}") or {}
    after = int(request.args.get("after", "0") or 0)
    ts = int(snap.get("ts", 0) or 0)
    if not snap.get("b64") or ts < after or now() - ts > SNAPSHOT_TTL_S:
        return Response(status=204)
    return Response(base64.b64decode(snap["b64"]), mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


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
    return jsonify({"commands": out, "heartbeat_s": FAST_HEARTBEAT_S if fast else HEARTBEAT_S,
                    "paired": bool(dev.get("owners"))})


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
    db.set(f"snapshots/{did}", {"b64": b64, "ts": now()})
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
                    "telegram": bool(TG_TOKEN)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
