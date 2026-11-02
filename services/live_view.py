# -*- coding: utf-8 -*-
"""
Xem camera TRỰC TIẾP không đi qua Firebase / Vercel (services/live_view.py).

Hình ảnh đi thẳng: Máy học sinh (trang_web.py :5000) ──▶ phụ huynh, theo 1 trong 2 đường:
  1. Cùng Wi-Fi nhà:  http://<IP-LAN>:5000/live?t=<token>
  2. Ở ngoài:         https://<ngẫu-nhiên>.trycloudflare.com/live?t=<token>
     (Cloudflare Quick Tunnel - miễn phí, không cần tài khoản; chỉ bật khi phụ huynh bấm "Xem trực tiếp",
      tự tắt sau LIVE_TTL_S giây). Cần cài cloudflared:  winget install --id Cloudflare.cloudflared
Cloud (Vercel/Firebase) chỉ nhận ĐƯỜNG LINK + token (vài chục byte), không nhận khung hình.

Bảo vệ: token ngẫu nhiên 128-bit đổi mỗi lần bật; qua tunnel CHỈ mở /live, /video_feed, /snapshot.jpg
(mọi API khác của web nội bộ bị chặn 403 - xem trang_web.py).
"""

import glob
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from core.config_manager import get_app_dir, load_config, load_runtime_state, safe_log, update_runtime_state

LIVE_TTL_S = 15 * 60
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_lock = threading.Lock()
_proc: Optional[subprocess.Popen] = None


def find_cloudflared() -> Optional[str]:
    cands = [shutil.which("cloudflared"), os.path.join(get_app_dir(), "tools", "cloudflared.exe"),
             os.path.join(os.path.dirname(get_app_dir()), "tools", "cloudflared.exe")]
    cands += glob.glob(os.path.expandvars(r"%ProgramFiles(x86)%\cloudflared\cloudflared.exe"))
    cands += glob.glob(os.path.expandvars(r"%ProgramFiles%\cloudflared\cloudflared.exe"))
    cands += glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Cloudflare.cloudflared*\cloudflared.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def live_info() -> Dict[str, Any]:
    info = (load_runtime_state().get("live") or {})
    if info.get("active") and int(info.get("expires", 0) or 0) < time.time():
        stop_live()
        return {"active": False}
    return info


def token_valid(tok: str) -> bool:
    info = live_info()
    return bool(info.get("active") and tok and secrets.compare_digest(str(info.get("token", "")), tok))


def ensure_tunnel() -> str:
    """
    Giữ SẴN 1 Cloudflare Quick Tunnel suốt phiên chạy (mở 1 lần mất 5-25s) để bấm "Xem trực tiếp"
    hiện hình gần như ngay. Hình chỉ chảy khi có token hợp lệ, nên tunnel mở sẵn vẫn không lộ gì.
    Trả về địa chỉ gốc https://xxx.trycloudflare.com hoặc "" nếu không có cloudflared.
    """
    global _proc
    with _lock:
        tun = load_runtime_state().get("tunnel") or {}
        if _proc is not None and _proc.poll() is None and tun.get("base"):
            return tun["base"]
        cf = find_cloudflared()
        if not cf:
            update_runtime_state(tunnel={"base": "", "note": "Chưa cài cloudflared"})
            return ""
        port = int(load_config().get("web_port", 5000))
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            _proc = subprocess.Popen([cf, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="ignore", creationflags=flags)
            deadline = time.time() + 30
            base = ""
            while time.time() < deadline and _proc.poll() is None:
                m = _URL_RE.search(_proc.stdout.readline() or "")
                if m:
                    base = m.group(0)
                    break
            if base:
                threading.Thread(target=_drain, args=(_proc,), daemon=True).start()
                safe_log(f"[LIVE] Đường truyền Internet sẵn sàng: {base}")
            update_runtime_state(tunnel={"base": base, "note": "" if base else "cloudflared chưa trả về link"})
            return base
        except Exception as exc:
            update_runtime_state(tunnel={"base": "", "note": f"Lỗi cloudflared: {exc}"})
            return ""


def start_live(lan_ip: str) -> Dict[str, Any]:
    """Bật xem trực tiếp: tạo token mới (15 phút) trên tunnel đã mở sẵn. Trả về thông tin link."""
    base = ensure_tunnel()
    port = int(load_config().get("web_port", 5000))
    token = secrets.token_urlsafe(16)
    info: Dict[str, Any] = {"active": True, "token": token, "expires": int(time.time()) + LIVE_TTL_S,
                            "lan_url": f"http://{lan_ip}:{port}/live?t={token}",
                            "public_url": f"{base}/live?t={token}" if base else "",
                            "note": "" if base else ((load_runtime_state().get("tunnel") or {}).get("note")
                                                     or "Chỉ xem được trong Wi-Fi nhà")}
    update_runtime_state(live=info)
    safe_log(f"[LIVE] Bật xem trực tiếp: LAN={info['lan_url']} | Internet={info['public_url'] or '(không có)'}")
    return info


def _drain(p: subprocess.Popen) -> None:
    try:
        for _ in p.stdout:
            pass
    except Exception:
        pass


def stop_live() -> None:
    """Thu hồi token (link cũ hết hiệu lực ngay). Tunnel vẫn giữ để lần sau mở nhanh."""
    update_runtime_state(live={"active": False})


def shutdown_tunnel() -> None:
    global _proc
    with _lock:
        if _proc is not None:
            try:
                _proc.terminate()
                _proc.wait(timeout=3)
            except Exception:
                try:
                    _proc.kill()
                except Exception:
                    pass
            _proc = None
        update_runtime_state(tunnel={"base": ""})
