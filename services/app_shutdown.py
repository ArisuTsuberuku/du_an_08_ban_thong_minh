# -*- coding: utf-8 -*-
"""
Tắt FocusDesk SẠCH (services/app_shutdown.py)

Vấn đề: FocusDesk chạy kèm các tiến trình con riêng — đường truyền Cloudflare (cloudflared.exe, cho "Xem trực tiếp")
và AI camera tách tiến trình (python/pythonw posture_ai_engine.py). Thoát bằng os._exit() thì Windows KHÔNG tự tắt
tiến trình con -> cloudflared còn chạy ngầm mãi, giữ đường hầm ra Internet.

3 lớp:
  1. JOB OBJECT (KILL_ON_JOB_CLOSE): mỗi tiến trình con được gắn vào 1 "job" của Windows. FocusDesk chết kiểu gì
     (thoát, crash, bị End task) thì Windows cũng tự đóng cả job -> tiến trình con chết theo.
  2. shutdown_all(): khi thoát, báo phụ huynh, tắt lần lượt từng phần, rồi quét lại tiến trình con còn sót.
  3. kill_orphan_helpers(): lúc khởi động, dọn cloudflared / AI camera mồ côi của lần chạy trước (bản cũ chưa có lớp 1).
Chỉ đụng tới tiến trình do FocusDesk tạo (nhận diện bằng dòng lệnh), KHÔNG đụng ứng dụng học sinh mở.
"""

import os
import sys
import time
from typing import Callable, List, Optional

from core.config_manager import get_app_dir, load_config, safe_log

IS_WINDOWS = sys.platform.startswith("win")
_job = [None]


# ----------------------------------------------------------------------------- lớp 1: Job Object
def _get_job():
    if not IS_WINDOWS:
        return None
    if _job[0]:
        return _job[0]
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IOC(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXT()
        info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # Extended limit info
            return None
        _job[0] = job
        return job
    except Exception as exc:
        safe_log(f"[THOÁT] Không tạo được Job Object: {exc}")
        return None


def adopt(proc) -> bool:
    """Gắn 1 tiến trình con (subprocess.Popen) vào job: FocusDesk tắt -> nó tắt theo. Không lỗi nếu không làm được."""
    job = _get_job()
    if not job or proc is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        return bool(k32.AssignProcessToJobObject(job, int(proc._handle)))
    except Exception:
        return False


# ----------------------------------------------------------------------------- nhận diện tiến trình con của FocusDesk
def _is_helper(p, port: int) -> str:
    """Trả về tên dễ hiểu nếu `p` là tiến trình phụ do FocusDesk tạo, ngược lại ''."""
    try:
        name = (p.name() or "").lower()
        cmd = " ".join(p.cmdline() or []).lower()
    except Exception:
        return ""
    if name == "cloudflared.exe" or name == "cloudflared":
        if "tunnel" in cmd and (f"127.0.0.1:{port}" in cmd or f"localhost:{port}" in cmd):
            return "Đường truyền Cloudflare (cloudflared)"
        return ""
    if name.startswith("python") or name in ("giam_sat.exe",):
        app_dir = get_app_dir().lower()
        if "posture_ai_engine" in cmd:
            return "AI camera (python)"
        if "giam_sat" in cmd or name == "giam_sat.exe":
            return "Giám sát (python)"
        if app_dir and app_dir in cmd and "he_thong_quan_ly_3_cap" not in cmd:
            return "Tiến trình phụ FocusDesk (python)"
    return ""


def _my_helpers(port: int) -> List[tuple]:
    try:
        import psutil

        me = psutil.Process(os.getpid())
        return [(c, lbl) for c in me.children(recursive=True) for lbl in [_is_helper(c, port)] if lbl]
    except Exception:
        return []


def _kill(procs, timeout: float = 3.0) -> int:
    try:
        import psutil
    except Exception:
        return 0
    procs = list(procs)
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    _gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass
    return len(procs)


def kill_orphan_helpers() -> int:
    """Lúc khởi động: tắt cloudflared / AI camera của lần chạy TRƯỚC còn sót (cha đã chết)."""
    try:
        import psutil
    except Exception:
        return 0
    port = int(load_config().get("web_port", 5000))
    me = os.getpid()
    orphans = []
    for p in psutil.process_iter(["pid", "ppid"]):
        try:
            if p.pid == me or not _is_helper(p, port):
                continue
            ppid = p.info.get("ppid") or 0
            if ppid and psutil.pid_exists(ppid) and ppid != me:
                parent = psutil.Process(ppid)
                if "he_thong_quan_ly_3_cap" in " ".join(parent.cmdline() or []).lower() or \
                        (parent.name() or "").lower().startswith("focusdesk"):
                    continue        # đang thuộc 1 FocusDesk khác còn sống
            orphans.append(p)
        except Exception:
            continue
    if orphans:
        safe_log(f"[THOÁT] Dọn {len(orphans)} tiến trình phụ mồ côi của lần chạy trước: "
                 + ", ".join(sorted({(o.name() or '?') for o in orphans})))
        _kill(orphans)
    return len(orphans)


# ----------------------------------------------------------------------------- lớp 2: tắt sạch khi thoát
def shutdown_all(reason: str, progress: Optional[Callable[[str], None]] = None) -> List[str]:
    """Tắt mọi phần chạy kèm. Trả về danh sách dòng kết quả (✓/⚠) để hiện thông báo cho người dùng."""
    lines: List[str] = []

    def say(line: str) -> None:
        lines.append(line)
        safe_log(f"[THOÁT] {line}")
        if progress:
            try:
                progress(line)
            except Exception:
                pass

    port = int(load_config().get("web_port", 5000))
    before = _my_helpers(port)

    # 1. Báo phụ huynh TRƯỚC khi tắt kết nối cloud
    try:
        from services.cloud_agent import notify_shutdown

        r = notify_shutdown(reason)
        if r is None:
            say("•  Chưa bật kết nối web phụ huynh — bỏ qua báo phụ huynh")
        elif r:
            say("✓  Đã báo phụ huynh (web + Telegram): FocusDesk đã tắt")
        else:
            say("⚠  Không gửi được báo cho phụ huynh (mất mạng?) — web sẽ tự hiện máy ngoại tuyến")
    except Exception as exc:
        say(f"⚠  Không gửi được báo cho phụ huynh: {exc}")

    # 2. Kết nối cloud + đường truyền Cloudflare
    try:
        from services.cloud_agent import stop_cloud_agent

        had_tunnel = any(lbl.startswith("Đường truyền") for _p, lbl in before)
        stop_cloud_agent()
        say("✓  Đã ngắt kết nối web phụ huynh" + (" và tắt đường truyền Cloudflare" if had_tunnel else ""))
    except Exception as exc:
        say(f"⚠  Lỗi khi ngắt kết nối cloud: {exc}")

    # 3. AI camera (trong tiến trình hoặc tách tiến trình python)
    try:
        from services.posture_controller import posture_manager

        posture_manager.stop()
        say("✓  Đã tắt AI camera và nhả webcam")
    except Exception as exc:
        say(f"⚠  Lỗi khi tắt AI camera: {exc}")

    # 4. Hộp điện thoại (nhả cổng COM)
    try:
        from services import phone_box

        box = getattr(phone_box, "_link", None)    # chỉ dừng nếu đã kết nối, không tạo mới
        if box is not None:
            box.stop()
            say("✓  Đã ngắt hộp điện thoại (nhả cổng COM)")
    except Exception:
        pass

    # 5. Web nội bộ :5000 + bot Telegram cục bộ
    try:
        from trang_web import stop_web_server_in_background

        stop_web_server_in_background()
        say(f"✓  Đã tắt web nội bộ (cổng {port})")
    except Exception as exc:
        say(f"⚠  Lỗi khi tắt web nội bộ: {exc}")

    # 6. Quét lại: còn tiến trình con nào của FocusDesk thì tắt nốt
    left = _my_helpers(port)
    if left:
        names = ", ".join(sorted({lbl for _p, lbl in left}))
        _kill([p for p, _l in left])
        still = _my_helpers(port)
        say(("✓  Đã tắt nốt: " if not still else "⚠  Không tắt được hết: ") + names)
    else:
        say("✓  Không còn tiến trình python / cloudflared nào chạy ngầm")
    return lines
