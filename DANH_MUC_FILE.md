# FocusDesk — Danh mục toàn bộ file

Mọi file trong thư mục dự án (quét trên máy MSI ngày 03/11/2026), **xếp từ nhỏ đến lớn**. Không tính bộ cài
(`dist_installer/`, `installer/redist/`), lịch sử git (`.git/`) và cache Python (`__pycache__/`).
Cột cuối: file dùng làm gì và phần cấu trúc cần biết khi sửa.

## Sơ đồ phụ thuộc

```
MO_FOCUSDESK.bat ──▶ he_thong_quan_ly_3_cap.py (app 3 cấp, giao diện Tkinter)
                        ├─ core/config_manager.py   (shared_config.json + runtime_state.json)
                        ├─ core/failsafe.py         (thoát khẩn cấp, Task Manager)
                        ├─ services/kiosk_guard.py  (khóa máy khi học sinh học)
                        ├─ services/giam_sat_worker.py ─ activity_tracker.py, hosts_blocker.py
                        ├─ services/posture_controller.py ─▶ services/posture_ai_engine.py
                        │                                       ├─ attention_model.py, action_recognizer.py
                        │                                       └─ camera_selector.py, camera_setup.py, desk_camera.py
                        ├─ services/phone_box.py ◀─USB/COM─▶ firmware/hop_dien_thoai_esp32s3
                        └─ trang_web.py (web nội bộ :5000) ─ services/cloud_agent.py ─HTTPS─▶ cloud/app.py (Vercel)
                                                            └─ services/live_view.py (camera trực tiếp qua cloudflared)
```

## Bảng file (nhỏ → lớn)

| # | File | Kích thước | Nhóm | Dùng làm gì · cấu trúc quan trọng |
|---|---|---|---|---|
| 1 | `DuAnAI/mo_khoa_admin.txt` | 0 B | Dự án cũ | File cờ rỗng của tracker cũ (mở khóa admin). Không còn dùng |
| 2 | `DuAnAI/01_11/Gemini API Key.txt` | 53 B | Bí mật cũ | Khóa Gemini của tracker cũ. **Bí mật**, nằm ngoài GitHub (.gitignore); không mở, không gửi ai. Nên xóa khi không cần |
| 3 | `cloud/.gitignore` | 54 B | Cấu hình git | Không đưa lên GitHub: `.vercel`, cache Python, file tạm, file khóa service-account Firebase |
| 4 | `secrets.local.json` | 56 B | Bí mật máy | Địa chỉ web phụ huynh của máy này (`cloud_url`). Không lên GitHub. Bộ cài tự tạo |
| 5 | `cloud/requirements.txt` | 59 B | Web phụ huynh | Thư viện Vercel cài khi deploy: Flask, requests, google-auth, pywebpush |
| 6 | `cloud/database.rules.json` | 61 B | Web phụ huynh | Luật Firebase: chặn mọi đọc/ghi từ trình duyệt; chỉ máy chủ (service account) truy cập |
| 7 | `core/__init__.py` | 71 B | Lõi | Đánh dấu thư mục `core` là gói Python |
| 8 | `services/__init__.py` | 84 B | Dịch vụ | Đánh dấu thư mục `services` là gói Python |
| 9 | `secrets.local.example.json` | 84 B | Mẫu | Mẫu `secrets.local.json` (chỉ có `cloud_url`, token để trống) |
| 10 | `DuAnAI/01_11/Firebase.txt` | 85 B | Bí mật cũ | Thông tin Firebase của tracker cũ. **Bí mật**, ngoài GitHub; không mở |
| 11 | `DuAnAI/secrets.local.example.json` | 150 B | Dự án cũ | Mẫu bí mật của tracker cũ (`GEMINI_API_KEY`, `FIREBASE_URL`) |
| 12 | `chay_ai_tu_the.bat` | 214 B | Khởi chạy | Mở cửa sổ xem thử AI camera bằng `pythonw posture_ai_engine.py` |
| 13 | `device_identity.json` | 228 B | Bí mật máy | Mã máy + token để máy nói chuyện với web phụ huynh (`device_id`, `device_token`). Không lên GitHub |
| 14 | `installer/KIEM_TRA.bat` | 242 B | Bộ cài | Lối tắt Kiểm tra / Sửa lỗi cài đặt: gọi `kiem_tra_cai_dat.py %*` (thêm `--sua` để tự sửa) |
| 15 | `DuAnAI/build/WinSystemHost/localpycs/struct.pyc` | 308 B | Dự án cũ | Tệp trung gian PyInstaller (mã Python đã biên dịch) của tracker cũ — có thể xóa |
| 16 | `DuAnAI/build/AppActivityTracker/localpycs/struct.pyc` | 308 B | Dự án cũ | Tệp trung gian PyInstaller (mã Python đã biên dịch) của tracker cũ — có thể xóa |
| 17 | `.gitignore` | 670 B | Cấu hình git | Liệt kê file không đưa lên GitHub: bí mật, log, `dist_installer/`, `installer/redist/`, dữ liệu riêng máy |
| 18 | `DuAnAI/AppActivityTracker.spec` | 712 B | Dự án cũ | Cấu hình PyInstaller của tracker cũ |
| 19 | `dist/KHOI_PHUC_KHAN_CAP.bat` | 873 B | Bản cũ | Bản khôi phục khẩn cấp đi kèm `.exe` cũ trong `dist/` |
| 20 | `requirements.txt` | 905 B | Cài đặt | Thư viện Python của máy học sinh. Ghim `opencv-contrib-python<5` (OpenCV 5 bỏ bộ nhận mặt Haar), mediapipe ≥1.0, ai-edge-litert, pyserial… |
| 21 | `MO_FOCUSDESK.bat` | 995 B | Khởi chạy | Mở FocusDesk hằng ngày: kiểm quyền Admin (`net session`) → tự xin quyền → tìm Python 3.14 → chạy `he_thong_quan_ly_3_cap.py` |
| 22 | `KHOI_PHUC_KHAN_CAP.bat` | 1.0 KB | An toàn | Trả Windows về bình thường: tắt FocusDesk (cả tên cũ), bật lại Task Manager, khởi động lại explorer |
| 23 | `giam_sat.pyw` | 1.0 KB | Khởi chạy | Chạy riêng bộ giám sát web/app (tương thích bản cũ của nhóm); gọi `BoGiamSatThongMinh` |
| 24 | `installer/go_cai_dat.bat` | 1.2 KB | Bộ cài | Chạy khi gỡ cài đặt: tắt tiến trình, xóa tác vụ tự mở (tên mới + cũ), bật Task Manager, gỡ dòng chặn trong hosts, mở lại explorer |
| 25 | `FocusDeskLauncher.spec` | 1.3 KB | Đóng gói | Cấu hình PyInstaller cho bản `.exe` (không còn bắt buộc vì đã có bộ cài một file) |
| 26 | `dist/runtime_state.json` | 1.5 KB | Bản cũ | Trạng thái đi kèm bản `.exe` cũ |
| 27 | `DuAnAI/build/AppActivityTracker/localpycs/pyimod04_pywin32.pyc` | 1.7 KB | Dự án cũ | Tệp trung gian PyInstaller (mã Python đã biên dịch) của tracker cũ — có thể xóa |
| 28 | `DuAnAI/build/WinSystemHost/localpycs/pyimod04_pywin32.pyc` | 1.7 KB | Dự án cũ | Tệp trung gian PyInstaller (mã Python đã biên dịch) của tracker cũ — có thể xóa |
| 29 | `cloud/tao_khoa_vapid.py` | 1.7 KB | Công cụ | Tạo cặp khóa VAPID (thông báo đẩy): in 3 biến `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` để dán vào Vercel |
| 30 | `core/secrets_store.py` | 1.7 KB | Lõi | `get_secret(key)`: đọc biến môi trường `FOCUSDESK_*` (tên cũ `SMARTDESK_*`) rồi `secrets.local.json` |
| 31 | `tests/test_cuu_ho.py` | 1.9 KB | Kiểm thử | 3 kịch bản: phân tích phím cứu hộ, phím mặc định, PIN băm SHA-256 (đổi PIN thì 999999 hết hiệu lực) |
| 32 | `telegram_service.py` | 2.2 KB | Tương thích | Lớp cũ ở thư mục gốc cho module Telegram; chuyển sang `services/telegram_service.py` |
| 33 | `dist/shared_config.json` | 2.7 KB | Bản cũ | Cấu hình đi kèm bản `.exe` cũ |
| 34 | `kiem_tra_webcam.py` | 2.7 KB | Công cụ | Chẩn đoán webcam; `--no-view` chỉ in kết quả |
| 35 | `build_exe.bat` | 2.9 KB | Đóng gói | Đóng gói `dist\FocusDeskLauncher.exe` bằng PyInstaller (`--icon assets\focusdesk.ico`, danh sách hidden-import) |
| 36 | `services/app_scanner.py` | 2.9 KB | Dịch vụ | `scan_installed_apps()`: quét lối tắt menu Start → danh sách ứng dụng đã cài để phụ huynh tick chọn |
| 37 | `_icon_test.png` | 3.0 KB | File thử | Ảnh thử trích icon ứng dụng (tạo khi phát triển). Có thể xóa |
| 38 | `tests/fake_phone_box.py` | 3.0 KB | Kiểm thử | Hộp điện thoại giả lập đúng giao thức: `FakeBox` (`put_phone`, `take_phone`, `pair_mode`), `FakeTransport` |
| 39 | `services/app_icons.py` | 3.2 KB | Dịch vụ | `get_icon_image(path)`: lấy icon thật của `.exe` cho giao diện; `emoji_for()` dự phòng |
| 40 | `services/hosts_blocker.py` | 3.2 KB | Dịch vụ | Chặn tên miền ở mức Windows qua file hosts: `apply_hosts_block()`, `clear_hosts_block()`; dòng đánh dấu `# FOCUSDESK-BLOCK` (gỡ cả dòng cũ `# SMARTDESK-BLOCK`) |
| 41 | `CAI_DAT.bat` | 3.5 KB | Cài đặt | Cài trên máy khi không dùng bộ cài .exe: tìm/cài Python → `pip install` → `kiem_tra_cai_dat.py --sua` → lối tắt `FocusDesk.lnk` (icon mới) → tác vụ tự mở. `/tu_bo_cai` = chế độ im lặng khi bộ cài gọi |
| 42 | `posture_ai_engine.py` | 3.8 KB | Khởi chạy | Chạy riêng AI camera: cửa sổ xem thử, hoặc `--daemon` làm tiến trình phụ |
| 43 | `TAO_BO_CAI.bat` | 3.9 KB | Đóng gói | Tạo `dist_installer\FocusDesk_Setup.exe`: tìm Inno Setup → tùy chọn kèm Python → ký số nếu có `FOCUSDESK_SIGN_SHA1` → biên dịch `installer\FocusDesk.iss`. Viết bằng `goto`, không dùng khối `if (...)` |
| 44 | `shared_config.json` | 4.1 KB | Dữ liệu | Cấu hình Admin/Phụ huynh chỉnh: mật khẩu, `allowed_apps`, luật web, `youtube_mode`, camera, `phone_box_*`, `rescue_hotkey`, `rescue_pin_hash`, `student_close_background`, `background_allow`, `ai_apps` |
| 45 | `danh_gia_ai_tu_the.py` | 4.7 KB | Công cụ | Đo độ chính xác AI từ log đặc trưng có gán nhãn (`logs/posture_features_*.csv`) |
| 46 | `DuAnAI/build/WinSystemHost/localpycs/pyimod01_archive.pyc` | 4.9 KB | Dự án cũ | Tệp trung gian PyInstaller (mã Python đã biên dịch) của tracker cũ — có thể xóa |
| 47 | `DuAnAI/build/AppActivityTracker/localpycs/pyimod01_archive.pyc` | 4.9 KB | Dự án cũ | Tệp trung gian PyInstaller (mã Python đã biên dịch) của tracker cũ — có thể xóa |
| 48 | `tests/test_phone_box.py` | 5.1 KB | Kiểm thử | 8 kịch bản hộp điện thoại: kết nối, tự khóa khi học, chữ ký sai/phát lại, ghép, rút cáp, nhật ký TX/RX, firmware tên cũ, Serial monitor cổng thô |
| 49 | `runtime_state.json` | 5.4 KB | Dữ liệu | Trạng thái tức thời dùng chung giữa các luồng: `current_role`, `alerts`, `app_usage`, `ai_seconds`, `ai_sources`, `ai_running`, `posture_telemetry`, `toilet_break`, `phone_box`, `background_cleanup` |
| 50 | `services/live_view.py` | 5.8 KB | Dịch vụ | Xem camera trực tiếp không qua máy chủ: `start_live()` tạo mã 15 phút, `ensure_tunnel()` mở đường hầm cloudflared |
| 51 | `cloud/db.py` | 5.9 KB | Web phụ huynh | Lớp lưu trữ: `get_store()` → `_FirebaseStore` (Vercel) hoặc `_LocalStore` (file JSON khi thử cục bộ); `get/set/update/push/delete` |
| 52 | `services/camera_setup.py` | 5.9 KB | Dịch vụ | Chọn camera bằng tên + ảnh xem trước: `list_cameras()`, `grab_preview()`, `auto_assign()`. `_face_detector()` chịu lỗi OpenCV hỏng/OpenCV 5, dùng file Haar dự phòng trong `models/` |
| 53 | `README.md` | 6.5 KB | Tài liệu | Giới thiệu FocusDesk, đường dẫn tới hướng dẫn cài đặt, danh mục file, chuẩn hộp điện thoại |
| 54 | `DuAnAI/build/AppActivityTracker/localpycs/pyimod03_ctypes.pyc` | 6.5 KB | Dự án cũ | Tệp trung gian PyInstaller (mã Python đã biên dịch) của tracker cũ — có thể xóa |
| 55 | `DuAnAI/build/WinSystemHost/localpycs/pyimod03_ctypes.pyc` | 6.5 KB | Dự án cũ | Tệp trung gian PyInstaller (mã Python đã biên dịch) của tracker cũ — có thể xóa |
| 56 | `tests/test_attention_v3.py` | 6.5 KB | Kiểm thử | 16 kịch bản bộ máy chú ý: nhìn xuống viết bài không phải ngủ gật, nháy mắt, quay đầu, cầm điện thoại, nghỉ vệ sinh… |
| 57 | `services/desk_camera.py` | 6.6 KB | Dịch vụ | Camera mặt bàn tùy chọn để nhìn rõ tay: lớp `DeskCamera` |
| 58 | `cloud/HUONG_DAN_DEPLOY.md` | 6.7 KB | Tài liệu | Triển khai Vercel + Firebase + Telegram + đăng nhập Google |
| 59 | `installer/FocusDesk.iss` | 7.0 KB | Bộ cài | Kịch bản Inno Setup: `[Setup]` (AppId cố định, `C:\FocusDesk`, `SetupIconFile`, `#ifdef SIGN`), `[Files]`, `[Icons]` (lối tắt + *Sửa lỗi cài đặt*), `[Run]`, `[UninstallRun]`, `[Code]` hỏi địa chỉ web |
| 60 | `firmware/hop_dien_thoai_esp32s3/hop_dien_thoai_esp32s3.ino` | 7.5 KB | Phần cứng | Firmware mẫu ESP32-S3: khối CẤU HÌNH chân (khóa, nắp, cảm biến, nút, còi, LED); `handle()` xử lý lệnh; `hmac16()`; `readSensors()`; lưu trạng thái khóa bằng `Preferences` |
| 61 | `HUONG_DAN_CAI_DAT.md` | 7.7 KB | Tài liệu | Tạo bộ cài, xử lý SmartScreen, cài khóa VAPID và Gemini, bảng biến môi trường Vercel |
| 62 | `cloud/static/badge-96.png` | 8.2 KB | Icon web | Huy hiệu đơn sắc cho thông báo đẩy trên Android |
| 63 | `models/kinetics_600_labels.txt` | 9.0 KB | Mô hình AI | Tên 600 hành động của MoViNet (Kinetics-600) |
| 64 | `assets/focusdesk_badge.svg` | 9.1 KB | Icon | Bản vector đơn sắc của icon (dùng làm huy hiệu thông báo) |
| 65 | `assets/focusdesk.svg` | 9.1 KB | Icon | Bản vector gốc icon FocusDesk (đèn bàn chiếu sáng cuốn sách trên bàn học); mọi PNG/ICO tạo từ file này |
| 66 | `cloud/static/favicon.svg` | 9.1 KB | Icon web | Icon tab trình duyệt dạng vector |
| 67 | `assets/focusdesk_maskable.svg` | 9.2 KB | Icon | Bản vector nền tràn viền cho icon Android (vùng an toàn 80%) |
| 68 | `firmware/GIAO_THUC.md` | 9.8 KB | Phần cứng | **Chuẩn giao tiếp** cho người làm hộp: lớp vật lý, khung NDJSON, bảng tin nhắn, HMAC + bộ số kiểm tra, ghép hộp, Serial monitor, 9 bước nghiệm thu |
| 69 | `assets/focusdesk_64.png` | 10.2 KB | Icon | Logo 64 px hiện ở màn hình khóa |
| 70 | `DuAnAI/index1.html` | 10.7 KB | Dự án cũ | Trang web thử nghiệm của tracker cũ |
| 71 | `services/action_recognizer.py` | 10.8 KB | Dịch vụ | Nhận diện hành động MoViNet-A0 (nhắn tin, chơi rubik, ngáp…) làm bằng chứng phụ; bảng `GROUPS` gom 600 lớp thành study/phone/play/eat/drowsy/other |
| 72 | `services/camera_selector.py` | 10.8 KB | Dịch vụ | Mở webcam ổn định, chẩn đoán lý do lỗi (quyền camera, bị app khác giữ, antivirus): `open_best_camera()`, `build_diagnosis()` |
| 73 | `core/config_manager.py` | 10.8 KB | Lõi | Đọc/ghi cấu hình và trạng thái an toàn đa luồng: `DEFAULT_CONFIG`, `load_config/save_config`, `update_runtime_state`, `get_app_dir()`, `safe_log()` |
| 74 | `cloud/static/favicon.ico` | 11.6 KB | Icon web | Icon tab trình duyệt (16–64 px), phục vụ cả ở `/favicon.ico` |
| 75 | `services/telegram_service.py` | 11.8 KB | Dịch vụ | Bot Telegram chế độ trực tiếp (không qua cloud): `trigger_parent_alert()`, xử lý lệnh, poller |
| 76 | `DuAnAI/build/WinSystemHost/PKG-00.toc` | 12.3 KB | Dự án cũ | Tệp trung gian PyInstaller (danh mục gói) của tracker cũ |
| 77 | `services/process_cleanup.py` | 12.9 KB | Dịch vụ | Tắt app chạy nền khi vào học. `find_targets()` (xem trước), `close_targets()` (WM_CLOSE → buộc tắt), `on_student_start()`, quét lại 60 s. Danh sách giữ lại: `WINDOWS_CORE`, `INPUT_SYNC`, `HARDWARE_VENDOR`, `ANTIVIRUS`, `PROTECTED_PATH_HINTS` |
| 78 | `03_huong_dan_ai_mediapipe_hinh_hoc_antigravity.md` | 13.2 KB | Tài liệu gốc | Tài liệu kỹ thuật số 3 của nhóm: AI MediaPipe + hình học 3D |
| 79 | `DuAnAI/build/AppActivityTracker/PKG-00.toc` | 13.4 KB | Dự án cũ | Tệp trung gian PyInstaller của tracker cũ |
| 80 | `services/activity_tracker.py` | 13.7 KB | Dịch vụ | Ghi ứng dụng/tab đang mở, phát hiện đổi việc (app mới, tên miền mới): `ActivityTracker._scan_open`, `_check_web`, `drain_events()`; `domain_of()` chỉ giữ tên miền |
| 81 | `services/kiosk_guard.py` | 13.9 KB | Dịch vụ | Khóa máy khi học: ẩn taskbar, hook bàn phím `WH_KEYBOARD_LL` chặn phím Windows, `_watch_thread` kéo cửa sổ lạ xuống. Không chặn phím cứu hộ |
| 82 | `DuAnAI/build/WinSystemHost/EXE-00.toc` | 14.1 KB | Dự án cũ | Tệp trung gian PyInstaller của tracker cũ |
| 83 | `DuAnAI/tracker.py` | 14.6 KB | Dự án cũ | Tracker cũ: nguồn gốc của `activity_tracker.py` và `hosts_blocker.py` |
| 84 | `DANH_MUC_FILE.md` | 14.7 KB | Tài liệu | Danh mục file này (bản trong repo) |
| 85 | `DuAnAI/build/AppActivityTracker/EXE-00.toc` | 15.1 KB | Dự án cũ | Tệp trung gian PyInstaller của tracker cũ |
| 86 | `kiem_tra_cai_dat.py` | 16.5 KB | Công cụ | Kiểm tra máy; `--sua` tự sửa. `check_opencv()` chạy kiểm tra cv2 ở tiến trình riêng, phát hiện cài lẫn/hỏng → gỡ sạch + cài lại; các hàm `check_*` khác (Python, thư viện, mô hình, Admin, camera, cloud, cổng, hộp điện thoại) |
| 87 | `02_huong_dan_build_bot_telegram_va_web_config.md` | 17.0 KB | Tài liệu gốc | Tài liệu kỹ thuật số 2 của nhóm: bot Telegram + web cấu hình |
| 88 | `cloud/static/icon-192.png` | 18.2 KB | Icon web | Icon ứng dụng web 192 px (nền tràn viền, dùng cho Android) |
| 89 | `cloud/static/apple-touch-icon.png` | 19.1 KB | Icon web | Icon khi thêm web phụ huynh vào màn hình chính iPhone (180 px) |
| 90 | `tien_trinh_lam_viec.md` | 19.3 KB | Tài liệu gốc | Nhật ký giai đoạn 1–9 của nhóm |
| 91 | `core/failsafe.py` | 19.6 KB | Lõi | An toàn khẩn cấp: phím cứu hộ Admin đặt (`parse_hotkey`, `start_global_panic_hotkeys`, `restart_global_panic_hotkeys`), PIN băm (`set_rescue_pin`, `verify_rescue_pin`), `UNLOCK.txt`, bắt crash → `restore_windows_normal_state()` |
| 92 | `services/phone_box.py` | 20.7 KB | Dịch vụ | Phía máy tính của hộp điện thoại: `PhoneBoxLink` (dò cổng, ping 2 s, lệnh ký HMAC, `suspend/resume`), `RawSerialMonitor`, `TRAFFIC`/`log_traffic()`, `set_studying()` |
| 93 | `services/posture_controller.py` | 20.7 KB | Dịch vụ | Điều phối AI camera (cùng tiến trình hoặc tiến trình phụ `pythonw`): `PostureAIManager`, bộ đệm khung hình MJPEG |
| 94 | `DuAnAI/index.html` | 23.5 KB | Dự án cũ | Trang web của tracker cũ |
| 95 | `services/cloud_agent.py` | 23.9 KB | Dịch vụ | Cầu nối máy học sinh ↔ web phụ huynh (chỉ gọi ra HTTPS): `heartbeat()` 30 s (5 s khi phụ huynh xem), `execute()` lệnh từ xa (khóa máy, mở hộp theo phút…), gửi `ai_sources`/`ai_running` |
| 96 | `DuAnAI/build/WinSystemHost/warn-WinSystemHost.txt` | 25.2 KB | Dự án cũ | Cảnh báo build PyInstaller của tracker cũ |
| 97 | `services/giam_sat_worker.py` | 25.4 KB | Dịch vụ | Giám sát web/ứng dụng: phân loại học/chơi, YouTube theo từ khóa, **giờ AI gộp app AI + web AI** (`ai_app_label`, `ai_web_label`, `DEFAULT_AI_APPS`), `gui_canh_bao_len_web()` |
| 98 | `DuAnAI/build/AppActivityTracker/warn-AppActivityTracker.txt` | 25.5 KB | Dự án cũ | Cảnh báo build PyInstaller của tracker cũ |
| 99 | `services/attention_model.py` | 26.2 KB | Dịch vụ | Bộ máy trạng thái chú ý v3 (SCREEN, WRITING, READING, LOOK_AWAY, DROWSY, PLAYING, ABSENT, BREAK…): `AttentionModel.update(FrameFeatures)`, thùng bằng chứng `_Bucket` |
| 100 | `01_huong_dan_build_app_exe_3_cap_quyen.md` | 28.7 KB | Tài liệu gốc | Tài liệu kỹ thuật số 1 của nhóm: app khóa máy 3 cấp |
| 101 | `assets/focusdesk_256.png` | 29.7 KB | Icon | Icon 256 px cho cửa sổ app và hộp thoại (Tk `iconphoto`) |
| 102 | `DuAnAI/build/WinSystemHost/localpycs/pyimod02_importers.pyc` | 31.7 KB | Dự án cũ | Tệp trung gian PyInstaller (mã Python đã biên dịch) của tracker cũ — có thể xóa |
| 103 | `DuAnAI/build/AppActivityTracker/localpycs/pyimod02_importers.pyc` | 31.7 KB | Dự án cũ | Tệp trung gian PyInstaller (mã Python đã biên dịch) của tracker cũ — có thể xóa |
| 104 | `trang_web.py` | 43.8 KB | Ứng dụng | Web nội bộ cổng 5000: bảng điều khiển trong nhà, `/video_feed` MJPEG, `/live` có mã, trang ghép cloud; `POST /api/nhan_canh_bao` |
| 105 | `_anh_test_ai.jpg` | 44.2 KB | File thử | Ảnh chụp thử camera AI khi phát triển. Có thể xóa |
| 106 | `PROMPT_CHI_TIET_BUILD_EXE_TONG_HOP.md` | 48.0 KB | Tài liệu gốc | Đặc tả gốc để hợp nhất `giam_sat.pyw` và `trang_web.py` thành bản tổng hợp |
| 107 | `cloud/static/icon-512.png` | 59.2 KB | Icon web | Icon ứng dụng web 512 px (nền tràn viền, Android) |
| 108 | `assets/focusdesk.ico` | 61.1 KB | Icon | Icon Windows nhiều cỡ (16–256 px): cửa sổ app, lối tắt Desktop/Start, bộ cài, mục gỡ cài đặt |
| 109 | `cloud/static/icon-any-512.png` | 72.7 KB | Icon web | Icon 512 px bo góc (máy tính, cài web như ứng dụng) |
| 110 | `services/posture_ai_engine.py` | 85.9 KB | Dịch vụ | Lõi AI camera: `PostureAIEngine` (mắt, đầu, tay, khoảng cách ≥30 cm), `ObjectWatcher` (EfficientDet), `PostureAIServiceWorker._run_loop` (vòng chính, cảnh báo, nghỉ vệ sinh) |
| 111 | `cloud/app.py` | 103.0 KB | Web phụ huynh | Toàn bộ web phụ huynh + API máy + webhook Telegram + PWA: `/api/parent/...`, `/api/agent/heartbeat`, `COMMAND_TYPES` (có `box_unlock_min`), `classify_domain()`, thẻ hộp điện thoại và thẻ AI, `/api/health` |
| 112 | `he_thong_quan_ly_3_cap.py` | 133.6 KB | Ứng dụng | Phần mềm trung tâm: lớp `PhanMemQuanLyBanHocEXE`; màn khóa, chế độ học sinh (kiosk + tắt app nền), trang Admin (`_trang_he_thong`, `_trang_cuu_ho`, `_trang_hop_dien_thoai`, `_serial_monitor`), trang Phụ huynh; `_goi_ui` cho luồng nền |
| 113 | `DuAnAI/build/WinSystemHost/PYZ-00.toc` | 222.8 KB | Dự án cũ | Tệp trung gian PyInstaller (danh mục build) của tracker cũ — có thể xóa |
| 114 | `DuAnAI/build/AppActivityTracker/PYZ-00.toc` | 234.7 KB | Dự án cũ | Tệp trung gian PyInstaller (danh mục build) của tracker cũ — có thể xóa |
| 115 | `DuAnAI/build/WinSystemHost/Analysis-00.toc` | 254.3 KB | Dự án cũ | Tệp trung gian PyInstaller (danh mục build) của tracker cũ — có thể xóa |
| 116 | `DuAnAI/build/AppActivityTracker/Analysis-00.toc` | 267.2 KB | Dự án cũ | Tệp trung gian PyInstaller (danh mục build) của tracker cũ — có thể xóa |
| 117 | `models/haarcascade_frontalface_default.xml` | 908.3 KB | Mô hình AI | Bộ nhận mặt Haar dự phòng (khi OpenCV thiếu file này), dùng chấm ảnh xem trước camera |
| 118 | `DuAnAI/build/AppActivityTracker/base_library.zip` | 1.4 MB | Dự án cũ | Tệp trung gian PyInstaller (thư viện chuẩn nén) của tracker cũ — có thể xóa |
| 119 | `DuAnAI/build/WinSystemHost/base_library.zip` | 1.4 MB | Dự án cũ | Tệp trung gian PyInstaller (thư viện chuẩn nén) của tracker cũ — có thể xóa |
| 120 | `DuAnAI/build/WinSystemHost/xref-WinSystemHost.html` | 2.4 MB | Dự án cũ | Tệp trung gian PyInstaller (báo cáo phụ thuộc) của tracker cũ — có thể xóa |
| 121 | `DuAnAI/build/AppActivityTracker/xref-AppActivityTracker.html` | 2.6 MB | Dự án cũ | Tệp trung gian PyInstaller (báo cáo phụ thuộc) của tracker cũ — có thể xóa |
| 122 | `models/efficientdet_lite0.tflite` | 4.4 MB | Mô hình AI | Nhận diện đồ vật nhanh (điện thoại, đồ chơi… trong 80 loại COCO) |
| 123 | `models/efficientdet_lite2.tflite` | 7.2 MB | Mô hình AI | Nhận diện đồ vật chính xác hơn (chế độ quét kỹ) |
| 124 | `models/hand_landmarker.task` | 7.5 MB | Mô hình AI | Điểm mốc bàn tay (đang viết / cầm đồ vật) |
| 125 | `DuAnAI/build/WinSystemHost/PYZ-00.pyz` | 8.8 MB | Dự án cũ | Tệp trung gian PyInstaller (kho mã nén) của tracker cũ — có thể xóa |
| 126 | `DuAnAI/build/AppActivityTracker/PYZ-00.pyz` | 9.4 MB | Dự án cũ | Tệp trung gian PyInstaller (kho mã nén) của tracker cũ — có thể xóa |
| 127 | `models/holistic_landmarker.task` | 13.0 MB | Mô hình AI | MediaPipe Holistic: khuôn mặt + blendshape mắt, tư thế, tay — lõi của camera AI |
| 128 | `models/movinet_a0_stream_k600.tflite` | 13.1 MB | Mô hình AI | MoViNet-A0 nhận diện 600 hành động theo luồng video |
| 129 | `DuAnAI/build/WinSystemHost/WinSystemHost.pkg` | 23.3 MB | Dự án cũ | Tệp trung gian PyInstaller (gói dữ liệu) của tracker cũ — có thể xóa |
| 130 | `dist/SmartDeskLauncher.exe` | 24.5 MB | Bản cũ | Bản `.exe` cũ (trước khi đổi tên). Chỉ tham khảo; bộ cài một file đã thay thế |
| 131 | `DuAnAI/build/AppActivityTracker/AppActivityTracker.pkg` | 29.4 MB | Dự án cũ | Tệp trung gian PyInstaller (gói dữ liệu) của tracker cũ — có thể xóa |
| 132 | `DuAnAI/dist/AppActivityTracker.exe` | 29.8 MB | Dự án cũ | Bản `.exe` của tracker cũ |

## Ghi chú

- File **Bí mật** (`secrets.local.json`, `device_identity.json`, `DuAnAI/01_11/*`) không bao giờ đưa lên GitHub.
- Thư mục `DuAnAI/build/`, `DuAnAI/dist/`, `dist/SmartDeskLauncher.exe`, `_icon_test.png`, `_anh_test_ai.jpg` là
  bản cũ / file thử, có thể xóa để gọn thư mục (khoảng 134 MB).
- `logs/` (nhật ký chạy, log Serial monitor) được tạo khi chạy, không đưa lên GitHub.
