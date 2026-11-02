# -*- coding: utf-8 -*-
"""
Hệ thống An toàn Khẩn cấp 5 Lớp (5-Layer Fail-Safe) cho phần mềm Khóa máy Bàn học Thông minh:
  1. Phím tắt Cứu hộ Toàn cục cấp Hệ điều hành (Win32 RegisterHotKey):
       - Ctrl + Alt + Shift + F12
       - Ctrl + Shift + F10
     Hoạt động ngay cả khi cửa sổ khác (Chrome, Game...) đang chiếm quyền bàn phím hoặc UI bị treo.
  2. Tự động Khôi phục khi Crash (atexit, sys.excepthook, signal, Tkinter callback exception hook).
  3. Mở khóa Khẩn cấp bằng Mã PIN Cứu hộ (999999) qua thanh cứu hộ cố định ở đáy mọi màn hình.
  4. Công tắc File Cứu hộ UNLOCK.txt quét định kỳ mỗi 1 giây ở mọi chế độ.
  5. Tự động sinh File KHOI_PHUC_KHAN_CAP.bat ngay khi khởi động phần mềm.
"""

import atexit
import ctypes
from ctypes import wintypes
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
import winreg
from typing import Callable, Optional

import psutil

from core.config_manager import (
    EMERGENCY_BAT_FILE,
    UNLOCK_FLAG_FILE,
    get_app_dir,
    safe_log,
    update_runtime_state,
)

MASTER_RESCUE_PIN = "999999"
CRASH_LOG_FILE = os.path.join(get_app_dir(), "crash_recovery.log")

# Hằng số Win32 API cho Global Hotkeys
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_F10 = 0x79
VK_F12 = 0x7B

HOTKEY_ID_CTRL_ALT_SHIFT_F12 = 101
HOTKEY_ID_CTRL_SHIFT_F10 = 102

CREATE_NO_WINDOW = 0x08000000

_failsafe_initialized = False
_hotkey_thread_id: Optional[int] = None
_watchdog_stop_event = threading.Event()
_stop_workers_callbacks = []


def register_worker_cleanup(callback: Callable[[], None]) -> None:
    """Đăng ký hàm dọn dẹp luồng nền (như BoGiamSatThongMinh.dung_lai) khi thoát khẩn cấp."""
    if callback not in _stop_workers_callbacks:
        _stop_workers_callbacks.append(callback)


# =====================================================================
# CÁC HÀM CAN THIỆP HỆ ĐIỀU HÀNH WINDOWS (REGISTRY & EXPLORER SHELL)
# =====================================================================
def is_running_as_admin() -> bool:
    """Kiểm tra ứng dụng hiện tại có đang chạy với quyền Administrator hay không."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def set_windows_task_manager_lock(disable: bool) -> bool:
    """
    disable = True  -> Khóa cứng Task Manager gốc của Windows (DisableTaskMgr = 1)
    disable = False -> Mở lại Task Manager gốc cho Phụ huynh / Admin (DisableTaskMgr = 0)
    """
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 1 if disable else 0)
        winreg.CloseKey(key)
        return True
    except Exception as exc:
        safe_log(f"[WARN] Không thể ghi Registry DisableTaskMgr (cần quyền Admin): {exc}")
        return False


def get_windows_task_manager_lock() -> int:
    """Đọc trạng thái hiện tại của khóa DisableTaskMgr trong Registry (0 hoặc 1)."""
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, "DisableTaskMgr")
        winreg.CloseKey(key)
        return int(val)
    except Exception:
        return 0


def is_explorer_running() -> bool:
    """Kiểm tra tiến trình explorer.exe có đang hoạt động hay không."""
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name == "explorer.exe":
                return True
    except Exception:
        pass
    return False


def set_desktop_explorer_state(enable_explorer: bool) -> None:
    """Ẩn (enable_explorer=False) hoặc khôi phục (enable_explorer=True) thanh Taskbar & Desktop."""
    try:
        if not enable_explorer:
            subprocess.run(
                ["taskkill", "/f", "/im", "explorer.exe"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            if not is_explorer_running():
                win_dir = os.environ.get("WINDIR", r"C:\Windows")
                explorer_path = os.path.join(win_dir, "explorer.exe")
                target = explorer_path if os.path.exists(explorer_path) else "explorer.exe"
                subprocess.Popen([target])
    except Exception as exc:
        safe_log(f"[WARN] Lỗi điều khiển explorer.exe: {exc}")


def _terminate_child_pythonw_processes() -> None:
    """Dừng các luồng giám sát đăng ký và tắt tiến trình con pythonw.exe nếu có."""
    for cb in list(_stop_workers_callbacks):
        try:
            cb()
        except Exception:
            pass

    try:
        current_proc = psutil.Process(os.getpid())
        for child in current_proc.children(recursive=True):
            try:
                cname = (child.name() or "").lower()
                if cname in {"pythonw.exe", "giam_sat.exe"}:
                    child.kill()
            except Exception:
                continue
    except Exception:
        pass


def restore_windows_normal_state() -> None:
    """
    Hàm Cứu hộ Bất tử (Idempotent):
    1. Mở khóa lại Task Manager gốc của Windows (DisableTaskMgr = 0).
    2. Khởi chạy lại explorer.exe nếu thanh Taskbar/Desktop đang bị ẩn.
    3. Dừng toàn bộ luồng quét giam_sat và tiến trình con pythonw.exe.
    """
    set_windows_task_manager_lock(False)
    try:
        from services.kiosk_guard import stop_kiosk

        stop_kiosk()          # hiện lại Taskbar + gỡ hook phím Windows
    except Exception:
        pass
    set_desktop_explorer_state(True)
    _terminate_child_pythonw_processes()
    try:
        update_runtime_state(monitoring_active=False)
    except Exception:
        pass


def emergency_restore_and_exit(reason: str = "Khẩn cấp", exit_code: int = 0) -> None:
    """Khôi phục hoàn toàn trạng thái chuẩn của Windows và đóng ứng dụng ngay lập tức."""
    _watchdog_stop_event.set()
    stop_global_hotkeys()
    restore_windows_normal_state()
    try:
        update_runtime_state(current_role="unlocked", monitoring_active=False)
    except Exception:
        pass
    safe_log(f"[FAIL-SAFE EXIT] Đã khôi phục Windows và thoát an toàn. Lý do: {reason}")
    os._exit(exit_code)


# =====================================================================
# LỚP 5: TỰ ĐỘNG SINH FILE KHOI_PHUC_KHAN_CAP.BAT NGAY KHI KHỞI ĐỘNG
# =====================================================================
def generate_emergency_bat_file() -> str:
    """Tạo sẵn file KHOI_PHUC_KHAN_CAP.bat cạnh phần mềm để cứu hộ thủ công 1-click."""
    bat_content = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "echo ============================================================\r\n"
        "echo   CONG CU KHOI PHUC KHAN CAP - BAN HOC THONG MINH (STEM)\r\n"
        "echo ============================================================\r\n"
        "echo [1/4] Dang mo khoa Task Manager goc cua Windows (DisableTaskMgr = 0)...\r\n"
        'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System" '
        "/v DisableTaskMgr /t REG_DWORD /d 0 /f >nul 2>&1\r\n"
        "echo [2/4] Dang tat trinh khoa may va tien trinh giam sat ngam...\r\n"
        "taskkill /f /im SmartDeskLauncher.exe >nul 2>&1\r\n"
        "taskkill /f /im pythonw.exe >nul 2>&1\r\n"
        'powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '
        "'*he_thong_quan_ly_3_cap*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }\" >nul 2>&1\r\n"
        "echo [3/4] Dang khoi dong lai Windows Explorer (hien lai Taskbar/Desktop)...\r\n"
        "taskkill /f /im explorer.exe >nul 2>&1\r\n"
        "start explorer.exe\r\n"
        "echo [4/4] HOAN TAT! May tinh da tro ve trang thai binh thuong.\r\n"
        "timeout /t 3\r\n"
    )
    try:
        with open(EMERGENCY_BAT_FILE, "w", encoding="utf-8") as f:
            f.write(bat_content)
    except Exception as exc:
        safe_log(f"[WARN] Không thể tạo KHOI_PHUC_KHAN_CAP.bat: {exc}")
    return EMERGENCY_BAT_FILE


# =====================================================================
# LỚP 1: PHÍM TẮT CỨU HỘ TOÀN CỤC WIN32 (PANIC HOTKEYS)
# =====================================================================
def start_global_panic_hotkeys(on_trigger: Optional[Callable[[str], None]] = None) -> None:
    """
    Đăng ký trực tiếp tổ hợp phím cứu hộ với nhân Windows (user32.RegisterHotKey):
      - Ctrl + Alt + Shift + F12
      - Ctrl + Shift + F10
    Nếu callback giao diện bị treo quá 0.6s, luồng cứu hộ tự cưỡng bức khôi phục Windows và thoát.
    """

    def _hotkey_loop() -> None:
        global _hotkey_thread_id
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        _hotkey_thread_id = kernel32.GetCurrentThreadId()

        ok1 = user32.RegisterHotKey(
            None,
            HOTKEY_ID_CTRL_ALT_SHIFT_F12,
            MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT,
            VK_F12,
        )
        ok2 = user32.RegisterHotKey(
            None,
            HOTKEY_ID_CTRL_SHIFT_F10,
            MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT,
            VK_F10,
        )
        if not ok1 and not ok2:
            safe_log("[WARN] Win32 RegisterHotKey đã được đăng ký bởi tiến trình khác.")

        msg = wintypes.MSG()
        try:
            while not _watchdog_stop_event.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0 or msg.message == WM_QUIT:
                    break
                if msg.message == WM_HOTKEY:
                    hk_id = msg.wParam
                    combo_name = (
                        "Ctrl + Alt + Shift + F12"
                        if hk_id == HOTKEY_ID_CTRL_ALT_SHIFT_F12
                        else "Ctrl + Shift + F10"
                    )
                    reason = f"Phím tắt Cứu hộ Toàn cục ({combo_name})"
                    # Luôn mở khóa hệ thống ngay lập tức ở luồng này trước tiên
                    restore_windows_normal_state()
                    if on_trigger is not None:
                        # Đặt hẹn giờ bảo hiểm 0.8s phòng trường hợp mainloop Tkinter bị đơ
                        threading.Timer(
                            0.8,
                            lambda r=reason: emergency_restore_and_exit(r, 0),
                        ).start()
                        try:
                            on_trigger(reason)
                        except Exception:
                            emergency_restore_and_exit(reason, 0)
                    else:
                        emergency_restore_and_exit(reason, 0)
                    break
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID_CTRL_ALT_SHIFT_F12)
            user32.UnregisterHotKey(None, HOTKEY_ID_CTRL_SHIFT_F10)

    t = threading.Thread(target=_hotkey_loop, name="Win32PanicHotkeyThread", daemon=True)
    t.start()


def stop_global_hotkeys() -> None:
    """Gửi thông điệp WM_QUIT giải phóng luồng lắng nghe Win32 RegisterHotKey."""
    global _hotkey_thread_id
    if _hotkey_thread_id is not None:
        try:
            ctypes.windll.user32.PostThreadMessageW(_hotkey_thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass
        _hotkey_thread_id = None


# =====================================================================
# LỚP 4: CÔNG TẮC FILE CỨU HỘ UNLOCK.TXT (QUÉT MỖI 1 GIÂY Ở MỌI MÀN HÌNH)
# =====================================================================
def check_and_consume_unlock_file() -> bool:
    """Kiểm tra sự tồn tại của UNLOCK.txt (tự xóa cờ sau khi phát hiện để không lặp vô hạn)."""
    candidate_paths = [
        UNLOCK_FLAG_FILE,
        os.path.abspath("UNLOCK.txt"),
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
            return True
    return False


def start_unlock_file_watchdog(on_trigger: Optional[Callable[[str], None]] = None) -> None:
    """Luồng nền độc lập kiểm tra UNLOCK.txt mỗi 1 giây bất kể phần mềm đang ở màn hình nào."""

    def _watch_loop() -> None:
        while not _watchdog_stop_event.is_set():
            if check_and_consume_unlock_file():
                reason = "Phát hiện file cứu hộ UNLOCK.txt"
                restore_windows_normal_state()
                if on_trigger is not None:
                    threading.Timer(
                        0.8,
                        lambda r=reason: emergency_restore_and_exit(r, 0),
                    ).start()
                    try:
                        on_trigger(reason)
                    except Exception:
                        emergency_restore_and_exit(reason, 0)
                else:
                    emergency_restore_and_exit(reason, 0)
                break
            time.sleep(1.0)

    t = threading.Thread(target=_watch_loop, name="UnlockFileWatchdogThread", daemon=True)
    t.start()


# =====================================================================
# LỚP 2: CRASH HOOKS (ATEXIT, SYS.EXCEPTHOOK, SIGNAL & TKINTER HOOK)
# =====================================================================
def _log_crash(exc_type, exc_value, exc_tb) -> None:
    try:
        with open(CRASH_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n=== CRASH RECOVERY [{time.strftime('%Y-%m-%d %H:%M:%S')}] ===\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    except Exception:
        pass


def _crash_safety_hook(exc_type, exc_value, exc_tb) -> None:
    _log_crash(exc_type, exc_value, exc_tb)
    restore_windows_normal_state()
    os._exit(1)


def _signal_safety_handler(signum, _frame) -> None:
    emergency_restore_and_exit(f"Nhận tín hiệu hệ thống ({signum})", 0)


def install_all_failsafes(on_ui_emergency: Optional[Callable[[str], None]] = None) -> None:
    """
    Kích hoạt đồng bộ toàn bộ hệ thống bảo vệ khẩn cấp ngay khi khởi động phần mềm:
      - Sinh file KHOI_PHUC_KHAN_CAP.bat
      - Đăng ký atexit, sys.excepthook, signal handlers
      - Bật Global Panic Hotkeys (Win32 RegisterHotKey)
      - Bật bộ quét UNLOCK.txt mỗi 1 giây
    """
    global _failsafe_initialized
    generate_emergency_bat_file()

    if not _failsafe_initialized:
        atexit.register(restore_windows_normal_state)
        sys.excepthook = _crash_safety_hook
        for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _signal_safety_handler)
                except Exception:
                    pass
        start_global_panic_hotkeys(on_ui_emergency)
        start_unlock_file_watchdog(on_ui_emergency)
        _failsafe_initialized = True


def attach_tkinter_failsafe(root, on_emergency_exit: Callable[[str], None]) -> None:
    """
    Gắn thêm bảo vệ cho cửa sổ Tkinter:
      - Bắt lỗi ngoại lệ phát sinh bên trong callback giao diện (report_callback_exception).
      - Gắn phím tắt dự phòng trực tiếp trên cửa sổ Tkinter.
    """

    def _tk_exception_handler(exc_type, exc_value, exc_tb):
        _log_crash(exc_type, exc_value, exc_tb)
        restore_windows_normal_state()
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "Khôi phục An toàn (Fail-Safe)",
                f"Phát hiện lỗi bất ngờ: {exc_value}\n"
                "Hệ thống đã tự động mở khóa lại Task Manager gốc và Windows Explorer!",
            )
        except Exception:
            pass

    root.report_callback_exception = _tk_exception_handler
    root.bind_all(
        "<Control-Alt-Shift-F12>",
        lambda _e: on_emergency_exit("Phím tắt Ctrl + Alt + Shift + F12"),
    )
    root.bind_all(
        "<Control-Shift-F10>",
        lambda _e: on_emergency_exit("Phím tắt Ctrl + Shift + F10"),
    )


# =====================================================================
# LỚP 3: MÃ PIN CỨU HỘ (999999)
# =====================================================================
def verify_rescue_pin(pin_input: Optional[str]) -> bool:
    """Kiểm tra mã PIN cứu hộ khẩn cấp (999999)."""
    return (pin_input or "").strip() == MASTER_RESCUE_PIN
