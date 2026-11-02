# -*- coding: utf-8 -*-
"""
Quản lý cấu hình dùng chung (shared_config.json) và trạng thái thời gian thực (runtime_state.json).
- Định vị chính xác thư mục chạy kể cả khi đóng gói thành file .exe (PyInstaller --onefile)
  hoặc khởi chạy từ Windows Task Scheduler.
- Đọc/ghi an toàn đa luồng (RLock + cơ chế retry chống khóa file tạm thời của OneDrive/Windows).
"""

import copy
import json
import os
import sys
import threading
import time
from typing import Any, Dict

_IO_LOCK = threading.RLock()


def configure_utf8_stdio() -> None:
    """Cấu hình UTF-8 an toàn cho stdout/stderr trên Windows và chống lỗi NoneType khi đóng gói --noconsole."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


configure_utf8_stdio()


def safe_log(message: str) -> None:
    """In log an toàn không bao giờ văng lỗi cp1252 UnicodeEncodeError hoặc lỗi stdout=None ở bản --noconsole."""
    try:
        if sys.stdout is not None:
            print(message)
    except Exception:
        pass


def get_app_dir() -> str:
    """Trả về thư mục gốc chứa file .exe (khi đã đóng gói) hoặc thư mục gốc dự án (khi chạy .py)."""
    env_dir = os.environ.get("FOCUSDESK_APP_DIR", "").strip()
    if env_dir and os.path.isdir(env_dir):
        return os.path.abspath(env_dir)
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # core/config_manager.py nằm trong thư mục con core/, nên lấy dirname 2 lần ra gốc dự án
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


CONFIG_FILE = os.path.join(get_app_dir(), "shared_config.json")
RUNTIME_STATE_FILE = os.path.join(get_app_dir(), "runtime_state.json")
UNLOCK_FLAG_FILE = os.path.join(get_app_dir(), "UNLOCK.txt")
EMERGENCY_BAT_FILE = os.path.join(get_app_dir(), "KHOI_PHUC_KHAN_CAP.bat")


def _detect_default_chrome_path() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def _detect_default_edge_path() -> str:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


DEFAULT_CONFIG: Dict[str, Any] = {
    "usernames": {
        "admin": "admin",
        "parent": "phuhuynh",
        "student": "hocsinh",
    },
    "passwords": {
        "admin": "admin123",
        "parent": "phuhuynh123",
        "student": "hocsinh123",
    },
    "kiosk_hide_explorer": False,
    "enforce_app_whitelist": False,
    "enable_posture_ai": True,
    "camera_index": 0,
    # Ưu tiên mở Webcam vật lý theo tên (khớp một phần, không phân biệt hoa thường) trước khi dùng camera_index
    "camera_preferred_names": ["HD Webcam"],
    # Bỏ qua các Camera ảo / driver phần mềm không phải Webcam thật
    "camera_ignored_names": [
        "Imaging Edge",
        "OBS Virtual",
        "SplitCam",
        "ManyCam",
        "VCam",
        "DroidCam",
        "Iriun",
        "XSplit",
    ],
    "web_port": 5000,
    "esp32_com_port": "COM3",
    "allowed_apps": [
        {
            "name": "Google Chrome (Học tập)",
            "exe_name": "chrome.exe",
            "path": _detect_default_chrome_path(),
        },
        {
            "name": "Microsoft Edge",
            "exe_name": "msedge.exe",
            "path": _detect_default_edge_path(),
        },
        {
            "name": "Sổ tay (Notepad)",
            "exe_name": "notepad.exe",
            "path": "notepad.exe",
        },
        {
            "name": "Máy tính (Calculator)",
            "exe_name": "CalculatorApp.exe",
            "path": "calc.exe",
        },
    ],
    "allowed_folders": [
        r"C:\Users\Public\Documents",
        r"D:\TaiLieuHocTap",
    ],
    # Bảo toàn 100% danh sách từ khóa gốc của nhóm trong giam_sat.pyw
    "bad_tabs": [
        "facebook",
        "tiktok",
        "shopee",
        "phim",
        "youtube",
        "game",
        "truyện",
        "poki",
        "y8",
    ],
    "bad_urls": [
        "facebook.com",
        "tiktok.com",
        "shopee.vn",
        "poki.com",
        "y8.com",
        "roblox.com",
        "youtube.com/shorts",
        "discord.com",
    ],
    "allowed_urls": [
        "olm.vn",
        "vietjack.com",
        "hocmai.vn",
        "scholar.google",
        "github.com",
        "docs.google.com",
        "drive.google.com",
        "wikipedia.org",
        "khanacademy.org",
    ],
    "academic_keywords": [
        "bài giảng",
        "vật lý",
        "vật lí",
        "toán",
        "hóa",
        "hóa học",
        "sinh học",
        "tiếng anh",
        "ngữ văn",
        "lịch sử",
        "địa lý",
        "khtn",
        "lập trình",
        "python",
        "ôn thi",
        "chữa đề",
        "học tập",
        "stem",
        "thí nghiệm",
    ],
    # Bảo toàn 100% danh sách game cấm gốc của nhóm
    "bad_apps": [
        "roblox",
        "league",
        "valorant",
        "steam",
        "fc_online",
        "genshin",
    ],
    "ai_domains": [
        "chatgpt.com",
        "gemini.google.com",
        "claude.ai",
        "copilot.microsoft.com",
    ],
    "max_ai_minutes": 15,
    # Schema chuẩn bị sẵn cho Giai đoạn 7 (Dịch vụ Bot Telegram 2 chiều)
    "telegram": {
        "bot_token": "",
        "chat_id": "",
        "enable_alerts": True,
        "alert_on_game": True,
        "alert_on_wrong_login": True,
        "alert_on_ai_overuse": True,
        "alert_on_posture": True,
        "cooldown_seconds": 30,
    },
}

DEFAULT_RUNTIME_STATE: Dict[str, Any] = {
    "updated_at": "",
    "current_role": "locked",
    "monitoring_active": False,
    "remote_lock_requested": False,
    "recalibrate_posture_requested": False,
    "ai_seconds": 0,
    "app_usage": {},
    "alerts": [],
    "posture_telemetry": {
        "calibrated": False,
        "visual_focus_score": 100.0,
        "cfi_score": 100.0,
        "ear": 0.30,
        "perclos": 0.0,
        "mar": 0.10,
        "pitch_deg": 0.0,
        "roll_deg": 0.0,
        "hands_on_desk": True,
        "alerts": [],
    },
    "hardware_box": {
        "connected": False,
        "phone_docked": False,
        "com_port": "COM3",
    },
}


def _deep_merge_defaults(data: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Đảm bảo mọi khóa mặc định luôn tồn tại ngay cả khi đọc file JSON phiên bản cũ."""
    merged = copy.deepcopy(defaults)
    if not isinstance(data, dict):
        return merged
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_defaults(v, merged[k])
        else:
            merged[k] = v
    return merged


def _safe_write_json(filepath: str, payload: Dict[str, Any]) -> bool:
    """Ghi file JSON an toàn có retry ngắn để không bị lỗi PermissionError khi OneDrive đang đồng bộ."""
    with _IO_LOCK:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        tmp_path = f"{filepath}.tmp"
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        for attempt in range(5):
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(serialized)
                os.replace(tmp_path, filepath)
                return True
            except PermissionError:
                time.sleep(0.04 * (attempt + 1))
            except Exception:
                break
        # Fallback ghi trực tiếp nếu os.replace bị trình đồng bộ giữ khóa
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(serialized)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return True
        except Exception as exc:
            safe_log(f"[WARN] Không thể ghi tệp {filepath}: {exc}")
            return False


def _safe_read_json(filepath: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    with _IO_LOCK:
        if not os.path.exists(filepath):
            initial = copy.deepcopy(defaults)
            _safe_write_json(filepath, initial)
            return initial
        for attempt in range(4):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                return _deep_merge_defaults(raw, defaults)
            except (json.JSONDecodeError, PermissionError):
                time.sleep(0.03 * (attempt + 1))
            except Exception:
                break
        return copy.deepcopy(defaults)


def load_config() -> Dict[str, Any]:
    """Đọc cấu hình dùng chung từ shared_config.json (tự khởi tạo nếu chưa có)."""
    return _safe_read_json(CONFIG_FILE, DEFAULT_CONFIG)


def save_config(cfg: Dict[str, Any]) -> bool:
    """Lưu cấu hình dùng chung vào shared_config.json."""
    merged = _deep_merge_defaults(cfg, DEFAULT_CONFIG)
    return _safe_write_json(CONFIG_FILE, merged)


def load_runtime_state() -> Dict[str, Any]:
    """Đọc trạng thái thời gian thực từ runtime_state.json."""
    return _safe_read_json(RUNTIME_STATE_FILE, DEFAULT_RUNTIME_STATE)


def save_runtime_state(state: Dict[str, Any]) -> bool:
    """Cập nhật trạng thái thời gian thực vào runtime_state.json."""
    merged = _deep_merge_defaults(state, DEFAULT_RUNTIME_STATE)
    merged["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return _safe_write_json(RUNTIME_STATE_FILE, merged)


def update_runtime_state(**kwargs: Any) -> Dict[str, Any]:
    """Cập nhật nhanh một số trường trong runtime_state.json một cách nguyên tử."""
    with _IO_LOCK:
        state = load_runtime_state()
        for k, v in kwargs.items():
            state[k] = v
        save_runtime_state(state)
        return state


def ensure_data_files_exist() -> None:
    """Đảm bảo cả shared_config.json và runtime_state.json được khởi tạo chuẩn ngay từ đầu."""
    cfg = load_config()
    save_config(cfg)
    state = load_runtime_state()
    save_runtime_state(state)
