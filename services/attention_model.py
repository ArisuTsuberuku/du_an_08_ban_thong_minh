# -*- coding: utf-8 -*-
"""
Bộ máy Trạng thái Chú ý theo Ngữ cảnh — v3 (services/attention_model.py)

Lỗi thực tế đã gặp và cách v3 xử lý:
  1. BÁO "BUỒN NGỦ" KHI MẮT VẪN MỞ: liếc mắt xuống bàn phím/vở làm mí trên hạ theo -> EAR giảm -> bị coi là nhắm.
     -> Dùng blendshape MediaPipe (eyeBlink = nhắm, eyeLookDown = nhìn xuống; mô hình học sẵn phân biệt được 2 việc).
        Nhìn xuống càng nhiều thì ngưỡng eyeBlink càng cao; EAR/gốc còn > 0.85 thì chắc chắn đang mở.
        Không có blendshape -> dự phòng EAR với ngưỡng hạ theo vị trí con ngươi (nhìn xuống).
  2. THUẬT TOÁN NGỦ GẬT CŨ quá nhạy (PERCLOS tính cả chớp mắt, bình xả 0.5/s nên cảnh báo "dính" ~18s):
     -> Phân loại từng lần nhắm: chớp (<0.4s), chớp chậm (0.4-1.2s, dấu hiệu mỏi), vi ngủ (>=1.2s).
        PERCLOS chỉ tính thời gian nhắm >= 0.4s trong 60s. Gật ngủ = đầu rơi khi mắt nhắm RỒI bật lại (giật mình).
        Ngáp chỉ cộng bằng chứng khi >= 2 lần / 5 phút. Bình xả 1.2/s.
  3. QUAY ĐẦU SANG NGANG BỊ TÍNH LÀ LÀM BÀI: mất lưới mặt + mũi Pose thấp -> "cúi"; trộn 2 nguồn mũi -> vi chuyển động ảo.
     -> Góc quay đầu từ Pose (mũi so với 2 tai: yaw ≈ atan(2·lệch/khoảng_cách_tai)) chạy được cả khi mất mặt.
        Vi chuyển động tính thống nhất theo đơn vị bề rộng vai, bỏ cú nhảy do lỗi bám điểm.
  4. CÚI LÀM BÀI vs NGỒI CHƠI: vùng tay (mặt bàn / dưới khung / giơ trước ngực), 2 tay chụm, đồ vật trên tay
     (điện thoại, tay cầm game, đồ chơi — từ bộ nhận diện vật thể) -> trạng thái PLAYING riêng.

Mọi ngưỡng nằm trong DEFAULT_THRESHOLDS, ghi đè được qua shared_config["attention_thresholds"].
"""

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

DEFAULT_THRESHOLDS: Dict[str, float] = {
    # --- Mức cúi đầu (so với gốc hiệu chỉnh) ---
    "down_ratio_mild": 0.06,
    "down_ratio_strong": 0.14,
    "down_neck_mild": 0.12,
    "down_neck_strong": 0.28,
    "look_down_bs": 0.45,         # eyeLookDown >= 0.45 -> mắt đang nhìn xuống bàn (dù đầu chưa cúi)
    # --- Mắt: blendshape (ưu tiên) ---
    "blink_closed": 0.50,         # eyeBlink >= 0.50 khi nhìn thẳng => nhắm
    "blink_down_bonus": 0.30,     # nhìn xuống hoàn toàn => cần eyeBlink >= 0.80 mới tính nhắm
    "ear_open_guard": 0.85,       # EAR/gốc > 0.85 => chắc chắn mở (chặn báo nhầm)
    # --- Mắt: EAR dự phòng ---
    "closed_ratio": 0.60,
    "closed_ratio_down": 0.42,    # khi con ngươi nhìn xuống
    # --- Ngủ gật ---
    "blink_max_s": 0.40,          # nhắm < 0.4s = chớp mắt bình thường
    "microsleep_s": 1.20,         # nhắm liên tục >= 1.2s = vi ngủ
    "perclos_window_s": 60.0,
    "perclos_drowsy": 0.12,       # >= 12% thời gian 60s nhắm (chỉ tính lần nhắm >= 0.4s)
    "slow_blinks_drowsy": 4.0,    # >= 4 lần chớp chậm (0.4-1.2s) / 60s
    "nod_drop": 0.10,             # đầu rơi >= 0.10 (tỉ lệ cúi) trong 1.2s
    "nod_rebound": 0.05,          # rồi bật lại >= 0.05 trong 2s
    # --- Hướng nhìn ---
    "yaw_away_deg": 28.0,
    "yaw_away_down_deg": 40.0,
    "pose_yaw_away_deg": 35.0,    # góc quay đầu ước lượng từ Pose (khi mất lưới mặt)
    "gaze_away": 0.22,
    # --- Vi chuyển động (đơn vị: độ lệch chuẩn / bề rộng vai, cửa sổ 4s) ---
    "motion_window_s": 4.0,
    "motion_idle": 0.006,
    "motion_jump": 0.35,          # nhảy > 0.35 bề rộng vai trong 1 khung = lỗi bám điểm, bỏ qua
    # --- Tay & đồ vật ---
    "hands_together": 0.45,       # 2 cổ tay cách nhau < 0.45 bề rộng vai
    # --- Tích lũy bằng chứng (giây) ---
    "lookaway_fill_s": 6.0,
    "drowsy_fill_s": 5.0,
    "drowsy_decay": 1.2,
    "absent_fill_s": 5.0,
    "idle_fill_s": 120.0,
    "play_fill_s": 8.0,
    "action_min": 0.30,           # tổng xác suất nhóm hành động MoViNet >= 0.30 mới tính là bằng chứng
    "phone_near_fill_s": 25.0,
    "posture_fill_s": 20.0,
    "cfi_tau_s": 8.0,
    # --- Khoảng cách mắt -> webcam (cm) ---
    "min_distance_cm": 30.0,
    "calib_min_cm": 30.0,
    "calib_max_cm": 85.0,
    "max_reliable_cm": 110.0,
    # --- Công thái học ---
    "too_close_ratio": 1.30,
    "roll_deg": 16.0,
    "shoulder_tilt": 0.06,
}

STATE_VI = {
    "SCREEN": "Nhìn màn hình",
    "WRITING": "Cúi làm bài (tay trên bàn, có vận động)",
    "READING": "Cúi đọc / suy nghĩ",
    "LOOK_AWAY": "Mất tập trung: quay đầu / nhìn ra ngoài",
    "DROWSY": "Buồn ngủ",
    "IDLE": "Gục đầu / bất động lâu",
    "PLAYING": "Nghịch đồ / dùng điện thoại",
    "BREAK": "Đang nghỉ (đi vệ sinh)",
    "ABSENT": "Vắng mặt",
    "NO_DATA": "Chưa đủ dữ liệu",
}
STATE_ASCII = {
    "SCREEN": "NHIN MAN HINH",
    "WRITING": "LAM BAI (cui viet)",
    "READING": "CUI DOC / SUY NGHI",
    "LOOK_AWAY": "MAT TAP TRUNG (quay dau)",
    "DROWSY": "BUON NGU",
    "IDLE": "GUC DAU BAT DONG",
    "PLAYING": "NGHICH DO / DIEN THOAI",
    "BREAK": "DANG NGHI (VE SINH)",
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
    face_down_delta: Optional[float] = None
    neck_drop: Optional[float] = None
    gaze_dev: Optional[float] = None
    nose_xy: Optional[Tuple[float, float]] = None      # mũi / bề rộng vai — dùng tính vi chuyển động
    nose_src: str = "face"                             # face (lưới mặt, ít rung) | pose
    wrist_xy: List[Tuple[float, float]] = field(default_factory=list)   # (tương thích cũ)
    arm_pts: Dict[str, Tuple[float, float]] = field(default_factory=dict)  # {"L"/"R": đầu ngón/cổ tay/khuỷu}
    eye_span_rel: Optional[float] = None
    shoulder_tilt_delta: Optional[float] = None
    distance_cm: Optional[float] = None
    # --- v3 ---
    blink: Optional[float] = None          # blendshape eyeBlink (TB 2 mắt) 0..1
    look_down: Optional[float] = None      # blendshape eyeLookDown 0..1
    iris_down: Optional[float] = None      # dự phòng: con ngươi lệch xuống (0 = giữa)
    pose_yaw_deg: Optional[float] = None   # góc quay đầu từ Pose (đã trừ gốc)
    hands_zone: str = "unknown"            # desk | below | raised | unknown
    hands_together: bool = False
    held_object: Optional[str] = None      # nhãn VN đồ đang cầm (điện thoại/đồ chơi...)
    held_kind: Optional[str] = None        # phone | toy | book
    near_object: Optional[str] = None      # đồ trong khung hình nhưng không cầm (điện thoại trên bàn)
    yawn_event: bool = False
    ext_motion: Optional[float] = None     # vi chuyển động tay từ camera mặt bàn (nếu có)
    face_covered: bool = False             # tay / đồ vật che mặt (điện thoại giơ trước mặt...)
    action_group: Optional[str] = None     # nhóm hành động MoViNet: study|phone|play|eat|drowsy|other
    action_vi: Optional[str] = None
    action_score: float = 0.0
    on_break: bool = False                 # học sinh đang xin nghỉ (đi vệ sinh) -> không tính vắng mặt


class _Bucket:
    """Bình tích lũy bằng chứng có rò rỉ + trễ (hysteresis) chống nhấp nháy cảnh báo."""

    def __init__(self, fill_s: float, decay: float = 1.0):
        self.fill_s = fill_s
        self.decay = decay
        self.level = 0.0
        self.active = False

    def step(self, on: bool, dt: float, gain: float = 1.0) -> bool:
        if on:
            self.level = min(self.fill_s * 1.6, self.level + dt * gain)
        else:
            self.level = max(0.0, self.level - dt * self.decay)
        if not self.active and self.level >= self.fill_s:
            self.active = True
        elif self.active and self.level < self.fill_s * 0.6:
            self.active = False
        return self.active

    def bump(self, amount: float) -> None:
        self.level = min(self.fill_s * 1.6, self.level + amount)

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
        self._closed_since: Optional[float] = None
        self._closures: Deque[Tuple[float, float]] = deque()   # (thời điểm kết thúc, thời lượng) các lần nhắm
        self._mhist: Dict[str, Deque[Tuple[float, float, float]]] = {}
        self._ema: Dict[str, Tuple[float, float]] = {}
        self._down_hist: Deque[Tuple[float, float, bool]] = deque()
        self._nod_pending: Optional[Tuple[float, float]] = None   # (thời điểm, mức cúi đỉnh)
        self._yawns: Deque[float] = deque()
        self._last_nod_t = 0.0
        self.nod_count = 0
        self.b_look = _Bucket(self.th["lookaway_fill_s"], decay=1.0)
        self.b_drowsy = _Bucket(self.th["drowsy_fill_s"], decay=self.th["drowsy_decay"])
        self.b_absent = _Bucket(self.th["absent_fill_s"], decay=2.0)
        self.b_idle = _Bucket(self.th["idle_fill_s"], decay=3.0)
        self.b_play = _Bucket(self.th["play_fill_s"], decay=1.0)
        self.b_phone = _Bucket(self.th["phone_near_fill_s"], decay=1.0)
        self.b_close = _Bucket(self.th["posture_fill_s"], decay=1.0)
        self.b_roll = _Bucket(self.th["posture_fill_s"], decay=1.0)
        self.b_shoulder = _Bucket(self.th["posture_fill_s"], decay=1.0)
        self.cfi = 100.0
        self.state = "NO_DATA"
        self._last_obj = ""

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _prune(dq: Deque, now: float, horizon: float) -> None:
        while dq and now - (dq[0][0] if isinstance(dq[0], tuple) else dq[0]) > horizon:
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
        hist = self._ear_hist[level]
        factor = (1.0, 0.82, 0.68)[level]
        if len(hist) >= 20:
            vals = sorted(e for _, e in hist)
            p90 = vals[int(0.9 * (len(vals) - 1))]
            return max(p90, self.base_ear * factor * 0.7)
        return self.base_ear * factor

    def _push_motion(self, key: str, now: float, xy: Tuple[float, float]) -> None:
        """Mỗi điểm theo dõi có lịch sử riêng (không trộn nguồn), làm mượt EMA để khử rung của mô hình."""
        hist = self._mhist.setdefault(key, deque())
        prev = self._ema.get(key)
        if prev is not None and math.hypot(xy[0] - prev[0], xy[1] - prev[1]) > self.th["motion_jump"]:
            hist.clear()   # lỗi bám điểm -> bắt đầu lại, không tạo vi chuyển động ảo
            prev = None
        sm = xy if prev is None else (0.5 * xy[0] + 0.5 * prev[0], 0.5 * xy[1] + 0.5 * prev[1])
        self._ema[key] = sm
        hist.append((now, sm[0], sm[1]))

    def _motion(self, now: float, keys: List[str]) -> float:
        win = self.th["motion_window_s"]
        m = 0.0
        for key, hist in list(self._mhist.items()):
            self._prune(hist, now, win)
            if key not in keys:
                continue
            if len(hist) >= 5:
                xs = [x for _, x, _ in hist]
                ys = [y for _, _, y in hist]
                # Điểm tay/khuỷu của Pose rung mạnh hơn mũi lưới mặt -> hạ trọng số để không tạo "đang viết" ảo
                wgt = 0.5 if key.startswith("arm:") else 1.0
                m = max(m, wgt * (_std(xs) + _std(ys)))
        return m

    def _eyes_closed(self, f: FrameFeatures, level: int, too_far: bool) -> Tuple[bool, Optional[float]]:
        """Trả về (đang nhắm?, EAR/gốc). Blendshape ưu tiên, EAR dự phòng; luôn chặn bằng EAR rất cao."""
        th = self.th
        if not f.face_ok or too_far:
            return False, None
        ear_norm = None
        if f.ear is not None:
            hist = self._ear_hist[level]
            hist.append((f.t, f.ear))
            self._prune(hist, f.t, 120.0)
            ref = self._ear_reference(level)
            ear_norm = f.ear / ref if ref > 0 else 1.0
        if f.blink is not None:
            ld = max(0.0, min(1.0, (f.look_down or 0.0) / 0.6))
            closed = f.blink >= th["blink_closed"] + th["blink_down_bonus"] * ld
        elif ear_norm is not None:
            down = max(0.0, min(1.0, (f.iris_down or 0.0) / 0.25))
            thr = th["closed_ratio"] - (th["closed_ratio"] - th["closed_ratio_down"]) * max(down, level / 2.0)
            closed = ear_norm < thr
        else:
            closed = False
        if closed and ear_norm is not None and ear_norm > th["ear_open_guard"]:
            closed = False
        return closed, ear_norm

    # ------------------------------------------------------------------ main
    def update(self, f: FrameFeatures) -> Dict[str, Any]:
        now = f.t
        dt = 0.0 if self._last_t is None else max(0.0, min(0.5, now - self._last_t))
        self._last_t = now
        th = self.th

        level = self._down_level(f)
        eyes_down = f.face_ok and f.look_down is not None and f.look_down >= th["look_down_bs"]
        keys: List[str] = []
        if f.nose_xy is not None:
            keys.append("nose:" + f.nose_src)
            self._push_motion(keys[-1], now, f.nose_xy)
        for side, xy in f.arm_pts.items():
            keys.append("arm:" + side)
            self._push_motion(keys[-1], now, xy)
        motion_now = self._motion(now, keys)
        if f.ext_motion is not None:
            motion_now = max(motion_now, f.ext_motion)
        too_far = f.distance_cm is not None and f.distance_cm > th["max_reliable_cm"]

        # ---------- Mắt: phân loại từng lần nhắm ----------
        closed, ear_norm = self._eyes_closed(f, level, too_far)
        closure_s = 0.0
        if closed:
            if self._closed_since is None:
                self._closed_since = now
            closure_s = now - self._closed_since
        elif self._closed_since is not None:
            self._closures.append((now, now - self._closed_since))
            self._closed_since = None
        self._prune(self._closures, now, th["perclos_window_s"])
        win = th["perclos_window_s"]
        sustained = sum(d for _, d in self._closures if d >= th["blink_max_s"])
        if closure_s >= th["blink_max_s"]:
            sustained += closure_s
        perclos = min(1.0, sustained / win)
        slow_blinks = sum(1 for _, d in self._closures if th["blink_max_s"] <= d < th["microsleep_s"])
        blinks = sum(1 for _, d in self._closures if d < th["blink_max_s"])
        microsleep = closure_s >= th["microsleep_s"]

        # ---------- Gật ngủ: đầu rơi khi mắt nhắm / mất mặt, rồi bật lại ----------
        down_val = max(f.face_down_delta or 0.0, (f.neck_drop or 0.0) * 0.5)
        self._down_hist.append((now, down_val, closed or not f.face_ok))
        self._prune(self._down_hist, now, 1.2)
        nod_event = False
        if self._nod_pending is None and len(self._down_hist) >= 5 and now - self._last_nod_t > 3.0:
            vals = [v for _, v, _ in self._down_hist]
            closed_frac = sum(1 for _, _, c in self._down_hist if c) / len(self._down_hist)
            if vals[-1] - min(vals) >= th["nod_drop"] and closed_frac >= 0.5:
                self._nod_pending = (now, vals[-1])
        elif self._nod_pending is not None:
            t0, peak = self._nod_pending
            peak = max(peak, down_val)
            self._nod_pending = (t0, peak)
            if peak - down_val >= th["nod_rebound"]:
                nod_event = True
                self._nod_pending = None
                self._last_nod_t = now
                self.nod_count += 1
            elif now - t0 > 2.0:
                self._nod_pending = None   # cúi xuống rồi ở yên = cúi viết/gục -> không phải gật ngủ

        if f.yawn_event:
            self._yawns.append(now)
        while self._yawns and now - self._yawns[0] > 300.0:
            self._yawns.popleft()

        # ---------- Hướng nhìn / quay đầu ----------
        yaw_limit = th["yaw_away_down_deg"] if level >= 1 else th["yaw_away_deg"]
        pose_turned = f.pose_yaw_deg is not None and abs(f.pose_yaw_deg) > th["pose_yaw_away_deg"]
        looking_away = False
        if f.face_ok:
            if f.yaw_deg is not None and abs(f.yaw_deg) > yaw_limit:
                looking_away = True
            if level == 0 and not eyes_down and not too_far and f.gaze_dev is not None and f.gaze_dev > th["gaze_away"]:
                looking_away = True
            if pose_turned and abs(f.pose_yaw_deg or 0) > th["pose_yaw_away_deg"] + 15:
                looking_away = True
        elif f.pose_ok:
            # Mất lưới mặt nhưng vai còn: quay đầu (Pose) -> mất tập trung; cúi sát bàn (cổ sụt mạnh) -> làm bài
            if pose_turned or level < 2:
                looking_away = True
        if looking_away:
            level_for_state = 0
        else:
            level_for_state = level

        absent_now = not f.face_ok and not f.pose_ok and not f.face_covered
        if f.on_break:
            absent_now = False
        head_down_ctx = level_for_state >= 1 or eyes_down
        idle_now = level >= 1 and motion_now < th["motion_idle"] and (closed or not f.face_ok) and not looking_away

        # ---------- Nghịch đồ / điện thoại ----------
        play_gain = 0.0
        act_on = f.action_group is not None and f.action_score >= th["action_min"]
        if f.held_kind in ("phone", "toy"):
            play_gain = 2.5
        elif f.hands_zone == "raised" and f.hands_together and (head_down_ctx or eyes_down):
            play_gain = 1.0   # 2 tay chụm trước ngực, mắt nhìn vào tay = tư thế cầm điện thoại/đồ chơi
        elif f.face_covered:
            play_gain = 0.8   # tay / đồ vật che mặt lâu (giơ điện thoại trước mặt)
        if act_on and f.action_group in ("phone", "play") and f.held_kind != "book":
            play_gain = max(play_gain, 0.6) + (0.6 if play_gain > 0 else 0.0)   # MoViNet: nhắn tin / chơi game...
        if f.on_break:
            play_gain = 0.0
        a_play = self.b_play.step(play_gain > 0, dt, gain=play_gain or 1.0)
        a_phone = self.b_phone.step(bool(f.near_object) and play_gain == 0, dt)
        if f.held_object:
            self._last_obj = f.held_object
        elif f.near_object:
            self._last_obj = f.near_object

        # ---------- Tích lũy bằng chứng ----------
        drowsy_gain = 0.0
        if microsleep:
            drowsy_gain = 2.0
        elif perclos >= th["perclos_drowsy"]:
            drowsy_gain = 1.0
        elif slow_blinks >= th["slow_blinks_drowsy"]:
            drowsy_gain = 0.6
        if act_on and f.action_group == "drowsy":
            drowsy_gain = max(drowsy_gain, 0.5) + (0.5 if drowsy_gain > 0 else 0.0)   # MoViNet: ngáp / ngủ
        if f.on_break:
            drowsy_gain = 0.0
        a_drowsy = self.b_drowsy.step(drowsy_gain > 0, dt, gain=drowsy_gain or 1.0)
        if nod_event:
            self.b_drowsy.bump(th["drowsy_fill_s"] * 0.6)
            a_drowsy = self.b_drowsy.step(True, 0.0)
        if f.yawn_event and len(self._yawns) >= 2:
            self.b_drowsy.bump(1.5)
        a_look = self.b_look.step(looking_away and not absent_now and not a_play and not f.face_covered
                                  and not f.on_break, dt)
        a_absent = self.b_absent.step(absent_now, dt)
        a_idle = self.b_idle.step(idle_now, dt)
        if f.distance_cm is not None:
            too_close = f.distance_cm < th["min_distance_cm"]
        else:
            too_close = f.eye_span_rel is not None and f.eye_span_rel > th["too_close_ratio"]
        a_close = self.b_close.step(too_close and f.face_ok, dt)
        a_roll = self.b_roll.step(f.roll_deg is not None and abs(f.roll_deg) > th["roll_deg"] and level == 0
                                  and not looking_away, dt)
        a_sh = self.b_shoulder.step(
            f.shoulder_tilt_delta is not None and abs(f.shoulder_tilt_delta) > th["shoulder_tilt"], dt
        )

        # ---------- Trạng thái hiện tại ----------
        if f.on_break:
            state = "BREAK"
        elif a_absent:
            state = "ABSENT"
        elif a_drowsy:
            state = "DROWSY"
        elif a_play:
            state = "PLAYING"
        elif a_look:
            state = "LOOK_AWAY"
        elif a_idle:
            state = "IDLE"
        elif head_down_ctx or (not f.face_ok and f.pose_ok and not looking_away):
            hands_working = f.hands_zone in ("desk", "below", "unknown")
            state = "WRITING" if (motion_now >= th["motion_idle"] and hands_working) else "READING"
        elif f.face_ok:
            state = "SCREEN"
        else:
            state = "NO_DATA"
        self.state = state

        # ---------- CFI: EMA của điểm tức thời ----------
        inst = {"SCREEN": 100.0, "WRITING": 100.0, "READING": 95.0, "LOOK_AWAY": 35.0, "PLAYING": 25.0,
                "BREAK": self.cfi,
                "DROWSY": 20.0, "IDLE": 45.0, "ABSENT": 0.0, "NO_DATA": self.cfi}[state]
        if state in FOCUS_STATES:
            inst -= 25.0 * max(self.b_look.ratio, self.b_drowsy.ratio, self.b_play.ratio) ** 2
        inst -= 8.0 * sum((a_close, a_roll, a_sh)) + (10.0 if a_phone else 0.0)
        inst = max(0.0, min(100.0, inst))
        alpha = 1.0 - math.exp(-dt / th["cfi_tau_s"]) if dt > 0 else 0.0
        self.cfi = self.cfi + alpha * (inst - self.cfi)

        alerts: List[str] = []
        codes: List[str] = []
        if a_absent:
            alerts.append("VẮNG MẶT: Không thấy học sinh trước camera")
            codes.append("ABSENT")
        if a_drowsy:
            why = ("vi ngủ: nhắm mắt %.1fs" % closure_s) if microsleep else (
                f"nhắm mắt {perclos * 100:.0f}% thời gian / {slow_blinks} lần chớp chậm")
            if self.nod_count:
                why += f" / gật đầu {self.nod_count} lần"
            alerts.append(f"BUỒN NGỦ: {why}")
            codes.append("DROWSY")
        if a_play:
            obj = self._last_obj or "đồ vật (2 tay chụm trước ngực)"
            alerts.append(f"NGHỊCH ĐỒ: Đang cầm {obj} trong giờ học")
            codes.append("PLAYING")
        elif a_phone:
            alerts.append(f"XAO NHÃNG: Có {self._last_obj or 'điện thoại'} trong tầm tay khi học")
            codes.append("PHONE_NEAR")
        if a_look:
            alerts.append("MẤT TẬP TRUNG: Quay đầu / nhìn ra ngoài khu vực học")
            codes.append("LOOK_AWAY")
        if a_idle:
            alerts.append("GỤC ĐẦU: Cúi bất động, không có hoạt động viết/đọc")
            codes.append("IDLE")
        if a_close:
            dist_txt = f" ({f.distance_cm:.0f}cm < {th['min_distance_cm']:.0f}cm)" if f.distance_cm else ""
            alerts.append(f"CẬN THỊ: Mặt quá sát màn hình/camera{dist_txt}")
            codes.append("TOO_CLOSE")
        if a_roll:
            alerts.append("SAI TƯ THẾ: Nghiêng đầu sang vai")
            codes.append("ROLL")
        if a_sh:
            alerts.append("SAI TƯ THẾ: Lệch vai / vẹo lưng")
            codes.append("SHOULDER")

        if f.on_break:
            alerts, codes = [], []
        return {
            "state": state,
            "state_vi": STATE_VI[state],
            "state_ascii": STATE_ASCII[state],
            "focused": state in FOCUS_STATES,
            "cfi": round(self.cfi, 1),
            "alerts": alerts,
            "alert_codes": codes,
            "head_down_level": level,
            "eyes_down": bool(eyes_down),
            "ear_norm": None if ear_norm is None else round(ear_norm, 3),
            "eyes_closed": closed,
            "closure_s": round(closure_s, 2),
            "perclos_rel": round(perclos * 100.0, 1),
            "blinks_60s": blinks,
            "slow_blinks_60s": slow_blinks,
            "motion": round(motion_now, 4),
            "distance_cm": None if f.distance_cm is None else round(f.distance_cm, 1),
            "low_quality": bool(too_far),
            "nod_count": self.nod_count,
            "head_turned": bool(looking_away),
            "face_covered": bool(f.face_covered),
            "action": ({"group": f.action_group, "vi": f.action_vi, "score": round(f.action_score, 2)}
                       if f.action_group else None),
            "hands_zone": f.hands_zone,
            "evidence": {
                "look_away": round(self.b_look.ratio, 2),
                "drowsy": round(self.b_drowsy.ratio, 2),
                "absent": round(self.b_absent.ratio, 2),
                "idle": round(self.b_idle.ratio, 2),
                "play": round(self.b_play.ratio, 2),
            },
        }


def _std(vals: List[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
