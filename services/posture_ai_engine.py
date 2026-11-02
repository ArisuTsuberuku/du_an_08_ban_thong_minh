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
from collections import deque
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
AI_PROC_WIDTH = 640            # độ rộng ảnh đưa vào MediaPipe
LIVE_WIDTH, LIVE_QUALITY = 480, 60      # xem trực tiếp: nhẹ, ~10-15 KB/khung
FULL_QUALITY = 90                       # ảnh chụp: giữ nguyên độ phân giải gốc
IRIS_DIAMETER_CM = 1.17        # đường kính mống mắt người gần như hằng số (~11.7mm) -> thước đo khoảng cách
OUTER_CANTHAL_CM = 8.8         # khoảng cách 2 đuôi mắt trung bình (dự phòng khi không có điểm iris)
# Tai: lấy từ viền lưới mặt (ngang tragus) thay cho Pose 7/8 vốn lệch khi đeo kính / tóc che
FACE_EAR_IDS = (234, 454)
LABEL_KEYS = {ord("1"): "SCREEN", ord("2"): "WRITING", ord("3"): "READING",
              ord("4"): "LOOK_AWAY", ord("5"): "DROWSY", ord("6"): "ABSENT", ord("7"): "PLAYING", ord("0"): ""}

OBJECT_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/object_detector/"
                    "efficientdet_lite0/int8/latest/efficientdet_lite0.tflite")
OBJECT_MODELS = {
    "lite0": ("efficientdet_lite0.tflite", OBJECT_MODEL_URL),
    "lite2": ("efficientdet_lite2.tflite", "https://storage.googleapis.com/mediapipe-models/object_detector/"
                                           "efficientdet_lite2/int8/latest/efficientdet_lite2.tflite"),
}
# Chế độ quét Camera AI: (độ rộng ảnh AI, nghỉ giữa 2 khung, chu kỳ quét đồ vật, mô hình đồ vật, nhận diện hành động)
SCAN_MODES = {
    "eco": {"proc_width": 480, "sleep": 0.12, "obj_interval": 2.0, "obj_model": "lite0", "action": False,
            "label": "Tiết kiệm (máy yếu, ~7 khung/giây)"},
    "standard": {"proc_width": 640, "sleep": 0.07, "obj_interval": 1.0, "obj_model": "lite0", "action": True,
                 "label": "Tiêu chuẩn (~12 khung/giây, đồ vật 1 lần/giây)"},
    "accurate": {"proc_width": 960, "sleep": 0.03, "obj_interval": 0.5, "obj_model": "lite2", "action": True,
                 "label": "Quét kỹ (máy mạnh, ~15-20 khung/giây, đồ vật 2 lần/giây, mô hình lớn)"},
}


def scan_mode(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return SCAN_MODES.get(str(cfg.get("ai_scan_mode", "standard")), SCAN_MODES["standard"])
# Lớp COCO quan tâm -> (nhãn tiếng Việt, loại). phone/toy = giải trí; book = học tập (miễn trừ)
OBJECT_CLASSES = {
    "cell phone": ("điện thoại", "phone"),
    "remote": ("tay cầm game / điều khiển", "toy"),
    "teddy bear": ("gấu bông / thú nhồi bông", "toy"),
    "sports ball": ("quả bóng", "toy"),
    "frisbee": ("đĩa ném đồ chơi", "toy"),
    "kite": ("đồ chơi", "toy"),
    "book": ("sách / vở", "book"),
}
POSE_HAND_IDS = (15, 16, 17, 18, 19, 20, 21, 22)   # cổ tay + ngón (Pose) dùng khi không có điểm bàn tay chi tiết


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


def ensure_model_file(filename: str, url: str, min_bytes: int = 1_000_000) -> str:
    """Đảm bảo tệp mô hình models/<filename> tồn tại cục bộ (tự tải 1 lần duy nhất nếu thiếu)."""
    models_dir = resolve_models_dir()
    model_path = os.path.join(models_dir, filename)
    if os.path.exists(model_path) and os.path.getsize(model_path) > min_bytes:
        return model_path
    safe_log(f"[AI] Đang tải mô hình {filename} về lưu trữ cục bộ (chỉ tải 1 lần đầu)...")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(model_path, "wb") as f:
        f.write(resp.content)
    return model_path


def ensure_holistic_task_model() -> str:
    return ensure_model_file("holistic_landmarker.task", HOLISTIC_MODEL_URL)


def _blendshape_dict(raw: Any) -> Dict[str, float]:
    """Chuẩn hóa blendshape MediaPipe (List[Category] hoặc List[List[Category]]) -> {tên: điểm}."""
    if not raw:
        return {}
    cats = raw[0] if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], (list, tuple)) else raw
    out: Dict[str, float] = {}
    for c in cats or []:
        name = getattr(c, "category_name", None) or getattr(c, "display_name", None)
        if name:
            out[name] = float(getattr(c, "score", 0.0) or 0.0)
    return out


class ObjectWatcher:
    """
    Nhận diện đồ vật (EfficientDet-Lite0 / Lite2, 80 lớp COCO): điện thoại, tay cầm game, gấu bông, bóng...
    (giải trí) và sách/vở (học tập). Quét tốt hơn bản đầu:
      - Ngưỡng riêng từng lớp (điện thoại cầm tay hay bị che -> 0.30; đồ chơi 0.45).
      - Ngoài ảnh toàn khung, cắt thêm VÙNG QUANH BÀN TAY từ ảnh gốc độ phân giải cao -> đồ vật nhỏ trong tay
        to ra trước mô hình (EfficientDet chỉ nhìn ảnh 320-448px nên ảnh toàn khung làm điện thoại chỉ còn vài chục px).
      - Bỏ phiếu theo thời gian: cần >= 2 lần thấy trong ~3.5s (hoặc 1 lần rất chắc) mới tính -> ít báo nhầm.
    """

    CLASS_MIN = {"cell phone": 0.30, "remote": 0.35, "book": 0.40}
    DEFAULT_MIN = 0.45

    def __init__(self, interval_s: float = 1.0, model: str = "lite0"):
        self.interval_s = interval_s
        self._last_t = 0.0
        self.last: List[Dict[str, Any]] = []
        self._hist: "deque[Tuple[float, List[Dict[str, Any]]]]" = deque()
        self.ok = False
        self.model = model
        try:
            import mediapipe as mp
            from mediapipe.tasks.python.core import base_options
            from mediapipe.tasks.python import vision

            fname, url = OBJECT_MODELS.get(model, OBJECT_MODELS["lite0"])
            try:
                path = ensure_model_file(fname, url, min_bytes=1_000_000)
            except Exception:
                fname, url = OBJECT_MODELS["lite0"]
                path = ensure_model_file(fname, url, min_bytes=1_000_000)
                self.model = "lite0"
            with open(path, "rb") as fh:
                buf = fh.read()
            opts = vision.ObjectDetectorOptions(
                base_options=base_options.BaseOptions(model_asset_buffer=buf),
                running_mode=vision.RunningMode.IMAGE,
                max_results=10,
                score_threshold=0.28,
                category_allowlist=list(OBJECT_CLASSES.keys()),
            )
            self._mp = mp
            self._det = vision.ObjectDetector.create_from_options(opts)
            self.ok = True
        except Exception as exc:
            safe_log(f"[AI] Không bật được nhận diện đồ vật: {exc}")
            self._det = None

    def _run(self, bgr: np.ndarray, offset: Tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)) -> List[Dict[str, Any]]:
        """Chạy detector trên 1 ảnh; offset = vị trí (x0,y0,x1,y1) của ảnh này trong khung gốc (chuẩn hóa)."""
        h, w = bgr.shape[:2]
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                             data=np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
        res = self._det.detect(img)
        ox0, oy0, ox1, oy1 = offset
        sx, sy = ox1 - ox0, oy1 - oy0
        out = []
        for d in res.detections or []:
            cat = d.categories[0] if d.categories else None
            if cat is None or cat.category_name not in OBJECT_CLASSES:
                continue
            if float(cat.score) < self.CLASS_MIN.get(cat.category_name, self.DEFAULT_MIN):
                continue
            bb = d.bounding_box
            vi, kind = OBJECT_CLASSES[cat.category_name]
            box = (ox0 + bb.origin_x / w * sx, oy0 + bb.origin_y / h * sy,
                   ox0 + (bb.origin_x + bb.width) / w * sx, oy0 + (bb.origin_y + bb.height) / h * sy)
            out.append({"label": cat.category_name, "vi": vi, "kind": kind, "score": round(float(cat.score), 2),
                        "box": box})
        return out

    @staticmethod
    def _iou(a, b) -> float:
        ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / ua if ua > 0 else 0.0

    def detect(self, bgr: np.ndarray, now: float, force: bool = False,
               rois: Optional[List[Tuple[float, float, float, float]]] = None,
               hires: Optional[np.ndarray] = None) -> List[Dict[str, Any]]:
        """Trả danh sách đồ vật ĐÃ ỔN ĐỊNH (bỏ phiếu theo thời gian). rois cắt từ hires (ảnh gốc) nếu có."""
        if not self.ok or (not force and now - self._last_t < self.interval_s):
            return self.last
        self._last_t = now
        found: List[Dict[str, Any]] = []
        try:
            found = self._run(bgr)
            src = hires if hires is not None else bgr
            H, W = src.shape[:2]
            for roi in (rois or [])[:2]:
                x0, y0, x1, y1 = roi
                px0, py0, px1, py1 = int(max(0, x0) * W), int(max(0, y0) * H), int(min(1, x1) * W), int(min(1, y1) * H)
                if px1 - px0 < 48 or py1 - py0 < 48:
                    continue
                for o in self._run(src[py0:py1, px0:px1], (px0 / W, py0 / H, px1 / W, py1 / H)):
                    dup = next((f for f in found if f["label"] == o["label"] and self._iou(f["box"], o["box"]) > 0.3), None)
                    if dup is None:
                        found.append(o)
                    elif o["score"] > dup["score"]:
                        dup.update(o)
        except Exception:
            return self.last
        self._hist.append((now, found))
        while self._hist and now - self._hist[0][0] > max(3.5, self.interval_s * 3.5):
            self._hist.popleft()
        stable = []
        for o in found:
            hits = sum(1 for _t, lst in self._hist if any(x["label"] == o["label"] for x in lst))
            if hits >= 2 or o["score"] >= 0.6:
                stable.append({**o, "t": now, "hits": hits})
        self.last = stable
        return stable

    @staticmethod
    def held_by(obj: Dict[str, Any], hand_pts: Sequence[Tuple[float, float]], pad: float = 0.06) -> bool:
        """Đồ vật được coi là ĐANG CẦM khi khung bao (nới 6%) chứa >= 1 điểm bàn tay / cổ tay."""
        x0, y0, x1, y1 = obj["box"]
        return any(x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad for x, y in hand_pts)

    def close(self) -> None:
        if self._det is not None:
            try:
                self._det.close()
            except Exception:
                pass
            self._det = None
            self.ok = False


class _UnifiedHolisticResult:
    """Cấu trúc kết quả chuẩn hóa giữa MediaPipe Tasks API (1.x) và Solutions API (0.10.x)."""

    def __init__(self, face_lm=None, pose_lm=None, left_hand_lm=None, right_hand_lm=None, blendshapes=None):
        self.face_landmarks = face_lm
        self.pose_landmarks = pose_lm
        self.left_hand_landmarks = left_hand_lm
        self.right_hand_landmarks = right_hand_lm
        self.blendshapes: Dict[str, float] = blendshapes or {}


class PostureAIEngine:
    """Bộ xử lý Thị giác Máy tính nhận diện tư thế 3D, buồn ngủ và chống gian lận tại biên."""

    def __init__(self, camera_index: int = 0, open_camera: bool = True):
        self.camera_index = camera_index
        self._auto_open_camera = open_camera
        self._last_cam_probe_time = 0.0
        self._read_fail_count = 0
        self._black_frame_count = 0
        self.last_raw_frame: Optional[np.ndarray] = None   # khung GỐC độ phân giải đầy đủ (không lớp AI)
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

            opt_kw = dict(
                base_options=base_options.BaseOptions(model_asset_buffer=model_bytes),
                running_mode=vision.RunningMode.VIDEO,
                min_face_detection_confidence=0.55,
                min_face_landmarks_confidence=0.55,
                min_pose_detection_confidence=0.55,
                min_pose_landmarks_confidence=0.55,
                min_hand_landmarks_confidence=0.50,
            )
            # Blendshape eyeBlink / eyeLookDown: phân biệt NHẮM MẮT với LIẾC XUỐNG BÀN (EAR không làm được)
            try:
                opts = vision.HolisticLandmarkerOptions(output_face_blendshapes=True, **opt_kw)
            except TypeError:
                opts = vision.HolisticLandmarkerOptions(**opt_kw)
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

        # Nhận diện đồ vật (điện thoại / đồ chơi / sách) + camera mặt bàn tùy chọn
        try:
            _ocfg = load_config()
        except Exception:
            _ocfg = {}
        self.objects: Optional[ObjectWatcher] = None
        self.mode = scan_mode(_ocfg)
        self.proc_width = int(self.mode["proc_width"])
        if self._use_tasks_api and _ocfg.get("posture_object_detection", True):
            self.objects = ObjectWatcher(interval_s=float(self.mode["obj_interval"]), model=self.mode["obj_model"])
        self.actions = None
        if self.mode.get("action") and _ocfg.get("posture_action_recognition", True):
            try:
                from services.action_recognizer import ActionRecognizer

                self.actions = ActionRecognizer(resolve_models_dir())
                if not self.actions.ok:
                    safe_log(f"[AI] Nhận diện hành động tắt: {self.actions.reason}")
                    self.actions = None
            except Exception as exc:
                safe_log(f"[AI] Nhận diện hành động tắt: {exc}")
                self.actions = None
        self.on_break = False
        self.desk_cam = None
        if _ocfg.get("desk_camera_enabled") and open_camera:
            try:
                from services.desk_camera import DeskCamera

                self.desk_cam = DeskCamera(_ocfg, main_camera_name=lambda: self.camera_info.get("name", ""))
                self.desk_cam.start()
            except Exception as exc:
                safe_log(f"[DESK-CAM] Không khởi động được camera mặt bàn: {exc}")
        self.camera_setup: Dict[str, Any] = {"score": None, "hints": []}
        self._last_setup_check = 0.0
        self._last_head: Optional[Tuple[float, List[Tuple[float, float]]]] = None
        self._pad_hist: "deque[Tuple[float, float, float, float]]" = deque()
        self._hand_vis_hist: "deque[Tuple[float, bool]]" = deque()

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
        self.base_pose_yaw = 0.0
        self.base_sh_y = 0.0
        self.base_sh_w = 0.0
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
            cap, info = open_best_camera(cfg, fallback_index=self.camera_index,
                                         width=int(cfg.get("camera_width", 1280)),
                                         height=int(cfg.get("camera_height", 720)))
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
                blendshapes=_blendshape_dict(getattr(res, "face_blendshapes", None)),
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
                                   kw.get("roll", 0.0), kw.get("distance_cm") or 0.0,
                                   kw.get("pose_yaw") or 0.0, kw.get("sh_y") or 0.0, kw.get("sh_w") or 0.0))
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
            self.base_pose_yaw = float(arr[9])
            self.base_sh_y = float(arr[10])
            self.base_sh_w = float(arr[11])
            self.attn = AttentionModel(self.attn.th)
            self.attn.set_baseline(float(arr[5]))
            self.calibrated = True
            safe_log(
                f"[CALIBRATED] Gốc: EAR={arr[5]:.3f} FaceRatio={self.base_face_ratio:.3f} "
                f"Neck={self.base_neck_height:.3f} Yaw0={self.base_yaw_deg:.1f}° "
                f"Roll0={self.base_roll_deg:.1f}° Khoảng cách={arr[8]:.0f}cm PoseYaw0={arr[9]:.0f}° "
                f"Camera={self.camera_setup.get('score')}/100 {'; '.join(self.camera_setup.get('hints') or [])}"
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

    # =====================================================================
    # ĐẶC TRƯNG v3: HƯỚNG NHÌN DỌC, QUAY ĐẦU TỪ POSE, VÙNG TAY, ĐỒ VẬT, KIỂM TRA GÓC CAMERA
    # =====================================================================
    @staticmethod
    def compute_iris_down(lm: Sequence[Any]) -> Optional[float]:
        """Con ngươi lệch xuống so với đường nối 2 khóe mắt (chuẩn hóa theo bề rộng mắt). ~0 nhìn thẳng, >0.15 nhìn xuống."""
        if len(lm) < 478:
            return None
        vals = []
        for c1, c2, iris in ((33, 133, 468), (362, 263, 473)):
            ew = abs(lm[c2].x - lm[c1].x)
            if ew > 1e-4:
                vals.append((lm[iris].y - (lm[c1].y + lm[c2].y) / 2.0) / ew)
        return float(np.mean(vals)) if vals else None

    @staticmethod
    def compute_pose_yaw(plm: Sequence[Any], w: int, sw_px: float) -> Optional[float]:
        """
        Góc quay đầu ngang chỉ từ Pose (mũi + 2 tai) -> chạy được cả khi mất lưới mặt (quay nghiêng hẳn).
        Mô hình đầu: tai ở ±R, mũi nhô ra trước ~R => lệch mũi / khoảng cách 2 tai ≈ 0.5·tan(θ) => θ = atan(2·tỉ lệ).
        """
        nose, le, re = plm[0], plm[7], plm[8]
        ear_dist = abs(le.x - re.x) * w
        off = (nose.x - (le.x + re.x) / 2.0) * w
        ratio = off / max(ear_dist, 0.08 * sw_px, 1.0)
        return float(np.degrees(np.arctan(2.0 * ratio)))

    def _body_features(self, res: "_UnifiedHolisticResult", w: int, h: int, feats: FrameFeatures) -> Dict[str, Any]:
        """Vai -> thước đo chuẩn (bề rộng vai). Suy vùng tay kể cả khi bàn tay nằm ngoài khung (qua khuỷu tay)."""
        plm = res.pose_landmarks
        ls, rs, nose = plm[11], plm[12], plm[0]
        sw = max(1.0, abs(ls.x - rs.x) * w)
        sy = (ls.y + rs.y) / 2.0 * h
        info: Dict[str, Any] = {"sw": sw, "sy": sy, "hand_pts": [], "pose_yaw": self.compute_pose_yaw(plm, w, sw)}
        if feats.nose_xy is None and getattr(nose, "visibility", 1.0) > 0.3:
            feats.nose_xy, feats.nose_src = (nose.x * w / sw, nose.y * h / sw), "pose"
        zones: List[str] = []
        wr: List[Tuple[float, float, bool, str]] = []
        for side, wi, ei, hand in (("L", 15, 13, res.left_hand_landmarks), ("R", 16, 14, res.right_hand_landmarks)):
            pw, pe = plm[wi], plm[ei]
            cand = None
            if hand and len(hand) >= 21:
                wx, wy, inframe = hand[0].x * w, hand[0].y * h, True
                info["hand_pts"] += [(p.x, p.y) for p in hand]
                cand = (hand[8].x * w / sw, hand[8].y * h / sw)        # đầu ngón trỏ: nhạy với nét viết
            else:
                inframe = getattr(pw, "visibility", 1.0) > 0.5 and 0.0 <= pw.y <= 1.0 and 0.0 <= pw.x <= 1.0
                wx, wy = pw.x * w, pw.y * h
                if inframe:
                    info["hand_pts"] += [(plm[i].x, plm[i].y) for i in (wi, wi + 2, wi + 4, wi + 6)]
                    if getattr(pw, "visibility", 1.0) > 0.9:
                        cand = (wx / sw, wy / sw)
                elif getattr(pe, "visibility", 1.0) > 0.9 and 0.0 <= pe.y <= 1.0:
                    cand = (pe.x * w / sw, pe.y * h / sw)               # tay ngoài khung -> theo dõi khuỷu tay
            y_rel = (wy - sy) / sw
            if inframe:
                zone = "raised" if y_rel < 0.55 else "desk"
            else:
                e_rel = (pe.y * h - sy) / sw
                zone = "below" if (pw.y > 1.0 or e_rel > 0.25) else "unknown"
            zones.append(zone)
            wr.append((wx, wy, inframe, zone))
            if cand is not None:
                feats.arm_pts[side] = cand
        feats.hands_zone = ("desk" if "desk" in zones else "below" if "below" in zones
                            else "raised" if "raised" in zones else "unknown")
        if len(wr) == 2 and wr[0][2] and wr[1][2]:
            d = float(np.hypot(wr[0][0] - wr[1][0], wr[0][1] - wr[1][1])) / sw
            feats.hands_together = d < self.attn.th["hands_together"] and wr[0][3] == wr[1][3] == "raised"
        info["hands_in_frame"] = any(x[2] for x in wr)
        return info

    @staticmethod
    def _roi_around(pts: List[Tuple[float, float]], w: int, h: int, min_frac: float = 0.32,
                    grow: float = 2.2) -> Optional[Tuple[float, float, float, float]]:
        """Ô vuông (theo pixel) bao quanh các điểm, nới grow lần, tối thiểu min_frac cạnh ngắn của khung."""
        if not pts:
            return None
        xs = [p[0] * w for p in pts]
        ys = [p[1] * h for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        side = max(max(xs) - min(xs), max(ys) - min(ys)) * grow
        side = max(side, min_frac * min(w, h))
        return ((cx - side / 2) / w, (cy - side / 2) / h, (cx + side / 2) / w, (cy + side / 2) / h)

    def _object_features(self, frame: np.ndarray, now: float, hand_pts: List[Tuple[float, float]],
                         feats: FrameFeatures, head_pts: Optional[List[Tuple[float, float]]] = None) -> List[Dict[str, Any]]:
        objs: List[Dict[str, Any]] = []
        if self.objects is not None and self.objects.ok:
            h, w = frame.shape[:2]
            rois = []
            r1 = self._roi_around(hand_pts, w, h)
            if r1:
                rois.append(r1)
            if head_pts and not feats.face_ok:      # mặt bị che -> soi vùng đầu (điện thoại giơ trước mặt)
                r2 = self._roi_around(head_pts, w, h, min_frac=0.45, grow=3.0)
                if r2:
                    rois.append(r2)
            raw = self.last_raw_frame
            hires = raw if raw is not None and abs(raw.shape[1] / raw.shape[0] - w / h) < 0.02 else None
            for o in self.objects.detect(frame, now, rois=rois, hires=hires):
                o = dict(o)
                o["held"] = bool(hand_pts) and ObjectWatcher.held_by(o, hand_pts)
                o["cam"] = "main"
                objs.append(o)
        if self.desk_cam is not None:
            d = self.desk_cam.latest(max_age=2.5)
            if d:
                for o in d.get("objects", []):
                    objs.append({**o, "cam": "desk"})
                if d.get("hands", 0) > 0:
                    feats.hands_zone = "desk" if feats.hands_zone in ("unknown", "below", "desk") else feats.hands_zone
                    feats.ext_motion = 0.02 if d.get("writing") else 0.0
        held = near = book = None
        for o in objs:
            if o["kind"] in ("phone", "toy") and not o.get("held") and head_pts and not feats.face_ok:
                # Điện thoại / đồ chơi che ngay trước mặt (tay khuất sau đồ vật) cũng tính là đang cầm
                hx = [p[0] for p in head_pts]
                hy = [p[1] for p in head_pts]
                x0, y0, x1, y1 = o["box"]
                if x0 < max(hx) + 0.05 and x1 > min(hx) - 0.05 and y0 < max(hy) + 0.08 and y1 > min(hy) - 0.08:
                    o["held"] = True
            if o["kind"] in ("phone", "toy"):
                if o.get("held") and (held is None or o["score"] > held["score"]):
                    held = o
                elif not o.get("held") and near is None:
                    near = o
            elif o["kind"] == "book" and o.get("held"):
                book = o
        if held is not None:
            feats.held_object, feats.held_kind = held["vi"], held["kind"]
        elif book is not None:
            feats.held_kind = "book"
        if near is not None:
            feats.near_object = near["vi"]
        return objs

    def _camera_setup_check(self, res: "_UnifiedHolisticResult", w: int, h: int,
                            raw_pitch: Optional[float], hands_in_frame: Optional[bool]) -> Dict[str, Any]:
        """Chấm điểm vị trí/góc webcam & gợi ý chỉnh (mặt + 2 vai + vùng tay/mặt bàn phải nằm trong khung)."""
        hints: List[str] = []
        score = 100

        def vis(p) -> float:
            return float(getattr(p, "visibility", 1.0))

        if res.face_landmarks:
            fl = res.face_landmarks
            cx, cy = (fl[33].x + fl[263].x) / 2.0, (fl[33].y + fl[263].y) / 2.0
            if cy < 0.15:
                hints.append("Mặt sát mép trên khung: chúc camera xuống một chút")
                score -= 15
            elif cy > 0.50:
                hints.append("Mặt quá thấp trong khung: nâng camera lên ngang tầm mắt")
                score -= 15
            if abs(cx - 0.5) > 0.22:
                hints.append("Ngồi lệch khỏi tâm camera: dời camera hoặc ghế vào giữa")
                score -= 10
        if res.pose_landmarks and len(res.pose_landmarks) >= 25:
            plm = res.pose_landmarks
            ls, rs = plm[11], plm[12]
            if min(vis(ls), vis(rs)) < 0.5:
                hints.append("Chưa thấy đủ 2 vai: lùi ghế ra hoặc dùng webcam góc rộng (≥ 90°)")
                score -= 20
            else:
                sy = (ls.y + rs.y) / 2.0
                if 1.0 - sy < 0.20:
                    hints.append("Không thấy vùng tay/mặt bàn: chúc camera xuống 10-15°, lùi ghế, "
                                 "hoặc bật camera mặt bàn")
                    score -= 25
                ang = abs(float(np.degrees(np.arctan2((ls.y - rs.y) * h, (ls.x - rs.x) * w))))
                dev = min(ang, abs(180.0 - ang))
                if dev > 8.0:
                    hints.append(f"Khung hình nghiêng {dev:.0f}°: chỉnh webcam cho thẳng ngang")
                    score -= 10
        else:
            hints.append("Chưa thấy thân người: ngồi lùi để camera thấy cả đầu và 2 vai")
            score -= 30
        if hands_in_frame is not None:
            self._hand_vis_hist.append((time.time(), bool(hands_in_frame)))
            while self._hand_vis_hist and time.time() - self._hand_vis_hist[0][0] > 120.0:
                self._hand_vis_hist.popleft()
        pct = None
        if len(self._hand_vis_hist) >= 30:
            pct = round(100.0 * sum(1 for _, v in self._hand_vis_hist if v) / len(self._hand_vis_hist))
            if pct < 25 and self.desk_cam is None and not any("vùng tay" in x for x in hints):
                hints.append(f"Camera chỉ thấy tay {pct}% thời gian: chúc camera xuống / dùng góc rộng / camera mặt bàn")
                score -= 10
        return {"score": max(0, score), "hints": hints[:3], "hands_visible_pct": pct,
                "desk_camera": bool(self.desk_cam is not None and self.desk_cam.ok)}

    def process_bgr_frame(self, frame: np.ndarray) -> Tuple[bytes, Dict[str, Any]]:
        """
        Xử lý 1 khung hình BGR -> (annotated_jpeg_bytes, telemetry_dict).
        Pipeline: điểm mốc + blendshape MediaPipe -> đặc trưng chuẩn hóa (khoảng cách cm, tỉ lệ cúi, nhắm/nhìn xuống,
        quay đầu Pose, vùng tay, đồ vật) -> AttentionModel v3 (ngữ cảnh + tích lũy bằng chứng) -> CFI & cảnh báo.
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
        raw_pitch: Optional[float] = None
        raw_yaw = 0.0
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
        bs = res.blendshapes or {}

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
            feats.iris_down = self.compute_iris_down(lm)
            if bs:
                feats.blink = (bs.get("eyeBlinkLeft", 0.0) + bs.get("eyeBlinkRight", 0.0)) / 2.0
                feats.look_down = (bs.get("eyeLookDownLeft", 0.0) + bs.get("eyeLookDownRight", 0.0)) / 2.0
            if eye_span_px > 1:
                # Mũi lưới mặt (ít rung hơn Pose ~5 lần), quy về đơn vị ~bề rộng vai (≈ 2.8 lần khoảng cách 2 mắt)
                feats.nose_xy = (lm[1].x * w / (eye_span_px * 2.8), lm[1].y * h / (eye_span_px * 2.8))
                feats.nose_src = "face"
                if self.calibrated and self.base_eye_span_px > 1:
                    feats.eye_span_rel = eye_span_px / self.base_eye_span_px

            # Ngáp: MAR > 0.60 liên tục >= 1.5s (mô hình chỉ cộng bằng chứng khi >= 2 lần / 5 phút)
            if mar > 0.60:
                if self.yawn_start_time is None:
                    self.yawn_start_time = now
                    self.yawn_counted_current = False
                elif now - self.yawn_start_time >= 1.5 and not self.yawn_counted_current:
                    self.yawn_count += 1
                    self.yawn_counted_current = True
                    feats.yawn_event = True
            else:
                self.yawn_start_time = None
                self.yawn_counted_current = False

        # 3. Đặc trưng thân người (vai, cổ, quay đầu Pose, vùng tay) — mọi đơn vị theo bề rộng vai
        neck_height = sh_diff = None
        body: Dict[str, Any] = {}
        if pose_ok:
            plm = res.pose_landmarks
            left_sh, right_sh, nose = plm[11], plm[12], plm[0]
            neck_height = (left_sh.y + right_sh.y) / 2.0 - nose.y
            sh_diff = left_sh.y - right_sh.y
            body = self._body_features(res, w, h, feats)
            if self.calibrated:
                feats.neck_drop = (self.base_neck_height - neck_height) / max(0.05, self.base_neck_height)
                feats.shoulder_tilt_delta = sh_diff - self.base_shoulder_diff
                if body.get("pose_yaw") is not None:
                    feats.pose_yaw_deg = body["pose_yaw"] - self.base_pose_yaw

        hands_detected = bool(res.left_hand_landmarks or res.right_hand_landmarks)
        if hands_detected or body.get("hands_in_frame"):
            self.last_hand_time = now

        # 3a. Điểm bàn tay (kể cả khi Pose hỏng vì bị che) + vùng đầu (Pose 0-10 hoặc khung mặt gần nhất)
        hand_pts = list(body.get("hand_pts", []))
        if not hand_pts:
            for hand in (res.left_hand_landmarks, res.right_hand_landmarks):
                if hand:
                    hand_pts += [(p.x, p.y) for p in hand]
        head_pts: List[Tuple[float, float]] = []
        if face_ok:
            fl = res.face_landmarks
            head_pts = [(fl[i].x, fl[i].y) for i in (10, 152, 234, 454)]
            self._last_head = (now, head_pts)
        elif res.pose_landmarks and len(res.pose_landmarks) >= 11:
            head_pts = [(res.pose_landmarks[i].x, res.pose_landmarks[i].y) for i in range(11)
                        if 0.0 <= res.pose_landmarks[i].y <= 1.0]
        if not head_pts and self._last_head and now - self._last_head[0] < 4.0:
            head_pts = self._last_head[1]
        if not face_ok and hand_pts and head_pts:
            hx = [p[0] for p in head_pts]
            hy = [p[1] for p in head_pts]
            mx, my = 0.6 * (max(hx) - min(hx) + 0.05), 0.6 * (max(hy) - min(hy) + 0.05)
            feats.face_covered = any(min(hx) - mx <= x <= max(hx) + mx and min(hy) - my <= y <= max(hy) + my
                                     for x, y in hand_pts)

        # 3b. Đồ vật trên tay / trước mặt / trong tầm tay
        objs = self._object_features(frame, now, hand_pts, feats, head_pts=head_pts)
        if feats.held_kind in ("phone", "toy") and not face_ok:
            feats.face_covered = True

        # 3b'. Nhận diện hành động (MoViNet) trên vùng nửa người trên
        if self.actions is not None:
            roi = self._roi_around(head_pts + hand_pts, w, h, min_frac=0.6, grow=1.6) if head_pts else None
            self.actions.push(frame, roi)
            act = self.actions.latest()
            if act and act.get("confident"):
                feats.action_group, feats.action_vi = act.get("group"), act.get("vi")
                feats.action_score = float(act.get("score", 0.0))
        feats.on_break = bool(self.on_break)

        # 3c. Kiểm tra góc đặt webcam: chỉ chấm trên khung RÕ (thấy mặt + 2 vai), tránh kết luận sai khi bị che
        if face_ok and pose_ok and (not self.calibrated or now - self._last_setup_check >= 2.0):
            self._last_setup_check = now
            self.camera_setup = self._camera_setup_check(res, w, h, raw_pitch, body.get("hands_in_frame"))

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
                                     roll=roll_deg, distance_cm=distance_cm, pose_yaw=body.get("pose_yaw"),
                                     sh_y=body.get("sy"), sh_w=body.get("sw"))

        # 5. Bộ máy trạng thái chú ý (chỉ chạy sau khi đã có gốc)
        if self.calibrated:
            ar = self.attn.update(feats)
        else:
            ar = {"state": "NO_DATA", "state_ascii": "DANG HIEU CHINH", "state_vi": "Đang hiệu chỉnh",
                  "focused": True, "cfi": 100.0, "alerts": [], "alert_codes": [], "head_down_level": 0,
                  "ear_norm": None, "eyes_closed": False, "closure_s": 0.0, "perclos_rel": 0.0, "motion": 0.0,
                  "nod_count": 0, "evidence": {}, "distance_cm": distance_cm, "low_quality": False,
                  "hands_zone": feats.hands_zone}

        # Chống ảnh tĩnh PAD: chỉ xét khi nhìn thẳng (cúi đầu khó thấy chớp mắt)
        if ar.get("eyes_closed"):
            self.last_blink_time = now
        # Chống ảnh tĩnh (PAD) v2: người thật luôn có vi chuyển động đầu + biểu cảm dù mở mắt lâu (đọc chăm chú).
        # Chỉ nghi ảnh tĩnh khi ĐỒNG THỜI: không chớp mắt > 60s + mũi gần như bất động + blendshape gần như không đổi
        # trong 20s (ảnh in/ảnh trên điện thoại cho lưới mặt đứng yên tuyệt đối).
        if face_ok:
            lm = res.face_landmarks
            expr = sum(bs.get(k, 0.0) for k in ("browInnerUp", "jawOpen", "mouthSmileLeft", "eyeSquintLeft",
                                                  "eyeBlinkLeft", "mouthPressLeft"))
            self._pad_hist.append((now, lm[1].x, lm[1].y, expr))
        while self._pad_hist and now - self._pad_hist[0][0] > 20.0:
            self._pad_hist.popleft()
        if (face_ok and self.calibrated and now - self.last_blink_time > 60.0 and len(self._pad_hist) >= 40
                and self._pad_hist[-1][0] - self._pad_hist[0][0] > 15.0):
            arr = np.array([p[1:] for p in self._pad_hist], dtype=np.float64)
            head_still = float(arr[:, 0].std() + arr[:, 1].std()) < 0.0015
            expr_still = (not bs) or float(arr[:, 2].std()) < 0.01
            if head_still and expr_still:
                extra_alerts.append("NGHI ẢNH TĨNH: Mặt bất động tuyệt đối >20s và không chớp mắt >60s")

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
            # Ranh giới vùng mặt bàn (0.55 bề rộng vai dưới đường vai)
            desk_y = int(body.get("sy", 0) + 0.55 * body.get("sw", 0))
            if 0 < desk_y < h:
                for x0 in range(0, w, 18):
                    cv2.line(frame, (x0, desk_y), (min(w, x0 + 9), desk_y), (148, 163, 184), 1)
                cv2.putText(frame, f"VUNG BAN ({to_ascii(feats.hands_zone)})", (8, max(98, desk_y - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (203, 213, 225), 1)
        if self.draw_landmarks:
            for o in objs:
                if o.get("cam") != "main":
                    continue
                x0, y0, x1, y1 = o["box"]
                col = (52, 211, 153) if o["kind"] == "book" else ((60, 60, 240) if o.get("held") else (0, 165, 255))
                p0, p1 = (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h))
                cv2.rectangle(frame, p0, p1, col, 2)
                tag = f"{to_ascii(o['vi'])} {o['score'] * 100:.0f}%" + (" [CAM TAY]" if o.get("held") else "")
                cv2.putText(frame, tag, (p0[0], max(96, p0[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 2)
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
        blink_txt = f"Nham:{feats.blink:.2f} NhinXuong:{feats.look_down:.2f}" if feats.blink is not None else \
            f"EAR:{ear:.2f}({ar.get('ear_norm') or 0:.2f})"
        metrics_txt = (f"{blink_txt} Yaw:{yaw_deg:.0f}/{feats.pose_yaw_deg or 0:.0f} Cui:{ar.get('head_down_level', 0)} "
                       f"VD:{ar.get('motion', 0):.3f} Tay:{to_ascii(feats.hands_zone)}")
        cv2.putText(frame, metrics_txt, (12, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (203, 213, 225), 1)
        ev = ar.get("evidence") or {}
        if not self.calibrated and self.camera_setup.get("hints"):
            ev_txt = "CAMERA: " + to_ascii(self.camera_setup["hints"][0])[:70]
        else:
            ev_txt = (f"Bang chung -> MatTT:{ev.get('look_away', 0) * 100:.0f}% BuonNgu:{ev.get('drowsy', 0) * 100:.0f}% "
                      f"Nghich:{ev.get('play', 0) * 100:.0f}% GucDau:{ev.get('idle', 0) * 100:.0f}% "
                      f"| PERCLOS {ar.get('perclos_rel', 0):.0f}%")
        act = ar.get("action")
        if act and act.get("score", 0) >= 0.2:
            ev_txt += f" | HANH DONG: {to_ascii(act.get('vi') or '')} {act['score'] * 100:.0f}%"
        if self.current_label:
            ev_txt += f" | NHAN: {self.current_label}"
        cv2.putText(frame, ev_txt, (12, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (148, 163, 184), 1)

        _, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        held = next((o for o in objs if o.get("held") and o["kind"] in ("phone", "toy")), None)
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
            "hands_on_desk": feats.hands_zone in ("desk", "below") or (now - self.last_hand_time <= 15.0),
            "visual_focus_score": round(visual_focus, 1),
            "cfi_score": round(visual_focus, 1),
            "alerts": alerts,
            "alert_codes": ar.get("alert_codes", []),
            # Bộ máy trạng thái chú ý v3
            "activity_state": ar["state"],
            "activity_state_vi": ar["state_vi"],
            "head_down_level": ar.get("head_down_level", 0),
            "head_turned": ar.get("head_turned", False),
            "distance_cm": None if distance_cm is None else round(distance_cm, 1),
            "ear_norm": ar.get("ear_norm"),
            "eyes_closed": ar.get("eyes_closed", False),
            "blink_score": None if feats.blink is None else round(feats.blink, 2),
            "look_down": None if feats.look_down is None else round(feats.look_down, 2),
            "blinks_60s": ar.get("blinks_60s", 0),
            "slow_blinks_60s": ar.get("slow_blinks_60s", 0),
            "nod_count": ar.get("nod_count", 0),
            "motion": ar.get("motion", 0.0),
            "hands_zone": feats.hands_zone,
            "objects": [{"vi": o["vi"], "kind": o["kind"], "score": o["score"], "held": bool(o.get("held")),
                         "cam": o.get("cam", "main")} for o in objs],
            "held_object": held["vi"] if held else None,
            "face_covered": bool(feats.face_covered),
            "action": ar.get("action"),
            "on_break": bool(self.on_break),
            "present": bool(face_ok or pose_ok or feats.face_covered),
            "scan_mode": self.mode.get("label", ""),
            "camera_setup": self.camera_setup,
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
        self.last_raw_frame = frame
        # AI chỉ cần ảnh ~640px: thu nhỏ để giữ tốc độ khi webcam chạy 720p/1080p (tọa độ điểm mốc là tỉ lệ nên không đổi)
        proc = frame
        if frame.shape[1] > self.proc_width:
            scale = self.proc_width / frame.shape[1]
            proc = cv2.resize(frame, (self.proc_width, int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)

        # Khung hình đen liên tục = Webcam đang bị tiến trình khác giữ (vd: trang_web.py đã bật AI) hoặc nắp che camera
        small = frame[::16, ::16]
        if float(small.mean()) < 4.0 and float(small.std()) < 3.0:
            self._black_frame_count += 1
        else:
            self._black_frame_count = 0
        jpeg_bytes, telemetry = self.process_bgr_frame(proc)
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

    def encode_raw(self, kind: str = "live") -> bytes:
        """Mã hóa khung GỐC (không lớp AI): live = 480px q60, full = độ phân giải gốc q90."""
        frame = self.last_raw_frame
        if frame is None:
            return b""
        if kind == "live":
            if frame.shape[1] > LIVE_WIDTH:
                scale = LIVE_WIDTH / frame.shape[1]
                frame = cv2.resize(frame, (LIVE_WIDTH, int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
            ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), LIVE_QUALITY])
        else:
            ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), FULL_QUALITY])
        return enc.tobytes() if ok else b""

    def release(self) -> None:
        if getattr(self, "desk_cam", None) is not None:
            try:
                self.desk_cam.stop()
            except Exception:
                pass
            self.desk_cam = None
        if getattr(self, "objects", None) is not None:
            self.objects.close()
            self.objects = None
        if getattr(self, "actions", None) is not None:
            self.actions.close()
            self.actions = None
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
        raw_callback: Optional[Any] = None,
    ):
        self.camera_index = camera_index
        self.push_via_http = push_via_http
        self.frame_callback = frame_callback
        self.raw_callback = raw_callback
        self._last_full_t = 0.0
        self.running = False
        self._engine: Optional[PostureAIEngine] = None
        self._thread: Optional[threading.Thread] = None
        self.latest_jpeg: Optional[bytes] = None
        self.latest_telemetry: Dict[str, Any] = {}
        self._low_cfi_since: Optional[float] = None
        self._last_posture_alert_time: float = 0.0
        self._active_codes: set = set()
        self._code_last: Dict[str, float] = {}
        self._brk: Optional[Dict[str, Any]] = None
        self._brk_state: Dict[str, Any] = {}
        self._brk_read_t = 0.0

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

    def _publish_raw(self, now: float, port: int) -> None:
        """Đẩy khung GỐC cho phụ huynh: live mỗi khung, full (độ phân giải gốc) mỗi 1 giây."""
        if self._engine is None or self._engine.last_raw_frame is None:
            return
        kinds = ["live"] + (["full"] if now - self._last_full_t >= 1.0 else [])
        if "full" in kinds:
            self._last_full_t = now
        for kind in kinds:
            jpg = self._engine.encode_raw(kind)
            if not jpg:
                continue
            if self.raw_callback is not None:
                try:
                    self.raw_callback(kind, jpg)
                except Exception:
                    pass
            elif self.push_via_http:
                try:
                    requests.post(f"http://127.0.0.1:{port}/api/posture_frame?kind={kind}", data=jpg,
                                  headers={"Content-Type": "image/jpeg"}, timeout=0.5)
                except Exception:
                    pass

    def _update_break(self, telemetry: Dict[str, Any], cfg: Dict[str, Any], now: float) -> None:
        """
        Nghỉ đi vệ sinh: học sinh bấm "Xin đi vệ sinh" -> runtime_state.toilet_break.active.
        Camera tính giờ TỪ LÚC KHÔNG CÒN THẤY học sinh (left_t) tới lúc thấy lại liên tục 3s -> tự kết thúc + báo
        phụ huynh thời lượng; quá toilet_max_minutes thì nhắc 1 lần.
        """
        if now - self._brk_read_t >= 1.0:
            self._brk_read_t = now
            from core.config_manager import load_runtime_state

            self._brk_state = load_runtime_state().get("toilet_break") or {}
        st = self._brk_state
        active = bool(st.get("active"))
        if self._engine is not None:
            self._engine.on_break = active
        if not active:
            self._brk = None
            return
        if self._brk is None or self._brk.get("id") != st.get("start"):
            self._brk = {"id": st.get("start"), "absent_since": None, "left_t": st.get("left_t"),
                         "present_since": None, "warned": False}
        b = self._brk
        present = bool(telemetry.get("camera_connected")) and bool(telemetry.get("present"))
        if not present:
            b["present_since"] = None
            if b["absent_since"] is None:
                b["absent_since"] = now
            if b["left_t"] is None and now - b["absent_since"] >= 3.0:
                b["left_t"] = b["absent_since"]
                st = {**st, "left_t": b["left_t"]}
                self._brk_state = st
                update_runtime_state(toilet_break=st)
        else:
            if b["left_t"] is not None:
                if b["present_since"] is None:
                    b["present_since"] = now
                if now - b["present_since"] >= 3.0 and b["present_since"] - b["left_t"] >= 10.0:
                    dur = b["present_since"] - b["left_t"]
                    st = {**st, "active": False, "back_t": b["present_since"], "duration_s": int(dur), "ended_by": "camera"}
                    self._brk_state = st
                    update_runtime_state(toilet_break=st)
                    self._emit_posture_violation(telemetry, cfg, message=(
                        f"✅ Học sinh đã quay lại bàn học sau {int(dur // 60)} phút {int(dur % 60)} giây "
                        f"(rời chỗ {time.strftime('%H:%M', time.localtime(b['left_t']))} → "
                        f"về {time.strftime('%H:%M', time.localtime(b['present_since']))})"), event_type="BREAK")
                    self._brk = None
                    if self._engine is not None:
                        self._engine.on_break = False
                    return
            else:
                b["absent_since"] = None
        max_s = float(cfg.get("toilet_max_minutes", 10)) * 60.0
        ref = b["left_t"] or float(st.get("start", now) or now)
        if not b["warned"] and now - ref > max_s:
            b["warned"] = True
            self._emit_posture_violation(telemetry, cfg, message=(
                f"⏰ Học sinh đi vệ sinh đã quá {int(max_s // 60)} phút "
                f"(rời chỗ lúc {time.strftime('%H:%M', time.localtime(ref))}) mà chưa quay lại"), event_type="BREAK")

    # Sự kiện báo NGAY cho phụ huynh khi vừa xuất hiện (mỗi loại cách nhau >= 2 phút)
    INSTANT_CODES = {"PLAYING": "🎮", "DROWSY": "😴", "PHONE_NEAR": "📱", "IDLE": "💤"}

    def _emit_instant_events(self, telemetry: Dict[str, Any], cfg: Dict[str, Any], now: float) -> None:
        codes = list(telemetry.get("alert_codes") or [])
        alerts = telemetry.get("alerts") or []
        for code in codes:
            if code not in self.INSTANT_CODES or code in self._active_codes:
                continue
            if now - self._code_last.get(code, 0.0) < 120.0:
                continue
            self._code_last[code] = now
            prefix = {"PLAYING": "NGHỊCH ĐỒ", "DROWSY": "BUỒN NGỦ", "PHONE_NEAR": "XAO NHÃNG", "IDLE": "GỤC ĐẦU"}[code]
            text = next((a for a in alerts if a.startswith(prefix)), code)
            self._emit_posture_violation(telemetry, cfg, message=f"{self.INSTANT_CODES[code]} Camera AI: {text}")
            if code == "PLAYING" and cfg.get("auto_photo_on_object", True):
                # Cloud agent sẽ gửi 1 ảnh gốc (không lớp AI) cho phụ huynh qua Telegram làm bằng chứng
                update_runtime_state(auto_snapshot_request={"t": now, "reason": text})
        self._active_codes = set(codes)

    def _emit_posture_violation(self, telemetry: Dict[str, Any], cfg: Dict[str, Any], message: str = "",
                                event_type: str = "POSTURE_OR_HW") -> None:
        cfi = float(telemetry.get("visual_focus_score", 100.0))
        alerts = telemetry.get("alerts") or []
        detail = alerts[0] if alerts else "Tư thế ngồi thiếu tập trung kéo dài"
        msg = message or f"⚠️ Cảnh báo Tư thế AI 3D: Điểm tập trung giảm xuống {cfi}% ({detail})"

        port = int(cfg.get("web_port", 5000))
        try:
            requests.post(
                f"http://127.0.0.1:{port}/api/nhan_canh_bao",
                json={"message": msg, "event_type": event_type},
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
                self._publish_raw(now, port)

                self._update_break(telemetry, cfg, now)
                if telemetry.get("calibrated") and not telemetry.get("on_break"):
                    self._emit_instant_events(telemetry, cfg, now)
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

                time.sleep(float(getattr(self._engine, "mode", {}).get("sleep", 0.08)))  # nhịp theo chế độ quét
        except Exception as exc:
            safe_log(f"[WARN] Luồng Posture AI dừng do lỗi: {exc}")
        finally:
            if self._engine is not None:
                self._engine.release()
                self._engine = None
