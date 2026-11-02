# TÀI LIỆU KỸ THUẬT SỐ 2: XÂY DỰNG BOT TELEGRAM CẢNH BÁO PHỤ HUYNH & TRANG WEB THIẾT LẬP THUỘC TÍNH BOT

## 1. Mục tiêu & Đặc tả Giao việc cho Antigravity (Prompt Blueprint)

> **Prompt mẫu để bạn copy dán thẳng vào Antigravity:**
> "Dựa trên tài liệu `02_huong_dan_build_bot_telegram_va_web_config.md`, hãy xây dựng module `telegram_service.py` và tích hợp giao diện cấu hình Bot Telegram lên Website trung tâm (chạy bằng Flask). Hệ thống phải đọc/ghi trực tiếp vào file `shared_config.json` (cùng file cấu hình với ứng dụng `.exe`), cho phép phụ huynh chỉnh sửa trực tiếp trên Website các thuộc tính của Bot gồm: `bot_token`, `chat_id`, bật/tắt cảnh báo mở Game, bật/tắt cảnh báo đăng nhập sai quyền Admin/Phụ huynh, thiết lập ngưỡng số phút tối đa được dùng ChatGPT/AI (`max_ai_minutes`), và thời gian giãn cách chống spam (`cooldown_seconds`). Đồng thời hỗ trợ Bot nhận lệnh phản hồi hai chiều từ điện thoại phụ huynh như `/status` và `/lock`."

---

## 2. Quy tắc Bảo mật & 4 Kịch bản Bắn Cảnh báo Tự động

Để tuân thủ chặt chẽ nguyên tắc **Xử lý cục bộ, bảo mật quyền riêng tư (Local-first, Zero-cloud)** đã cam kết trong báo cáo khoa học kỹ thuật, Bot Telegram **chỉ gửi siêu dữ liệu cảnh báo (Metadata & Chỉ số tập trung $\text{CFI}$)**, tuyệt đối không chụp ảnh màn hình tin nhắn cá nhân hay gửi ảnh khuôn mặt học sinh lên đám mây.

| Mã Sự kiện | Tình huống Bất thường Kích hoạt | Nội dung Tin nhắn Gửi về Điện thoại Phụ huynh |
| :--- | :--- | :--- |
| `GAME_DETECTED` | Học sinh truy cập đường dẫn Web Game (`poki.com`, `roblox.com`...) hoặc bật tiến trình Game trong Task Manager. | 🚨 **[CẢNH BÁO VI PHẠM KỶ LUẬT SỐ]** Phát hiện mở trang web/ứng dụng trò chơi: `{tên_game_hoặc_url}`. Hệ thống đã đếm ngược $5\text{ s}$ ân hạn và tự động đóng! |
| `WRONG_LOGIN` | Học sinh cố tình thử mật khẩu cấp **Phụ huynh** hoặc **Admin** sai liên tiếp $\ge 3\text{ lần}$. | 🔐 **[CẢNH BÁO VƯỢT QUYỀN HỆ THỐNG]** Phát hiện học sinh thử đăng nhập trái phép vào tài khoản `{role}` 3 lần liên tiếp! |
| `AI_OVERUSE` | Tổng thời gian mở các tab `chatgpt.com`, `claude.ai`, `gemini.google.com` vượt quá ngưỡng `max_ai_minutes` (mặc định $15\text{ phút}$). | 🤖 **[CẢNH BÁO LẠM DỤNG CHATBOT AI]** Học sinh đã sử dụng công cụ AI (`{url}`) vượt quá `{max_ai_minutes}` phút quy định trong phiên học! |
| `POSTURE_OR_HW` | Điểm tập trung $\text{CFI} < 50\%$ kéo dài hoặc phát hiện rút cáp điện thoại khỏi hộp khóa `ESP32-S3`. | ⚠️ **[CẢNH BÁO TẬP TRUNG & PHẦN CỨNG]** Điểm tập trung hiện tại giảm xuống `{cfi}%` hoặc kết nối hộp khóa điện thoại bị gián đoạn! |

---

## 3. Các bước Lấy `Bot Token` và `Chat ID` trong 2 phút

1. Mở ứng dụng Telegram trên điện thoại hoặc máy tính, tìm kiếm tài khoản **`@BotFather`** (có dấu tích xanh).
2. Gửi lệnh `/newbot` $\rightarrow$ Đặt tên hiển thị cho Bot (ví dụ: `Giám Sát Bàn Học Thông Minh`) $\rightarrow$ Đặt username có đuôi `bot` (ví dụ: `FocusDeskMonitor_bot`).
3. Copy chuỗi **HTTP API Token** (dạng `7123456789:AAH...`) mà `@BotFather` cấp.
4. Bấm vào đường link Bot vừa tạo và nhấn nút **Start** (gửi 1 tin nhắn bất kỳ như `Xin chào`).
5. Mở trình duyệt truy cập đường dẫn: `https://api.telegram.org/bot<DÁN_TOKEN_VÀO_ĐÂY>/getUpdates` $\rightarrow$ Tìm trường `"chat":{"id": 123456789}` để lấy mã **`Chat ID`** của phụ huynh.

---

## 4. Mã nguồn Module Telegram & Web Cấu hình (`telegram_service.py`)

Tệp này vừa đóng vai trò là thư viện để App `.exe` và AI Camera gọi hàm `trigger_parent_alert()`, vừa cung cấp giao diện Website để Phụ huynh chỉnh sửa thuộc tính Bot trực tiếp trên trình duyệt:

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