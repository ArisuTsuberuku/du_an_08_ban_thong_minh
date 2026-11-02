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


def start_live(lan_ip: str) -> Dict[str, Any]:
    """Bật xem trực tiếp: tạo token mới, mở Quick Tunnel nếu có cloudflared. Trả về thông tin link."""
    global _proc
    with _lock:
        port = int(load_config().get("web_port", 5000))
        token = secrets.token_urlsafe(16)
        info: Dict[str, Any] = {"active": True, "token": token, "expires": int(time.time()) + LIVE_TTL_S,
                                "lan_url": f"http://{lan_ip}:{port}/live?t={token}", "public_url": "", "note": ""}
        cf = find_cloudflared()
        if _proc is not None and _proc.poll() is None:
            old = load_runtime_state().get("live") or {}
            info["public_base"] = old.get("public_base", "")
        elif cf:
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                _proc = subprocess.Popen([cf, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="ignore", creationflags=flags)
                deadline = time.time() + 25
                base = ""
                while time.time() < deadline and _proc.poll() is None:
                    line = _proc.stdout.readline()
                    m = _URL_RE.search(line or "")
                    if m:
                        base = m.group(0)
                        break
                info["public_base"] = base
                if not base:
                    info["note"] = "cloudflared chưa trả về link (mạng chặn?)"
                else:
                    threading.Thread(target=_drain, args=(_proc,), daemon=True).start()
            except Exception as exc:
                info["note"] = f"Lỗi cloudflared: {exc}"
        else:
            info["note"] = "Chưa cài cloudflared -> chỉ xem được khi cùng Wi-Fi (winget install Cloudflare.cloudflared)"
        if info.get("public_base"):
            info["public_url"] = f"{info['public_base']}/live?t={token}"
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
        update_runtime_state(live={"active": False})
