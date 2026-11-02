# -*- coding: utf-8 -*-
"""
Dịch vụ Bot Telegram Cảnh báo Phụ huynh & Điều khiển Hai chiều (services/telegram_service.py)
Thực thi theo Giai đoạn 7 (thành phần sau cùng):
  - Nguyên tắc Local-first, Zero-cloud: Chỉ gửi Metadata văn bản & chỉ số tập trung CFI, không gửi hình ảnh.
  - 4 kịch bản cảnh báo tự động (GAME / GAME_DETECTED, WRONG_LOGIN, AI_OVERUSE, POSTURE_OR_HW) + SYSTEM_TEST.
  - Bộ lọc chống spam theo thời gian giãn cách (cooldown_seconds).
  - Nhận lệnh 2 chiều an toàn (xác thực đúng chat_id Phụ huynh): /status, /lock, /reset_ai, /help.
"""

import html
import threading
import time
from typing import Any, Dict, Optional

import requests

from core.config_manager import (
    load_config,
    load_runtime_state,
    safe_log,
    update_runtime_state,
)

_last_sent_timestamps: Dict[str, float] = {}
_poller_running = False
_poller_lock = threading.Lock()


def _normalize_event_type(event_type: str) -> str:
    ev = (event_type or "GENERAL").strip().upper()
    aliases = {
        "GAME": "GAME_DETECTED",
        "TEST": "SYSTEM_TEST",
        "POSTURE": "POSTURE_OR_HW",
    }
    return aliases.get(ev, ev)


def _get_telegram_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Đọc cấu hình Telegram hỗ trợ cả khóa 'telegram' và khóa 'telegram_config' tương thích ngược."""
    tg = cfg.get("telegram") or cfg.get("telegram_config") or {}
    return {
        "bot_token": str(tg.get("bot_token", "")).strip(),
        "chat_id": str(tg.get("chat_id", "")).strip(),
        "enable_alerts": bool(tg.get("enable_alerts", True)),
        "alert_on_game": bool(tg.get("alert_on_game", True)),
        "alert_on_wrong_login": bool(tg.get("alert_on_wrong_login", True)),
        "alert_on_ai_overuse": bool(tg.get("alert_on_ai_overuse", True)),
        "alert_on_posture": bool(tg.get("alert_on_posture", True)),
        "cooldown_seconds": max(5, int(tg.get("cooldown_seconds", 30))),
    }


def _is_valid_credentials(token: str, chat_id: str) -> bool:
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        return False
    if not chat_id or chat_id == "YOUR_CHAT_ID_HERE":
        return False
    return ":" in token


def send_telegram_html(token: str, chat_id: str, html_text: str, async_send: bool = True) -> bool:
    """Gửi tin nhắn định dạng HTML an toàn (không bao giờ lỗi ký tự '_' trong URL hay tên file .exe)."""
    if not _is_valid_credentials(token, chat_id):
        safe_log(f"[TELEGRAM MOCK] {html_text}")
        return False

    def _do_post() -> bool:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": html_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=4.0,
            )
            return resp.status_code == 200
        except Exception as exc:
            safe_log(f"[WARN] Không thể gửi tin nhắn Telegram: {exc}")
            return False

    if async_send:
        threading.Thread(target=_do_post, name="TelegramSendAsync", daemon=True).start()
        return True
    return _do_post()


def trigger_parent_alert(event_type: str, detail_message: str, sync: bool = False) -> bool:
    """
    Hàm bắn cảnh báo tức thì về điện thoại Phụ huynh (có Cooldown chống spam).
    event_type hỗ trợ:
      - 'GAME_DETECTED' / 'GAME'
      - 'WRONG_LOGIN'
      - 'AI_OVERUSE'
      - 'POSTURE_OR_HW'
      - 'SYSTEM_TEST' / 'TEST'
    """
    norm_type = _normalize_event_type(event_type)
    cfg = load_config()
    tg = _get_telegram_settings(cfg)

    if norm_type != "SYSTEM_TEST":
        if not tg["enable_alerts"]:
            return False
        if norm_type == "GAME_DETECTED" and not tg["alert_on_game"]:
            return False
        if norm_type == "WRONG_LOGIN" and not tg["alert_on_wrong_login"]:
            return False
        if norm_type == "AI_OVERUSE" and not tg["alert_on_ai_overuse"]:
            return False
        if norm_type == "POSTURE_OR_HW" and not tg["alert_on_posture"]:
            return False

        now = time.time()
        last_t = _last_sent_timestamps.get(norm_type, 0.0)
        if now - last_t < tg["cooldown_seconds"]:
            return False
        _last_sent_timestamps[norm_type] = now

    titles = {
        "GAME_DETECTED": "🚨 [CẢNH BÁO VI PHẠM KỶ LUẬT SỐ]",
        "WRONG_LOGIN": "🔐 [CẢNH BÁO VƯỢT QUYỀN HỆ THỐNG]",
        "AI_OVERUSE": "🤖 [CẢNH BÁO LẠM DỤNG CHATBOT AI]",
        "POSTURE_OR_HW": "⚠️ [CẢNH BÁO TẬP TRUNG & PHẦN CỨNG]",
        "SYSTEM_TEST": "✅ [KIỂM TRA KẾT NỐI BOT TELEGRAM THÀNH CÔNG]",
    }
    header = titles.get(norm_type, "⚠️ [THÔNG BÁO BÀN HỌC THÔNG MINH]")
    safe_detail = html.escape(str(detail_message))
    ts = time.strftime("%H:%M:%S - %d/%m/%Y")

    formatted_html = (
        f"<b>{html.escape(header)}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🕒 <b>Thời gian:</b> <code>{ts}</code>\n"
        f"📌 <b>Nội dung:</b> {safe_detail}\n"
        f"🌐 <i>Mở Web Dashboard Phụ huynh trong mạng Wi-Fi để xem chi tiết.</i>"
    )
    return send_telegram_html(tg["bot_token"], tg["chat_id"], formatted_html, async_send=not sync)


def build_status_report_html() -> str:
    """Tổng hợp báo cáo trạng thái phiên học hiện tại khi Phụ huynh nhắn lệnh /status."""
    cfg = load_config()
    state = load_runtime_state()

    role_map = {
        "locked": "🔒 Đang ở Màn hình Khóa",
        "student": "👨‍🎓 Học sinh đang học (Giám sát BẬT)",
        "parent": "👪 Chế độ Phụ huynh",
        "admin": "🛠️ Chế độ Quản trị viên",
        "unlocked": "🔓 Đã mở khóa",
    }
    role_str = role_map.get(state.get("current_role", "locked"), str(state.get("current_role")))
    ai_min = round(int(state.get("ai_seconds", 0)) / 60.0, 1)
    max_ai = int(cfg.get("max_ai_minutes", 15))
    posture = state.get("posture_telemetry", {})
    cfi = posture.get("visual_focus_score", 100.0)
    alerts = state.get("alerts", [])
    app_usage = state.get("app_usage", {})

    lines = [
        "📊 <b>BÁO CÁO TRẠNG THÁI BÀN HỌC THÔNG MINH</b>",
        "━━━━━━━━━━━━━━━",
        f"👤 <b>Trạng thái máy:</b> {html.escape(role_str)}",
        f"🎯 <b>Chỉ số tập trung (CFI):</b> <code>{cfi}%</code>",
        f"🤖 <b>Thời gian dùng AI:</b> <code>{ai_min} / {max_ai} phút</code>",
        f"🚨 <b>Tổng cảnh báo phiên này:</b> <code>{len(alerts)}</code>",
    ]

    if app_usage:
        lines.append("\n⏱️ <b>Thời gian dùng ứng dụng:</b>")
        for app_name, sec in list(app_usage.items())[:8]:
            lines.append(f"  • {html.escape(app_name)}: <code>{round(sec / 60.0, 1)} phút</code>")
    else:
        lines.append("\n⏱️ <i>Chưa ghi nhận thời gian mở ứng dụng.</i>")

    if alerts:
        lines.append("\n⚠️ <b>Vi phạm gần nhất:</b>")
        for item in alerts[:3]:
            t_str = html.escape(str(item.get("time", "")))
            m_str = html.escape(str(item.get("message", "")))
            lines.append(f"  • [<code>{t_str}</code>] {m_str}")

    lines.append("\n💡 <i>Gửi /lock để khóa màn hình máy tính ngay lập tức, /reset_ai để đặt lại giờ AI.</i>")
    return "\n".join(lines)


def handle_incoming_command(command_text: str) -> Optional[str]:
    """Xử lý lệnh điều khiển hai chiều từ Phụ huynh và trả về nội dung phản hồi HTML."""
    cmd = (command_text or "").strip().split()[0].lower()
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]

    if cmd in ("/start", "/help"):
        return (
            "👋 <b>Xin chào Phụ huynh! Đây là Trợ lý Bàn Học Thông Minh (Zero-Cloud).</b>\n"
            "Các lệnh điều khiển từ xa:\n"
            "• <code>/status</code> — Xem điểm tập trung CFI, thời gian dùng App/AI & vi phạm gần nhất.\n"
            "• <code>/lock</code> — Khóa màn hình máy tính của Học sinh ngay lập tức.\n"
            "• <code>/reset_ai</code> — Cấp lại từ đầu quỹ thời gian dùng ChatGPT/AI cho phiên học."
        )
    if cmd == "/status":
        return build_status_report_html()
    if cmd == "/lock":
        update_runtime_state(remote_lock_requested=True)
        return (
            "🔒 <b>ĐÃ GỬI LỆNH KHÓA MÀN HÌNH TỪ XA!</b>\n"
            "Máy tính của học sinh sẽ chuyển ngay về Màn hình Khóa trong vòng ~1 giây."
        )
    if cmd == "/reset_ai":
        update_runtime_state(ai_seconds=0)
        return "♻️ <b>Đã đặt lại bộ đếm thời gian dùng ChatGPT / Chatbot AI về 0 phút!</b>"
    return None


def start_telegram_command_poller() -> None:
    """
    Luồng nền lắng nghe lệnh 2 chiều (/status, /lock, /reset_ai) từ điện thoại Phụ huynh.
    Tự động ngủ nghỉ êm ái khi chưa nhập Bot Token, và tự kích hoạt ngay khi Phụ huynh lưu Token trên Web!
    """
    global _poller_running
    with _poller_lock:
        if _poller_running:
            return
        _poller_running = True

    def _poll_loop() -> None:
        last_update_id = 0
        active_token = ""
        while _poller_running:
            try:
                cfg = load_config()
                tg = _get_telegram_settings(cfg)
                token = tg["bot_token"]
                authorized_chat_id = tg["chat_id"]

                if not _is_valid_credentials(token, authorized_chat_id):
                    time.sleep(4.0)
                    continue

                if token != active_token:
                    active_token = token
                    last_update_id = 0

                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {"timeout": 10, "allowed_updates": ["message"]}
                if last_update_id > 0:
                    params["offset"] = last_update_id + 1

                resp = requests.get(url, params=params, timeout=14.0)
                if resp.status_code != 200:
                    time.sleep(5.0)
                    continue

                payload = resp.json()
                for upd in payload.get("result", []):
                    upd_id = int(upd.get("update_id", 0))
                    if upd_id > last_update_id:
                        last_update_id = upd_id

                    msg = upd.get("message") or {}
                    sender_chat_id = str((msg.get("chat") or {}).get("id", "")).strip()
                    text = str(msg.get("text", "")).strip()

                    # Bảo mật nghiêm ngặt: Chỉ thực thi lệnh từ đúng Chat ID của Phụ huynh đã cấu hình
                    if sender_chat_id != authorized_chat_id or not text.startswith("/"):
                        continue

                    reply_html = handle_incoming_command(text)
                    if reply_html:
                        send_telegram_html(token, authorized_chat_id, reply_html, async_send=False)
            except Exception:
                time.sleep(4.0)

    threading.Thread(target=_poll_loop, name="TelegramPollerThread", daemon=True).start()


def stop_telegram_command_poller() -> None:
    global _poller_running
    _poller_running = False
