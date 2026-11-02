# -*- coding: utf-8 -*-
"""Kiểm thử nhận diện tiến trình phụ của FocusDesk khi thoát (chỉ tắt cloudflared / AI camera, không đụng app khác)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services import app_shutdown  # noqa: E402


class P:
    def __init__(self, name, cmd):
        self._n, self._c = name, cmd

    def name(self):
        return self._n

    def cmdline(self):
        return self._c


def test_helpers_detected():
    assert app_shutdown._is_helper(P("cloudflared.exe", ["cf", "tunnel", "--no-autoupdate", "--url",
                                                         "http://127.0.0.1:5000"]), 5000)
    assert app_shutdown._is_helper(P("pythonw.exe", ["pythonw", r"C:\FocusDesk\posture_ai_engine.py", "--daemon"]), 5000)


def test_others_untouched():
    assert not app_shutdown._is_helper(P("cloudflared.exe", ["cloudflared", "service", "run"]), 5000)  # tunnel riêng
    assert not app_shutdown._is_helper(P("pythonw.exe", ["pythonw", "-m", "idlelib"]), 5000)          # IDLE học sinh
    assert not app_shutdown._is_helper(P("winword.exe", ["winword"]), 5000)


def test_adopt_noop_without_windows():
    assert app_shutdown.adopt(None) is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
