# -*- coding: utf-8 -*-
"""Kiểm thử chặn theo TỪNG LINK (chỉ 1 video / trang, không cả tên miền)."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for m in ("pyautogui", "pygetwindow", "winreg"):
    sys.modules.setdefault(m, types.ModuleType(m))
from services.giam_sat_worker import phan_loai_truy_cap_web  # noqa: E402
from services.page_rules import match_blocked_page, norm_title, page_key  # noqa: E402

EDGE = "(50) Thức Giấc - Da LAB (Official Music Video) - YouTube and 2 more pages - Personal - Microsoft​ Edge"
RULES = [{"d": "youtube.com", "t": norm_title(EDGE), "u": "v=abc123"}]


def test_norm_title_stable_across_tab_count():
    single = "Thức Giấc - Da LAB (Official Music Video) - YouTube - Personal - Microsoft Edge"
    assert norm_title(EDGE) == norm_title(single) == "thức giấc - da lab (official music video) - youtube - personal"


def test_page_key():
    assert page_key("https://www.youtube.com/watch?v=abc123&t=5") == "v=abc123"
    assert page_key("https://youtu.be/abc123") == "v=abc123"
    assert page_key("https://www.youtube.com/shorts/xyz98765") == "shorts/xyz98765"
    assert page_key("https://www.google.com/search?q=Liên+Quân") == "q=liên quân"


def test_only_that_video_blocked():
    cfg = {"web_port": 5000, "blocked_pages": RULES, "youtube_mode": "allow"}
    assert phan_loai_truy_cap_web("https://www.youtube.com/watch?v=abc123", "x - YouTube", cfg)["should_block"]
    assert not phan_loai_truy_cap_web("https://www.youtube.com/watch?v=lesson1", "Toán 8 bài 3 - YouTube", cfg)["should_block"]


def test_title_match_without_url_needs_long_title():
    assert match_blocked_page("", EDGE, RULES)
    assert not match_blocked_page("", "youtube", [{"d": "youtube.com", "t": "youtube", "u": ""}])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
