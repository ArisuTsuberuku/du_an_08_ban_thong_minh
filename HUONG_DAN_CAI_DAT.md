# FocusDesk — Hướng dẫn cài đặt và cấu hình khóa

Tài liệu cho người cài FocusDesk lên máy học sinh và người quản lý máy chủ web phụ huynh (Vercel).

## 1. Tạo bộ cài một file (trên máy phát triển)

1. Chạy `TAO_BO_CAI.bat`. Lần đầu nó hỏi cài **Inno Setup 6** (miễn phí) bằng winget: chọn `Y`.
2. Nó hỏi có kèm bộ cài Python 3.14 (khoảng 28 MB) không. Chọn `Y` nếu máy đích có thể chưa có Python.
3. Kết quả: `dist_installer\FocusDesk_Setup.exe`. Gửi file này sang máy khác, nháy đúp, bấm Next.

Bộ cài chép phần mềm vào `C:\FocusDesk`, hỏi địa chỉ web phụ huynh, cài thư viện Python (cần Internet, 3–10 phút),
tạo lối tắt, và tùy chọn tự mở khi đăng nhập Windows. Gỡ cài đặt trong *Settings → Apps* trả Windows về bình thường
(Task Manager, file hosts, taskbar, tác vụ tự mở).

## 2. Windows báo "Windows protected your PC" (SmartScreen)

**Vì sao:** bộ cài chưa có chữ ký số, và file được tải qua Internet (Zalo, Drive, email, trình duyệt) bị Windows gắn
"dấu tải về" (Mark of the Web). SmartScreen chặn mọi file chưa có chữ ký và chưa có uy tín khi file mang dấu này.
File chạy ngay trên máy đã tạo ra nó thì không bị hỏi.

**Cách cài ngay, chọn một:**

| Cách | Làm thế nào |
| --- | --- |
| Bấm qua cảnh báo | Bấm **More info** (Thông tin thêm) → **Run anyway** (Vẫn chạy). Chỉ hỏi lần đầu với file đó |
| Gỡ dấu tải về | Chuột phải `FocusDesk_Setup.exe` → **Properties** → tick **Unblock** → OK, rồi chạy lại |
| Gỡ dấu bằng lệnh | PowerShell: `Unblock-File "$env:USERPROFILE\Downloads\FocusDesk_Setup.exe"` |
| Chép bằng USB | Chép file từ máy tạo bộ cài sang USB rồi sang máy đích: file không mang dấu tải về nên không bị hỏi |
| Gửi file nén | Nếu gửi `.zip`: chuột phải file zip → Properties → **Unblock** **trước khi giải nén** |

**Cách làm cho người nhận không còn thấy cảnh báo (lâu dài):**

- **Ký số bộ cài.** `TAO_BO_CAI.bat` tự ký nếu bạn đặt biến môi trường `FOCUSDESK_SIGN_SHA1` = dấu vân tay
  (thumbprint) của chứng thư ký mã đã cài trong Windows, và máy có `signtool.exe` (cài kèm *Windows SDK*).
- Chứng thư ký mã OV mua từ các hãng như DigiCert, Sectigo, Certum: khoảng 150–300 USD/năm.
  Dịch vụ Azure Artifact Signing (khoảng 9,99 USD/tháng) hiện chỉ nhận cá nhân ở Mỹ và Canada.
- Kể cả khi đã ký, SmartScreen vẫn có thể cảnh báo các bản đầu cho tới khi file tích đủ uy tín (nhiều lượt cài an toàn).
  Chứng thư EV không còn được bỏ qua cảnh báo ngay từ năm 2024.
- Với một dự án dùng trong trường hoặc gia đình, cách thực tế nhất là chép bằng USB hoặc bấm *Run anyway* một lần.

## 3. Khóa VAPID — thông báo đẩy lên điện thoại phụ huynh kể cả khi đã đóng web

Không có khóa VAPID, web phụ huynh chỉ hiện thông báo khi trang đang mở.

1. Trên máy có mã nguồn, mở cửa sổ lệnh tại thư mục dự án và chạy:
   ```
   python cloud\tao_khoa_vapid.py
   ```
   Nếu báo thiếu thư viện: `python -m pip install cryptography` rồi chạy lại.
2. Màn hình in ra 3 dòng `VAPID_PUBLIC_KEY=…`, `VAPID_PRIVATE_KEY=…`, `VAPID_SUBJECT=…`.
   **Khóa PRIVATE là bí mật**: không gửi qua chat, không commit lên GitHub.
3. Mở https://vercel.com → project của web phụ huynh → **Settings → Environment Variables**. Thêm 3 biến:

   | Tên | Giá trị | Ghi chú |
   | --- | --- | --- |
   | `VAPID_PUBLIC_KEY` | dòng PUBLIC vừa in | Công khai, gửi cho trình duyệt |
   | `VAPID_PRIVATE_KEY` | dòng PRIVATE vừa in | Bật **Sensitive** |
   | `VAPID_SUBJECT` | `mailto:email-cua-ban@gmail.com` | Email liên hệ của người vận hành |

   Chọn môi trường **Production** (và Preview nếu dùng) → **Save**.
4. Vào **Deployments** → bản mới nhất → **⋯ → Redeploy**. Biến môi trường chỉ có hiệu lực ở bản deploy mới.
5. Kiểm tra: mở `https://banhocthongminh.vercel.app/api/health` → phải thấy `"web_push": true`.
6. Trên điện thoại phụ huynh: mở web phụ huynh → **🔔 Bật thông báo** → cho phép → **Thử thông báo**.
   - iPhone: phải **Thêm vào Màn hình chính** (Share → Add to Home Screen) rồi mở từ biểu tượng đó; Safari thường
     không nhận thông báo đẩy của web.
   - Android: Chrome nhận được ngay.

Đổi khóa VAPID về sau thì mọi điện thoại phải bấm **Bật thông báo** lại.

## 4. Khóa Gemini API — AI phân loại trang web lạ và hoạt động

Không có khóa, web phụ huynh vẫn chạy và dùng danh sách dựng sẵn cùng gợi ý theo tiêu đề. Có khóa thì
tên miền lạ (ví dụ một trang game mới) được Gemini phân loại, kết quả lưu lại để không hỏi lại.

1. Mở https://aistudio.google.com/apikey bằng tài khoản Google → **Create API key** → chọn hoặc tạo một project →
   sao chép khóa (bắt đầu bằng `AIza…`). **Đây là bí mật.**
2. Vercel → project → **Settings → Environment Variables** → thêm:

   | Tên | Giá trị | Bắt buộc |
   | --- | --- | --- |
   | `GEMINI_API_KEY` | khóa vừa tạo (bật **Sensitive**) | Có |
   | `GEMINI_MODEL` | danh sách model cách nhau dấu phẩy, mặc định `gemini-flash-lite-latest,gemini-flash-latest,gemini-2.0-flash` | Không |
   | `ACTIVITY_AI` | `1` (bật, mặc định) hoặc `0` (tắt AI phân loại hoạt động) | Không |

3. **Redeploy** như bước VAPID.
4. Kiểm tra: `https://banhocthongminh.vercel.app/api/health` → `"gemini": true`.

Lưu ý:

- Khóa Gemini chỉ nằm trên Vercel. Máy học sinh **không** cần và **không** giữ khóa này.
- Gói miễn phí của Gemini có giới hạn số lượt gọi; phần mềm có bộ nhớ đệm kết quả nên số lượt dùng thấp.
- Nếu lỡ để lộ khóa: vào AI Studio xóa khóa đó, tạo khóa mới, cập nhật Vercel và Redeploy.
- Có thể giới hạn khóa chỉ dùng cho *Generative Language API* trong Google Cloud Console → APIs & Services → Credentials.

## 5. Bảng biến môi trường của web phụ huynh (Vercel)

| Biến | Bắt buộc | Ý nghĩa |
| --- | --- | --- |
| `SECRET_KEY` | Có | Khóa ký phiên đăng nhập (chuỗi ngẫu nhiên dài) |
| `FIREBASE_DB_URL`, `FIREBASE_SERVICE_ACCOUNT` | Có | Cơ sở dữ liệu Firebase Realtime Database |
| `GOOGLE_CLIENT_ID` | Có | Đăng nhập bằng Google |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | Có | Bot Telegram báo phụ huynh |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Không | Thông báo đẩy (mục 3) |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `ACTIVITY_AI` | Không | AI phân loại (mục 4) |
| `ALLOWED_EMAILS` | Không | Chỉ cho các email này đăng nhập (cách nhau dấu phẩy) |
| `AGENT_HEARTBEAT_S`, `AGENT_FAST_HEARTBEAT_S` | Không | Chu kỳ máy học sinh báo về (mặc định 15 s, và 2 s khi phụ huynh đang mở web hoặc vừa có lệnh). Nhiều máy (> 6) thì tăng lên 20–30 s |
| `TZ_OFFSET_H` | Không | Múi giờ, mặc định 7 |

Máy học sinh đọc địa chỉ web từ `secrets.local.json` (`"cloud_url"`) hoặc biến `FOCUSDESK_CLOUD_URL`
(tên cũ `SMARTDESK_CLOUD_URL` vẫn được đọc).

## 6. Hộp khóa điện thoại

Xem `firmware/GIAO_THUC.md`: chuẩn giao tiếp, ghép hộp, Serial monitor gỡ lỗi trong trang Admin, danh sách nghiệm thu.
