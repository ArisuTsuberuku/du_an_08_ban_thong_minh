# App điện thoại FocusDesk (bản 2.0)

## Lấy file APK (không cần Android Studio)
1. Mỗi lần sửa code trong thư mục `Code APK/` rồi đẩy lên GitHub, GitHub Actions tự build.
2. Vào GitHub → tab **Actions** → lần chạy "Build APK điện thoại" mới nhất → mục **Artifacts** → tải `FocusDesk-apk` (file zip chứa `app-debug.apk`).
3. Chép APK vào điện thoại → mở → cho phép "Cài ứng dụng không rõ nguồn gốc".
4. Bản 2.0 có mã ứng dụng mới (`vn.focusdesk.app`) nên cài song song với bản cũ được; ghép xong thì gỡ bản cũ.

## Ghép điện thoại với tủ (làm 1 lần, hoặc khi đổi điện thoại)
1. Mở app → **Liên kết bàn học lần đầu** → app hiện **Mã ghép 6 số** (ví dụ `482 913`).
2. Phụ huynh vào web → thẻ **Tủ điện thoại** → **📱 Ghép điện thoại mới** → nhập mã 6 số đó.
3. Trong 2 phút, trên điện thoại bấm **Liên kết lại tủ** (để gần tủ).
4. Web báo **"Tủ đã ghép điện thoại mã 482 913"** → đúng máy. Mã khác → tủ từ chối, không ghép nhầm máy.

## Mỗi ca học
Mở app → **BẮT ĐẦU HỌC** → cất điện thoại vào tủ, cắm sạc (trước hay sau đều được) → đóng cửa tủ → ca học bắt đầu.
App giữ kết nối suốt ca; rút sạc là báo tủ ngay; rút sạc quá 2 phút app tự ngừng cho đỡ pin.

## Những gì đã sửa so với bản 1.0
- Gửi trạng thái sạc ngay sau khi ký (bản cũ: cắm sạc trước thì tủ không bao giờ biết).
- Gửi `UNCHARGED` khi rút sạc; không tự tắt sau 2 giây nữa.
- Trả lời mọi yêu cầu đọc của tủ, có ô CCCD chuẩn BLE, theo dõi kết nối / mất kết nối.
- Kiểm tra khóa thật trong Keystore mỗi lần mở app; nút **Tạo khóa mới**; không cần gỡ app để ghép lại.
- Mã ghép 6 số để đối chiếu với web phụ huynh; tắt sao lưu (khóa TEE không sao lưu được).
- Bỏ PowerConnectionReceiver (không chạy từ Android 8+), quyền khởi động máy thừa, thư viện biometric alpha.
