# -*- coding: utf-8 -*-
"""
Bộ máy Trạng thái Chú ý theo Ngữ cảnh (services/attention_model.py)

Vấn đề của bản cũ: mỗi khung hình vi phạm 1 ngưỡng cứng -> trừ ngay 22% CFI. Học sinh cúi đầu làm bài
bị nhận nhầm hàng loạt vì:
  - Nhìn xuống làm mí mắt hạ thấp -> EAR giảm -> PERCLOS cao -> báo "BUỒN NGỦ".
  - Cúi đầu -> neck_height giảm -> "Cúi gập đầu quá thấp"; liếc vở <-> màn hình -> "NGỦ GẬT (>15°/1.5s)".
  - Tay nằm ngoài khung hình camera -> "Giấu 2 tay dưới gầm bàn".
  - Mặt khuất khi cúi sâu -> "VẮNG MẶT" dù vai vẫn ở trước camera.

Cách tiếp cận mới (4 lớp):
  1. CHUẨN HÓA THEO NGỮ CẢNH: ngưỡng nhắm mắt tính theo phân vị P90 của EAR *trong cùng mức cúi đầu*
     (0 = nhìn thẳng, 1 = cúi vừa, 2 = cúi sâu) trong 120s gần nhất -> cúi viết không bị coi là nhắm mắt.
  2. ĐỘNG HỌC THAY VÌ TƯ THẾ TĨNH: chớp mắt (<0.5s) khác nhắm lâu (>=1s); gật ngủ = đầu rơi xuống
     KHI mắt đang nhắm, khác liếc vở chủ động; vi chuyển động mũi/cổ tay (viết bài) khác bất động.
  3. TÍCH LŨY BẰNG CHỨNG (leaky integrator + hysteresis): mỗi loại hành vi có "bình chứa" giây,
     đầy tới ngưỡng mới cảnh báo, xả dần khi hành vi dừng -> 1 cái quay đầu 2s không bị phạt.
  4. CFI = EMA (tau ~8s) của điểm tức thời theo trạng thái, không trừ cứng theo số cảnh báo.

Mọi ngưỡng nằm trong DEFAULT_THRESHOLDS và có thể ghi đè qua shared_config["attention_thresholds"],
để tinh chỉnh bằng dữ liệu thật (xem danh_gia_ai_tu_the.py).
"""

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

DEFAULT_THRESHOLDS: Dict[str, float] = {
    # --- Mức cúi đầu (so với gốc hiệu chỉnh) ---
    "down_ratio_mild": 0.06,      # tăng tỉ lệ (mũi-mắt)/(cằm-mắt) -> cúi vừa
    "down_ratio_strong": 0.14,    # -> cúi sâu (viết bài sát vở)
    "down_neck_mild": 0.12,       # sụt chiều cao cổ (tỉ lệ so với gốc) -> cúi vừa
    "down_neck_strong": 0.28,     # -> cúi sâu
    # --- Mắt ---
    "closed_ratio": 0.60,         # EAR < 0.60 * P90(EAR cùng mức cúi) => mắt đang nhắm
    "long_closure_s": 1.0,        # nhắm liên tục >= 1s (không phải chớp mắt)
    "perclos_window_s": 30.0,
    "perclos_drowsy": 0.25,       # >= 25% thời gian nhắm trong 30s
    # --- Hướng nhìn ---
    "yaw_away_deg": 28.0,         # quay mặt ngang khi nhìn thẳng
    "yaw_away_down_deg": 40.0,    # khi cúi (vở để lệch) cho phép quay nhiều hơn
    "gaze_away": 0.22,            # |tỉ lệ vị trí con ngươi - 0.5| (cần mô hình có điểm iris)
    # --- Vi chuyển động (đơn vị: độ lệch chuẩn vị trí mũi / khoảng cách 2 mắt, cửa sổ 4s) ---
    "motion_window_s": 4.0,
    "motion_idle": 0.015,         # dưới mức này coi như bất động
    # --- Tích lũy bằng chứng (giây) ---
    "lookaway_fill_s": 6.0,
    "drowsy_fill_s": 6.0,
    "absent_fill_s": 5.0,
    "idle_fill_s": 120.0,         # cúi bất động, không hoạt động 2 phút
    "posture_fill_s": 20.0,
    "cfi_tau_s": 8.0,
    # --- Khoảng cách mắt -> webcam (cm), ước lượng bằng mống mắt 11.7mm / đuôi mắt ---
    "min_distance_cm": 40.0,      # gần hơn => cảnh báo quá sát màn hình (khuyến nghị >= 40-50cm)
    "calib_min_cm": 40.0,         # vùng khoảng cách hợp lệ để hiệu chỉnh gốc
    "calib_max_cm": 85.0,
    "max_reliable_cm": 110.0,     # xa hơn => điểm mốc kém tin cậy, không kết luận nhắm mắt/nhìn
    # --- Công thái học ---
    "too_close_ratio": 1.30,      # khoảng cách 2 mắt > 1.3 lần gốc => mặt quá sát (dự phòng khi không đo được cm)
    "roll_deg": 16.0,
    "shoulder_tilt": 0.06,
}

STATE_VI = {
    "SCREEN": "Nhìn màn hình",
    "WRITING": "Cúi làm bài (có vận động)",
    "READING": "Cúi đọc / suy nghĩ",
    "LOOK_AWAY": "Mất tập trung: nhìn ra ngoài",
    "DROWSY": "Buồn ngủ",
    "IDLE": "Gục đầu / bất động lâu",
    "ABSENT": "Vắng mặt",
    "NO_DATA": "Chưa đủ dữ liệu",
}
STATE_ASCII = {
    "SCREEN": "NHIN MAN HINH",
    "WRITING": "LAM BAI (cui viet)",
    "READING": "CUI DOC / SUY NGHI",
    "LOOK_AWAY": "MAT TAP TRUNG",
    "DROWSY": "BUON NGU",
    "IDLE": "GUC DAU BAT DONG",
    "ABSENT": "VANG MAT",
    "NO_DATA": "DANG THU THAP",
}
FOCUS_STATES = ("SCREEN", "WRITING", "READING")


@dataclass
class FrameFeatures:
    """Đặc trưng 1 khung hình do PostureAIEngine trích xuất (None = không đo được)."""

    t: float
    face_ok: bool
    pose_ok: bool
    ear: Optional[float] = None
    mar: Optional[float] = None
    yaw_deg: Optional[float] = None
    roll_deg: Optional[float] = None
    face_down_delta: Optional[float] = None   # tỉ lệ cúi mặt - gốc (dương = cúi)
    neck_drop: Optional[float] = None         # (cổ gốc - cổ hiện tại)/cổ gốc (dương = cúi)
    gaze_dev: Optional[float] = None          # |vị trí con ngươi - 0.5|
    nose_xy: Optional[Tuple[float, float]] = None  # vị trí mũi chuẩn hóa theo khoảng cách 2 mắt
    wrist_xy: List[Tuple[float, float]] = field(default_factory=list)
    eye_span_rel: Optional[float] = None
    shoulder_tilt_delta: Optional[float] = None
    distance_cm: Optional[float] = None       # khoảng cách mắt -> webcam ước lượng


class _Bucket:
    """Bình tích lũy bằng chứng có rò rỉ + trễ (hysteresis) chống nhấp nháy cảnh báo."""

    def __init__(self, fill_s: float, decay: float = 1.0):
        self.fill_s = fill_s
        self.decay = decay
        self.level = 0.0
        self.active = False

    def step(self, on: bool, dt: float, gain: float = 1.0) -> bool:
        if on:
            self.level = min(self.fill_s * 2.0, self.level + dt * gain)
        else:
            self.level = max(0.0, self.level - dt * self.decay)
        if not self.active and self.level >= self.fill_s:
            self.active = True
        elif self.active and self.level < self.fill_s * 0.5:
            self.active = False
        return self.active

    def bump(self, amount: float) -> None:
        self.level = min(self.fill_s * 2.0, self.level + amount)

    @property
    def ratio(self) -> float:
        return min(1.0, self.level / self.fill_s) if self.fill_s > 0 else 0.0


class AttentionModel:
    def __init__(self, thresholds: Optional[Dict[str, float]] = None):
        self.th = dict(DEFAULT_THRESHOLDS)
        if thresholds:
            self.th.update({k: float(v) for k, v in thresholds.items() if k in self.th})
        self.base_ear = 0.30
        self._last_t: Optional[float] = None
        self._ear_hist: Dict[int, Deque[Tuple[float, float]]] = {0: deque(), 1: deque(), 2: deque()}
        self._closed_hist: Deque[Tuple[float, bool]] = deque()
        self._closed_since: Optional[float] = None
        self._nose_hist: Deque[Tuple[float, float, float]] = deque()
        self._wrist_hist: Deque[Tuple[float, float, float]] = deque()
        self._down_hist: Deque[Tuple[float, float, bool]] = deque()
        self._last_nod_t = 0.0
        self.nod_count = 0
        self.b_look = _Bucket(self.th["lookaway_fill_s"], decay=1.0)
        self.b_drowsy = _Bucket(self.th["drowsy_fill_s"], decay=0.5)
        self.b_absent = _Bucket(self.th["absent_fill_s"], decay=2.0)
        self.b_idle = _Bucket(self.th["idle_fill_s"], decay=3.0)
        self.b_close = _Bucket(self.th["posture_fill_s"], decay=1.0)
        self.b_roll = _Bucket(self.th["posture_fill_s"], decay=1.0)
        self.b_shoulder = _Bucket(self.th["posture_fill_s"], decay=1.0)
        self.cfi = 100.0
        self.state = "NO_DATA"

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _prune(dq: Deque, now: float, horizon: float) -> None:
        while dq and now - dq[0][0] > horizon:
            dq.popleft()

    def set_baseline(self, base_ear: float) -> None:
        self.base_ear = max(0.15, float(base_ear))

    def _down_level(self, f: FrameFeatures) -> int:
        lvl = 0
        if f.face_down_delta is not None:
            if f.face_down_delta >= self.th["down_ratio_strong"]:
                lvl = 2
            elif f.face_down_delta >= self.th["down_ratio_mild"]:
                lvl = 1
        if f.neck_drop is not None:
            if f.neck_drop >= self.th["down_neck_strong"]:
                lvl = max(lvl, 2)
            elif f.neck_drop >= self.th["down_neck_mild"]:
                lvl = max(lvl, 1)
        return lvl

    def _ear_reference(self, level: int) -> float:
        """P90 của EAR trong cùng mức cúi đầu (120s gần nhất); thiếu mẫu thì dùng gốc * hệ số hạ mí."""
        hist = self._ear_hist[level]
        factor = (1.0, 0.82, 0.68)[level]
        if len(hist) >= 20:
            vals = sorted(e for _, e in hist)
            p90 = vals[int(0.9 * (len(vals) - 1))]
            return max(p90, self.base_ear * factor * 0.7)
        return self.base_ear * factor

    def _motion(self, now: float) -> float:
        win = self.th["motion_window_s"]
        self._prune(self._nose_hist, now, win)
        self._prune(self._wrist_hist, now, win)
        m = 0.0
        for hist in (self._nose_hist, self._wrist_hist):
            if len(hist) >= 5:
                xs = [x for _, x, _ in hist]
                ys = [y for _, _, y in hist]
                m = max(m, _std(xs) + _std(ys))
        return m

    # ------------------------------------------------------------------ main
    def update(self, f: FrameFeatures) -> Dict[str, Any]:
        now = f.t
        dt = 0.0 if self._last_t is None else max(0.0, min(0.5, now - self._last_t))
        self._last_t = now
        th = self.th

        level = self._down_level(f)
        motion_now = 0.0
        if f.nose_xy is not None:
            self._nose_hist.append((now, f.nose_xy[0], f.nose_xy[1]))
        for wx, wy in f.wrist_xy[:1]:
            self._wrist_hist.append((now, wx, wy))
        motion_now = self._motion(now)

        # ---------- Mắt: nhắm tương đối theo mức cúi ----------
        closed = False
        ear_norm = None
        closure_s = 0.0
        too_far = f.distance_cm is not None and f.distance_cm > th["max_reliable_cm"]
        if f.face_ok and f.ear is not None and not too_far:
            hist = self._ear_hist[level]
            hist.append((now, f.ear))
            self._prune(hist, now, 120.0)
            ref = self._ear_reference(level)
            ear_norm = f.ear / ref if ref > 0 else 1.0
            closed = ear_norm < th["closed_ratio"]
            if closed:
                if self._closed_since is None:
                    self._closed_since = now
                closure_s = now - self._closed_since
            else:
                self._closed_since = None
        else:
            self._closed_since = None
        self._closed_hist.append((now, closed and f.face_ok))
        self._prune(self._closed_hist, now, th["perclos_window_s"])
        perclos = (sum(1 for _, c in self._closed_hist if c) / len(self._closed_hist)) if self._closed_hist else 0.0

        # ---------- Gật ngủ: đầu rơi xuống TRONG LÚC mắt nhắm ----------
        down_val = max(f.face_down_delta or 0.0, (f.neck_drop or 0.0) * 0.5)
        self._down_hist.append((now, down_val, closed))
        self._prune(self._down_hist, now, 2.0)
        nod_event = False
        if len(self._down_hist) >= 6 and now - self._last_nod_t > 3.0:
            first, last = self._down_hist[0][1], self._down_hist[-1][1]
            closed_frac = sum(1 for _, _, c in self._down_hist if c) / len(self._down_hist)
            if last - first > 0.08 and closed_frac >= 0.6:
                nod_event = True
                self._last_nod_t = now
                self.nod_count += 1

        # ---------- Hướng nhìn ----------
        yaw_limit = th["yaw_away_down_deg"] if level >= 1 else th["yaw_away_deg"]
        looking_away = False
        if f.face_ok:
            if f.yaw_deg is not None and abs(f.yaw_deg) > yaw_limit:
                looking_away = True
            if level == 0 and not too_far and f.gaze_dev is not None and f.gaze_dev > th["gaze_away"]:
                looking_away = True
        elif f.pose_ok and level == 0:
            # Vai còn trước camera, đầu không cúi mà mất mặt -> đang quay hẳn đi chỗ khác
            looking_away = True

        absent_now = not f.face_ok and not f.pose_ok
        idle_now = level >= 1 and motion_now < th["motion_idle"] and (closed or not f.face_ok)

        # ---------- Tích lũy bằng chứng ----------
        drowsy_signal = (closure_s >= th["long_closure_s"]) or (perclos >= th["perclos_drowsy"])
        a_look = self.b_look.step(looking_away and not absent_now, dt)
        a_drowsy = self.b_drowsy.step(drowsy_signal, dt, gain=1.5 if closure_s >= 2.0 else 1.0)
        if nod_event:
            self.b_drowsy.bump(th["drowsy_fill_s"] * 0.6)
            a_drowsy = self.b_drowsy.step(True, 0.0)
        a_absent = self.b_absent.step(absent_now, dt)
        a_idle = self.b_idle.step(idle_now, dt)
        if f.distance_cm is not None:
            too_close = f.distance_cm < th["min_distance_cm"]
        else:
            too_close = f.eye_span_rel is not None and f.eye_span_rel > th["too_close_ratio"]
        a_close = self.b_close.step(too_close and f.face_ok, dt)
        a_roll = self.b_roll.step(f.roll_deg is not None and abs(f.roll_deg) > th["roll_deg"] and level == 0, dt)
        a_sh = self.b_shoulder.step(
            f.shoulder_tilt_delta is not None and abs(f.shoulder_tilt_delta) > th["shoulder_tilt"], dt
        )

        # ---------- Trạng thái hiện tại ----------
        if a_absent:
            state = "ABSENT"
        elif a_drowsy:
            state = "DROWSY"
        elif a_look:
            state = "LOOK_AWAY"
        elif a_idle:
            state = "IDLE"
        elif level >= 1 or (not f.face_ok and f.pose_ok):
            state = "WRITING" if motion_now >= th["motion_idle"] else "READING"
        elif f.face_ok:
            state = "SCREEN"
        else:
            state = "NO_DATA"
        self.state = state

        # ---------- CFI: EMA của điểm tức thời ----------
        inst = {"SCREEN": 100.0, "WRITING": 100.0, "READING": 95.0, "LOOK_AWAY": 35.0,
                "DROWSY": 20.0, "IDLE": 45.0, "ABSENT": 0.0, "NO_DATA": self.cfi}[state]
        if state in FOCUS_STATES:
            # Phạt mềm theo mức đầy của các bình (cảnh báo sớm, chưa kết luận)
            inst -= 25.0 * max(self.b_look.ratio, self.b_drowsy.ratio) ** 2
        inst -= 8.0 * sum((a_close, a_roll, a_sh))
        inst = max(0.0, min(100.0, inst))
        alpha = 1.0 - math.exp(-dt / th["cfi_tau_s"]) if dt > 0 else 0.0
        self.cfi = self.cfi + alpha * (inst - self.cfi)

        alerts: List[str] = []
        if a_absent:
            alerts.append("VẮNG MẶT: Không thấy học sinh trước camera")
        if a_drowsy:
            alerts.append(f"BUỒN NGỦ: Nhắm mắt lâu / PERCLOS {perclos * 100:.0f}% / gật đầu {self.nod_count} lần")
        if a_look:
            alerts.append("MẤT TẬP TRUNG: Quay mặt / nhìn ra ngoài khu vực học")
        if a_idle:
            alerts.append("GỤC ĐẦU: Cúi bất động, không có hoạt động viết/đọc")
        if a_close:
            dist_txt = f" ({f.distance_cm:.0f}cm < {th['min_distance_cm']:.0f}cm)" if f.distance_cm else ""
            alerts.append(f"CẬN THỊ: Mặt quá sát màn hình/camera{dist_txt}")
        if a_roll:
            alerts.append("SAI TƯ THẾ: Nghiêng đầu sang vai")
        if a_sh:
            alerts.append("SAI TƯ THẾ: Lệch vai / vẹo lưng")

        return {
            "state": state,
            "state_vi": STATE_VI[state],
            "state_ascii": STATE_ASCII[state],
            "focused": state in FOCUS_STATES,
            "cfi": round(self.cfi, 1),
            "alerts": alerts,
            "head_down_level": level,
            "ear_norm": None if ear_norm is None else round(ear_norm, 3),
            "eyes_closed": closed,
            "closure_s": round(closure_s, 2),
            "perclos_rel": round(perclos * 100.0, 1),
            "motion": round(motion_now, 4),
            "distance_cm": None if f.distance_cm is None else round(f.distance_cm, 1),
            "low_quality": bool(too_far),
            "nod_count": self.nod_count,
            "evidence": {
                "look_away": round(self.b_look.ratio, 2),
                "drowsy": round(self.b_drowsy.ratio, 2),
                "absent": round(self.b_absent.ratio, 2),
                "idle": round(self.b_idle.ratio, 2),
            },
        }


def _std(vals: List[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
