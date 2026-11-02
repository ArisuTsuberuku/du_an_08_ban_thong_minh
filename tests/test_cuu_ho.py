# -*- coding: utf-8 -*-
"""Kiểm thử phím cứu hộ + PIN cứu hộ (python tests/test_cuu_ho.py). Chạy được cả ngoài Windows."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not sys.platform.startswith("win"):
    sys.modules.setdefault("winreg", types.ModuleType("winreg"))
from core import failsafe as fs  # noqa: E402

CFG = {}
fs.load_config = lambda: dict(CFG)
import core.config_manager as cm  # noqa: E402

cm.save_config = lambda c: (CFG.clear(), CFG.update(c))


def test_parse_hotkey():
    assert fs.parse_hotkey("ctrl+alt+shift+f12")[2] == "Ctrl+Alt+Shift+F12"
    assert fs.parse_hotkey("Shift+Ctrl+K")[2] == "Ctrl+Shift+K"
    for bad in ("Alt+F4", "F12", "Ctrl+Ctrl+F1", "Ctrl+Alt+Del", "Win+Ctrl+F1", ""):
        assert fs.parse_hotkey(bad) is None, bad


def test_default_hotkey_when_unset_or_invalid():
    CFG.clear()
    assert fs.get_rescue_hotkey() == fs.RESCUE_HOTKEY_DEFAULT
    CFG["rescue_hotkey"] = "Alt+F4"
    assert fs.get_rescue_hotkey() == fs.RESCUE_HOTKEY_DEFAULT
    CFG["rescue_hotkey"] = "Ctrl+Shift+F9"
    assert fs.get_rescue_hotkey() == "Ctrl+Shift+F9"


def test_pin_default_then_custom_hashed():
    CFG.clear()
    assert fs.rescue_pin_is_default() and fs.verify_rescue_pin("999999")
    fs.set_rescue_pin("246810")
    assert "rescue_pin_hash" in CFG and "246810" not in str(CFG), "PIN chỉ được lưu dạng băm"
    assert fs.verify_rescue_pin("246810") and not fs.verify_rescue_pin("999999"), "đổi PIN rồi thì PIN mặc định hết hiệu lực"
    assert not fs.rescue_pin_is_default()


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                fails += 1
                print("FAIL", name, exc)
    sys.exit(1 if fails else 0)
