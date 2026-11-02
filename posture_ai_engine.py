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
    from services.posture_ai_engine import LABEL_KEYS

    ensure_data_files_exist()
    cfg = load_config()
    cam_idx = int(cfg.get("camera_index", 0))
    engine = PostureAIEngine(camera_index=cam_idx, open_camera=True)
    safe_log(
        ">>> Cửa sổ AI Tư thế 3D: 'r' hiệu chỉnh lại 5s | 'q' thoát | 'l' bật/tắt ghi log đặc trưng\n"
        "    Gán NHÃN thực tế (để đo độ chính xác): 1 Nhìn màn hình | 2 Cúi viết | 3 Cúi đọc | "
        "4 Mất tập trung | 5 Buồn ngủ | 6 Vắng mặt | 0 Bỏ nhãn"
    )
    try:
        while True:
            jpeg_bytes, telemetry = engine.process_frame()
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            vis_frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if vis_frame is not None:
                if engine.log_features:
                    cv2.circle(vis_frame, (vis_frame.shape[1] - 18, 100), 7, (0, 0, 255), -1)  # chấm đỏ = đang ghi log
                cv2.imshow("Smart Study Desk - AI Nhan Dien Tu The 3D (MediaPipe)", vis_frame)
            key = cv2.waitKey(30) & 0xFF
            if key == ord("q") or key == 27:
                break
            if key == ord("r"):
                engine.reset_calibration()
            elif key == ord("l"):
                engine.log_features = not engine.log_features
                safe_log(f"[LOG] Ghi log đặc trưng: {'BẬT' if engine.log_features else 'TẮT'} -> thư mục logs/")
            elif key in LABEL_KEYS:
                engine.current_label = LABEL_KEYS[key]
                safe_log(f"[NHÃN] {engine.current_label or '(bỏ nhãn)'}")
    finally:
        engine.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        run_daemon_mode()
    else:
        run_interactive_preview()
