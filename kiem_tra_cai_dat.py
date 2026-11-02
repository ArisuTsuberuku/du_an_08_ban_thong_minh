# -*- coding: utf-8 -*-
"""
Kiểm tra cài đặt FocusDesk (kiem_tra_cai_dat.py)

    python kiem_tra_cai_dat.py          -> chỉ kiểm tra, in bảng ✅ / ⚠️ / ❌
    python kiem_tra_cai_dat.py --sua    -> kiểm tra + tự sửa được gì thì sửa (cài gói thiếu, tải mô hình thiếu)

Kiểm tra: phiên bản Python, thư viện, mô hình AI, webcam + quyền camera của Windows, quyền Admin,
kết nối web phụ huynh (cloud_url), cổng 5000, cloudflared, mật khẩu mặc định.
Không gửi dữ liệu gì đi đâu ngoài 1 lần gọi /api/health của web phụ huynh.
"""

import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.abspath(__file__))
FIX = "--sua" in sys.argv
rows: List[Tuple[str, str, str]] = []   # (trạng thái, hạng mục, chi tiết)


def ok(item, detail=""):
    rows.append(("✅", item, detail))


def warn(item, detail=""):
    rows.append(("⚠️", item, detail))


def bad(item, detail=""):
    rows.append(("❌", item, detail))


# (tên import, tên gói pip, bắt buộc?, ghi chú)
PACKAGES = [
    ("flask", "flask", True, "web nội bộ :5000"),
    ("requests", "requests", True, "gọi web phụ huynh"),
    ("psutil", "psutil", True, "quản lý tiến trình"),
    ("numpy", "numpy", True, "xử lý ảnh"),
    ("mediapipe", "mediapipe", True, "AI tư thế (cần >= 1.0)"),
    ("PIL", "pillow", True, "icon ứng dụng, ảnh xem trước"),
    ("win32api", "pywin32", True, "icon, quét ứng dụng, Windows API"),
    ("uiautomation", "uiautomation", True, "đọc link trình duyệt / tab"),
    ("pyautogui", "pyautogui", True, "đóng tab bị chặn"),
    ("pygetwindow", "pygetwindow", True, "cửa sổ đang dùng"),
    ("pygrabber", "pygrabber", False, "đọc tên webcam"),
    ("ai_edge_litert", "ai-edge-litert", False, "nhận diện hành động MoViNet"),
    ("serial", "pyserial", False, "hộp khóa điện thoại ESP32-S3"),
]

MODELS = [
    ("holistic_landmarker.task", True, "https://storage.googleapis.com/mediapipe-models/holistic_landmarker/"
                                       "holistic_landmarker/float16/latest/holistic_landmarker.task"),
    ("efficientdet_lite0.tflite", True, "https://storage.googleapis.com/mediapipe-models/object_detector/"
                                        "efficientdet_lite0/int8/latest/efficientdet_lite0.tflite"),
    ("efficientdet_lite2.tflite", False, "https://storage.googleapis.com/mediapipe-models/object_detector/"
                                         "efficientdet_lite2/int8/latest/efficientdet_lite2.tflite"),
    ("hand_landmarker.task", False, "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
                                    "hand_landmarker/float16/latest/hand_landmarker.task"),
    ("movinet_a0_stream_k600.tflite", False, "https://tfhub.dev/tensorflow/lite-model/movinet/a0/stream/"
                                             "kinetics-600/classification/tflite/float16/1?lite-format=tflite"),
    ("haarcascade_frontalface_default.xml", False, "https://raw.githubusercontent.com/opencv/opencv/4.x/data/"
                                                   "haarcascades/haarcascade_frontalface_default.xml"),
    ("kinetics_600_labels.txt", False, "https://raw.githubusercontent.com/tensorflow/models/master/official/"
                                       "projects/movinet/files/kinetics_600_labels.txt"),
]


def check_python() -> None:
    v = sys.version_info
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    txt = f"{v.major}.{v.minor}.{v.micro} {bits}-bit · {sys.executable}"
    if v < (3, 10) or bits != 64:
        bad("Python", txt + " (cần 3.10+ 64-bit)")
    else:
        ok("Python", txt)


OPENCV_DISTS = ("opencv-python", "opencv-python-headless", "opencv-contrib-python", "opencv-contrib-python-headless")
OPENCV_WANT = os.environ.get("FOCUSDESK_OPENCV_PKG", "opencv-contrib-python>=4.9.0,<5")
_CV2_PROBE = (
    "import json,sys\n"
    "r={}\n"
    "try:\n"
    " import cv2\n"
    " r['file']=getattr(cv2,'__file__',None)\n"
    " r['ver']=getattr(cv2,'__version__','')\n"
    " r['missing']=[a for a in ('VideoCapture','CascadeClassifier','cvtColor','data') if not hasattr(cv2,a)]\n"
    " if hasattr(cv2,'data'):\n"
    "  import os\n"
    "  r['haar']=os.path.exists(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')\n"
    "except Exception as e:\n"
    " r['error']=str(e)[:200]\n"
    "print(json.dumps(r))\n"
)


def _probe_cv2() -> Dict[str, Any]:
    """Thử import cv2 trong TIẾN TRÌNH RIÊNG: nếu phải sửa thì file .pyd không bị khóa bởi chính chương trình này."""
    try:
        out = subprocess.run([sys.executable, "-c", _CV2_PROBE], capture_output=True, text=True, timeout=120)
        return json.loads((out.stdout or "{}").strip().splitlines()[-1])
    except Exception as exc:
        return {"error": f"không chạy được kiểm tra cv2: {exc}"}


def _opencv_dists() -> Dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version as _ver

    found = {}
    for d in OPENCV_DISTS:
        try:
            found[d] = _ver(d)
        except PackageNotFoundError:
            pass
        except Exception:
            pass
    return found


def check_opencv() -> None:
    """OpenCV hay hỏng khi máy cài lẫn nhiều bản (opencv-python + opencv-contrib-python...) hoặc gỡ dở một bản:
    thư mục cv2 còn sót -> "module 'cv2' has no attribute 'CascadeClassifier' / 'VideoCapture'". --sua: gỡ sạch mọi
    bản OpenCV rồi cài lại đúng 1 bản opencv-contrib-python (bản mediapipe cần)."""
    dists = _opencv_dists()
    r = _probe_cv2()
    problems = []
    if len(dists) > 1:
        problems.append("cài lẫn " + ", ".join(f"{k} {v}" for k, v in dists.items()))
    if not dists:
        problems.append("chưa cài OpenCV")
    if r.get("error"):
        problems.append("import cv2 lỗi: " + r["error"])
    elif not r.get("file"):
        problems.append("thư mục cv2 bị hỏng (gỡ cài dở)")
    if r.get("missing"):
        problems.append("cv2 thiếu " + ", ".join(r["missing"]))
    elif "haar" in r and not r["haar"]:
        problems.append("thiếu file haarcascade (bộ nhận mặt)")
    if not problems:
        ok("Gói opencv-contrib-python", f"{r.get('ver', '')} · webcam + nhận mặt")
        return
    if not FIX:
        bad("Gói OpenCV (cv2)", "; ".join(problems)[:160] + " — chạy KIEM_TRA.bat --sua để cài lại sạch")
        return
    print("→ Đang sửa OpenCV: gỡ mọi bản rồi cài lại opencv-contrib-python (2-5 phút)…")
    pip = [sys.executable, "-m", "pip", "--disable-pip-version-check"]
    subprocess.call(pip + ["uninstall", "-y", *OPENCV_DISTS])
    # Xóa thư mục cv2 sót lại sau khi gỡ (nguyên nhân chính của lỗi thiếu thuộc tính)
    try:
        import site

        for sp in set(site.getsitepackages() + [site.getusersitepackages()]):
            left = os.path.join(sp, "cv2")
            if os.path.isdir(left):
                import shutil

                shutil.rmtree(left, ignore_errors=True)
                print(f"   đã xóa thư mục sót: {left}")
    except Exception:
        pass
    subprocess.call(pip + ["install", "--no-cache-dir", "--force-reinstall", OPENCV_WANT])
    r2 = _probe_cv2()
    if r2.get("file") and not r2.get("missing") and not r2.get("error"):
        ok("Gói opencv-contrib-python", f"{r2.get('ver', '')} · đã cài lại sạch")
    else:
        bad("Gói OpenCV (cv2)", f"vẫn lỗi sau khi cài lại: {r2}"[:160])


def check_packages() -> None:
    missing = []
    for mod, pip_name, required, note in PACKAGES:
        try:
            m = importlib.import_module(mod)
            try:
                from importlib.metadata import version as _ver

                ver = _ver(pip_name)
            except Exception:
                ver = ""
            if mod == "mediapipe" and ver and int(ver.split(".")[0]) < 1:
                warn(f"Gói {pip_name}", f"{ver} — nên nâng lên >= 1.0 (blendshape mắt, nhận diện đồ vật)")
            else:
                ok(f"Gói {pip_name}", f"{ver} · {note}".strip(" ·"))
        except Exception as exc:
            (bad if required else warn)(f"Gói {pip_name}", f"chưa cài ({note}) — {str(exc)[:60]}")
            missing.append(pip_name)
    if missing and FIX:
        print(f"→ Đang cài: {' '.join(missing)}")
        subprocess.call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *missing])


def check_models() -> None:
    mdir = os.path.join(ROOT, "models")
    os.makedirs(mdir, exist_ok=True)
    for name, required, url in MODELS:
        path = os.path.join(mdir, name)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        if size > 1000:
            ok(f"Mô hình {name}", f"{size / 1e6:.1f} MB")
            continue
        if FIX:
            try:
                import requests

                print(f"→ Đang tải {name}…")
                r = requests.get(url, timeout=120)
                r.raise_for_status()
                with open(path, "wb") as fh:
                    fh.write(r.content)
                ok(f"Mô hình {name}", f"vừa tải {len(r.content) / 1e6:.1f} MB")
                continue
            except Exception as exc:
                (bad if required else warn)(f"Mô hình {name}", f"tải lỗi: {str(exc)[:60]}")
                continue
        (bad if required else warn)(f"Mô hình {name}", "thiếu — chạy lại với --sua để tải")


def check_admin() -> None:
    try:
        import ctypes

        if ctypes.windll.shell32.IsUserAnAdmin():
            ok("Quyền Administrator", "có (khóa Task Manager, chặn DNS hoạt động)")
        else:
            warn("Quyền Administrator", "không — mở bằng MO_FOCUSDESK.bat (tự xin quyền) để chặn DNS / khóa Task Manager")
    except Exception:
        warn("Quyền Administrator", "không xác định (không phải Windows?)")


def check_camera() -> None:
    try:
        sys.path.insert(0, ROOT)
        from services.camera_setup import list_cameras

        cams = list_cameras({})
        real = [c["name"] for c in cams if not c["virtual"]]
        if real:
            ok("Webcam", ", ".join(real) + (f" · {len(real)} camera → có thể dùng camera mặt bàn" if len(real) > 1 else ""))
        else:
            bad("Webcam", "không tìm thấy webcam thật")
    except Exception as exc:
        warn("Webcam", f"không liệt kê được: {str(exc)[:60]}")
    if sys.platform.startswith("win"):
        try:
            import winreg

            base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
            vals = {}
            for sub in ("", r"\NonPackaged"):
                try:
                    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + sub)
                    vals[sub or "all"] = winreg.QueryValueEx(k, "Value")[0]
                    winreg.CloseKey(k)
                except OSError:
                    pass
            denied = [k for k, v in vals.items() if str(v).lower() == "deny"]
            if denied:
                bad("Quyền camera Windows", "đang TẮT cho ứng dụng desktop — Cài đặt → Quyền riêng tư → Camera → "
                                           "bật 'Cho phép ứng dụng desktop truy cập camera'")
            else:
                ok("Quyền camera Windows", "cho phép")
        except Exception:
            pass


def check_cloud() -> None:
    path = os.path.join(ROOT, "secrets.local.json")
    url = ""
    try:
        with open(path, encoding="utf-8") as fh:
            url = (json.load(fh).get("cloud_url") or "").rstrip("/")
    except Exception:
        pass
    url = os.environ.get("FOCUSDESK_CLOUD_URL", url)
    if not url:
        warn("Web phụ huynh (cloud)", "chưa cấu hình cloud_url trong secrets.local.json")
        return
    try:
        import requests

        h = requests.get(url + "/api/health", timeout=8).json()
        ok("Web phụ huynh (cloud)", f"{url} · db={h.get('db')} · telegram={h.get('telegram')}")
    except Exception as exc:
        bad("Web phụ huynh (cloud)", f"{url} không phản hồi: {str(exc)[:60]}")
    ident = os.path.join(ROOT, "device_identity.json")
    if os.path.exists(ident):
        try:
            with open(ident, encoding="utf-8") as fh:
                d = json.load(fh)
            ok("Ghép máy", f"mã máy {d.get('device_id')} · {'đã ghép phụ huynh' if d.get('paired') else 'CHƯA ghép'}")
        except Exception:
            pass
    else:
        warn("Ghép máy", "chưa đăng ký — mở app, vào Phụ huynh → Điện thoại phụ huynh để lấy mã ghép")


def check_port() -> None:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 5000))
        ok("Cổng 5000", "trống, sẵn sàng")
    except OSError:
        warn("Cổng 5000", "đang có chương trình dùng (bình thường nếu app/trang_web đang chạy)")
    finally:
        s.close()


def check_cloudflared() -> None:
    exe = shutil.which("cloudflared")
    if not exe:
        for p in (r"C:\Program Files (x86)\cloudflared\cloudflared.exe", r"C:\Program Files\cloudflared\cloudflared.exe",
                  os.path.join(ROOT, "tools", "cloudflared.exe")):
            if os.path.exists(p):
                exe = p
                break
    if exe:
        ok("cloudflared", f"{exe} (xem camera từ xa qua Internet)")
    else:
        warn("cloudflared", "chưa cài — chỉ cần nếu muốn xem camera từ xa ngoài mạng nhà: "
                            "winget install --id Cloudflare.cloudflared")


def check_phone_box() -> None:
    try:
        with open(os.path.join(ROOT, "shared_config.json"), encoding="utf-8") as fh:
            enabled = bool(json.load(fh).get("phone_box_enabled"))
    except Exception:
        enabled = False
    try:
        sys.path.insert(0, ROOT)
        from services.phone_box import find_box_ports

        ports = find_box_ports()
    except Exception:
        ports = []
    esp = [p for p in ports if p.get("vid") in (0x303A, 0x1A86, 0x10C4, 0x0403)]
    if esp:
        ok("Hộp điện thoại (USB)", ", ".join(f"{p['port']} {p['desc']}" for p in esp[:3]))
    elif enabled:
        warn("Hộp điện thoại (USB)", "đã bật nhưng chưa thấy ESP32 cắm vào cổng USB")
    else:
        ok("Hộp điện thoại (USB)", "chưa dùng (tùy chọn)")


def check_config() -> None:
    try:
        with open(os.path.join(ROOT, "shared_config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        warn("Cấu hình", "chưa có shared_config.json (sẽ tự tạo khi mở app lần đầu)")
        return
    defaults = {"student": "hocsinh123", "parent": "phuhuynh123", "admin": "admin123"}
    still = [k for k, v in defaults.items() if (cfg.get("passwords") or {}).get(k) == v]
    if still:
        warn("Mật khẩu", "đang dùng mật khẩu mặc định cho: " + ", ".join(still) + " — đổi trong Admin → Mật khẩu")
    else:
        ok("Mật khẩu", "đã đổi khỏi mặc định")
    apps = cfg.get("allowed_apps") or []
    missing = [a.get("name") for a in apps if a.get("path") and os.path.sep in a["path"] and not os.path.exists(a["path"])]
    if missing:
        warn("Ứng dụng cho phép", "đường dẫn không tồn tại: " + ", ".join(missing[:4]))
    else:
        ok("Ứng dụng cho phép", f"{len(apps)} ứng dụng")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("Đang kiểm tra cài đặt FocusDesk" + (" (chế độ tự sửa)" if FIX else "") + "…\n")
    for fn in (check_python, check_opencv, check_packages, check_models, check_admin, check_camera, check_cloud, check_port,
               check_cloudflared, check_phone_box, check_config):
        try:
            fn()
        except Exception as exc:
            warn(fn.__name__, f"lỗi khi kiểm tra: {exc}")
    w = max(len(r[1]) for r in rows)
    for st, item, detail in rows:
        print(f"{st}  {item.ljust(w)}  {detail}")
    n_bad = sum(1 for r in rows if r[0] == "❌")
    n_warn = sum(1 for r in rows if r[0] == "⚠️")
    print(f"\nKết quả: {len(rows) - n_bad - n_warn} đạt · {n_warn} cảnh báo · {n_bad} lỗi")
    if n_bad and not FIX:
        print("→ Chạy:  python kiem_tra_cai_dat.py --sua   để tự cài gói / tải mô hình còn thiếu.")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
