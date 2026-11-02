# FocusDesk — Danh mục file trong thư mục dự án

Mỗi file: **dùng làm gì**, **ý nghĩa trong hệ thống**, và **cấu trúc quan trọng** (hàm/lớp/khối cần biết khi sửa).
Thứ tự: file khởi chạy → lõi → dịch vụ → web phụ huynh → phần cứng → cài đặt → kiểm thử → tài liệu → dữ liệu.

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

## 1. File khởi chạy (người dùng bấm vào)

| File | Dùng làm gì | Cấu trúc quan trọng |
| --- | --- | --- |
| `MO_FOCUSDESK.bat` | Mở FocusDesk hằng ngày; tự xin quyền Admin | `net session` kiểm tra Admin → `Start-Process -Verb RunAs`; tìm Python 3.14 → chạy `he_thong_quan_ly_3_cap.py` |
| `CAI_DAT.bat` | Cài lần đầu trên một máy (khi không dùng bộ cài .exe) | 5 bước: tìm/cài Python → `pip install -r requirements.txt` → `kiem_tra_cai_dat.py --sua` → lối tắt `FocusDesk.lnk` → tùy chọn tác vụ tự mở + cloudflared. Tham số `/tu_bo_cai` = chạy im lặng khi bộ cài gọi |
| `TAO_BO_CAI.bat` | Đóng gói `dist_installer\FocusDesk_Setup.exe` | Tìm `ISCC.exe` (Inno Setup) → tùy chọn tải Python vào `installer\redist` → ký số nếu có `FOCUSDESK_SIGN_SHA1` → biên dịch `installer\FocusDesk.iss`. Không dùng khối `if (...)` để tránh lỗi dấu ngoặc |
| `KHOI_PHUC_KHAN_CAP.bat` | Trả Windows về bình thường khi bị kẹt ở màn khóa | Tắt tiến trình FocusDesk (cả tên cũ SmartDesk), bật lại Task Manager, khởi động lại explorer |
| `chay_ai_tu_the.bat` | Mở cửa sổ xem thử AI camera bằng `pythonw` | Một lệnh `start pythonw posture_ai_engine.py` |
| `build_exe.bat` | Đóng gói bản `.exe` cũ bằng PyInstaller (`dist\FocusDeskLauncher.exe`) | Danh sách `--hidden-import`; `.exe` vẫn cần Python của máy để chạy AI. Bộ cài một file đã thay thế cách này |

## 2. Ứng dụng chính

| File | Dùng làm gì | Cấu trúc quan trọng |
| --- | --- | --- |
| `he_thong_quan_ly_3_cap.py` (~2.300 dòng) | Phần mềm trung tâm: màn khóa, đăng nhập 3 cấp (Admin / Phụ huynh / Học sinh), dock học sinh, các trang cài đặt | Lớp `PhanMemQuanLyBanHocEXE`. Vào/ra chế độ: `hien_man_hinh_dang_nhap`, `vao_che_do_hoc_sinh`. Trang Admin/Phụ huynh: các hàm `_trang_*` (ví dụ `_trang_hop_dien_thoai`, `_trang_he_thong`). `_serial_monitor` = cửa sổ gỡ lỗi hộp. Luồng nền gọi giao diện qua `_goi_ui` (Tkinter chỉ an toàn ở luồng chính). `main()` bật failsafe, cloud agent, hộp điện thoại |
| `trang_web.py` | Web nội bộ cổng 5000 (cùng Wi-Fi), xem camera trực tiếp, trang ghép cloud `/ket-noi-cloud` | Flask; API gốc `POST /api/nhan_canh_bao`; `/video_feed` MJPEG; `/live` (có mã); `start_web_server_in_background()` để app nhúng |
| `giam_sat.pyw` | Chạy riêng bộ giám sát web/app (tương thích bản cũ của nhóm) | Chỉ khởi chạy `BoGiamSatThongMinh` từ `services/giam_sat_worker.py` |
| `posture_ai_engine.py` | Chạy riêng AI camera: cửa sổ xem thử, hoặc `--daemon` làm tiến trình phụ | `run_interactive_preview()`, `run_daemon_mode()`; lõi nằm ở `services/posture_ai_engine.py` |
| `telegram_service.py` | Lớp tương thích cũ cho module Telegram ở thư mục gốc | `track_ai_chatbot_usage()`; gọi sang `services/telegram_service.py` |

## 3. Lõi (`core/`)

| File | Dùng làm gì | Cấu trúc quan trọng |
| --- | --- | --- |
| `core/config_manager.py` | Đọc/ghi cấu hình và trạng thái dùng chung, an toàn đa luồng | `DEFAULT_CONFIG` (mọi khóa mặc định), `load_config()/save_config()`, `load_runtime_state()/update_runtime_state()`, `get_app_dir()` (đọc `FOCUSDESK_APP_DIR`), `safe_log()` |
| `core/failsafe.py` | 5 lớp thoát khẩn cấp | Phím `Ctrl+Alt+Shift+F12`, PIN cứu hộ, file `UNLOCK.txt`, bắt crash → `restore_windows_normal_state()`; `set_windows_task_manager_lock()`; sinh `KHOI_PHUC_KHAN_CAP.bat` |
| `core/secrets_store.py` | Đọc bí mật riêng máy | `get_secret(key)`: biến môi trường `FOCUSDESK_*` (tên cũ `SMARTDESK_*` vẫn đọc) → `secrets.local.json` |

## 4. Dịch vụ chạy nền (`services/`)

| File | Dùng làm gì | Cấu trúc quan trọng |
| --- | --- | --- |
| `kiosk_guard.py` | Khóa máy khi học sinh học: ẩn taskbar, chặn phím Windows, chỉ cho ứng dụng được phép ở phía trước | `start_kiosk()/stop_kiosk()`; hook bàn phím `WH_KEYBOARD_LL` (`_keyboard_thread`); `_watch_thread` kéo cửa sổ lạ xuống |
| `giam_sat_worker.py` | Giám sát web/ứng dụng: đọc link trình duyệt, phân loại học/chơi, đếm giờ AI chatbot, YouTube theo từ khóa | Lớp `BoGiamSatThongMinh` (`_vong_lap_giam_sat`); `phan_loai_truy_cap_web()`, `danh_gia_youtube()`; `gui_canh_bao_len_web()` = điểm gửi mọi cảnh báo |
| `activity_tracker.py` | Ghi ứng dụng/tab đang mở, phát hiện "đổi việc" (app mới, tên miền mới) | Lớp `ActivityTracker`: `_scan_open`, `_check_web`, `drain_events()`; `domain_of(url)` chỉ giữ tên miền (riêng tư) |
| `hosts_blocker.py` | Chặn tên miền ở mức Windows qua file hosts | `apply_hosts_block(domains)`, `clear_hosts_block()`; mỗi dòng đánh dấu `# FOCUSDESK-BLOCK` (dòng cũ `# SMARTDESK-BLOCK` cũng được gỡ) |
| `app_scanner.py` | Quét ứng dụng đã cài từ menu Start để phụ huynh tick chọn | `scan_installed_apps()` → `[{name, exe_name, path}]` |
| `app_icons.py` | Lấy icon thật của `.exe` cho giao diện | `get_icon_image(path, size)`, `emoji_for(exe)` |
| `cloud_agent.py` | Cầu nối máy học sinh ↔ web phụ huynh (chỉ gọi ra HTTPS) | Lớp `CloudAgent`: `heartbeat()` 30 s/lần (5 s khi phụ huynh đang xem), `execute()` chạy lệnh từ xa (khóa máy, mở hộp…), `_apply_rules()` nhận luật chặn; `device_identity.json` giữ mã máy |
| `telegram_service.py` | Bot Telegram chế độ trực tiếp (không qua cloud) | `trigger_parent_alert()`, `handle_incoming_command()`, poller |
| `live_view.py` | Xem camera trực tiếp không qua máy chủ | `start_live()` tạo mã 15 phút; `ensure_tunnel()` mở đường hầm cloudflared |
| `posture_controller.py` | Điều phối AI camera (cùng tiến trình hoặc tiến trình phụ `pythonw`) | Lớp `PostureAIManager`; bộ đệm khung hình MJPEG cho web |
| `posture_ai_engine.py` (~1.700 dòng) | Lõi AI camera: MediaPipe Holistic, mắt/đầu/tay, đồ vật, khoảng cách | `PostureAIEngine` (đo đạc), `ObjectWatcher` (EfficientDet), `PostureAIServiceWorker._run_loop` (vòng chính, cảnh báo, nghỉ vệ sinh) |
| `attention_model.py` | Bộ máy trạng thái chú ý v3: SCREEN, WRITING, READING, LOOK_AWAY, DROWSY, PLAYING, ABSENT, BREAK… | Lớp `AttentionModel.update(FrameFeatures)`; `_Bucket` = thùng bằng chứng rò rỉ; ngưỡng ở `attention_thresholds` |
| `action_recognizer.py` | Nhận diện hành động MoViNet-A0 (nhắn tin, chơi rubik, ngáp…) làm bằng chứng phụ | Lớp `ActionRecognizer`; bảng `GROUPS` gom 600 lớp Kinetics thành study/phone/play/eat/drowsy/other |
| `camera_selector.py` | Mở webcam ổn định, chẩn đoán lý do lỗi (quyền, bị giữ, antivirus) | `open_best_camera()`, `build_diagnosis()` |
| `camera_setup.py` | Chọn camera bằng tên + ảnh xem trước, tự gán camera chính/phụ | `list_cameras()`, `grab_preview()`, `auto_assign()` |
| `desk_camera.py` | Camera mặt bàn tùy chọn để nhìn rõ tay | Lớp `DeskCamera` |
| `phone_box.py` | Phía máy tính của hộp khóa điện thoại | `PhoneBoxLink` (tự dò cổng, ping 2 s, lệnh ký HMAC, `suspend/resume`), `RawSerialMonitor` (cổng thô), `TRAFFIC` + `log_traffic()` (nhật ký TX/RX cho Serial monitor), `set_studying()`, `_default_event_handler` |

## 5. Web phụ huynh (`cloud/`, chạy trên Vercel)

| File | Dùng làm gì | Cấu trúc quan trọng |
| --- | --- | --- |
| `cloud/app.py` (~1.550 dòng) | Toàn bộ web phụ huynh + API cho máy học sinh + webhook Telegram + PWA | Đăng nhập Google (`/auth/google`); API phụ huynh `/api/parent/...` (devices, command, rules, block); API máy `/api/agent/heartbeat`; `COMMAND_TYPES`; `classify_domain()` (danh sách sẵn → tiêu đề → Gemini); `web_push_owners()`; `/api/health` báo cấu hình (`web_push`, `gemini`…) |
| `cloud/db.py` | Lớp lưu trữ: Firebase RTDB (thật) hoặc file JSON (thử cục bộ) | `get_store()` → `_FirebaseStore` / `_LocalStore` với `get/set/update/push/delete` |
| `cloud/tao_khoa_vapid.py` | Tạo cặp khóa VAPID cho thông báo đẩy | In 3 biến để dán vào Vercel |
| `cloud/database.rules.json` | Luật bảo mật Firebase | Chặn mọi đọc/ghi từ trình duyệt; chỉ máy chủ (service account) truy cập |
| `cloud/requirements.txt` | Thư viện của bản cloud | Flask, requests, google-auth, pywebpush… |
| `cloud/static/*.png` | Icon PWA | 192, 512, apple-touch |
| `cloud/HUONG_DAN_DEPLOY.md` | Hướng dẫn triển khai Vercel + Firebase + Telegram | |

## 6. Phần cứng

| File | Dùng làm gì | Cấu trúc quan trọng |
| --- | --- | --- |
| `firmware/GIAO_THUC.md` | **Chuẩn giao tiếp** cho người làm hộp | Khung NDJSON, bảng tin nhắn, chữ ký HMAC + bộ số kiểm tra, ghép hộp, Serial monitor, danh sách nghiệm thu |
| `firmware/hop_dien_thoai_esp32s3/*.ino` | Firmware mẫu cho ESP32-S3 | Khối CẤU HÌNH (chân, servo/solenoid); `handle()` xử lý lệnh; `readSensors()`, `readButton()`; `hmac16()`; trạng thái khóa lưu bằng `Preferences` |

## 7. Bộ cài (`installer/`)

| File | Dùng làm gì | Cấu trúc quan trọng |
| --- | --- | --- |
| `installer/FocusDesk.iss` | Kịch bản Inno Setup tạo bộ cài một file | `[Setup]` (AppId cố định, cài vào `C:\FocusDesk`, `#ifdef SIGN` ký số); `[Files]`; `[Run]` (Python, `CAI_DAT.bat /tu_bo_cai`, tác vụ tự mở); `[UninstallRun]` gọi `go_cai_dat.bat`; `[Code]` trang hỏi địa chỉ web phụ huynh |
| `installer/go_cai_dat.bat` | Dọn dẹp khi gỡ cài đặt | Tắt tiến trình, xóa tác vụ tự mở (tên mới và cũ), bật lại Task Manager, gỡ dòng chặn trong hosts, khởi động lại explorer |
| `installer/KIEM_TRA.bat` | Lối tắt "Kiểm tra cài đặt" sau khi cài | Gọi `kiem_tra_cai_dat.py` |
| `FocusDeskLauncher.spec` | Cấu hình PyInstaller cho bản `.exe` cũ | |

## 8. Kiểm tra và kiểm thử

| File | Dùng làm gì | Cấu trúc quan trọng |
| --- | --- | --- |
| `kiem_tra_cai_dat.py` | Kiểm tra máy đã cài đủ chưa; `--sua` tự sửa | Các hàm `check_*` (Python, thư viện, mô hình, Admin, camera, cloud, cổng, cloudflared, hộp điện thoại, cấu hình) in ✅/⚠️/❌ |
| `kiem_tra_webcam.py` | Chẩn đoán webcam | `--no-view` chỉ in kết quả |
| `danh_gia_ai_tu_the.py` | Đo độ chính xác AI từ log có gán nhãn (`logs/posture_features_*.csv`) | |
| `tests/test_attention_v3.py` | 16 kịch bản cho bộ máy chú ý | `python tests/test_attention_v3.py` |
| `tests/test_phone_box.py` | 8 kịch bản giao thức hộp (kết nối, tự khóa, chữ ký, ghép, mất kết nối, nhật ký, firmware cũ, Serial monitor) | `python tests/test_phone_box.py` |
| `tests/fake_phone_box.py` | Hộp giả lập đúng giao thức | `FakeBox` (`put_phone`, `take_phone`, `pair_mode`), `FakeTransport` |

## 9. Tài liệu

| File | Nội dung |
| --- | --- |
| `README.md` | Giới thiệu tổng quan, cấu trúc thư mục ban đầu |
| `HUONG_DAN_CAI_DAT.md` | Tạo bộ cài, SmartScreen, khóa VAPID, khóa Gemini, biến môi trường Vercel |
| `DANH_MUC_FILE.md` | File này |
| `tien_trinh_lam_viec.md` | Nhật ký các giai đoạn phát triển ban đầu |
| `01_…`, `02_…`, `03_…` `.md` | Tài liệu kỹ thuật gốc của nhóm (app 3 cấp, bot Telegram, AI MediaPipe) |
| `PROMPT_CHI_TIET_BUILD_EXE_TONG_HOP.md` | Đặc tả gốc dùng để hợp nhất `giam_sat.pyw` và `trang_web.py` |

## 10. Dữ liệu và cấu hình (thay đổi khi chạy)

| File | Ý nghĩa | Khóa quan trọng |
| --- | --- | --- |
| `shared_config.json` | Cấu hình do Admin/Phụ huynh chỉnh | `passwords`, `allowed_apps`, `bad_urls`, `allowed_urls`, `youtube_mode`, `max_ai_minutes`, `kiosk_strict`, `camera_*`, `ai_scan_mode`, `phone_box_enabled`, `phone_box_key`, `esp32_com_port`, `esp32_baud` |
| `runtime_state.json` | Trạng thái tức thời để các luồng/tiến trình đọc chung | `current_role`, `alerts`, `app_usage`, `ai_seconds`, `posture_telemetry`, `toilet_break`, `phone_box` |
| `secrets.local.json` | Bí mật riêng máy, **không lên GitHub** | `cloud_url`; mẫu ở `secrets.local.example.json` |
| `device_identity.json` | Mã máy + token để nói chuyện với web phụ huynh, **không lên GitHub** | `device_id`, `device_token` |
| `requirements.txt` | Thư viện Python của máy học sinh | mediapipe, opencv, ai-edge-litert, uiautomation, pyserial… |
| `models/` | Mô hình AI (khoảng 46 MB) | `holistic_landmarker.task`, `hand_landmarker.task`, `efficientdet_lite0/2.tflite`, `movinet_a0_stream_k600.tflite`, `kinetics_600_labels.txt` |
| `logs/` | Nhật ký chạy, log đặc trưng AI, log Serial monitor | Không lên GitHub |
| `dist/` | Bản `.exe` cũ và file đi kèm | Bản cũ, chỉ để tham khảo |
| `dist_installer/` | Bộ cài một file sau khi chạy `TAO_BO_CAI.bat` | Không lên GitHub |
| `DuAnAI/` | Dự án tracker cũ (nguồn gốc của `activity_tracker` và `hosts_blocker`) | Không còn dùng trực tiếp |
