# -*- coding: utf-8 -*-
"""
Bộ chọn & mở Webcam vật lý ổn định trên Windows (services/camera_selector.py)

Vì sao cần module này:
  - cv2.VideoCapture(index) chỉ trả về True/False, không cho biết LÝ DO thất bại.
    Trên máy thực tế, nguyên nhân phổ biến nhất lại là phần mềm diệt virus
    (Kaspersky / ESET / Bitdefender "Webcam Protection") hoặc quyền riêng tư Windows
    chặn ứng dụng python.exe -> DirectShow trả về E_ACCESSDENIED (0x80070005).
  - Thứ tự index của Camera ảo (Imaging Edge, OBS Virtual, DroidCam...) có thể đứng trước Webcam thật.

Chức năng:
  1. Liệt kê tên thiết bị DirectShow bằng pygrabber (thứ tự trùng với index của cv2.CAP_DSHOW).
  2. Xếp hạng ứng viên: khớp `camera_preferred_names` > `camera_index` > camera thật khác; bỏ `camera_ignored_names`.
  3. Thử lần lượt backend DSHOW -> MSMF, chỉ chấp nhận khi ĐỌC ĐƯỢC KHUNG HÌNH THẬT (không chỉ isOpened()).
  4. Khi thất bại: chẩn đoán mã lỗi COM để báo đúng nguyên nhân (ACCESS_DENIED / BUSY / NOT_FOUND / OPEN_FAILED).
"""

import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2

try:  # Giảm log rác của OpenCV khi dò nhiều index/backend
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except Exception:
    pass

IS_WINDOWS = sys.platform.startswith("win")

# Mã HRESULT thường gặp khi bind/chạy filter DirectShow của Webcam
HRESULT_ACCESS_DENIED = -2147024891      # 0x80070005 E_ACCESSDENIED (AV Webcam Protection / Privacy)
HRESULT_SHARING_VIOLATION = -2147024864  # 0x80070020 thiết bị đang bị ứng dụng khác giữ
HRESULT_NO_RESOURCES = -2147023446       # 0x800705AA thiết bị bận (Zoom/Zalo/Teams...)
HRESULT_DEVICE_NOT_CONNECTED = -2147023729  # 0x8007048F

AV_PROCESS_HINTS = {
    "avp.exe": "Kaspersky",
    "avpui.exe": "Kaspersky",
    "ekrn.exe": "ESET",
    "egui.exe": "ESET",
    "bdagent.exe": "Bitdefender",
    "vsserv.exe": "Bitdefender",
    "avastui.exe": "Avast",
    "avgui.exe": "AVG",
    "nortonsecurity.exe": "Norton",
}

REASON_TEXT_VI = {
    "OK": "Webcam hoạt động bình thường.",
    "NOT_FOUND": "Windows không tìm thấy Webcam nào (chưa cắm / bị tắt bằng phím Fn / driver lỗi).",
    "ONLY_VIRTUAL": "Chỉ thấy Camera ảo trong danh sách bỏ qua, không có Webcam thật.",
    "ACCESS_DENIED": "Webcam bị CHẶN QUYỀN truy cập (phần mềm diệt virus Webcam Protection hoặc Quyền riêng tư Camera của Windows).",
    "BUSY": "Webcam đang bị ứng dụng khác chiếm (Zoom / Zalo / Teams / Camera...). Hãy tắt ứng dụng đó.",
    "OPEN_FAILED": "Có Webcam nhưng OpenCV không mở được (thử rút/cắm lại hoặc khởi động lại máy).",
    "NO_FRAME": "Mở được Webcam nhưng không đọc được khung hình (driver treo / nắp che camera).",
}

REASON_TEXT_ASCII = {  # Dùng cho cv2.putText (không vẽ được tiếng Việt có dấu)
    "NOT_FOUND": "Khong tim thay Webcam (chua cam / tat bang phim Fn / loi driver)",
    "ONLY_VIRTUAL": "Chi co Camera ao, khong co Webcam that",
    "ACCESS_DENIED": "Webcam bi CHAN QUYEN (Antivirus Webcam Protection / Privacy)",
    "BUSY": "Webcam dang bi ung dung khac chiem (Zoom/Zalo/Teams...)",
    "OPEN_FAILED": "Co Webcam nhung OpenCV khong mo duoc",
    "NO_FRAME": "Mo duoc Webcam nhung khong doc duoc khung hinh",
}


def _com_init() -> None:
    """Khởi tạo COM cho luồng hiện tại (pygrabber dùng comtypes, luồng nền bắt buộc phải CoInitialize)."""
    if not IS_WINDOWS:
        return
    try:
        import comtypes  # type: ignore

        comtypes.CoInitialize()
    except Exception:
        pass


def list_dshow_devices() -> List[str]:
    """Danh sách tên Webcam DirectShow theo đúng thứ tự index của cv2.CAP_DSHOW."""
    if not IS_WINDOWS:
        return []
    _com_init()
    try:
        from pygrabber.dshow_graph import FilterGraph  # type: ignore

        return list(FilterGraph().get_input_devices())
    except Exception:
        return []


def _match_any(name: str, patterns: List[str]) -> bool:
    low = (name or "").lower()
    return any(p and p.lower() in low for p in patterns)


def rank_camera_candidates(
    devices: List[str],
    preferred: List[str],
    ignored: List[str],
    fallback_index: int = 0,
) -> List[int]:
    """Trả về danh sách index Webcam theo thứ tự ưu tiên, đã loại Camera ảo."""
    if not devices:
        order = [fallback_index, 0, 1, 2]
        return list(dict.fromkeys(i for i in order if i >= 0))

    real = [i for i, n in enumerate(devices) if not _match_any(n, ignored)]
    pref = [i for i in real if _match_any(devices[i], preferred)]
    ranked = list(pref)
    if fallback_index in real and fallback_index not in ranked:
        ranked.append(fallback_index)
    ranked += [i for i in real if i not in ranked]
    return ranked


def probe_dshow_access(index: int) -> Tuple[str, str]:
    """
    Bind trực tiếp filter DirectShow để lấy mã lỗi COM thật sự (OpenCV nuốt mất lỗi này).
    Trả về (reason_code, detail).
    """
    if not IS_WINDOWS:
        return "OPEN_FAILED", "Không phải Windows"
    _com_init()
    try:
        from pygrabber.dshow_graph import FilterGraph  # type: ignore

        g = FilterGraph()
        g.add_video_input_device(index)
        return "OK", "Bind DirectShow thành công"
    except Exception as exc:
        hr = None
        args = getattr(exc, "args", ())
        if args and isinstance(args[0], int):
            hr = args[0]
        hr = getattr(exc, "hresult", hr)
        if hr == HRESULT_ACCESS_DENIED:
            return "ACCESS_DENIED", f"HRESULT 0x80070005 Access is denied ({exc})"
        if hr in (HRESULT_SHARING_VIOLATION, HRESULT_NO_RESOURCES):
            return "BUSY", f"HRESULT {hr & 0xFFFFFFFF:#010x} ({exc})"
        if hr == HRESULT_DEVICE_NOT_CONNECTED:
            return "NOT_FOUND", f"HRESULT 0x8007048F ({exc})"
        return "OPEN_FAILED", str(exc)


def detect_antivirus() -> List[str]:
    """Phát hiện phần mềm diệt virus đang chạy (thường có tính năng chặn Webcam)."""
    found = []
    try:
        import psutil

        for p in psutil.process_iter(["name"]):
            vendor = AV_PROCESS_HINTS.get((p.info.get("name") or "").lower())
            if vendor and vendor not in found:
                found.append(vendor)
    except Exception:
        pass
    return found


def _backend_list() -> List[Tuple[str, int]]:
    if IS_WINDOWS:
        return [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF)]
    return [("ANY", cv2.CAP_ANY)]


def _try_open(index: int, backend: int, width: int, height: int, warmup_s: float = 1.5):
    """Mở 1 tổ hợp (index, backend) và chỉ trả cap khi đọc được khung hình thật."""
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return None, "OPEN_FAILED"
    if backend == getattr(cv2, "CAP_DSHOW", -1):
        # MJPG cho phép webcam USB chạy 720p/1080p ở 30fps (YUY2 thường tụt còn 5-10fps)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    deadline = time.time() + warmup_s
    while time.time() < deadline:
        ok, frame = cap.read()
        if ok and frame is not None and frame.size > 0:
            return cap, "OK"
        time.sleep(0.05)
    cap.release()
    return None, "NO_FRAME"


def build_diagnosis(reason: str, devices: List[str], detail: str = "") -> Dict[str, Any]:
    hint = ""
    av = detect_antivirus() if reason in ("ACCESS_DENIED", "OPEN_FAILED") else []
    if reason == "ACCESS_DENIED":
        exe = sys.executable
        if av:
            hint = (
                f"Đang chạy {', '.join(av)}. Mở {av[0]} -> Cài đặt -> Bảo vệ Webcam (Webcam Protection) / "
                f"Quyền ứng dụng: cho phép '{exe}' (và pythonw.exe, SmartDeskLauncher.exe) truy cập Webcam."
            )
        else:
            hint = (
                "Mở Windows Settings -> Privacy & security -> Camera: bật 'Camera access' và "
                "'Let desktop apps access your camera'."
            )
        if exe.lower().endswith("python.exe"):
            # Đã gặp thực tế (MSI Bravo 15 + Kaspersky): python.exe bị từ chối nhưng pythonw.exe mở Webcam bình thường
            hint += " Mẹo: thử chạy bằng pythonw.exe (cùng thư mục với python.exe)."
    elif reason == "BUSY":
        hint = "Tắt Zoom / Zalo Call / Teams / ứng dụng Camera rồi bật lại AI Camera."
    elif reason == "NOT_FOUND":
        hint = "Kiểm tra phím tắt Webcam (MSI: Fn+F6) và Device Manager -> Cameras."
    return {
        "ok": reason == "OK",
        "reason": reason,
        "message": REASON_TEXT_VI.get(reason, reason),
        "message_ascii": REASON_TEXT_ASCII.get(reason, reason),
        "hint": hint,
        "detail": detail,
        "devices": devices,
        "antivirus": av,
        "python_exe": sys.executable,
    }


def open_best_camera(
    cfg: Optional[Dict[str, Any]] = None,
    fallback_index: int = 0,
    width: int = 640,
    height: int = 480,
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """
    Mở Webcam vật lý tốt nhất. Trả về (cap | None, info_dict).
    info_dict luôn có: ok, reason, message, hint, devices; khi ok thêm index, name, backend.
    """
    cfg = cfg or {}
    preferred = list(cfg.get("camera_preferred_names") or [])
    ignored = list(cfg.get("camera_ignored_names") or [])
    try:
        fallback_index = int(cfg.get("camera_index", fallback_index))
    except (TypeError, ValueError):
        pass

    devices = list_dshow_devices()
    candidates = rank_camera_candidates(devices, preferred, ignored, fallback_index)
    if devices and not candidates:
        return None, build_diagnosis("ONLY_VIRTUAL", devices)

    last_reason = "NOT_FOUND" if (IS_WINDOWS and not devices) else "OPEN_FAILED"
    for idx in candidates:
        for be_name, be in _backend_list():
            cap, why = _try_open(idx, be, width, height)
            if cap is not None:
                info = build_diagnosis("OK", devices)
                info.update(
                    index=idx,
                    name=devices[idx] if idx < len(devices) else f"Camera #{idx}",
                    backend=be_name,
                )
                return cap, info
            if why == "NO_FRAME":
                last_reason = "NO_FRAME"

    # Không mở được -> lấy mã lỗi COM thật để báo đúng nguyên nhân
    detail = ""
    if devices and candidates:
        code, detail = probe_dshow_access(candidates[0])
        if code != "OK":
            last_reason = code
    return None, build_diagnosis(last_reason, devices, detail)
