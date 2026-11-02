# -*- coding: utf-8 -*-
"""
Gia cố chế độ Kiosk cho Học sinh (services/kiosk_guard.py)

Vấn đề: học sinh mở 1 ứng dụng được phép -> thanh Taskbar Windows hiện lên -> bấm Start / Win+E / Win+R
hoặc chuột vào taskbar để mở mọi thứ khác trên máy.

3 lớp (chỉ bật ở màn hình Khóa & chế độ Học sinh, tự gỡ khi sang Phụ huynh/Admin hoặc thoát khẩn cấp):
  1. ẨN TASKBAR: ShowWindow(Shell_TrayWnd + Shell_SecondaryTrayWnd, SW_HIDE), nhắc lại mỗi 1s (Explorer hay tự hiện).
     Không kill explorer.exe -> không mất khay hệ thống, khôi phục tức thì.
  2. CHẶN PHÍM HỆ THỐNG: hook bàn phím mức thấp (WH_KEYBOARD_LL) nuốt phím Windows (Start, Win+D/E/R/Tab...),
     Ctrl+Esc, Alt+Esc. KHÔNG chặn phím cứu hộ do Admin đặt (RegisterHotKey vẫn chạy).
  3. CANH CỬA SỔ: cửa sổ đang được focus mà KHÔNG thuộc ứng dụng được phép (hoặc là Start menu/Search/Desktop)
     -> thu nhỏ ngay + trả màn hình Góc học tập lên trước + báo vi phạm (mỗi ứng dụng 1 lần/60s).
     Ứng dụng UWP (Máy tính...) chạy trong ApplicationFrameHost -> dò tiến trình thật qua cửa sổ con.
  4. GIỚI HẠN THƯ MỤC: cửa sổ File Explorer chỉ được ở trong các "Thư mục tài liệu" Admin cho phép. Mỗi 0,3s đọc
     đường dẫn thật của từng cửa sổ/tab Explorer (COM Shell.Application) -> đi ra ngoài (lên ổ C:, This PC, gõ địa chỉ,
     cây thư mục bên trái...) thì kéo về thư mục được phép gần nhất + báo phụ huynh. Không có thư mục nào -> cấm Explorer.

Mọi API Win32 đều qua ctypes; trên hệ điều hành khác các hàm là no-op (để kiểm thử trên Linux).
"""

import ctypes
import ntpath
import os
import sys
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

IS_WINDOWS = sys.platform.startswith("win")

# Tiến trình hệ thống luôn được phép có cửa sổ focus (hộp thoại UAC, IME, chuyển ngôn ngữ...)
# Công cụ có sẵn của Windows (không phải ứng dụng .exe riêng) vẫn cho dùng khi khóa máy — xem WIN_TOOL_COMBOS
WIN_TOOLS_OK = {"screenclippinghost.exe", "snippingtool.exe", "screensketch.exe"}
SYSTEM_OK = {"textinputhost.exe", "ctfmon.exe", "consent.exe", "lockapp.exe", "logonui.exe",
             "credentialuibroker.exe", "applicationframehost.exe", "dwm.exe"}
# Cửa sổ "lối thoát" phải đóng ngay
SHELL_ESCAPES = {"startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe", "shellexperiencehost.exe",
                 "searchui.exe", "widgets.exe", "msedgewebview2.exe", "copilot.exe"}
DESKTOP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

_state = {"active": False, "allowed": set(), "own_pids": set(), "on_violation": None, "on_refocus": None,
          "allow_explorer_windows": True, "allowed_folders": [], "allow_win_tools": True}
_lock = threading.Lock()
_threads: Dict[str, threading.Thread] = {}
_stop = threading.Event()
_hook_thread_id = [0]
_last_violation: Dict[str, float] = {}
# Đếm phím (thời điểm, có phải phím điều khiển game) để nhận ra học sinh đang CHƠI game trong khung AI
from collections import deque  # noqa: E402

_GAME_VK = {0x25, 0x26, 0x27, 0x28, 0x20, 0x57, 0x41, 0x53, 0x44}   # ← ↑ → ↓ Space W A S D
_keys: "deque" = deque(maxlen=2000)


def key_stats(window_s: float = 60.0) -> Tuple[int, int]:
    """(tổng phím, phím game) trong `window_s` giây qua. Chỉ có số liệu khi kiosk (hook bàn phím) đang bật."""
    cut = time.time() - window_s
    tot = gamey = 0
    for t, g in list(_keys):
        if t >= cut:
            tot += 1
            gamey += g
    return tot, gamey


def reset_key_stats() -> None:
    _keys.clear()

if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]

WH_KEYBOARD_LL = 13
VK_SHIFT, VK_SPACE, VK_SNAPSHOT, VK_V, VK_S, VK_OEM_1, VK_OEM_PERIOD = 0x10, 0x20, 0x2C, 0x56, 0x53, 0xBA, 0xBE
MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
LLKHF_INJECTED = 0x10
# Phím Windows được phép khi khóa máy (công cụ có sẵn của Windows, không mở được ứng dụng / Start / Explorer):
#   Win+Shift+S chụp vùng màn hình · Win+PrtSc chụp cả màn hình (lưu Pictures\Screenshots) · Win+V lịch sử clipboard
#   Win+. / Win+; bảng emoji, ký hiệu · Win+Space đổi bàn phím / ngôn ngữ nhập
WIN_TOOL_COMBOS = "Win+Shift+S, Win+PrtSc, Win+V, Win+. , Win+Space"
_win = {"down": False, "injecting": 0}


def _win_combo_allowed(vk: int) -> bool:
    if vk in MODIFIER_VKS or vk in (VK_SNAPSHOT, VK_V, VK_OEM_1, VK_OEM_PERIOD, VK_SPACE):
        return True
    return vk == VK_S and bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)


def _release_win_without_start(vk: int) -> None:
    """Thả phím Windows mà KHÔNG mở Start: chèn 1 phím 'rỗng' (0xFF) trước khi thả Win (mẹo chuẩn của Windows)."""
    _win["injecting"] += 3
    user32.keybd_event(0xFF, 0, 0, 0)
    user32.keybd_event(0xFF, 0, 2, 0)
    user32.keybd_event(vk, 0, 2 | 1, 0)        # KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY
WM_KEYDOWN, WM_SYSKEYDOWN, WM_QUIT = 0x0100, 0x0104, 0x0012
VK_LWIN, VK_RWIN, VK_ESCAPE, VK_TAB, VK_CONTROL, VK_MENU, VK_F4 = 0x5B, 0x5C, 0x1B, 0x09, 0x11, 0x12, 0x73
SW_HIDE, SW_SHOWNA, SW_MINIMIZE = 0, 8, 6


# ----------------------------------------------------------------------------- tiện ích cửa sổ
def _pid_of(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _class_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _title_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _exe_of_pid(pid: int) -> str:
    try:
        import psutil

        return (psutil.Process(pid).name() or "").lower()
    except Exception:
        return ""


def real_process_of_window(hwnd) -> Tuple[int, str]:
    """(pid, exe) thật của cửa sổ; UWP trong ApplicationFrameHost -> lấy tiến trình của cửa sổ con CoreWindow."""
    pid = _pid_of(hwnd)
    exe = _exe_of_pid(pid)
    if exe == "applicationframehost.exe":
        found: List[int] = []

        def _cb(child, _lp):
            cp = _pid_of(child)
            if cp and cp != pid:
                found.append(cp)
                return False
            return True

        user32.EnumChildWindows(hwnd, WNDENUMPROC(_cb), 0)
        if found:
            return found[0], _exe_of_pid(found[0])
    return pid, exe


def _taskbar_windows() -> List[int]:
    out = []
    h = user32.FindWindowW("Shell_TrayWnd", None)
    if h:
        out.append(h)
    h2 = None
    while True:
        h2 = user32.FindWindowExW(None, h2, "Shell_SecondaryTrayWnd", None)
        if not h2:
            break
        out.append(h2)
    return out


def set_taskbar_visible(visible: bool) -> None:
    if not IS_WINDOWS:
        return
    try:
        for h in _taskbar_windows():
            user32.ShowWindow(h, SW_SHOWNA if visible else SW_HIDE)
    except Exception:
        pass


def get_work_area() -> Tuple[int, int, int, int]:
    """(left, top, right, bottom) vùng làm việc (trừ chỗ taskbar chiếm) — để đặt thanh dock học sinh đúng chỗ."""
    if not IS_WINDOWS:
        return (0, 0, 0, 0)

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

    rc = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0)  # SPI_GETWORKAREA
    return rc.l, rc.t, rc.r, rc.b


def focus_window(hwnd: int) -> None:
    """Đưa 1 cửa sổ lên trước (mẹo ALT để vượt giới hạn SetForegroundWindow của Windows)."""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def list_app_windows(allowed_exes: Iterable[str]) -> List[Dict[str, object]]:
    """Cửa sổ đang mở (hiện, có tiêu đề) của các ứng dụng được phép -> cho thanh dock học sinh."""
    if not IS_WINDOWS:
        return []
    allowed = {a.lower() for a in allowed_exes}
    out: List[Dict[str, object]] = []

    def _cb(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER -> bỏ hộp thoại con
                return True
            title = _title_of(hwnd)
            if not title or _class_of(hwnd) in DESKTOP_CLASSES:
                return True
            if _is_cloaked(hwnd):
                return True
            _, exe = real_process_of_window(hwnd)
            if exe in allowed or (exe == "explorer.exe" and _class_of(hwnd) == "CabinetWClass"):
                out.append({"hwnd": int(hwnd), "exe": exe, "title": title})
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def _is_cloaked(hwnd) -> bool:
    try:
        val = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(val), ctypes.sizeof(val))  # DWMWA_CLOAKED
        return val.value != 0
    except Exception:
        return False


# ----------------------------------------------------------------------------- lớp 2: hook bàn phím
def _keyboard_thread() -> None:
    def _proc(n_code, w_param, l_param):
        try:
            if n_code == 0 and _state["active"] and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN, 0x0101, 0x0105):
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = kb.vkCode
                if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    _keys.append((time.time(), 1 if vk in _GAME_VK else 0))
                ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                alt = bool(kb.flags & 0x20)
                key_up = w_param in (0x0101, 0x0105)
                if kb.flags & LLKHF_INJECTED and _win["injecting"] > 0:
                    _win["injecting"] -= 1                 # phím do chính FocusDesk chèn -> cho qua
                    return user32.CallNextHookEx(None, n_code, w_param, l_param)
                if vk in (VK_LWIN, VK_RWIN):
                    if not _state.get("allow_win_tools", True):
                        return 1                           # chặn hẳn phím Windows
                    if not key_up:
                        _win["down"] = True                # cho Windows biết Win đang giữ -> tổ hợp được phép chạy
                        return user32.CallNextHookEx(None, n_code, w_param, l_param)
                    _win["down"] = False
                    _release_win_without_start(vk)         # nuốt lần thả thật, thả lại kèm phím rỗng -> không mở Start
                    return 1
                if _win["down"] and not _win_combo_allowed(vk):
                    return 1                               # Win+R, Win+E, Win+D, Win+Tab, Win+S… -> nuốt phím
                if vk == VK_ESCAPE and (ctrl or alt):
                    return 1                       # Ctrl+Esc (Start), Alt+Esc
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    cb = HOOKPROC(_proc)
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, cb, kernel32.GetModuleHandleW(None), 0)
    _hook_thread_id[0] = kernel32.GetCurrentThreadId()
    if not hook:
        return
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnhookWindowsHookEx(hook)


# ----------------------------------------------------------------------------- lớp 1 + 3: canh cửa sổ
def _is_allowed_window(hwnd) -> Tuple[bool, str, str]:
    cls = _class_of(hwnd)
    pid, exe = real_process_of_window(hwnd)
    if pid in _state["own_pids"]:
        return True, exe, cls
    if cls in DESKTOP_CLASSES or exe in SHELL_ESCAPES:
        return False, exe or cls, cls
    if not exe or exe in SYSTEM_OK:
        return True, exe, cls
    if exe in WIN_TOOLS_OK and _state.get("allow_win_tools", True):
        return True, exe, cls     # khung chụp màn hình / Snipping Tool có sẵn của Windows
    if exe in _state["allowed"]:
        return True, exe, cls
    if exe == "explorer.exe" and cls == "CabinetWClass" and _state["allow_explorer_windows"] \
            and _state["allowed_folders"] and _folder_com_ok[0]:
        return True, exe, cls     # cửa sổ thư mục tài liệu; lớp 4 giữ nó trong thư mục được phép
    if cls == "#32770" and exe == "explorer.exe":
        return True, exe, cls     # hộp thoại hệ thống (sao chép file...)
    return False, exe, cls


def _watch_thread() -> None:
    tick = 0
    while not _stop.is_set():
        try:
            if _state["active"]:
                if tick % 4 == 0:
                    set_taskbar_visible(False)
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    ok, exe, cls = _is_allowed_window(hwnd)
                    if not ok:
                        if cls not in DESKTOP_CLASSES:
                            user32.ShowWindow(hwnd, SW_MINIMIZE)
                        cb = _state.get("on_refocus")
                        if cb:
                            cb()
                        now = time.time()
                        if exe and exe not in SHELL_ESCAPES and cls not in DESKTOP_CLASSES \
                                and now - _last_violation.get(exe, 0) > 60:
                            _last_violation[exe] = now
                            vcb = _state.get("on_violation")
                            if vcb:
                                try:
                                    vcb(exe, _title_of(hwnd))
                                except Exception:
                                    pass
        except Exception:
            pass
        tick += 1
        _stop.wait(0.25)


# ----------------------------------------------------------------------------- lớp 4: giới hạn thư mục Explorer
def _norm_dir(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path)).rstrip("\\")


def path_in_allowed(path: str, roots: Iterable[str]) -> bool:
    """True nếu `path` là một thư mục được phép hoặc nằm bên trong nó (so theo kiểu Windows, không phân biệt hoa thường).
    Đường dẫn ảo (This PC '::{…}', thư viện, kết quả tìm kiếm, mạng 'search-ms:', 'shell:'…) luôn bị coi là ngoài."""
    if not path or path.startswith("::") or (":" not in path[:3] and not path.startswith("\\\\")):
        return False
    if "search-ms:" in path.lower() or path.lower().startswith("shell:"):
        return False
    p = _norm_dir(path)
    for r in roots:
        if not r:
            continue
        rn = _norm_dir(r)
        if p == rn or p.startswith(rn + "\\"):
            return True
    return False


def _explorer_path(win) -> str:
    """Đường dẫn thư mục đang mở trong 1 cửa sổ/tab Explorer (COM InternetExplorer/ShellBrowserWindow)."""
    try:
        return str(win.Document.Folder.Self.Path or "")
    except Exception:
        pass
    try:
        url = str(win.LocationURL or "")
        if url.lower().startswith("file:///"):
            from urllib.parse import unquote

            return unquote(url[8:]).replace("/", "\\")
    except Exception:
        pass
    return ""


_last_ok_path: Dict[Tuple[int, int], str] = {}
_folder_com_ok = [True]


def _folder_thread() -> None:
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore

        pythoncom.CoInitialize()
        shell = win32com.client.Dispatch("Shell.Application")
    except Exception:
        _folder_com_ok[0] = False      # không đọc được đường dẫn -> lớp 3 coi mọi cửa sổ Explorer là vi phạm
        return
    while not _stop.is_set():
        try:
            roots = list(_state["allowed_folders"])
            if _state["active"] and roots:
                wins = shell.Windows()
                for i in range(wins.Count):
                    try:
                        w = wins.Item(i)
                        if w is None or not str(w.FullName or "").lower().endswith("explorer.exe"):
                            continue
                        key = (int(w.HWND), i)
                        path = _explorer_path(w)
                        if path_in_allowed(path, roots):
                            _last_ok_path[key] = path
                            continue
                        back = _last_ok_path.get(key) or roots[0]
                        try:
                            os.makedirs(back, exist_ok=True)
                        except Exception:
                            pass
                        w.Navigate2(back)
                        now = time.time()
                        vkey = "folder:" + (path or "?").lower()
                        if now - _last_violation.get(vkey, 0) > 60:
                            _last_violation[vkey] = now
                            vcb = _state.get("on_violation")
                            if vcb:
                                try:
                                    vcb("explorer.exe", f"thư mục ngoài danh sách: {path or 'không rõ'}")
                                except Exception:
                                    pass
                    except Exception:
                        continue
        except Exception:
            pass
        _stop.wait(0.3)
    try:
        pythoncom.CoUninitialize()
    except Exception:
        pass


# ----------------------------------------------------------------------------- API
def start_kiosk(allowed_exes: Iterable[str], on_violation: Optional[Callable[[str, str], None]] = None,
                on_refocus: Optional[Callable[[], None]] = None, allow_explorer_windows: bool = True,
                allowed_folders: Iterable[str] = (), allow_win_tools: bool = True) -> None:
    """Bật (hoặc cập nhật danh sách) kiosk. Gọi lại nhiều lần an toàn."""
    with _lock:
        _state.update(active=True, allowed={a.lower() for a in allowed_exes if a},
                      own_pids={os.getpid()} | _child_pids(), on_violation=on_violation, on_refocus=on_refocus,
                      allow_explorer_windows=allow_explorer_windows,
                      allowed_folders=[str(f) for f in allowed_folders if f], allow_win_tools=bool(allow_win_tools))
        if not IS_WINDOWS:
            return
        _stop.clear()
        if "kb" not in _threads or not _threads["kb"].is_alive():
            _threads["kb"] = threading.Thread(target=_keyboard_thread, name="KioskKeyboardHook", daemon=True)
            _threads["kb"].start()
        if "watch" not in _threads or not _threads["watch"].is_alive():
            _threads["watch"] = threading.Thread(target=_watch_thread, name="KioskWindowWatch", daemon=True)
            _threads["watch"].start()
        if "folder" not in _threads or not _threads["folder"].is_alive():
            _threads["folder"] = threading.Thread(target=_folder_thread, name="KioskFolderGuard", daemon=True)
            _threads["folder"].start()
    set_taskbar_visible(False)


def stop_kiosk() -> None:
    """Gỡ kiosk: hiện lại taskbar, mở khóa phím Windows. An toàn khi gọi lúc chưa bật."""
    with _lock:
        _state["active"] = False
        _stop.set()
        if IS_WINDOWS and _hook_thread_id[0]:
            try:
                user32.PostThreadMessageW(_hook_thread_id[0], WM_QUIT, 0, 0)
            except Exception:
                pass
            _hook_thread_id[0] = 0
        _threads.clear()
    set_taskbar_visible(True)


def is_active() -> bool:
    return bool(_state["active"])


def _child_pids() -> Set[int]:
    try:
        import psutil

        return {c.pid for c in psutil.Process(os.getpid()).children(recursive=True)}
    except Exception:
        return set()
