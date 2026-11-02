# -*- coding: utf-8 -*-
"""
Dọn ứng dụng chạy nền khi vào chế độ Học sinh (services/process_cleanup.py).

Khi học sinh đăng nhập: tắt mọi ứng dụng của phiên người dùng hiện tại (cửa sổ + chạy ngầm/khay hệ thống), TRỪ:
  1. Tiến trình lõi của Windows (shell, dịch vụ, bảo mật, bộ gõ ctfmon…)
  2. Bộ gõ tiếng Việt (EVKey, UniKey, OpenKey, GoTiengViet…), đồng bộ đám mây (OneDrive, Google Drive, Dropbox)
  3. Phần mềm driver / phần cứng mặc định (AMD Adrenalin, NVIDIA, Intel, Realtek, touchpad, phần mềm hãng laptop)
  4. Phần mềm diệt virus
  5. Chính FocusDesk (python/pythonw của thư mục phần mềm, cloudflared, FocusDeskLauncher)
  6. Ứng dụng học tập phụ huynh đã cấp (allowed_apps) và danh sách Admin thêm (shared_config "background_allow")
Dịch vụ Windows (phiên 0) không bao giờ bị đụng tới.

Cách tắt: ứng dụng có cửa sổ -> gửi WM_CLOSE (cho cơ hội tự đóng), chờ vài giây -> buộc tắt cả cây tiến trình.
Ứng dụng chạy ngầm không cửa sổ -> buộc tắt ngay.
Có chế độ XEM TRƯỚC (dry_run) để Admin xem danh sách sẽ bị tắt mà không tắt gì.
Trong lúc học: cứ 60 giây quét lại, chỉ tắt tiến trình MỚI mở sau lúc vào học (không đụng lại thứ đã xét).
"""

import fnmatch
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from core.config_manager import get_app_dir, load_config, safe_log, update_runtime_state

IS_WINDOWS = sys.platform.startswith("win")

WINDOWS_CORE = {
    "system", "system idle process", "registry", "memory compression", "secure system", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe", "fontdrvhost.exe",
    "dwm.exe", "explorer.exe", "sihost.exe", "taskhostw.exe", "ctfmon.exe", "runtimebroker.exe", "searchhost.exe",
    "searchapp.exe", "searchindexer.exe", "searchprotocolhost.exe", "searchfilterhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "shellhost.exe", "textinputhost.exe", "applicationframehost.exe", "lockapp.exe",
    "logonui.exe", "userinit.exe", "conhost.exe", "openconsole.exe", "dllhost.exe", "wmiprvse.exe", "audiodg.exe",
    "spoolsv.exe", "wudfhost.exe", "consent.exe", "backgroundtaskhost.exe", "useroobebroker.exe", "sgrmbroker.exe",
    "unsecapp.exe", "dashost.exe", "sppsvc.exe", "smartscreen.exe", "securityhealthsystray.exe",
    "securityhealthservice.exe", "msmpeng.exe", "nissrv.exe", "mpdefendercoreservice.exe", "mpcmdrun.exe",
    "msiexec.exe", "trustedinstaller.exe", "tiworker.exe", "taskmgr.exe", "credentialuibroker.exe",
    "credentialenrollmentmanager.exe", "rundll32.exe", "msedgewebview2.exe", "crossdeviceservice.exe",
    "windowsinternal.composableshell.experiences.textinput.inputapp.exe", "inputapp.exe", "tabtip.exe", "osk.exe",
    "narrator.exe", "magnify.exe", "atbroker.exe", "utilman.exe", "wlanext.exe", "midisrv.exe", "lsm.exe",
    "ngciso.exe", "vmmem", "vmmemws", "vmcompute.exe", "wsl.exe", "wslhost.exe", "wslservice.exe", "cmd.exe",
    "powershell.exe", "pwsh.exe", "windowsterminal.exe",
}
INPUT_SYNC = {
    "evkey*.exe", "unikey*.exe", "openkey*.exe", "gotiengviet*.exe", "vietkey*.exe", "laban*key*.exe",
    "onedrive*.exe", "filecoauth.exe", "filesyncconfig.exe", "googledrivefs.exe", "googledrivesync.exe",
    "dropbox*.exe", "icloud*.exe",
}
HARDWARE_VENDOR = {
    # AMD / Radeon Adrenalin
    "radeonsoftware.exe", "amdrsserv.exe", "amdrssrcext.exe", "cncmd.exe", "atieclxx.exe", "atiesrxx.exe",
    "amdow.exe", "amddvr.exe", "amdfendrsr.exe", "rsservcmd.exe", "amd*.exe",
    # NVIDIA
    "nvcontainer.exe", "nvdisplay.container.exe", "nvcplui.exe", "nvidia share.exe", "nvidia web helper.exe",
    "nvsphelper64.exe", "nvidia overlay.exe", "nvidia app.exe", "nvtelemetrycontainer.exe", "nvbroadcast*.exe",
    # Intel
    "igfx*.exe", "igcc*.exe", "intel*.exe", "onecontrolcenter*.exe",
    # Âm thanh / touchpad / bluetooth / phần mềm hãng laptop
    "rtk*.exe", "realtek*.exe", "nahimic*.exe", "dolby*.exe", "waves*.exe", "maxxaudio*.exe", "dts*.exe",
    "syntp*.exe", "etdctrl*.exe", "etd*.exe", "elan*.exe", "asus*.exe", "armourycrate*.exe", "msi*.exe",
    "mysticlight*.exe", "lenovo*.exe", "hp*.exe", "dell*.exe", "acer*.exe", "razer*.exe", "logi*.exe",
    "btservice*.exe", "ibtsiva.exe", "bluetooth*.exe",
    # Chuột / bàn phím / tai nghe gaming (giữ cấu hình DPI, macro, đèn), bút vẽ / bảng vẽ cho môn Mỹ thuật
    "ngenuity*.exe", "hyperx*.exe", "icue*.exe", "corsair*.exe", "steelseries*.exe", "lghub*.exe", "wacom*.exe",
    "wtablet*.exe", "pentablet*.exe", "xppen*.exe", "huion*.exe", "tablet*.exe",
}
ANTIVIRUS = {
    "avp*.exe", "avast*.exe", "avg*.exe", "afwserv.exe", "bkav*.exe", "egui.exe", "ekrn.exe", "mcshield.exe",
    "mcafee*.exe", "norton*.exe", "ns.exe", "bdagent.exe", "bdservicehost.exe", "mbam*.exe", "kav*.exe",
    "ksde*.exe", "microsoftsecurityapp.exe", "securityhealth*.exe", "omapsvc*.exe",
}
# Giữ theo THƯ MỤC CÀI (bắt được cả tiến trình phụ tên lạ của hãng: MSI Center "DCv2.exe", AMD "CPUMetricsServer.exe"…)
PROTECTED_PATH_HINTS = (
    "\\windows\\systemapps\\", "\\windows\\system32\\", "\\windows\\syswow64\\", "\\windows defender",
    "\\amd\\", "\\ati technologies\\", "\\nvidia", "\\intel\\", "\\realtek", "\\msi\\",
    "micro-starinternation", "\\asus", "\\lenovo", "\\hewlett-packard", "\\hp\\", "\\dell\\", "\\acer\\",
    "\\huawei\\pcmanager", "\\lghub", "\\logitech", "\\razer", "\\synaptics", "\\elantech",
    "\\kaspersky", "\\avast", "\\avg\\", "\\eset", "\\bkav", "\\mcafee", "\\norton", "\\bitdefender",
    "\\malwarebytes", "\\microsoft onedrive", "\\evkey", "\\unikey",
    "\\hyperx", "\\ngenuity", "\\corsair", "\\steelseries", "\\wacom", "\\tablet", "\\xp-pen", "\\huion",
)
FOCUSDESK_OWN = {"focusdesklauncher.exe", "smartdesklauncher.exe", "cloudflared.exe"}


def _session_id(pid: int) -> Optional[int]:
    if not IS_WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes

    sid = wintypes.DWORD()
    if ctypes.windll.kernel32.ProcessIdToSessionId(wintypes.DWORD(pid), ctypes.byref(sid)):
        return int(sid.value)
    return None


def _visible_windows_by_pid() -> Dict[int, List[int]]:
    out: Dict[int, List[int]] = {}
    if not IS_WINDOWS:
        return out
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _l):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.setdefault(int(pid.value), []).append(hwnd)
        return True

    user32.EnumWindows(_cb, 0)
    return out


def _matches(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name, p.lower()) for p in patterns)


def protected_patterns(extra_allowed: Iterable[str] = ()) -> Set[str]:
    cfg = load_config()
    pats = set(WINDOWS_CORE) | INPUT_SYNC | HARDWARE_VENDOR | ANTIVIRUS | FOCUSDESK_OWN
    pats |= {str(a.get("exe_name", "")).lower() for a in cfg.get("allowed_apps", []) if a.get("exe_name")}
    pats |= {str(x).strip().lower() for x in cfg.get("background_allow", []) if str(x).strip()}
    pats |= {str(x).strip().lower() for x in extra_allowed if str(x).strip()}
    return pats


def _is_focusdesk_python(proc) -> bool:
    """python/pythonw đang chạy mã của thư mục FocusDesk (app chính, AI sidecar, web nội bộ) -> giữ lại."""
    try:
        cmd = " ".join(proc.cmdline()).lower()
    except Exception:
        return True   # không đọc được -> an toàn: giữ lại
    app_dir = get_app_dir().lower()
    return app_dir in cmd or any(k in cmd for k in ("he_thong_quan_ly_3_cap", "posture_ai_engine", "trang_web.py",
                                                   "giam_sat.pyw", "kiem_tra_cai_dat"))


def find_targets(extra_allowed: Iterable[str] = (), newer_than: float = 0.0) -> List[Dict[str, Any]]:
    """Danh sách tiến trình sẽ bị tắt (đã gộp theo tên .exe). Không tắt gì."""
    import psutil

    me = os.getpid()
    try:
        ancestors = {p.pid for p in psutil.Process(me).parents()} | {me}
    except Exception:
        ancestors = {me}
    my_session = _session_id(me)
    my_user = None
    try:
        my_user = psutil.Process(me).username()
    except Exception:
        pass
    pats = protected_patterns(extra_allowed)
    wins = _visible_windows_by_pid()
    groups: Dict[str, Dict[str, Any]] = {}
    for p in psutil.process_iter(["pid", "name", "username", "create_time", "exe"]):
        try:
            pid, name = p.info["pid"], (p.info["name"] or "").lower()
            if pid in ancestors or pid <= 4 or not name:
                continue
            if newer_than and (p.info.get("create_time") or 0) < newer_than:
                continue
            if IS_WINDOWS:
                sid = _session_id(pid)
                if sid is None or sid == 0 or (my_session is not None and sid != my_session):
                    continue          # dịch vụ / phiên khác -> không đụng
            if my_user and p.info.get("username") and p.info["username"] != my_user:
                continue
            if _matches(name, pats):
                continue
            exe = (p.info.get("exe") or "").lower()
            if any(h in exe for h in PROTECTED_PATH_HINTS):
                continue              # thành phần Windows / driver / phần mềm hãng / diệt virus -> giữ
            if name in ("python.exe", "pythonw.exe", "py.exe", "pyw.exe") and _is_focusdesk_python(p):
                continue
            g = groups.setdefault(name, {"name": name, "pids": [], "windows": 0, "exe": p.info.get("exe") or ""})
            g["pids"].append(pid)
            g["windows"] += len(wins.get(pid, []))
        except Exception:
            continue
    return sorted(groups.values(), key=lambda g: (-g["windows"], g["name"]))


def close_targets(targets: List[Dict[str, Any]], grace_s: float = 4.0) -> Dict[str, Any]:
    """Đóng nhẹ (WM_CLOSE) app có cửa sổ, chờ grace_s, rồi buộc tắt mọi tiến trình còn sống (kèm tiến trình con)."""
    import psutil

    WM_CLOSE = 0x0010
    wins = _visible_windows_by_pid()
    if IS_WINDOWS:
        import ctypes

        for g in targets:
            for pid in g["pids"]:
                for hwnd in wins.get(pid, []):
                    try:
                        ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                    except Exception:
                        pass
        if any(g["windows"] for g in targets):
            time.sleep(grace_s)
    procs = []
    for g in targets:
        for pid in g["pids"]:
            try:
                p = psutil.Process(pid)
                procs.extend(p.children(recursive=True))
                procs.append(p)
            except Exception:
                pass
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    _gone, alive = psutil.wait_procs(procs, timeout=3)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass
    closed = [g["name"] for g in targets]
    return {"closed": closed, "count": len(closed), "t": int(time.time())}


_sweeper: Dict[str, Any] = {"stop": None}


def on_student_start(extra_allowed: Iterable[str] = (), notify: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Gọi khi vào chế độ Học sinh: tắt app chạy nền theo cấu hình, rồi bật quét định kỳ app mới mở."""
    cfg = load_config()
    if not cfg.get("student_close_background", True) or not IS_WINDOWS:
        return {"skipped": True}
    t0 = time.time()
    extra = list(extra_allowed)
    try:
        res = close_targets(find_targets(extra))
    except Exception as exc:
        safe_log(f"[DỌN NỀN] Lỗi: {exc}")
        res = {"closed": [], "count": 0, "error": str(exc)}
    update_runtime_state(background_cleanup=res)
    if res.get("count"):
        msg = f"🧹 Vào học: đã tắt {res['count']} ứng dụng chạy nền ({', '.join(res['closed'][:8])}" + \
              ("…)" if res["count"] > 8 else ")")
        safe_log("[DỌN NỀN] " + msg)
        if notify:
            notify(msg)
    stop_sweeper()
    if cfg.get("student_bg_sweep", True):
        ev = threading.Event()
        _sweeper["stop"] = ev

        def loop() -> None:
            while not ev.wait(60):
                try:
                    tg = find_targets(extra, newer_than=t0)
                    if tg:
                        r = close_targets(tg)
                        safe_log(f"[DỌN NỀN] Trong giờ học tắt thêm: {', '.join(r['closed'])}")
                        if notify:
                            notify(f"🧹 Đã tắt ứng dụng chạy nền mới mở trong giờ học: {', '.join(r['closed'][:6])}")
                except Exception as exc:
                    safe_log(f"[DỌN NỀN] Lỗi quét định kỳ: {exc}")

        threading.Thread(target=loop, name="BackgroundSweeper", daemon=True).start()
    return res


def stop_sweeper() -> None:
    ev = _sweeper.get("stop")
    if ev is not None:
        ev.set()
    _sweeper["stop"] = None
