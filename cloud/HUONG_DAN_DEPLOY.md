# Triển khai bản Cloud (Vercel + Firebase RTDB + Telegram)

## Kiến trúc

```
Máy học sinh (FocusDeskLauncher / trang_web.py + services/cloud_agent.py)
   │  HTTPS gọi ra 30s/lần · báo NGAY khi có cảnh báo · 5s/lần khi phụ huynh đang xem
   ▼
Vercel  cloud/app.py (Flask)  ──REST + Service Account──▶  Firebase Realtime Database (Rules: cấm client)
   ▲                 ▲
   │ web phụ huynh   │ webhook
Phụ huynh A, B…    Telegram (1 bot, token chỉ nằm trong env Vercel)
```

- 1 phụ huynh quản lý nhiều máy; 1 máy có nhiều phụ huynh (bố + mẹ). Quan hệ nhiều–nhiều lưu ở
  `parents/{uid}/devices` và `devices/{did}/owners`.
- Mỗi phụ huynh liên kết Telegram riêng qua link `t.me/<bot>?start=<mã 1 lần>` → `chat_id` gắn với tài khoản.
  Cảnh báo của máy X chỉ gửi tới chat của các phụ huynh sở hữu máy X.
- Máy học sinh không giữ token bot. Mỗi máy có `device_id + device_token` riêng (`device_identity.json`);
  cloud chỉ lưu SHA-256 của token.
- Ảnh camera chỉ gửi khi phụ huynh bấm "Chụp 1 ảnh" (web) hoặc `/anh` (Telegram); ảnh hết hạn sau 5 phút.

## 1. Firebase

1. console.firebase.google.com → tạo project → **Realtime Database** → Create (chọn asia-southeast1).
2. Tab **Rules** → dán nội dung `cloud/database.rules.json` (cấm mọi client đọc/ghi) → Publish.
3. Project settings → **Service accounts** → *Generate new private key* → tải file JSON.
   **Không commit file này.** Mở file, copy toàn bộ nội dung để dán vào Vercel ở bước 3.

> Không dùng lại database của DuAnAI: DB đó đang mở công khai (ai có URL cũng đọc/ghi được).

## 2. Telegram

1. @BotFather → `/newbot` → lấy token. (Bot cũ dùng tiếp được; nếu token từng dán vào code/Zalo → `/revoke` để lấy token mới.)
2. Tạo 1 chuỗi ngẫu nhiên làm `TELEGRAM_WEBHOOK_SECRET`, ví dụ chạy:
   `python -c "import secrets;print(secrets.token_hex(24))"`

## 3. Vercel

Trang **New Project** (import `du_an_08_ban_thong_minh`):

| Mục | Giá trị |
|---|---|
| Root Directory | **`cloud`** (bấm Edit, chọn thư mục cloud) |
| Application Preset | Flask |
| Environment Variables | bảng dưới |

| Biến | Giá trị |
|---|---|
| `SECRET_KEY` | chuỗi ngẫu nhiên 48 ký tự (`secrets.token_hex(24)`) |
| `FIREBASE_DB_URL` | `https://<project>-default-rtdb.asia-southeast1.firebasedatabase.app` |
| `FIREBASE_SERVICE_ACCOUNT` | toàn bộ nội dung file JSON service account |
| `TELEGRAM_BOT_TOKEN` | token BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | chuỗi ngẫu nhiên ở bước 2 |
| `GOOGLE_CLIENT_ID` | Client ID OAuth (mục 3b) — bật đăng nhập Google, tự TẮT đăng nhập mật khẩu |
| `ALLOWED_EMAILS` | (tùy chọn) `a@gmail.com,b@gmail.com` hoặc `@truong.edu.vn` — chỉ những email này được vào |
| `GEMINI_API_KEY` | (tùy chọn) khóa Gemini MỚI để phân loại hoạt động Học tập/Giải trí; trống = phân loại theo luật |
| `GEMINI_MODEL` | (tùy chọn) mặc định `gemini-flash-lite-latest,gemini-flash-latest,gemini-2.0-flash` |
| `AGENT_HEARTBEAT_S` | `30` (mặc định; chu kỳ báo bình thường) |
| `AGENT_FAST_HEARTBEAT_S` | `5` (khi phụ huynh đang mở web / vừa gửi lệnh, trong `AGENT_FAST_WINDOW_S`=120s) |

### 3b. Đăng nhập Google (khuyến nghị)

1. console.cloud.google.com (chọn đúng project Firebase) → **APIs & Services → OAuth consent screen**:
   User type *External*, tên app "Bàn Học Thông Minh", email hỗ trợ → Save. Khi thử: thêm email phụ huynh vào *Test users*
   (hoặc bấm *Publish app* để ai có Gmail cũng đăng nhập được — vẫn bị giới hạn bởi `ALLOWED_EMAILS` nếu đặt).
2. **Credentials → Create credentials → OAuth client ID → Web application**:
   *Authorized JavaScript origins*: `https://banhocthongminh.vercel.app` (thêm `http://localhost:8000` nếu chạy thử máy).
   Không cần Redirect URI. Copy **Client ID** (dạng `xxxx.apps.googleusercontent.com`) → biến `GOOGLE_CLIENT_ID` → Redeploy.
3. Server tự xác minh chữ ký Google, hạn token, audience = Client ID, email đã xác minh; phiên có CSRF token,
   cookie HttpOnly/Secure/SameSite=Lax, HSTS.

Deploy xong:
1. Mở `https://<ten>.vercel.app/api/health` → phải thấy `"db": "firebase"`, `"telegram": true`.
2. Mở **1 lần** `https://<ten>.vercel.app/api/telegram/setup?key=<TELEGRAM_WEBHOOK_SECRET>` để đăng ký webhook.

## 4. Máy học sinh

1. Tạo `secrets.local.json` cạnh `trang_web.py` (hoặc cạnh `FocusDeskLauncher.exe`):
   ```json
   { "cloud_url": "https://<ten>.vercel.app" }
   ```
   Để trống `telegram_bot_token` khi dùng cloud — nếu điền, poller Telegram cục bộ sẽ tranh chấp với webhook.
2. Chạy `python trang_web.py` (hoặc FocusDeskLauncher.exe) → mở `http://127.0.0.1:5000/ket-noi-cloud` → thấy **mã ghép 6 số**.
3. Phụ huynh vào web Vercel → Đăng ký → **Thêm máy học sinh** → nhập mã → **Kết nối Telegram**.

## Lệnh Telegram

`/may` danh sách máy · `/status [số]` · `/lock [số]` · `/reset_ai [số]` · `/anh [số]` · `/help`

## Chạy thử không cần Firebase/Telegram

```bash
cd cloud && pip install -r requirements.txt
TELEGRAM_WEBHOOK_SECRET=test python app.py      # dùng DB JSON cục bộ %TEMP%\focusdesk_local_db.json, Telegram in ra console
```

## Xem camera trực tiếp (không qua Firebase/Vercel)

- Phụ huynh bấm **📺 Xem trực tiếp** → máy mở link có token ngẫu nhiên, hết hạn 15 phút:
  - Cùng Wi-Fi: `http://<IP-LAN>:5000/live?t=...` (nút **🏠 Mở trong Wi-Fi nhà**)
  - Ở ngoài: Cloudflare Quick Tunnel `https://xxx.trycloudflare.com/live?t=...` — cần cài 1 lần trên máy học sinh:
    `winget install --id Cloudflare.cloudflared`
- Qua Internet chỉ mở `/live`, `/video_feed`, `/snapshot.jpg` kèm token; mọi API khác của web nội bộ trả 403.
- Firebase chỉ lưu đường link (vài chục byte). Ảnh `/anh` trên Telegram được chuyển thẳng, không lưu.

## Báo cáo hoạt động (port từ DuAnAI)

- Máy ghi cửa sổ đang dùng mỗi 3s (bỏ qua khi rời máy >3 phút, ẩn tiêu đề nhạy cảm), gửi kèm heartbeat ~60s/lô.
- Server phân loại Học tập/Giải trí/Khác (Gemini nếu có `GEMINI_API_KEY`, ngược lại theo luật), lưu `activity/{máy}/{ngày}`.
- Dashboard: **📊 Báo cáo hoạt động** (chọn ngày, thanh tỉ lệ, bảng chi tiết) và **🔒 Cấu hình chặn từ xa**
  (app / tên miền / từ khóa → đồng bộ vào `shared_config.json` của máy).
