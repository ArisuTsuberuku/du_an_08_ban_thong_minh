# -*- coding: utf-8 -*-
"""
Bộ ghi nhận hoạt động máy tính (services/activity_tracker.py) — port từ DuAnAI/tracker.py, đã gia cố riêng tư.

- Mỗi 3s đọc cửa sổ đang dùng (tên .exe + tiêu đề) bằng Win32 API qua ctypes (không cần pywin32).
- Không thao tác chuột/phím > 3 phút => coi như rời máy, ngừng đếm.
- Tiêu đề chứa từ khóa nhạy cảm (ẩn danh, mật khẩu, ngân hàng...) => chỉ ghi "Nội dung riêng tư".
- Cứ ~60s gom thành 1 lô {exe, title, seconds} để cloud_agent gửi kèm heartbeat
  (cloud phân loại Học tập / Giải trí / Khác bằng Gemini; khóa Gemini chỉ nằm trên server).
- Mỗi ~12s chụp DANH SÁCH CỬA SỔ ĐANG MỞ (ứng dụng + tab trình duyệt đọc bằng UI Automation) cho thẻ
  "Đang dùng gì" của phụ huynh; cửa sổ đang focus được đánh dấu.
- KHÔNG chụp màn hình, KHÔNG đọc lịch sử trình duyệt (khác bản DuAnAI).
"""

import ctypes
import os
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

POLL_S = 3.0
IDLE_LIMIT_S = 180.0
PRIVATE_KEYWORDS = ("incognito", "inprivate", "ẩn danh", "password", "mật khẩu", "ngân hàng", "banking",
                    "đăng nhập", "sign in", "login")
TITLE_MAX = 120
OPEN_SCAN_S = 12.0
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "browser.exe", "coccoc.exe", "opera.exe"}
SKIP_EXES = {"textinputhost.exe", "applicationframehost.exe", "shellexperiencehost.exe", "searchhost.exe",
             "startmenuexperiencehost.exe", "systemsettings.exe", "lockapp.exe"}


def _private(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in PRIVATE_KEYWORDS)


def _browser_tabs(hwnd: int, limit: int = 15) -> List[str]:
    """Tên các tab của 1 cửa sổ trình duyệt (Chrome/Edge/Cốc Cốc/Brave: TabItemControl). Lỗi -> []."""
    try:
        import uiautomation as auto

        win = auto.ControlFromHandle(hwnd)
        names: List[str] = []

        def walk(ctrl, depth: int) -> None:
            if depth > 14 or len(names) >= limit:
                return
            for ch in ctrl.GetChildren():
                if ch.ControlTypeName == "TabItemControl":
                    nm = (ch.Name or "").strip()
                    if nm:
                        names.append("Nội dung riêng tư" if _private(nm) else nm[:TITLE_MAX])
                elif ch.ControlTypeName in ("PaneControl", "GroupControl", "TabControl", "CustomControl",
                                            "ToolBarControl", "WindowControl"):
                    walk(ch, depth + 1)   # không đi vào DocumentControl (nội dung trang web) cho nhanh
                if len(names) >= limit:
                    return

        walk(win, 0)
        return names
    except Exception:
        return []


def list_open_windows(with_tabs: bool = True) -> List[Dict]:
    """Cửa sổ ứng dụng đang mở (hiện trên taskbar), gom theo .exe; trình duyệt kèm danh sách tab."""
    if not sys.platform.startswith("win"):
        return []
    import psutil
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    found: List[Dict] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):
                return True
            ex = user32.GetWindowLongW(hwnd, -20)
            if ex & 0x80:            # WS_EX_TOOLWINDOW
                return True
            cloaked = ctypes.c_int(0)
            ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
            if cloaked.value:
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = psutil.Process(pid.value).name()
            if exe.lower() in SKIP_EXES or buf.value in ("Program Manager",):
                return True
            found.append({"hwnd": int(hwnd), "exe": exe, "title": buf.value.strip(), "focused": hwnd == fg})
        except Exception:
            pass
        return True

    user32.EnumWindows(proto(cb), 0)
    apps: Dict[str, Dict] = {}
    for w in found:
        a = apps.setdefault(w["exe"].lower(), {"exe": w["exe"], "windows": [], "tabs": [], "focused": False})
        title = "Nội dung riêng tư (không ghi)" if _private(w["title"]) else w["title"][:TITLE_MAX]
        a["windows"].append(title)
        a["focused"] = a["focused"] or w["focused"]
        if with_tabs and w["exe"].lower() in BROWSERS and len(a["tabs"]) < 25:
            a["tabs"] += _browser_tabs(w["hwnd"])
    out = sorted(apps.values(), key=lambda a: (not a["focused"], a["exe"].lower()))
    for a in out:
        a["windows"] = a["windows"][:6]
        a["tabs"] = list(dict.fromkeys(a["tabs"]))[:25]
    return out[:15]


def _foreground_window() -> Tuple[Optional[str], str]:
    if not sys.platform.startswith("win"):
        return None, ""
    try:
        import psutil

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None, ""
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        exe = psutil.Process(pid.value).name() if pid.value > 0 else None
        return exe, buf.value.strip()
    except Exception:
        return None, ""


def _idle_seconds() -> float:
    if not sys.platform.startswith("win"):
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0
    except Exception:
        return 0.0


class ActivityTracker:
    def __init__(self, batch_s: float = 60.0):
        self.batch_s = batch_s
        self._buf: Dict[Tuple[str, str], float] = {}
        self._period_start = time.time()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.self_names = {os.path.basename(sys.executable).lower(), "smartdesklauncher.exe"}
        self.current: Dict[str, str] = {}   # cửa sổ đang dùng ngay lúc này (cho thẻ trạng thái phụ huynh)
        self.open_apps: List[Dict] = []      # các ứng dụng / tab đang mở (quét mỗi ~12s)
        self._last_open_scan = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ActivityTrackerThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _scan_open(self) -> None:
        try:
            self.open_apps = list_open_windows(with_tabs=True)
        except Exception:
            pass

    def _loop(self) -> None:
        com = None
        try:
            import uiautomation as auto  # COM cho UI Automation trong luồng này (đọc tên tab trình duyệt)

            com = auto.UIAutomationInitializerInThread()
        except Exception:
            com = None
        try:
            self._loop_inner()
        finally:
            if com is not None:
                try:
                    com.Uninitialize()
                except Exception:
                    pass

    def _loop_inner(self) -> None:
        while not self._stop.is_set():
            if time.time() - self._last_open_scan >= OPEN_SCAN_S:
                self._last_open_scan = time.time()
                self._scan_open()
            if _idle_seconds() >= IDLE_LIMIT_S:
                self.current = {"exe": "", "title": "Rời máy (không thao tác > 3 phút)", "t": int(time.time()), "idle": "1"}
            if _idle_seconds() < IDLE_LIMIT_S:
                exe, title = _foreground_window()
                if exe:
                    low = f"{exe} {title}".lower()
                    if any(k in low for k in PRIVATE_KEYWORDS):
                        title = "Nội dung riêng tư (không ghi)"
                    key = (exe, (title or exe)[:TITLE_MAX])
                    self.current = {"exe": exe, "title": key[1], "t": int(time.time())}
                    with self._lock:
                        self._buf[key] = self._buf.get(key, 0.0) + POLL_S
            self._stop.wait(POLL_S)

    def drain_if_due(self, force: bool = False) -> Optional[Dict]:
        """Trả về 1 lô hoạt động khi đã đủ batch_s giây (hoặc force), rồi xóa bộ đệm."""
        now = time.time()
        with self._lock:
            if not self._buf or (not force and now - self._period_start < self.batch_s):
                return None
            items: List[Dict] = [{"exe": k[0], "title": k[1], "seconds": int(v)}
                                 for k, v in sorted(self._buf.items(), key=lambda kv: -kv[1])][:25]
            batch = {"start": int(self._period_start), "end": int(now), "items": items}
            self._buf.clear()
            self._period_start = now
        return batch

    def requeue(self, batch: Dict) -> None:
        """Gửi lỗi -> trả lô về bộ đệm để lần sau gửi lại (không mất số liệu)."""
        with self._lock:
            for it in batch.get("items", []):
                key = (it["exe"], it["title"])
                self._buf[key] = self._buf.get(key, 0.0) + it["seconds"]
            self._period_start = min(self._period_start, batch.get("start", self._period_start))
