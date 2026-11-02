# -*- coding: utf-8 -*-
"""
Thiết lập camera bằng TÊN + ẢNH XEM TRƯỚC (services/camera_setup.py) — thay cho gõ số thứ tự 0/1 khó hiểu.

- list_cameras(): danh sách webcam thật theo tên Windows (vd "FHD Camera", "HD Webcam"), đã bỏ camera ảo.
- grab_preview(index): chụp 1 khung nhỏ + tìm khuôn mặt (Haar cascade có sẵn trong OpenCV, không cần mô hình ngoài).
- auto_assign(): chụp thử mọi camera -> camera thấy MẶT RÕ NHẤT = camera chính (mặt + vai);
  camera còn lại thấy ít/không có mặt = camera mặt bàn (tay + vở).
- apply_assignment(): ghi vào shared_config theo TÊN (camera_preferred_names / desk_camera_name) nên đổi cổng USB
  hay cắm thêm camera thì vẫn đúng.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from services.camera_selector import IS_WINDOWS, _backend_list, _match_any, _try_open, list_dshow_devices

VIRTUAL_HINTS = ["obs", "virtual", "manycam", "droidcam", "iriun", "snap camera", "xsplit", "nvidia broadcast"]
_face = None


def _face_detector():
    global _face
    if _face is None:
        _face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _face


def list_cameras(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    ignored = list((cfg or {}).get("camera_ignored_names") or []) + VIRTUAL_HINTS
    names = list_dshow_devices()
    if not names and not IS_WINDOWS:
        names = ["Camera #0"]
    return [{"index": i, "name": n, "virtual": _match_any(n, ignored)} for i, n in enumerate(names)]


def grab_preview(index: int, width: int = 320) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """Mở nhanh camera, lấy 1 khung (bỏ 5 khung đầu cho cân sáng), trả (ảnh BGR thu nhỏ, thông tin mặt)."""
    for _name, be in _backend_list():
        cap, why = _try_open(index, be, 640, 480, warmup_s=2.0)
        if cap is None:
            continue
        frame = None
        try:
            for _ in range(6):
                ok, f = cap.read()
                if ok and f is not None:
                    frame = f
                time.sleep(0.03)
        finally:
            cap.release()
        if frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _face_detector().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        best = max((w * h for (_x, _y, w, h) in faces), default=0)
        face_ratio = best / float(frame.shape[0] * frame.shape[1])
        scale = width / frame.shape[1]
        small = cv2.resize(frame, (width, int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        for (x, y, w, h) in faces:
            cv2.rectangle(small, (int(x * scale), int(y * scale)), (int((x + w) * scale), int((y + h) * scale)),
                          (80, 200, 120), 2)
        return small, {"ok": True, "faces": len(faces), "face_ratio": round(face_ratio, 4),
                       "dark": float(gray.mean()) < 25}
    return None, {"ok": False, "faces": 0, "face_ratio": 0.0, "error": "Không mở được (đang bị app khác dùng?)"}


def auto_assign(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cams = [c for c in list_cameras(cfg) if not c["virtual"]]
    results = []
    for c in cams:
        img, info = grab_preview(c["index"])
        results.append({**c, **info, "image": img})
    usable = [r for r in results if r.get("ok")]
    if not usable:
        return {"main": None, "desk": None, "results": results}
    main = max(usable, key=lambda r: (r["faces"] > 0, r["face_ratio"]))
    others = [r for r in usable if r is not main]
    desk = min(others, key=lambda r: r["face_ratio"]) if others else None
    return {"main": main["name"], "desk": desk["name"] if desk else None, "results": results}


def apply_assignment(cfg: Dict[str, Any], main_name: Optional[str], desk_name: Optional[str]) -> Dict[str, Any]:
    """Ghi lựa chọn theo tên vào cấu hình (chưa save — người gọi tự save_config)."""
    if main_name:
        cfg["camera_preferred_names"] = [main_name]
        names = [c["name"] for c in list_cameras(cfg)]
        if main_name in names:
            cfg["camera_index"] = names.index(main_name)
    ignored = [n for n in (cfg.get("camera_ignored_names") or []) if n not in (main_name, desk_name)]
    cfg["camera_ignored_names"] = ignored
    if desk_name and desk_name != main_name:
        cfg["desk_camera_enabled"] = True
        cfg["desk_camera_name"] = desk_name
    else:
        cfg["desk_camera_enabled"] = False
        cfg["desk_camera_name"] = ""
    return cfg
