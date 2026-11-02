# -*- coding: utf-8 -*-
"""
Hệ thống An toàn Khẩn cấp 5 Lớp (5-Layer Fail-Safe) cho phần mềm Khóa máy Bàn học Thông minh:
  1. Phím tắt Cứu hộ Toàn cục cấp Hệ điều hành (Win32 RegisterHotKey):
       - 1 tổ hợp do Admin đặt (Admin → 🆘 Cứu hộ), mặc định Ctrl + Alt + Shift + F12, bấm xong phải nhập PIN
     Hoạt động ngay cả khi cửa sổ khác (Chrome, Game...) đang chiếm quyền bàn phím hoặc UI bị treo.
  2. Tự động Khôi phục khi Crash (atexit, sys.excepthook, signal, Tkinter callback exception hook).
  3. Mã PIN cứu hộ (Admin đặt, lưu băm SHA-256; mặc định 999999 khi chưa đổi) — chỉ hỏi sau phím cứu hộ,
     không có nút cứu hộ nào trên màn hình học sinh.
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
    load_config,
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
        "echo   CONG CU KHOI PHUC KHAN CAP - FOCUSDESK (STEM)\r\n"
        "echo ============================================================\r\n"
        "echo [1/4] Dang mo khoa Task Manager goc cua Windows (DisableTaskMgr = 0)...\r\n"
        'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System" '
        "/v DisableTaskMgr /t REG_DWORD /d 0 /f >nul 2>&1\r\n"
        "echo [2/4] Dang tat trinh khoa may va tien trinh giam sat ngam...\r\n"
        "taskkill /f /im FocusDeskLauncher.exe >nul 2>&1\r\n"
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
RESCUE_HOTKEY_DEFAULT = "Ctrl+Alt+Shift+F12"
_MOD_BITS = {"CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL, "ALT": MOD_ALT, "SHIFT": MOD_SHIFT}
_hotkey_callback: Optional[Callable[[str], None]] = None
_rescue_ack = threading.Event()


def parse_hotkey(text: str):
    """"Ctrl+Alt+Shift+F12" -> (mods, vk, chuẩn hóa). Cần >= 2 phím bổ trợ để học sinh khó bấm nhầm/đoán.
    Phím chính: F1–F24, A–Z, 0–9. Trả None nếu không hợp lệ."""
    parts = [p.strip().upper() for p in (text or "").replace(" ", "").split("+") if p.strip()]
    if len(parts) < 3:
        return None
    mods, names = 0, []
    for p in parts[:-1]:
        if p not in _MOD_BITS:
            return None
        bit = _MOD_BITS[p]
        if mods & bit:
            return None
        mods |= bit
        names.append({MOD_CONTROL: "Ctrl", MOD_ALT: "Alt", MOD_SHIFT: "Shift"}[bit])
    key = parts[-1]
    if len(key) >= 2 and key[0] == "F" and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and (key.isalpha() or key.isdigit()):
        vk = ord(key)
    else:
        return None
    order = {"Ctrl": 0, "Alt": 1, "Shift": 2}
    names.sort(key=lambda n: order[n])
    return mods, vk, "+".join(names + [key])


def get_rescue_hotkey() -> str:
    try:
        hk = str(load_config().get("rescue_hotkey", "") or "")
    except Exception:
        hk = ""
    return parse_hotkey(hk)[2] if parse_hotkey(hk) else RESCUE_HOTKEY_DEFAULT


def ack_rescue_prompt() -> None:
    """Giao diện gọi khi đã hiện được hộp nhập PIN -> hủy hẹn giờ cưỡng bức khôi phục."""
    _rescue_ack.set()


def start_global_panic_hotkeys(on_trigger: Optional[Callable[[str], None]] = None) -> None:
    """
    Đăng ký 1 tổ hợp phím cứu hộ do Admin đặt (shared_config "rescue_hotkey", mặc định Ctrl+Alt+Shift+F12)
    với nhân Windows (user32.RegisterHotKey). Không hiện ở bất kỳ màn hình nào của học sinh.
      - Có giao diện (on_trigger): bấm phím -> giao diện hỏi PIN cứu hộ. Nếu giao diện treo, không hiện được hộp PIN
        trong 4 giây -> tự cưỡng bức khôi phục Windows và thoát (an toàn hơn là kẹt máy).
      - Không có giao diện: khôi phục Windows ngay.
    """
    global _hotkey_callback
    _hotkey_callback = on_trigger
    parsed = parse_hotkey(get_rescue_hotkey()) or parse_hotkey(RESCUE_HOTKEY_DEFAULT)
    mods, vk, combo_name = parsed

    def _hotkey_loop() -> None:
        global _hotkey_thread_id
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        _hotkey_thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, HOTKEY_ID_CTRL_ALT_SHIFT_F12, mods | MOD_NOREPEAT, vk):
            safe_log(f"[WARN] Không đăng ký được phím cứu hộ {combo_name} (chương trình khác đang dùng).")
        msg = wintypes.MSG()
        try:
            while not _watchdog_stop_event.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0 or msg.message == WM_QUIT:
                    break
                if msg.message != WM_HOTKEY:
                    continue
                reason = f"Phím cứu hộ ({combo_name})"
                cb = _hotkey_callback
                if cb is None:
                    emergency_restore_and_exit(reason, 0)
                    break
                _rescue_ack.clear()

                def _bao_hiem(r=reason) -> None:
                    if not _rescue_ack.wait(4.0):
                        safe_log("[FAILSAFE] Giao diện không phản hồi phím cứu hộ -> cưỡng bức khôi phục Windows.")
                        emergency_restore_and_exit(r + " · giao diện treo", 0)

                threading.Thread(target=_bao_hiem, daemon=True).start()
                try:
                    cb(reason)
                except Exception:
                    emergency_restore_and_exit(reason, 0)
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID_CTRL_ALT_SHIFT_F12)

    t = threading.Thread(target=_hotkey_loop, name="Win32PanicHotkeyThread", daemon=True)
    t.start()


def restart_global_panic_hotkeys() -> None:
    """Admin vừa đổi phím cứu hộ -> gỡ phím cũ, đăng ký phím mới."""
    cb = _hotkey_callback
    stop_global_hotkeys()
    time.sleep(0.3)
    start_global_panic_hotkeys(cb)


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
    # Phím cứu hộ chỉ đăng ký 1 nơi (RegisterHotKey toàn cục, Admin đặt) -> không gắn thêm phím cố định ở đây,
    # để học sinh không thể dùng tổ hợp mặc định cũ (Ctrl+Shift+F10) thoát phần mềm.


# =====================================================================
# LỚP 3: MÃ PIN CỨU HỘ (Admin đặt; mặc định 999999)
# =====================================================================
def _hash_pin(pin: str) -> str:
    import hashlib

    return hashlib.sha256(("focusdesk-rescue|" + pin.strip()).encode()).hexdigest()


def set_rescue_pin(pin: str) -> None:
    """Admin đổi PIN cứu hộ: chỉ lưu bản băm SHA-256 vào shared_config ("rescue_pin_hash")."""
    from core.config_manager import save_config

    cfg = load_config()
    cfg["rescue_pin_hash"] = _hash_pin(pin)
    save_config(cfg)


def rescue_pin_is_default() -> bool:
    try:
        return not load_config().get("rescue_pin_hash")
    except Exception:
        return True


def verify_rescue_pin(pin_input: Optional[str]) -> bool:
    """Kiểm tra PIN cứu hộ: PIN Admin đã đặt (băm), hoặc 999999 nếu Admin chưa đổi."""
    pin = (pin_input or "").strip()
    try:
        h = load_config().get("rescue_pin_hash")
    except Exception:
        h = None
    if h:
        import hmac as _hmac

        return _hmac.compare_digest(_hash_pin(pin), str(h))
    return pin == MASTER_RESCUE_PIN
