# -*- coding: utf-8 -*-
"""
Nhận diện HÀNH ĐỘNG con người bằng mô hình đã huấn luyện sẵn (services/action_recognizer.py)

Mô hình: MoViNet-A0 Stream (Google, huấn luyện trên Kinetics-600: 600 hành động thường ngày), bản TFLite float16
~13.7 MB, chạy CPU qua `ai-edge-litert`. Dạng "stream": mỗi lần chỉ nạp 1 khung 172x172 + trạng thái nội bộ
-> nhẹ, hợp chạy nền 5 khung/giây.

Các lớp liên quan đến góc học tập được gom nhóm:
  study    : viết, đọc sách, vẽ, gọt bút chì...           -> củng cố "đang học"
  phone    : nhắn tin, nhìn điện thoại, gọi điện          -> bằng chứng NGHỊCH ĐỒ
  play     : chơi tay cầm game, rubik, bài, lego, xếp hình, tung hứng, bồn chồn nghịch tay
  eat      : ăn / uống                                    -> ghi nhận (không phạt nặng)
  drowsy   : ngáp, ngủ                                    -> bằng chứng BUỒN NGỦ
  other    : chải tóc, làm móng, trang điểm, chơi nhạc cụ, hát...

Kết quả chỉ là BẰNG CHỨNG MỀM cộng vào bộ máy trạng thái (attention_model), không tự kết luận một mình,
vì mô hình huấn luyện trên video YouTube toàn cảnh, còn webcam bàn học chỉ thấy nửa người trên.
Thiếu thư viện / mô hình -> tự tắt, hệ thống vẫn chạy bình thường.
"""

import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from core.config_manager import safe_log

MODEL_FILE = "movinet_a0_stream_k600.tflite"
LABEL_FILE = "kinetics_600_labels.txt"
MODEL_URL = ("https://tfhub.dev/tensorflow/lite-model/movinet/a0/stream/kinetics-600/classification/"
             "tflite/float16/1?lite-format=tflite")
LABEL_URL = ("https://raw.githubusercontent.com/tensorflow/models/master/official/projects/movinet/files/"
             "kinetics_600_labels.txt")
INPUT = 172
FPS = 5.0
RESET_FRAMES = 40          # làm mới trạng thái mỗi ~8s để kết quả phản ánh hành động GẦN ĐÂY
MIN_FRAMES = 8             # cần >= 8 khung (~1.6s) sau mỗi lần làm mới mới đọc kết quả

GROUPS: Dict[str, Tuple[str, str]] = {
    # study
    "writing": ("viết bài", "study"), "reading book": ("đọc sách", "study"), "reading newspaper": ("đọc tài liệu", "study"),
    "drawing": ("vẽ", "study"), "calligraphy": ("viết chữ đẹp", "study"), "sharpening pencil": ("gọt bút chì", "study"),
    "answering questions": ("trả lời câu hỏi", "study"),
    # phone
    "texting": ("nhắn tin điện thoại", "phone"), "looking at phone": ("xem điện thoại", "phone"),
    "talking on cell phone": ("gọi điện thoại", "phone"),
    # play
    "playing controller": ("chơi tay cầm game", "play"), "playing rubiks cube": ("chơi rubik", "play"),
    "shuffling cards": ("chơi bài", "play"), "playing poker": ("chơi bài", "play"), "card stacking": ("chơi bài", "play"),
    "card throwing": ("chơi bài", "play"), "building lego": ("chơi lego", "play"), "doing jigsaw puzzle": ("xếp hình", "play"),
    "playing with trains": ("chơi đồ chơi", "play"), "juggling balls": ("tung hứng bóng", "play"),
    "spinning poi": ("nghịch đồ chơi", "play"), "fidgeting": ("bồn chồn nghịch tay", "play"),
    "playing hand clapping games": ("chơi vỗ tay", "play"), "playing chess": ("chơi cờ", "play"),
    # eat
    "eating chips": ("ăn vặt", "eat"), "eating cake": ("ăn bánh", "eat"), "eating doughnuts": ("ăn bánh", "eat"),
    "eating ice cream": ("ăn kem", "eat"), "eating carrots": ("ăn", "eat"), "eating burger": ("ăn", "eat"),
    "eating hotdog": ("ăn", "eat"), "tasting food": ("ăn", "eat"), "drinking shots": ("uống", "eat"),
    # drowsy
    "yawning": ("ngáp", "drowsy"), "sleeping": ("ngủ", "drowsy"),
    # other distraction
    "brushing hair": ("chải tóc", "other"), "doing nails": ("làm móng", "other"), "cutting nails": ("cắt móng", "other"),
    "putting on lipstick": ("trang điểm", "other"), "putting on eyeliner": ("trang điểm", "other"),
    "playing guitar": ("chơi guitar", "other"), "playing ukulele": ("chơi ukulele", "other"),
    "playing keyboard": ("chơi đàn", "other"), "singing": ("hát", "other"), "beatboxing": ("beatbox", "other"),
    "whistling": ("huýt sáo", "other"), "sticking tongue out": ("lè lưỡi đùa", "other"),
}


def _litert_interpreter():
    try:
        from ai_edge_litert.interpreter import Interpreter  # type: ignore

        return Interpreter
    except Exception:
        pass
    try:
        from tflite_runtime.interpreter import Interpreter  # type: ignore

        return Interpreter
    except Exception:
        pass
    try:
        import tensorflow as tf  # type: ignore

        return tf.lite.Interpreter
    except Exception:
        return None


class ActionRecognizer:
    """Chạy MoViNet trong luồng riêng; engine gọi push(frame, roi) mỗi khung, đọc latest() khi cần."""

    def __init__(self, models_dir: str):
        self.ok = False
        self.reason = ""
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._latest: Dict[str, Any] = {}
        self._probs_ema: Optional[np.ndarray] = None
        self._votes: List[str] = []
        Interpreter = _litert_interpreter()
        if Interpreter is None:
            self.reason = "Chưa cài ai-edge-litert (pip install ai-edge-litert)"
            return
        try:
            path = os.path.join(models_dir, MODEL_FILE)
            if not os.path.exists(path) or os.path.getsize(path) < 1_000_000:
                import requests

                safe_log("[AI] Đang tải mô hình nhận diện hành động MoViNet (1 lần)...")
                r = requests.get(MODEL_URL, timeout=90)
                r.raise_for_status()
                with open(path, "wb") as fh:
                    fh.write(r.content)
            lpath = os.path.join(models_dir, LABEL_FILE)
            if not os.path.exists(lpath):
                import requests

                r = requests.get(LABEL_URL, timeout=30)
                r.raise_for_status()
                with open(lpath, "w", encoding="utf-8") as fh:
                    fh.write(r.text)
            with open(lpath, encoding="utf-8") as fh:
                self.labels = [ln.strip() for ln in fh if ln.strip()]
            # Nạp qua bộ nhớ (model_content) để không lỗi với đường dẫn tiếng Việt có dấu trên Windows
            with open(path, "rb") as fh:
                self._model_bytes = fh.read()
            self._interp = Interpreter(model_content=self._model_bytes)
            self._runner = self._interp.get_signature_runner()
            self._in_details = self._runner.get_input_details()
            self._idx = {lab: i for i, lab in enumerate(self.labels)}
            self._group_idx = [(self._idx[k], k) for k in GROUPS if k in self._idx]
            self._reset_states()
            self.ok = True
            threading.Thread(target=self._loop, name="ActionRecognizerThread", daemon=True).start()
        except Exception as exc:
            self.reason = f"Không nạp được MoViNet: {exc}"
            safe_log(f"[AI] {self.reason}")

    def _reset_states(self) -> None:
        self._states = {name: np.zeros(d["shape"], dtype=d["dtype"]) for name, d in self._in_details.items()
                        if name != "image"}
        self._n = 0

    def push(self, bgr: np.ndarray, roi: Optional[Tuple[float, float, float, float]] = None) -> None:
        """Nhận khung mới (cắt vùng nửa người trên nếu có roi chuẩn hóa x0,y0,x1,y1)."""
        if not self.ok:
            return
        h, w = bgr.shape[:2]
        if roi is not None:
            x0, y0, x1, y1 = roi
            x0, x1 = int(max(0, x0) * w), int(min(1, x1) * w)
            y0, y1 = int(max(0, y0) * h), int(min(1, y1) * h)
            if x1 - x0 > 40 and y1 - y0 > 40:
                bgr = bgr[y0:y1, x0:x1]
        with self._lock:
            self._frame = bgr

    def latest(self, max_age: float = 3.0) -> Dict[str, Any]:
        d = self._latest
        return d if d and time.time() - d.get("t", 0) <= max_age else {}

    def _loop(self) -> None:
        period = 1.0 / FPS
        while not self._stop.is_set():
            t0 = time.time()
            with self._lock:
                frame, self._frame = self._frame, None
            if frame is not None:
                try:
                    self._step(frame)
                except Exception as exc:
                    safe_log(f"[AI] Lỗi MoViNet: {exc}")
                    self._reset_states()
            self._stop.wait(max(0.0, period - (time.time() - t0)))

    def _step(self, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        s = min(h, w)
        crop = bgr[(h - s) // 2:(h - s) // 2 + s, (w - s) // 2:(w - s) // 2 + s]
        img = cv2.cvtColor(cv2.resize(crop, (INPUT, INPUT), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        x = (img.astype(np.float32) / 255.0)[None, None]
        out = self._runner(image=x, **self._states)
        logits = out.pop("logits")[0]
        self._states = out
        self._n += 1
        if self._n >= RESET_FRAMES:
            self._reset_states()
        if self._n < MIN_FRAMES and self._probs_ema is not None:
            return
        e = np.exp(logits - logits.max())
        probs = e / e.sum()
        self._probs_ema = probs if self._probs_ema is None else 0.6 * self._probs_ema + 0.4 * probs
        p = self._probs_ema
        top_all = int(np.argmax(p))
        best = max(self._group_idx, key=lambda ik: p[ik[0]])
        group_scores: Dict[str, float] = {}
        for i, k in self._group_idx:
            g = GROUPS[k][1]
            group_scores[g] = group_scores.get(g, 0.0) + float(p[i])
        rank = int((p > p[best[0]]).sum())   # 0 = lớp liên quan đang đứng đầu cả 600 lớp
        raw_ok = bool(p[best[0]] >= 0.35 and rank <= 2)
        self._votes.append(GROUPS[best[1]][1] if raw_ok else "")
        self._votes = self._votes[-5:]
        grp = GROUPS[best[1]][1]
        stable = raw_ok and sum(1 for v in self._votes if v == grp) >= 4   # >= 4/5 lần đọc gần nhất (~1s)
        self._latest = {
            "t": time.time(),
            "rank": rank,
            # Chỉ coi là chắc khi lớp liên quan tốt nhất >= 0.35 VÀ nằm trong top-3 của cả 600 hành động
            "confident": stable,
            "label": best[1], "vi": GROUPS[best[1]][0], "group": GROUPS[best[1]][1], "score": round(float(p[best[0]]), 3),
            "top_any": self.labels[top_all], "top_any_score": round(float(p[top_all]), 3),
            "groups": {k: round(v, 3) for k, v in group_scores.items()},
        }

    def close(self) -> None:
        self._stop.set()
        self.ok = False
