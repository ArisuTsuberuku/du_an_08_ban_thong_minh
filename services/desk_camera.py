# -*- coding: utf-8 -*-
"""
Camera MẶT BÀN tùy chọn (services/desk_camera.py) — chuẩn hóa góc nhìn vùng tay khi webcam chính không thấy tay.

Vì sao cần: webcam trên màn hình chỉ thấy mặt + vai; khi cúi viết, bàn tay nằm dưới mép khung -> không phân biệt
được "đang viết" hay "đang nghịch đồ dưới gầm bàn". Một camera thứ 2 (webcam USB rẻ kẹp đèn bàn / giá đỡ chúc
thẳng xuống vở, hoặc điện thoại cũ làm webcam) cho góc nhìn chuẩn, cố định từ trên xuống.

Chạy nền ~5 FPS trên ảnh 480px:
  - HandLandmarker (2 tay): đếm tay, vi chuyển động đầu ngón trỏ chuẩn hóa theo cỡ bàn tay -> "đang viết".
  - ObjectWatcher: điện thoại / đồ chơi / sách; đồ vật chứa điểm bàn tay = ĐANG CẦM.
Bật trong shared_config: "desk_camera_enabled": true, "desk_camera_name": "<một phần tên webcam>" (hoặc
"desk_camera_index"). Webcam chính tự bị loại khỏi danh sách chọn.
"""

import math
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

from core.config_manager import safe_log

HAND_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
                  "hand_landmarker/float16/latest/hand_landmarker.task")
PROC_WIDTH = 480
WRITING_MOTION = 0.03     # std đầu ngón trỏ / cỡ bàn tay trong 3s >= 0.03 => đang viết / thao tác


class DeskCamera:
    def __init__(self, cfg: Dict[str, Any], main_camera_name: Callable[[], str] = lambda: ""):
        self.cfg = cfg
        self.main_camera_name = main_camera_name
        self.ok = False
        self.info: Dict[str, Any] = {}
        self._latest: Optional[Dict[str, Any]] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tips: Deque[Tuple[float, float, float]] = deque()
        self.latest_jpeg: bytes = b""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="DeskCameraThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self, max_age: float = 2.0) -> Optional[Dict[str, Any]]:
        d = self._latest
        if d and time.time() - d["t"] <= max_age:
            return d
        return None

    def _open(self):
        from services.camera_selector import open_best_camera

        time.sleep(4.0)   # để webcam chính mở trước, tránh 2 luồng giành cùng thiết bị
        main = self.main_camera_name() or ""
        sub = {
            "camera_preferred_names": [self.cfg.get("desk_camera_name", "")] if self.cfg.get("desk_camera_name") else [],
            "camera_ignored_names": list(self.cfg.get("camera_ignored_names") or []) + ([main] if main else []),
            "camera_index": int(self.cfg.get("desk_camera_index", 1)),
        }
        return open_best_camera(sub, fallback_index=sub["camera_index"], width=640, height=480)

    def _run(self) -> None:
        cap = None
        hands = None
        try:
            import mediapipe as mp
            from mediapipe.tasks.python.core import base_options
            from mediapipe.tasks.python import vision
            from services.posture_ai_engine import ObjectWatcher, ensure_model_file

            cap, info = self._open()
            self.info = info
            if cap is None:
                safe_log(f"[DESK-CAM] Không mở được camera mặt bàn: {info.get('reason')} {info.get('devices')}")
                return
            safe_log(f"[DESK-CAM] Đã mở camera mặt bàn '{info.get('name')}'")
            path = ensure_model_file("hand_landmarker.task", HAND_MODEL_URL)
            with open(path, "rb") as fh:
                buf = fh.read()
            hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
                base_options=base_options.BaseOptions(model_asset_buffer=buf),
                running_mode=vision.RunningMode.VIDEO, num_hands=2,
                min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))
            objects = ObjectWatcher(interval_s=1.0)
            self.ok = True
            last_ts = 0
            while not self._stop.is_set():
                ret, frame = cap.read()
                if not ret or frame is None:
                    time.sleep(0.5)
                    continue
                if frame.shape[1] > PROC_WIDTH:
                    s = PROC_WIDTH / frame.shape[1]
                    frame = cv2.resize(frame, (PROC_WIDTH, int(frame.shape[0] * s)), interpolation=cv2.INTER_AREA)
                now = time.time()
                ts = max(last_ts + 1, int(time.monotonic() * 1000))
                last_ts = ts
                img = mp.Image(image_format=mp.ImageFormat.SRGB,
                               data=np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
                hr = hands.detect_for_video(img, ts)
                hand_pts: List[Tuple[float, float]] = []
                for hl in hr.hand_landmarks or []:
                    hand_pts += [(p.x, p.y) for p in hl]
                if hr.hand_landmarks:
                    hl = hr.hand_landmarks[0]
                    size = math.hypot(hl[9].x - hl[0].x, hl[9].y - hl[0].y) or 1e-3
                    self._tips.append((now, hl[8].x / size, hl[8].y / size))
                while self._tips and now - self._tips[0][0] > 3.0:
                    self._tips.popleft()
                motion = 0.0
                if len(self._tips) >= 6:
                    xs = [x for _, x, _ in self._tips]
                    ys = [y for _, _, y in self._tips]
                    motion = float(np.std(xs) + np.std(ys))
                objs = []
                for o in objects.detect(frame, now):
                    o = dict(o)
                    o["held"] = bool(hand_pts) and ObjectWatcher.held_by(o, hand_pts)
                    objs.append(o)
                self._latest = {"t": now, "hands": len(hr.hand_landmarks or []), "motion": round(motion, 4),
                                "writing": bool(hr.hand_landmarks) and motion >= WRITING_MOTION, "objects": objs}
                self._stop.wait(0.2)
        except Exception as exc:
            safe_log(f"[DESK-CAM] Dừng do lỗi: {exc}")
        finally:
            self.ok = False
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            if hands is not None:
                try:
                    hands.close()
                except Exception:
                    pass
