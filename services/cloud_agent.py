# -*- coding: utf-8 -*-
"""
Agent kết nối Cloud (services/cloud_agent.py) — chạy nền trong SmartDeskLauncher / trang_web.py.

- Chỉ GỌI RA (HTTPS) tới bản cloud trên Vercel: không mở cổng, không cần IP tĩnh, xuyên NAT/Wi-Fi gia đình.
- Máy KHÔNG giữ token bot Telegram. Chỉ giữ device_id + device_token riêng (device_identity.json, gitignore).
- HEARTBEAT linh hoạt (30s bình thường, 5s khi phụ huynh đang xem, báo NGAY khi có cảnh báo mới): gửi chỉ số tóm tắt + cảnh báo mới, nhận lệnh của phụ huynh và thực thi.
- Ảnh camera chỉ gửi khi phụ huynh bấm "Chụp 1 ảnh" (lệnh snapshot), không stream video lên cloud.

Bật bằng cách đặt cloud_url trong secrets.local.json hoặc biến môi trường SMARTDESK_CLOUD_URL.
"""

import base64
import json
import os
import socket
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from core.config_manager import get_app_dir, load_config, load_runtime_state, safe_log, save_config, update_runtime_state

IDENTITY_FILE = os.path.join(get_app_dir(), "device_identity.json")
AGENT_VERSION = "1.0"

_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _cloud_url() -> str:
    try:
        from core.secrets_store import get_secret

        url = get_secret("cloud_url") or ""
    except Exception:
        url = ""
    if not url:
        url = str((load_config().get("cloud") or {}).get("url", ""))
    return url.rstrip("/")


def _load_identity() -> Dict[str, Any]:
    try:
        with open(IDENTITY_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_identity(ident: Dict[str, Any]) -> None:
    tmp = IDENTITY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ident, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, IDENTITY_FILE)


def _publish_local(ident: Dict[str, Any], paired: Optional[bool] = None, error: str = "") -> None:
    """Ghi trạng thái cloud vào runtime_state để web nội bộ (:5000/ket-noi-cloud) hiển thị mã ghép."""
    info = {
        "enabled": True,
        "url": _cloud_url(),
        "device_id": ident.get("device_id", ""),
        "pair_code": ident.get("pair_code", ""),
        "pair_expires": ident.get("pair_expires", 0),
        "paired": bool(ident.get("paired")) if paired is None else paired,
        "last_ok": int(time.time()) if not error else (load_runtime_state().get("cloud") or {}).get("last_ok", 0),
        "error": error,
    }
    update_runtime_state(cloud=info)


class CloudAgent:
    def __init__(self, base_url: str):
        self.base = base_url
        self.ident = _load_identity()
        self.heartbeat_s = 30
        self._sent_alert_keys: List[str] = []
        self._session = requests.Session()

    # ---------------- HTTP ----------------
    def _auth(self) -> Dict[str, str]:
        return {"Authorization": f"Device {self.ident['device_id']}:{self.ident['device_token']}"}

    def _post(self, path: str, body: Dict[str, Any], auth: bool = True, timeout: float = 10.0) -> Dict[str, Any]:
        r = self._session.post(self.base + path, json=body, headers=self._auth() if auth else {}, timeout=timeout)
        if r.status_code == 401:
            raise PermissionError("unauthorized")
        r.raise_for_status()
        return r.json()

    # ---------------- Đăng ký / mã ghép ----------------
    def ensure_registered(self) -> None:
        if self.ident.get("device_id") and self.ident.get("device_token"):
            return
        res = self._post("/api/agent/register", {"hostname": socket.gethostname(), "version": AGENT_VERSION}, auth=False)
        self.ident = {"device_id": res["device_id"], "device_token": res["device_token"], "base": self.base,
                      "pair_code": res["pair_code"], "pair_expires": res["pair_expires"], "paired": False}
        _save_identity(self.ident)
        safe_log(f"[CLOUD] Đã đăng ký máy {res['device_id']}. MÃ GHÉP cho phụ huynh: {res['pair_code']} (15 phút)")

    def refresh_pair_code(self) -> None:
        res = self._post("/api/agent/pair_code", {})
        self.ident.update(pair_code=res["pair_code"], pair_expires=res["pair_expires"])
        _save_identity(self.ident)
        safe_log(f"[CLOUD] Mã ghép mới: {res['pair_code']}")

    # ---------------- Heartbeat ----------------
    def _collect_status(self) -> Dict[str, Any]:
        st = load_runtime_state()
        cfg = load_config()
        pt = st.get("posture_telemetry") or {}
        usage = st.get("app_usage") or {}
        top_apps = sorted(usage.items(), key=lambda kv: -float(kv[1] or 0))[:5]
        return {
            "hostname": socket.gethostname(),
            "version": AGENT_VERSION,
            "role": st.get("current_role", "?"),
            "monitoring": bool(st.get("monitoring_active")),
            "ai_seconds": int(st.get("ai_seconds", 0) or 0),
            "max_ai_minutes": cfg.get("max_ai_minutes"),
            "top_apps": [{"name": k, "seconds": v} for k, v in top_apps],
            "posture": {
                "camera": bool(pt.get("camera_connected")),
                "cfi": pt.get("cfi_score"),
                "state": pt.get("activity_state"),
                "state_vi": pt.get("activity_state_vi"),
                "distance_cm": pt.get("distance_cm"),
                "alerts": (pt.get("alerts") or [])[:3],
            },
        }

    def _new_alerts(self) -> List[Dict[str, Any]]:
        alerts = (load_runtime_state().get("alerts") or [])[:20]
        fresh = []
        for a in reversed(alerts):  # cũ -> mới
            key = f"{a.get('time')}|{a.get('type')}|{a.get('message')}"
            if key not in self._sent_alert_keys:
                self._sent_alert_keys.append(key)
                fresh.append({"time": a.get("time"), "type": a.get("type"), "message": a.get("message")})
        self._sent_alert_keys = self._sent_alert_keys[-100:]
        return fresh

    def heartbeat(self, first: bool = False) -> None:
        alerts = self._new_alerts()
        if first:
            alerts = []  # không bắn lại cảnh báo cũ khi vừa khởi động
        res = self._post("/api/agent/heartbeat", {"status": self._collect_status(), "alerts": alerts})
        self.heartbeat_s = int(res.get("heartbeat_s", self.heartbeat_s) or 30)
        paired = bool(res.get("paired"))
        if paired != bool(self.ident.get("paired")):
            self.ident["paired"] = paired
            _save_identity(self.ident)
        if not paired and int(self.ident.get("pair_expires", 0) or 0) < time.time() + 30:
            self.refresh_pair_code()
        _publish_local(self.ident, paired=paired)
        for cmd in res.get("commands") or []:
            self.execute(cmd)

    # ---------------- Thực thi lệnh ----------------
    def execute(self, cmd: Dict[str, Any]) -> None:
        ctype = cmd.get("type")
        safe_log(f"[CLOUD] Nhận lệnh từ phụ huynh: {ctype}")
        try:
            if ctype == "lock":
                update_runtime_state(remote_lock_requested=True)
            elif ctype == "reset_ai":
                update_runtime_state(ai_seconds=0)
            elif ctype in ("ai_on", "ai_off"):
                from services.posture_controller import posture_manager

                cfg = load_config()
                cfg["enable_posture_ai"] = ctype == "ai_on"
                save_config(cfg)
                posture_manager.start(force_enable=True) if ctype == "ai_on" else posture_manager.stop()
            elif ctype == "recalibrate":
                from services.posture_controller import posture_manager

                posture_manager.recalibrate()
            elif ctype == "snapshot":
                self._send_snapshot(cmd.get("chat_id") or "")
        except Exception as exc:
            safe_log(f"[CLOUD] Lỗi thực thi lệnh {ctype}: {exc}")

    def _send_snapshot(self, chat_id: str) -> None:
        from services.posture_controller import get_latest_posture_frame, has_live_posture_stream, posture_manager

        started_here = False
        if not has_live_posture_stream():
            started_here = posture_manager.start(force_enable=True)
            for _ in range(40):  # chờ tối đa ~10s cho Camera AI ra khung hình đầu tiên
                if has_live_posture_stream():
                    break
                time.sleep(0.25)
            time.sleep(1.0)
        jpg = get_latest_posture_frame() if has_live_posture_stream() else b""
        if started_here:
            posture_manager.stop()
        if not jpg:
            safe_log("[CLOUD] Không có khung hình camera để gửi.")
            return
        self._post("/api/agent/snapshot", {"jpg_b64": base64.b64encode(jpg).decode("ascii"), "chat_id": chat_id},
                   timeout=20.0)

    # ---------------- Vòng lặp ----------------
    def _has_pending_event(self) -> bool:
        """Sự kiện đặc biệt cần báo NGAY: có cảnh báo mới chưa gửi (game, AI quá giờ, tư thế, đăng nhập sai...)."""
        alerts = (load_runtime_state().get("alerts") or [])[:5]
        return any(f"{a.get('time')}|{a.get('type')}|{a.get('message')}" not in self._sent_alert_keys for a in alerts)

    def run(self) -> None:
        """
        Báo theo chu kỳ server chỉ định (30s bình thường / 5s khi phụ huynh đang xem),
        nhưng quét sự kiện cục bộ mỗi 1s để báo NGAY khi có cảnh báo mới (cách nhau tối thiểu 3s).
        """
        first = True
        backoff = 5
        last_sent = 0.0
        while not _stop.is_set():
            try:
                due = time.time() - last_sent >= self.heartbeat_s
                urgent = (not first) and time.time() - last_sent >= 3.0 and self._has_pending_event()
                if first or due or urgent:
                    self.ensure_registered()
                    self.heartbeat(first=first)
                    if urgent:
                        safe_log("[CLOUD] Có cảnh báo mới -> đã báo ngay cho phụ huynh.")
                    first = False
                    backoff = 5
                    last_sent = time.time()
                _stop.wait(1.0)
            except PermissionError:
                safe_log("[CLOUD] Máy bị từ chối (token sai / đã xóa trên cloud) -> đăng ký lại.")
                self.ident = {}
                try:
                    os.remove(IDENTITY_FILE)
                except OSError:
                    pass
                _stop.wait(3)
            except Exception as exc:
                _publish_local(self.ident, error=str(exc)[:200])
                _stop.wait(backoff)
                backoff = min(120, backoff * 2)


def start_cloud_agent() -> bool:
    """Khởi động agent nền nếu đã cấu hình cloud_url. Gọi nhiều lần vẫn an toàn."""
    global _thread
    url = _cloud_url()
    if not url or "--self-test" in sys.argv:
        return False
    if _thread is not None and _thread.is_alive():
        return True
    _stop.clear()
    agent = CloudAgent(url)
    _thread = threading.Thread(target=agent.run, name="CloudAgentThread", daemon=True)
    _thread.start()
    safe_log(f"[CLOUD] Agent kết nối {url} đã chạy.")
    return True


def stop_cloud_agent() -> None:
    _stop.set()
