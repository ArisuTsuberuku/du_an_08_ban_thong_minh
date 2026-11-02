# Hệ Thống Bàn Học Thông Minh Hợp Nhất Không Gian Mạng - Vật Lý (Cyber-Physical Smart Study Desk)

Phần mềm trung tâm quản lý kỷ luật số 3 cấp quyền kiểu Gcafe/CSM trên Windows kết hợp kiểm soát trình duyệt theo Link Address (URL), đếm thời gian Chatbot AI, Bảng điều khiển Web LAN (`0.0.0.0:5000`) cho PC & Điện thoại Android và Trợ lý cảnh báo hai chiều qua Bot Telegram (Zero-Cloud).

---

## 1. Cấu trúc Thư mục Dự án

```text
Du_an_D08_ban_thong_minh/
├── dist/
│   ├── SmartDeskLauncher.exe       # Bản đóng gói hoàn chỉnh chạy ngay (Quyền Administrator)
│   ├── shared_config.json          # Cấu hình dùng chung đi kèm file .exe
│   └── KHOI_PHUC_KHAN_CAP.bat      # Công cụ khôi phục khẩn cấp 1-click
├── core/
│   ├── config_manager.py           # Quản lý đọc/ghi nguyên tử shared_config.json & runtime_state.json
│   └── failsafe.py                 # 5 Lớp An toàn Khẩn cấp chống kẹt máy Windows
├── services/
│   ├── giam_sat_worker.py          # Quét URL (uiautomation) + Tiêu đề Tab + Đếm giờ AI + Diệt Game
│   └── telegram_service.py         # Bắn cảnh báo Cooldown & Nhận lệnh 2 chiều (/status, /lock, /reset_ai)
├── he_thong_quan_ly_3_cap.py       # Ứng dụng Desktop trung tâm 3 cấp quyền + Student Task Manager
├── trang_web.py                    # Flask Web Dashboard (0.0.0.0:5000) cho Máy tính & Điện thoại cùng Wi-Fi
├── giam_sat.pyw                    # Bản chạy ngầm độc lập (tương thích ngược báo cáo nhóm)
├── telegram_service.py             # Wrapper tương thích cho các module mở rộng gọi trực tiếp
├── build_exe.bat                   # Script tự động đóng gói ra dist\SmartDeskLauncher.exe
├── requirements.txt                # Danh sách thư viện Python
└── tien_trinh_lam_viec.md          # Nhật ký bàn giao chi tiết từng giai đoạn
```

---

## 2. Cách Chạy Ngay (Bản `.EXE` Đã Đóng Gói)

1. Mở thư mục `dist/` và nhấp đúp chạy **`SmartDeskLauncher.exe`** (chọn **Yes** khi Windows hỏi cấp quyền Administrator để cho phép khóa/mở khóa Task Manager gốc).
2. Ba tài khoản Demo tích hợp sẵn trên màn hình khóa:
   - **👨‍🎓 Học sinh:** `hocsinh` / `hocsinh123` *(Khóa Task Manager gốc, chỉ mở App/Thư mục cho phép, bật Student Task Manager riêng & chạy nền kiểm soát Web/Game/AI)*
   - **👪 Phụ huynh:** `phuhuynh` / `phuhuynh123` *(Mở khóa máy bình thường, quản lý Whitelist `.exe` & Thư mục, Link cấm/cho phép, giới hạn phút AI, cấu hình Bot Telegram & xem thống kê)*
   - **🛠️ Admin:** `admin` / `admin123` *(Toàn quyền hệ thống, cấu hình Kiosk ẩn `explorer.exe`, đổi mật khẩu 3 cấp & thoát hoàn toàn phần mềm)*
3. **Xem Bảng điều khiển trên Điện thoại Phụ huynh:** Khi `SmartDeskLauncher.exe` đang chạy, mở trình duyệt trên điện thoại Android cùng mạng Wi-Fi truy cập địa chỉ LAN hiển thị ở thanh dưới màn hình (ví dụ `http://192.168.1.x:5000`).

---

## 3. 5 Lớp An Toàn Khẩn Cấp Chống Kẹt Máy (Fail-Safe)

1. **Phím tắt Cứu hộ Toàn cục (Win32 `RegisterHotKey`):** Nhấn `Ctrl + Alt + Shift + F12` hoặc `Ctrl + Shift + F10` ở bất kỳ cửa sổ nào để lập tức mở lại Task Manager gốc, bật lại `explorer.exe` và đóng phần mềm.
2. **Nút Thoát Khẩn Cấp trên Màn hình:** Nhấn nút `🆘 Thoát Khẩn Cấp` ở góc dưới bên phải màn hình và nhập mã PIN cứu hộ **`999999`**.
3. **Tự động Khôi phục khi Crash:** Tích hợp `atexit`, `sys.excepthook` và `Tk.report_callback_exception` tự trả lại Windows bình thường nếu có lỗi bất ngờ.
4. **Công tắc File `UNLOCK.txt`:** Tạo tệp trống tên `UNLOCK.txt` cạnh file `.exe`, hệ thống tự mở khóa trong vòng 1 giây.
5. **File `KHOI_PHUC_KHAN_CAP.bat`:** Nhấp đúp chạy trực tiếp để mở khóa Registry `DisableTaskMgr = 0` và khởi động lại `explorer.exe`.

---

## 4. Hướng dẫn Tự Build Lại Từ Mã Nguồn

```powershell
python -m pip install -r requirements.txt
cmd /c build_exe.bat
```
