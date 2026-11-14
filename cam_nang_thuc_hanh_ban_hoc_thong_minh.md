# CẨM NANG THỰC HÀNH KỸ THUẬT: XÂY DỰNG PHẦN MỀM BÀN HỌC THÔNG MINH
*(Tài liệu hướng dẫn từng bước dành cho học sinh THCS - Lớp 8)*

---

## TỔNG QUAN DỰ ÁN & CẤU TRÚC THƯ MỤC

Khi hoàn thành tất cả 6 bước trong tài liệu này, thư mục dự án `BanHocThongMinh` trên máy tính của em sẽ bao gồm 4 tệp cốt lõi hoạt động phối hợp với nhau:

```text
📁 BanHocThongMinh/
 ├── 📄 shared_config.json         (Bước 2: Tệp lưu mật khẩu, danh sách phần mềm và cấu hình)
 ├── 🐍 telegram_service.py        (Bước 3: Mô-đun gửi tin nhắn cảnh báo về Telegram phụ huynh)
 ├── 🐍 posture_ai_engine.py       (Bước 4: Trí tuệ nhân tạo AI nhận diện tư thế ngồi học qua Webcam)
 ├── 🐍 focus_desk_launcher.py     (Bước 5: Giao diện khóa máy 3 cấp quyền kiểu phòng máy Gcafe)
 └── 🚀 FocusDeskLauncher.exe      (Bước 6: Sản phẩm đóng gói hoàn chỉnh chạy quyền Quản trị viên)
```

> **💡 Lưu ý quan trọng trước khi bắt đầu:**
> Khi tạo các tệp `.json` hoặc `.py` bằng Notepad trên Windows, ở hộp thoại **Save As (Lưu như)**, em bắt buộc phải chọn mục **Save as type** là **`All Files (*.*)`** và chọn **Encoding** là **`UTF-8`** để tệp không bị biến thành đuôi `.txt`.

---

## BƯỚC 1: CHUẨN BỊ MÔI TRƯỜNG & CÀI ĐẶT THƯ VIỆN

### 1.1. Tạo thư mục làm việc
1. Mở ổ đĩa `D:\` (hoặc màn hình Desktop), nhấn chuột phải chọn **New $\rightarrow$ Folder**.
2. Đặt tên thư mục là `BanHocThongMinh` (viết liền không dấu).

### 1.2. Cài đặt ngôn ngữ lập trình Python
1. Tải bộ cài đặt Python (khuyến nghị phiên bản **Python 3.10** hoặc **Python 3.11** để tương thích tốt nhất với thư viện AI MediaPipe) từ trang chủ `https://www.python.org/downloads/`.
2. Mở bộ cài đặt lên. **Bắt buộc đánh dấu tích vào ô `Add Python to PATH`** ở ngay màn hình đầu tiên, sau đó nhấn **Install Now**.

### 1.3. Cài đặt các gói thư viện công cụ
1. Mở thư mục `BanHocThongMinh` vừa tạo.
2. Nhấp chuột vào thanh địa chỉ phía trên của thư mục, gõ chữ `cmd` rồi nhấn phím **Enter**. Một cửa sổ màn hình đen (**Command Prompt**) sẽ hiện ra.
3. Sao chép câu lệnh dưới đây, dán vào cửa sổ màn hình đen và nhấn **Enter** (chờ khoảng $2 - 3\text{ phút}$ để máy tải xong):

```bash
pip install psutil pyinstaller requests flask opencv-python numpy mediapipe
```

---

## BƯỚC 2: TẠO TỆP CẤU HÌNH DÙNG CHUNG (`shared_config.json`)

Tệp này đóng vai trò là "bộ nhớ trung tâm" lưu trữ mật khẩu của 3 cấp tài khoản, danh sách các ứng dụng học tập được phép mở, các trang web bị chặn và thông tin kết nối Bot Telegram.

### 2.1. Cách thực hiện
1. Mở phần mềm **Notepad** (hoặc **Visual Studio Code**).
2. Sao chép toàn bộ đoạn mã JSON bên dưới và dán vào.
3. Chọn **File $\rightarrow$ Save As**, tìm đến thư mục `BanHocThongMinh`, đặt tên tệp chính xác là `shared_config.json` (chọn `All Files (*.*)` và mã hóa `UTF-8`).
4. Kiểm tra lại đường dẫn của phần mềm Google Chrome hoặc VS Code trong tệp xem đã khớp với đường dẫn cài đặt trên máy tính của em chưa.

### 2.2. Mã nguồn tệp `shared_config.json`

```json
{
  "passwords": {
    "admin": "admin123",
    "parent": "phuhuynh123",
    "student": "hocsinh"
  },
  "allowed_apps": [
    {
      "name": "Google Chrome (Học tập)",
      "exe_name": "chrome.exe",
      "path": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    },
    {
      "name": "Visual Studio Code",
      "exe_name": "Code.exe",
      "path": "C:\\Users\\Public\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe"
    },
    {
      "name": "Máy tính (Calculator)",
      "exe_name": "CalculatorApp.exe",
      "path": "calc.exe"
    },
    {
      "name": "Sổ tay (Notepad)",
      "exe_name": "notepad.exe",
      "path": "notepad.exe"
    }
  ],
  "allowed_folders": [
    "D:\\TaiLieuHocTap",
    "C:\\Users\\Public\\Documents"
  ],
  "web_policy": {
    "blocked_domains": ["poki.com", "y8.com", "roblox.com", "facebook.com", "tiktok.com", "discord.com"],
    "ai_domains": ["chatgpt.com", "claude.ai", "gemini.google.com"],
    "max_ai_minutes": 15,
    "grace_period_seconds": 5
  },
  "telegram_config": {
    "bot_token": "YOUR_BOT_TOKEN_HERE",
    "chat_id": "YOUR_CHAT_ID_HERE",
    "enable_alerts": true,
    "alert_on_game": true,
    "alert_on_wrong_login": true,
    "alert_on_ai_overuse": true,
    "cooldown_seconds": 30
  }
}
```

---

## BƯỚC 3: XÂY DỰNG BOT TELEGRAM CẢNH BÁO & WEB CẤU HÌNH (`telegram_service.py`)

Mô-đun này giúp gửi tin nhắn báo cáo tức thời về điện thoại của phụ huynh khi phát hiện vi phạm, đồng thời tạo một trang Web để phụ huynh dễ dàng bật/tắt cảnh báo trên trình duyệt.

### 3.1. Cách lấy mã `Bot Token` và `Chat ID` trên Telegram (Mất $2\text{ phút}$)
1. Mở ứng dụng **Telegram**, gõ tìm kiếm tài khoản **`@BotFather`** (có dấu tích xanh).
2. Nhắn lệnh `/newbot` $\rightarrow$ Nhập tên hiển thị cho Bot (ví dụ: `Giám Sát Bàn Học`) $\rightarrow$ Nhập tên định danh kết thúc bằng chữ `bot` (ví dụ: `BanHocThongMinh2026_bot`).
3. `@BotFather` sẽ trả về một đoạn mã **HTTP API Token** (có dạng `7123456789:AAH...`). Hãy sao chép mã này.
4. Bấm vào đường link Bot vừa tạo trong tin nhắn, nhấn nút **Start** và gửi một tin nhắn bất kỳ (ví dụ: `Xin chào`).
5. Mở trình duyệt web (Chrome/Edge), truy cập địa chỉ sau (nhớ thay `<TOKEN_CỦA_EM>` bằng mã vừa copy):
   `https://api.telegram.org/bot<TOKEN_CỦA_EM>/getUpdates`
6. Tìm cụm chữ `"chat":{"id": 123456789...}` trên màn hình trình duyệt. Dãy số đó chính là **`Chat ID`** của phụ huynh.

### 3.2. Tạo tệp `telegram_service.py`
1. Mở Notepad hoặc VS Code, tạo tệp mới và lưu vào thư mục `BanHocThongMinh` với tên `telegram_service.py`.
2. Sao chép và dán toàn bộ đoạn mã dưới đây vào tệp:

```python
import os
import json
import time
import threading
import requests
from flask import Flask, request, jsonify, render_template_string

CONFIG_FILE = "shared_config.json"
last_sent_timestamps = {} # Lưu thời điểm gửi gần nhất của từng loại cảnh báo để chống spam
ai_cumulative_seconds = 0 # Bộ đếm cộng dồn thời gian sử dụng ChatGPT / Chatbot AI

def load_shared_config():
    if not os.path.exists(CONFIG_FILE):
        return {
            "web_policy": {"max_ai_minutes": 15, "blocked_domains": ["poki.com", "roblox.com"]},
            "telegram_config": {
                "bot_token": "YOUR_BOT_TOKEN_HERE",
                "chat_id": "YOUR_CHAT_ID_HERE",
                "enable_alerts": True,
                "alert_on_game": True,
                "alert_on_wrong_login": True,
                "alert_on_ai_overuse": True,
                "cooldown_seconds": 30
            }
        }
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_shared_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# =====================================================================
# 1. HÀM GỬI CẢNH BÁO TỨC THÌ VỀ TELEGRAM PHỤ HUYNH (CÓ CHỐNG SPAM)
# =====================================================================
def trigger_parent_alert(event_type: str, detail_message: str):
    """
    event_type nhận 1 trong các giá trị:
    - 'GAME_DETECTED' : Khi phát hiện mở web game hoặc phần mềm game
    - 'WRONG_LOGIN'   : Khi học sinh cố tình đăng nhập quyền Admin/Phụ huynh
    - 'AI_OVERUSE'    : Khi dùng ChatGPT/Gemini/Claude quá thời gian quy định
    - 'SYSTEM_TEST'   : Khi phụ huynh bấm nút Test trên Website
    """
    cfg = load_shared_config()
    tg = cfg.get("telegram_config", {})

    if not tg.get("enable_alerts", True) and event_type != "SYSTEM_TEST":
        return False

    # Kiểm tra công tắc bật/tắt riêng cho từng loại cảnh báo trên Website
    if event_type == "GAME_DETECTED" and not tg.get("alert_on_game", True):
        return False
    if event_type == "WRONG_LOGIN" and not tg.get("alert_on_wrong_login", True):
        return False
    if event_type == "AI_OVERUSE" and not tg.get("alert_on_ai_overuse", True):
        return False

    # Kiểm tra thời gian giãn cách (Cooldown) để không bắn tin nhắn liên tục mỗi giây
    cooldown = int(tg.get("cooldown_seconds", 30))
    now = time.time()
    if event_type != "SYSTEM_TEST":
        last_time = last_sent_timestamps.get(event_type, 0)
        if now - last_time < cooldown:
            return False
    last_sent_timestamps[event_type] = now

    token = tg.get("bot_token", "").strip()
    chat_id = tg.get("chat_id", "").strip()

    titles = {
        "GAME_DETECTED": "🚨 CẢNH BÁO TRUY CẬP GAME / GIẢI TRÍ",
        "WRONG_LOGIN": "🔐 CẢNH BÁO THỬ ĐĂNG NHẬP VƯỢT QUYỀN",
        "AI_OVERUSE": "🤖 CẢNH BÁO SỬ DỤNG CHATBOT AI QUÁ LÂU",
        "SYSTEM_TEST": "✅ KIỂM TRA KẾT NỐI BOT TELEGRAM THÀNH CÔNG"
    }
    header = titles.get(event_type, "⚠️ THÔNG BÁO BÀN HỌC THÔNG MINH")
    formatted_msg = (
        f"*{header}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🕒 *Thời gian:* `{time.strftime('%H:%M:%S - %d/%m/%Y')}`\n"
        f"📌 *Nội dung:* {detail_message}\n"
        f"🌐 _Truy cập Web Dashboard của Phụ huynh để xem chi tiết._"
    )

    if not token or token == "YOUR_BOT_TOKEN_HERE":
        print(f"[TELEGRAM MOCK] {formatted_msg}")
        return False

    def _send_async():
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, json={
                "chat_id": chat_id,
                "text": formatted_msg,
                "parse_mode": "Markdown"
            }, timeout=4)
        except Exception as e:
            print(f"[ERR] Không thể gửi Telegram: {e}")

    threading.Thread(target=_send_async, daemon=True).start()
    return True

# =====================================================================
# 2. BỘ ĐẾM THỜI GIAN TRA CỨU CHATGPT / CHATBOT AI
# =====================================================================
def track_ai_chatbot_usage(current_url: str, window_title: str, interval_sec: int = 2):
    """
    Gọi hàm này mỗi 2 giây trong luồng quét trình duyệt:
    Nếu URL hoặc tiêu đề chứa ChatGPT, Claude, Gemini, Copilot -> cộng dồn thời gian.
    Khi vượt ngưỡng max_ai_minutes do Phụ huynh đặt trên Web -> Bắn cảnh báo Telegram.
    """
    global ai_cumulative_seconds
    cfg = load_shared_config()
    ai_domains = cfg.get("web_policy", {}).get("ai_domains", ["chatgpt.com", "claude.ai", "gemini.google.com"])
    max_minutes = int(cfg.get("web_policy", {}).get("max_ai_minutes", 15))

    target_str = f"{current_url} {window_title}".lower()
    if any(domain in target_str for domain in ai_domains):
        ai_cumulative_seconds += interval_sec
        used_minutes = round(ai_cumulative_seconds / 60.0, 1)
        if ai_cumulative_seconds >= max_minutes * 60:
            trigger_parent_alert(
                "AI_OVERUSE",
                f"Học sinh đã tra cứu Chatbot AI ({current_url or window_title}) tổng cộng *{used_minutes} phút* (Vượt giới hạn {max_minutes} phút)!"
            )

# =====================================================================
# 3. GIAO DIỆN WEB CẤU HÌNH THUỘC TÍNH BOT TELEGRAM CHO PHỤ HUYNH
# =====================================================================
app = Flask(__name__)

WEB_CONFIG_HTML = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cấu Hình Bot Telegram & Chính Sách Giám Sát - Phụ Huynh</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: 'Segoe UI', sans-serif; padding: 24px; }
        .container { max-width: 680px; margin: 0 auto; background: #1e293b; padding: 28px; border-radius: 14px; border: 1px solid #334155; }
        h1 { font-size: 20px; color: #38bdf8; margin-bottom: 6px; }
        p.sub { font-size: 13px; color: #94a3b8; margin-bottom: 20px; }
        .form-group { margin-bottom: 16px; }
        label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; color: #e2e8f0; }
        input[type="text"], input[type="number"] { width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #475569; background: #0f172a; color: white; font-size: 14px; }
        .checkbox-group { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; font-size: 14px; }
        .btn-row { display: flex; gap: 12px; margin-top: 22px; }
        button { padding: 10px 18px; border: none; border-radius: 8px; font-weight: 700; cursor: pointer; font-size: 14px; }
        .btn-save { background: #0284c7; color: white; flex: 1; }
        .btn-test { background: #10b981; color: white; }
        #status-msg { margin-top: 14px; font-size: 13px; font-weight: 600; color: #4ade80; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚙️ THIẾT LẬP THUỘC TÍNH BOT TELEGRAM TRÊN WEBSITE</h1>
        <p class="sub">Mọi thay đổi tại đây được lưu trực tiếp vào hệ thống và áp dụng tức thời cho phiên học.</p>

        <div class="form-group">
            <label>1. Telegram Bot Token (Lấy từ @BotFather):</label>
            <input type="text" id="bot_token" value="{{ tg.bot_token }}">
        </div>
        <div class="form-group">
            <label>2. Telegram Chat ID của Phụ huynh:</label>
            <input type="text" id="chat_id" value="{{ tg.chat_id }}">
        </div>
        <div class="form-group">
            <label>3. Thời gian tối đa cho phép dùng ChatGPT / Chatbot AI (Phút):</label>
            <input type="number" id="max_ai_minutes" value="{{ policy.max_ai_minutes }}">
        </div>
        <div class="form-group">
            <label>4. Thời gian giãn cách giữa 2 tin nhắn cảnh báo (Giây - Chống spam):</label>
            <input type="number" id="cooldown_seconds" value="{{ tg.cooldown_seconds }}">
        </div>

        <label style="margin-top:16px;">5. Tùy chọn các sự kiện bất thường cần Bot nhắn tin:</label>
        <div class="checkbox-group">
            <input type="checkbox" id="enable_alerts" {% if tg.enable_alerts %}checked{% endif %}>
            <span>Kích hoạt gửi cảnh báo qua Telegram</span>
        </div>
        <div class="checkbox-group">
            <input type="checkbox" id="alert_on_game" {% if tg.alert_on_game %}checked{% endif %}>
            <span>Báo cáo khi học sinh cố truy cập Web Game / Phần mềm Game</span>
        </div>
        <div class="checkbox-group">
            <input type="checkbox" id="alert_on_wrong_login" {% if tg.alert_on_wrong_login %}checked{% endif %}>
            <span>Báo cáo khi học sinh thử đăng nhập quyền Phụ huynh / Admin thất bại</span>
        </div>
        <div class="checkbox-group">
            <input type="checkbox" id="alert_on_ai_overuse" {% if tg.alert_on_ai_overuse %}checked{% endif %}>
            <span>Báo cáo khi học sinh tra cứu ChatGPT / Chatbot AI quá thời gian quy định</span>
        </div>

        <div class="btn-row">
            <button class="btn-save" onclick="saveSettings()">💾 Lưu Cấu Hình Bot</button>
            <button class="btn-test" onclick="testBot()">📨 Gửi Tin Nhắn Thử Nghiệm</button>
        </div>
        <div id="status-msg"></div>
    </div>

    <script>
        async function saveSettings() {
            const payload = {
                bot_token: document.getElementById('bot_token').value,
                chat_id: document.getElementById('chat_id').value,
                max_ai_minutes: parseInt(document.getElementById('max_ai_minutes').value || 15),
                cooldown_seconds: parseInt(document.getElementById('cooldown_seconds').value || 30),
                enable_alerts: document.getElementById('enable_alerts').checked,
                alert_on_game: document.getElementById('alert_on_game').checked,
                alert_on_wrong_login: document.getElementById('alert_on_wrong_login').checked,
                alert_on_ai_overuse: document.getElementById('alert_on_ai_overuse').checked
            };
            const res = await fetch('/api/telegram-config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            document.getElementById('status-msg').innerText = data.message;
        }

        async function testBot() {
            await saveSettings();
            const res = await fetch('/api/telegram-test', {method: 'POST'});
            const data = await res.json();
            document.getElementById('status-msg').innerText = data.message;
        }
    </script>
</body>
</html>
"""

@app.route("/bot-settings")
def bot_settings_page():
    cfg = load_shared_config()
    return render_template_string(
        WEB_CONFIG_HTML,
        tg=cfg.get("telegram_config", {}),
        policy=cfg.get("web_policy", {})
    )

@app.route("/api/telegram-config", methods=["POST"])
def update_telegram_config():
    data = request.json
    cfg = load_shared_config()
    cfg["telegram_config"].update({
        "bot_token": data.get("bot_token", ""),
        "chat_id": data.get("chat_id", ""),
        "cooldown_seconds": int(data.get("cooldown_seconds", 30)),
        "enable_alerts": bool(data.get("enable_alerts", True)),
        "alert_on_game": bool(data.get("alert_on_game", True)),
        "alert_on_wrong_login": bool(data.get("alert_on_wrong_login", True)),
        "alert_on_ai_overuse": bool(data.get("alert_on_ai_overuse", True))
    })
    cfg["web_policy"]["max_ai_minutes"] = int(data.get("max_ai_minutes", 15))
    save_shared_config(cfg)
    return jsonify({"status": "ok", "message": "✅ Đã lưu toàn bộ thuộc tính Bot Telegram vào hệ thống!"})

@app.route("/api/telegram-test", methods=["POST"])
def test_telegram_alert():
    ok = trigger_parent_alert("SYSTEM_TEST", "Cấu hình Bot Telegram từ Website Bàn Học Thông Minh đã hoạt động chính xác!")
    if ok:
        return jsonify({"status": "ok", "message": "📨 Đã bắn tin nhắn thử nghiệm về Telegram của Phụ huynh!"})
    return jsonify({"status": "err", "message": "⚠️ Chưa nhập Bot Token hợp lệ (Đã in nội dung ra Console)."})

if __name__ == "__main__":
    print(">>> Truy cập trang cấu hình Bot Telegram tại: http://127.0.0.1:5001/bot-settings")
    app.run(host="0.0.0.0", port=5001, debug=False)
```

### 3.3. Cách kiểm thử Bước 3
1. Tại cửa sổ dòng lệnh `cmd` trong thư mục `BanHocThongMinh`, gõ lệnh:
   ```bash
   python telegram_service.py
   ```
2. Mở trình duyệt web truy cập địa chỉ: `http://127.0.0.1:5001/bot-settings`
3. Dán `Bot Token` và `Chat ID` vừa lấy ở mục 3.1 vào hai ô đầu tiên rồi bấm nút **"📨 Gửi Tin Nhắn Thử Nghiệm"**. Nếu điện thoại ting ting nhận được tin nhắn từ Bot là em đã thành công!

---

## BƯỚC 4: XÂY DỰNG MÔ-ĐUN AI NHẬN DIỆN TƯ THẾ NGỒI HỌC (`posture_ai_engine.py`)

Mô-đun này sử dụng thư viện **Google MediaPipe Holistic** kết hợp các công thức hình học không gian để đo tỷ lệ mở mắt ($\text{EAR}_t$), tỷ lệ nhắm mắt tích lũy ($\text{PERCLOS}$), độ mở miệng ngáp ($\text{MAR}_t$), góc nghiêng đầu ($\text{Pitch}, \text{Roll}$) và theo dõi 2 bàn tay trên bàn học:

$$\text{EAR}_t = \frac{\|p_2 - p_6\| + \|p_3 - p_5\|}{2\|p_1 - p_4\|}$$

$$\text{MAR}_t = \frac{\|p_{62} - p_{66}\| + \|p_{63} - p_{65}\| + \|p_{61} - p_{67}\|}{3\|p_{60} - p_{64}\|}$$

### 4.1. Tạo tệp `posture_ai_engine.py`
1. Mở Notepad hoặc VS Code, tạo tệp mới lưu vào thư mục `BanHocThongMinh` với tên `posture_ai_engine.py`.
2. Sao chép và dán toàn bộ đoạn mã dưới đây vào tệp:

```python
import time
import json
import cv2
import psutil
import numpy as np
import mediapipe as mp

class PostureAIEngine:
    def __init__(self, camera_index=0):
        self.cap = cv2.VideoCapture(camera_index)
        self.mp_holistic = mp.solutions.holistic
        self.holistic = self.mp_holistic.Holistic(
            min_detection_confidence=0.6,
            min_tracking_confidence=0.6
        )

        # Gốc chuẩn cá nhân hóa (Sẽ được tự động hiệu chỉnh trong 5 giây đầu)
        self.calibrated = False
        self.calib_samples = []
        self.base_eye_span = 0.16
        self.base_neck_height = 0.22

        # Theo dõi chuỗi thời gian chống giả mạo & tính PERCLOS
        self.last_blink_time = time.time()
        self.last_hand_time = time.time()
        self.ear_window = []       # Cửa sổ trượt tính PERCLOS
        self.pitch_history = []    # Cửa sổ trượt 1.5s tính sự kiện gật gù E_nod

    @staticmethod
    def _dist(p1, p2):
        return float(np.linalg.norm(np.array([p1.x, p1.y]) - np.array([p2.x, p2.y])))

    def check_virtual_camera_spoofing(self):
        """Chặn đứng các trình điều khiển Camera ảo (OBS, ManyCam, VCam)"""
        blacklist = ["obs64.exe", "obs32.exe", "manycam.exe", "vcam.exe", "splitcam.exe"]
        for p in psutil.process_iter(['name']):
            try:
                if (p.info['name'] or "").lower() in blacklist:
                    return True, p.info['name']
            except Exception:
                continue
        return False, ""

    def calibrate_baseline(self, eye_span, neck_height):
        """Tự động học tư thế ngồi chuẩn của học sinh trong 30 khung hình đầu tiên"""
        self.calib_samples.append((eye_span, neck_height))
        if len(self.calib_samples) >= 30:
            arr = np.array(self.calib_samples)
            self.base_eye_span = float(np.mean(arr[:, 0]))
            self.base_neck_height = float(np.mean(arr[:, 1]))
            self.calibrated = True
            print(f"[CALIBRATED] Gốc chuẩn: EyeSpan={self.base_eye_span:.3f}, Neck={self.base_neck_height:.3f}")

    def process_frame(self):
        """
        Xử lý 1 khung hình Webcam tại biên (Zero-cloud):
        Trả về (annotated_jpeg_bytes, telemetry_dict) để đẩy thẳng lên Website báo cáo.
        """
        ret, frame = self.cap.read()
        if not ret:
            return None, {"status": "ERROR", "message": "Không đọc được Webcam"}

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = self.holistic.process(rgb)

        alerts = []
        ear = 0.30
        mar = 0.10
        pitch_deg = 0.0
        roll_deg = 0.0
        drowsy_nod = False

        # 1. Kiểm tra Camera ảo
        is_vcam, vcam_name = self.check_virtual_camera_spoofing()
        if is_vcam:
            alerts.append(f"GIAN LẬN: Phát hiện Camera ảo ({vcam_name})")

        # 2. Phân tích Điểm mốc Khuôn mặt (EAR, PERCLOS, MAR, Roll, Khoảng cách)
        if res.face_landmarks:
            lm = res.face_landmarks.landmark
            # Tính EAR trung bình 2 mắt
            ear_l = self._dist(lm[159], lm[145]) / (self._dist(lm[33], lm[133]) + 1e-6)
            ear_r = self._dist(lm[386], lm[374]) / (self._dist(lm[362], lm[263]) + 1e-6)
            ear = (ear_l + ear_r) / 2.0

            # Cập nhật cửa sổ trượt PERCLOS (100 khung hình gần nhất)
            self.ear_window.append(1 if ear < 0.20 else 0)
            self.ear_window = self.ear_window[-100:]
            perclos = round((sum(self.ear_window) / len(self.ear_window)) * 100.0, 1)

            if ear < 0.20:
                self.last_blink_time = time.time()
            elif time.time() - self.last_blink_time > 25.0:
                alerts.append("CẢNH BÁO PAD: Không chớp mắt >25s (Nghi ảnh tĩnh)")

            if perclos > 35.0:
                alerts.append(f"BUỒN NGỦ: Chỉ số PERCLOS cao ({perclos}%)")

            # Tính MAR (Độ mở miệng ngáp)
            mar = self._dist(lm[13], lm[14]) / (self._dist(lm[78], lm[308]) + 1e-6)
            if mar > 0.62:
                alerts.append("MỆT MỎI: Phát hiện ngáp (MAR cao)")

            # Góc nghiêng đầu sang vai (Roll)
            roll_deg = float(np.degrees(np.arctan2(lm[263].y - lm[33].y, lm[263].x - lm[33].x)))
            if abs(roll_deg) > 14.0:
                alerts.append(f"SAI TƯ THẾ: Nghiêng đầu ({roll_deg:.1f}°)")

            eye_span = self._dist(lm[33], lm[263])
        else:
            perclos = 0.0
            eye_span = self.base_eye_span
            alerts.append("VẮNG MẶT: Học sinh rời khỏi ghế ngồi")

        # 3. Phân tích Khung xương Vai & Cúi đầu (Pose Landmarks)
        if res.pose_landmarks:
            plm = res.pose_landmarks.landmark
            left_sh, right_sh, nose = plm[11], plm[12], plm[0]
            mid_sh_y = (left_sh.y + right_sh.y) / 2.0
            neck_height = mid_sh_y - nose.y

            if not self.calibrated and res.face_landmarks:
                self.calibrate_baseline(eye_span, neck_height)

            # Kiểm tra nhìn quá sát màn hình (So với gốc chuẩn cá nhân)
            if self.calibrated and eye_span > self.base_eye_span * 1.25:
                alerts.append("CẬN THỊ: Cúi quá sát màn hình/mặt bàn!")

            # Kiểm tra cúi đầu thấp & Sự kiện gật gù E_nod trong 1.5s
            pitch_deg = float((self.base_neck_height - neck_height) * 180.0)
            now = time.time()
            self.pitch_history.append((now, pitch_deg))
            self.pitch_history = [(t, p) for (t, p) in self.pitch_history if now - t <= 1.5]
            if len(self.pitch_history) > 5:
                p_vals = [p for (_, p) in self.pitch_history]
                if max(p_vals) - min(p_vals) > 15.0:
                    drowsy_nod = True
                    alerts.append("NGỦ GẬT: Phát hiện gật đầu đột ngột (>15°)")

            if self.calibrated and neck_height < self.base_neck_height * 0.72:
                alerts.append("SAI TƯ THẾ: Cúi đầu quá thấp!")

            # Kiểm tra lệch vai (Vẹo cột sống)
            if abs(left_sh.y - right_sh.y) > 0.055:
                alerts.append("SAI TƯ THẾ: Ngồi vẹo lưng / Lệch vai!")

        # 4. Theo dõi 2 bàn tay trên mặt bàn (Hands-on-Desk Tracking)
        hands_detected = bool(res.left_hand_landmarks or res.right_hand_landmarks)
        if hands_detected:
            self.last_hand_time = time.time()
        elif time.time() - self.last_hand_time > 15.0:
            alerts.append("MẤT TẬP TRUNG: Giấu cả 2 tay dưới gầm bàn (>15s)")

        # 5. Tính điểm tập trung thị giác (Visual Focus Score: 0 - 100%)
        visual_focus = max(0.0, 100.0 - len(alerts) * 22.0)

        # Vẽ lớp phủ thông số kỹ thuật lên khung hình để hiển thị trên Website
        banner_color = (16, 185, 129) if not alerts else (225, 29, 72)
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 38), (15, 23, 42), -1)
        header_txt = "CALIBRATING (5s)..." if not self.calibrated else (
            "TU THE CHUAN | CFI VISUAL: 100%" if not alerts else alerts[0]
        )
        cv2.putText(frame, header_txt, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.58, banner_color, 2)

        _, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])

        telemetry = {
            "calibrated": self.calibrated,
            "ear": round(ear, 3),
            "perclos": perclos,
            "mar": round(mar, 3),
            "pitch_deg": round(pitch_deg, 1),
            "roll_deg": round(roll_deg, 1),
            "drowsy_nod": drowsy_nod,
            "hands_on_desk": (time.time() - self.last_hand_time <= 15.0),
            "visual_focus_score": round(visual_focus, 1),
            "alerts": alerts
        }
        return jpeg.tobytes(), telemetry

    def release(self):
        self.cap.release()

# Đoạn mã chạy thử nghiệm trực tiếp trên màn hình dành cho học sinh kiểm tra Camera
if __name__ == "__main__":
    engine = PostureAIEngine(0)
    print(">>> Đang mở Webcam kiểm thử tư thế (Nhấn phím 'q' trên cửa sổ hình ảnh để thoát)...")
    while True:
        jpeg_bytes, info = engine.process_frame()
        if jpeg_bytes:
            frame_arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            img = cv2.imdecode(frame_arr, cv2.IMREAD_COLOR)
            cv2.imshow("Kiem thu AI Nhan dien Tu the", img)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    engine.release()
    cv2.destroyAllWindows()
```

### 4.2. Cách kiểm thử Bước 4
1. Mở cửa sổ lệnh `cmd` và chạy lệnh:
   ```bash
   python posture_ai_engine.py
   ```
2. Trong **$5\text{ giây}$ đầu tiên** khi cửa sổ Webcam bật lên (hiện chữ `CALIBRATING (5s)...`), em hãy ngồi thẳng lưng chuẩn mực và nhìn thẳng vào màn hình để AI tự động đo khoảng cách mắt và chiều cao cổ của riêng em.
3. Sau khi thanh trạng thái chuyển sang màu xanh (`TU THE CHUAN | CFI VISUAL: 100%`), hãy thử cúi đầu sát màn hình, nghiêng đầu, ngáp hoặc giấu cả hai tay xuống dưới gầm bàn quá $15\text{ giây}$ để kiểm tra cảnh báo màu đỏ. Nhấn phím **`q`** để tắt cửa sổ Camera.

---

## BƯỚC 5: XÂY DỰNG PHẦN MỀM KHÓA MÁY 3 CẤP QUYỀN (`focus_desk_launcher.py`)

Đây là giao diện chính của phần mềm có thiết kế khóa toàn màn hình tương tự phần mềm phòng máy Gcafe/CSM, chia làm 3 cấp tài khoản: **Học sinh**, **Phụ huynh** và **Admin**.

### 5.1. Tạo tệp `focus_desk_launcher.py`
1. Mở Notepad hoặc VS Code, tạo tệp mới lưu vào thư mục `BanHocThongMinh` với tên `focus_desk_launcher.py`.
2. Sao chép và dán toàn bộ đoạn mã dưới đây vào tệp:

```python
import os
import sys
import json
import time
import winreg
import ctypes
import subprocess
import threading
import psutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

CONFIG_FILE = "shared_config.json"

# =====================================================================
# 1. QUẢN LÝ FILE CẤU HÌNH DÙNG CHUNG (SHARED CONFIG)
# =====================================================================
def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_cfg = {
            "passwords": {"admin": "admin123", "parent": "phuhuynh123", "student": "hocsinh"},
            "allowed_apps": [
                {"name": "Google Chrome", "exe_name": "chrome.exe", "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe"},
                {"name": "Notepad", "exe_name": "notepad.exe", "path": "notepad.exe"},
                {"name": "Calculator", "exe_name": "CalculatorApp.exe", "path": "calc.exe"}
            ],
            "allowed_folders": [r"C:\Users\Public\Documents"],
            "web_policy": {
                "blocked_domains": ["poki.com", "y8.com", "roblox.com", "facebook.com"],
                "ai_domains": ["chatgpt.com", "claude.ai", "gemini.google.com"],
                "max_ai_minutes": 15,
                "grace_period_seconds": 5
            },
            "telegram_config": {
                "bot_token": "YOUR_BOT_TOKEN_HERE",
                "chat_id": "YOUR_CHAT_ID_HERE",
                "enable_alerts": True,
                "alert_on_game": True,
                "alert_on_wrong_login": True,
                "alert_on_ai_overuse": True,
                "cooldown_seconds": 30
            }
        }
        save_config(default_cfg)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# =====================================================================
# 2. CƠ CHẾ KHÓA HỆ THỐNG WINDOWS (REGISTRY & EXPLORER SHELL)
# =====================================================================
def set_windows_task_manager_lock(lock_state: bool):
    """
    lock_state = True  -> Khóa cứng Task Manager gốc của Windows (DisableTaskMgr = 1)
    lock_state = False -> Mở lại Task Manager gốc cho Phụ huynh / Admin (DisableTaskMgr = 0)
    """
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 1 if lock_state else 0)
        winreg.CloseKey(key)
    except Exception as e:
        print(f"[WARN] Cần quyền Administrator để ghi Registry: {e}")

def set_desktop_explorer_state(enable_explorer: bool):
    """Ẩn hoặc khôi phục giao diện Desktop/Taskbar (explorer.exe) giống Gcafe"""
    try:
        if not enable_explorer:
            subprocess.run(["taskkill", "/f", "/im", "explorer.exe"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # Kiểm tra nếu explorer.exe chưa chạy thì bật lại
            running = [p.info['name'].lower() for p in psutil.process_iter(['name']) if p.info['name']]
            if "explorer.exe" not in running:
                subprocess.Popen("explorer.exe")
    except Exception as e:
        print(f"[ERR] Lỗi điều khiển explorer.exe: {e}")

# =====================================================================
# 3. CỔNG TÍCH HỢP MODULE KIỂM SOÁT WEB & BOT TELEGRAM
# =====================================================================
class BackgroundMonitorHook:
    """
    Class này dùng để gọi module kiểm soát web của Thành viên 3
    và gửi cảnh báo qua module Telegram Bot (File hướng dẫn số 2).
    """
    def __init__(self):
        self.running = False
        self.worker_thread = None
        self.usage_stats = {} # Lưu thời gian sử dụng từng ứng dụng (giây)

    def send_alert_to_telegram(self, event_type, message):
        """Gọi sang module telegram_service.py (nếu đã ghép file) hoặc ghi log"""
        try:
            from telegram_service import trigger_parent_alert
            trigger_parent_alert(event_type, message)
        except ImportError:
            print(f"[TELEGRAM HOOK - {event_type}] {message}")

    def start_monitoring(self):
        if self.running:
            return
        self.running = True
        self.worker_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.worker_thread.start()

    def stop_monitoring(self):
        self.running = False

    def _monitor_loop(self):
        """
        Vòng lặp chạy nền mỗi 2 giây khi Học sinh đang trong phiên học:
        1. Thống kê thời gian sử dụng ứng dụng cho Phụ huynh tra cứu.
        2. Chặn các phần mềm nằm ngoài Whitelist (nếu học sinh cố bật từ đường dẫn phụ).
        3. Gọi hàm kiểm tra Link Web (của Thành viên 3).
        """
        SYSTEM_WHITELIST = {
            "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "services.exe",
            "lsass.exe", "svchost.exe", "fontdrvhost.exe", "winlogon.exe", "dwm.exe",
            "sihost.exe", "taskhostw.exe", "conhost.exe", "ctfmon.exe", "python.exe",
            "pythonw.exe", "focusdesklauncher.exe", "cmd.exe"
        }

        while self.running:
            cfg = load_config()
            allowed_exes = {item["exe_name"].lower() for item in cfg["allowed_apps"]}

            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    pname = (proc.info['name'] or "").lower()
                    if pname in allowed_exes:
                        self.usage_stats[pname] = self.usage_stats.get(pname, 0) + 2
                except Exception:
                    continue

            time.sleep(2.0)

monitor_hook = BackgroundMonitorHook()

# =====================================================================
# 4. CỬA SỔ "TASK MANAGER BẢN HỌC SINH" (STUDENT TASK MANAGER)
# =====================================================================
def open_student_task_manager(parent_root):
    """
    Task Manager thu gọn dành riêng cho Học sinh:
    - Chỉ hiển thị các ứng dụng nằm trong danh sách học sinh được phép mở.
    - Ẩn tuyệt đối explorer.exe, python.exe và các tiến trình hệ thống.
    """
    cfg = load_config()
    allowed_exes = {item["exe_name"].lower() for item in cfg["allowed_apps"]}

    win = tk.Toplevel(parent_root)
    win.title("Task Manager - Phiên Bản Học Sinh")
    win.geometry("560x380")
    win.configure(bg="#111827")
    win.attributes("-topmost", True)

    tk.Label(win, text="QUẢN LÝ ỨNG DỤNG ĐANG CHẠY (CHỈ ĐƯỢC TẮT ỨNG DỤNG HỌC TẬP)",
             bg="#111827", fg="#38bdf8", font=("Segoe UI", 10, "bold")).pack(pady=12)

    columns = ("pid", "name", "ram")
    tree = ttk.Treeview(win, columns=columns, show="headings", height=11)
    tree.heading("pid", text="PID")
    tree.heading("name", text="Tên Ứng Dụng Cho Phép")
    tree.heading("ram", text="RAM Đang Dùng")
    tree.column("pid", width=90, anchor="center")
    tree.column("name", width=280, anchor="w")
    tree.column("ram", width=130, anchor="center")
    tree.pack(padx=16, fill="x")

    def refresh_student_procs():
        for item in tree.get_children():
            tree.delete(item)
        for p in psutil.process_iter(['pid', 'name', 'memory_info']):
            try:
                pname = (p.info['name'] or "").lower()
                # Ràng buộc cốt lõi: Chỉ hiện app trong Whitelist và KHÔNG bao giờ hiện explorer.exe
                if pname in allowed_exes and pname != "explorer.exe":
                    mem_mb = round(p.info['memory_info'].rss / (1024 * 1024), 1)
                    tree.insert("", "end", values=(p.info['pid'], p.info['name'], f"{mem_mb} MB"))
            except Exception:
                continue

    def kill_selected():
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Chưa chọn", "Vui lòng chọn một ứng dụng trong danh sách để đóng!", parent=win)
            return
        pid, name, _ = tree.item(sel[0])["values"]
        if str(name).lower() == "explorer.exe":
            return
        try:
            psutil.Process(int(pid)).terminate()
            time.sleep(0.3)
            refresh_student_procs()
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể đóng ứng dụng: {e}", parent=win)

    btn_bar = tk.Frame(win, bg="#111827")
    btn_bar.pack(pady=14)
    tk.Button(btn_bar, text="🔄 Làm mới", command=refresh_student_procs,
              bg="#334155", fg="white", font=("Segoe UI", 9, "bold"), padx=12, pady=5).pack(side="left", padx=8)
    tk.Button(btn_bar, text="❌ Kết thúc ứng dụng (Kill Task)", command=kill_selected,
              bg="#e11d48", fg="white", font=("Segoe UI", 9, "bold"), padx=12, pady=5).pack(side="left", padx=8)

    refresh_student_procs()

# =====================================================================
# 5. GIAO DIỆN CHÍNH 3 CẤP TÀI KHOẢN (GCAFE / CSM STYLE)
# =====================================================================
class FocusDeskApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart Study Desk - Cyber-Physical Access Control")
        self.root.configure(bg="#090d16")
        self.failed_attempts = 0

        # Chặn Alt+F4 đóng cửa sổ trái phép
        self.root.protocol("WM_DELETE_WINDOW", self.disable_close_event)
        self.show_lock_screen()

    def disable_close_event(self):
        pass

    def clear_ui(self):
        for w in self.root.winfo_children():
            w.destroy()

    # -----------------------------------------------------------------
    # MÀN HÌNH ĐĂNG NHẬP BAN ĐẦU (TOÀN MÀN HÌNH)
    # -----------------------------------------------------------------
    def show_lock_screen(self):
        self.clear_ui()
        monitor_hook.stop_monitoring()
        set_windows_task_manager_lock(True)
        # Lưu ý: Khi triển khai thật, bỏ comment dòng dưới để tắt explorer.exe ngay ở màn hình khóa
        # set_desktop_explorer_state(False)

        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)

        card = tk.Frame(self.root, bg="#1e293b", padx=45, pady=40,
                        highlightbackground="#06b6d4", highlightthickness=2)
        card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(card, text="HỆ THỐNG BÀN HỌC THÔNG MINH", bg="#1e293b", fg="#22d3ee",
                 font=("Segoe UI", 18, "bold")).pack(pady=(0, 6))
        tk.Label(card, text="Kiểm soát Kỷ luật Số & Phân quyền Truy cập 3 Cấp", bg="#1e293b", fg="#94a3b8",
                 font=("Segoe UI", 10)).pack(pady=(0, 24))

        tk.Label(card, text="Chọn phân cấp tài khoản:", bg="#1e293b", fg="white",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")

        self.role_var = tk.StringVar(value="student")
        roles = [
            ("👨‍🎓 Học sinh (Chỉ mở App/Thư mục cho phép & Bật giám sát)", "student"),
            ("👪 Phụ huynh (Cấu hình App, Link Web, Xem thời gian & Bot)", "parent"),
            ("🛠️ Admin (Toàn quyền hệ thống & Mở khóa Windows)", "admin")
        ]
        for label, val in roles:
            tk.Radiobutton(card, text=label, variable=self.role_var, value=val,
                           bg="#1e293b", fg="#f1f5f9", selectcolor="#0f172a",
                           activebackground="#1e293b", font=("Segoe UI", 10)).pack(anchor="w", pady=4)

        tk.Label(card, text="Mật khẩu:", bg="#1e293b", fg="white",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(16, 4))
        self.pwd_entry = tk.Entry(card, show="*", font=("Segoe UI", 13), width=30)
        self.pwd_entry.pack(pady=4)
        self.pwd_entry.bind("<Return>", lambda e: self.handle_login())
        self.pwd_entry.focus()

        tk.Button(card, text="ĐĂNG NHẬP HỆ THỐNG", command=self.handle_login,
                  bg="#0891b2", fg="white", font=("Segoe UI", 11, "bold"),
                  width=28, pady=8).pack(pady=(22, 0))

    def handle_login(self):
        cfg = load_config()
        role = self.role_var.get()
        pwd = self.pwd_entry.get().strip()

        if pwd == cfg["passwords"].get(role):
            self.failed_attempts = 0
            if role == "student":
                self.enter_student_shell()
            elif role == "parent":
                self.enter_parent_dashboard()
            elif role == "admin":
                self.enter_admin_mode()
        else:
            self.failed_attempts += 1
            messagebox.showwarning("Từ chối truy cập",
                                   f"Sai mật khẩu cấp [{role.upper()}]! (Lần thử: {self.failed_attempts})")
            if role in ["parent", "admin"] and self.failed_attempts >= 3:
                monitor_hook.send_alert_to_telegram(
                    "WRONG_LOGIN",
                    f"Phát hiện cố tình đăng nhập trái phép vào quyền [{role.upper()}] {self.failed_attempts} lần liên tiếp!"
                )

    # -----------------------------------------------------------------
    # CẤP 1: CHẾ ĐỘ HỌC SINH (KHÓA MÁY + STUDENT SHELL + CHẠY NỀN PYTHON)
    # -----------------------------------------------------------------
    def enter_student_shell(self):
        self.clear_ui()
        set_windows_task_manager_lock(True)
        # Bật luồng chạy nền kiểm soát Web và thống kê thời gian
        monitor_hook.start_monitoring()

        cfg = load_config()

        # Thanh công cụ phía trên
        header = tk.Frame(self.root, bg="#1e293b", height=64)
        header.pack(fill="x")
        tk.Label(header, text="👨‍🎓 KHÔNG GIAN HỌC TẬP TẬP TRUNG (CHẾ ĐỘ HỌC SINH)",
                 bg="#1e293b", fg="#34d399", font=("Segoe UI", 13, "bold")).pack(side="left", padx=24, pady=16)

        tk.Button(header, text="🔒 Khóa máy / Đổi tài khoản", command=self.show_lock_screen,
                  bg="#475569", fg="white", font=("Segoe UI", 10, "bold"), padx=14, pady=6).pack(side="right", padx=20)
        tk.Button(header, text="🧰 Task Manager (Bản Học sinh)", command=lambda: open_student_task_manager(self.root),
                  bg="#0284c7", fg="white", font=("Segoe UI", 10, "bold"), padx=14, pady=6).pack(side="right", padx=6)

        # Khu vực hiển thị các ứng dụng và thư mục được phép truy cập
        container = tk.Frame(self.root, bg="#090d16", padx=40, pady=30)
        container.pack(fill="both", expand=True)

        tk.Label(container, text="DANH SÁCH ỨNG DỤNG ĐƯỢC PHÉP SỬ DỤNG:",
                 bg="#090d16", fg="#38bdf8", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 14))

        apps_frame = tk.Frame(container, bg="#090d16")
        apps_frame.pack(fill="x", pady=(0, 30))

        for app_item in cfg["allowed_apps"]:
            btn = tk.Button(apps_frame, text=f"🖥️\n{app_item['name']}",
                            width=22, height=4, bg="#1e293b", fg="white",
                            font=("Segoe UI", 10, "bold"),
                            command=lambda p=app_item["path"]: self.launch_allowed_app(p))
            btn.pack(side="left", padx=10, pady=6)

        tk.Label(container, text="THƯ MỤC TÀI LIỆU ĐƯỢC PHÉP TRUY CẬP:",
                 bg="#090d16", fg="#38bdf8", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(10, 14))

        folders_frame = tk.Frame(container, bg="#090d16")
        folders_frame.pack(fill="x")

        for folder_path in cfg["allowed_folders"]:
            tk.Button(folders_frame, text=f"📁 {folder_path}",
                      bg="#334155", fg="#fde047", font=("Segoe UI", 10, "bold"),
                      padx=16, pady=10,
                      command=lambda fp=folder_path: self.open_allowed_folder(fp)).pack(side="left", padx=10, pady=6)

    def launch_allowed_app(self, app_path):
        try:
            subprocess.Popen(app_path)
        except Exception as e:
            messagebox.showerror("Lỗi mở ứng dụng", f"Không thể mở đường dẫn: {app_path}\nChi tiết: {e}")

    def open_allowed_folder(self, folder_path):
        if not os.path.exists(folder_path):
            os.makedirs(folder_path, exist_ok=True)
        os.startfile(folder_path)

    # -----------------------------------------------------------------
    # CẤP 2: CHẾ ĐỘ PHỤ HUYNH (QUẢN LÝ WHITELIST, LINK WEB, THỜI GIAN DÙNG)
    # -----------------------------------------------------------------
    def enter_parent_dashboard(self):
        self.clear_ui()
        set_windows_task_manager_lock(False)
        set_desktop_explorer_state(True)
        self.root.attributes("-fullscreen", False)
        self.root.attributes("-topmost", False)
        self.root.geometry("950x640")

        cfg = load_config()

        header = tk.Frame(self.root, bg="#1e293b", pady=12)
        header.pack(fill="x")
        tk.Label(header, text="👪 BẢNG ĐIỀU KHIỂN DÀNH CHO PHỤ HUYNH",
                 bg="#1e293b", fg="#38bdf8", font=("Segoe UI", 14, "bold")).pack(side="left", padx=20)
        tk.Button(header, text="🔒 Bật lại Khóa màn hình Học sinh", command=self.show_lock_screen,
                  bg="#e11d48", fg="white", font=("Segoe UI", 10, "bold"), padx=12).pack(side="right", padx=20)

        body = tk.Frame(self.root, bg="#090d16", padx=24, pady=18)
        body.pack(fill="both", expand=True)

        # Cột trái: Thêm/Xóa ứng dụng & thư mục cho phép
        left_col = tk.LabelFrame(body, text=" 1. Quản lý Ứng dụng & Thư mục Học sinh được phép dùng ",
                                 bg="#1e293b", fg="#38bdf8", font=("Segoe UI", 10, "bold"), padx=14, pady=12)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 10))

        app_listbox = tk.Listbox(left_col, bg="#0f172a", fg="white", font=("Segoe UI", 10), height=8)
        app_listbox.pack(fill="x", pady=6)
        for a in cfg["allowed_apps"]:
            app_listbox.insert("end", f"{a['name']} ({a['exe_name']})")

        def add_new_app():
            fpath = filedialog.askopenfilename(title="Chọn file .exe cho phép học sinh mở",
                                               filetypes=[("Executable", "*.exe")])
            if fpath:
                exe_name = os.path.basename(fpath)
                app_name = os.path.splitext(exe_name)[0].capitalize()
                cfg["allowed_apps"].append({"name": app_name, "exe_name": exe_name, "path": fpath})
                save_config(cfg)
                app_listbox.insert("end", f"{app_name} ({exe_name})")

        def remove_selected_app():
            sel = app_listbox.curselection()
            if sel:
                idx = sel[0]
                cfg["allowed_apps"].pop(idx)
                save_config(cfg)
                app_listbox.delete(idx)

        btn_row = tk.Frame(left_col, bg="#1e293b")
        btn_row.pack(fill="x", pady=6)
        tk.Button(btn_row, text="➕ Thêm ứng dụng (.exe)", command=add_new_app, bg="#0284c7", fg="white", padx=8).pack(side="left", padx=4)
        tk.Button(btn_row, text="🗑️ Xóa ứng dụng", command=remove_selected_app, bg="#475569", fg="white", padx=8).pack(side="left", padx=4)

        # Cột phải: Giới hạn Link Web, AI Quota & Thống kê thời gian
        right_col = tk.LabelFrame(body, text=" 2. Giới hạn Đường dẫn Web, Chatbot AI & Thời gian sử dụng ",
                                  bg="#1e293b", fg="#34d399", font=("Segoe UI", 10, "bold"), padx=14, pady=12)
        right_col.pack(side="right", fill="both", expand=True, padx=(10, 0))

        tk.Label(right_col, text="Danh sách tên miền cấm (cách nhau bằng dấu phẩy):", bg="#1e293b", fg="white").pack(anchor="w")
        domains_entry = tk.Entry(right_col, width=45, font=("Segoe UI", 10))
        domains_entry.insert(0, ", ".join(cfg["web_policy"]["blocked_domains"]))
        domains_entry.pack(fill="x", pady=(4, 12))

        tk.Label(right_col, text="Thời gian tối đa dùng ChatGPT / AI (phút/phiên):", bg="#1e293b", fg="white").pack(anchor="w")
        ai_limit_entry = tk.Entry(right_col, width=15, font=("Segoe UI", 10))
        ai_limit_entry.insert(0, str(cfg["web_policy"]["max_ai_minutes"]))
        ai_limit_entry.pack(anchor="w", pady=(4, 12))

        def save_policy():
            raw_domains = domains_entry.get().split(",")
            cfg["web_policy"]["blocked_domains"] = [d.strip() for d in raw_domains if d.strip()]
            cfg["web_policy"]["max_ai_minutes"] = int(ai_limit_entry.get() or 15)
            save_config(cfg)
            messagebox.showinfo("Thành công", "Đã lưu cấu hình kiểm soát Web và Chatbot AI!")

        tk.Button(right_col, text="💾 Lưu chính sách Web & AI", command=save_policy,
                  bg="#16a34a", fg="white", font=("Segoe UI", 10, "bold"), pady=6).pack(fill="x", pady=6)

        tk.Label(right_col, text="Thống kê thời gian học sinh mở ứng dụng:", bg="#1e293b", fg="#fde047",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(14, 4))
        stats_box = tk.Text(right_col, height=6, bg="#0f172a", fg="#e2e8f0", font=("Consolas", 10))
        stats_box.pack(fill="x")
        if not monitor_hook.usage_stats:
            stats_box.insert("end", "Chưa có dữ liệu phiên học hôm nay.\n")
        else:
            for k, sec in monitor_hook.usage_stats.items():
                stats_box.insert("end", f"• {k}: {round(sec/60, 1)} phút ({sec} giây)\n")

    # -----------------------------------------------------------------
    # CẤP 3: CHẾ ĐỘ ADMIN (MỞ TOÀN BỘ QUYỀN HỆ THỐNG)
    # -----------------------------------------------------------------
    def enter_admin_mode(self):
        set_windows_task_manager_lock(False)
        set_desktop_explorer_state(True)
        monitor_hook.stop_monitoring()
        if messagebox.askyesno("Quyền Quản Trị Viên (Admin)",
                               "Đã mở khóa Task Manager gốc và Windows Explorer.\nBạn có muốn đóng hoàn toàn trình khóa máy để bảo trì hệ thống không?"):
            self.root.destroy()
            sys.exit(0)
        else:
            self.enter_parent_dashboard()

if __name__ == "__main__":
    root = tk.Tk()
    app = FocusDeskApp(root)
    root.mainloop()
```

### 5.2. Cách kiểm thử Bước 5
1. Mở cửa sổ lệnh `cmd` và chạy:
   ```bash
   python focus_desk_launcher.py
   ```
2. **Thử quyền Học sinh:** Chọn ô Học sinh, nhập mật khẩu `hocsinh`. Giao diện học tập hiện ra, em chỉ có thể mở các ứng dụng trên màn hình và bật **Task Manager (Bản Học sinh)**.
3. **Thử cảnh báo vượt quyền:** Bấm nút **Khóa máy / Đổi tài khoản**, chọn ô **Phụ huynh** và cố tình nhập mật khẩu sai $3\text{ lần}$ liên tiếp $\rightarrow$ Kiểm tra điện thoại xem Bot Telegram đã gửi cảnh báo đỏ về chưa.
4. **Thoát chương trình an toàn:** Chọn ô **Admin**, nhập mật khẩu `admin123` và nhấn **Yes** để đóng hoàn toàn trình khóa máy.

---

## BƯỚC 6: ĐÓNG GÓI THÀNH TỆP `.EXE` & CÀI ĐẶT TỰ KHỞI ĐỘNG

Sau khi đã kiểm tra mọi tính năng hoạt động trơn tru, bước cuối cùng là đóng gói mã nguồn thành một tệp thực thi `.exe` duy nhất chạy quyền Quản trị viên (Administrator).

### 6.1. Kích hoạt chế độ ẩn thanh Taskbar (Tùy chọn khi chạy thực tế)
Nếu muốn khi khóa máy, thanh công cụ Taskbar bên dưới màn hình biến mất hoàn toàn giống phòng máy Gcafe:
1. Mở tệp `focus_desk_launcher.py`.
2. Tìm đến hàm `show_lock_screen(self):` và xóa dấu `#` ở trước dòng `# set_desktop_explorer_state(False)`.
3. Lưu tệp lại. *(Khi đăng nhập quyền Phụ huynh hoặc Admin, thanh Taskbar sẽ tự động hiện trở lại).*

### 6.2. Chạy lệnh đóng gói ra tệp `.EXE`
1. Mở cửa sổ dòng lệnh `cmd` tại thư mục `BanHocThongMinh`.
2. Sao chép câu lệnh dưới đây dán vào và nhấn **Enter**:

```bash
pyinstaller --onefile --noconsole --uac-admin --name FocusDeskLauncher focus_desk_launcher.py
```

3. Chờ khoảng $1\text{ phút}$ để PyInstaller đóng gói xong.
4. Mở thư mục con tên là `dist` vừa xuất hiện trong thư mục dự án, copy tệp **`FocusDeskLauncher.exe`** đem ra ngoài thư mục `BanHocThongMinh` (đặt nằm ngay cạnh tệp `shared_config.json`).

### 6.3. Thiết lập tự động bật khi mở máy tính (Windows Task Scheduler)
1. Nhấn phím **Windows**, gõ tìm kiếm **`Task Scheduler`** và mở công cụ này lên.
2. Nhấn vào mục **Create Task...** ở cột bên phải:
   * **Thẻ General:** Đặt tên là `FocusDeskAutoStart`, đánh dấu tích vào ô **`Run with highest privileges`** (Chạy với quyền cao nhất).
   * **Thẻ Triggers:** Nhấn **New...** $\rightarrow$ Ở mục *Begin the task*, chọn **`At log on`** (Khi đăng nhập máy tính) $\rightarrow$ Nhấn OK.
   * **Thẻ Actions:** Nhấn **New...** $\rightarrow$ Nhấn **Browse...** và chọn tới tệp `FocusDeskLauncher.exe` trong thư mục `BanHocThongMinh`, đồng thời ở ô **Start in (optional)** điền đường dẫn thư mục chứa tệp (ví dụ: `D:\BanHocThongMinh`) $\rightarrow$ Nhấn OK.
3. Nhấn **OK** để hoàn tất. Từ bây giờ, mỗi khi bật máy tính, màn hình khóa 3 cấp quyền sẽ tự động kích hoạt!