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
   chế độ Học sinh trên máy + cửa ĐÓNG + điện thoại ĐANG SẠC + chữ ký TEE hợp lệ
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
| `ping` | `ts`, `cfi` (0–100, **-1** = không có camera / không trong giờ học), `study` (true = học sinh đang ở chế độ Học sinh) | cập nhật CFI + cờ học, trả `state`. Phần mềm ping 2 s/lần. Tủ CHỈ vào ca khi `study=true` (FocusDesk phải đang chạy; mất ping > 10 s coi như false) | — |
| `get` | — | trả `state` | — |
| `cfg` | `session_ms` (60 000 – 14 400 000) hoặc `session_min`; `led_cfi`, `buzz_cfi` (0–100) | lưu vào bộ nhớ (Preferences), áp dụng **từ ca sau** | — |
| `water` | `ms` (300 – 10 000, mặc định 2500) | bật rơ-le bơm, event `watering` → `watered` | — |
| `beep` | `ms` (50 – 3000) | còi + đèn đỏ | — |
| `reset_key` | `expect` (tùy chọn): mã ghép 6 số trên app | xóa khóa điện thoại đã ghép, ngắt BLE, **mở cửa sổ ghép 2 phút**; có `expect` thì chỉ nhận đúng điện thoại có mã đó (khác mã → event `pair_mismatch`, không ghép); event `key_reset` | `busy` (đang trong ca) |
| `end` | `reason` | kết thúc ca sớm → event `session_end` | `no_session` |
| `start` | `session_ms` (10 000 – 14 400 000, bỏ trống = theo `cfg`) | **chỉ để thử nghiệm**: vào ca ngay, bỏ qua cửa / sạc / chữ ký; mọi tin trong ca có `"test":true` → FocusDesk không ghi ca, không tính vi phạm, không tưới (chỉ báo kèm “🧪 [Thử nghiệm]”) | `busy` |
| `io` | — | thử dây: đèn đỏ 0,6 s → đèn xanh 0,6 s → còi 0,3 s (không bật bơm) | — |
| `charge` | `on` (true/false) | **thử nghiệm**: giả lập điện thoại đang sạc (trạng thái có `sim_charge:true`) | — |
| `cfi`, `set_time` | `score` / `ms` | tên cũ trong code của nhóm — vẫn nhận | — |
| khác | | | `unknown` |

## 4. Tin ESP32 → máy tính

`hello` = `{"t":"hello","dev":"focusdesk-box","fw":"2.4.2","box_id":"A1B2","nonce":"…", …trường trạng thái}`
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
| `key_fp` | **mã ghép 6 số** của điện thoại đã ghép = (4 byte đầu SHA-256 của khóa công khai DER, số không dấu) mod 1 000 000 — app tính giống hệt; rỗng = chưa ghép |
| `pair_left`, `pair_expect` | số giây còn lại của cửa sổ ghép · mã phụ huynh đã nhập (nếu có) |
| `lock` | luôn `"none"` ở v2 |

**Sự kiện** `{"t":"event","e":…}`:

| `e` | Khi nào | Phần mềm FocusDesk xử lý |
| --- | --- | --- |
| `session_started` | đủ 3 điều kiện ở pha `idle` | mở ca học (`source:"box"`), báo “bắt đầu ca học N phút” |
| `session_end` | hết giờ (`reason:"time_up"`) hoặc lệnh `end`; kèm `dur_s` | chốt ca theo `dur_s` của tủ → gửi web chấm |
| `door_open` / `door_close` | công tắc hành trình đổi trạng thái (chống dội 60 ms) | `active:true` → vi phạm `PHONE` + báo; còn lại im lặng |
| `phone_in` / `phone_out` | bắt đầu / ngừng sạc (BLE `CHARGED`/`UNCHARGED` hoặc chân `PIN_CHARGE`) | như trên |
| `paired`, `key_reset` | ghép điện thoại mới (kèm `key_fp`) / đã xóa khóa | báo phụ huynh “đã ghép điện thoại mã 482 913” |
| `pair_mismatch` | điện thoại có mã `got` khác mã `expect` phụ huynh nhập | báo phụ huynh, tủ ngắt máy đó, không ghép |
| `pair_refused` | có điện thoại chưa ghép muốn ghép nhưng hết cửa sổ ghép | báo phụ huynh bấm “Ghép điện thoại mới” |
| `verify_failed` | chữ ký sai (điện thoại có khóa khác khóa đã ghép: cài lại app / tạo khóa mới) | báo phụ huynh ghép lại |
| `watering`, `watered` | bơm bật / tắt | ghi nhật ký |
| `ble_connected` | tủ vừa nối BLE với app; kèm `has_sign`, `has_cmd`, `cmd_notify`, `cmd_indicate`, `cmd_read` | ghi nhật ký (gỡ lỗi app) |
| `ble_cmd` | app gửi chuỗi qua `…de4`; `v` = chuỗi thô | ghi nhật ký — xem đúng chữ app gửi |
| `ble_lost` | mất BLE | ghi nhật ký |

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

## 7b. Ghép điện thoại từ web phụ huynh (giống mã ghép 6 số của máy tính)

1. App điện thoại (bản 2.0, `Code APK/FocusDesk`) hiện **Mã ghép 6 số**.
2. Web phụ huynh → thẻ Tủ điện thoại → **📱 Ghép điện thoại mới** → nhập mã → lệnh `box_reset_key {code}` → FocusDesk → `reset_key {expect}`.
3. Trong 2 phút, trên app bấm **Liên kết lại tủ** → tủ đọc khóa, tính mã, đúng `expect` mới lưu → event `paired` → web hiện “Mã ghép điện thoại: 482 913”.
4. Tủ mới tinh (chưa có khóa) tự mở cửa sổ ghép 5 phút sau khi cấp điện. Ngoài cửa sổ ghép, tủ không nhận khóa lạ (`pair_refused`).

## 8. Phía app Android

Service BLE `12345678-1234-5678-1234-567890abcdef`: `…de1` nonce (ESP32 ghi 32 byte ngẫu nhiên), `…de2` chữ ký
(app ký SHA-256(nonce) bằng khóa TEE, notify), `…de3` khóa công khai DER (ESP32 đọc lần ghép đầu), `…de4` lệnh
(notify hoặc indicate). Tủ nhận `CHARGED` / `CHARGING` / `PLUGGED` / `POWER_CONNECTED` / `1` / `ON` = đang sạc và
`UNCHARGED` / `UNPLUGGED` / `DISCHARGING` / `NOT_CHARGING` / `0` / `OFF` = rút sạc (bỏ khoảng trắng, không phân biệt hoa
thường). **App nên**: (1) gửi trạng thái sạc HIỆN TẠI ngay khi ESP32 kết nối và đăng ký notify (không chỉ lúc vừa cắm),
(2) cho phép đọc (READ) charCmd = trạng thái hiện tại, (3) gửi `UNCHARGED` khi rút sạc.
