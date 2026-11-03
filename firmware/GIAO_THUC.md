# Hộp khóa điện thoại FocusDesk — chuẩn giao tiếp với máy tính (cho người làm phần cứng)

Tài liệu này là **hợp đồng giữa hộp và phần mềm FocusDesk**. Bạn có thể dùng mạch, cảm biến và cơ cấu khóa tùy ý.
Chỉ cần hộp nói đúng giao thức dưới đây là phần mềm nhận được và điều khiển được.

- Phần mềm phía máy tính: `services/phone_box.py`
- Firmware mẫu (Arduino, ESP32-S3) đã chạy đúng chuẩn: `firmware/hop_dien_thoai_esp32s3/`
- Hộp giả lập để đối chiếu: `tests/fake_phone_box.py`; bộ kiểm thử: `python tests/test_phone_box.py`

## 1. Lớp vật lý

| Mục | Chuẩn |
| --- | --- |
| Kết nối | Cáp USB → cổng COM ảo (USB-CDC native của ESP32-S3, hoặc chip CH340 / CP210x / FTDI) |
| Tốc độ | **115200 baud**, 8N1. Đổi được trong Admin → Hộp điện thoại → *Tốc độ (baud)*, nhưng phải trùng `Serial.begin()` |
| Tự dò cổng | Phần mềm ưu tiên VID `0x303A` (Espressif), rồi `0x1A86` (CH340), `0x10C4` (CP210x), `0x0403` (FTDI); bỏ qua cổng Bluetooth |
| DTR | Phần mềm bật DTR khi mở cổng (USB-CDC của ESP32-S3 cần DTR mới gửi dữ liệu) |
| Arduino IDE | Board *ESP32S3 Dev Module*, **USB CDC On Boot = Enabled**, thư viện ArduinoJson 7 |

## 2. Khung tin nhắn

- Mỗi tin là **một dòng JSON UTF-8, kết thúc bằng `\n`** (NDJSON). Không gộp nhiều JSON trên một dòng.
- Trường bắt buộc: `"t"` = loại tin. Lệnh từ máy tính có `"id"` (số nguyên tăng dần); hộp trả `ack` với đúng `id` đó.
- Dòng không phải JSON (log khởi động `ESP-ROM...`, `Serial.println` gỡ lỗi) **được phép**: phần mềm bỏ qua,
  nhưng vẫn hiện trong Serial monitor (màu xám) để bạn đọc.
- Độ dài một dòng nên < 400 byte.

## 3. Danh sách tin nhắn

| Chiều | `t` | Trường | Hộp phải làm gì |
| --- | --- | --- | --- |
| PC → hộp | `hello` | `id` | Trả `hello` (mục 4) **trong vòng 2,5 giây**, nếu không phần mềm coi cổng này không phải hộp |
| PC → hộp | `ping` | `id`, `ts` | Gửi 2 giây/lần; trả `state`. Quá 6 giây không nghe gì từ hộp → phần mềm báo mất kết nối |
| PC → hộp | `get` | `id` | Trả `state` ngay (dùng khi gỡ lỗi) |
| PC → hộp | `beep` | `id`, `n` | Kêu `n` tiếng, trả `ack ok` |
| PC → hộp | `water` | `id`, `ms` | **Thưởng xanh:** bật rơ-le bơm tưới cây `ms` mili-giây (firmware khóa cứng tối đa 10 000), trả `ack ok`, gửi `event watering` rồi `event watered` khi tắt bơm |
| PC → hộp | `lock` | `id`, `mac` | Kiểm chữ ký → khóa → `ack ok` → `event locked` |
| PC → hộp | `unlock` | `id`, `sec`, `mac` | Kiểm chữ ký → mở → `ack ok` → `event unlocked`. `sec > 0` = tự khóa lại sau `sec` giây khi nắp đã đóng |
| PC → hộp | `setkey` | `id`, `key` | Chỉ nhận khi đang ở chế độ ghép (mục 6); lưu `key` vào bộ nhớ không mất khi tắt nguồn |
| Hộp → PC | `hello` | `dev`, `fw`, `box_id`, `paired`, `nonce`, `phone`, `lid`, `lock` | `dev` **phải là** `"focusdesk-box"` (firmware cũ `"smartdesk-box"` vẫn được nhận) |
| Hộp → PC | `state` | `phone`, `lid`, `lock`, `nonce`, `unlock_left?` | Trạng thái hiện tại |
| Hộp → PC | `event` | `e` + `phone`, `lid`, `lock`, `nonce` | Gửi ngay khi có thay đổi, không chờ ping |
| Hộp → PC | `ack` | `id`, `ok`, `err?`, `nonce` | Kết quả lệnh. `err`: `bad_mac`, `not_in_pair_mode`, `unknown` |

Giá trị trạng thái: `phone` = `true`/`false`; `lid` = `"closed"`/`"open"`; `lock` = `"locked"`/`"unlocked"`.

Sự kiện `e` và cách phần mềm xử lý:

| `e` | Khi nào | Phần mềm làm gì |
| --- | --- | --- |
| `phone_in` | Đặt điện thoại vào | Báo phụ huynh; đang giờ học thì tự gửi `lock` |
| `phone_out` | Lấy điện thoại ra khi hộp đang mở | Báo phụ huynh nếu đang giờ học |
| `lid_open` / `lid_close` | Mở / đóng nắp bình thường | Cập nhật trạng thái |
| `locked` / `unlocked` | Sau lệnh hợp lệ hoặc tự khóa lại | Báo phụ huynh, cập nhật dock học sinh và web |
| `tamper` | Nắp mở hoặc điện thoại bị lấy ra **khi đang khóa** | Báo khẩn lên web và Telegram |
| `button` | Bấm nút ngắn | Báo "học sinh xin lấy điện thoại" |
| `pair_mode` | Giữ nút 5 giây khi hộp đang mở | Cho phép ghép trong 60 giây |

## 4. Ví dụ một phiên

```
→ {"t":"hello","id":1}
← {"t":"hello","dev":"focusdesk-box","fw":"1.0.0","box_id":"A1B2C3D4E5F6","paired":true,"nonce":"a1b2c3d4","phone":false,"lid":"closed","lock":"unlocked"}
→ {"t":"ping","id":2,"ts":1793660000}
← {"t":"state","phone":false,"lid":"closed","lock":"unlocked","nonce":"a1b2c3d4"}
← {"t":"event","e":"phone_in","phone":true,"lid":"closed","lock":"unlocked","nonce":"a1b2c3d4"}
→ {"t":"lock","id":3,"mac":"…16 ký tự hex…"}
← {"t":"ack","id":3,"ok":true,"nonce":"5e6f7a8b"}
← {"t":"event","e":"locked","phone":true,"lid":"closed","lock":"locked","nonce":"5e6f7a8b"}
```

## 5. Chữ ký lệnh (bắt buộc với `lock`, `unlock`)

```
mac = 16 ký tự hex đầu của HMAC-SHA256(key, chuỗi)       chuỗi = t + "|" + id + "|" + nonce + "|" + sec
```

- `key`: chuỗi 32 ký tự hex nhận qua `setkey` lúc ghép. `sec` = 0 nếu lệnh không có `sec`.
- `nonce`: chuỗi ngẫu nhiên **do hộp tạo**, gửi kèm mọi `hello`/`state`/`event`/`ack`. **Phải đổi nonce mới sau mỗi
  lệnh hợp lệ**, để lệnh cũ bị bắt lại không dùng được lần hai.
- Sai `mac` → trả `{"t":"ack","id":…,"ok":false,"err":"bad_mac"}` và **không** đổi trạng thái.

Bộ số kiểm tra (dùng để thử hàm HMAC trên mạch của bạn), với `key = 0123456789abcdef0123456789abcdef`:

| t | id | nonce | sec | mac đúng |
| --- | --- | --- | --- | --- |
| unlock | 12 | a1b2c3d4 | 600 | `0189edba9cb2c4b7` |
| lock | 13 | 5e6f7a8b | 0 | `41582e29323ee4fa` |

## 6. Ghép hộp với máy tính

1. Hộp đang **mở khóa**: giữ nút 5 giây → hộp gửi `event pair_mode`, kêu 1 tiếng dài, mở cửa sổ ghép 60 giây.
2. Trên máy: Admin → 📦 Hộp điện thoại → **Ghép hộp**. Phần mềm tạo khóa ngẫu nhiên, gửi `setkey`, lưu khóa vào cấu hình.
3. Hộp trả `ack ok`, lưu khóa, thoát chế độ ghép. Từ đây `hello` có `"paired": true`.

Ngoài cửa sổ ghép, hộp phải từ chối `setkey` bằng `err: "not_in_pair_mode"`.

## 7. Yêu cầu an toàn của phần cứng

- Mất điện hoặc khởi động lại: **giữ nguyên trạng thái khóa** đã lưu (không tự mở).
- Mất máy tính quá 60 giây: giữ nguyên trạng thái, có thể nhấp nháy đèn để báo.
- Nên có chìa khóa cơ dự phòng cho phụ huynh.

## 8. Gỡ lỗi bằng Serial monitor trong phần mềm (đăng nhập Admin)

Đường dẫn: đăng nhập **Admin** → menu **📦 Hộp điện thoại** → thẻ *Gỡ lỗi giao tiếp* → **🛠️ Mở Serial monitor**.

| Thành phần | Dùng để |
| --- | --- |
| Màu xanh dương `→` | Dòng máy tính gửi (TX) |
| Màu xanh lá `←` | Dòng JSON hộp gửi về (RX) |
| Màu xám `←` | Dòng hộp gửi nhưng không phải JSON (log khởi động, `Serial.println` gỡ lỗi) |
| Màu đỏ | `ack` lỗi (`ok:false`) hoặc `tamper` |
| Màu vàng `•` | Thông báo hệ thống: mở/đóng cổng, không thấy `hello`, mất kết nối |
| *Ẩn ping/state định kỳ* | Ẩn nhịp ping 2 giây và câu trả lời của nó; `get` bấm tay vẫn hiện |
| Ô nhập + **Gửi** | Gửi nguyên văn một dòng JSON bất kỳ |
| Lệnh nhanh | `hello`, `get`, `ping`, `beep 2`, `lock (ký)`, `unlock 60s (ký)`: phần mềm tự tính `mac` bằng khóa đã ghép |
| **▶ Mở cổng thô** | Tạm dừng kết nối tự động, mở đúng cổng + baud bạn chọn và hiện mọi dòng nhận được. Dùng khi hộp chưa kết nối được |
| **💾 Lưu log** | Ghi nhật ký vào `logs/serial_YYYYMMDD_HHMMSS.txt` để gửi cho người kia xem |

Đóng cửa sổ hoặc bấm **⏹ Đóng cổng thô** thì phần mềm tự bật lại kết nối tự động.
Nhớ **đóng Serial Monitor của Arduino IDE** trước, vì một cổng COM chỉ một chương trình mở được.

Lỗi hay gặp:

| Hiện tượng trong Serial monitor | Nguyên nhân thường gặp |
| --- | --- |
| `• Không mở được COMx: Access is denied` | Arduino IDE hoặc chương trình khác đang giữ cổng |
| `• COMx: không thấy hello hợp lệ…` | Sai baud, `dev` khác `focusdesk-box`, hoặc trả `hello` chậm quá 2,5 giây |
| Toàn ký tự rác | Sai baud |
| Không có dòng nào, kể cả ở cổng thô | Chưa bật *USB CDC On Boot*, cáp chỉ có dây nguồn, hoặc chọn nhầm cổng |
| `ack … "err":"bad_mac"` | Hàm HMAC sai (thử bằng bảng ở mục 5), hộp chưa đổi nonce đúng cách, hoặc chưa ghép |
| `ack … "err":"not_in_pair_mode"` | Bấm *Ghép hộp* khi chưa giữ nút 5 giây, hoặc đã quá 60 giây |

## 9. Danh sách nghiệm thu (hộp đạt chuẩn khi qua hết)

1. Cắm USB, Admin → Hộp điện thoại hiện 🟢 *Đã kết nối* trong vòng 10 giây, đúng `box_id` và `fw`.
2. Serial monitor: `get` → nhận `state` có `nonce`.
3. Ghép hộp thành công; `hello` sau đó có `"paired": true`.
4. `lock (ký)` → `ack ok` + `event locked`, nonce đổi. Gửi lại đúng dòng `lock` cũ bằng tay → `bad_mac`.
5. Đặt / lấy điện thoại, mở / đóng nắp → đúng `event` tương ứng.
6. Đang khóa mà mở nắp → `tamper`.
7. `unlock 60s (ký)` → mở; đóng nắp, sau 60 giây tự `event locked`.
8. Rút cáp → phần mềm báo mất kết nối trong khoảng 6 giây; cắm lại → tự kết nối lại.
9. Tắt nguồn khi đang khóa, bật lại → vẫn khóa.
