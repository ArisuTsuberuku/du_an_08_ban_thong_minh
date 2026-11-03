# -*- coding: utf-8 -*-
"""
Ca học (services/study_sessions.py)

Một "ca học" = từ lúc học sinh vào chế độ Học sinh tới lúc rời chế độ đó (về màn hình Khóa, phụ huynh / admin
đăng nhập, thoát phần mềm, hoặc máy tắt ngang). Trong ca, phần mềm ghi lại:
  - số phút học (trừ thời gian nghỉ vệ sinh),
  - số lần vi phạm theo loại (mở game / web giải trí, nhờ AI làm game, quá giờ AI, ứng dụng ngoài danh sách),
  - mức tập trung trung bình từ camera AI (nếu có camera),
  - số phút dùng AI trong ca.
Hết ca, bản tổng kết được xếp vào hàng đợi `session_reports`; cloud_agent gửi lên web phụ huynh. Web chấm
"thành công" theo tiêu chí phụ huynh / giáo viên đặt, cộng điểm thưởng và xuất cho hệ thống LMS của trường.
"""

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from core.config_manager import load_runtime_state, safe_log, save_runtime_state

try:
    from core.config_manager import _IO_LOCK  # khóa ghi runtime_state dùng chung
except Exception:  # pragma: no cover
    import threading

    _IO_LOCK = threading.RLock()

# Loại cảnh báo tính là vi phạm trong ca học
VIOLATION_TYPES = {"GAME": "Mở game / web giải trí", "AI_GAME": "Nhờ AI làm game", "AI_OVERUSE": "Quá giờ AI",
                   "PHONE": "Dùng điện thoại", "POSTURE_PHONE": "Cầm điện thoại"}
FOCUS_CFI = 60.0      # khoảnh khắc có CFI >= 60% (camera) được tính là "đang tập trung"
STALE_S = 180          # ca không được cập nhật quá 3 phút (máy tắt ngang) -> chốt ở lần cập nhật cuối
MAX_QUEUE = 30


def _mutate(fn: Callable[[Dict[str, Any]], Any]) -> Any:
    with _IO_LOCK:
        st = load_runtime_state()
        out = fn(st)
        save_runtime_state(st)
        return out


def current() -> Optional[Dict[str, Any]]:
    s = load_runtime_state().get("study_session")
    return s if isinstance(s, dict) and s.get("id") else None


def start_session(ai_seconds: float = 0.0, phone_in_box: Optional[bool] = None) -> Dict[str, Any]:
    """Bắt đầu ca mới (ca cũ còn mở do máy tắt ngang thì chốt trước)."""
    close_stale(force=True)
    now = time.time()
    sess = {"id": uuid.uuid4().hex[:12], "start": now, "last": now, "violations": {}, "cfi_sum": 0.0, "cfi_n": 0,
            "ai_start": float(ai_seconds or 0), "break_s": 0.0, "phone_in_box": phone_in_box}
    _mutate(lambda st: st.__setitem__("study_session", sess))
    safe_log(f"[CA HỌC] Bắt đầu ca {sess['id']}")
    return sess


def note_violation(event_type: str) -> None:
    """Gọi mỗi khi có cảnh báo; chỉ tính khi đang trong ca và loại đó là vi phạm."""
    t = str(event_type or "").upper()
    if t not in VIOLATION_TYPES:
        return

    def fn(st: Dict[str, Any]) -> None:
        s = st.get("study_session")
        if isinstance(s, dict) and s.get("id"):
            v = s.setdefault("violations", {})
            v[t] = int(v.get(t, 0)) + 1
    _mutate(fn)


def tick(cfi: Optional[float] = None, ai_seconds: Optional[float] = None, on_break: bool = False,
         dt: float = 0.0) -> None:
    """Gọi định kỳ (khoảng 5 giây) từ bộ giám sát: cập nhật mốc sống, mức tập trung, thời gian nghỉ."""
    def fn(st: Dict[str, Any]) -> None:
        s = st.get("study_session")
        if not (isinstance(s, dict) and s.get("id")):
            return
        s["last"] = time.time()
        if cfi is not None and not on_break:
            s["cfi_sum"] = float(s.get("cfi_sum", 0)) + float(cfi)
            s["cfi_n"] = int(s.get("cfi_n", 0)) + 1
            step = dt if dt > 0 else 5.0
            s["cam_s"] = float(s.get("cam_s", 0)) + step              # thời gian camera quan sát được
            if float(cfi) >= FOCUS_CFI:
                s["focus_s"] = float(s.get("focus_s", 0)) + step      # trong đó, thời gian tập trung
        if on_break and dt > 0:
            s["break_s"] = float(s.get("break_s", 0)) + dt
        if ai_seconds is not None:
            s["ai_now"] = float(ai_seconds)
    _mutate(fn)


def _report(s: Dict[str, Any], end: float, reason: str) -> Dict[str, Any]:
    dur = max(0.0, end - float(s.get("start", end)) - float(s.get("break_s", 0)))
    n = int(s.get("cfi_n", 0))
    ai_used = max(0.0, float(s.get("ai_now", s.get("ai_start", 0))) - float(s.get("ai_start", 0)))
    return {"id": s.get("id"), "start": int(s.get("start", end)), "end": int(end), "minutes": round(dur / 60.0, 1),
            "break_min": round(float(s.get("break_s", 0)) / 60.0, 1),
            "violations": {k: int(v) for k, v in (s.get("violations") or {}).items()},
            "focus": round(float(s.get("cfi_sum", 0)) / n, 1) if n else None,
            # % thời gian học có tập trung (chỉ tính khi camera quan sát được >= 1 phút)
            "focus_ratio": (round(100.0 * float(s.get("focus_s", 0)) / float(s["cam_s"]), 1)
                            if float(s.get("cam_s", 0)) >= 60 else None),
            "ai_min": round(ai_used / 60.0, 1), "phone_in_box": s.get("phone_in_box"), "end_reason": reason}


def end_session(reason: str = "Rời chế độ học sinh") -> Optional[Dict[str, Any]]:
    """Chốt ca đang mở -> xếp bản tổng kết vào hàng đợi gửi web. Không có ca -> None."""
    def fn(st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        s = st.get("study_session")
        if not (isinstance(s, dict) and s.get("id")):
            return None
        end = time.time()
        if end - float(s.get("last", end)) > STALE_S:
            end = float(s.get("last", end))              # máy tắt ngang: chốt ở lần cập nhật cuối
        rep = _report(s, end, reason)
        q = [r for r in (st.get("session_reports") or []) if isinstance(r, dict)]
        q.append(rep)
        st["session_reports"] = q[-MAX_QUEUE:]
        st["study_session"] = {}
        return rep
    rep = _mutate(fn)
    if rep:
        safe_log(f"[CA HỌC] Kết thúc ca {rep['id']}: {rep['minutes']} phút, vi phạm {rep['violations']}, "
                 f"tập trung {rep['focus']}")
    return rep


def close_stale(force: bool = False) -> Optional[Dict[str, Any]]:
    """Lúc khởi động: ca còn mở từ lần chạy trước (mất điện, tắt ngang) -> chốt."""
    s = current()
    if not s:
        return None
    if force or time.time() - float(s.get("last", 0)) > STALE_S:
        return end_session("Máy tắt / phần mềm dừng giữa ca")
    return None


def pop_reports() -> List[Dict[str, Any]]:
    """Lấy hết bản tổng kết chờ gửi (cloud_agent gọi). Gửi lỗi thì trả lại bằng requeue()."""
    def fn(st: Dict[str, Any]) -> List[Dict[str, Any]]:
        q = [r for r in (st.get("session_reports") or []) if isinstance(r, dict)]
        st["session_reports"] = []
        return q
    return _mutate(fn)


def requeue(reports: List[Dict[str, Any]]) -> None:
    if not reports:
        return

    def fn(st: Dict[str, Any]) -> None:
        q = list(reports) + [r for r in (st.get("session_reports") or []) if isinstance(r, dict)]
        st["session_reports"] = q[-MAX_QUEUE:]
    _mutate(fn)


def has_pending() -> bool:
    return bool(load_runtime_state().get("session_reports"))
