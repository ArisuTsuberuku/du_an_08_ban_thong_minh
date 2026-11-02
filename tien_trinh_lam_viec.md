# NHẬT KÝ TIẾN TRÌNH & TÀI LIỆU BÀN GIAO DỰ ÁN
**Dự án:** Hệ thống Bàn học Thông minh Hợp nhất Không gian mạng - Vật lý (Cyber-Physical Smart Study Desk)  
**Kho lưu trữ GitHub:** https://github.com/ArisuTsuberuku/du_an_08_ban_thong_minh  
**Môi trường phát triển:** Windows 64-bit | Python 3.14.6 | Tkinter 8.6 | PyInstaller 6.22.0  

---

## Tổng Quan Thứ Tự Các Giai Đoạn Triển Khai
| Giai đoạn | Hạng mục công việc | Trạng thái |
| :--- | :--- | :--- |
| **Giai đoạn 1** | Chuẩn bị môi trường, đồng bộ Git Repo & Giao ước dữ liệu (`core/config_manager.py`, `shared_config.json`, `runtime_state.json`) | ✅ Đã hoàn thành |
| **Giai đoạn 2** | 5 Lớp An toàn Khẩn cấp chống kẹt máy (`core/failsafe.py`, `KHOI_PHUC_KHAN_CAP.bat`, Global Hotkeys, Crash Hooks, PIN `999999`, `UNLOCK.txt`) | ✅ Đã hoàn thành |
| **Giai đoạn 3** | Web Server Dashboard Trung tâm (`trang_web.py` chạy `0.0.0.0:5000`, giữ nguyên `/api/nhan_canh_bao`, hỗ trợ PC & Android) | ✅ Đã hoàn thành |
| **Giai đoạn 4** | Bộ Giám sát Ngầm nâng cấp (`services/giam_sat_worker.py` & `giam_sat.pyw` đọc URL bằng `uiautomation`, đếm giờ AI, diệt Game) | ✅ Đã hoàn thành |
| **Giai đoạn 5** | Giao diện Khóa máy 3 Cấp quyền kiểu Gcafe/CSM & Student Task Manager (`he_thong_quan_ly_3_cap.py`) | ✅ Đã hoàn thành |
| **Giai đoạn 6** | Đóng gói `dist/SmartDeskLauncher.exe` (`--uac-admin`), tạo `build_exe.bat` & Kiểm thử nghiệm thu khối `.exe` | ✅ Đã hoàn thành |
| **Giai đoạn 7** | Tích hợp Dịch vụ Bot Telegram 2 chiều (`services/telegram_service.py` cảnh báo Cooldown + lệnh `/status`, `/lock`) | ✅ Đã hoàn thành |
| **Giai đoạn 8** | Lõi AI Nhận diện Tư thế 3D tại Biên (`services/posture_ai_engine.py`, `posture_ai_engine.py`, MediaPipe Holistic + `EAR`/`PERCLOS`/`MAR`/`solvePnP`) | ✅ Đã hoàn thành |
| **Giai đoạn 9** | Hợp nhất luồng MJPEG `/video_feed` & điều khiển AI Tư thế vào `trang_web.py`, `he_thong_quan_ly_3_cap.py` & đóng gói lại `.EXE` | 🔄 Đang triển khai |

---

## Chi Tiết Bàn Giao Từng Giai Đoạn

### ✅ Giai đoạn 1: Môi trường, Git Repo & Giao ước Dữ liệu Dùng chung
- **Thời gian hoàn thành:** 02/11/2026
- **Các công việc đã thực hiện:**
  1. Khởi tạo Git tại thư mục gốc dự án, kết nối nhánh `main` tới `https://github.com/ArisuTsuberuku/du_an_08_ban_thong_minh.git`.
  2. Cài đặt đầy đủ các thư viện phụ thuộc trên Python 3.14.6: `flask 3.1.3`, `psutil 7.2.2`, `uiautomation 2.0.29`, `pyautogui 0.9.54`, `pygetwindow 0.0.9`, `comtypes 1.4.17`.
  3. Tạo `.gitignore` và `requirements.txt` chuẩn hóa môi trường cho các thành viên khác trong nhóm.
  4. Xây dựng module `core/config_manager.py`:
     - Tự động phân giải đường dẫn gốc qua `sys.executable` khi đóng gói PyInstaller `.exe` (`sys.frozen`) để không ghi lệch file khi chạy quyền Admin hoặc Task Scheduler.
     - Đọc/ghi nguyên tử đa luồng (`threading.RLock` + cơ chế retry chống khóa file tạm thời của OneDrive trên Windows).
     - Khởi tạo thành công 2 tệp giao ước dữ liệu `shared_config.json` (bảo toàn 100% `bad_tabs`, `bad_apps`, bổ sung `bad_urls`, `allowed_urls`, `academic_keywords`, `allowed_apps`, `allowed_folders`, `max_ai_minutes`, `telegram`) và `runtime_state.json`.
- **Kiểm thử:** Đã chạy kiểm chứng khởi tạo và đọc/ghi cả 2 tệp JSON thành công.

---

### ✅ Giai đoạn 2: 5 Lớp An toàn Khẩn cấp Chống Kẹt Máy (`core/failsafe.py`)
- **Thời gian hoàn thành:** 02/11/2026
- **Các công việc đã thực hiện:**
  1. Xây dựng module `core/failsafe.py` tích hợp trọn vẹn 5 lớp bảo vệ khẩn cấp:
     - **Lớp 1 (Win32 Global Panic Hotkeys):** Đăng ký trực tiếp `user32.RegisterHotKey` với nhân Windows cho 2 tổ hợp `Ctrl + Alt + Shift + F12` và `Ctrl + Shift + F10` (bắt được phím ngay cả khi học sinh đang mở Chrome/Game toàn màn hình hoặc giao diện Tkinter bị treo, kèm bộ hẹn giờ bảo hiểm 0.8 giây tự cưỡng bức mở khóa).
     - **Lớp 2 (Crash & Exception Hooks):** Bọc `atexit.register`, `sys.excepthook`, `signal` (`SIGINT`, `SIGTERM`, `SIGBREAK`) và `Tk.report_callback_exception`, tự động ghi `crash_recovery.log`, trả `DisableTaskMgr = 0` và khôi phục `explorer.exe`.
     - **Lớp 3 (Mã PIN Cứu hộ `999999`):** Hàm `verify_rescue_pin("999999")` sẵn sàng gắn lên thanh cứu hộ cố định ở đáy mọi màn hình.
     - **Lớp 4 (Công tắc File `UNLOCK.txt`):** Luồng nền độc lập quét `UNLOCK.txt` mỗi 1 giây ở mọi màn hình, tự xóa cờ sau khi kích hoạt để không bị thoát lặp ở lần mở sau.
     - **Lớp 5 (Tự động sinh `KHOI_PHUC_KHAN_CAP.bat`):** Tự sinh ngay khi khởi động phần mềm, cho phép khôi phục Registry + `explorer.exe` chỉ bằng 1 cú nhấp đúp bên ngoài.
  2. Khắc phục triệt để lỗi bảng mã Windows `cp1252` (`UnicodeEncodeError` khi in tiếng Việt) và lỗi `sys.stdout = None` khi đóng gói PyInstaller `--noconsole` thông qua hàm `configure_utf8_stdio()` và `safe_log()`.
- **Kiểm thử:** Đã chạy script kiểm thử tự động xác nhận cả 5 lớp (`KHOI_PHUC_KHAN_CAP.bat`, Registry handler khi có/không có quyền Admin, mã PIN `999999`, thu hồi cờ `UNLOCK.txt`, đăng ký/hủy đăng ký Win32 `RegisterHotKey`) đều vượt qua 100%.

---

### ✅ Giai đoạn 3: Web Server Dashboard Trung tâm (`trang_web.py`)
- **Thời gian hoàn thành:** 02/11/2026
- **Các công việc đã thực hiện:**
  1. Xây dựng `trang_web.py` chạy trên `0.0.0.0:5000`, tự động nhận diện địa chỉ IP LAN (ví dụ `http://10.10.1.169:5000`) để Phụ huynh mở Bảng điều khiển trực tiếp từ trình duyệt trên điện thoại Android cùng mạng Wi-Fi.
  2. Bảo toàn 100% giao thức gốc của nhóm: cổng `POST /api/nhan_canh_bao` nhận `{'message': noi_dung}` và giữ nguyên cụm từ `"Bảng Điều Khiển Bàn Học"` trong `<title>` để bộ quét `giam_sat` miễn trừ không đóng nhầm.
  3. Cung cấp các API mở rộng đồng bộ với `shared_config.json` & `runtime_state.json`:
     - `POST /api/cap_nhat_thong_ke`, `GET /api/du_lieu_dashboard`
     - `POST /api/luu_cau_hinh_web` (chỉnh sửa trực tiếp `max_ai_minutes`, `bad_urls`, `allowed_urls`, `bad_tabs`, `academic_keywords`, `bad_apps`)
     - `POST /api/khoa_may_tu_xa` (nút khóa màn hình máy tính học sinh ngay trên trình duyệt điện thoại)
     - `POST /api/posture_telemetry` (cổng chờ cho AI Camera MediaPipe 3D)
     - Cổng Hook `_dispatch_telegram_hook()` chờ sẵn để tự động kích hoạt khi lắp Dịch vụ Bot Telegram ở **Giai đoạn 7**.
  4. Xây dựng hàm `start_web_server_in_background()` bằng `werkzeug.serving.make_server(..., threaded=True)` để nhúng êm ái vào trong `SmartDeskLauncher.exe` mà không xung đột nếu cổng `5000` đã mở sẵn bên ngoài.
- **Kiểm thử:** Đã chạy kiểm thử thực tế khởi chạy luồng nền cổng `5000`, gọi `POST /api/nhan_canh_bao`, `GET /api/du_lieu_dashboard`, `POST /api/xoa_lich_su_canh_bao` và tắt server an toàn.

---

### ✅ Giai đoạn 4: Bộ Giám sát Ngầm Nâng cấp (`services/giam_sat_worker.py` & `giam_sat.pyw`)
- **Thời gian hoàn thành:** 02/11/2026
- **Các công việc đã thực hiện:**
  1. Bảo toàn 100% biến, hàm và cơ chế cốt lõi của nhóm: `BAD_TABS`, `BAD_APPS`, `pyautogui.FAILSAFE = False`, `pyautogui.hotkey('ctrl', 'w')`, `URL_WEB = "http://127.0.0.1:5000/api/nhan_canh_bao"`, hàm `gui_canh_bao_len_web(noi_dung)` và tệp chạy độc lập `giam_sat.pyw`.
  2. Nâng cấp đọc trực tiếp **Link Address (URL)** trên thanh địa chỉ trình duyệt (`Chrome`, `Edge`, `Brave`, `Cốc Cốc`, `Firefox`) bằng `uiautomation` với khởi tạo COM (`CoInitialize`) chuẩn trong luồng nền và timeout `150ms` không gây trễ máy.
  3. Xây dựng hàm phân loại `phan_loai_truy_cap_web()`:
     - Miễn trừ tuyệt đối `Bảng Điều Khiển Bàn Học` (`127.0.0.1:5000`).
     - Cho phép học bình thường trên `ALLOWED_URLS` (`olm.vn`, `vietjack.com`...) và **cho phép xem bài giảng YouTube hợp lệ** có chứa từ khóa học thuật (`ACADEMIC_KW`), đồng thời chặn cứng `youtube.com/shorts`, `facebook.com`, `poki.com` và các video YouTube giải trí/game.
  4. Tích hợp bộ đếm thời gian tra cứu ChatGPT / Gemini / Claude / Copilot (`ai_seconds`), cảnh báo khi vượt `max_ai_minutes`.
  5. Quét `psutil` mỗi giây để tiêu diệt tiến trình game ngầm (`BAD_APPS`) và cộng dồn chính xác thời gian mở từng ứng dụng học tập (khử trùng lặp đa tiến trình con của Chrome/Edge).
- **Kiểm thử:** Đã vượt qua toàn bộ 7 kịch bản kiểm thử đơn vị (miễn trừ Dashboard, `olm.vn`, YouTube bài giảng Vật lý, YouTube giải trí, YouTube Shorts, Poki Games, ChatGPT quota) và chạy thử vòng lặp luồng nền thực tế.

---

### ✅ Giai đoạn 5: Giao diện Khóa máy 3 Cấp quyền Kiểu Gcafe/CSM & Student Task Manager (`he_thong_quan_ly_3_cap.py`)
- **Thời gian hoàn thành:** 02/11/2026
- **Các công việc đã thực hiện:**
  1. Xây dựng ứng dụng trung tâm `he_thong_quan_ly_3_cap.py` kết nối toàn bộ các mô-đun (`core/config_manager`, `core/failsafe`, `services/giam_sat_worker`, `trang_web`).
  2. **Màn hình Khóa Toàn màn hình (Gcafe/CSM):**
     - Fullscreen Topmost, chặn `Alt+F4`, gắn thanh cứu hộ cố định ở đáy màn hình kèm nút `🆘 Thoát Khẩn Cấp (PIN: 999999)`.
     - 3 nút **⚡ Chọn nhanh Tài khoản Demo** (`hocsinh/hocsinh123`, `phuhuynh/phuhuynh123`, `admin/admin123`), tự phát hiện và cảnh báo khi đăng nhập sai quyền Phụ huynh/Admin ≥ 3 lần (`WRONG_LOGIN`).
  3. **Chế độ Học sinh & Student Task Manager:**
     - Lưới mở ứng dụng (`allowed_apps`) và thư mục (`allowed_folders`) được phép, đồng hồ Quota AI thời gian thực, tự động kích hoạt `BoGiamSatThongMinh` và lắng nghe lệnh Khóa từ xa.
     - **Student Task Manager:** Chỉ hiển thị tiến trình trong `allowed_apps` (kèm dung lượng RAM), ẩn và khóa cứng tuyệt đối `explorer.exe`, `python.exe`, `pythonw.exe`, `SmartDeskLauncher.exe` và các tiến trình hệ thống.
  4. **Chế độ Phụ huynh & Admin:**
     - Mở lại `explorer.exe` và Task Manager gốc, cung cấp Bảng điều khiển 3 Tab cho Phụ huynh (Quản lý `.exe` & Thư mục, Chính sách Web/AI/Game, Báo cáo thống kê & Lịch sử vi phạm) và Bảng Quản trị Hệ thống cho Admin (Kiosk `explorer.exe`, cổng Web/COM ESP32-S3, đổi mật khẩu 3 cấp, Thoát hoàn toàn phần mềm).
     - Tự động khởi chạy ngầm `trang_web.py` cổng `5000` suốt vòng đời ứng dụng.
- **Kiểm thử:** Chạy `python he_thong_quan_ly_3_cap.py --self-test` kiểm chứng toàn bộ 4 màn hình UI + Student Task Manager + Embedded Web Server thành công 100%.

---

### ✅ Giai đoạn 6: Đóng gói `dist/SmartDeskLauncher.exe` (`--uac-admin`) & Kiểm thử Nghiệm thu
- **Thời gian hoàn thành:** 02/11/2026
- **Các công việc đã thực hiện:**
  1. Xây dựng script đóng gói tự động `build_exe.bat` với PyInstaller 6.22.0:
     - Tham số chuẩn: `--onefile --noconsole --uac-admin --name SmartDeskLauncher`
     - Đóng gói kèm thư viện DLL gốc `--collect-data uiautomation` để đọc thanh địa chỉ trình duyệt trực tiếp bên trong file `.exe`.
     - Loại trừ các thư viện khoa học dữ liệu không cần thiết (`numpy`, `scipy`, `pandas`, `onnxruntime`...) giúp dung lượng `dist/SmartDeskLauncher.exe` tối ưu chỉ **~24.5 MB**, khởi động nhanh.
     - Tự động sao chép `shared_config.json` và `KHOI_PHUC_KHAN_CAP.bat` vào thư mục `dist/`.
  2. Chạy kiểm thử thực tế trực tiếp trên file thực thi đã biên dịch `dist\SmartDeskLauncher.exe --self-test`:
     - Mã thoát: `ExitCode = 0`
     - `self_test_passed = true`, `uia_available = true`, Embedded Flask Server cổng `5000` phản hồi `200 OK`, tự động định vị đúng thư mục `dist/` thông qua `sys.executable`.

---

### ✅ Giai đoạn 7 (Thành phần sau cùng): Dịch vụ Bot Telegram 2 Chiều & Đóng gói Hoàn chỉnh (`services/telegram_service.py`)
- **Thời gian hoàn thành:** 02/11/2026
- **Các công việc đã thực hiện:**
  1. Xây dựng module `services/telegram_service.py` và wrapper tương thích gốc `telegram_service.py`:
     - Tuân thủ nguyên tắc **Local-first, Zero-cloud**: chỉ gửi siêu dữ liệu cảnh báo văn bản định dạng HTML an toàn (`html.escape`, không lỗi ký tự `_` của tên tiến trình game hay URL), không gửi hình ảnh.
     - Hỗ trợ đủ 4 kịch bản cảnh báo tự động (`GAME_DETECTED`, `WRONG_LOGIN`, `AI_OVERUSE`, `POSTURE_OR_HW`) + `SYSTEM_TEST` với bộ lọc chống spam `cooldown_seconds`.
     - Xây dựng `start_telegram_command_poller()` nhận lệnh 2 chiều xác thực theo đúng `chat_id` Phụ huynh: `/help`, `/status` (báo cáo điểm `CFI`, thời gian dùng App & AI, vi phạm gần nhất), `/lock` (khóa màn hình máy tính học sinh từ xa trong ~1 giây), `/reset_ai` (cấp lại thời gian AI).
  2. Tích hợp Tab **4. Cấu hình Bot Telegram 2 Chiều** trực tiếp trên Bảng điều khiển Phụ huynh trong App `.exe` và kích hoạt đầy đủ khu vực cấu hình + nút Test Telegram trên `trang_web.py` (kèm các route tương thích `/bot-settings`, `/api/telegram-config`, `/api/telegram-test`).
  3. Tối ưu `build_exe.bat` sử dụng `--workpath "%TEMP%\SmartDeskLauncher_pyi_build"` để triệt tiêu xung đột khóa file tạm của OneDrive khi đóng gói lại `dist/SmartDeskLauncher.exe`.
- **Hướng dẫn bàn giao tiếp theo:**
  - Mọi cổng dữ liệu (`runtime_state.json` trường `posture_telemetry` và API `POST http://127.0.0.1:5000/api/posture_telemetry`) cùng cảnh báo `POSTURE_OR_HW` qua Bot Telegram đã được mở sẵn để kết nối sang Giai đoạn 8 & 9.

---

### ✅ Giai đoạn 8: Lõi AI Nhận diện Tư thế Ngồi học 3D tại Biên (`services/posture_ai_engine.py` & `posture_ai_engine.py`)
- **Thời gian hoàn thành:** 02/11/2026
- **Các công việc đã thực hiện:**
  1. Cài đặt `opencv-python`, `opencv-contrib-python`, `mediapipe 1.2.0` trên Python 3.14.6 và lưu trữ cục bộ mô hình ngoại tuyến `models/holistic_landmarker.task` (13.05 MB), nạp qua `model_asset_buffer` để tương thích tuyệt đối với đường dẫn tiếng Việt có dấu trên Windows.
  2. Xây dựng bộ máy `PostureAIEngine` (`services/posture_ai_engine.py`) tương thích kép giữa MediaPipe Tasks API (`>=1.0`) và Legacy Solutions API (`0.10.x`), kèm ghép nối tùy chọn `models/keras_model.h5` (70% Hình học 3D + 30% Teachable Machine):
     - **Tỷ lệ mở mắt 6 điểm (`EAR_t`) & `PERCLOS` cửa sổ 100 khung hình** + Chống giả mạo ảnh tĩnh **PAD** (cảnh báo nếu không chớp mắt > 25 giây).
     - **Tỷ lệ mở miệng 8 điểm (`MAR_t > 0.60` duy trì >= 1.5s)** & Bộ đếm tần suất ngáp (`yawn_count`).
     - **Giải bài toán `cv2.solvePnP` 3D** chiếu 6 điểm mốc nhân trắc học suy ra đủ 3 góc Euler (`Pitch`, `Yaw`, `Roll`) và phát hiện **sự kiện ngủ gật `E_nod`** (`ΔPitch > 15°` trong `1.5s`).
     - **Hiệu chỉnh Gốc Tư thế Cá nhân hóa tự động trong 5 giây đầu** (Baseline Auto-Calibration) và hỗ trợ đặt lại gốc từ xa.
     - **Phát hiện 5 lỗi công thái học + Theo dõi 2 bàn tay trên mặt bàn (>15s)** + **Chặn Camera ảo** (`obs64.exe`, `manycam.exe`, `vcam.exe`...).
  3. Xây dựng `PostureAIServiceWorker` (~12 FPS, tự giãn cách dò phần cứng 5s khi máy chưa cắm Webcam, bắn cảnh báo `POSTURE_OR_HW` về Web & Bot Telegram khi `CFI < 50%` liên tục > 15s) và tệp chạy độc lập `posture_ai_engine.py` (hỗ trợ cả chế độ cửa sổ trực quan và `--daemon --parent-pid`).
- **Kiểm thử:** Chạy kiểm thử đơn vị toàn bộ công thức Toán học (`EAR=0.300`, `MAR=0.833`, `Euler solvePnP`) và luồng xử lý ảnh thành công 100%.
