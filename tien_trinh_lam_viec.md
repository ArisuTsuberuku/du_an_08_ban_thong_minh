# NHẬT KÝ TIẾN TRÌNH & TÀI LIỆU BÀN GIAO DỰ ÁN
**Dự án:** Hệ thống Bàn học Thông minh Hợp nhất Không gian mạng - Vật lý (Cyber-Physical Smart Study Desk)  
**Kho lưu trữ GitHub:** https://github.com/ArisuTsuberuku/du_an_08_ban_thong_minh  
**Môi trường phát triển:** Windows 64-bit | Python 3.14.6 | Tkinter 8.6 | PyInstaller 6.22.0  

---

## Tổng Quan Thứ Tự Các Giai Đoạn Triển Khai
| Giai đoạn | Hạng mục công việc | Trạng thái |
| :--- | :--- | :--- |
| **Giai đoạn 1** | Chuẩn bị môi trường, đồng bộ Git Repo & Giao ước dữ liệu (`core/config_manager.py`, `shared_config.json`, `runtime_state.json`) | ✅ Đã hoàn thành |
| **Giai đoạn 2** | 5 Lớp An toàn Khẩn cấp chống kẹt máy (`core/failsafe.py`, `KHOI_PHUC_KHAN_CAP.bat`, Global Hotkeys, Crash Hooks, PIN `999999`, `UNLOCK.txt`) | ⏳ Đang chuẩn bị |
| **Giai đoạn 3** | Web Server Dashboard Trung tâm (`trang_web.py` chạy `0.0.0.0:5000`, giữ nguyên `/api/nhan_canh_bao`, hỗ trợ PC & Android) | ⏳ Chờ thực hiện |
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
