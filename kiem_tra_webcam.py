# -*- coding: utf-8 -*-
"""
Công cụ chẩn đoán Webcam cho AI Tư thế 3D.
Chạy:  python kiem_tra_webcam.py          (chẩn đoán + mở cửa sổ xem thử 10 giây nếu mở được)
       python kiem_tra_webcam.py --no-view (chỉ in kết quả chẩn đoán)
"""

import json
import sys
import time

import cv2

from core.config_manager import configure_utf8_stdio, ensure_data_files_exist, load_config
from services.camera_selector import (
    detect_antivirus,
    list_dshow_devices,
    open_best_camera,
    probe_dshow_access,
    rank_camera_candidates,
)


def main() -> int:
    configure_utf8_stdio()
    ensure_data_files_exist()
    cfg = load_config()

    print("=" * 70)
    print(" CHẨN ĐOÁN WEBCAM - AI TƯ THẾ 3D (Smart Study Desk)")
    print("=" * 70)
    print(f"Python      : {sys.executable}")
    print(f"OpenCV      : {cv2.__version__}")

    devices = list_dshow_devices()
    print(f"Thiết bị    : {devices or '(không thấy thiết bị DirectShow nào)'}")
    ranked = rank_camera_candidates(
        devices,
        cfg.get("camera_preferred_names") or [],
        cfg.get("camera_ignored_names") or [],
        int(cfg.get("camera_index", 0)),
    )
    print(f"Thứ tự thử  : {[(i, devices[i] if i < len(devices) else '?') for i in ranked]}")
    av = detect_antivirus()
    print(f"Antivirus   : {av or 'không phát hiện'}")
    for i in ranked[:3]:
        code, detail = probe_dshow_access(i)
        print(f"Quyền idx {i} : {code}  {detail}")

    print("-" * 70)
    t0 = time.time()
    cap, info = open_best_camera(cfg)
    print(f"Kết quả     : {info['reason']} ({time.time() - t0:.1f}s) - {info['message']}")
    if info.get("hint"):
        print(f"Khắc phục   : {info['hint']}")

    if cap is None:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 1

    print(f"Đã mở       : '{info['name']}' index={info['index']} backend={info['backend']}")
    if "--no-view" in sys.argv:
        cap.release()
        return 0

    print("Đang mở cửa sổ xem thử 10 giây (nhấn q để thoát sớm)...")
    end = time.time() + 10
    frames = 0
    while time.time() < end:
        ok, frame = cap.read()
        if not ok:
            continue
        frames += 1
        cv2.putText(frame, f"{info['name']} | idx {info['index']} | {info['backend']}", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.imshow("Kiem tra Webcam", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
    print(f"Đọc được {frames} khung hình. Webcam OK -> có thể chạy: python posture_ai_engine.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
