# Hệ Thống Bàn Học Thông Minh Hợp Nhất Không Gian Mạng - Vật Lý (Cyber-Physical Smart Study Desk)

Phần mềm trung tâm quản lý kỷ luật số 3 cấp quyền kiểu Gcafe/CSM trên Windows kết hợp kiểm soát trình duyệt theo Link Address (URL), đếm thời gian Chatbot AI, Thị giác Máy tính nhận diện tư thế 3D tại biên (MediaPipe Holistic + `cv2.solvePnP`), Bảng điều khiển Web LAN (`0.0.0.0:5000`) cho PC & Điện thoại Android và Trợ lý cảnh báo hai chiều qua Bot Telegram (Local-first, Zero-Cloud).

---

## 1. Cấu trúc Thư mục Dự án

```text
Du_an_D08_ban_thong_minh/
├── dist/
│   ├── SmartDeskLauncher.exe       # Bản đóng gói hoàn chỉnh chạy ngay (Quyền Administrator, ~25 MB)
│   ├── shared_config.json          # Cấu hình dùng chung đi kèm file .exe
│   └── KHOI_PHUC_KHAN_CAP.bat      # Công cụ khôi phục khẩn cấp 1-click
├── models/
│   └── holistic_landmarker.task    # Mô hình MediaPipe Holistic ngoại tuyến (kèm tùy chọn keras_model.h5)
├── core/
│   ├── config_manager.py           # Quản lý đọc/ghi nguyên tử shared_config.json & runtime_state.json
│   └── failsafe.py                 # 5 Lớp An toàn Khẩn cấp chống kẹt máy Windows
├── services/
│   ├── giam_sat_worker.py          # Quét URL (uiautomation) + Tiêu đề Tab + Đếm giờ AI + Diệt Game
│   ├── posture_ai_engine.py        # Lõi Toán học 3D: EAR, PERCLOS, MAR ngáp, solvePnP Euler, PAD, 2 tay
│   ├── posture_controller.py       # Bộ điều phối MJPEG /video_feed & Sidecar AI cho cả .py và .exe
│   └── telegram_service.py         # Bắn cảnh báo Cooldown & Nhận lệnh 2 chiều (/status, /lock, /reset_ai)
├── he_thong_quan_ly_3_cap.py       # Ứng dụng Desktop trung tâm 3 cấp quyền + Student Task Manager
├── trang_web.py                    # Flask Web Dashboard (0.0.0.0:5000) + Luồng MJPEG /video_feed nội bộ LAN
├── posture_ai_engine.py            # Bản xem trực tiếp cửa sổ AI 3D hoặc chạy ngầm (--daemon)
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

## 4. Mô-đun AI Nhận Diện Tư Thế Ngồi Học 3D (MediaPipe Holistic)

- **Tự động kích hoạt khi Học sinh đăng nhập** (hoặc bật/tắt trực tiếp trên Web Dashboard qua nút `📷 Bật / Tắt Camera AI`).
- **Hiệu chỉnh Gốc Tư thế Cá nhân hóa 5 giây đầu:** Tự động lấy chuẩn riêng theo chiều cao và góc đặt Webcam của từng học sinh; Phụ huynh có thể bấm `🔄 Hiệu chỉnh lại Gốc Tư thế (5s)` từ điện thoại bất cứ lúc nào.
- **Đầy đủ chỉ số sinh trắc học 3D tại biên:** `EAR` 6 điểm, `PERCLOS` buồn ngủ, `MAR` & bộ đếm tần suất ngáp, giải `cv2.solvePnP` suy ra 3 góc Euler (`Pitch`, `Yaw`, `Roll`), phát hiện ngủ gật `E_nod`, theo dõi 2 bàn tay trên bàn (`>15s`), chống giả mạo ảnh tĩnh PAD (`>25s`) và chặn Camera ảo (`OBS`, `ManyCam`, `VCam`).
- **Bảo mật quyền riêng tư (Zero-Cloud):** Luồng video `/video_feed` chỉ truyền nội bộ mạng Wi-Fi gia đình (LAN); Bot Telegram chỉ nhận cảnh báo chữ kèm điểm số `CFI%` khi học sinh sai tư thế kéo dài.
- **Chạy thử cửa sổ AI độc lập:**
  ```powershell
  python posture_ai_engine.py
  ```

---

## 5. Hướng dẫn Tự Build Lại Từ Mã Nguồn

```powershell
python -m pip install -r requirements.txt
cmd /c build_exe.bat
```
