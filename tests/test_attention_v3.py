# -*- coding: utf-8 -*-
"""Kiểm thử kịch bản cho AttentionModel v3 (chạy: python -m pytest tests/ hoặc python tests/test_attention_v3.py)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.attention_model import AttentionModel, FrameFeatures  # noqa: E402

FPS = 12.0


def run(model, seconds, make, t0=0.0):
    out = None
    n = int(seconds * FPS)
    for i in range(n):
        t = t0 + i / FPS
        out = model.update(make(t, i))
    return out, t0 + n / FPS


def face(t, **kw):
    base = dict(t=t, face_ok=True, pose_ok=True, ear=0.30, yaw_deg=0.0, roll_deg=0.0, face_down_delta=0.0,
                neck_drop=0.0, gaze_dev=0.05, nose_xy=(1.0 + 0.004 * math.sin(t * 7), 1.0), distance_cm=55.0,
                blink=0.05, look_down=0.05, pose_yaw_deg=0.0, hands_zone="below")
    base.update(kw)
    return FrameFeatures(**base)


def test_look_down_with_open_eyes_is_not_drowsy():
    m = AttentionModel()
    # Liếc xuống vở: EAR giảm mạnh, eyeBlink tăng vừa (0.6) nhưng eyeLookDown cao
    out, _ = run(m, 60, lambda t, i: face(t, ear=0.17, blink=0.6, look_down=0.75, face_down_delta=0.08))
    assert out["state"] != "DROWSY", out
    assert out["evidence"]["drowsy"] < 0.2


def test_ear_guard_blocks_false_closure():
    m = AttentionModel()
    m.set_baseline(0.50)
    out, _ = run(m, 30, lambda t, i: face(t, ear=0.50, blink=0.7, look_down=0.0))  # blendshape lỗi, EAR vẫn mở
    assert out["state"] != "DROWSY"


def test_microsleep_detected_fast_and_clears():
    m = AttentionModel()
    _, t = run(m, 5, lambda t, i: face(t))
    out, t = run(m, 4.0, lambda t, i: face(t, blink=0.92, ear=0.08), t0=t)
    assert out["state"] == "DROWSY", out
    out, t = run(m, 6.0, lambda t, i: face(t), t0=t)
    assert out["state"] != "DROWSY", "Cảnh báo buồn ngủ phải tự hết sau ~6s mở mắt"


def test_normal_blinks_not_drowsy():
    m = AttentionModel()
    out, _ = run(m, 90, lambda t, i: face(t, **({"blink": 0.9, "ear": 0.08} if (t % 4.0) < 0.2 else {})))
    assert out["state"] == "SCREEN", out
    assert out["blinks_60s"] >= 10


def test_slow_blinks_raise_drowsy():
    m = AttentionModel()
    out, _ = run(m, 60, lambda t, i: face(t, **({"blink": 0.9, "ear": 0.08} if (t % 3.0) < 0.9 else {})))
    assert out["state"] == "DROWSY", out


def test_profile_turn_is_look_away_not_writing():
    m = AttentionModel()
    _, t = run(m, 3, lambda t, i: face(t))
    # Quay hẳn sang ngang: mất lưới mặt, Pose còn; mũi Pose thấp (cổ sụt nhẹ) + Pose yaw 70°
    out, t = run(m, 8, lambda t, i: FrameFeatures(t=t, face_ok=False, pose_ok=True, neck_drop=0.15,
                                                   pose_yaw_deg=70.0, nose_xy=(1.3, 1.0), nose_src="pose",
                                                   hands_zone="below"), t0=t)
    assert out["state"] == "LOOK_AWAY", out


def test_deep_head_down_writing():
    m = AttentionModel()
    out, _ = run(m, 20, lambda t, i: FrameFeatures(
        t=t, face_ok=False, pose_ok=True, neck_drop=0.35, pose_yaw_deg=4.0, nose_src="pose",
        nose_xy=(1.0 + 0.02 * math.sin(t * 5), 1.0), hands_zone="below",
        arm_pts={"R": (1.2 + 0.03 * math.sin(t * 9), 2.0)}))
    assert out["state"] == "WRITING", out


def test_writing_with_eyes_on_paper():
    m = AttentionModel()
    out, _ = run(m, 30, lambda t, i: face(t, face_down_delta=0.16, ear=0.16, blink=0.55, look_down=0.8,
                                          arm_pts={"R": (1.2 + 0.03 * math.sin(t * 9), 2.0)}, hands_zone="desk"))
    assert out["state"] == "WRITING", out


def test_phone_in_hand_playing():
    m = AttentionModel()
    out, _ = run(m, 5, lambda t, i: face(t, face_down_delta=0.10, look_down=0.6, hands_zone="raised",
                                         held_object="điện thoại", held_kind="phone"))
    assert out["state"] == "PLAYING", out
    assert any("điện thoại" in a for a in out["alerts"])


def test_two_hands_together_raised_playing():
    m = AttentionModel()
    out, _ = run(m, 12, lambda t, i: face(t, face_down_delta=0.10, look_down=0.7, hands_zone="raised",
                                          hands_together=True))
    assert out["state"] == "PLAYING", out


def test_book_held_is_reading_not_playing():
    m = AttentionModel()
    out, _ = run(m, 15, lambda t, i: face(t, face_down_delta=0.08, look_down=0.6, hands_zone="raised",
                                          hands_together=False, held_kind="book"))
    assert out["state"] in ("READING", "WRITING"), out


def test_nod_off_counts_nod():
    m = AttentionModel()
    _, t = run(m, 3, lambda t, i: face(t))

    def nod(t, i):
        k = (t - 3.0)
        if k < 0.8:   # đầu rơi dần khi mắt nhắm
            return face(t, face_down_delta=0.18 * k / 0.8, blink=0.9, ear=0.08)
        if k < 1.2:   # giật mình bật lại
            return face(t, face_down_delta=0.02, blink=0.2)
        return face(t)
    out, _ = run(m, 3, nod, t0=t)
    assert m.nod_count == 1, out


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                fails += 1
                print("FAIL", name, str(exc)[:300])
    sys.exit(1 if fails else 0)
