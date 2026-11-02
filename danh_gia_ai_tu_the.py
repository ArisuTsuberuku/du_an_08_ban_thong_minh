# -*- coding: utf-8 -*-
"""
Đánh giá độ chính xác AI Tư thế từ log đặc trưng có gán nhãn (logs/posture_features_*.csv).

Quy trình thu dữ liệu:
  1. python posture_ai_engine.py  -> ngồi thẳng 5s để hiệu chỉnh gốc
  2. Nhấn 'l' bật ghi log (chấm đỏ góc phải)
  3. Diễn từng tình huống 1-3 phút và nhấn phím nhãn tương ứng TRƯỚC khi diễn:
       1 Nhìn màn hình | 2 Cúi viết | 3 Cúi đọc | 4 Mất tập trung | 5 Buồn ngủ | 6 Vắng mặt | 0 Bỏ nhãn
  4. python danh_gia_ai_tu_the.py [file.csv ...]

Chỉ số in ra:
  - Ma trận nhầm lẫn nhãn thật x trạng thái AI
  - Mức nhị phân "Tập trung" (SCREEN/WRITING/READING) vs "Mất tập trung" (còn lại):
      Precision = báo mất tập trung đúng / tổng lần báo   (thấp => báo nhầm nhiều, phụ huynh bị làm phiền)
      Recall    = bắt được / tổng lần thật sự mất tập trung
      FPR-cúi-viết = tỉ lệ khung "Cúi viết/đọc" bị coi là mất tập trung  (mục tiêu < 5%)
  - Thống kê đặc trưng theo nhãn để chỉnh ngưỡng trong shared_config["attention_thresholds"].
Lưu ý: bỏ 6 giây đầu sau mỗi lần đổi nhãn (thời gian bình bằng chứng cần để đầy/xả).
"""

import csv
import glob
import os
import statistics
import sys
from collections import Counter, defaultdict

FOCUS = {"SCREEN", "WRITING", "READING"}
ORDER = ["SCREEN", "WRITING", "READING", "LOOK_AWAY", "DROWSY", "IDLE", "ABSENT", "NO_DATA"]
WARMUP_S = 6.0


def load_rows(paths):
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))
    rows.sort(key=lambda r: float(r["t"]))
    out, last_label, label_start = [], None, 0.0
    for r in rows:
        t = float(r["t"])
        if r["label"] != last_label:
            last_label, label_start = r["label"], t
        if r["label"] and t - label_start >= WARMUP_S:
            out.append(r)
    return out


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    paths = sys.argv[1:] or sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                          "logs", "posture_features_*.csv")))
    if not paths:
        print("Chưa có log. Chạy posture_ai_engine.py, nhấn 'l' và gán nhãn bằng phím 1-6.")
        return 1
    rows = load_rows(paths)
    if not rows:
        print("Log chưa có dòng nào được gán nhãn (sau 6s ổn định).")
        return 1

    labels = [l for l in ORDER if any(r["label"] == l for r in rows)]
    preds = [p for p in ORDER if any(r["state"] == p for r in rows)]
    cm = defaultdict(Counter)
    for r in rows:
        cm[r["label"]][r["state"]] += 1

    print(f"Số khung đánh giá: {len(rows)} (từ {len(paths)} file)\n")
    print("MA TRẬN NHẦM LẪN (hàng = nhãn thật, cột = AI dự đoán, % theo hàng)")
    print(f"{'':12s}" + "".join(f"{p[:9]:>10s}" for p in preds) + f"{'n':>8s}")
    for l in labels:
        n = sum(cm[l].values())
        print(f"{l:12s}" + "".join(f"{100 * cm[l][p] / n:9.1f}%" for p in preds) + f"{n:8d}")

    tp = sum(1 for r in rows if r["label"] not in FOCUS and r["state"] not in FOCUS)
    fp = sum(1 for r in rows if r["label"] in FOCUS and r["state"] not in FOCUS)
    fn = sum(1 for r in rows if r["label"] not in FOCUS and r["state"] in FOCUS)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if prec == prec and rec == rec and prec + rec else float("nan")
    down = [r for r in rows if r["label"] in ("WRITING", "READING")]
    fpr_down = sum(1 for r in down if r["state"] not in FOCUS) / len(down) if down else float("nan")
    print("\nNHỊ PHÂN Tập trung / Mất tập trung")
    print(f"  Precision={prec:.3f}  Recall={rec:.3f}  F1={f1:.3f}  | Báo nhầm khi cúi viết/đọc = {fpr_down:.1%}")

    print("\nTHỐNG KÊ ĐẶC TRƯNG THEO NHÃN (trung vị) -> dùng để chỉnh attention_thresholds")
    feats = ["ear_norm", "face_down_delta", "neck_drop", "yaw", "gaze_dev", "motion", "distance_cm"]
    print(f"{'':12s}" + "".join(f"{c[:12]:>14s}" for c in feats))
    by = defaultdict(list)
    for r in rows:
        by[r["label"]].append(r)
    for l in labels:
        cells = []
        for c in feats:
            vals = [v for v in (fnum(r[c]) for r in by[l]) if v is not None]
            cells.append(f"{statistics.median(vals):14.3f}" if vals else f"{'-':>14s}")
        print(f"{l:12s}" + "".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
