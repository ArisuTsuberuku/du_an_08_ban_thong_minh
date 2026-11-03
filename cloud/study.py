# -*- coding: utf-8 -*-
"""
Ca học thành công · điểm thưởng · xuất cho LMS (cloud/study.py)

Máy học sinh gửi bản tổng kết mỗi "ca học" (từ lúc vào chế độ Học sinh tới lúc rời chế độ đó). Web chấm ca đó
THÀNH CÔNG khi đủ cả 3 tiêu chí phụ huynh / giáo viên đặt:
  1. học đủ `min_minutes` phút (đã trừ thời gian xin đi vệ sinh),
  2. số lần vi phạm (mở game / web giải trí, nhờ AI làm game, quá giờ AI, cầm điện thoại) <= `max_violations`,
  3. mức tập trung trung bình từ camera AI >= `min_focus` % (chỉ xét khi máy có camera).
Mỗi ca thành công được cộng điểm; điểm đổi được phần thưởng phụ huynh đặt. Giáo viên nhận số liệu qua link
LMS (JSON / CSV cho Google Sheets IMPORTDATA, Moodle, K12Online…) và trang "Bảng tuyên dương" in được.

Lưu trên Firebase:
  sessions/{did}/{start:010d}-{id}   bản ghi 1 ca (khóa sắp xếp theo thời gian -> lọc N ngày gần nhất rẻ)
  devices/{did}/study_cfg            tiêu chí + phần thưởng + thông tin học sinh cho LMS
  devices/{did}/study_stats          điểm, tổng số ca thành công, chuỗi ngày, lịch sử đổi quà
  lms_tokens/{token}                 {did, uid, created}: link công khai cho giáo viên (thu hồi được)
"""

import csv
import io
import time
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_CFG: Dict[str, Any] = {
    "min_minutes": 25, "max_violations": 0, "min_focus": 67, "points_per_session": 10, "max_blocks": 3,
    "water_on_success": True, "water_seconds": 2.5,
    "student_name": "", "class_name": "", "student_code": "", "school": "", "webhook_url": "",
    "rewards": [{"name": "30 phút chơi game cuối tuần", "points": 50},
                {"name": "Chọn phim tối thứ Bảy", "points": 80},
                {"name": "Đi ăn kem / đồ ăn yêu thích", "points": 150}],
}
VIOLATION_VI = {"GAME": "mở game / web giải trí", "AI_GAME": "nhờ AI làm game", "AI_OVERUSE": "quá giờ AI",
                "PHONE": "dùng điện thoại", "POSTURE_PHONE": "cầm điện thoại"}
MIN_RECORD_MIN = 1.0          # ca ngắn hơn 1 phút (vào nhầm rồi thoát) -> không ghi
BADGES = ((20, "🥇 Huy hiệu Vàng"), (12, "🥈 Huy hiệu Bạc"), (6, "🥉 Huy hiệu Đồng"))


def _int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError):
        return default


def clean_cfg(raw: Any) -> Dict[str, Any]:
    """Chuẩn hóa tiêu chí từ web (giới hạn giá trị, cắt chuỗi). Thiếu trường -> giá trị mặc định."""
    raw = raw if isinstance(raw, dict) else {}
    cfg = dict(DEFAULT_CFG)
    cfg["min_minutes"] = _int(raw.get("min_minutes", cfg["min_minutes"]), 25, 5, 240)
    cfg["max_violations"] = _int(raw.get("max_violations", cfg["max_violations"]), 0, 0, 20)
    # min_focus = % THỜI GIAN HỌC có tập trung (mặc định 67% ≈ 2/3), phụ huynh đặt riêng
    cfg["min_focus"] = _int(raw.get("min_focus", cfg["min_focus"]), 67, 0, 100)
    cfg["water_on_success"] = bool(raw.get("water_on_success", cfg["water_on_success"]))
    try:
        cfg["water_seconds"] = round(max(0.5, min(10.0, float(raw.get("water_seconds", cfg["water_seconds"])))), 1)
    except (TypeError, ValueError):
        cfg["water_seconds"] = 2.5
    cfg["points_per_session"] = _int(raw.get("points_per_session", cfg["points_per_session"]), 10, 1, 1000)
    cfg["max_blocks"] = _int(raw.get("max_blocks", cfg["max_blocks"]), 3, 1, 10)
    for k, n in (("student_name", 60), ("class_name", 30), ("student_code", 40), ("school", 80)):
        cfg[k] = str(raw.get(k, "") or "").strip()[:n]
    wh = str(raw.get("webhook_url", "") or "").strip()[:300]
    cfg["webhook_url"] = wh if wh.startswith("https://") else ""
    rw = raw.get("rewards", cfg["rewards"])
    rewards = []
    for r in (rw if isinstance(rw, list) else []):
        if isinstance(r, dict) and str(r.get("name", "")).strip():
            rewards.append({"name": str(r["name"]).strip()[:60], "points": _int(r.get("points"), 50, 1, 100000)})
    cfg["rewards"] = rewards[:12]
    return cfg


def evaluate(rep: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[bool, List[str], int]:
    """(thành công?, lý do chưa đạt, điểm). Điểm = điểm/ca × số khối `min_minutes` học được (tối đa max_blocks)."""
    reasons: List[str] = []
    minutes = float(rep.get("minutes") or 0)
    if minutes < cfg["min_minutes"]:
        reasons.append(f"học {minutes:g}/{cfg['min_minutes']} phút")
    viol = {k: int(v) for k, v in (rep.get("violations") or {}).items() if isinstance(v, (int, float))}
    nv = sum(viol.values())
    if nv > cfg["max_violations"]:
        reasons.append(f"{nv} vi phạm (" + ", ".join(f"{VIOLATION_VI.get(k, k.lower())} ×{v}"
                                                    for k, v in viol.items() if v) + ")")
    # Tập trung: % thời gian học có tập trung (bản máy mới); bản cũ chỉ có điểm tập trung trung bình
    fr = rep.get("focus_ratio")
    if not isinstance(fr, (int, float)):
        fr = rep.get("focus") if isinstance(rep.get("focus"), (int, float)) else None
    if fr is not None and cfg["min_focus"] and fr < cfg["min_focus"]:
        reasons.append(f"tập trung {fr:g}% thời gian < {cfg['min_focus']}%")
    ok = not reasons
    blocks = min(cfg["max_blocks"], int(minutes // cfg["min_minutes"])) if ok else 0
    return ok, reasons, cfg["points_per_session"] * max(1, blocks) if ok else 0


def session_key(rep: Dict[str, Any]) -> str:
    sid = "".join(c for c in str(rep.get("id", "")) if c.isalnum())[:16] or "x"
    return f"{int(rep.get('start') or 0):010d}-{sid}"


def update_stats(stats: Dict[str, Any], day: str, prev_day: str, points: int, minutes: float) -> Dict[str, Any]:
    """Cộng điểm + chuỗi ngày liên tiếp có ít nhất 1 ca thành công."""
    st = dict(stats or {})
    st["points"] = int(st.get("points", 0) or 0) + points
    st["earned_total"] = int(st.get("earned_total", 0) or 0) + points
    st["total_success"] = int(st.get("total_success", 0) or 0) + 1
    st["success_minutes"] = round(float(st.get("success_minutes", 0) or 0) + minutes, 1)
    last = str(st.get("last_success_day", "") or "")
    if last != day:
        st["streak"] = int(st.get("streak", 0) or 0) + 1 if last == prev_day else 1
        st["last_success_day"] = day
    st["best_streak"] = max(int(st.get("best_streak", 0) or 0), int(st.get("streak", 0) or 0))
    return st


def live_streak(stats: Dict[str, Any], today: str, yesterday: str) -> int:
    """Chuỗi hiện tại: ngày thành công gần nhất không phải hôm nay / hôm qua -> chuỗi đã đứt."""
    return int(stats.get("streak", 0) or 0) if stats.get("last_success_day") in (today, yesterday) else 0


def badge(success_30d: int) -> str:
    for n, name in BADGES:
        if success_30d >= n:
            return name
    return ""


def summarize(records: Dict[str, Any], day_of, today: str, week_start: str, month_start: str) -> Dict[str, Any]:
    """Tổng hợp danh sách ca: số ca / số ca thành công / phút học theo hôm nay, tuần, 30 ngày + theo từng ngày."""
    out = {"today": [0, 0], "week": [0, 0], "d30": [0, 0], "minutes_30d": 0.0, "focus_30d": None, "by_day": {},
           "water_30d": 0}
    fsum, fn = 0.0, 0
    for _k, r in sorted((records or {}).items()):
        if not isinstance(r, dict):
            continue
        d = day_of(r.get("start"))
        ok = 1 if r.get("success") else 0
        bd = out["by_day"].setdefault(d, [0, 0, 0.0])
        bd[0] += 1
        bd[1] += ok
        bd[2] = round(bd[2] + float(r.get("minutes") or 0), 1)
        for key, start in (("today", today), ("week", week_start), ("d30", month_start)):
            if d >= start:
                out[key][0] += 1
                out[key][1] += ok
        if d >= month_start:
            out["minutes_30d"] = round(out["minutes_30d"] + float(r.get("minutes") or 0), 1)
            out["water_30d"] += 1 if r.get("watered") else 0
            fv = r.get("focus_ratio") if isinstance(r.get("focus_ratio"), (int, float)) else r.get("focus")
            if isinstance(fv, (int, float)):
                fsum, fn = fsum + float(fv), fn + 1
    out["focus_30d"] = round(fsum / fn, 1) if fn else None
    return out


CSV_HEADER = ["ngay", "bat_dau", "ket_thuc", "phut_hoc", "nghi_ve_sinh_phut", "tap_trung_pct", "tap_trung_thoi_gian_pct",
              "so_vi_pham", "chi_tiet_vi_pham", "phut_ai", "thanh_cong", "tuoi_cay", "diem", "ly_do_chua_dat",
              "ma_hoc_sinh", "ho_ten", "lop"]


def to_csv(records: Dict[str, Any], cfg: Dict[str, Any], tz_offset_s: int) -> str:
    """CSV UTF-8 (có BOM để Excel đọc đúng tiếng Việt). Cột không dấu để Moodle / LMS nhập dễ."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER)
    fmt = lambda ts, f: time.strftime(f, time.gmtime(int(ts or 0) + tz_offset_s))  # noqa: E731
    for _k, r in sorted((records or {}).items()):
        if not isinstance(r, dict):
            continue
        viol = r.get("violations") or {}
        w.writerow([fmt(r.get("start"), "%Y-%m-%d"), fmt(r.get("start"), "%H:%M"), fmt(r.get("end"), "%H:%M"),
                    r.get("minutes", 0), r.get("break_min", 0), "" if r.get("focus") is None else r.get("focus"),
                    "" if r.get("focus_ratio") is None else r.get("focus_ratio"),
                    sum(int(v) for v in viol.values() if isinstance(v, (int, float))),
                    "; ".join(f"{VIOLATION_VI.get(k, k)} x{v}" for k, v in viol.items()),
                    r.get("ai_min", 0), 1 if r.get("success") else 0, 1 if r.get("watered") else 0, r.get("points", 0),
                    "; ".join(r.get("reasons") or []), cfg.get("student_code", ""), cfg.get("student_name", ""),
                    cfg.get("class_name", "")])
    return "﻿" + buf.getvalue()


def redeem(stats: Dict[str, Any], cfg: Dict[str, Any], index: int, ts: int) -> Tuple[Optional[Dict[str, Any]], str]:
    """Đổi phần thưởng thứ `index`. Trả (stats mới, thông báo) hoặc (None, lỗi)."""
    rewards = cfg.get("rewards") or []
    if not (0 <= index < len(rewards)):
        return None, "Không có phần thưởng này."
    rw = rewards[index]
    pts = int(stats.get("points", 0) or 0)
    if pts < int(rw["points"]):
        return None, f"Chưa đủ điểm: cần {rw['points']}, đang có {pts}."
    st = dict(stats)
    st["points"] = pts - int(rw["points"])
    hist = [h for h in (st.get("redeemed") or []) if isinstance(h, dict)]
    st["redeemed"] = ([{"ts": ts, "name": rw["name"], "points": int(rw["points"])}] + hist)[:30]
    return st, f"Đã đổi “{rw['name']}” (−{rw['points']} điểm). Còn {st['points']} điểm."
