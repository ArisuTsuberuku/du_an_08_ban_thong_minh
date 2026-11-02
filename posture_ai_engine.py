# -*- coding: utf-8 -*-
"""
Entry-point độc lập posture_ai_engine.py (theo Tài liệu số 03).
Hỗ trợ 2 chế độ:
  1. `python posture_ai_engine.py`          -> Mở cửa sổ trực quan xem thử AI Nhận diện Tư thế 3D + đẩy lên Web :5000.
  2. `pythonw posture_ai_engine.py --daemon` -> Chạy ngầm không cửa sổ (Sidecar cho SmartDeskLauncher.exe),
     tự động dừng nếu có tín hiệu UNLOCK.txt hoặc tắt từ Bảng điều khiển Web.
"""

import sys
import time
from typing import Optional

import cv2
import numpy as np
import psutil

from core.config_manager import ensure_data_files_exist, load_config, safe_log
from core.failsafe import check_and_consume_unlock_file
from services.posture_ai_engine import PostureAIEngine, PostureAIServiceWorker

__all__ = ["PostureAIEngine", "PostureAIServiceWorker"]


def _parse_parent_pid() -> Optional[int]:
    for i, arg in enumerate(sys.argv):
        if arg == "--parent-pid" and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                return None
    return None


def run_daemon_mode() -> None:
    ensure_data_files_exist()
    cfg = load_config()
    cam_idx = int(cfg.get("camera_index", 0))
    parent_pid = _parse_parent_pid()
    worker = PostureAIServiceWorker(camera_index=cam_idx, push_via_http=True)
    safe_log(">>> Đã khởi chạy ngầm dịch vụ AI Tư thế 3D (Sidecar Daemon) đẩy dữ liệu lên cổng 5000...")
    worker.start()
    try:
        while worker.running:
            if parent_pid is not None and not psutil.pid_exists(parent_pid):
                break
            if check_and_consume_unlock_file():
                break
            cfg_now = load_config()
            if not cfg_now.get("enable_posture_ai", True):
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        time.sleep(0.3)


def run_interactive_preview() -> None:
    ensure_data_files_exist()
    cfg = load_config()
    cam_idx = int(cfg.get("camera_index", 0))
    engine = PostureAIEngine(camera_index=cam_idx, open_camera=True)
    safe_log(">>> Đang mở cửa sổ xem trực tiếp AI Nhận diện Tư thế 3D (Nhấn 'r' để hiệu chỉnh lại 5s, 'q' để thoát)...")
    try:
        while True:
            jpeg_bytes, telemetry = engine.process_frame()
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            vis_frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if vis_frame is not None:
                cv2.imshow("Smart Study Desk - AI Nhan Dien Tu The 3D (MediaPipe)", vis_frame)
            key = cv2.waitKey(30) & 0xFF
            if key == ord("q") or key == 27:
                break
            if key == ord("r"):
                engine.reset_calibration()
    finally:
        engine.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        run_daemon_mode()
    else:
        run_interactive_preview()
