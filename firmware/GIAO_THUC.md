# Hộp khóa điện thoại ESP32-S3 — giao thức với máy tính

Kết nối: cáp USB → cổng COM ảo, 115200 baud. Mỗi dòng là 1 JSON, kết thúc bằng `\n`.
Phần mềm máy tính: `services/phone_box.py` (tự dò cổng, bỏ qua cổng Bluetooth). Firmware: `hop_dien_thoai_esp32s3/`.

## Tin nhắn

| Chiều | `t` | Trường thêm | Ý nghĩa |
| --- | --- | --- | --- |
| PC → hộp | `hello` | | Hỏi danh tính khi mới mở cổng |
| PC → hộp | `ping` | `ts` | 2 giây/lần; hộp trả `state` |
| PC → hộp | `lock` | `mac` | Khóa hộp |
| PC → hộp | `unlock` | `sec`, `mac` | Mở khóa; `sec > 0` = tự khóa lại sau chừng ấy giây khi nắp đã đóng |
| PC → hộp | `beep` | `n` | Kêu n tiếng (thử kết nối) |
| PC → hộp | `setkey` | `key` | Ghép: chỉ nhận trong 60 giây sau khi giữ nút 5 giây lúc hộp đang mở |
| Hộp → PC | `hello` | `dev`, `fw`, `box_id`, `paired`, `nonce`, trạng thái | `dev` luôn là `smartdesk-box` |
| Hộp → PC | `state` | `phone`, `lid`, `lock`, `nonce`, `unlock_left` | Trả lời `ping` |
| Hộp → PC | `event` | `e` + trạng thái | `phone_in`, `phone_out`, `lid_open`, `lid_close`, `locked`, `unlocked`, `tamper`, `button`, `pair_mode` |
| Hộp → PC | `ack` | `id`, `ok`, `err`, `nonce` | Kết quả lệnh; `err`: `bad_mac`, `not_in_pair_mode`, `unknown` |

## Chữ ký lệnh

`mac = HMAC-SHA256(key, "t|id|nonce|sec")` lấy 16 ký tự hex đầu. `nonce` do hộp tạo ngẫu nhiên, đổi sau mỗi lệnh
hợp lệ → không thể gõ tay lệnh mở hộp từ phần mềm terminal, không phát lại được lệnh cũ.

## Luồng sử dụng

1. Học sinh đăng nhập → bỏ điện thoại vào hộp (`phone_in`) → máy tự gửi `lock` → hộp báo `locked`.
2. Trong giờ học: mở nắp / lấy điện thoại khi đang khóa → `tamper` → báo phụ huynh ngay. Rút cáp → máy báo "mất kết nối".
3. Học sinh bấm nút trên hộp → `button` → phụ huynh nhận "xin lấy điện thoại".
4. Phụ huynh bấm "Mở hộp điện thoại" trên web / gõ `/mohop` trên Telegram → cloud → máy → `unlock` (có thể kèm phút).
5. Mất điện / máy tắt: hộp giữ nguyên trạng thái khóa (an toàn), nên có chìa khóa cơ dự phòng cho phụ huynh.
