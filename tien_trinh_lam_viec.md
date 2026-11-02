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
| **Giai đoạn 5** | Giao diện Khóa máy 3 Cấp quyền kiểu Gcafe/CSM & Student Task Manager (`he_thong_quan_ly_3_cap.py`) | ⏳ Đang thực hiện |
| **Giai đoạn 6** | Đóng gói `dist/SmartDeskLauncher.exe` (`--uac-admin`), tạo `build_exe.bat` & Kiểm thử nghiệm thu khối `.exe` | ⏳ Chờ thực hiện |
| **Giai đoạn 7** | *(Thành phần sau cùng)* Tích hợp Dịch vụ Bot Telegram 2 chiều (`services/telegram_service.py` cảnh báo Cooldown + lệnh `/status`, `/lock`) | ⏳ Chờ thực hiện |

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
