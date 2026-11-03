# Giao thức FocusDesk ⇄ ESP32 — v2 (tủ điện thoại + tưới cây thưởng)

Đây là **hợp đồng giữa mô-đun ESP32 và phần mềm FocusDesk** trên máy tính. Mạch, cảm biến, app Android có thể
thay tùy ý, miễn là ESP32 nói đúng các tin nhắn dưới đây.

| Thành phần | Vị trí |
| --- | --- |
| Firmware chuẩn v2 (gộp từ code của nhóm `esp_code/sketch_nov2a.ino`) | `firmware/focusdesk_esp32/focusdesk_esp32.ino` |
| Phần mềm phía máy tính | `services/phone_box.py` (kết nối, lệnh, chính sách cảnh báo), `services/study_sessions.py` (ca học) |
| Tủ giả lập + kiểm thử | `tests/fake_tu_v2.py`, `python tests/test_tu_v2.py` |
| Bảng lệnh thử nghiệm | App FocusDesk → **Admin → 📦 Hộp điện thoại → 🧪 Tủ điện thoại v2** và **Serial monitor** (hàng “Tủ v2”) |
| Giao thức cũ (hộp khóa, giai đoạn 1) | `firmware/GIAO_THUC_v1_hop_khoa.md` — phần mềm vẫn hỗ trợ (tự nhận theo `proto`) |

## 1. Lớp vật lý & khung tin

- Cáp USB → cổng COM ảo (USB-CDC của ESP32-S3/C3, hoặc CH340 / CP210x). **115200 baud**, 8N1.
- Mỗi tin là **1 dòng JSON UTF-8 kết thúc `\n`** (NDJSON), < 400 byte. Dòng không phải JSON (log khởi động,
  `Serial.println` gỡ lỗi) được phép: phần mềm bỏ qua nhưng vẫn hiện trong Serial monitor.
- Trường `t` = loại tin. Lệnh từ máy tính có `id` (số tăng dần) → ESP32 trả `{"t":"ack","id":…,"ok":…}` trong ≤ 3 s.
  Lệnh không có `id` (gõ tay) vẫn chạy nhưng không có ack.
- v2 **không ký HMAC** (không còn khóa cơ khí). Danh tính điện thoại do chữ ký **TEE (ECDSA, BLE)** của app Android bảo đảm.

## 2. Máy trạng thái của tủ

```
             cửa ĐÓNG + điện thoại ĐANG SẠC + chữ ký TEE hợp lệ
   ┌──────┐ ───────────────────────────────────────────────► ┌────────┐   hết session_ms   ┌──────┐
   │ idle │                                                  │ active │ ─────────────────► │ done │
   └──────┘ ◄─────────────── mở cửa (lấy điện thoại) ──────── └────────┘   (hoặc lệnh end)  └──────┘
       ▲                                                                                      │
       └──────────────────────────────── mở cửa ─────────────────────────────────────────────┘
```

| Pha | Đèn / còi | Mở cửa, rút sạc |
| --- | --- | --- |
| `idle` | tắt | gửi event, `active:false` → phần mềm **không báo** |
| `active` | CFI < `led_cfi` → đèn đỏ nháy 0,5 s; CFI < `buzz_cfi` → còi 0,3 s; vi phạm → đỏ + còi 1,5 s | event `door_open` / `phone_out`, `active:true` → **vi phạm** + báo phụ huynh |
| `done` | đèn xanh nháy 5 s | event với `active:false` → **không báo nữa** |

Tủ **không tự bơm** khi hết giờ: web chấm ca rồi mới gửi lệnh `water` (mục 5).

## 3. Lệnh máy tính → ESP32

| `t` | Trường | ESP32 làm gì | ack lỗi (`err`) |
| --- | --- | --- | --- |
| `hello` | — | trả tin `hello` (mục 4) | — |
| `ping` | `ts`, `cfi` (0–100, **-1** = không có camera / không trong giờ học) | cập nhật CFI, trả `state`. Phần mềm ping 2 s/lần | — |
| `get` | — | trả `state` | — |
| `cfg` | `session_ms` (60 000 – 14 400 000) hoặc `session_min`; `led_cfi`, `buzz_cfi` (0–100) | lưu vào bộ nhớ (Preferences), áp dụng **từ ca sau** | — |
| `water` | `ms` (300 – 10 000, mặc định 2500) | bật rơ-le bơm, event `watering` → `watered` | — |
| `beep` | `ms` (50 – 3000) | còi + đèn đỏ | — |
| `reset_key` | — | xóa khóa công khai điện thoại đã ghép, ngắt BLE; điện thoại kết nối **tiếp theo** được ghép mới; event `key_reset` | `busy` (đang trong ca) |
| `end` | `reason` | kết thúc ca sớm → event `session_end` | `no_session` |
| `start` | `session_ms` (10 000 – 14 400 000, bỏ trống = theo `cfg`) | **chỉ để thử nghiệm**: vào ca ngay, bỏ qua cửa / sạc / chữ ký; mọi tin trong ca có `"test":true` → FocusDesk không ghi ca, không tính vi phạm, không tưới (chỉ báo kèm “🧪 [Thử nghiệm]”) | `busy` |
| `io` | — | thử dây: đèn đỏ 0,6 s → đèn xanh 0,6 s → còi 0,3 s (không bật bơm) | — |
| `cfi`, `set_time` | `score` / `ms` | tên cũ trong code của nhóm — vẫn nhận | — |
| khác | | | `unknown` |

## 4. Tin ESP32 → máy tính

`hello` = `{"t":"hello","dev":"focusdesk-box","fw":"2.2.0","box_id":"A1B2","nonce":"…", …trường trạng thái}`
(`dev` phải là `focusdesk-box`, phần mềm chờ tối đa 2,5 s sau khi mở cổng).

**Trường trạng thái** (có trong `hello`, `state` và mọi `event`):

| Trường | Ý nghĩa |
| --- | --- |
| `proto` | `2` — phần mềm dựa vào đây để dùng giao thức v2 |
| `phase` / `active` | `idle` · `active` · `done` / `true` khi đang trong ca |
| `door` (`lid`) | `closed` / `open` — công tắc hành trình cửa tủ (`lid` = tên cũ v1) |
| `phone` | `true` = điện thoại đang cắm sạc trong tủ |
| `ble`, `verified`, `paired` | đã nối BLE với app · chữ ký TEE ca này đã hợp lệ · đã lưu khóa điện thoại |
| `session_s`, `left_s` | thời lượng ca đang đặt · số giây còn lại của ca |
| `viol`, `cfi`, `pump` | số vi phạm trong ca · CFI tủ đang dùng · bơm đang chạy |
| `test` | `true` khi đang trong ca thử nghiệm (lệnh `start`) |
| `lock` | luôn `"none"` ở v2 |

**Sự kiện** `{"t":"event","e":…}`:

| `e` | Khi nào | Phần mềm FocusDesk xử lý |
| --- | --- | --- |
| `session_started` | đủ 3 điều kiện ở pha `idle` | mở ca học (`source:"box"`), báo “bắt đầu ca học N phút” |
| `session_end` | hết giờ (`reason:"time_up"`) hoặc lệnh `end`; kèm `dur_s` | chốt ca theo `dur_s` của tủ → gửi web chấm |
| `door_open` / `door_close` | công tắc hành trình đổi trạng thái (chống dội 60 ms) | `active:true` → vi phạm `PHONE` + báo; còn lại im lặng |
| `phone_in` / `phone_out` | bắt đầu / ngừng sạc (BLE `CHARGED`/`UNCHARGED` hoặc chân `PIN_CHARGE`) | như trên |
| `paired`, `key_reset` | ghép điện thoại mới / đã xóa khóa | báo phụ huynh |
| `watering`, `watered` | bơm bật / tắt | ghi nhật ký |

## 5. Luồng đầy đủ một ca học

```
Phụ huynh (web)        Cloud (Vercel)            FocusDesk (PC)                    Tủ ESP32
 đặt Thời gian ca 45′,  study_cfg + study_box_ver
 đèn<50, còi<30  ─────► heartbeat: study_box ───► lưu study_box_cfg ── cfg ───────► lưu, ack
                                                  ── ping {cfi} mỗi 2 s ──────────► đèn/còi theo CFI
                                                                       ◄─ session_started (đóng cửa+sạc)
                                                  mở ca (source=box)
                                                                       ◄─ door_open (active) → vi phạm
                                                                       ◄─ session_end {time_up, dur_s}
                        ◄── sessions (heartbeat) ─ chốt ca
                        chấm: đủ phút, vi phạm ≤ ngưỡng,
                        tập trung ≥ 2/3 thời gian
                        đạt → lệnh water_plant ──► water {ms} ──────────────────────► bơm 2,5 s, watered
                                                                       ◄─ phone_out (active=false) → không báo
```

- Lệnh từ web: `water_plant` (🌱 Tưới cây ngay), `box_reset_key` (🔑 Reset khóa điện thoại) — thẻ Tủ điện thoại ở trang chủ.
- Ca do tủ đếm giờ: phần mềm **không** mở ca khi học sinh vào chế độ Học sinh và **không** chốt ca khi rời chế độ đó
  (chỉ tủ báo `session_end` mới chốt). Mất kết nối tủ giữa ca → báo phụ huynh.
- Web chấp nhận sai lệch ≤ 6 giây với ca `time_up` (độ trễ USB / làm tròn).

## 6. Nối dây (firmware mặc định)

| Chân | Nối với |
| --- | --- |
| GPIO4 | IN rơ-le bơm mini (HIGH = chạy) |
| GPIO5 | công tắc hành trình cửa tủ: COM → GND, NO → GPIO5 (INPUT_PULLUP, cửa đóng = LOW) |
| GPIO6 | (tùy chọn) cảm biến sạc, LOW = đang sạc — đặt `PIN_CHARGE 6` |
| GPIO7 / GPIO8 | LED đỏ (mất tập trung, vi phạm) / LED xanh (hết giờ, đang bơm) qua điện trở 220 Ω |
| GPIO9 | còi buzzer (qua transistor nếu còi > 20 mA) |

## 6b. Biên dịch & nạp (đã thử 03/11/2026 trên bo của nhóm)

- Bo: **ESP32-S3 (QFN56) rev 0.2, PSRAM 8 MB**, cổng nạp qua chip **CH343** (COM7 trên máy MSI).
- Thư viện: ArduinoJson 7.4.3; core esp32 3.3.12. Bản build: 685 KB (21 % phân vùng huge_app), RAM 28,7 KB.
- Arduino IDE: Board *ESP32S3 Dev Module*, Partition *Huge APP (3MB No OTA)*, USB CDC On Boot = *Disabled* (dùng cổng CH343).
- Dòng lệnh:
  `arduino-cli compile --fqbn esp32:esp32:esp32s3:PartitionScheme=huge_app firmware/focusdesk_esp32`
  `arduino-cli upload -p COM7 --fqbn esp32:esp32:esp32s3:PartitionScheme=huge_app firmware/focusdesk_esp32`
- Khóa điện thoại đã ghép bằng firmware cũ của nhóm (Preferences `focusdesk/pubkey`) được giữ nguyên sau khi nạp.

## 7. Thử nhanh bằng Serial monitor (gõ từng dòng)

```
{"t":"hello","id":1}
{"t":"cfg","id":2,"session_ms":60000,"led_cfi":50,"buzz_cfi":30}
{"t":"io","id":3}                       ← thử dây: đỏ → xanh → còi
{"t":"start","id":4,"session_ms":60000} ← ca thử 1 phút (không cần điện thoại)
{"t":"ping","id":5,"cfi":20}            ← trong ca: đèn đỏ nháy + còi
{"t":"water","id":6,"ms":2000}
{"t":"end","id":7,"reason":"admin"}
{"t":"reset_key","id":8}
```

## 8. Phía app Android (không đổi)

Service BLE `12345678-1234-5678-1234-567890abcdef`: `…de1` nonce (ESP32 ghi 32 byte ngẫu nhiên), `…de2` chữ ký
(app ký SHA-256(nonce) bằng khóa TEE, notify), `…de3` khóa công khai DER (ESP32 đọc lần ghép đầu), `…de4` lệnh
(notify `CHARGED`; nên bổ sung `UNCHARGED` khi rút sạc để tủ phát hiện ngay cả khi cửa chưa mở).
