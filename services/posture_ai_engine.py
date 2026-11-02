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

import csv
import os
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import psutil
import requests

from core.config_manager import get_app_dir, load_config, safe_log, update_runtime_state
from services.camera_selector import open_best_camera
from services.attention_model import AttentionModel, FrameFeatures

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

# Khung xương thân trên (Pose) & bàn tay (21 điểm) để vẽ trực quan lên luồng giám sát
POSE_UPPER_BODY_EDGES = ((11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24))
HAND_EDGES = (
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
)
CALIBRATION_SECONDS = 5.0
IRIS_DIAMETER_CM = 1.17        # đường kính mống mắt người gần như hằng số (~11.7mm) -> thước đo khoảng cách
OUTER_CANTHAL_CM = 8.8         # khoảng cách 2 đuôi mắt trung bình (dự phòng khi không có điểm iris)
# Tai: lấy từ viền lưới mặt (ngang tragus) thay cho Pose 7/8 vốn lệch khi đeo kính / tóc che
FACE_EAR_IDS = (234, 454)
LABEL_KEYS = {ord("1"): "SCREEN", ord("2"): "WRITING", ord("3"): "READING",
              ord("4"): "LOOK_AWAY", ord("5"): "DROWSY", ord("6"): "ABSENT", ord("0"): ""}


def to_ascii(text: str) -> str:
    """Bỏ dấu tiếng Việt để cv2.putText không in ra '????'."""
    text = text.replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")

VIRTUAL_CAMERA_BLACKLIST = {
    "obs64.exe",
    "obs32.exe",
    "manycam.exe",
    "vcam.exe",
    "splitcam.exe",
    "droidcamapp.exe",
    "iruinwebcam.exe",
    "iriunwebcam.exe",
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
        self._read_fail_count = 0
        self._black_frame_count = 0
        self._last_cam_reason = ""
        try:
            self.draw_landmarks = bool(load_config().get("posture_draw_landmarks", True))
        except Exception:
            self.draw_landmarks = True
        self.camera_info: Dict[str, Any] = {"ok": False, "reason": "NOT_PROBED", "message_ascii": "Dang do tim Webcam..."}
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
        self.base_yaw_deg = 0.0
        self.base_roll_deg = 0.0
        self.base_face_ratio = 0.45
        self.base_eye_span_px = 0.0
        self.base_distance_cm: Optional[float] = None
        try:
            _cfg = load_config()
        except Exception:
            _cfg = {}
        self.camera_hfov_deg = float(_cfg.get("camera_hfov_deg", 70.0))
        self.attn = AttentionModel(_cfg.get("attention_thresholds") or None)
        # Ghi nhãn & log đặc trưng để đo precision/recall (bật bằng phím 'l' hoặc posture_log_features=true)
        self.current_label = ""
        self.log_features = bool(_cfg.get("posture_log_features", False))
        self._last_log_t = 0.0

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
        """Chọn Webcam thật theo tên (bỏ Camera ảo), thử DSHOW -> MSMF, chẩn đoán lý do nếu thất bại."""
        now = time.time()
        if not force and (now - self._last_cam_probe_time < 5.0):
            return
        self._last_cam_probe_time = now
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        try:
            cfg = load_config()
        except Exception:
            cfg = {}
        try:
            cap, info = open_best_camera(cfg, fallback_index=self.camera_index)
        except Exception as exc:
            cap, info = None, {"ok": False, "reason": "OPEN_FAILED", "message": str(exc),
                               "message_ascii": "Loi mo Webcam", "hint": "", "devices": []}
        self.cap = cap
        self.camera_info = info
        self._read_fail_count = 0
        reason = info.get("reason", "")
        if reason != self._last_cam_reason:  # Chỉ log khi trạng thái thay đổi, tránh spam mỗi 5s
            self._last_cam_reason = reason
            if cap is not None:
                safe_log(f"[CAMERA] Đã mở '{info.get('name')}' (index {info.get('index')}, {info.get('backend')})")
            else:
                safe_log(f"[CAMERA] {reason}: {info.get('message')} | Thiết bị: {info.get('devices')}")
                if info.get("hint"):
                    safe_log(f"[CAMERA] Cách khắc phục: {info['hint']}")
                if info.get("detail"):
                    safe_log(f"[CAMERA] Chi tiết: {info['detail']}")

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
        self.attn = AttentionModel(self.attn.th)
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
        roll = float(np.degrees(np.arctan2((lm[263].y - lm[33].y) * frame_h, (lm[263].x - lm[33].x) * frame_w)))
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

    def _calibrate_step(self, eye_span_px: float, neck_height: float, shoulder_diff: float,
                        raw_pitch: float, **kw: Any) -> None:
        """Gom mẫu 5 giây (ngồi thẳng, nhìn màn hình) -> gốc cá nhân cho mọi đặc trưng tương đối."""
        if not self.calib_samples:
            # Đồng hồ 5 giây chỉ bắt đầu khi có mẫu hợp lệ đầu tiên (không tính thời gian mở Webcam / nạp mô hình)
            self.calib_start_time = time.time()
        self.calib_samples.append((eye_span_px, neck_height, shoulder_diff, raw_pitch,
                                   kw.get("face_ratio", 0.45), kw.get("ear", 0.30), kw.get("yaw", 0.0),
                                   kw.get("roll", 0.0), kw.get("distance_cm") or 0.0))
        elapsed = time.time() - self.calib_start_time
        # Hoàn tất hiệu chỉnh khi đủ trọn 5 giây VÀ có tối thiểu 15 mẫu hợp lệ (dùng trung vị chống nhiễu)
        if elapsed >= CALIBRATION_SECONDS and len(self.calib_samples) >= 15:
            arr = np.median(np.array(self.calib_samples, dtype=np.float64), axis=0)
            self.base_eye_span_px = float(arr[0])
            self.base_neck_height = max(0.05, float(arr[1]))
            self.base_shoulder_diff = float(arr[2])
            self.base_pitch_deg = float(arr[3])
            self.base_face_ratio = float(arr[4])
            self.base_yaw_deg = float(arr[6])
            self.base_roll_deg = float(arr[7])
            self.base_distance_cm = float(arr[8]) or None
            self.attn = AttentionModel(self.attn.th)
            self.attn.set_baseline(float(arr[5]))
            self.calibrated = True
            safe_log(
                f"[CALIBRATED] Gốc: EAR={arr[5]:.3f} FaceRatio={self.base_face_ratio:.3f} "
                f"Neck={self.base_neck_height:.3f} Yaw0={self.base_yaw_deg:.1f}° "
                f"Roll0={self.base_roll_deg:.1f}° Khoảng cách={arr[8]:.0f}cm"
            )

    def estimate_distance_cm(self, lm: Sequence[Any], w: int, h: int) -> Optional[float]:
        """
        Ước lượng khoảng cách mắt -> webcam (cm) theo mô hình camera lỗ kim:
            f_px = (W/2) / tan(HFOV/2);   D = f_px * kích_thước_thật / kích_thước_ảnh_px
        Ưu tiên đường kính mống mắt (11.7mm, sai số giữa người ~5%), dự phòng khoảng cách 2 đuôi mắt.
        HFOV webcam laptop thường 65-78°, chỉnh bằng shared_config["camera_hfov_deg"].
        """
        f_px = (w / 2.0) / np.tan(np.radians(self.camera_hfov_deg) / 2.0)
        if len(lm) >= 478:
            d = []
            for a, b in ((469, 471), (474, 476)):
                px = float(np.hypot((lm[a].x - lm[b].x) * w, (lm[a].y - lm[b].y) * h))
                if px > 2.0:
                    d.append(px)
            if d:
                return float(f_px * IRIS_DIAMETER_CM / max(d))  # mắt nhìn rõ hơn (ít bị xoay) cho đường kính lớn hơn
        span = float(np.hypot((lm[263].x - lm[33].x) * w, (lm[263].y - lm[33].y) * h))
        return float(f_px * OUTER_CANTHAL_CM / span) if span > 4.0 else None

    @staticmethod
    def compute_gaze_dev(lm: Sequence[Any]) -> Optional[float]:
        """Độ lệch hướng nhìn ngang: vị trí tâm con ngươi giữa 2 khóe mắt (0.5 = nhìn thẳng)."""
        if len(lm) < 478:
            return None
        ratios = []
        for c1, c2, iris in ((33, 133, 468), (362, 263, 473)):
            lo, hi = min(lm[c1].x, lm[c2].x), max(lm[c1].x, lm[c2].x)
            if hi - lo > 1e-4:
                ratios.append((lm[iris].x - lo) / (hi - lo))
        return abs(float(np.mean(ratios)) - 0.5) if ratios else None

    def _log_features(self, f: "FrameFeatures", tel: Dict[str, Any]) -> None:
        """Ghi đặc trưng ~5 dòng/giây ra logs/posture_features_YYYYMMDD.csv để đánh giá & tinh chỉnh ngưỡng."""
        if not self.log_features or f.t - self._last_log_t < 0.2:
            return
        self._last_log_t = f.t
        try:
            log_dir = os.path.join(get_app_dir(), "logs")
            os.makedirs(log_dir, exist_ok=True)
            path = os.path.join(log_dir, time.strftime("posture_features_%Y%m%d.csv"))
            new = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as fh:
                wr = csv.writer(fh)
                cols = ["t", "label", "state", "cfi", "face_ok", "pose_ok", "ear", "ear_norm", "mar", "yaw",
                        "roll", "face_down_delta", "neck_drop", "gaze_dev", "motion", "distance_cm",
                        "head_down_level", "eyes_closed", "ev_look", "ev_drowsy", "ev_idle", "ev_absent"]
                if new:
                    wr.writerow(cols)
                ev = tel.get("evidence") or {}
                wr.writerow([f"{f.t:.2f}", self.current_label, tel.get("activity_state"), tel.get("cfi_score"),
                             int(f.face_ok), int(f.pose_ok), tel.get("ear"), tel.get("ear_norm"), tel.get("mar"),
                             f.yaw_deg, f.roll_deg, f.face_down_delta, f.neck_drop, f.gaze_dev, tel.get("motion"),
                             tel.get("distance_cm"), tel.get("head_down_level"), int(bool(tel.get("eyes_closed"))),
                             ev.get("look_away"), ev.get("drowsy"), ev.get("idle"), ev.get("absent")])
        except Exception as exc:
            safe_log(f"[WARN] Không ghi được log đặc trưng: {exc}")

    def _build_no_camera_placeholder(self) -> Tuple[bytes, Dict[str, Any]]:
        """Tạo khung hình thông báo trực quan khi máy chưa gắn Webcam vật lý."""
        canvas = np.zeros((360, 640, 3), dtype=np.uint8)
        canvas[:] = (22, 18, 12)
        cv2.rectangle(canvas, (0, 0), (640, 44), (30, 41, 59), -1)
        cv2.putText(
            canvas,
            "AI POSTURE ENGINE: KHONG MO DUOC WEBCAM",
            (18, 29),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (56, 189, 248),
            2,
        )
        cv2.putText(
            canvas,
            str(self.camera_info.get("message_ascii", "Hay cam Webcam USB"))[:60],
            (18, 185),
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
            "camera_error": self.camera_info.get("reason", ""),
            "camera_message": self.camera_info.get("message", ""),
            "camera_hint": self.camera_info.get("hint", ""),
            "camera_devices": self.camera_info.get("devices", []),
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

    @staticmethod
    def _draw_landmark_overlay(frame: np.ndarray, res: "_UnifiedHolisticResult", w: int, h: int) -> None:
        """Vẽ các điểm mốc MediaPipe: lưới 468 điểm khuôn mặt, khung xương thân trên, 21 điểm mỗi bàn tay."""

        def pt(lm):
            return int(lm.x * w), int(lm.y * h)

        if res.face_landmarks:
            for lm in res.face_landmarks[:468]:
                cv2.circle(frame, pt(lm), 1, (160, 160, 160), -1)
            for ids in (LEFT_EYE_IDS, RIGHT_EYE_IDS):
                cv2.polylines(frame, [np.array([pt(res.face_landmarks[i]) for i in ids], dtype=np.int32)],
                              True, (56, 189, 248), 2)
            # Viền môi trong (8 điểm dùng tính MAR ngáp)
            mouth_ids = (78, 81, 13, 311, 308, 402, 14, 178)
            cv2.polylines(frame, [np.array([pt(res.face_landmarks[i]) for i in mouth_ids], dtype=np.int32)],
                          True, (251, 146, 60), 2)
            # 6 điểm mốc dùng giải solvePnP góc đầu 3D (mũi, cằm, đuôi mắt, khóe miệng)
            for i in SOLVEPNP_LANDMARK_IDS:
                cv2.circle(frame, pt(res.face_landmarks[i]), 4, (56, 189, 248), -1)
            x, y = pt(res.face_landmarks[33])
            cv2.putText(frame, "Mat", (x - 10, y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

        if res.pose_landmarks and len(res.pose_landmarks) >= 25:
            plm = res.pose_landmarks
            for a, b in POSE_UPPER_BODY_EDGES:
                if getattr(plm[a], "visibility", 1.0) > 0.4 and getattr(plm[b], "visibility", 1.0) > 0.4:
                    cv2.line(frame, pt(plm[a]), pt(plm[b]), (250, 204, 21), 2)
            for i in (0, 11, 12, 13, 14, 15, 16):
                if getattr(plm[i], "visibility", 1.0) > 0.4:
                    cv2.circle(frame, pt(plm[i]), 4, (250, 204, 21), -1)
            # Tai: lấy từ viền lưới mặt (234/454, ngang tragus) - chính xác hơn Pose 7/8 khi đeo kính / tóc che.
            # Bên tai bị khuất khi quay đầu (mũi lệch hẳn về phía đó) thì không vẽ.
            if res.face_landmarks:
                fl = res.face_landmarks
                nose_x = fl[1].x
                for ear_id, sh in zip(FACE_EAR_IDS, (12, 11)):
                    span = abs(fl[ear_id].x - nose_x)
                    other = abs(fl[FACE_EAR_IDS[1] if ear_id == FACE_EAR_IDS[0] else FACE_EAR_IDS[0]].x - nose_x)
                    if span < 0.35 * max(other, 1e-4):
                        continue
                    cv2.circle(frame, pt(fl[ear_id]), 6, (232, 121, 249), 2)
                    cv2.putText(frame, "Tai", (pt(fl[ear_id])[0] - 12, pt(fl[ear_id])[1] - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
                    if getattr(plm[sh], "visibility", 1.0) > 0.4:
                        cv2.line(frame, pt(fl[ear_id]), pt(plm[sh]), (232, 121, 249), 1)
            labels = ((0, "Mui"), (11, "Vai"), (12, "Vai"), (15, "Tay"), (16, "Tay"))
            for i, txt in labels:
                if getattr(plm[i], "visibility", 1.0) > 0.5:
                    x, y = pt(plm[i])
                    cv2.putText(frame, txt, (x + 6, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

        for hand, color in ((res.left_hand_landmarks, (74, 222, 128)), (res.right_hand_landmarks, (244, 114, 182))):
            if hand and len(hand) >= 21:
                for a, b in HAND_EDGES:
                    cv2.line(frame, pt(hand[a]), pt(hand[b]), color, 1)
                for lm in hand:
                    cv2.circle(frame, pt(lm), 2, color, -1)

    def process_bgr_frame(self, frame: np.ndarray) -> Tuple[bytes, Dict[str, Any]]:
        """
        Xử lý 1 khung hình BGR -> (annotated_jpeg_bytes, telemetry_dict).
        Pipeline: điểm mốc MediaPipe -> trích đặc trưng chuẩn hóa (khoảng cách cm, tỉ lệ cúi, EAR, gaze,
        vi chuyển động) -> AttentionModel (ngữ cảnh + tích lũy bằng chứng) -> CFI & cảnh báo.
        """
        now = time.time()
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = self._detect_landmarks(rgb)
        if self.draw_landmarks:
            self._draw_landmark_overlay(frame, res, w, h)

        extra_alerts: List[str] = []
        ear, mar = 0.30, 0.10
        pitch_deg = yaw_deg = roll_deg = 0.0
        distance_cm: Optional[float] = None
        face_ok = bool(res.face_landmarks and len(res.face_landmarks) >= 468)
        pose_ok = bool(res.pose_landmarks and len(res.pose_landmarks) >= 25
                       and getattr(res.pose_landmarks[11], "visibility", 1.0) > 0.5
                       and getattr(res.pose_landmarks[12], "visibility", 1.0) > 0.5)

        # 1. Chặn Camera ảo (OBS, ManyCam, VCam...)
        is_vcam, vcam_name = self.check_virtual_camera_spoofing()
        if is_vcam:
            extra_alerts.append(f"GIAN LẬN: Phát hiện Camera ảo ({vcam_name})")

        feats = FrameFeatures(t=now, face_ok=face_ok, pose_ok=pose_ok)
        eye_span_px = None
        face_ratio = None

        # 2. Đặc trưng khuôn mặt
        if face_ok:
            lm = res.face_landmarks
            self.last_face_seen_time = now
            ear = (self.compute_ear_6pt(lm, LEFT_EYE_IDS) + self.compute_ear_6pt(lm, RIGHT_EYE_IDS)) / 2.0
            mar = self.compute_mar_8pt(lm)
            raw_pitch, raw_yaw, roll_deg = self.compute_head_pose_solvepnp(lm, w, h)
            pitch_deg = raw_pitch - self.base_pitch_deg if self.calibrated else 0.0
            yaw_deg = raw_yaw - self.base_yaw_deg if self.calibrated else raw_yaw
            roll_deg = roll_deg - self.base_roll_deg if self.calibrated else roll_deg

            eye_span_px = float(np.hypot((lm[263].x - lm[33].x) * w, (lm[263].y - lm[33].y) * h))
            distance_cm = self.estimate_distance_cm(lm, w, h)
            eye_mid_y = (lm[33].y + lm[263].y) / 2.0
            denom = lm[152].y - eye_mid_y
            if abs(denom) > 1e-4:
                face_ratio = (lm[1].y - eye_mid_y) / denom

            feats.ear, feats.mar = ear, mar
            feats.yaw_deg = yaw_deg if self.calibrated else None
            feats.roll_deg = roll_deg if self.calibrated else None
            feats.distance_cm = distance_cm
            if self.calibrated and face_ratio is not None:
                feats.face_down_delta = face_ratio - self.base_face_ratio
            feats.gaze_dev = self.compute_gaze_dev(lm)
            if eye_span_px > 1:
                feats.nose_xy = (lm[1].x * w / eye_span_px, lm[1].y * h / eye_span_px)
                if self.calibrated and self.base_eye_span_px > 1:
                    feats.eye_span_rel = eye_span_px / self.base_eye_span_px

            # Ngáp: MAR > 0.60 liên tục >= 1.5s
            if mar > 0.60:
                if self.yawn_start_time is None:
                    self.yawn_start_time = now
                    self.yawn_counted_current = False
                elif now - self.yawn_start_time >= 1.5 and not self.yawn_counted_current:
                    self.yawn_count += 1
                    self.yawn_counted_current = True
                    self.attn.b_drowsy.bump(1.5)
            else:
                self.yawn_start_time = None
                self.yawn_counted_current = False

        # 3. Đặc trưng thân người (vai, cổ, cổ tay)
        neck_height = sh_diff = None
        if pose_ok:
            plm = res.pose_landmarks
            left_sh, right_sh, nose = plm[11], plm[12], plm[0]
            neck_height = (left_sh.y + right_sh.y) / 2.0 - nose.y
            sh_diff = left_sh.y - right_sh.y
            if self.calibrated:
                feats.neck_drop = (self.base_neck_height - neck_height) / max(0.05, self.base_neck_height)
                feats.shoulder_tilt_delta = sh_diff - self.base_shoulder_diff
            scale = eye_span_px or (abs(left_sh.x - right_sh.x) * w * 0.35)
            if scale and scale > 1:
                for hand in (res.left_hand_landmarks, res.right_hand_landmarks):
                    if hand:
                        feats.wrist_xy.append((hand[0].x * w / scale, hand[0].y * h / scale))
                if feats.nose_xy is None and getattr(nose, "visibility", 1.0) > 0.3:
                    feats.nose_xy = (nose.x * w / scale, nose.y * h / scale)

        hands_detected = bool(res.left_hand_landmarks or res.right_hand_landmarks)
        if hands_detected:
            self.last_hand_time = now

        # 4. Hiệu chỉnh gốc 5s: chỉ nhận mẫu khi ngồi đúng vùng khoảng cách chuẩn
        calib_hint = ""
        if not self.calibrated and face_ok and pose_ok and face_ratio is not None:
            th = self.attn.th
            if distance_cm is not None and not (th["calib_min_cm"] <= distance_cm <= th["calib_max_cm"]):
                self.calib_samples.clear()
                calib_hint = (f"NGOI CACH CAMERA {th['calib_min_cm']:.0f}-{th['calib_max_cm']:.0f}cm "
                              f"(hien tai {distance_cm:.0f}cm)")
            else:
                self._calibrate_step(eye_span_px or 0.0, neck_height, sh_diff, raw_pitch,
                                     face_ratio=face_ratio, ear=ear, yaw=raw_yaw,
                                     roll=roll_deg, distance_cm=distance_cm)

        # 5. Bộ máy trạng thái chú ý (chỉ chạy sau khi đã có gốc)
        if self.calibrated:
            ar = self.attn.update(feats)
        else:
            ar = {"state": "NO_DATA", "state_ascii": "DANG HIEU CHINH", "state_vi": "Đang hiệu chỉnh",
                  "focused": True, "cfi": 100.0, "alerts": [], "head_down_level": 0, "ear_norm": None,
                  "eyes_closed": False, "closure_s": 0.0, "perclos_rel": 0.0, "motion": 0.0, "nod_count": 0,
                  "evidence": {}, "distance_cm": distance_cm, "low_quality": False}

        # Chống ảnh tĩnh PAD: chỉ xét khi nhìn thẳng (cúi đầu khó thấy chớp mắt)
        if ar.get("eyes_closed"):
            self.last_blink_time = now
        if face_ok and self.calibrated and ar.get("head_down_level", 0) == 0 and now - self.last_blink_time > 40.0:
            extra_alerts.append("CẢNH BÁO PAD: Không chớp mắt >40s (nghi ảnh tĩnh)")

        alerts = extra_alerts + list(ar["alerts"])
        visual_focus = float(ar["cfi"])
        if is_vcam:
            visual_focus = min(visual_focus, 30.0)
        if self._tm_model is not None and self.calibrated:
            try:
                tm_in = cv2.resize(rgb, (224, 224)).astype(np.float32) / 127.5 - 1.0
                pred = self._tm_model.predict(np.expand_dims(tm_in, 0), verbose=0)[0]
                visual_focus = 0.7 * visual_focus + 0.3 * float(pred[0]) * 100.0
            except Exception:
                pass

        # 6. Lớp phủ thông số (chữ không dấu vì cv2.putText không vẽ được tiếng Việt)
        if self.draw_landmarks and pose_ok:
            plm = res.pose_landmarks
            col = (52, 211, 153) if not alerts else (94, 63, 244)
            cv2.line(frame, (int(plm[11].x * w), int(plm[11].y * h)), (int(plm[12].x * w), int(plm[12].y * h)), col, 2)
        cv2.rectangle(frame, (0, 0), (w, 84), (15, 23, 42), -1)
        dist_txt = f" | {distance_cm:.0f}cm" if distance_cm else ""
        if not self.calibrated:
            calib_elapsed = (time.time() - self.calib_start_time) if self.calib_samples else 0.0
            progress = min(100, int(calib_elapsed / CALIBRATION_SECONDS * 100))
            status_txt = calib_hint or f"HIEU CHINH GOC (ngoi thang, nhin man hinh 5s): {progress}%{dist_txt}"
            color = (56, 189, 248) if not calib_hint else (0, 165, 255)
        elif not alerts:
            status_txt = f"{ar['state_ascii']} | CFI {visual_focus:.0f}%{dist_txt}"
            color = (74, 222, 128)
        else:
            status_txt = f"CANH BAO {visual_focus:.0f}%: {to_ascii(alerts[0])[:50]}"
            color = (113, 113, 248)
        cv2.putText(frame, status_txt, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2)
        metrics_txt = (f"EAR:{ear:.2f}({ar.get('ear_norm') or 0:.2f}) MAR:{mar:.2f} Pitch:{pitch_deg:.0f} "
                       f"Yaw:{yaw_deg:.0f} Roll:{roll_deg:.0f} Cui:{ar.get('head_down_level', 0)} "
                       f"VD:{ar.get('motion', 0):.3f}")
        cv2.putText(frame, metrics_txt, (12, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (203, 213, 225), 1)
        ev = ar.get("evidence") or {}
        ev_txt = (f"Bang chung -> MatTT:{ev.get('look_away', 0) * 100:.0f}% BuonNgu:{ev.get('drowsy', 0) * 100:.0f}% "
                  f"GucDau:{ev.get('idle', 0) * 100:.0f}% Vang:{ev.get('absent', 0) * 100:.0f}%")
        if self.current_label:
            ev_txt += f" | NHAN: {self.current_label}"
        cv2.putText(frame, ev_txt, (12, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (148, 163, 184), 1)

        _, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        telemetry = {
            "status": "OK",
            "camera_connected": True,
            "camera_name": self.camera_info.get("name", ""),
            "calibrated": self.calibrated,
            "ear": round(ear, 3),
            "perclos": ar.get("perclos_rel", 0.0),
            "mar": round(mar, 3),
            "yawn_count": self.yawn_count,
            "pitch_deg": round(pitch_deg, 1),
            "yaw_deg": round(yaw_deg, 1),
            "roll_deg": round(roll_deg, 1),
            "drowsy_nod": ar.get("nod_count", 0) > 0,
            "hands_on_desk": (now - self.last_hand_time <= 15.0),
            "visual_focus_score": round(visual_focus, 1),
            "cfi_score": round(visual_focus, 1),
            "alerts": alerts,
            # Trường mới của bộ máy trạng thái chú ý
            "activity_state": ar["state"],
            "activity_state_vi": ar["state_vi"],
            "head_down_level": ar.get("head_down_level", 0),
            "distance_cm": None if distance_cm is None else round(distance_cm, 1),
            "ear_norm": ar.get("ear_norm"),
            "eyes_closed": ar.get("eyes_closed", False),
            "motion": ar.get("motion", 0.0),
            "evidence": ar.get("evidence", {}),
        }
        self._log_features(feats, telemetry)
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
            # Webcam bị rút / bị app khác giành / driver treo -> sau ~15 lần lỗi liên tiếp thì đóng và dò lại
            self._read_fail_count += 1
            if self._read_fail_count >= 15:
                safe_log("[CAMERA] Mất khung hình liên tục, đóng Webcam và dò lại...")
                self.camera_info = {**self.camera_info, "ok": False, "reason": "NO_FRAME",
                                    "message_ascii": "Mat khung hinh Webcam, dang do lai..."}
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
                self._read_fail_count = 0
            return self._build_no_camera_placeholder()
        self._read_fail_count = 0

        # Khung hình đen liên tục = Webcam đang bị tiến trình khác giữ (vd: trang_web.py đã bật AI) hoặc nắp che camera
        small = frame[::8, ::8]
        if float(small.mean()) < 4.0 and float(small.std()) < 3.0:
            self._black_frame_count += 1
        else:
            self._black_frame_count = 0
        jpeg_bytes, telemetry = self.process_bgr_frame(frame)
        if self._black_frame_count >= 30:
            if self._black_frame_count == 30:
                safe_log("[CAMERA] Webcam trả về khung hình ĐEN liên tục: đang bị ứng dụng khác dùng "
                         "(trang_web.py / SmartDeskLauncher đã bật AI, Zoom, Zalo...) hoặc nắp che camera đang đóng.")
            vis = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            if vis is not None:
                cv2.putText(vis, "KHUNG HINH DEN: Webcam dang bi app khac dung", (12, 200),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)
                cv2.putText(vis, "(tat AI tren web :5000 / Zoom / Zalo) hoac mo nap che camera", (12, 230),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (203, 213, 225), 1)
                ok, enc = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
                if ok:
                    jpeg_bytes = enc.tobytes()
            telemetry["camera_warning"] = "BLACK_FRAME"
        return jpeg_bytes, telemetry

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
