# -*- coding: utf-8 -*-
"""Kiểm thử: tắt FocusDesk phải trả file hosts về ĐÚNG nguyên bản (giả lập Windows bằng file tạm)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services import hosts_blocker as hb  # noqa: E402

ORIG = b"# Copyright (c) Microsoft\r\n127.0.0.1 my.dev.local\r\n# cuoi file khong xuong dong"


def _setup():
    d = tempfile.mkdtemp()
    hb.HOSTS_PATH = os.path.join(d, "hosts")
    hb._backup_path = lambda: os.path.join(d, "hosts_goc.bak")
    hb._ai_lock_domains = lambda: []
    hb.subprocess.run = lambda *a, **k: None
    hb.sys.platform = "win32"
    with open(hb.HOSTS_PATH, "wb") as fh:
        fh.write(ORIG)


def test_restore_exact_original():
    _setup()
    ok, _ = hb.apply_hosts_block(["poki.com", "gemini.google.com", "google.com"])
    data = open(hb.HOSTS_PATH, "rb").read()
    assert ok and b"0.0.0.0 poki.com" in data and b"gemini.google.com" in data
    assert b"0.0.0.0 google.com" not in data            # miền hệ thống vẫn không bị chặn
    hb.clear_hosts_block()
    assert open(hb.HOSTS_PATH, "rb").read() == ORIG    # y hệt từng byte
    assert not os.path.exists(hb._backup_path())


def test_user_edit_kept():
    _setup()
    hb.apply_hosts_block(["poki.com"])
    with open(hb.HOSTS_PATH, "ab") as fh:
        fh.write(b"10.0.0.5 nas.local\n")
    hb.clear_hosts_block()
    data = open(hb.HOSTS_PATH, "rb").read()
    assert b"nas.local" in data and b"FOCUSDESK" not in data


def test_only_student_mode_blocks():
    _setup()
    cfg = {"bad_urls": ["poki.com"], "dns_block_enabled": True}
    hb.sync_hosts_for_role("student", cfg)
    assert b"poki.com" in open(hb.HOSTS_PATH, "rb").read()
    for role in ("parent", "admin", "locked"):
        hb.sync_hosts_for_role("student", cfg)
        hb.sync_hosts_for_role(role, cfg)
        assert open(hb.HOSTS_PATH, "rb").read() == ORIG, role
    hb.sync_hosts_for_role("locked", {**cfg, "monitor_always": True})
    assert b"poki.com" in open(hb.HOSTS_PATH, "rb").read()
    hb.clear_hosts_block()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
