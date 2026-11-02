# -*- coding: utf-8 -*-
"""
Bộ Điều Phối Luồng AI Tư Thế 3D (services/posture_controller.py).
- KHÔNG import cv2 / mediapipe / numpy ở mức đỉnh để file đóng gói SmartDeskLauncher.exe
  giữ nguyên dung lượng siêu gọn (~25 MB, đẩy trực tiếp lên GitHub không vượt ngưỡng 100 MB).
- Quản lý bộ đệm khung hình MJPEG trong RAM cho route /video_feed trên trang_web.py.
- Hỗ trợ Chế độ Kép (Dual-Mode):
    1. Khi chạy từ mã nguồn Python (.py): Khởi chạy trực tiếp PostureAIServiceWorker trong cùng tiến trình.
    2. Khi chạy từ bản biên dịch SmartDeskLauncher.exe (sys.frozen): Tự động tìm pythonw.exe và khởi chạy
       tiến trình vệ tinh ẩn `posture_ai_engine.py --daemon --parent-pid <PID>` đẩy MJPEG & Telemetry về cổng 5000.
"""

import base64
import glob
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Dict, Optional, Tuple

from core.config_manager import get_app_dir, load_config, safe_log, update_runtime_state

# Ảnh JPEG 480x270 chuẩn nén sẵn dùng làm màn hình chờ trên luồng /video_feed khi Camera AI đang nghỉ
_STANDBY_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0JCgwKCA0MCwwPDg0QFCIWFBISFCkdHxgiMSszMjArLy42PE1CNjlJOi4vQ1xESVBS"
    "V1dXNEFfZl5UZU1VV1P/2wBDAQ4PDxQSFCcWFidTNy83U1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NT"
    "U1NTU1NTU1P/wAARCAEOAeADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUF"
    "BAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW"
    "V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi"
    "4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAEC"
    "AxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm"
    "Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq"
    "8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDgaKKK1ICirT6dcpYLeNHiBujZH8qY9pMlnHdMoEMjFVbI5P0/CnyvsTzxfUgoq/aaPeXk"
    "CzQIrIxwMuBSpo17JcywLGvmRAFxvHGelVyS7E+1hqr7GfRVq9066sdv2mIoG6NkEH8RTrPS7q+iaWBFKKdpJYDn8aXLK9rD5425"
    "r6FOirkmlXsd0ls1u3mvyoGCD+PSlvNKvLKMSTxYTONysGAPvijkl2D2kNFfcpUVojQ9QMHmi3OMbtu4bsfTrSwaDqFxCkscQKOM"
    "glwKfs5difa0/wCZGbRV660m8tInlmjCohCsQwPJpsWmXUyW7JGCLgkR/MOcdf5UuSV7WK9pC17lOir9zo99axGWSH5F6srBsfXF"
    "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"
    "c28xyJYUjfJXaMdPp/OuZOo3p63lwfrK3+NQxSyQvvikeN/7yMQaIzjFWSFKlOb5m9jqjJDLY6dYXGFWeBTG/wDdcYxWR4n51yXH"
    "ov8AIVmPNK4QPI7BBhQWJ2j29KmhvporsXLETSjvL8386JVFJWYQouEuZeZc8S/8hdh/EEUN9cVlU+aZ55nllYs7nJJ70ys5O8mz"
    "enHliohRRRUlhRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFACbh6ijcPUVDRSuOxNuHqKNw9RUNFFwsTbh6ijcPU"
    "VDRRcLE24eoo3D1FQ0UXCxNuHqKNw9RUNFFwsTbh6ijcPUVDRRcLE24eoo3D1FQ0UXCxNuHqKNw9RUNFFwsTbh6ijcPUVDRRcLE2"
    "4eoo3D1FQ0UXCxNuHqKNw9RUNFFwsTbh6ijcPUVDRRcLE24eoo3D1FQ0UXCxNuHqKNw9RUNFFwsTbh6ijcPUVDRRcLE24eoo3D1F"
    "Q0UXCxNuHqKNw9RUNFFwsTbh6ijcPUVDRRcLE24eoo3D1FQ0UXCxNuHqKNw9RUNFFwsTbh6ijcPUVDRRcLE24eoo3D1FQ0UXCxNu"
    "HqKNw9RUNFFwsTbh6ijcPUVDRRcLE24eoo3D1FQ0UXCxNuHqKNw9RUNFFwsTbh6ijcPUVDRRcLE24eoo3D1FQ0UXCxNuHqKNw9RU"
    "NFFwsTbh6ijcPUVDRRcLBRRRUgFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQ"
    "AUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUU"
    "UAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFF"
    "FFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFAB"
    "RRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQ"
    "AUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUU"
    "UAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRW9a3Mum+GI7qyIjnmu3jkmCgsF"
    "VVIXJ6ZyT70DMGiuhEC31xLeX9gYfkjJxKtvGxI+8SR1YDIAHqas3Oi6Xp8m24FzNvvTbqUkC7U2IwJ+U5I3+2aLhY5WiumXQrK7"
    "uZbS1aaKS3vY7WSSRgwkDFhuAwMHK9MnrUkGiaXPcxqZDGA0gaJLuOZ2URswYbRxyuCD6ii4WOVorodM07T751c208UE0wiQyXaL"
    "jgZx8uXbJzgDA49ah0bS/M8StblDPHaO7yKB98Ien4kAfjRcDEortTZzHxFpt/dW/kvdwyCVCuAJFRgePcbT+NY+hvarousG4t5J"
    "SIoydsoXjzF4+6cc85ouFjCordTR7dtYt7bMnlSWIuCc87vJL+nTdVbT7W0GmXN/eJLMkUiRJFG4TJYMck4PAC+negDLorrNYs7F"
    "Wlvp455IIYbWKOJXCMd0WcscHGAvp1NRXuk6VplmtzOl3OsswWNFkVCqGNH5+U/MN+O2fai4WOYoro7zR7DSt4uhcXG+7eCMxOF2"
    "qoU7jwcn5hxx0p2n2cFl44nsxvNvF9oTnBbaI3/Wi4WOaorfi02yvYLW6tYLlYmleKWEzKT8qBtwcgADHUnp1rTs7Cwsm+0CF5Fu"
    "LG5JRLgOFKqQcMF5yPyPrRcLHG0V0Phe0TUrzU7aMiBJbUhDId20eYmBnjJ7dsmpLYafDpWsh7e6MEU0CiN3CyMw3g5ODt+mD6e9"
    "FwOaoror/T4rXSr17aWYQP8AZZUjZh0dXOG45Ix14oltbObw9pDXN6tq22XA8ln3fOfSi4HO0V1+vWOnwX8lzdxzzLPOIUWBwmwK"
    "iEtyDkndwOOlVb/R9O0m4jtLwXU8sxkxLARhAHZFwuPmPy5IyOtK4WOaorV0W0srpJ/tLBp1KiKEzrCHBzk7mBGRxxx1rUs9Iilj"
    "itrtruKBby5VoiRuTZCGBxjG71p3A5aius0O0sJLqzvrZJ4YmNxDLHI4kIxCTuBwOx/MVkX9rZtpMF/ZJNCrTNC8csgfkAEEEAdj"
    "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"
    "9aOZB7WJl0Va1OPydTuo852SsMhQM8+g4qrTRad1cKKKKBhRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQA"
    "UUUUAFFFFABRRRQAUUUUATi8nW4inEn72EKEbA429KfHqFzFB5KSAKAQDtBZQeoBxkZ9qq0UWQuVdi22pXTwtEXXa6hWIRQWAxjJ"
    "xk9BSXGoXNzEY5ZAVJBbCAFiOhYgZP41VopWQuSPYtx6ldRQJEkihUUqnyKSoOScHGR1NDaldPC0RddrqFY7FywGMZOMnoKqUUWQ"
    "ckexPJdzy+dvfPnuHk4HzHnn9TUtvfFL5rm4UzF0ZHAIUkMpXrj39Kp0U7IOVWtYupqdxCipCUWOMny90aMyAnP3iM0i6ndom0SD"
    "7pTcUUttOcjcRnHJqnRSsg5I9i3HqE8caplGCIyISoyoOc4PXuaghmkgk8yJtrYIzjPBGD+hqOinYfKi1bajc2qoIXUbG3ISisVP"
    "sSOKjN1KbYwFgY927BUEg98HqOnaoaKLIOVb2LdvqN1bRrHFINqnK7kVih9QSOPwpV1O7SPaJB90puKKW2nORuIzjk1TopWQuSPY"
    "nN3OSxL8vGIjwOVGMD9BTkv7hMfOrAKF2uisMDpwRjiq1FOyHyrsWft1zuVvNO5JDKDgcMcZP6CkuL2e5jSOQr5aElVRFUAnGeAB"
    "6VXoosg5V2J/tk/2tbnzP3ykENgcY4HHTtUy3/7icSozyyRCFX3AKqggjjHP3fWqVFKyE4p9C1b6hc20QiiddgbeA0athuORkdeB"
    "SLf3IjKeYCDnllBYZ64JGRnPaq1FOyDlj2J/tk/mF9/zGPys4H3du3H5U62v7i1TZC6hQ28bkVtreoyOD9KrUUWQ+VPSxdj1a8j+"
    "7KMhiwYxqSCeuCRxn0FMbULlrfyTINm0JnaNxUdAWxnHtmqtFKyFyR7D5pXnmeaVt0jsWY4xkmmUUUygooooAKKKKACiiigAoooo"
    "AKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKK"
    "KACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACi"
    "iigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigA"
    "ooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAoooo"
    "AKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKK"
    "KACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACi"
    "iigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigA"
    "ooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAoooo"
    "AKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKK"
    "KACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACi"
    "iigD/9k="
)
STANDBY_POSTURE_JPEG: bytes = base64.b64decode(_STANDBY_B64)

_frame_lock = threading.Lock()
_latest_jpeg: Optional[bytes] = None
_latest_ts: float = 0.0
_recalibrate_flag: bool = False


def set_latest_posture_frame(jpeg_bytes: bytes) -> None:
    """Cập nhật khung hình MJPEG mới nhất lên bộ nhớ RAM nội bộ."""
    global _latest_jpeg, _latest_ts
    if not jpeg_bytes:
        return
    with _frame_lock:
        _latest_jpeg = jpeg_bytes
        _latest_ts = time.time()


# Hình GỐC (không vẽ lớp AI) cho phụ huynh: "live" = 480px nhẹ cho xem trực tiếp, "full" = độ phân giải gốc cho ảnh chụp
_raw_frames: Dict[str, Tuple[bytes, float]] = {}


def set_latest_raw_frame(kind: str, jpeg_bytes: bytes) -> None:
    if jpeg_bytes and kind in ("live", "full"):
        with _frame_lock:
            _raw_frames[kind] = (jpeg_bytes, time.time())


def get_latest_raw_frame(kind: str = "live", max_age_sec: float = 5.0) -> bytes:
    """Khung hình gốc mới nhất (không lớp AI); thiếu thì dùng bản có lớp AI / ảnh chờ."""
    with _frame_lock:
        item = _raw_frames.get(kind) or _raw_frames.get("live")
        if item and time.time() - item[1] <= max_age_sec:
            return item[0]
    return get_latest_posture_frame(max_age_sec)


def get_latest_posture_frame(max_age_sec: float = 5.0) -> bytes:
    """Lấy khung hình MJPEG mới nhất; nếu Camera đang nghỉ thì trả về ảnh chờ STANDBY_POSTURE_JPEG."""
    with _frame_lock:
        if _latest_jpeg is not None and (time.time() - _latest_ts) <= max_age_sec:
            return _latest_jpeg
    return STANDBY_POSTURE_JPEG


def has_live_posture_stream(max_age_sec: float = 5.0) -> bool:
    with _frame_lock:
        return _latest_jpeg is not None and (time.time() - _latest_ts) <= max_age_sec


def request_posture_recalibration() -> None:
    global _recalibrate_flag
    with _frame_lock:
        _recalibrate_flag = True
    update_runtime_state(recalibrate_posture_requested=True)


def consume_recalibration_request() -> bool:
    global _recalibrate_flag
    with _frame_lock:
        if _recalibrate_flag:
            _recalibrate_flag = False
            return True
    return False


def _sibling_pythonw() -> Optional[str]:
    """pythonw.exe nằm cạnh trình thông dịch hiện tại (khi đang chạy bằng mã nguồn .py)."""
    if getattr(sys, "frozen", False) or os.name != "nt":
        return None
    cand = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "pythonw.exe")
    return cand if os.path.isfile(cand) else None


def _should_use_pythonw_sidecar() -> bool:
    """
    Tùy chọn: ép tách AI Camera sang tiến trình pythonw.exe khi chạy bằng mã nguồn .py
    (đặt biến môi trường SMARTDESK_AI_SIDECAR=1). Mặc định chạy AI trong cùng tiến trình.
    Dùng khi Windows/Antivirus chặn Webcam riêng với python.exe (đã gặp 02/11/2026 trên MSI Bravo 15).
    """
    if os.environ.get("SMARTDESK_AI_SIDECAR", "").strip() != "1":
        return False
    return _sibling_pythonw() is not None


def _find_python_interpreter() -> Optional[str]:
    """Định vị pythonw.exe / python.exe trên máy Windows khi chạy từ bản đóng gói .EXE."""
    sib = _sibling_pythonw()
    if sib:
        return sib
    for cmd in ("pythonw", "python"):
        found = shutil.which(cmd)
        if found and os.path.exists(found):
            return found

    patterns = [
        os.path.expandvars(r"%LOCALAPPDATA%\Python\pythoncore-*\pythonw.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Python\pythoncore-*\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python*\pythonw.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python*\python.exe"),
        r"C:\Python*\pythonw.exe",
    ]
    for pat in patterns:
        matches = sorted(glob.glob(pat), reverse=True)
        if matches:
            return matches[0]
    return None


def _find_posture_script_path() -> Optional[str]:
    """Định vị tệp posture_ai_engine.py cạnh .exe hoặc ở thư mục gốc dự án (cha của dist/)."""
    app_dir = get_app_dir()
    candidates = [
        os.path.join(app_dir, "posture_ai_engine.py"),
        os.path.join(os.path.dirname(app_dir), "posture_ai_engine.py"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


class PostureAIManager:
    """Bộ quản lý vòng đời AI Camera 3D an toàn cho cả bản .py lẫn bản .exe."""

    def __init__(self) -> None:
        self._worker = None
        self._sidecar_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        with self._lock:
            if self._worker is not None and getattr(self._worker, "running", False):
                return True
            if self._sidecar_proc is not None and self._sidecar_proc.poll() is None:
                return True
            return False

    def start(self, force_enable: bool = False) -> bool:
        if "--self-test" in sys.argv:
            return False

        cfg = load_config()
        if not force_enable and not cfg.get("enable_posture_ai", True):
            return False

        if self.is_running():
            return True

        cam_idx = int(cfg.get("camera_index", 0))
        with self._lock:
            # Chế độ 1: Chạy trực tiếp trong tiến trình Python khi không phải bản đóng gói .exe
            if not getattr(sys, "frozen", False) and not _should_use_pythonw_sidecar():
                try:
                    from services.posture_ai_engine import PostureAIServiceWorker

                    self._worker = PostureAIServiceWorker(
                        camera_index=cam_idx,
                        push_via_http=False,
                        frame_callback=lambda jpg, _tel: set_latest_posture_frame(jpg),
                        raw_callback=set_latest_raw_frame,
                    )
                    self._worker.start()
                    safe_log("[AI] Đã bật luồng AI Nhận diện Tư thế 3D trực tiếp trong tiến trình.")
                    return True
                except Exception as exc:
                    safe_log(f"[WARN] Không thể nạp trực tiếp PostureAIServiceWorker: {exc}")
                    self._worker = None

            # Chế độ 2: Bản .exe HOẶC đang chạy bằng python.exe -> gọi tiến trình vệ tinh pythonw posture_ai_engine.py
            py_exe = _find_python_interpreter()
            script_path = _find_posture_script_path()
            if not py_exe or not script_path:
                safe_log("[WARN] Chưa tìm thấy pythonw.exe hoặc posture_ai_engine.py để khởi chạy Sidecar AI.")
                return False

            env = os.environ.copy()
            env["SMARTDESK_APP_DIR"] = get_app_dir()
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
            try:
                self._sidecar_proc = subprocess.Popen(
                    [py_exe, script_path, "--daemon", "--parent-pid", str(os.getpid())],
                    cwd=os.path.dirname(script_path),
                    env=env,
                    creationflags=creation_flags,
                )
                safe_log(f"[AI] Đã khởi chạy Sidecar AI Tư thế 3D (PID={self._sidecar_proc.pid}).")
                return True
            except Exception as exc:
                safe_log(f"[WARN] Lỗi khởi chạy Sidecar AI: {exc}")
                self._sidecar_proc = None
                return False

    def recalibrate(self) -> None:
        request_posture_recalibration()
        with self._lock:
            if self._worker is not None:
                try:
                    self._worker.recalibrate()
                except Exception:
                    pass

    def stop(self) -> None:
        global _latest_ts
        with self._lock:
            if self._worker is not None:
                try:
                    self._worker.stop()
                except Exception:
                    pass
                self._worker = None

            if self._sidecar_proc is not None:
                try:
                    if self._sidecar_proc.poll() is None:
                        self._sidecar_proc.terminate()
                        try:
                            self._sidecar_proc.wait(timeout=1.5)
                        except Exception:
                            self._sidecar_proc.kill()
                except Exception:
                    pass
                self._sidecar_proc = None

        with _frame_lock:
            _latest_ts = 0.0


posture_manager = PostureAIManager()
