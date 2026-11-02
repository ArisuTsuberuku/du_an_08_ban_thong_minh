# -*- coding: utf-8 -*-
"""Kiểm thử giới hạn thư mục Explorer của kiosk (chạy được trên Linux: so đường dẫn theo kiểu Windows)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.kiosk_guard import path_in_allowed  # noqa: E402

ROOTS = [r"D:\TaiLieuHocTap", r"C:\Users\Public\Documents\\"]


def test_inside_and_root():
    assert path_in_allowed(r"D:\TaiLieuHocTap", ROOTS)
    assert path_in_allowed(r"d:\tailieuhoctap\Toan\Bai 1", ROOTS)
    assert path_in_allowed(r"C:\Users\Public\Documents", ROOTS)


def test_outside():
    for p in (r"D:\\", r"D:\TaiLieuHocTapGame", r"D:\TaiLieuHocTap\..\Games", r"C:\Users\Public",
              r"C:\Windows\System32", "", "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}", "shell:Downloads",
              "search-ms:query=abc", "Documents"):
        assert not path_in_allowed(p, ROOTS), p


def test_no_roots():
    assert not path_in_allowed(r"D:\TaiLieuHocTap", [])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
