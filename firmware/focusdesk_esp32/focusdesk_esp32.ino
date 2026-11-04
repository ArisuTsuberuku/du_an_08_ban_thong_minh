/*
 * FocusDesk — Tủ điện thoại + tưới cây thưởng (ESP32 / ESP32-S3 / ESP32-C3)       GIAO THỨC v2 — xem firmware/GIAO_THUC.md
 * ------------------------------------------------------------------------------------------------------------------
 * Gộp từ code của nhóm học sinh (esp_code/sketch_nov2a.ino): giữ nguyên xác thực điện thoại bằng chữ ký TEE (BLE, ECDSA)
 * và tín hiệu "CHARGED" từ app Android; bổ sung công tắc hành trình cửa tủ, giao thức chuẩn với phần mềm FocusDesk.
 *
 * LUỒNG MỘT CA HỌC
 *   IDLE   ── cửa tủ ĐÓNG (công tắc hành trình) + điện thoại ĐANG SẠC + chữ ký TEE hợp lệ ──►  ACTIVE  (event session_started)
 *   ACTIVE ── mở cửa / rút sạc  -> event door_open / phone_out (active=true)  => FocusDesk ghi VI PHẠM + báo phụ huynh
 *          ── CFI < led_cfi  -> nháy đèn đỏ ;  CFI < buzz_cfi -> kêu còi   (CFI do FocusDesk gửi từ camera AI)
 *          ── hết session_ms -> event session_end {reason:"time_up"} ──►  DONE   (đèn xanh nháy 5 s)
 *   DONE   ── mở cửa / rút sạc -> event gửi kèm active=false => FocusDesk KHÔNG báo nữa; mở cửa -> về IDLE chờ ca mới
 *   Tưới cây: tủ KHÔNG tự bơm. Web chấm ca (đủ phút, không vi phạm, tập trung >= 2/3 thời gian) -> lệnh "water" về tủ.
 *
 * NỐI DÂY (mặc định; đổi ở khối PIN bên dưới)
 *   GPIO4  rơ-le bơm (HIGH = chạy)      GPIO7 LED đỏ (mất tập trung)    GPIO8 LED xanh (bơm / hết giờ)    GPIO9 còi
 *   GPIO5  công tắc hành trình cửa tủ: chân COM -> GND, chân NO -> GPIO5 (INPUT_PULLUP). Cửa đóng đè công tắc = LOW.
 *   GPIO6  (tùy chọn) cảm biến sạc: LOW = đang sạc. Đặt PIN_CHARGE = -1 nếu chỉ dùng tín hiệu "CHARGED" qua BLE.
 *
 * Thư viện: ArduinoJson 7.x · ESP32 Arduino core 3.x (BLE Bluedroid, Preferences, mbedtls có sẵn).
 */
#include <Arduino.h>
#include <ArduinoJson.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <Preferences.h>
#include "mbedtls/pk.h"
#include "mbedtls/md.h"

#define FW_VERSION   "2.7.0"
#define PROTO        2

// ---------------------------------------------------------------- PIN
#define RELAY_PIN       4
#define LED_RED_PIN     7
#define LED_GREEN_PIN   8
#define BUZZER_PIN      9
#define PIN_DOOR        5     // công tắc hành trình (LOW = cửa đóng)
#define PIN_CHARGE     -1     // -1 = không dùng; >= 0: LOW = đang sạc
// Kiểu kích rơ-le (đổi được bằng lệnh cfg {"relay":"high|low|low_od"}, lưu vào bộ nhớ):
//   high   : IN = HIGH thì bơm chạy (module kích mức cao)
//   low    : IN = LOW  thì bơm chạy (đa số module rơ-le 5V bán sẵn — "kích mức thấp")
//   low_od : như low nhưng lúc TẮT thả chân lơ lửng (INPUT) — dùng khi module cấp 5V mà mức 3,3 V của ESP32
//            không đủ tắt rơ-le (triệu chứng: cắm vào là bơm chạy luôn, lệnh water không đổi gì)
enum RelayMode { RELAY_HIGH = 0, RELAY_LOW = 1, RELAY_LOW_OD = 2 };
int relayMode = RELAY_LOW;
int relayPin = RELAY_PIN;                   // đổi được bằng cfg {"relay_pin":n} nếu nối rơ-le vào chân khác GPIO4
// Chân an toàn trên ESP32-S3 (bỏ chân strapping 0/3/45/46, USB 19/20, UART0 43/44 nối CH343, flash/PSRAM 26–37,
// và các chân đã dùng: cửa 5, LED 7/8, còi 9)
bool relayPinOk(int p) {
  const int ok[] = {1, 2, 4, 6, 10, 11, 12, 13, 14, 15, 16, 17, 18, 21, 38, 39, 40, 41, 42, 47, 48};
  for (int x : ok) if (x == p) return true;
  return false;
}
const char* relayName() { return relayMode == RELAY_HIGH ? "high" : relayMode == RELAY_LOW_OD ? "low_od" : "low"; }
void pumpSet(bool on) {
  if (relayMode == RELAY_HIGH) { pinMode(relayPin, OUTPUT); digitalWrite(relayPin, on ? HIGH : LOW); }
  else if (relayMode == RELAY_LOW) { pinMode(relayPin, OUTPUT); digitalWrite(relayPin, on ? LOW : HIGH); }
  else if (on) { pinMode(relayPin, OUTPUT); digitalWrite(relayPin, LOW); }
  else pinMode(relayPin, INPUT);                    // thả nổi: điện trở kéo lên 5V trên module tự tắt rơ-le
}
#define REQUIRE_TEE     1     // 1 = bắt buộc chữ ký TEE của điện thoại đã ghép mới bắt đầu ca

#define PUMP_MAX_MS    10000UL
#define CFI_STALE_MS   10000UL  // quá 10 s không nhận CFI -> coi như không có camera (tắt đèn/còi)
#define DEBOUNCE_MS       60UL
#define PAIR_WINDOW_MS  120000UL  // sau reset_key: 2 phút để điện thoại mới ghép (chống điện thoại lạ ghép trộm)
#define PAIR_BOOT_MS    300000UL  // tủ mới tinh (chưa có khóa): mở ghép 5 phút sau khi cấp điện

// ---------------------------------------------------------------- BLE UUID (giữ như app Android của nhóm)
static BLEUUID serviceUUID("12345678-1234-5678-1234-567890abcdef");
static BLEUUID charNonceUUID("12345678-1234-5678-1234-567890abcde1");
static BLEUUID charSignUUID("12345678-1234-5678-1234-567890abcde2");
static BLEUUID charPubKeyUUID("12345678-1234-5678-1234-567890abcde3");
static BLEUUID charCmdUUID("12345678-1234-5678-1234-567890abcde4");

Preferences prefs;
uint8_t savedPubKey[256];
size_t savedPubKeyLen = 0;
bool hasPubKey = false;
uint8_t currentNonce[32];
char boxId[9] = "0000";

// ---------------------------------------------------------------- trạng thái
enum Phase { IDLE, ACTIVE, DONE, BREAK };   // BREAK = nghỉ giữa ca (phụ huynh bấm "Bắt đầu ca mới sau N phút nghỉ")
Phase phase = IDLE;
const char* phaseName() { return phase == ACTIVE ? "active" : phase == DONE ? "done" : phase == BREAK ? "break" : "idle"; }
unsigned long breakUntil = 0;

volatile bool doConnect = false;
volatile bool bleConnected = false;
volatile bool signatureVerified = false;
volatile bool bleCharging = false;
volatile bool bleCmdNew = false, bleLinkNew = false, bleLostNew = false;   // cờ từ tác vụ BLE -> loop() gửi JSON
char bleCmdRaw[33] = "";                    // giá trị thô app gửi qua charCmd (gỡ lỗi: event ble_cmd)
bool simCharge = false;                     // lệnh "charge" (THỬ NGHIỆM): giả lập điện thoại đang sạc
uint8_t cmdProps = 0;
unsigned long pairUntil = 0;                // còn trong cửa sổ ghép khi millis() < pairUntil
char keyFp[7] = "";                         // MÃ GHÉP 6 số của điện thoại đã ghép (giống app hiển thị): SHA-256(khóa DER)
char expectFp[7] = "";                      // phụ huynh nhập mã 6 số trên web -> tủ chỉ nhận đúng điện thoại có mã này
char gotFp[7] = "";
bool pairMismatchNew = false;
volatile bool verifyFailNew = false;        // chữ ký sai -> báo PC (điện thoại có khóa khác khóa đã ghép)
bool pairRefusedNew = false;
bool studyKnown = false, pcStudy = false;   // FocusDesk báo học sinh đang ở CHẾ ĐỘ HỌC SINH (trường "study" trong ping)
unsigned long pcStudyAt = 0;
// Bắt buộc FocusDesk đang chạy và báo chế độ Học sinh (ping 2 s/lần, mất > 10 s coi như không học)
bool studyOk() { return studyKnown && pcStudy && millis() - pcStudyAt < 10000UL; }                       // bit0 notify, bit1 indicate, bit2 read của charCmd
bool doorClosed = false, charging = false;
bool doorRaw = false; unsigned long doorChangedAt = 0;

unsigned long sessionMs = 25UL * 60000UL;   // phụ huynh đặt trên web -> FocusDesk gửi "cfg"
int ledCfi = 50, buzzCfi = 30;
int currentCfi = -1;                        // -1 = chưa có / camera tắt
unsigned long cfiAt = 0;
unsigned long sessionStart = 0, sessionLen = 0;
int sessionViol = 0;
bool testSession = false;                   // ca THỬ NGHIỆM (lệnh start): bỏ qua điều kiện điện thoại, PC không chấm điểm
unsigned long ioUntil = 0, ioStart = 0;     // lệnh io: thử lần lượt đèn đỏ -> đèn xanh -> còi
unsigned long pumpOffAt = 0, greenUntil = 0, alarmUntil = 0;
unsigned long lastRed = 0, lastGreen = 0, lastBuzz = 0, lastScan = 0;

BLEClient* pClient = nullptr;
BLEAdvertisedDevice* targetDevice = nullptr;
BLERemoteCharacteristic *pNonceChar = nullptr, *pSignChar = nullptr, *pPubKeyChar = nullptr, *pCmdChar = nullptr;

// ================================================================ gửi JSON cho máy tính (1 dòng / tin)
void sendDoc(JsonDocument& d) { serializeJson(d, Serial); Serial.println(); }

bool pairOpen() { return pairUntil && (long)(millis() - pairUntil) < 0; }

// Mã ghép 6 số = (4 byte đầu SHA-256 của khóa công khai, số nguyên không dấu) mod 1 000 000 — app tính giống hệt
void fpOf(const uint8_t* key, size_t len, char* out) {
  uint8_t h[32];
  mbedtls_md_context_t md; mbedtls_md_init(&md);
  mbedtls_md_setup(&md, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 0);
  mbedtls_md_starts(&md); mbedtls_md_update(&md, key, len); mbedtls_md_finish(&md, h); mbedtls_md_free(&md);
  uint32_t v = ((uint32_t)h[0] << 24) | ((uint32_t)h[1] << 16) | ((uint32_t)h[2] << 8) | h[3];
  snprintf(out, 7, "%06lu", (unsigned long)(v % 1000000UL));
}

void computeFp() {
  if (!hasPubKey) { keyFp[0] = 0; return; }
  fpOf(savedPubKey, savedPubKeyLen, keyFp);
}

unsigned long leftS() {
  if (phase != ACTIVE) return 0;
  unsigned long el = millis() - sessionStart;
  return el >= sessionLen ? 0 : (sessionLen - el) / 1000UL;
}

void fillState(JsonDocument& d) {
  d["proto"] = PROTO;
  d["phase"] = phaseName();
  d["active"] = phase == ACTIVE;
  d["door"] = doorClosed ? "closed" : "open";
  d["lid"] = doorClosed ? "closed" : "open";     // tên cũ (v1) cho phần mềm đời trước
  d["phone"] = charging;                          // điện thoại đang cắm sạc trong tủ
  d["ble"] = (bool)bleConnected;
  d["verified"] = (bool)signatureVerified;
  d["paired"] = hasPubKey;
  d["lock"] = "none";                             // v2 không còn khóa cơ khí
  d["session_s"] = (phase == ACTIVE ? sessionLen : sessionMs) / 1000UL;   // trong ca: thời lượng ca đang chạy
  d["left_s"] = leftS();
  d["viol"] = sessionViol;
  d["cfi"] = currentCfi;
  d["pump"] = pumpOffAt != 0;
  d["relay"] = relayName();
  d["relay_pin"] = relayPin;
  d["break_left"] = phase == BREAK ? (long)(breakUntil - millis()) > 0 ? (breakUntil - millis() + 999) / 1000UL : 0 : 0;
  d["test"] = testSession;
  d["study"] = studyOk();
  d["sim_charge"] = simCharge;
  d["key_fp"] = keyFp;
  d["pair_left"] = pairOpen() ? (pairUntil - millis()) / 1000UL : 0;
  if (pairOpen() && expectFp[0]) d["pair_expect"] = expectFp;
}

void sendHello() {
  JsonDocument d;
  d["t"] = "hello"; d["dev"] = "focusdesk-box"; d["fw"] = FW_VERSION; d["box_id"] = boxId;
  d["nonce"] = String(esp_random(), HEX);
  fillState(d); sendDoc(d);
}

void sendState() { JsonDocument d; d["t"] = "state"; fillState(d); sendDoc(d); }

void sendEvent(const char* e, const char* reason = nullptr) {
  JsonDocument d;
  d["t"] = "event"; d["e"] = e;
  if (reason) d["reason"] = reason;
  if (strcmp(e, "session_end") == 0) { d["dur_s"] = (millis() - sessionStart) / 1000UL; }
  fillState(d); sendDoc(d);
}

void sendAck(int id, bool ok, const char* err = nullptr) {
  if (id < 0) return;                              // lệnh không có id (gõ tay / bản cũ) -> không ack
  JsonDocument d;
  d["t"] = "ack"; d["id"] = id; d["ok"] = ok;
  if (err) d["err"] = err;
  sendDoc(d);
}

// ================================================================ ca học
void startSession(bool test = false, unsigned long lenMs = 0) {
  phase = ACTIVE; sessionStart = millis(); sessionLen = lenMs ? lenMs : sessionMs; sessionViol = 0; testSession = test;
  sendEvent("session_started");
}

void endSession(const char* reason) {
  sendEvent("session_end", reason);
  phase = doorClosed ? DONE : IDLE;                 // cửa đã mở sẵn -> sẵn sàng ca mới luôn
  signatureVerified = false;                        // ca sau phải xác thực lại điện thoại
  testSession = false;
  greenUntil = millis() + 5000; alarmUntil = 0;
  digitalWrite(LED_RED_PIN, LOW); digitalWrite(BUZZER_PIN, LOW);
}

void violation(const char* e) {
  sessionViol++; alarmUntil = millis() + 1500;     // còi + đèn đỏ 1,5 s
  sendEvent(e);
}

void startPump(unsigned long ms) {
  ms = constrain(ms, 300UL, PUMP_MAX_MS);
  pumpSet(true);
  pumpOffAt = millis() + ms; greenUntil = pumpOffAt;
  sendEvent("watering");
}

// ================================================================ BLE + chữ ký TEE (giữ từ code của nhóm)
bool verifyECDSASignature(const uint8_t* pub, size_t pubLen, const uint8_t* nonce, size_t nLen, const uint8_t* sig, size_t sLen) {
  uint8_t hash[32];
  mbedtls_md_context_t md; mbedtls_md_init(&md);
  if (mbedtls_md_setup(&md, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 0) != 0) { mbedtls_md_free(&md); return false; }
  mbedtls_md_starts(&md); mbedtls_md_update(&md, nonce, nLen); mbedtls_md_finish(&md, hash); mbedtls_md_free(&md);
  mbedtls_pk_context pk; mbedtls_pk_init(&pk);
  if (mbedtls_pk_parse_public_key(&pk, pub, pubLen) != 0) { mbedtls_pk_free(&pk); return false; }
  int ret = mbedtls_pk_verify(&pk, MBEDTLS_MD_SHA256, hash, sizeof(hash), sig, sLen);
  mbedtls_pk_free(&pk);
  return ret == 0;
}

unsigned long lastVerifyReq = 0;
int verifyFails = 0;
void requestVerify() {                              // gửi nonce mới -> app ký bằng khóa TEE -> notify charSign
  if (!bleConnected || !pNonceChar || !hasPubKey) return;
  lastVerifyReq = millis();
  esp_fill_random(currentNonce, sizeof(currentNonce));
  pNonceChar->writeValue(currentNonce, sizeof(currentNonce));
}

// Chuẩn hóa lệnh app: bỏ khoảng trắng / \r\n / \0, không phân biệt hoa thường; nhận nhiều cách viết
void handleBleCmd(const uint8_t* data, size_t len) {
  size_t n = 0;
  for (size_t i = 0; i < len && n < sizeof(bleCmdRaw) - 1; i++)
    if (data[i] >= 32 && data[i] < 127) bleCmdRaw[n++] = (char)data[i];
  bleCmdRaw[n] = 0;
  String c = String(bleCmdRaw); c.trim(); c.toUpperCase();
  if (c == "CHARGED" || c == "CHARGING" || c == "PLUGGED" || c == "POWER_CONNECTED" || c == "AC" || c == "USB" ||
      c == "1" || c == "ON") bleCharging = true;
  else if (c == "UNCHARGED" || c == "UNPLUGGED" || c == "DISCHARGING" || c == "NOT_CHARGING" ||
           c == "POWER_DISCONNECTED" || c == "0" || c == "OFF") bleCharging = false;
  bleCmdNew = true;
}

static void notifyCallback(BLERemoteCharacteristic* ch, uint8_t* data, size_t len, bool) {
  if (ch->getUUID().equals(charSignUUID)) {
    if (hasPubKey && len > 0) {
      if (verifyECDSASignature(savedPubKey, savedPubKeyLen, currentNonce, sizeof(currentNonce), data, len)) {
        signatureVerified = true; verifyFails = 0;
      } else { verifyFailNew = true; verifyFails++; }
    }
  } else if (ch->getUUID().equals(charCmdUUID)) {
    handleBleCmd(data, len);
  }
}

class ClientCB : public BLEClientCallbacks {
  void onConnect(BLEClient*) override {}
  void onDisconnect(BLEClient*) override {
    bleConnected = false; signatureVerified = false; bleLostNew = true;
    if (phase != ACTIVE) bleCharging = false;       // trong ca: rớt BLE không tính là rút sạc (cửa tủ mới là căn cứ chính)
  }
};

class AdvCB : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice dev) override {
    if (dev.haveServiceUUID() && dev.isAdvertisingService(serviceUUID)) {
      BLEDevice::getScan()->stop();
      if (targetDevice) delete targetDevice;
      targetDevice = new BLEAdvertisedDevice(dev);
      doConnect = true;
    }
  }
};

bool connectToPhone() {
  if (!pClient) { pClient = BLEDevice::createClient(); pClient->setClientCallbacks(new ClientCB()); }
  // connect() mặc định chờ VÔ HẠN nếu điện thoại không trả lời -> treo loop (lỗi "tìm thấy tủ mà chờ mãi"). Giới hạn 6 s.
  if (!pClient->connectTimeout(targetDevice, 6000)) return false;
  BLERemoteService* svc = pClient->getService(serviceUUID);
  if (!svc) { pClient->disconnect(); return false; }
  pNonceChar = svc->getCharacteristic(charNonceUUID);
  pSignChar = svc->getCharacteristic(charSignUUID);
  pPubKeyChar = svc->getCharacteristic(charPubKeyUUID);
  pCmdChar = svc->getCharacteristic(charCmdUUID);
  if (pSignChar) {
    if (pSignChar->canNotify()) pSignChar->registerForNotify(notifyCallback);
    else if (pSignChar->canIndicate()) pSignChar->registerForNotify(notifyCallback, false);
  }
  cmdProps = 0;
  if (pCmdChar) {
    cmdProps = (pCmdChar->canNotify() ? 1 : 0) | (pCmdChar->canIndicate() ? 2 : 0) | (pCmdChar->canRead() ? 4 : 0);
    if (pCmdChar->canNotify()) pCmdChar->registerForNotify(notifyCallback);
    else if (pCmdChar->canIndicate()) pCmdChar->registerForNotify(notifyCallback, false);   // app dùng indicate
  }
  if (!hasPubKey && pPubKeyChar && !pairOpen()) pairRefusedNew = true;   // hết giờ ghép -> không nhận khóa lạ
  if (!hasPubKey && pPubKeyChar && pairOpen()) {    // trong cửa sổ ghép: lưu khóa của điện thoại ĐẦU TIÊN kết nối
    String v = pPubKeyChar->readValue();
    if (v.length() > 50 && v.length() <= sizeof(savedPubKey)) fpOf((const uint8_t*)v.c_str(), v.length(), gotFp);
    if (v.length() > 50 && v.length() <= sizeof(savedPubKey) && expectFp[0] && strcmp(gotFp, expectFp) != 0) {
      pairMismatchNew = true;                       // không đúng điện thoại phụ huynh chọn -> không ghép, chờ máy khác
    } else if (v.length() > 50 && v.length() <= sizeof(savedPubKey)) {
      savedPubKeyLen = v.length();
      memcpy(savedPubKey, (const uint8_t*)v.c_str(), savedPubKeyLen);
      prefs.putBytes("pubkey", savedPubKey, savedPubKeyLen);
      hasPubKey = true; pairUntil = 0; expectFp[0] = 0; verifyFails = 0; computeFp();
      sendEvent("paired");
    }
  }
  bleConnected = true; bleLinkNew = true;
  // KHÔNG đọc charCmd ở đây: app hiện tại (Code APK) khai báo READ nhưng không trả lời yêu cầu đọc -> readValue()
  // chặn loop() tới ~30 s (mất ping với máy tính). App bản mới tự gửi trạng thái sạc ngay sau khi ký (xem GIAO_THUC.md).
  requestVerify();
  return true;
}

void resetKey() {
  prefs.remove("pubkey");
  hasPubKey = false; savedPubKeyLen = 0; signatureVerified = false; keyFp[0] = 0;
  pairUntil = millis() + PAIR_WINDOW_MS;            // 2 phút cho điện thoại mới bấm "Liên kết lại tủ"
  if (pClient && pClient->isConnected()) pClient->disconnect();
  sendEvent("key_reset");
}

// ================================================================ lệnh từ máy tính
void handleLine(const String& line) {
  if (!line.startsWith("{")) return;
  JsonDocument m;
  if (deserializeJson(m, line)) return;
  String t = m["t"] | "";
  int id = m["id"] | -1;

  if (t == "hello") { sendHello(); return; }
  if (t == "ping" || t == "get") {
    if (m["cfi"].is<int>()) { currentCfi = m["cfi"]; cfiAt = millis(); }   // -1 = camera tắt / không trong giờ học
    if (m["study"].is<bool>()) { studyKnown = true; pcStudy = m["study"]; pcStudyAt = millis(); }
    sendState(); return;
  }
  if (t == "cfi") {                                 // tên cũ của nhóm: {"t":"cfi","score":72}
    currentCfi = m["score"] | -1; cfiAt = millis(); sendAck(id, true); return;
  }
  if (t == "cfg" || t == "set_time") {
    unsigned long ms = m["session_ms"] | (unsigned long)(m["ms"] | 0UL);
    if (m["session_min"].is<int>()) ms = (unsigned long)(int)m["session_min"] * 60000UL;
    if (ms) sessionMs = constrain(ms, 60000UL, 240UL * 60000UL);     // 1 – 240 phút; áp dụng từ ca SAU
    if (m["led_cfi"].is<int>()) ledCfi = constrain((int)m["led_cfi"], 0, 100);
    if (m["buzz_cfi"].is<int>()) buzzCfi = constrain((int)m["buzz_cfi"], 0, 100);
    if (m["relay"].is<const char*>()) {
      String r = m["relay"].as<const char*>();
      relayMode = r == "high" ? RELAY_HIGH : r == "low_od" ? RELAY_LOW_OD : RELAY_LOW;
      prefs.putInt("relay", relayMode);
      if (!pumpOffAt) pumpSet(false);               // áp ngay kiểu mới -> bơm phải TẮT
    }
    if (m["relay_pin"].is<int>()) {
      int p = m["relay_pin"];
      if (!relayPinOk(p)) { sendAck(id, false, "bad_pin"); return; }
      if (p != relayPin) { pinMode(relayPin, INPUT); relayPin = p; prefs.putInt("rpin", relayPin); }
      if (!pumpOffAt) pumpSet(false);
    }
    prefs.putULong("sess_ms", sessionMs); prefs.putInt("led", ledCfi); prefs.putInt("buzz", buzzCfi);
    sendAck(id, true); sendState(); return;
  }
  if (t == "water") { startPump(m["ms"] | 2500UL); sendAck(id, true); return; }
  if (t == "beep") {
    alarmUntil = millis() + constrain((unsigned long)(m["ms"] | 400UL), 50UL, 3000UL);
    sendAck(id, true); return;
  }
  if (t == "reset_key") {
    if (phase == ACTIVE) { sendAck(id, false, "busy"); return; }   // đang trong ca -> không cho đổi điện thoại
    const char* ex = m["expect"] | "";
    expectFp[0] = 0;
    if (strlen(ex) == 6) { strncpy(expectFp, ex, 6); expectFp[6] = 0; }
    sendAck(id, true); resetKey(); return;
  }
  if (t == "start") {                               // CHỈ ĐỂ THỬ NGHIỆM: vào ca ngay, không cần cửa / sạc / chữ ký
    if (phase == ACTIVE) { sendAck(id, false, "busy"); return; }
    unsigned long ms = m["session_ms"] | 0UL;
    if (ms) ms = constrain(ms, 10000UL, 240UL * 60000UL);
    phase = IDLE; sendAck(id, true); startSession(true, ms); return;
  }
  if (t == "charge") {                              // THỬ NGHIỆM: giả lập cắm / rút sạc khi app chưa gửi được CHARGED
    simCharge = m["on"] | true; sendAck(id, true); return;
  }
  if (t == "io") {                                  // thử dây: đỏ 0,6 s -> xanh 0,6 s -> còi 0,3 s (không bật bơm)
    ioStart = millis(); ioUntil = ioStart + 1500; sendAck(id, true); return;
  }
  if (t == "next") {                                // sau ca: nghỉ break_ms rồi về chờ ca mới (không cần mở/đóng cửa)
    if (phase == ACTIVE) { sendAck(id, false, "busy"); return; }
    unsigned long ms = m["break_ms"] | 0UL;
    ms = constrain(ms, 0UL, 60UL * 60000UL);
    sendAck(id, true);
    if (ms == 0) { phase = IDLE; sendEvent("break_end"); requestVerify(); }
    else { phase = BREAK; breakUntil = millis() + ms; sendEvent("break_started"); }
    return;
  }
  if (t == "end") {
    if (phase != ACTIVE) { sendAck(id, false, "no_session"); return; }
    sendAck(id, true); endSession(m["reason"] | "pc"); return;
  }
  sendAck(id, false, "unknown");
}

void readSerial() {
  static String buf;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') { buf.trim(); handleLine(buf); buf = ""; }
    else if (c != '\r' && buf.length() < 512) buf += c;
  }
}

// ================================================================ cảm biến
void readSensors() {
  bool raw = digitalRead(PIN_DOOR) == LOW;
  if (raw != doorRaw) { doorRaw = raw; doorChangedAt = millis(); }
  if (doorRaw != doorClosed && millis() - doorChangedAt > DEBOUNCE_MS) {
    doorClosed = doorRaw;
    if (!doorClosed && phase == ACTIVE) violation("door_open");
    else sendEvent(doorClosed ? "door_close" : "door_open");      // ngoài ca: active=false -> PC không báo
    if (!doorClosed && phase == DONE) phase = IDLE;                 // lấy điện thoại sau ca -> sẵn sàng ca mới
    if (doorClosed && phase == IDLE && !signatureVerified) requestVerify();
  }
  bool ch = bleCharging || simCharge;
#if PIN_CHARGE >= 0
  ch = ch || digitalRead(PIN_CHARGE) == LOW;
#endif
  if (ch != charging) {
    charging = ch;
    if (!charging && phase == ACTIVE) violation("phone_out");
    else sendEvent(charging ? "phone_in" : "phone_out");
  }
}

// ================================================================ setup / loop
void setup() {
  Serial.begin(115200);
  delay(300);
  pumpSet(false);                                   // tắt bơm NGAY khi cấp điện (theo kiểu rơ-le mặc định)
  pinMode(LED_RED_PIN, OUTPUT); pinMode(LED_GREEN_PIN, OUTPUT); pinMode(BUZZER_PIN, OUTPUT);
  pinMode(PIN_DOOR, INPUT_PULLUP);
#if PIN_CHARGE >= 0
  pinMode(PIN_CHARGE, INPUT_PULLUP);
#endif
  uint64_t mac = ESP.getEfuseMac();
  snprintf(boxId, sizeof(boxId), "%04X", (unsigned)((mac >> 32) & 0xFFFF));

  prefs.begin("focusdesk", false);
  savedPubKeyLen = prefs.getBytesLength("pubkey");
  if (savedPubKeyLen > 0 && savedPubKeyLen <= sizeof(savedPubKey)) { prefs.getBytes("pubkey", savedPubKey, savedPubKeyLen); hasPubKey = true; }
  computeFp();
  if (!hasPubKey) pairUntil = millis() + PAIR_BOOT_MS;
  sessionMs = prefs.getULong("sess_ms", sessionMs);
  ledCfi = prefs.getInt("led", ledCfi); buzzCfi = prefs.getInt("buzz", buzzCfi);
  relayMode = prefs.getInt("relay", relayMode);
  relayPin = prefs.getInt("rpin", relayPin);
  if (!relayPinOk(relayPin)) relayPin = RELAY_PIN;
  pumpSet(false);
  doorClosed = doorRaw = digitalRead(PIN_DOOR) == LOW;

  BLEDevice::init("FocusDesk_ESP32");
  BLEDevice::setMTU(185);                           // chữ ký DER ~72 byte, khóa công khai 91 byte -> cần MTU > 23
  BLEScan* scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new AdvCB());
  scan->setInterval(100); scan->setWindow(99); scan->setActiveScan(true);   // quét ~100% thời gian -> tìm điện thoại nhanh
  scan->start(5, nullptr, false);                    // không chặn (bản gốc chặn 5 s lúc khởi động)
  sendHello();                                      // PC đang mở cổng sẽ nhận ngay
}

void loop() {
  readSerial();
  readSensors();
  unsigned long now = millis();

  if (doConnect) { doConnect = false; connectToPhone(); }
  if (bleLinkNew) {
    bleLinkNew = false;
    JsonDocument d; d["t"] = "event"; d["e"] = "ble_connected"; d["has_sign"] = pSignChar != nullptr; d["has_cmd"] = pCmdChar != nullptr;
    d["cmd_notify"] = (cmdProps & 1) != 0; d["cmd_indicate"] = (cmdProps & 2) != 0; d["cmd_read"] = (cmdProps & 4) != 0;
    fillState(d); sendDoc(d);
  }
  if (bleLostNew) { bleLostNew = false; sendEvent("ble_lost"); }
  if (verifyFailNew) { verifyFailNew = false; sendEvent("verify_failed"); }
  if (pairRefusedNew) { pairRefusedNew = false; sendEvent("pair_refused"); }
  if (pairMismatchNew) {
    pairMismatchNew = false;
    JsonDocument d; d["t"] = "event"; d["e"] = "pair_mismatch"; d["got"] = gotFp; d["expect"] = expectFp; fillState(d); sendDoc(d);
    if (pClient && pClient->isConnected()) pClient->disconnect();   // nhả để điện thoại đúng kết nối
  }
  if (bleCmdNew) {                                   // gỡ lỗi: hiện đúng chuỗi app gửi (Serial monitor)
    bleCmdNew = false;
    JsonDocument d; d["t"] = "event"; d["e"] = "ble_cmd"; d["v"] = bleCmdRaw; fillState(d); sendDoc(d);
  }
  if (!bleConnected && !doConnect && now - lastScan > 3500) {   // quét KHÔNG chặn 3 s / 3,5 s khi chưa nối điện thoại
    BLEScan* sc = BLEDevice::getScan(); sc->clearResults(); sc->start(3, nullptr, false); lastScan = now;
  }
  // Xác minh lại tự động: đã nối mà chưa ký được (mất gói, app vừa mở...) -> gửi nonce mới mỗi 4 s
  // (chữ ký sai liên tục > 3 lần -> chậm lại 30 s/lần, chờ phụ huynh ghép lại)
  if (bleConnected && hasPubKey && !signatureVerified && phase != ACTIVE &&
      now - lastVerifyReq > (verifyFails > 3 ? 30000UL : 4000UL)) requestVerify();

  // 0. Hết giờ nghỉ giữa ca -> kêu ngắn báo học sinh, về chờ ca mới (đủ điều kiện là vào ca luôn)
  if (phase == BREAK && (long)(millis() - breakUntil) >= 0) {
    phase = IDLE; alarmUntil = millis() + 400; sendEvent("break_end"); requestVerify();
  }
  // 1. Bắt đầu ca: học sinh đang ở CHẾ ĐỘ HỌC SINH trên máy + cửa đóng + đang sạc + điện thoại ký đúng
  if (phase == IDLE && doorClosed && charging && (signatureVerified || !REQUIRE_TEE) && studyOk()) startSession();
  // 2. Hết giờ học — dùng millis() mới: sessionStart vừa đặt ở bước 1 có thể LỚN HƠN `now` (lỗi 2.4.0: tràn số
  //    unsigned -> ca vừa bắt đầu đã báo hết giờ)
  if (phase == ACTIVE && millis() - sessionStart >= sessionLen) endSession("time_up");

  // 3. Bơm tự tắt
  if (pumpOffAt && (long)(now - pumpOffAt) >= 0) { pumpSet(false); pumpOffAt = 0; sendEvent("watered"); }

  // 4. Đèn xanh: nháy khi hết giờ / đang bơm
  if (greenUntil && (long)(now - greenUntil) < 0) {
    if (now - lastGreen > 200) { digitalWrite(LED_GREEN_PIN, !digitalRead(LED_GREEN_PIN)); lastGreen = now; }
  } else { digitalWrite(LED_GREEN_PIN, LOW); greenUntil = 0; }

  // 4b. Lệnh io (thử dây) — ưu tiên hơn mọi hiệu ứng khác
  if (ioUntil) {
    if ((long)(now - ioUntil) >= 0) { ioUntil = 0; digitalWrite(LED_RED_PIN, LOW); digitalWrite(LED_GREEN_PIN, LOW); digitalWrite(BUZZER_PIN, LOW); }
    else {
      unsigned long e = now - ioStart;
      digitalWrite(LED_RED_PIN, e < 600); digitalWrite(LED_GREEN_PIN, e >= 600 && e < 1200); digitalWrite(BUZZER_PIN, e >= 1200);
    }
    return;
  }

  // 5. Đèn đỏ + còi: chỉ trong ca; theo CFI (camera AI trên PC) hoặc khi vừa vi phạm / lệnh beep
  bool alarm = alarmUntil && (long)(now - alarmUntil) < 0;
  if (!alarm) alarmUntil = 0;
  bool cfiOk = currentCfi >= 0 && now - cfiAt < CFI_STALE_MS;
  bool red = phase == ACTIVE && cfiOk && currentCfi < ledCfi;
  bool buzz = phase == ACTIVE && cfiOk && currentCfi < buzzCfi;
  if (alarm) { digitalWrite(LED_RED_PIN, HIGH); digitalWrite(BUZZER_PIN, (now / 150) % 2); }
  else {
    if (red) { if (now - lastRed > 500) { digitalWrite(LED_RED_PIN, !digitalRead(LED_RED_PIN)); lastRed = now; } }
    else digitalWrite(LED_RED_PIN, LOW);
    if (buzz) { if (now - lastBuzz > 300) { digitalWrite(BUZZER_PIN, !digitalRead(BUZZER_PIN)); lastBuzz = now; } }
    else digitalWrite(BUZZER_PIN, LOW);
  }
}
