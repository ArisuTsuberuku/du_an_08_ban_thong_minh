# -*- coding: utf-8 -*-
"""
Lấy icon THẬT của ứng dụng (.exe) để hiển thị trên giao diện Học sinh / Phụ huynh (Tkinter).

- Windows + pywin32 + Pillow: trích icon 32x32 từ file .exe, vẽ trên nền trắng, phóng 40px, cache theo đường dẫn.
- Không trích được (thiếu thư viện / file không tồn tại) -> trả None, giao diện dùng emoji theo loại ứng dụng.
"""

import os
import sys
from typing import Dict, Optional

_cache: Dict[str, object] = {}

EMOJI_BY_KEYWORD = (
    (("chrome", "msedge", "firefox", "brave", "coccoc", "opera"), "🌐"),
    (("winword", "word", "docs"), "📝"),
    (("excel", "sheets"), "📊"),
    (("powerpnt", "powerpoint", "slides"), "📽️"),
    (("onenote", "notepad", "note"), "🗒️"),
    (("calc",), "🧮"),
    (("zoom", "teams", "classin", "meet"), "🎥"),
    (("acrobat", "acrord", "pdf", "foxit", "sumatra"), "📕"),
    (("code", "pycharm", "python", "scratch", "thonny"), "💻"),
    (("geogebra", "desmos", "math"), "📐"),
    (("paint", "mspaint", "krita", "photoshop"), "🎨"),
    (("dict", "tudien", "lingo", "duolingo"), "📖"),
    (("explorer",), "📁"),
)


def emoji_for(exe_or_name: str) -> str:
    low = (exe_or_name or "").lower()
    for keys, emo in EMOJI_BY_KEYWORD:
        if any(k in low for k in keys):
            return emo
    return "🖥️"


def _extract_png(path: str, size: int = 40):
    import win32api
    import win32con
    import win32gui
    import win32ui
    from PIL import Image

    large, small = win32gui.ExtractIconEx(path, 0, 1)
    icons = list(large) + list(small)
    if not icons:
        return None
    hicon = icons[0]
    try:
        ico = win32api.GetSystemMetrics(win32con.SM_CXICON) or 32
        hdc_screen = win32gui.GetDC(0)
        hdc = win32ui.CreateDCFromHandle(hdc_screen)
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(hdc, ico, ico)
        mem = hdc.CreateCompatibleDC()
        mem.SelectObject(bmp)
        mem.FillSolidRect((0, 0, ico, ico), 0xFFFFFF)  # nền trắng khớp thẻ ứng dụng
        win32gui.DrawIconEx(mem.GetSafeHdc(), 0, 0, hicon, ico, ico, 0, None, win32con.DI_NORMAL)
        bits = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (ico, ico), bits, "raw", "BGRX", 0, 1)
        mem.DeleteDC()
        hdc.DeleteDC()
        win32gui.ReleaseDC(0, hdc_screen)
        win32gui.DeleteObject(bmp.GetHandle())
        return img.resize((size, size), Image.LANCZOS)
    finally:
        for h in icons:
            try:
                win32gui.DestroyIcon(h)
            except Exception:
                pass


def get_icon_image(path: str, size: int = 40):
    """Trả về ImageTk.PhotoImage (giữ tham chiếu trong cache) hoặc None."""
    key = f"{path}|{size}"
    if key in _cache:
        return _cache[key]
    img_tk = None
    if sys.platform.startswith("win") and path and os.path.isfile(path):
        try:
            from PIL import ImageTk

            img = _extract_png(path, size)
            if img is not None:
                img_tk = ImageTk.PhotoImage(img)
        except Exception:
            img_tk = None
    _cache[key] = img_tk
    return img_tk
