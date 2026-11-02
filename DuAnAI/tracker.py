import os
import io
import re
import time
import json
import shutil
import sqlite3
import requests
import psutil
import win32gui
import win32process
import win32api
from datetime import datetime, timedelta, timezone
from PIL import ImageGrab
from google import genai
from google.genai import types
from pydantic import BaseModel

# =====================================================================
# 1. KHÓA BÍ MẬT: KHÔNG VIẾT TRONG CODE (an toàn khi push GitHub)
#    Thứ tự đọc: biến môi trường -> secrets.local.json (cạnh tracker.py / .exe) -> thư mục 01_11/
#    secrets.local.json mẫu: {"GEMINI_API_KEY": "...", "FIREBASE_URL": "https://...firebasedatabase.app"}
#    Cả secrets.local.json và 01_11/ đều nằm trong .gitignore.
# =====================================================================
import sys as _sys


def _base_dir():
    if getattr(_sys, "frozen", False):
        return os.path.dirname(os.path.abspath(_sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _read_secret(name, legacy_file, want_url=False):
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        with open(os.path.join(_base_dir(), "secrets.local.json"), "r", encoding="utf-8") as fh:
            val = str(json.load(fh).get(name, "")).strip()
            if val:
                return val
    except Exception:
        pass
    try:
        with open(os.path.join(_base_dir(), "01_11", legacy_file), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip().strip('"').strip("'")
                if not line:
                    continue
                if want_url:
                    m = re.search(r"https://\S+", line)
                    if m:
                        return m.group(0).rstrip("/")
                else:
                    return line.split()[-1]
    except Exception:
        pass
    return ""


GEMINI_API_KEY = _read_secret("GEMINI_API_KEY", "Gemini API Key.txt")
FIREBASE_URL = _read_secret("FIREBASE_URL", "Firebase.txt", want_url=True)
if not GEMINI_API_KEY or not FIREBASE_URL:
    raise SystemExit("Thiếu GEMINI_API_KEY / FIREBASE_URL: tạo secrets.local.json cạnh tracker.py (xem hướng dẫn ở đầu file).")

# =====================================================================
# 2. CẤU HÌNH HỆ THỐNG & SMART HYBRID (AI VISION)
# =====================================================================
UNLOCK_FILE_PATH = os.path.expanduser(r"~\Desktop\mo_khoa_admin.txt")

POLL_INTERVAL = 3         # Quét cửa sổ mỗi 3 giây
BATCH_INTERVAL = 60       # Gửi báo cáo lên AI mỗi 60 giây
IDLE_LIMIT_SEC = 180      # Ngừng đếm nếu rời máy quá 3 phút (180s)
VISION_THRESHOLD = 15     # Nếu mở cửa sổ "mập mờ" quá 15 giây -> Kích hoạt chụp màn hình

# Từ khóa nhạy cảm: Tuyệt đối KHÔNG ghi nhận và KHÔNG chụp màn hình
BLACKLIST_KEYWORDS = ["incognito", "ẩn danh", "password", "mật khẩu", "ngân hàng", "banking"]

# Danh sách từ khóa "Mập mờ" cần AI nhìn trực tiếp vào màn hình để phân biệt Học tập hay Giải trí
AMBIGUOUS_KEYWORDS = [
    "facebook", "youtube", "tiktok", "reddit", "zalo", "telegram", "discord",
    "untitled", "document", "không có tiêu đề", ".pdf", "new tab", "thẻ mới"
]

HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
HOSTS_MARKER = "# --- AI MONITOR BLOCK ---"

client = genai.Client(api_key=GEMINI_API_KEY)

# Tự động lấy danh sách mô hình AI hỗ trợ cả Text và Hình ảnh (Multimodal)
def get_fallback_models():
    models = []
    try:
        for m in client.models.list():
            name = m.name.replace("models/", "")
            if ("flash" in name or "pro" in name) and not any(x in name for x in ["2.5-flash", "tts", "image", "embedding"]):
                models.append(name)
    except Exception:
        pass
    models.sort(key=lambda x: (0 if "lite" in x else 1 if "flash" in x else 2))
    return models if models else ["gemini-3.0-flash", "gemini-3-flash-preview"]

AVAILABLE_MODELS = get_fallback_models()

BLOCK_RULES = {"apps": [], "domains": [], "keywords": []}
last_domains_applied = set()

class ActivityItem(BaseModel):
    app_or_web: str
    title_summary: str
    category: str
    duration_sec: int
    reason: str

class BatchAnalysis(BaseModel):
    items: list[ActivityItem]

def get_idle_seconds():
    """Kiểm tra thời gian người dùng không thao tác chuột/phím"""
    return (win32api.GetTickCount() - win32api.GetLastInputInfo()) / 1000.0

def get_active_window():
    """Lấy tên file .exe và tiêu đề cửa sổ đang mở"""
    try:
        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid <= 0:
            return None, None
        exe_name = psutil.Process(pid).name()
        window_title = win32gui.GetWindowText(hwnd).strip()
        return exe_name, window_title
    except Exception:
        return None, None

def is_ambiguous_window(exe, title):
    """Kiểm tra xem cửa sổ hiện tại có nằm trong diện 'mập mờ' cần chụp màn hình không"""
    combined = f"{exe} {title}".lower()
    return any(kw in combined for kw in AMBIGUOUS_KEYWORDS)

def capture_screen_bytes():
    """Chụp màn hình, thu nhỏ về chuẩn HD và nén trên RAM (không lưu ra ổ cứng)"""
    try:
        img = ImageGrab.grab()
        img.thumbnail((1280, 720))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()
    except Exception:
        return None

def get_recent_chrome_urls(minutes_ago=10):
    """Đọc lịch sử Chrome qua bản sao tạm"""
    history_path = os.path.expanduser(r"~\AppData\Local\Google\Chrome\User Data\Default\History")
    temp_path = os.path.expanduser(r"~\AppData\Local\Temp\temp_chrome_hist")
    if not os.path.exists(history_path):
        return []
    try:
        shutil.copy2(history_path, temp_path)
        conn = sqlite3.connect(temp_path)
        cursor = conn.cursor()
        cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        epoch_start = datetime(1601, 1, 1, tzinfo=timezone.utc)
        webkit_cutoff = int((cutoff_dt - epoch_start).total_seconds() * 1_000_000)
        cursor.execute("SELECT title, url FROM urls WHERE last_visit_time > ? ORDER BY last_visit_time DESC LIMIT 10", (webkit_cutoff,))
        urls = [{"title": r[0], "url": r[1][:80]} for r in cursor.fetchall()]
        conn.close()
        return urls
    except Exception:
        return []
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

def sync_rules_from_firebase():
    """Tải cấu hình chặn từ Web Dashboard về máy tính"""
    global BLOCK_RULES, last_domains_applied
    try:
        clean_fb_url = FIREBASE_URL.rstrip("/")
        res = requests.get(f"{clean_fb_url}/rules.json", timeout=5)
        if res.status_code == 200 and res.json():
            data = res.json()
            BLOCK_RULES["apps"] = [x.lower() for x in data.get("apps", [])]
            BLOCK_RULES["domains"] = [x.lower() for x in data.get("domains", [])]
            BLOCK_RULES["keywords"] = [x.lower() for x in data.get("keywords", [])]
            
            current_domains = set(BLOCK_RULES["domains"])
            if current_domains != last_domains_applied:
                update_windows_hosts(current_domains)
                last_domains_applied = current_domains
    except Exception:
        pass

def update_windows_hosts(domains):
    """Khóa hoặc mở khóa tên miền web trong file hosts của Windows"""
    try:
        if not os.path.exists(HOSTS_PATH):
            return
        with open(HOSTS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

        clean_lines = [line for line in lines if HOSTS_MARKER not in line]

        for d in domains:
            clean_domain = d.replace("https://", "").replace("http://", "").split("/")[0]
            if clean_domain:
                clean_lines.append(f"127.0.0.1 {clean_domain} {HOSTS_MARKER}\n")
                clean_lines.append(f"127.0.0.1 www.{clean_domain} {HOSTS_MARKER}\n")

        with open(HOSTS_PATH, "w", encoding="utf-8") as f:
            f.writelines(clean_lines)

        os.system("ipconfig /flushdns >nul 2>&1")
    except Exception:
        pass

def push_block_alert(target_name, reason_text):
    """Đẩy cảnh báo vi phạm ngay lập tức lên Web để phát còi báo động"""
    try:
        clean_fb_url = FIREBASE_URL.rstrip("/")
        date_key = datetime.now().strftime("%Y-%m-%d")
        now_str = datetime.now().strftime("%H:%M:%S")
        alert_item = {
            "app_or_web": target_name,
            "title_summary": f"⛔ ĐÃ CHẶN: {reason_text}",
            "category": "Giai_tri",
            "duration_sec": POLL_INTERVAL,
            "reason": "Cảnh báo thao tác bất thường: Vi phạm danh sách cấm từ Quản trị viên.",
            "timestamp": now_str,
            "is_alert": True
        }
        requests.post(f"{clean_fb_url}/logs/{date_key}.json", json=alert_item)
    except Exception:
        pass

def check_and_block(exe, title):
    """Kiểm tra và đóng ngay ứng dụng hoặc cửa sổ vi phạm"""
    if not exe:
        return False
    exe_lower = exe.lower()
    title_lower = (title or "").lower()

    # Lớp 1: Chặn theo tên file .exe
    if exe_lower in BLOCK_RULES["apps"]:
        os.system(f"taskkill /F /IM \"{exe}\" >nul 2>&1")
        push_block_alert(exe, f"Cố tình mở công cụ/phần mềm bị cấm ({exe})")
        return True

    # Lớp 2: Chặn theo từ khóa trên tiêu đề cửa sổ
    for kw in BLOCK_RULES["keywords"]:
        if kw and kw in title_lower:
            hwnd = win32gui.GetForegroundWindow()
            win32gui.PostMessage(hwnd, 0x0010, 0, 0)
            push_block_alert(exe, f"Mở nội dung chứa từ khóa cấm '{kw}' ({title[:35]})")
            return True

    return False

def analyze_and_push(usage_dict, recent_urls, screenshot_bytes=None, snapshot_title=""):
    """Gửi dữ liệu Text + Ảnh chụp màn hình (nếu có) lên Gemini AI để phân tích"""
    raw_list = [{"exe": k[0], "window_title": k[1], "duration_sec": v} for k, v in usage_dict.items()]
    
    prompt_text = f"""
    Phân loại danh sách cửa sổ phần mềm sau đây vào 1 trong 3 nhóm: "Hoc_tap", "Giai_tri", hoặc "Khac".
    Dữ liệu cửa sổ: {json.dumps(raw_list, ensure_ascii=False)}
    Lịch sử web tham khảo thêm: {json.dumps(recent_urls, ensure_ascii=False)}
    """

    contents_payload = []
    if screenshot_bytes:
        prompt_text += f"""
        LƯU Ý QUAN TRỌNG (CHẾ ĐỘ SMART HYBRID):
        Có 1 ảnh chụp màn hình đính kèm tại thời điểm người dùng đang mở cửa sổ '{snapshot_title}'.
        Hãy quan sát kỹ nội dung thực tế trong bức ảnh để phân loại chính xác xem người dùng đang học tập/làm việc hay đang giải trí, và bắt buộc thêm ký hiệu '[👁️ AI Vision]' vào đầu câu ở trường reason của mục đó.
        """
        contents_payload = [
            types.Part.from_bytes(data=screenshot_bytes, mime_type="image/jpeg"),
            prompt_text
        ]
    else:
        prompt_text += "\nTrả về tiếng Việt ngắn gọn, rõ ràng ở trường title_summary và reason."
        contents_payload = prompt_text

    response = None
    for model_name in AVAILABLE_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents_payload,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=BatchAnalysis,
                    temperature=0.1,
                ),
            )
            break
        except Exception:
            time.sleep(2)
            
    if not response:
        return

    result = json.loads(response.text)
    clean_fb_url = FIREBASE_URL.rstrip("/")
    date_key = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%H:%M:%S")
    
    for item in result.get("items", []):
        item["timestamp"] = now_str
        item["is_alert"] = False
        requests.post(f"{clean_fb_url}/logs/{date_key}.json", json=item)

def main():
    usage_buffer = {}
    last_send = time.time()
    last_rule_sync = 0
    
    # Biến phục vụ chế độ chụp màn hình thông minh (Smart Hybrid)
    pending_screenshot = None
    pending_snapshot_title = ""

    sync_rules_from_firebase()

    while True:
        # 1. Kiểm tra chìa khóa thoát hiểm ngoài Desktop
        if os.path.exists(UNLOCK_FILE_PATH) or os.path.exists(UNLOCK_FILE_PATH + ".txt"):
            update_windows_hosts(set())
            break

        # 2. Đồng bộ danh sách chặn từ Web mỗi 15 giây
        if time.time() - last_rule_sync >= 15:
            sync_rules_from_firebase()
            last_rule_sync = time.time()

        # 3. Quét cửa sổ đang mở
        if get_idle_seconds() < IDLE_LIMIT_SEC:
            exe, title = get_active_window()
            if exe:
                # Chặn ngay lập tức nếu vi phạm quy tắc
                if check_and_block(exe, title):
                    time.sleep(1)
                    continue

                # Nếu không chứa từ khóa nhạy cảm thì cộng dồn thời gian
                if title and not any(kw in title.lower() for kw in BLACKLIST_KEYWORDS):
                    key = (exe, title)
                    usage_buffer[key] = usage_buffer.get(key, 0) + POLL_INTERVAL

                    # KÍCH HOẠT SMART HYBRID: Nếu cửa sổ thuộc diện "mập mờ" và đã mở >= 15 giây
                    if pending_screenshot is None and usage_buffer[key] >= VISION_THRESHOLD:
                        if is_ambiguous_window(exe, title):
                            pending_screenshot = capture_screen_bytes()
                            pending_snapshot_title = title

        # 4. Định kỳ gửi báo cáo (Text + Ảnh chụp màn hình nếu có) lên AI & Firebase
        if time.time() - last_send >= BATCH_INTERVAL and usage_buffer:
            try:
                urls = get_recent_chrome_urls()
                analyze_and_push(usage_buffer, urls, pending_screenshot, pending_snapshot_title)
                usage_buffer.clear()
                pending_screenshot = None
                pending_snapshot_title = ""
            except Exception:
                pass
            last_send = time.time()

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()