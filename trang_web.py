# -*- coding: utf-8 -*-
"""
Bảng Điều Khiển Web Trung Tâm (trang_web.py) - Chạy trên 0.0.0.0:5000 (PC & Điện thoại Android cùng Wi-Fi).
- Bảo toàn 100% cổng API gốc của nhóm: POST /api/nhan_canh_bao nhận {'message': noi_dung}
- Giữ nguyên tiêu đề chứa cụm "Bảng Điều Khiển Bàn Học" để giam_sat.pyw không đóng nhầm.
- Đồng bộ dữ liệu hai chiều với shared_config.json và runtime_state.json.
- Tích hợp sẵn cổng Hook chờ cho Dịch vụ Bot Telegram (sẽ kích hoạt ở Giai đoạn 7).
"""

import logging
import socket
import threading
import time
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, render_template_string, request
from werkzeug.serving import make_server

from core.config_manager import (
    ensure_data_files_exist,
    load_config,
    load_runtime_state,
    safe_log,
    save_config,
    update_runtime_state,
)
from services.posture_controller import (
    consume_recalibration_request,
    get_latest_posture_frame,
    has_live_posture_stream,
    posture_manager,
    set_latest_posture_frame,
)

# Giảm log rác của werkzeug khi chạy ngầm trong .exe (--noconsole)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)

# Bộ nhớ đệm tốc độ cao (đồng bộ với runtime_state.json)
lich_su_canh_bao: List[Dict[str, str]] = []
thong_ke_ung_dung: Dict[str, int] = {}
thoi_gian_dung_ai_giay: int = 0
_server_instance = None
_server_lock = threading.Lock()


def _sync_memory_from_disk() -> None:
    """Nạp lại trạng thái từ runtime_state.json để đồng bộ khi chạy đa tiến trình."""
    global lich_su_canh_bao, thong_ke_ung_dung, thoi_gian_dung_ai_giay
    state = load_runtime_state()
    if state.get("alerts"):
        lich_su_canh_bao = list(state["alerts"])[:50]
    if isinstance(state.get("app_usage"), dict):
        thong_ke_ung_dung = dict(state["app_usage"])
    thoi_gian_dung_ai_giay = int(state.get("ai_seconds", 0))


def get_local_lan_ip() -> str:
    """Lấy địa chỉ IPv4 LAN của máy tính để Phụ huynh nhập lên trình duyệt điện thoại Android."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.3)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def is_port_in_use(port: int = 5000, host: str = "127.0.0.1") -> bool:
    """Kiểm tra cổng Web Server đã có tiến trình khác lắng nghe hay chưa."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex((host, port)) == 0


def _dispatch_telegram_hook(noi_dung: str, loai_su_kien: str = "GAME") -> bool:
    """
    Cổng Hook chờ cho Dịch vụ Bot Telegram (triển khai ở Giai đoạn 7).
    Khi chưa có services/telegram_service.py (Giai đoạn 3-6), hàm tự bỏ qua êm ái.
    """
    try:
        from services.telegram_service import trigger_parent_alert  # type: ignore

        return bool(trigger_parent_alert(loai_su_kien, noi_dung))
    except ImportError:
        return False
    except Exception as exc:
        safe_log(f"[WARN] Telegram hook lỗi: {exc}")
        return False


def _is_telegram_module_available() -> bool:
    try:
        import services.telegram_service  # noqa: F401 # type: ignore

        return True
    except ImportError:
        return False


# =====================================================================
# CÁC CỔNG API RESTFUL (GIỮ NGUYÊN 100% GIAO THỨC GỐC CỦA NHÓM + MỞ RỘNG)
# =====================================================================
@app.route("/api/nhan_canh_bao", methods=["POST"])
def nhan_canh_bao():
    """Cổng API gốc của nhóm: nhận {'message': noi_dung} từ giam_sat.pyw & App .EXE."""
    global lich_su_canh_bao
    data = request.get_json(silent=True) or {}
    noi_dung = str(data.get("message", "Phát hiện hành vi bất thường")).strip()
    loai = str(data.get("event_type", "GAME")).strip().upper()
    thoi_gian = time.strftime("%H:%M:%S")

    state = load_runtime_state()
    alerts = list(state.get("alerts", lich_su_canh_bao))
    alerts.insert(0, {"time": thoi_gian, "message": noi_dung, "type": loai})
    del alerts[50:]

    lich_su_canh_bao = alerts
    update_runtime_state(alerts=alerts)

    # Chuyển tiếp sang cổng Hook Telegram (tự hoạt động khi lắp Giai đoạn 7)
    _dispatch_telegram_hook(noi_dung, loai)
    return jsonify({"status": "success"}), 200


@app.route("/api/cap_nhat_thong_ke", methods=["POST"])
def cap_nhat_thong_ke():
    """Cập nhật thời gian sử dụng từng ứng dụng và số giây dùng Chatbot AI."""
    global thong_ke_ung_dung, thoi_gian_dung_ai_giay
    data = request.get_json(silent=True) or {}
    if isinstance(data.get("app_usage"), dict):
        thong_ke_ung_dung = data["app_usage"]
    if "ai_seconds" in data:
        thoi_gian_dung_ai_giay = int(data["ai_seconds"])

    update_runtime_state(
        app_usage=thong_ke_ung_dung,
        ai_seconds=thoi_gian_dung_ai_giay,
    )
    return jsonify({"status": "ok"})


@app.route("/api/du_lieu_dashboard", methods=["GET"])
def du_lieu_dashboard():
    """Trả dữ liệu tổng hợp thời gian thực cho giao diện Web PC & Điện thoại Phụ huynh."""
    state = load_runtime_state()
    cfg = load_config()
    alerts = state.get("alerts") if state.get("alerts") is not None else lich_su_canh_bao
    app_usage = state.get("app_usage") if state.get("app_usage") is not None else thong_ke_ung_dung
    ai_sec = int(state.get("ai_seconds", thoi_gian_dung_ai_giay))
    port = int(cfg.get("web_port", 5000))

    return jsonify(
        {
            "alerts": alerts,
            "app_usage": app_usage,
            "ai_seconds": ai_sec,
            "ai_minutes": round(ai_sec / 60.0, 1),
            "max_ai_minutes": int(cfg.get("max_ai_minutes", 15)),
            "current_role": state.get("current_role", "locked"),
            "monitoring_active": bool(state.get("monitoring_active", False)),
            "posture_telemetry": state.get("posture_telemetry", {}),
            "posture_stream_live": has_live_posture_stream(),
            "enable_posture_ai": bool(cfg.get("enable_posture_ai", True)),
            "lan_url": f"http://{get_local_lan_ip()}:{port}",
            "telegram_ready": _is_telegram_module_available(),
            "cloud": state.get("cloud", {}),
        }
    )


@app.route("/api/luu_cau_hinh_web", methods=["POST"])
def luu_cau_hinh_web():
    """Lưu cấu hình Chính sách Web / AI (và thuộc tính Bot Telegram khi kích hoạt Giai đoạn 7)."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()

    if "max_ai_minutes" in data:
        try:
            cfg["max_ai_minutes"] = max(1, int(data["max_ai_minutes"]))
        except (ValueError, TypeError):
            cfg["max_ai_minutes"] = 15

    for list_field in ("bad_urls", "allowed_urls", "bad_tabs", "bad_apps", "academic_keywords"):
        if list_field in data and isinstance(data[list_field], str):
            cfg[list_field] = [x.strip() for x in data[list_field].split(",") if x.strip()]

    # Lưu sẵn cấu hình Telegram nếu gửi lên (phục vụ Giai đoạn 7)
    if any(k in data for k in ("bot_token", "chat_id", "enable_alerts", "cooldown_seconds")):
        tg = cfg.get("telegram", {})
        if "bot_token" in data:
            tg["bot_token"] = str(data.get("bot_token", "")).strip()
        if "chat_id" in data:
            tg["chat_id"] = str(data.get("chat_id", "")).strip()
        if "cooldown_seconds" in data:
            try:
                tg["cooldown_seconds"] = max(5, int(data.get("cooldown_seconds", 30)))
            except (ValueError, TypeError):
                tg["cooldown_seconds"] = 30
        for flag in ("enable_alerts", "alert_on_game", "alert_on_wrong_login", "alert_on_ai_overuse"):
            if flag in data:
                tg[flag] = bool(data[flag])
        cfg["telegram"] = tg

    save_config(cfg)

    if data.get("send_test"):
        sent = _dispatch_telegram_hook(
            "✅ Tin nhắn kiểm tra kết nối từ Bảng Điều Khiển Bàn Học Thông Minh!",
            "SYSTEM_TEST",
        )
        if sent:
            return jsonify({"status": "ok", "message": "✅ Đã lưu & gửi tin nhắn thử nghiệm về Telegram!"})
        return jsonify(
            {
                "status": "warn",
                "message": "⚠️ Đã lưu cấu hình! (Chưa gửi được Telegram: kiểm tra Token/Chat ID hoặc chờ Giai đoạn 7).",
            }
        )

    return jsonify({"status": "ok", "message": "✅ Đã lưu cấu hình chính sách Web & AI thành công!"})


@app.route("/api/khoa_may_tu_xa", methods=["POST"])
def khoa_may_tu_xa():
    """Cho phép Phụ huynh bấm nút khóa màn hình máy tính của Học sinh ngay từ trình duyệt điện thoại."""
    update_runtime_state(remote_lock_requested=True)
    return jsonify(
        {
            "status": "ok",
            "message": "🔒 Đã gửi lệnh khóa màn hình từ xa! Máy tính học sinh sẽ chuyển về màn hình khóa trong ~1 giây.",
        }
    )


@app.route("/api/xoa_lich_su_canh_bao", methods=["POST"])
def xoa_lich_su_canh_bao():
    """Xóa danh sách lịch sử vi phạm để bắt đầu phiên theo dõi mới."""
    global lich_su_canh_bao
    lich_su_canh_bao = []
    update_runtime_state(alerts=[])
    return jsonify({"status": "ok", "message": "🧹 Đã xóa lịch sử cảnh báo!"})


@app.route("/api/posture_telemetry", methods=["POST"])
def nhan_posture_telemetry():
    """Nhận chỉ số sinh trắc học 3D (EAR, PERCLOS, MAR, Pitch/Yaw/Roll, CFI) từ posture_ai_engine.py."""
    data = request.get_json(silent=True) or {}
    update_runtime_state(posture_telemetry=data)
    recalib = consume_recalibration_request()
    return jsonify({"status": "ok", "recalibrate_requested": recalib})


@app.route("/api/posture_frame", methods=["POST"])
def nhan_posture_frame():
    """Nhận khung hình JPEG đã vẽ lớp phủ thông số từ tiến trình Sidecar AI lưu vào bộ đệm RAM cục bộ."""
    raw = request.get_data()
    if raw:
        set_latest_posture_frame(raw)
    return jsonify({"status": "ok"})


@app.route("/api/posture_recalibrate", methods=["POST"])
def api_posture_recalibrate():
    """Kích hoạt Hiệu chỉnh lại Gốc Tư thế Cá nhân hóa (5 giây) từ trình duyệt Phụ huynh."""
    posture_manager.recalibrate()
    return jsonify(
        {
            "status": "ok",
            "message": "🔄 Đã gửi lệnh Hiệu chỉnh Gốc Tư thế (5 giây)! Hãy nhắc học sinh ngồi thẳng lưng nhìn màn hình.",
        }
    )


@app.route("/api/posture_toggle", methods=["POST"])
def api_posture_toggle():
    """Bật hoặc Tắt trực tiếp Camera AI Nhận diện Tư thế 3D ngay từ giao diện Web."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if "enable" in data:
        new_state = bool(data["enable"])
    else:
        new_state = not (posture_manager.is_running() or has_live_posture_stream())

    cfg["enable_posture_ai"] = new_state
    save_config(cfg)

    if new_state:
        started = posture_manager.start(force_enable=True)
        if started:
            return jsonify({"status": "ok", "enabled": True, "message": "📷 Đã bật Camera AI Nhận diện Tư thế 3D!"})
        return jsonify(
            {
                "status": "warn",
                "enabled": True,
                "message": "⚠️ Đã bật cờ AI Tư thế (Chờ phiên Học sinh hoặc kết nối Webcam vật lý).",
            }
        )
    else:
        posture_manager.stop()
        return jsonify({"status": "ok", "enabled": False, "message": "⏸️ Đã tạm dừng Camera AI Nhận diện Tư thế 3D."})


@app.route("/video_feed")
def video_feed():
    """Luồng truyền tải MJPEG thời gian thực trong mạng nội bộ LAN (Tuyệt đối không đẩy lên Đám mây)."""
    if request.args.get("snapshot") == "1":
        return Response(get_latest_posture_frame(), mimetype="image/jpeg")

    def _gen_mjpeg():
        while True:
            jpg = get_latest_posture_frame()
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.08 if has_live_posture_stream() else 0.45)

    return Response(_gen_mjpeg(), mimetype="multipart/x-mixed-replace; boundary=frame")


# =====================================================================
# GIAO DIỆN WEB DASHBOARD (PC & MOBILE RESPONSIVE)
# =====================================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <!-- BẮT BUỘC giữ cụm "Bảng Điều Khiển Bàn Học" để giam_sat.pyw miễn trừ không đóng nhầm -->
    <title>Bảng Điều Khiển Bàn Học Thông Minh (STEM Dashboard)</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #090d16; color: #f8fafc; margin: 0; padding: 18px; }
        .wrap { max-width: 1160px; margin: 0 auto; }
        .header { background: linear-gradient(135deg, #1e293b, #0f172a); padding: 18px 24px; border-radius: 14px; border: 1px solid #334155; display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; align-items: center; margin-bottom: 18px; }
        .header h1 { margin: 0; font-size: 20px; color: #38bdf8; }
        .sub-info { font-size: 13px; color: #94a3b8; margin-top: 5px; }
        .lan-badge { display: inline-block; background: #0f172a; border: 1px solid #0284c7; color: #7dd3fc; padding: 3px 10px; border-radius: 6px; font-family: Consolas, monospace; font-size: 12px; margin-top: 6px; }
        .status-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .status-pill { background: rgba(34,197,94,0.18); color: #4ade80; border: 1px solid #22c55e; padding: 6px 14px; border-radius: 99px; font-weight: 700; font-size: 13px; }
        .status-pill.idle { background: rgba(148,163,184,0.15); color: #cbd5e1; border-color: #64748b; }
        .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 18px; }
        @media (max-width: 860px) { .kpi-row { grid-template-columns: repeat(2, 1fr); } }
        .kpi { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 14px 16px; }
        .kpi .label { font-size: 12px; color: #94a3b8; text-transform: uppercase; font-weight: 600; }
        .kpi .val { font-size: 22px; font-weight: 800; margin-top: 4px; color: #f8fafc; }
        .grid { display: grid; grid-template-columns: 1.15fr 1fr; gap: 18px; }
        @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 14px; padding: 18px 20px; }
        .card h2 { margin-top: 0; font-size: 15px; color: #38bdf8; text-transform: uppercase; border-bottom: 1px solid #334155; padding-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
        .alert-item { background: rgba(239,68,68,0.14); border-left: 4px solid #ef4444; padding: 10px 12px; border-radius: 8px; margin-bottom: 9px; font-size: 13.5px; line-height: 1.45; }
        .alert-time { color: #fca5a5; font-weight: 700; font-family: Consolas, monospace; margin-right: 8px; }
        label { display: block; font-size: 13px; margin: 11px 0 5px; color: #cbd5e1; font-weight: 600; }
        input[type="text"], input[type="number"], textarea { width: 100%; padding: 9px 11px; border-radius: 8px; border: 1px solid #475569; background: #0f172a; color: #f8fafc; font-size: 13.5px; font-family: inherit; }
        textarea { resize: vertical; min-height: 54px; }
        .chk { display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 13px; color: #e2e8f0; }
        .btn-bar { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
        .btn { background: #0284c7; color: white; border: none; padding: 10px 16px; border-radius: 8px; font-weight: 700; cursor: pointer; font-size: 13.5px; }
        .btn:hover { opacity: 0.92; }
        .btn-danger { background: #e11d48; }
        .btn-secondary { background: #475569; padding: 5px 10px; font-size: 12px; }
        .btn-test { background: #059669; }
        .usage-row { display: flex; justify-content: space-between; padding: 7px 10px; background: #0f172a; border-radius: 6px; margin-bottom: 6px; font-size: 13.5px; }
        .stage-note { background: rgba(56,189,248,0.1); border: 1px dashed #0284c7; border-radius: 8px; padding: 10px 12px; font-size: 12.5px; color: #bae6fd; margin-top: 14px; }
    </style>
</head>
<body>
    <div class="wrap">
        <div class="header">
            <div>
                <h1>🖥️ BẢNG ĐIỀU KHIỂN BÀN HỌC THÔNG MINH (WEB & ANDROID DASHBOARD)</h1>
                <div class="sub-info">Đồng bộ thời gian thực mỗi 2 giây | Dành cho Phụ huynh giám sát trên Máy tính & Điện thoại cùng Wi-Fi</div>
                <div class="lan-badge" id="lan-url">📱 Mở trên Điện thoại Android: {{ lan_url }}</div>
            </div>
            <div class="status-row">
                <div id="role-pill" class="status-pill">🟢 ĐANG TRỰC TUYẾN</div>
                <button class="btn btn-danger" style="margin:0;" onclick="remoteLock()">🔒 Khóa Máy Từ Xa</button>
            </div>
        </div>

        <div class="kpi-row">
            <div class="kpi">
                <div class="label">Chế độ hiện tại</div>
                <div class="val" id="kpi-role">Đang tải...</div>
            </div>
            <div class="kpi">
                <div class="label">Số cảnh báo vi phạm</div>
                <div class="val" id="kpi-alerts" style="color:#f87171;">0</div>
            </div>
            <div class="kpi">
                <div class="label">Thời gian dùng AI / ChatGPT</div>
                <div class="val" id="kpi-ai" style="color:#38bdf8;">0 / {{ cfg.max_ai_minutes }} phút</div>
            </div>
            <div class="kpi">
                <div class="label">Điểm tập trung thị giác (CFI)</div>
                <div class="val" id="kpi-cfi" style="color:#4ade80;">100%</div>
            </div>
        </div>

        <div class="grid">
            <!-- CỘT 1: CAMERA AI TƯ THẾ 3D, CẢNH BÁO VI PHẠM & THỐNG KÊ ỨNG DỤNG -->
            <div class="card">
                <h2>
                    <span>📷 Giám Sát Tư Thế AI 3D (MediaPipe Holistic — Nội bộ LAN)</span>
                    <span id="posture-badge" style="font-size:11.5px; color:#38bdf8; text-transform:none;">Đang kết nối...</span>
                </h2>
                <div style="background:#0f172a; border:1px solid #334155; border-radius:10px; overflow:hidden; margin-bottom:10px;">
                    <img id="posture-stream" src="/video_feed" alt="AI Posture 3D Stream" style="width:100%; display:block; max-height:330px; object-fit:contain; background:#090d16;">
                </div>
                <div id="posture-metrics" style="display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin-bottom:10px; font-size:12.5px;">
                    <div class="usage-row" style="margin:0;"><span>👁️ EAR / PERCLOS</span><b id="m-ear">0.30 / 0%</b></div>
                    <div class="usage-row" style="margin:0;"><span>👄 MAR / Ngáp</span><b id="m-mar">0.10 / 0</b></div>
                    <div class="usage-row" style="margin:0;"><span>📐 Pitch / Yaw / Roll</span><b id="m-euler">0° / 0° / 0°</b></div>
                    <div class="usage-row" style="margin:0;"><span>🙌 2 Tay trên bàn</span><b id="m-hands" style="color:#4ade80;">Có</b></div>
                    <div class="usage-row" style="margin:0;"><span>🎯 Hiệu chỉnh 5s</span><b id="m-calib" style="color:#38bdf8;">Chờ phiên</b></div>
                    <div class="usage-row" style="margin:0;"><span>💤 Ngủ gật (E_nod)</span><b id="m-nod" style="color:#4ade80;">Không</b></div>
                </div>
                <div style="display:flex; flex-wrap:wrap; gap:8px; margin-bottom:18px;">
                    <button class="btn btn-test" style="padding:7px 12px; font-size:12.5px;" onclick="togglePostureAI()">📷 Bật / Tắt Camera AI</button>
                    <button class="btn" style="padding:7px 12px; font-size:12.5px;" onclick="recalibratePosture()">🔄 Hiệu chỉnh lại Gốc Tư thế (5s)</button>
                </div>

                <h2>
                    <span>🚨 Lịch Sử Cảnh Báo Vi Phạm Thời Gian Thực</span>
                    <button class="btn btn-secondary" onclick="clearAlerts()">🧹 Xóa lịch sử</button>
                </h2>
                <div id="alert-list" style="max-height: 260px; overflow-y: auto; padding-right: 4px;"></div>

                <h2 style="margin-top: 20px;"><span>⏱️ Thời Gian Sử Dụng Ứng Dụng & Chatbot AI</span></h2>
                <div id="usage-box"></div>
            </div>

            <!-- CỘT 2: CẤU HÌNH CHÍNH SÁCH WEB / AI (& BOT TELEGRAM Ở GIAI ĐOẠN 7) -->
            <div class="card">
                <h2><span>⚙️ Chính Sách Kiểm Soát Web, Từ Khóa & Giới Hạn AI</span></h2>

                <label>Giới hạn thời gian tra cứu ChatGPT / Chatbot AI (Phút/phiên):</label>
                <input type="number" id="max_ai_minutes" min="1" max="240" value="{{ cfg.max_ai_minutes }}">

                <label>Danh sách Link Address (URL) bị cấm (cách nhau dấu phẩy):</label>
                <textarea id="bad_urls">{{ cfg.bad_urls | join(', ') }}</textarea>

                <label>Danh sách Link Học tập được phép (ALLOWED_URLS):</label>
                <textarea id="allowed_urls">{{ cfg.allowed_urls | join(', ') }}</textarea>

                <label>Từ khóa tiêu đề Tab cấm (BAD_TABS - gốc giam_sat.pyw):</label>
                <input type="text" id="bad_tabs" value="{{ cfg.bad_tabs | join(', ') }}">

                <label>Từ khóa bài giảng được miễn trừ trên YouTube (ACADEMIC_KW):</label>
                <input type="text" id="academic_keywords" value="{{ cfg.academic_keywords | join(', ') }}">

                <label>Danh sách tiến trình Game cấm (BAD_APPS):</label>
                <input type="text" id="bad_apps" value="{{ cfg.bad_apps | join(', ') }}">

                <div id="telegram-section" style="margin-top: 16px; padding-top: 12px; border-top: 1px solid #334155;">
                    <div style="font-size:13.5px; font-weight:700; color:#34d399; margin-bottom:6px;">
                        🤖 Cấu hình Bot Telegram Phụ huynh
                        {% if not tg_ready %}<span style="color:#fbbf24; font-weight:normal;">(Sẽ kích hoạt dịch vụ gửi tin ở Giai đoạn 7)</span>{% endif %}
                    </div>
                    <label>Telegram Bot Token:</label>
                    <input type="text" id="bot_token" value="{{ cfg.telegram.bot_token }}" placeholder="Nhập Token từ @BotFather">

                    <label>Telegram Chat ID của Phụ huynh:</label>
                    <input type="text" id="chat_id" value="{{ cfg.telegram.chat_id }}" placeholder="Nhập Chat ID nhận cảnh báo">

                    <label>Giãn cách chống spam tin nhắn (Giây - Cooldown):</label>
                    <input type="number" id="cooldown_seconds" min="5" max="600" value="{{ cfg.telegram.cooldown_seconds }}">

                    <div class="chk"><input type="checkbox" id="enable_alerts" {% if cfg.telegram.enable_alerts %}checked{% endif %}> Kích hoạt gửi thông báo qua Telegram</div>
                    <div class="chk"><input type="checkbox" id="alert_on_game" {% if cfg.telegram.alert_on_game %}checked{% endif %}> Báo khi mở Web Game / Ứng dụng Game</div>
                    <div class="chk"><input type="checkbox" id="alert_on_wrong_login" {% if cfg.telegram.alert_on_wrong_login %}checked{% endif %}> Báo khi Học sinh thử đăng nhập quyền Admin/Phụ huynh</div>
                    <div class="chk"><input type="checkbox" id="alert_on_ai_overuse" {% if cfg.telegram.alert_on_ai_overuse %}checked{% endif %}> Báo khi tra cứu ChatGPT / AI quá thời gian quy định</div>
                </div>

                <div class="btn-bar">
                    <button class="btn" onclick="saveWebConfig(false)">💾 Lưu Chính Sách</button>
                    <button class="btn btn-test" onclick="saveWebConfig(true)">📨 Lưu & Test Telegram</button>
                </div>
                <div id="save-msg" style="margin-top:10px; color:#4ade80; font-size:13px; font-weight:700;"></div>
            </div>
        </div>
    </div>

    <script>
        const roleLabels = {
            "locked": "🔒 Màn hình khóa",
            "student": "👨‍🎓 Học sinh (Đang giám sát)",
            "parent": "👪 Phụ huynh",
            "admin": "🛠️ Quản trị viên (Admin)",
            "unlocked": "🔓 Đã mở khóa"
        };

        async function loadDashboard() {
            try {
                const res = await fetch('/api/du_lieu_dashboard');
                const data = await res.json();

                document.getElementById('lan-url').innerText = '📱 Mở trên Điện thoại Android: ' + data.lan_url;
                document.getElementById('kpi-role').innerText = roleLabels[data.current_role] || data.current_role;
                document.getElementById('kpi-alerts').innerText = (data.alerts || []).length;
                document.getElementById('kpi-ai').innerText = `${data.ai_minutes} / ${data.max_ai_minutes} phút`;

                const pt = data.posture_telemetry || {};
                const cfi = (pt.visual_focus_score !== undefined) ? pt.visual_focus_score : 100;
                document.getElementById('kpi-cfi').innerText = `${cfi}%`;

                const pBadge = document.getElementById('posture-badge');
                if (data.posture_stream_live && pt.camera_connected) {
                    pBadge.style.color = '#4ade80';
                    pBadge.innerText = '🟢 Đang phân tích 3D tại biên (Zero-Cloud)';
                } else if (data.posture_stream_live && pt.status === 'NO_CAMERA') {
                    pBadge.style.color = '#fbbf24';
                    pBadge.innerText = '⚠️ Chưa cắm Webcam vật lý';
                } else {
                    pBadge.style.color = '#94a3b8';
                    pBadge.innerText = data.enable_posture_ai ? '⏸️ Chờ Học sinh đăng nhập' : '⏹️ Đã tắt Camera AI';
                }

                document.getElementById('m-ear').innerText = `${pt.ear !== undefined ? pt.ear : 0.30} / ${pt.perclos !== undefined ? pt.perclos : 0}%`;
                document.getElementById('m-mar').innerText = `${pt.mar !== undefined ? pt.mar : 0.10} / ${pt.yawn_count || 0}`;
                document.getElementById('m-euler').innerText = `${pt.pitch_deg || 0}° / ${pt.yaw_deg || 0}° / ${pt.roll_deg || 0}°`;
                document.getElementById('m-hands').innerText = (pt.hands_on_desk === false) ? '⚠️ Giấu tay' : '✅ Trên bàn';
                document.getElementById('m-hands').style.color = (pt.hands_on_desk === false) ? '#f87171' : '#4ade80';
                document.getElementById('m-calib').innerText = pt.calibrated ? '✅ Đã khóa gốc' : (data.posture_stream_live ? '⏳ Đang đo 5s' : 'Chờ phiên');
                document.getElementById('m-nod').innerText = pt.drowsy_nod ? '🚨 GẬT ĐẦU!' : 'Không';
                document.getElementById('m-nod').style.color = pt.drowsy_nod ? '#f87171' : '#4ade80';

                const pill = document.getElementById('role-pill');
                if (data.monitoring_active) {
                    pill.className = 'status-pill';
                    pill.innerText = '🟢 ĐANG GIÁM SÁT HỌC SINH';
                } else {
                    pill.className = 'status-pill idle';
                    pill.innerText = '⏸️ CHỜ PHIÊN HỌC SINH';
                }

                const listEl = document.getElementById('alert-list');
                if (!data.alerts || data.alerts.length === 0) {
                    listEl.innerHTML = '<div style="color:#4ade80; padding:12px; background:rgba(34,197,94,0.08); border-radius:8px;">✅ Học sinh đang học tập nghiêm túc, chưa có vi phạm nào trong phiên này.</div>';
                } else {
                    listEl.innerHTML = data.alerts.map(a => `
                        <div class="alert-item">
                            <span class="alert-time">[${a.time}]</span>
                            <span>${a.message}</span>
                        </div>
                    `).join('');
                }

                let usageHtml = `<div class="usage-row"><span>🤖 <b>Tra cứu ChatGPT / Chatbot AI</b></span><span style="color:#38bdf8; font-weight:700;">${data.ai_minutes} phút (${data.ai_seconds}s)</span></div>`;
                const apps = data.app_usage || {};
                const keys = Object.keys(apps);
                if (keys.length === 0) {
                    usageHtml += '<div style="color:#94a3b8; font-size:13px; padding:6px 2px;">Chưa ghi nhận thời gian mở ứng dụng trong phiên này.</div>';
                } else {
                    for (const [k, sec] of Object.entries(apps)) {
                        usageHtml += `<div class="usage-row"><span>🖥️ <b>${k}</b></span><span>${(sec/60).toFixed(1)} phút (${sec}s)</span></div>`;
                    }
                }
                document.getElementById('usage-box').innerHTML = usageHtml;
            } catch (e) {}
        }

        async function saveWebConfig(sendTest) {
            const payload = {
                max_ai_minutes: document.getElementById('max_ai_minutes').value,
                bad_urls: document.getElementById('bad_urls').value,
                allowed_urls: document.getElementById('allowed_urls').value,
                bad_tabs: document.getElementById('bad_tabs').value,
                academic_keywords: document.getElementById('academic_keywords').value,
                bad_apps: document.getElementById('bad_apps').value,
                bot_token: document.getElementById('bot_token').value,
                chat_id: document.getElementById('chat_id').value,
                cooldown_seconds: document.getElementById('cooldown_seconds').value,
                enable_alerts: document.getElementById('enable_alerts').checked,
                alert_on_game: document.getElementById('alert_on_game').checked,
                alert_on_wrong_login: document.getElementById('alert_on_wrong_login').checked,
                alert_on_ai_overuse: document.getElementById('alert_on_ai_overuse').checked,
                send_test: sendTest
            };
            const res = await fetch('/api/luu_cau_hinh_web', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const d = await res.json();
            document.getElementById('save-msg').innerText = d.message;
        }

        async function remoteLock() {
            const res = await fetch('/api/khoa_may_tu_xa', {method: 'POST'});
            const d = await res.json();
            document.getElementById('save-msg').innerText = d.message;
        }

        async function clearAlerts() {
            await fetch('/api/xoa_lich_su_canh_bao', {method: 'POST'});
            loadDashboard();
        }

        async function togglePostureAI() {
            const res = await fetch('/api/posture_toggle', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({})
            });
            const d = await res.json();
            document.getElementById('save-msg').innerText = d.message;
            document.getElementById('posture-stream').src = '/video_feed?t=' + Date.now();
            loadDashboard();
        }

        async function recalibratePosture() {
            const res = await fetch('/api/posture_recalibrate', {method: 'POST'});
            const d = await res.json();
            document.getElementById('save-msg').innerText = d.message;
        }

        setInterval(loadDashboard, 2000);
        loadDashboard();
    </script>
</body>
</html>
"""


@app.route("/")
@app.route("/bot-settings")
def home():
    cfg = load_config()
    port = int(cfg.get("web_port", 5000))
    return render_template_string(
        DASHBOARD_HTML,
        cfg=cfg,
        lan_url=f"http://{get_local_lan_ip()}:{port}",
        tg_ready=_is_telegram_module_available(),
    )


@app.route("/api/telegram-config", methods=["POST"])
def api_telegram_config_compat():
    """Route tương thích Tài liệu số 02: cập nhật cấu hình Bot Telegram."""
    return luu_cau_hinh_web()


@app.route("/api/telegram-test", methods=["POST"])
def api_telegram_test_compat():
    """Route tương thích Tài liệu số 02: gửi tin nhắn kiểm tra kết nối Bot Telegram."""
    ok = _dispatch_telegram_hook(
        "Cấu hình Bot Telegram từ Website Bàn Học Thông Minh đã hoạt động chính xác!",
        "SYSTEM_TEST",
    )
    if ok:
        return jsonify({"status": "ok", "message": "📨 Đã bắn tin nhắn thử nghiệm về Telegram của Phụ huynh!"})
    return jsonify({"status": "err", "message": "⚠️ Chưa nhập Bot Token / Chat ID hợp lệ (Đã in nội dung Mock)."})


def _try_start_cloud_agent() -> None:
    """Bật agent kết nối bản cloud (Vercel) nếu secrets.local.json / env có cloud_url."""
    try:
        from services.cloud_agent import start_cloud_agent

        start_cloud_agent()
    except Exception as exc:
        safe_log(f"[WARN] Không khởi động được Cloud Agent: {exc}")


CLOUD_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kết nối Cloud - Bảng Điều Khiển Bàn Học</title>
<body style="font-family:system-ui;background:#0b1220;color:#e2e8f0;display:grid;place-items:center;min-height:90vh">
<div style="background:#131c2e;padding:28px;border-radius:16px;max-width:460px;text-align:center">
{% if not c.enabled %}<h2>☁️ Chưa bật kết nối Cloud</h2><p>Thêm <code>"cloud_url"</code> vào <code>secrets.local.json</code> rồi khởi động lại.</p>
{% elif c.paired %}<h2>✅ Máy đã được phụ huynh ghép</h2><p>Mã máy: {{ c.device_id }}</p>
{% else %}<h2>🔗 Mã ghép máy</h2><div style="font-size:56px;letter-spacing:10px;font-weight:700">{{ c.pair_code or '......' }}</div>
<p>Phụ huynh đăng nhập <b>{{ c.url }}</b> → <i>Thêm máy học sinh</i> → nhập mã này (hết hạn sau 15 phút, tự đổi mã mới).</p>{% endif %}
{% if c.error %}<p style="color:#f87171">Lỗi kết nối: {{ c.error }}</p>{% endif %}
</div><script>setTimeout(()=>location.reload(),10000)</script></body>"""


@app.route("/ket-noi-cloud")
def ket_noi_cloud():
    cloud = load_runtime_state().get("cloud") or {}
    return render_template_string(CLOUD_PAGE, c=type("C", (), {k: cloud.get(k) for k in (
        "enabled", "paired", "device_id", "pair_code", "url", "error")})())


def _try_start_telegram_poller() -> None:
    try:
        from services.telegram_service import start_telegram_command_poller

        start_telegram_command_poller()
    except Exception:
        pass


def _try_stop_telegram_poller() -> None:
    try:
        from services.telegram_service import stop_telegram_command_poller

        stop_telegram_command_poller()
    except Exception:
        pass


def start_web_server_in_background(port: Optional[int] = None) -> bool:
    """
    Khởi chạy Flask Web Server (0.0.0.0:5000) + Telegram 2-way Poller trong luồng nền an toàn.
    Nếu cổng 5000 đã có tiến trình trang_web.py bên ngoài chạy sẵn thì tự động tái sử dụng, không xung đột.
    """
    global _server_instance
    ensure_data_files_exist()
    _sync_memory_from_disk()
    _try_start_telegram_poller()
    _try_start_cloud_agent()
    cfg = load_config()
    target_port = int(port or cfg.get("web_port", 5000))

    if is_port_in_use(target_port):
        safe_log(f"[WEB] Cổng {target_port} đã hoạt động, kết nối trực tiếp vào Server hiện có.")
        return True

    with _server_lock:
        if _server_instance is not None:
            return True
        try:
            _server_instance = make_server("0.0.0.0", target_port, app, threaded=True)
        except Exception as exc:
            safe_log(f"[WARN] Không thể mở cổng Web {target_port}: {exc}")
            return False

        def _serve() -> None:
            safe_log(
                f"[WEB] Bảng Điều Khiển đã chạy tại http://127.0.0.1:{target_port} "
                f"(LAN: http://{get_local_lan_ip()}:{target_port})"
            )
            try:
                _server_instance.serve_forever()
            except Exception:
                pass

        t = threading.Thread(target=_serve, name="EmbeddedFlaskWebThread", daemon=True)
        t.start()
        return True


def stop_web_server_in_background() -> None:
    global _server_instance
    _try_stop_telegram_poller()
    posture_manager.stop()
    with _server_lock:
        if _server_instance is not None:
            try:
                _server_instance.shutdown()
            except Exception:
                pass
            _server_instance = None


if __name__ == "__main__":
    ensure_data_files_exist()
    _sync_memory_from_disk()
    _try_start_telegram_poller()
    _try_start_cloud_agent()
    cfg_main = load_config()
    listen_port = int(cfg_main.get("web_port", 5000))
    safe_log(
        f">>> Bảng Điều Khiển Bàn Học đang chạy tại: http://127.0.0.1:{listen_port} "
        f"| Điện thoại cùng Wi-Fi: http://{get_local_lan_ip()}:{listen_port}"
    )
    app.run(host="0.0.0.0", port=listen_port, debug=False)
