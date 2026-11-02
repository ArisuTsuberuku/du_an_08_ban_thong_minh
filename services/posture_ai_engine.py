# -*- coding: utf-8 -*-
"""
Mô-đun AI Nhận Diện Tư Thế Ngồi Học 3D tại Biên (services/posture_ai_engine.py)
Thực thi theo Tài liệu kỹ thuật số 03 (MediaPipe Holistic + Hình học Không gian 3D):
  - Tương thích kép: Hỗ trợ cả MediaPipe >= 1.0 (Tasks API HolisticLandmarker với models/holistic_landmarker.task)
    và MediaPipe 0.10.x (mp.solutions.holistic) để chạy trên mọi phiên bản Python (kể cả Python 3.14).
  - Hỗ trợ ghép nối tùy chọn mô hình Google Teachable Machine (models/keras_model.h5) theo tỷ trọng 70% Hình học + 30% TM.
  - Đầy đủ công thức Toán học Báo cáo KHKT:
      1. Tỷ lệ mở mắt 6 điểm (EAR_t) & Chỉ số buồn ngủ cửa sổ trượt (PERCLOS) + Chống giả mạo ảnh tĩnh (PAD >25s).
      2. Tỷ lệ mở miệng 8 điểm (MAR_t > 0.60 liên tục >= 1.5s) & Bộ đếm tần suất ngáp (F_yawn).
      3. Tư thế đầu 3D thực thụ bằng cv2.solvePnP suy ra đủ 3 góc Euler (Pitch, Yaw, Roll) & Sự kiện ngủ gật E_nod (>15° trong 1.5s).
      4. Hiệu chỉnh Gốc Tư thế Cá nhân hóa tự động trong 5 giây đầu (Baseline Auto-Calibration).
      5. Phát hiện 5 lỗi công thái học + Theo dõi 2 bàn tay trên mặt bàn (>15s) + Chặn Camera ảo (OBS, ManyCam, VCam).
  - Nguyên tắc Local-first, Zero-cloud: Xử lý 100% trên RAM cục bộ, không tải ảnh lên đám mây.
"""

import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import psutil
import requests

from core.config_manager import get_app_dir, load_config, safe_log, update_runtime_state

HOLISTIC_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "holistic_landmarker/holistic_landmarker/float16/latest/holistic_landmarker.task"
)

# 6 điểm mốc chuẩn nhân trắc học 3D khuôn mặt người (đơn vị mm tương đối) dùng cho cv2.solvePnP
# Thứ tự tương ứng với chỉ số Face Mesh: [1: Chóp mũi, 152: Cằm, 33: Đuôi mắt trái, 263: Đuôi mắt phải, 61: Khóe miệng trái, 291: Khóe miệng phải]
FACE_3D_MODEL_POINTS = np.array(
    [
        (0.0, 0.0, 0.0),          # Chóp mũi (1)
        (0.0, -330.0, -65.0),     # Cằm (152)
        (-225.0, 170.0, -135.0),  # Đuôi mắt trái (33)
        (225.0, 170.0, -135.0),   # Đuôi mắt phải (263)
        (-150.0, -150.0, -125.0), # Khóe miệng trái (61)
        (150.0, -150.0, -125.0),  # Khóe miệng phải (291)
    ],
    dtype=np.float64,
)
SOLVEPNP_LANDMARK_IDS = (1, 152, 33, 263, 61, 291)

# Chỉ số 6 điểm quanh mắt chuẩn của MediaPipe Face Mesh để tính EAR_t
LEFT_EYE_IDS = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_IDS = (362, 385, 387, 263, 373, 380)

# Chỉ số 8 điểm môi trong của MediaPipe Face Mesh để tính MAR_t
MOUTH_OUTER_CORNERS = (78, 308)
MOUTH_VERTICAL_PAIRS = ((81, 178), (13, 14), (311, 402))

VIRTUAL_CAMERA_BLACKLIST = {
    "obs64.exe",
    "obs32.exe",
    "manycam.exe",
    "vcam.exe",
    "splitcam.exe",
    "droidcamapp.exe",
    "iruinwebcam.exe",
}


def resolve_models_dir() -> str:
    """Tìm thư mục models/ cạnh mã nguồn hoặc cạnh file .exe."""
    candidates = [
        os.path.join(get_app_dir(), "models"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    os.makedirs(candidates[0], exist_ok=True)
    return candidates[0]


def ensure_holistic_task_model() -> str:
    """Đảm bảo tệp models/holistic_landmarker.task tồn tại cục bộ (tự tải 1 lần duy nhất nếu thiếu)."""
    models_dir = resolve_models_dir()
    model_path = os.path.join(models_dir, "holistic_landmarker.task")
    if os.path.exists(model_path) and os.path.getsize(model_path) > 1_000_000:
        return model_path

    safe_log("[AI] Đang tải mô hình MediaPipe Holistic chuẩn về lưu trữ cục bộ (chỉ tải 1 lần đầu)...")
    resp = requests.get(HOLISTIC_MODEL_URL, timeout=30)
    resp.raise_for_status()
    with open(model_path, "wb") as f:
        f.write(resp.content)
    return model_path


class _UnifiedHolisticResult:
    """Cấu trúc kết quả chuẩn hóa giữa MediaPipe Tasks API (1.x) và Solutions API (0.10.x)."""

    def __init__(self, face_lm=None, pose_lm=None, left_hand_lm=None, right_hand_lm=None):
        self.face_landmarks = face_lm
        self.pose_landmarks = pose_lm
        self.left_hand_landmarks = left_hand_lm
        self.right_hand_landmarks = right_hand_lm


class PostureAIEngine:
    """Bộ xử lý Thị giác Máy tính nhận diện tư thế 3D, buồn ngủ và chống gian lận tại biên."""

    def __init__(self, camera_index: int = 0, open_camera: bool = True):
        self.camera_index = camera_index
        self._auto_open_camera = open_camera
        self._last_cam_probe_time = 0.0
        self.cap: Optional[cv2.VideoCapture] = None
        if open_camera:
            self._open_camera(force=True)

        # Khởi tạo MediaPipe Holistic (Tự động chọn Tasks API hoặc Legacy Solutions API)
        import mediapipe as mp

        self._mp = mp
        self._use_tasks_api = not hasattr(mp, "solutions")
        self._last_ts_ms = 0

        if self._use_tasks_api:
            from mediapipe.tasks.python.core import base_options
            from mediapipe.tasks.python import vision

            model_path = ensure_holistic_task_model()
            # Đọc qua model_asset_buffer để không bao giờ lỗi đường dẫn tiếng Việt có dấu trên Windows
            with open(model_path, "rb") as f:
                model_bytes = f.read()

            opts = vision.HolisticLandmarkerOptions(
                base_options=base_options.BaseOptions(model_asset_buffer=model_bytes),
                running_mode=vision.RunningMode.VIDEO,
                min_face_detection_confidence=0.55,
                min_face_landmarks_confidence=0.55,
                min_pose_detection_confidence=0.55,
                min_pose_landmarks_confidence=0.55,
                min_hand_landmarks_confidence=0.50,
            )
            self._landmarker = vision.HolisticLandmarker.create_from_options(opts)
            self._legacy_holistic = None
        else:
            self._legacy_holistic = mp.solutions.holistic.Holistic(
                min_detection_confidence=0.6,
                min_tracking_confidence=0.6,
            )
            self._landmarker = None

        # Tùy chọn ghép nối mô hình Google Teachable Machine (nếu thành viên nhóm đặt keras_model.h5 vào models/)
        self._tm_model = self._try_load_teachable_machine_model()

        # Trạng thái hiệu chỉnh gốc cá nhân hóa (5 giây đầu)
        self.calibrated = False
        self.calib_start_time = time.time()
        self.calib_samples: List[Tuple[float, float, float, float]] = []
        self.base_eye_span = 0.16
        self.base_neck_height = 0.22
        self.base_shoulder_diff = 0.0
        self.base_pitch_deg = 0.0

        # Các cửa sổ thời gian trượt cho PERCLOS, MAR ngáp, E_nod, Yaw và PAD chống ảnh tĩnh
        now = time.time()
        self.last_blink_time = now
        self.last_hand_time = now
        self.last_face_seen_time = now
        self.yawn_start_time: Optional[float] = None
        self.yawn_counted_current = False
        self.yawn_count = 0
        self.yaw_distracted_since: Optional[float] = None

        self.ear_window: List[int] = []
        self.ear_raw_window: List[float] = []
        self.pitch_history: List[Tuple[float, float]] = []
        self._last_vcam_check = 0.0
        self._cached_vcam: Tuple[bool, str] = (False, "")

    def _open_camera(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_cam_probe_time < 5.0):
            return
        self._last_cam_probe_time = now
        try:
            # Ưu tiên CAP_DSHOW trên Windows để khởi tạo Webcam nhanh và tránh xung đột driver
            cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(self.camera_index)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self.cap = cap
            else:
                cap.release()
                self.cap = None
        except Exception:
            self.cap = None

    @staticmethod
    def _try_load_teachable_machine_model():
        """Nếu có models/keras_model.h5 của thành viên làm Teachable Machine thì tự nạp để kết hợp 70/30."""
        h5_path = os.path.join(resolve_models_dir(), "keras_model.h5")
        if not os.path.exists(h5_path):
            return None
        try:
            from tensorflow.keras.models import load_model  # type: ignore

            safe_log(f"[AI] Đã phát hiện và nạp song song mô hình Teachable Machine: {h5_path}")
            return load_model(h5_path, compile=False)
        except Exception:
            return None

    def reset_calibration(self) -> None:
        """Đặt lại chế độ Hiệu chỉnh Gốc Tư thế Cá nhân hóa (5 giây) theo yêu cầu từ Web/App."""
        self.calibrated = False
        self.calib_samples.clear()
        self.calib_start_time = time.time()
        now = time.time()
        self.last_blink_time = now
        self.last_hand_time = now
        safe_log("[AI] Đã kích hoạt hiệu chỉnh lại tư thế gốc cá nhân (5 giây)...")

    # =====================================================================
    # CÁC CÔNG THỨC TOÁN HỌC HÌNH HỌC 3D (EAR, MAR, SOLVEPNP EULER)
    # =====================================================================
    @staticmethod
    def _dist2d(p1, p2) -> float:
        return float(np.hypot(p1.x - p2.x, p1.y - p2.y))

    @classmethod
    def compute_ear_6pt(cls, lm: Sequence[Any], eye_ids: Sequence[int]) -> float:
        """
        Công thức EAR chuẩn 6 điểm mốc:
        EAR = (||p2 - p6|| + ||p3 - p5||) / (2 * ||p1 - p4||)
        """
        p1, p2, p3, p4, p5, p6 = (lm[idx] for idx in eye_ids)
        vertical_1 = cls._dist2d(p2, p6)
        vertical_2 = cls._dist2d(p3, p5)
        horizontal = cls._dist2d(p1, p4)
        return (vertical_1 + vertical_2) / (2.0 * horizontal + 1e-6)

    @classmethod
    def compute_mar_8pt(cls, lm: Sequence[Any]) -> float:
        """
        Công thức MAR chuẩn 8 điểm môi trong:
        MAR = (||v1_top - v1_bot|| + ||v2_top - v2_bot|| + ||v3_top - v3_bot||) / (3 * ||left - right||)
        """
        left_c, right_c = lm[MOUTH_OUTER_CORNERS[0]], lm[MOUTH_OUTER_CORNERS[1]]
        horiz = cls._dist2d(left_c, right_c)
        vert_sum = sum(cls._dist2d(lm[t], lm[b]) for t, b in MOUTH_VERTICAL_PAIRS)
        return vert_sum / (3.0 * horiz + 1e-6)

    @staticmethod
    def compute_head_pose_solvepnp(
        lm: Sequence[Any], frame_w: int, frame_h: int
    ) -> Tuple[float, float, float]:
        """
        Sử dụng cv2.solvePnP chiếu 6 điểm mốc 2D khuôn mặt lên mô hình 3D chuẩn
        để suy ra chính xác 3 góc Euler: (pitch_deg, yaw_deg, roll_deg).
        """
        pts_2d = np.array(
            [(lm[idx].x * frame_w, lm[idx].y * frame_h) for idx in SOLVEPNP_LANDMARK_IDS],
            dtype=np.float64,
        )
        focal_length = float(frame_w)
        center = (frame_w / 2.0, frame_h / 2.0)
        cam_matrix = np.array(
            [
                [focal_length, 0.0, center[0]],
                [0.0, focal_length, center[1]],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

        success, rvec, _tvec = cv2.solvePnP(
            FACE_3D_MODEL_POINTS,
            pts_2d,
            cam_matrix,
            dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not success:
            return 0.0, 0.0, 0.0

        rmat, _ = cv2.Rodrigues(rvec)
        angles, _, _, _, _, _ = cv2.RQDecomp3x3(rmat)
        pitch = float(angles[0])
        yaw = float(angles[1])
        # Roll tính trực tiếp từ vector nối hai đuôi mắt (ổn định tuyệt đối không bị nhảy dấu góc phần tư)
        roll = float(np.degrees(np.arctan2(lm[263].y - lm[33].y, lm[263].x - lm[33].x)))
        # Chuẩn hóa Pitch khi trục Z mô hình đối xứng qua +/-180°
        if pitch > 90.0:
            pitch -= 180.0
        elif pitch < -90.0:
            pitch += 180.0
        return pitch, yaw, roll

    def check_virtual_camera_spoofing(self) -> Tuple[bool, str]:
        """Chặn đứng trình điều khiển Camera ảo (OBS, ManyCam, VCam...) - làm mới mỗi 3 giây."""
        now = time.time()
        if now - self._last_vcam_check < 3.0:
            return self._cached_vcam
        self._last_vcam_check = now

        for p in psutil.process_iter(["name"]):
            try:
                pname = (p.info.get("name") or "").lower()
                if pname in VIRTUAL_CAMERA_BLACKLIST:
                    self._cached_vcam = (True, p.info["name"])
                    return self._cached_vcam
            except Exception:
                continue
        self._cached_vcam = (False, "")
        return self._cached_vcam

    def _detect_landmarks(self, rgb_frame: np.ndarray) -> _UnifiedHolisticResult:
        if self._use_tasks_api:
            mp_img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb_frame)
            ts_ms = int(time.monotonic() * 1000)
            if ts_ms <= self._last_ts_ms:
                ts_ms = self._last_ts_ms + 1
            self._last_ts_ms = ts_ms

            res = self._landmarker.detect_for_video(mp_img, ts_ms)
            return _UnifiedHolisticResult(
                face_lm=res.face_landmarks if res.face_landmarks else None,
                pose_lm=res.pose_landmarks if res.pose_landmarks else None,
                left_hand_lm=res.left_hand_landmarks if res.left_hand_landmarks else None,
                right_hand_lm=res.right_hand_landmarks if res.right_hand_landmarks else None,
            )
        else:
            res = self._legacy_holistic.process(rgb_frame)
            return _UnifiedHolisticResult(
                face_lm=res.face_landmarks.landmark if res.face_landmarks else None,
                pose_lm=res.pose_landmarks.landmark if res.pose_landmarks else None,
                left_hand_lm=res.left_hand_landmarks.landmark if res.left_hand_landmarks else None,
                right_hand_lm=res.right_hand_landmarks.landmark if res.right_hand_landmarks else None,
            )

    def _calibrate_step(
        self, eye_span: float, neck_height: float, shoulder_diff: float, raw_pitch: float
    ) -> None:
        self.calib_samples.append((eye_span, neck_height, shoulder_diff, raw_pitch))
        elapsed = time.time() - self.calib_start_time
        # Hoàn tất hiệu chỉnh sau khi đủ >= 25 mẫu hợp lệ và tối thiểu 3.5 - 5 giây đầu
        if len(self.calib_samples) >= 30 or (len(self.calib_samples) >= 15 and elapsed >= 5.0):
            arr = np.array(self.calib_samples, dtype=np.float64)
            self.base_eye_span = float(np.mean(arr[:, 0]))
            self.base_neck_height = max(0.05, float(np.mean(arr[:, 1])))
            self.base_shoulder_diff = float(np.mean(arr[:, 2]))
            self.base_pitch_deg = float(np.mean(arr[:, 3]))
            self.calibrated = True
            safe_log(
                f"[CALIBRATED] Gốc chuẩn cá nhân: EyeSpan={self.base_eye_span:.3f}, "
                f"Neck={self.base_neck_height:.3f}, Pitch0={self.base_pitch_deg:.1f}°"
            )

    def _build_no_camera_placeholder(self) -> Tuple[bytes, Dict[str, Any]]:
        """Tạo khung hình thông báo trực quan khi máy chưa gắn Webcam vật lý."""
        canvas = np.zeros((360, 640, 3), dtype=np.uint8)
        canvas[:] = (22, 18, 12)
        cv2.rectangle(canvas, (0, 0), (640, 44), (30, 41, 59), -1)
        cv2.putText(
            canvas,
            "AI POSTURE ENGINE: CHUA KET NOI WEBCAM VAT LY",
            (18, 29),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (56, 189, 248),
            2,
        )
        cv2.putText(
            canvas,
            "Hay cam Webcam USB de kich hoat nhan dien tu the 3D",
            (62, 185),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (203, 213, 225),
            1,
        )
        cv2.putText(
            canvas,
            "He thong van giam sat Web / URL / Chatbot AI binh thuong.",
            (55, 220),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (74, 222, 128),
            1,
        )
        _, jpg = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        telemetry = {
            "status": "NO_CAMERA",
            "camera_connected": False,
            "calibrated": False,
            "ear": 0.30,
            "perclos": 0.0,
            "mar": 0.10,
            "yawn_count": self.yawn_count,
            "pitch_deg": 0.0,
            "yaw_deg": 0.0,
            "roll_deg": 0.0,
            "drowsy_nod": False,
            "hands_on_desk": True,
            "visual_focus_score": 100.0,
            "cfi_score": 100.0,
            "alerts": [],
        }
        return jpg.tobytes(), telemetry

    def process_bgr_frame(self, frame: np.ndarray) -> Tuple[bytes, Dict[str, Any]]:
        """
        Xử lý trực tiếp 1 mảng ảnh BGR (cho phép kiểm thử tự động không cần phần cứng Webcam).
        Trả về (annotated_jpeg_bytes, telemetry_dict).
        """
        now = time.time()
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = self._detect_landmarks(rgb)

        alerts: List[str] = []
        ear = 0.30
        perclos = 0.0
        mar = 0.10
        pitch_deg = 0.0
        yaw_deg = 0.0
        roll_deg = 0.0
        drowsy_nod = False
        raw_solvepnp_pitch = 0.0

        # 1. Chặn Camera ảo (OBS, ManyCam, VCam...)
        is_vcam, vcam_name = self.check_virtual_camera_spoofing()
        if is_vcam:
            alerts.append(f"GIAN LẬN: Phát hiện Camera ảo ({vcam_name})")

        # 2. Phân tích Khuôn mặt (468+ điểm): EAR, PERCLOS, PAD, MAR ngáp, Góc Euler 3D solvePnP
        if res.face_landmarks and len(res.face_landmarks) >= 468:
            self.last_face_seen_time = now
            lm = res.face_landmarks

            ear_l = self.compute_ear_6pt(lm, LEFT_EYE_IDS)
            ear_r = self.compute_ear_6pt(lm, RIGHT_EYE_IDS)
            ear = (ear_l + ear_r) / 2.0

            self.ear_window.append(1 if ear < 0.20 else 0)
            self.ear_window = self.ear_window[-100:]
            self.ear_raw_window.append(ear)
            self.ear_raw_window = self.ear_raw_window[-60:]
            perclos = round((sum(self.ear_window) / len(self.ear_window)) * 100.0, 1)

            if ear < 0.20:
                self.last_blink_time = now
            elif now - self.last_blink_time > 25.0:
                alerts.append("CẢNH BÁO PAD: Không chớp mắt >25s (Nghi ảnh tĩnh)")

            if perclos > 35.0:
                alerts.append(f"BUỒN NGỦ: Chỉ số PERCLOS cao ({perclos}%)")

            # Tính MAR_t & phát hiện ngáp duy trì >= 1.5s
            mar = self.compute_mar_8pt(lm)
            if mar > 0.60:
                if self.yawn_start_time is None:
                    self.yawn_start_time = now
                    self.yawn_counted_current = False
                elif now - self.yawn_start_time >= 1.5:
                    alerts.append("MỆT MỎI: Phát hiện ngáp kéo dài (MAR > 0.60)")
                    if not self.yawn_counted_current:
                        self.yawn_count += 1
                        self.yawn_counted_current = True
            else:
                self.yawn_start_time = None
                self.yawn_counted_current = False

            # Giải bài toán cv2.solvePnP 3D lấy Pitch, Yaw, Roll
            raw_solvepnp_pitch, yaw_deg, roll_deg = self.compute_head_pose_solvepnp(lm, w, h)
            pitch_deg = raw_solvepnp_pitch - self.base_pitch_deg if self.calibrated else 0.0

            if abs(roll_deg) > 14.0 and self.calibrated:
                alerts.append(f"SAI TƯ THẾ: Nghiêng đầu sang vai ({roll_deg:.1f}°)")

            if abs(yaw_deg) > 26.0 and self.calibrated:
                if self.yaw_distracted_since is None:
                    self.yaw_distracted_since = now
                elif now - self.yaw_distracted_since >= 3.0:
                    alerts.append(f"MẤT TẬP TRUNG: Quay mặt sang ngang ({yaw_deg:.1f}°)")
            else:
                self.yaw_distracted_since = None

            eye_span = self._dist2d(lm[33], lm[263])

            # Vẽ viền định vị nhẹ quanh 2 mắt và mũi trên luồng giám sát
            for idx in SOLVEPNP_LANDMARK_IDS:
                px, py = int(lm[idx].x * w), int(lm[idx].y * h)
                cv2.circle(frame, (px, py), 2, (56, 189, 248), -1)
        else:
            eye_span = self.base_eye_span
            if now - self.last_face_seen_time > 2.5:
                alerts.append("VẮNG MẶT: Học sinh rời khỏi ghế ngồi")

        # 3. Phân tích Khung xương Vai & Cổ (Pose Landmarks) + Sự kiện ngủ gật E_nod
        if res.pose_landmarks and len(res.pose_landmarks) >= 13:
            plm = res.pose_landmarks
            left_sh, right_sh, nose = plm[11], plm[12], plm[0]
            mid_sh_y = (left_sh.y + right_sh.y) / 2.0
            neck_height = mid_sh_y - nose.y
            sh_diff = left_sh.y - right_sh.y

            if not self.calibrated and res.face_landmarks:
                self._calibrate_step(eye_span, neck_height, sh_diff, raw_solvepnp_pitch)

            if self.calibrated:
                # Lỗi 1: Cúi quá sát màn hình (nguy cơ cận thị)
                if eye_span > self.base_eye_span * 1.22:
                    alerts.append("CẬN THỊ: Cúi mặt quá sát màn hình/mặt bàn!")

                # Kết hợp độ sụt cổ và solvePnP để tính biến thiên góc cúi đầu Pitch
                neck_pitch_equiv = float((self.base_neck_height - neck_height) * 180.0)
                pitch_deg = 0.5 * pitch_deg + 0.5 * neck_pitch_equiv

                self.pitch_history.append((now, pitch_deg))
                self.pitch_history = [(t, p) for (t, p) in self.pitch_history if now - t <= 1.5]
                if len(self.pitch_history) >= 5:
                    p_vals = [p for (_, p) in self.pitch_history]
                    if max(p_vals) - min(p_vals) > 15.0:
                        drowsy_nod = True
                        alerts.append("NGỦ GẬT: Phát hiện gật đầu đột ngột (>15°/1.5s)")

                # Lỗi 2: Cúi gập cổ quá thấp
                if neck_height < self.base_neck_height * 0.74:
                    alerts.append("SAI TƯ THẾ: Cúi gập đầu quá thấp!")

                # Lỗi 3: Lệch vai / ngồi vẹo cột sống
                if abs(sh_diff - self.base_shoulder_diff) > 0.055:
                    alerts.append("SAI TƯ THẾ: Ngồi vẹo lưng / Lệch hai vai!")

            # Vẽ đường cân bằng vai trực quan
            p_lsh = (int(left_sh.x * w), int(left_sh.y * h))
            p_rsh = (int(right_sh.x * w), int(right_sh.y * h))
            cv2.line(frame, p_lsh, p_rsh, (52, 211, 153) if not alerts else (244, 63, 94), 2)

        # 4. Theo dõi 2 bàn tay trên mặt bàn (Hands-on-Desk Tracking)
        hands_detected = bool(res.left_hand_landmarks or res.right_hand_landmarks)
        if hands_detected:
            self.last_hand_time = now
        elif self.calibrated and (now - self.last_hand_time > 15.0):
            alerts.append("MẤT TẬP TRUNG: Giấu cả 2 tay dưới gầm bàn (>15s)")

        # 5. Tính điểm tập trung thị giác (Visual Focus / CFI: 0 - 100%)
        geom_score = max(0.0, 100.0 - len(alerts) * 22.0)
        if self._tm_model is not None:
            try:
                tm_in = cv2.resize(rgb, (224, 224)).astype(np.float32) / 127.5 - 1.0
                pred = self._tm_model.predict(np.expand_dims(tm_in, 0), verbose=0)[0]
                tm_good_score = float(pred[0]) * 100.0
                visual_focus = 0.7 * geom_score + 0.3 * tm_good_score
            except Exception:
                visual_focus = geom_score
        else:
            visual_focus = geom_score

        # 6. Vẽ lớp phủ thông số kỹ thuật chuẩn KHKT lên khung hình MJPEG
        cv2.rectangle(frame, (0, 0), (w, 64), (15, 23, 42), -1)
        if not self.calibrated:
            progress = min(100, int((len(self.calib_samples) / 30.0) * 100))
            status_txt = f"DANG HIEU CHINH GOC TU THE CA NHAN (5s): {progress}% - Ngoi thang lung!"
            color = (56, 189, 248)
        elif not alerts:
            status_txt = f"TU THE CHUAN | CFI: {visual_focus:.0f}% | Ngap: {self.yawn_count}"
            color = (74, 222, 128)
        else:
            status_txt = f"CANH BAO ({visual_focus:.0f}%): {alerts[0][:48]}"
            color = (248, 113, 113)

        cv2.putText(frame, status_txt, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2)
        metrics_txt = (
            f"EAR:{ear:.2f} PERCLOS:{perclos:.0f}% MAR:{mar:.2f} "
            f"Pitch:{pitch_deg:.1f} Yaw:{yaw_deg:.1f} Roll:{roll_deg:.1f}"
        )
        cv2.putText(frame, metrics_txt, (12, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (203, 213, 225), 1)

        _, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        telemetry = {
            "status": "OK",
            "camera_connected": True,
            "calibrated": self.calibrated,
            "ear": round(ear, 3),
            "perclos": perclos,
            "mar": round(mar, 3),
            "yawn_count": self.yawn_count,
            "pitch_deg": round(pitch_deg, 1),
            "yaw_deg": round(yaw_deg, 1),
            "roll_deg": round(roll_deg, 1),
            "drowsy_nod": drowsy_nod,
            "hands_on_desk": (now - self.last_hand_time <= 15.0),
            "visual_focus_score": round(visual_focus, 1),
            "cfi_score": round(visual_focus, 1),
            "alerts": alerts,
        }
        return jpeg.tobytes(), telemetry

    def process_frame(self) -> Tuple[bytes, Dict[str, Any]]:
        """Đọc 1 khung hình từ Webcam tại biên (tự động trả ảnh thông báo nếu không có Webcam)."""
        if self.cap is None or not self.cap.isOpened():
            if not self._auto_open_camera:
                return self._build_no_camera_placeholder()
            self._open_camera(force=False)
            if self.cap is None or not self.cap.isOpened():
                return self._build_no_camera_placeholder()

        ret, frame = self.cap.read()
        if not ret or frame is None:
            return self._build_no_camera_placeholder()

        return self.process_bgr_frame(frame)

    def release(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        if self._landmarker is not None:
            try:
                self._landmarker.close()
            except Exception:
                pass
            self._landmarker = None
        if self._legacy_holistic is not None:
            try:
                self._legacy_holistic.close()
            except Exception:
                pass
            self._legacy_holistic = None


class PostureAIServiceWorker:
    """
    Dịch vụ chạy nền quản lý vòng lặp PostureAIEngine (~12 FPS):
      - Lưu khung hình MJPEG mới nhất trên RAM để trang_web.py phát qua /video_feed.
      - Đẩy dữ liệu telemetry vào runtime_state.json (hoặc qua HTTP tới trang_web.py nếu chạy sidecar).
      - Tự động bắn cảnh báo POSTURE_OR_HW về Web + Bot Telegram khi CFI < 50% kéo dài > 15 giây!
    """

    def __init__(
        self,
        camera_index: int = 0,
        push_via_http: bool = False,
        frame_callback: Optional[Any] = None,
    ):
        self.camera_index = camera_index
        self.push_via_http = push_via_http
        self.frame_callback = frame_callback
        self.running = False
        self._engine: Optional[PostureAIEngine] = None
        self._thread: Optional[threading.Thread] = None
        self.latest_jpeg: Optional[bytes] = None
        self.latest_telemetry: Dict[str, Any] = {}
        self._low_cfi_since: Optional[float] = None
        self._last_posture_alert_time: float = 0.0

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, name="PostureAIWorkerThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False

    def recalibrate(self) -> None:
        if self._engine is not None:
            self._engine.reset_calibration()

    def _emit_posture_violation(self, telemetry: Dict[str, Any], cfg: Dict[str, Any]) -> None:
        cfi = float(telemetry.get("visual_focus_score", 100.0))
        alerts = telemetry.get("alerts") or []
        detail = alerts[0] if alerts else "Tư thế ngồi thiếu tập trung kéo dài"
        msg = f"⚠️ Cảnh báo Tư thế AI 3D: Điểm tập trung giảm xuống {cfi}% ({detail})"

        port = int(cfg.get("web_port", 5000))
        try:
            requests.post(
                f"http://127.0.0.1:{port}/api/nhan_canh_bao",
                json={"message": msg, "event_type": "POSTURE_OR_HW"},
                timeout=1.5,
            )
        except Exception:
            pass

    def _run_loop(self) -> None:
        try:
            self._engine = PostureAIEngine(camera_index=self.camera_index, open_camera=True)
            last_state_sync = 0.0
            while self.running:
                jpeg_bytes, telemetry = self._engine.process_frame()
                self.latest_jpeg = jpeg_bytes
                self.latest_telemetry = telemetry
                if self.frame_callback is not None:
                    try:
                        self.frame_callback(jpeg_bytes, telemetry)
                    except Exception:
                        pass

                now = time.time()
                cfg = load_config()
                port = int(cfg.get("web_port", 5000))

                # Theo dõi ngưỡng CFI < 50% duy trì liên tục > 15 giây để bắn cảnh báo POSTURE_OR_HW
                cfi = float(telemetry.get("visual_focus_score", 100.0))
                if telemetry.get("camera_connected") and telemetry.get("calibrated") and cfi < 50.0:
                    if self._low_cfi_since is None:
                        self._low_cfi_since = now
                    elif (now - self._low_cfi_since >= 15.0) and (now - self._last_posture_alert_time >= 45.0):
                        self._last_posture_alert_time = now
                        self._emit_posture_violation(telemetry, cfg)
                else:
                    self._low_cfi_since = None

                # Nếu chạy ở chế độ Sidecar độc lập cho file .EXE thì đẩy khung hình & telemetry sang cổng 5000
                if self.push_via_http:
                    try:
                        requests.post(
                            f"http://127.0.0.1:{port}/api/posture_frame",
                            data=jpeg_bytes,
                            headers={"Content-Type": "image/jpeg"},
                            timeout=0.5,
                        )
                    except Exception:
                        pass
                    if now - last_state_sync >= 1.0:
                        last_state_sync = now
                        try:
                            resp = requests.post(
                                f"http://127.0.0.1:{port}/api/posture_telemetry",
                                json=telemetry,
                                timeout=0.8,
                            )
                            if resp.ok and resp.json().get("recalibrate_requested"):
                                self._engine.reset_calibration()
                        except Exception:
                            update_runtime_state(posture_telemetry=telemetry)
                else:
                    if now - last_state_sync >= 1.0:
                        last_state_sync = now
                        update_runtime_state(posture_telemetry=telemetry)
                        from core.config_manager import load_runtime_state

                        st = load_runtime_state()
                        if st.get("recalibrate_posture_requested"):
                            update_runtime_state(recalibrate_posture_requested=False)
                            self._engine.reset_calibration()

                time.sleep(0.08)  # ~12 FPS tối ưu CPU cho máy tính bàn học
        except Exception as exc:
            safe_log(f"[WARN] Luồng Posture AI dừng do lỗi: {exc}")
        finally:
            if self._engine is not None:
                self._engine.release()
                self._engine = None
