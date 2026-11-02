# -*- coding: utf-8 -*-
"""
Tệp chạy độc lập giam_sat.pyw (tương thích ngược 100% với báo cáo tiến độ của nhóm).
Khi chạy trực tiếp phần mềm trung tâm FocusDeskLauncher.exe / he_thong_quan_ly_3_cap.py,
luồng BoGiamSatThongMinh đã được nhúng sẵn bên trong nên không cần mở riêng tệp này.
"""

import time

from core.config_manager import ensure_data_files_exist, safe_log
from services.giam_sat_worker import (
    BAD_APPS,
    BAD_TABS,
    URL_WEB,
    BoGiamSatThongMinh,
    gui_canh_bao_len_web,
)

__all__ = [
    "BAD_TABS",
    "BAD_APPS",
    "URL_WEB",
    "gui_canh_bao_len_web",
    "BoGiamSatThongMinh",
]

if __name__ == "__main__":
    ensure_data_files_exist()
    monitor = BoGiamSatThongMinh()
    safe_log(">>> Đã khởi chạy độc lập bộ giám sát ngầm giam_sat.pyw (Nhấn Ctrl+C hoặc tạo UNLOCK.txt để dừng)...")
    monitor.bat_dau()
    try:
        while monitor.dang_chay:
            time.sleep(1.0)
    except KeyboardInterrupt:
        monitor.dung_lai()
