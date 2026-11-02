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
| **Giai đoạn 3** | Web Server Dashboard Trung tâm (`trang_web.py` chạy `0.0.0.0:5000`, giữ nguyên `/api/nhan_canh_bao`, hỗ trợ PC & Android) | ⏳ Đang thực hiện |
| **Giai đoạn 4** | Bộ Giám sát Ngầm nâng cấp (`services/giam_sat_worker.py` & `giam_sat.pyw` đọc URL bằng `uiautomation`, đếm giờ AI, diệt Game) | ⏳ Chờ thực hiện |
| **Giai đoạn 5** | Giao diện Khóa máy 3 Cấp quyền kiểu Gcafe/CSM & Student Task Manager (`he_thong_quan_ly_3_cap.py`) | ⏳ Chờ thực hiện |
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
