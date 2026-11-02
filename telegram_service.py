# -*- coding: utf-8 -*-
"""
Module tương thích trực tiếp telegram_service.py ở thư mục gốc (theo Tài liệu số 02).
Cung cấp hàm trigger_parent_alert(), track_ai_chatbot_usage() cho các module thành viên khác import,
đồng thời hỗ trợ chạy độc lập giao diện cấu hình nhanh tại cổng 5001 nếu gọi riêng.
"""

from services.telegram_service import (
    build_status_report_html,
    handle_incoming_command,
    start_telegram_command_poller,
    stop_telegram_command_poller,
    trigger_parent_alert,
)
from core.config_manager import load_config, load_runtime_state, safe_log, update_runtime_state


def track_ai_chatbot_usage(current_url: str, window_title: str, interval_sec: int = 2) -> None:
    """Hàm tương thích Tài liệu 02: Cộng dồn thời gian dùng AI và cảnh báo khi vượt ngưỡng."""
    cfg = load_config()
    ai_domains = [d.lower() for d in cfg.get("ai_domains", ["chatgpt.com", "claude.ai", "gemini.google.com"])]
    max_minutes = int(cfg.get("max_ai_minutes", 15))

    target_str = f"{current_url} {window_title}".lower()
    if any(domain in target_str for domain in ai_domains):
        state = load_runtime_state()
        total_sec = int(state.get("ai_seconds", 0)) + int(interval_sec)
        update_runtime_state(ai_seconds=total_sec)
        if total_sec >= max_minutes * 60:
            used_min = round(total_sec / 60.0, 1)
            trigger_parent_alert(
                "AI_OVERUSE",
                f"Học sinh đã tra cứu Chatbot AI ({current_url or window_title}) tổng cộng {used_min} phút "
                f"(Vượt giới hạn {max_minutes} phút)!",
            )


__all__ = [
    "trigger_parent_alert",
    "track_ai_chatbot_usage",
    "handle_incoming_command",
    "build_status_report_html",
    "start_telegram_command_poller",
    "stop_telegram_command_poller",
]

if __name__ == "__main__":
    from trang_web import app, get_local_lan_ip

    start_telegram_command_poller()
    safe_log(
        f">>> Dịch vụ Bot Telegram 2 chiều & Web Cấu hình đang chạy tại: "
        f"http://127.0.0.1:5000 (LAN: http://{get_local_lan_ip()}:5000)"
    )
    app.run(host="0.0.0.0", port=5000, debug=False)
