# -*- coding: utf-8 -*-
"""Kiểm thử nhận diện 'nhờ AI làm game rồi chơi' và giới hạn AI (không cần Windows)."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for m in ("pyautogui", "pygetwindow", "winreg"):   # chạy được cả trên máy test không phải Windows
    sys.modules.setdefault(m, types.ModuleType(m))
from services.giam_sat_worker import ai_game_reason, phan_loai_truy_cap_web  # noqa: E402

CFG = {"web_port": 5000}


def test_ai_chat_normal_allowed():
    v = phan_loai_truy_cap_web("https://gemini.google.com/app/abc", "Giải phương trình bậc hai - Gemini", CFG)
    assert v["is_ai"] and not v["should_block"] and not v.get("ai_game")


def test_ai_chat_about_game_blocked():
    v = phan_loai_truy_cap_web("https://gemini.google.com/app/abc", "Tạo trò chơi rắn săn mồi - Gemini", CFG)
    assert v["is_ai"] and v["should_block"] and v.get("ai_game")
    v = phan_loai_truy_cap_web("https://chatgpt.com/c/1", "Snake Game in HTML", CFG)
    assert v["should_block"] and v.get("ai_game")


def test_game_theory_not_blocked():
    v = phan_loai_truy_cap_web("https://chatgpt.com/c/1", "Game theory homework", CFG)
    assert v["is_ai"] and not v["should_block"]


def test_sandbox_domain_is_game():
    v = phan_loai_truy_cap_web("https://abc123.scf.usercontent.goog/index.html", "Untitled", CFG)
    assert v["should_block"] and v.get("ai_game") and not v["is_ai"]
    assert ai_game_reason("https://xyz.claudeusercontent.com/", "", CFG)


def test_warn_and_off_modes():
    v = phan_loai_truy_cap_web("https://gemini.google.com/app", "Tetris - Gemini", {**CFG, "ai_game_action": "warn"})
    assert v.get("ai_game") and not v["should_block"]
    v = phan_loai_truy_cap_web("https://gemini.google.com/app", "Tetris - Gemini", {**CFG, "ai_game_action": "off"})
    assert not v.get("ai_game") and not v["should_block"]


def test_safe_keywords():
    cfg = {**CFG, "safe_keywords": ["bài giảng", "olm.vn"], "bad_tabs": ["game", "phim"]}
    assert not phan_loai_truy_cap_web("https://gemini.google.com/app", "Bài giảng tạo game giáo dục - Gemini", cfg)["should_block"]
    assert not phan_loai_truy_cap_web("https://www.youtube.com/watch?v=1", "Phim tài liệu bài giảng lịch sử - YouTube", cfg)["should_block"]
    assert not phan_loai_truy_cap_web("https://olm.vn/game-toan", "Game toán lớp 3", cfg)["should_block"]
    assert phan_loai_truy_cap_web("https://www.youtube.com/watch?v=2", "Phim hành động - YouTube", cfg)["should_block"]
    cfg["bad_urls"] = ["olm.vn"]                         # phụ huynh chặn tay -> vẫn chặn
    assert phan_loai_truy_cap_web("https://olm.vn/x", "Olm", cfg)["should_block"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
