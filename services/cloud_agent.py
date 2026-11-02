# -*- coding: utf-8 -*-
"""
Agent kết nối Cloud (services/cloud_agent.py) — chạy nền trong FocusDeskLauncher / trang_web.py.

- Chỉ GỌI RA (HTTPS) tới bản cloud trên Vercel: không mở cổng, không cần IP tĩnh, xuyên NAT/Wi-Fi gia đình.
- Máy KHÔNG giữ token bot Telegram. Chỉ giữ device_id + device_token riêng (device_identity.json, gitignore).
- HEARTBEAT linh hoạt (30s bình thường, 5s khi phụ huynh đang xem, báo NGAY khi có cảnh báo mới): gửi chỉ số tóm tắt + cảnh báo mới, nhận lệnh của phụ huynh và thực thi.
- Ảnh camera chỉ gửi khi phụ huynh bấm "Chụp 1 ảnh" (lệnh snapshot), không stream video lên cloud.

Bật bằng cách đặt cloud_url trong secrets.local.json hoặc biến môi trường FOCUSDESK_CLOUD_URL.
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
        self._acks: List[Dict[str, Any]] = []
        self._urgent = False
        from services.activity_tracker import ActivityTracker

        self.tracker = ActivityTracker(batch_s=30.0)
        self._monitor = None          # bộ giám sát "luôn bật" do agent quản lý (khi học sinh chưa đăng nhập)
        self._last_monitor_check = 0.0
        self._last_auto_snap_req = float((load_runtime_state().get("auto_snapshot_request") or {}).get("t", 0) or 0)
        self._last_auto_snap_sent = 0.0

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
        try:
            from services.live_view import live_info

            lv = live_info()
        except Exception:
            lv = {}
        return {
            "live": {k: lv.get(k) for k in ("active", "lan_url", "public_url", "expires", "note")},
            "rules": {"apps": cfg.get("bad_apps", []), "domains": cfg.get("bad_urls", []),
                      "keywords": cfg.get("bad_tabs", []), "youtube_mode": cfg.get("youtube_mode", "keyword"),
                      "youtube_keywords": cfg.get("youtube_keywords") or [],
                      "youtube_shorts": bool(cfg.get("youtube_block_shorts", True))},
            "rules_ver": int(cfg.get("rules_ver", 0) or 0),
            "monitor_always": bool(cfg.get("monitor_always", False)),
            "monitoring": bool(st.get("monitoring_active")),
            "dns_block": cfg.get("dns_block_status", ""),
            "now": dict(self.tracker.current),
            "break": {k: (st.get("toilet_break") or {}).get(k) for k in ("active", "start", "left_t", "duration_s")},
            "phone_box": {**{k: (st.get("phone_box") or {}).get(k) for k in ("connected", "phone", "lid", "lock",
                                                                            "unlock_left", "box_id")},
                          "enabled": bool(cfg.get("phone_box_enabled"))},
            "open_apps": self.tracker.open_apps,
            "hostname": socket.gethostname(),
            "version": AGENT_VERSION,
            "role": st.get("current_role", "?"),
            "ai_seconds": int(st.get("ai_seconds", 0) or 0),
            # Giờ AI tách theo nguồn (app AI trên máy + web AI) và app AI đang chạy
            # Dạng list (không dùng tên nguồn làm khóa: Firebase cấm dấu chấm như 'chatgpt.com')
            "ai_sources": [{"name": str(k)[:60], "seconds": int(v)} for k, v in
                           sorted((st.get("ai_sources") or {}).items(), key=lambda kv: -kv[1])[:8]],
            "ai_running": list(st.get("ai_running") or [])[:8],
            "max_ai_minutes": cfg.get("max_ai_minutes"),
            "top_apps": [{"name": k, "seconds": v} for k, v in top_apps],
            "posture": {
                "camera": bool(pt.get("camera_connected")),
                "cfi": pt.get("cfi_score"),
                "state": pt.get("activity_state"),
                "state_vi": pt.get("activity_state_vi"),
                "distance_cm": pt.get("distance_cm"),
                "held_object": pt.get("held_object"),
                "action": pt.get("action"),
                "camera_score": (pt.get("camera_setup") or {}).get("score"),
                "camera_hint": ((pt.get("camera_setup") or {}).get("hints") or [""])[0],
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
        batch = self.tracker.drain_if_due()
        events = self.tracker.drain_events()
        acks, self._acks = self._acks, []
        try:
            res = self._post("/api/agent/heartbeat", {"status": self._collect_status(), "alerts": alerts,
                                                      "activity": batch, "acks": acks, "events": events})
        except Exception:
            if batch:
                self.tracker.requeue(batch)  # gửi lỗi -> giữ lại số liệu cho lần sau
            if events:
                self.tracker.requeue_events(events)
            self._acks = acks + self._acks
            raise
        if res.get("rules"):
            self._apply_rules(res["rules"])
        self.heartbeat_s = int(res.get("heartbeat_s", self.heartbeat_s) or 30)
        # Phụ huynh đang xem -> gom báo cáo hoạt động 10s/lô cho gần thời gian thực; bình thường 30s
        self.tracker.batch_s = 10.0 if self.heartbeat_s <= 10 else 30.0
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
        ok, msg = True, ""
        try:
            if ctype == "lock":
                update_runtime_state(remote_lock_requested=True)
                if _launcher_running():
                    msg = "Đã chuyển máy về Màn hình Khóa FocusDesk"
                else:
                    import ctypes

                    ctypes.windll.user32.LockWorkStation()
                    msg = "Đã khóa Windows (app FocusDesk không chạy nên dùng khóa của Windows)"
            elif ctype == "reset_ai":
                update_runtime_state(ai_seconds=0, ai_reset_at=time.time())
                msg = "Đã đặt lại giờ dùng AI về 0"
            elif ctype == "reset_stats":
                # Phụ huynh bấm "Xóa cảnh báo & bộ đếm" trên web: giống nút Đặt lại trong trang Phụ huynh của máy
                update_runtime_state(alerts=[], app_usage={}, stats_reset_at=time.time())
                self._sent_alert_keys = []
                msg = "Đã xóa cảnh báo và bộ đếm giờ dùng ứng dụng trên máy"
            elif ctype in ("ai_on", "ai_off"):
                from services.posture_controller import posture_manager

                cfg = load_config()
                cfg["enable_posture_ai"] = ctype == "ai_on"
                save_config(cfg)
                if ctype == "ai_on":
                    ok = posture_manager.start(force_enable=True)
                    msg = "Camera AI đã bật" if ok else "Không bật được Camera AI (thiếu webcam/thư viện?)"
                else:
                    posture_manager.stop()
                    msg = "Camera AI đã tắt"
            elif ctype == "recalibrate":
                from services.posture_controller import posture_manager

                posture_manager.recalibrate()
                msg = "Đang hiệu chỉnh lại tư thế 5 giây (ngồi thẳng, nhìn màn hình)"
            elif ctype == "live_on":
                from services.live_view import start_live
                from services.posture_controller import posture_manager

                posture_manager.start(force_enable=True)
                info = start_live(_lan_ip())
                msg = "Đã bật xem trực tiếp" + ("" if info.get("public_url") else f" (chỉ trong Wi-Fi nhà: {info.get('note')})")
            elif ctype == "live_off":
                from services.live_view import stop_live

                stop_live()
                msg = "Đã tắt xem trực tiếp"
            elif ctype in ("box_unlock", "box_unlock_30", "box_unlock_min", "box_lock"):
                from services.phone_box import get_box

                box = get_box()
                minutes = 30 if ctype == "box_unlock_30" else int(cmd.get("minutes") or 0) if ctype == "box_unlock_min" else 0
                minutes = max(0, min(240, minutes))
                if ctype == "box_lock":
                    r = box.lock()
                else:
                    r = box.unlock(minutes * 60)
                ok = bool(r.get("ok"))
                if not ok:
                    msg = f"Không điều khiển được hộp: {r.get('err')}"
                elif ctype == "box_lock":
                    msg = "Đã khóa hộp điện thoại"
                elif minutes:
                    msg = f"Đã mở hộp điện thoại {minutes} phút (tự khóa lại khi nắp đóng)"
                else:
                    msg = "Đã mở hộp điện thoại"
            elif ctype == "snapshot":
                ok, msg = self._send_snapshot(cmd.get("chat_id") or "")
            else:
                ok, msg = False, "Lệnh không hỗ trợ"
        except Exception as exc:
            ok, msg = False, f"Lỗi: {exc}"
            safe_log(f"[CLOUD] Lỗi thực thi lệnh {ctype}: {exc}")
        self._acks.append({"id": cmd.get("id", ""), "type": ctype, "ok": bool(ok), "msg": msg[:200],
                           "time": time.strftime("%H:%M:%S")})
        self._urgent = True  # báo kết quả ngay cho phụ huynh

    def _tunnel_keeper(self) -> None:
        try:
            from services.live_view import ensure_tunnel, find_cloudflared
        except Exception:
            return
        while not _stop.is_set():
            if find_cloudflared():
                ensure_tunnel()
            _stop.wait(60)

    def _ensure_monitor(self) -> None:
        """
        Giám sát "luôn bật": chặn game / web cấm + báo phụ huynh ngay cả khi học sinh chưa đăng nhập app 3 cấp.
        Khi học sinh đăng nhập, app 3 cấp tự chạy bộ giám sát riêng -> agent nhường để không chạy trùng.
        """
        try:
            want = bool(load_config().get("monitor_always", False)) and load_runtime_state().get("current_role") != "student"
            running = self._monitor is not None and self._monitor.dang_chay
            if want and not running:
                from services.giam_sat_worker import BoGiamSatThongMinh

                self._monitor = BoGiamSatThongMinh()
                self._monitor.bat_dau()
                safe_log("[CLOUD] Bật giám sát luôn-bật (học sinh chưa đăng nhập).")
            elif not want and running:
                self._monitor.dung_lai()
                self._monitor = None
        except Exception as exc:
            safe_log(f"[CLOUD] Lỗi bộ giám sát luôn-bật: {exc}")

    def _apply_rules(self, rules: Dict[str, Any]) -> None:
        """Cấu hình chặn từ xa -> shared_config (giam_sat_worker đọc lại mỗi vòng quét)."""
        def clean(v):
            return [str(x).strip().lower() for x in (v or []) if str(x).strip()][:200]

        cfg = load_config()
        cfg["bad_apps"] = clean(rules.get("apps"))
        cfg["bad_urls"] = [d.replace("https://", "").replace("http://", "").split("/")[0] for d in clean(rules.get("domains"))]
        cfg["bad_tabs"] = clean(rules.get("keywords"))
        cfg["rules_ver"] = int(rules.get("ver", 0) or 0)
        if "monitor_always" in rules:
            cfg["monitor_always"] = bool(rules.get("monitor_always"))
        if rules.get("youtube_mode") in ("keyword", "block", "allow"):
            cfg["youtube_mode"] = rules["youtube_mode"]
        if isinstance(rules.get("youtube_keywords"), list):
            cfg["youtube_keywords"] = clean(rules.get("youtube_keywords"))
        if "youtube_shorts" in rules:
            cfg["youtube_block_shorts"] = bool(rules.get("youtube_shorts"))
        # Giới hạn AI phụ huynh đặt trên web (0 = cấm AI) + cách xử lý khi hết giờ / khi nhờ AI làm game
        if "max_ai_minutes" in rules:
            try:
                cfg["max_ai_minutes"] = max(0, min(240, int(rules["max_ai_minutes"])))
            except (TypeError, ValueError):
                pass
        if isinstance(rules.get("blocked_pages"), list):
            cfg["blocked_pages"] = [{"d": str(r.get("d", ""))[:80].lower(), "t": str(r.get("t", ""))[:100],
                                     "u": str(r.get("u", ""))[:120], "why": str(r.get("why", ""))[:60]}
                                    for r in rules["blocked_pages"] if isinstance(r, dict)][:300]
        if rules.get("ai_over_action") in ("block", "warn"):
            cfg["ai_over_action"] = rules["ai_over_action"]
        if rules.get("ai_game_action") in ("block", "warn", "off"):
            cfg["ai_game_action"] = rules["ai_game_action"]
        dns_on = bool(rules.get("dns_block", True))
        from services.hosts_blocker import sync_hosts_for_role

        # Chặn DNS tận gốc KHÔNG áp cho YouTube khi đang ở chế độ "chặn theo từ khóa" (để bài giảng vẫn vào được)
        yt_doms = ("youtube.com", "youtu.be", "m.youtube.com", "www.youtube.com")
        dns_list = [d for d in cfg["bad_urls"] if cfg.get("youtube_mode", "keyword") == "block" or d not in yt_doms]
        cfg["dns_block_enabled"] = dns_on
        ok_dns, dns_msg = sync_hosts_for_role(cfg=cfg)      # chỉ chặn DNS khi học sinh đang dùng máy
        if dns_on and dns_list and "Đã trả" in dns_msg:
            dns_msg = f"{len(dns_list)} tên miền — chặn DNS khi vào chế độ học sinh"
        cfg["dns_block_status"] = dns_msg
        save_config(cfg)
        self._last_monitor_check = 0.0
        safe_log(f"[CLOUD] Đã áp dụng cấu hình chặn mới (ver {cfg['rules_ver']}).")
        self._acks.append({"id": "", "type": "rules", "ok": True, "time": time.strftime("%H:%M:%S"),
                           "msg": f"Đã áp dụng: {len(cfg['bad_apps'])} app, {len(cfg['bad_urls'])} trang web, "
                                  f"{len(cfg['bad_tabs'])} từ khóa · {len(cfg.get('blocked_pages') or [])} link · AI {cfg.get('max_ai_minutes', 15)} phút/ngày · "
                                  f"{dns_msg} · giám sát "
                                  f"{'luôn bật' if cfg.get('monitor_always') else 'khi học sinh đăng nhập'}"})
        self._urgent = True

    def _auto_snapshot(self) -> None:
        """Camera AI phát hiện học sinh cầm điện thoại/đồ chơi -> gửi 1 ảnh gốc cho MỌI phụ huynh (tối đa 1 ảnh/5 phút)."""
        req = load_runtime_state().get("auto_snapshot_request") or {}
        t = float(req.get("t", 0) or 0)
        if not t or t <= self._last_auto_snap_req:
            return
        self._last_auto_snap_req = t
        if time.time() - t > 60 or time.time() - self._last_auto_snap_sent < 300:
            return
        from services.posture_controller import get_latest_raw_frame

        jpg = get_latest_raw_frame("full", max_age_sec=5.0)
        if not jpg:
            return
        self._last_auto_snap_sent = time.time()
        try:
            self._post("/api/agent/snapshot", {"jpg_b64": base64.b64encode(jpg).decode("ascii"), "broadcast": True,
                                               "caption": "📸 Bằng chứng: " + str(req.get("reason", ""))[:180]},
                       timeout=20.0)
            safe_log("[CLOUD] Đã gửi ảnh bằng chứng (nghịch đồ) cho phụ huynh.")
        except Exception as exc:
            safe_log(f"[CLOUD] Gửi ảnh bằng chứng lỗi: {exc}")

    def _send_snapshot(self, chat_id: str) -> None:
        from services.posture_controller import get_latest_raw_frame, has_live_posture_stream, posture_manager

        started_here = False
        if not has_live_posture_stream():
            started_here = posture_manager.start(force_enable=True)
            for _ in range(40):  # chờ tối đa ~10s cho Camera AI ra khung hình đầu tiên
                if has_live_posture_stream():
                    break
                time.sleep(0.25)
            time.sleep(1.0)
        jpg = get_latest_raw_frame("full") if has_live_posture_stream() else b""  # ảnh gốc full, không lớp AI
        if started_here:
            posture_manager.stop()
        if not jpg:
            safe_log("[CLOUD] Không có khung hình camera để gửi.")
            return False, "Không có khung hình camera (webcam bận/không có?)"
        # Ảnh đi qua Vercel chuyển thẳng sang Telegram, KHÔNG lưu vào Firebase
        self._post("/api/agent/snapshot", {"jpg_b64": base64.b64encode(jpg).decode("ascii"), "chat_id": chat_id},
                   timeout=20.0)
        return True, f"Đã gửi ảnh ({len(jpg) // 1024} KB) qua Telegram"

    # ---------------- Vòng lặp ----------------
    def _has_pending_event(self) -> bool:
        """Sự kiện đặc biệt cần báo NGAY: có cảnh báo mới chưa gửi (game, AI quá giờ, tư thế, đăng nhập sai...)."""
        alerts = (load_runtime_state().get("alerts") or [])[:5]
        if self.tracker.urgent:      # vừa mở ứng dụng / trang web mới -> báo ngay (phụ huynh thấy gần thời gian thực)
            return True
        return any(f"{a.get('time')}|{a.get('type')}|{a.get('message')}" not in self._sent_alert_keys for a in alerts)

    def run(self) -> None:
        """
        Báo theo chu kỳ server chỉ định (30s bình thường / 5s khi phụ huynh đang xem),
        nhưng quét sự kiện cục bộ mỗi 1s để báo NGAY khi có cảnh báo mới (cách nhau tối thiểu 1s).
        """
        first = True
        backoff = 5
        last_sent = 0.0
        self.tracker.start()
        # Mở sẵn đường truyền Internet cho camera (nếu đã cài cloudflared) -> bấm "Xem trực tiếp" hiện hình nhanh
        threading.Thread(target=self._tunnel_keeper, name="TunnelKeeper", daemon=True).start()
        while not _stop.is_set():
            try:
                if time.time() - self._last_monitor_check >= 5.0:
                    self._last_monitor_check = time.time()
                    self._ensure_monitor()
                if self.ident.get("device_id") and load_runtime_state().get("cloud_pair_refresh"):
                    update_runtime_state(cloud_pair_refresh=False)
                    self.refresh_pair_code()
                    _publish_local(self.ident)
                if self.ident.get("paired"):
                    self._auto_snapshot()
                due = time.time() - last_sent >= self.heartbeat_s
                urgent = (not first) and time.time() - last_sent >= 1.0 and (self._urgent or self._has_pending_event())
                if first or due or urgent:
                    self.ensure_registered()
                    self._urgent = False
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


def _launcher_running() -> bool:
    """App khóa máy FocusDesk (exe hoặc he_thong_quan_ly_3_cap.py) có đang chạy để xử lý lệnh khóa không."""
    try:
        import psutil

        for p in psutil.process_iter(["name", "cmdline"]):
            name = (p.info.get("name") or "").lower()
            cmd = " ".join(p.info.get("cmdline") or []).lower()
            if name == "focusdesklauncher.exe" or "he_thong_quan_ly_3_cap" in cmd:
                return True
    except Exception:
        pass
    return False


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


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


def notify_shutdown(reason: str, timeout: float = 4.0) -> Optional[bool]:
    """Báo web phụ huynh + Telegram 'FocusDesk đã tắt' NGAY (không chờ vòng heartbeat).
    None = chưa cấu hình cloud / chưa đăng ký máy; True = đã gửi; False = gửi lỗi (mất mạng...)."""
    url = _cloud_url()
    ident = _load_identity()
    if not url or not ident.get("device_id") or not ident.get("device_token") or "--self-test" in sys.argv:
        return None
    _stop.set()      # vòng heartbeat không gửi thêm gì sau thông báo tắt
    try:
        import requests

        msg = f"⏻ FocusDesk đã tắt trên máy {socket.gethostname()} — {reason}"
        r = requests.post(url + "/api/agent/heartbeat",
                          json={"shutdown": True, "reason": reason,
                                "alerts": [{"time": time.strftime("%H:%M:%S"), "type": "POWER", "message": msg}]},
                          headers={"Authorization": f"Device {ident['device_id']}:{ident['device_token']}"},
                          timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def stop_cloud_agent() -> None:
    _stop.set()
    try:
        from services.live_view import shutdown_tunnel

        shutdown_tunnel()
    except Exception:
        pass
