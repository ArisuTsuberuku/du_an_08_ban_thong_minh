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

#define FW_VERSION   "2.1.0"
#define PROTO        2

// ---------------------------------------------------------------- PIN
#define RELAY_PIN       4
#define LED_RED_PIN     7
#define LED_GREEN_PIN   8
#define BUZZER_PIN      9
#define PIN_DOOR        5     // công tắc hành trình (LOW = cửa đóng)
#define PIN_CHARGE     -1     // -1 = không dùng; >= 0: LOW = đang sạc
#define BOM_CHAY     HIGH
#define BOM_TAT      LOW
#define REQUIRE_TEE     1     // 1 = bắt buộc chữ ký TEE của điện thoại đã ghép mới bắt đầu ca

#define PUMP_MAX_MS    10000UL
#define CFI_STALE_MS   10000UL  // quá 10 s không nhận CFI -> coi như không có camera (tắt đèn/còi)
#define DEBOUNCE_MS       60UL

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
enum Phase { IDLE, ACTIVE, DONE };
Phase phase = IDLE;
const char* phaseName() { return phase == ACTIVE ? "active" : phase == DONE ? "done" : "idle"; }

volatile bool doConnect = false;
volatile bool bleConnected = false;
volatile bool signatureVerified = false;
volatile bool bleCharging = false;
bool doorClosed = false, charging = false;
bool doorRaw = false; unsigned long doorChangedAt = 0;

unsigned long sessionMs = 25UL * 60000UL;   // phụ huynh đặt trên web -> FocusDesk gửi "cfg"
int ledCfi = 50, buzzCfi = 30;
int currentCfi = -1;                        // -1 = chưa có / camera tắt
unsigned long cfiAt = 0;
unsigned long sessionStart = 0, sessionLen = 0;
int sessionViol = 0;
unsigned long pumpOffAt = 0, greenUntil = 0, alarmUntil = 0;
unsigned long lastRed = 0, lastGreen = 0, lastBuzz = 0, lastScan = 0;

BLEClient* pClient = nullptr;
BLEAdvertisedDevice* targetDevice = nullptr;
BLERemoteCharacteristic *pNonceChar = nullptr, *pSignChar = nullptr, *pPubKeyChar = nullptr, *pCmdChar = nullptr;

// ================================================================ gửi JSON cho máy tính (1 dòng / tin)
void sendDoc(JsonDocument& d) { serializeJson(d, Serial); Serial.println(); }

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
  d["session_s"] = sessionMs / 1000UL;
  d["left_s"] = leftS();
  d["viol"] = sessionViol;
  d["cfi"] = currentCfi;
  d["pump"] = pumpOffAt != 0;
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
void startSession() {
  phase = ACTIVE; sessionStart = millis(); sessionLen = sessionMs; sessionViol = 0;
  sendEvent("session_started");
}

void endSession(const char* reason) {
  sendEvent("session_end", reason);
  phase = DONE; signatureVerified = false;          // ca sau phải xác thực lại điện thoại
  greenUntil = millis() + 5000; alarmUntil = 0;
  digitalWrite(LED_RED_PIN, LOW); digitalWrite(BUZZER_PIN, LOW);
}

void violation(const char* e) {
  sessionViol++; alarmUntil = millis() + 1500;     // còi + đèn đỏ 1,5 s
  sendEvent(e);
}

void startPump(unsigned long ms) {
  ms = constrain(ms, 300UL, PUMP_MAX_MS);
  digitalWrite(RELAY_PIN, BOM_CHAY);
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

void requestVerify() {                              // gửi nonce mới -> app ký bằng khóa TEE -> notify charSign
  if (!bleConnected || !pNonceChar || !hasPubKey) return;
  esp_fill_random(currentNonce, sizeof(currentNonce));
  pNonceChar->writeValue(currentNonce, sizeof(currentNonce));
}

static void notifyCallback(BLERemoteCharacteristic* ch, uint8_t* data, size_t len, bool) {
  if (ch->getUUID().equals(charSignUUID)) {
    if (hasPubKey && len > 0 && verifyECDSASignature(savedPubKey, savedPubKeyLen, currentNonce, sizeof(currentNonce), data, len))
      signatureVerified = true;
  } else if (ch->getUUID().equals(charCmdUUID)) {
    String c; for (size_t i = 0; i < len; i++) c += (char)data[i];
    if (c == "CHARGED") bleCharging = true;
    else if (c == "UNCHARGED" || c == "UNPLUGGED" || c == "DISCHARGING") bleCharging = false;
  }
}

class ClientCB : public BLEClientCallbacks {
  void onConnect(BLEClient*) override {}
  void onDisconnect(BLEClient*) override {
    bleConnected = false; signatureVerified = false;
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
  if (!pClient->connect(targetDevice)) return false;
  BLERemoteService* svc = pClient->getService(serviceUUID);
  if (!svc) { pClient->disconnect(); return false; }
  pNonceChar = svc->getCharacteristic(charNonceUUID);
  pSignChar = svc->getCharacteristic(charSignUUID);
  pPubKeyChar = svc->getCharacteristic(charPubKeyUUID);
  pCmdChar = svc->getCharacteristic(charCmdUUID);
  if (pSignChar && pSignChar->canNotify()) pSignChar->registerForNotify(notifyCallback);
  if (pCmdChar && pCmdChar->canNotify()) pCmdChar->registerForNotify(notifyCallback);
  if (!hasPubKey && pPubKeyChar) {                  // chưa ghép: lưu khóa công khai của điện thoại ĐẦU TIÊN kết nối
    String v = pPubKeyChar->readValue();
    if (v.length() > 50 && v.length() <= sizeof(savedPubKey)) {
      savedPubKeyLen = v.length();
      memcpy(savedPubKey, (const uint8_t*)v.c_str(), savedPubKeyLen);
      prefs.putBytes("pubkey", savedPubKey, savedPubKeyLen);
      hasPubKey = true;
      sendEvent("paired");
    }
  }
  bleConnected = true;
  requestVerify();
  return true;
}

void resetKey() {
  prefs.remove("pubkey");
  hasPubKey = false; savedPubKeyLen = 0; signatureVerified = false;
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
    sendAck(id, true); resetKey(); return;
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
  bool ch = bleCharging;
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
  pinMode(RELAY_PIN, OUTPUT); digitalWrite(RELAY_PIN, BOM_TAT);
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
  sessionMs = prefs.getULong("sess_ms", sessionMs);
  ledCfi = prefs.getInt("led", ledCfi); buzzCfi = prefs.getInt("buzz", buzzCfi);
  doorClosed = doorRaw = digitalRead(PIN_DOOR) == LOW;

  BLEDevice::init("FocusDesk_ESP32");
  BLEScan* scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new AdvCB());
  scan->setInterval(1349); scan->setWindow(449); scan->setActiveScan(true);
  scan->start(5, nullptr, false);                    // không chặn (bản gốc chặn 5 s lúc khởi động)
  sendHello();                                      // PC đang mở cổng sẽ nhận ngay
}

void loop() {
  readSerial();
  readSensors();
  unsigned long now = millis();

  if (doConnect) { doConnect = false; connectToPhone(); }
  if (!bleConnected && !doConnect && now - lastScan > 7000) {   // quét KHÔNG chặn: serial vẫn trả lời ping
    BLEScan* sc = BLEDevice::getScan(); sc->clearResults(); sc->start(4, nullptr, false); lastScan = now;
  }

  // 1. Bắt đầu ca: cửa đóng + đang sạc + (điện thoại đã ghép ký đúng)
  if (phase == IDLE && doorClosed && charging && (signatureVerified || !REQUIRE_TEE)) startSession();
  // 2. Hết giờ học
  if (phase == ACTIVE && now - sessionStart >= sessionLen) endSession("time_up");

  // 3. Bơm tự tắt
  if (pumpOffAt && (long)(now - pumpOffAt) >= 0) { digitalWrite(RELAY_PIN, BOM_TAT); pumpOffAt = 0; sendEvent("watered"); }

  // 4. Đèn xanh: nháy khi hết giờ / đang bơm
  if (greenUntil && (long)(now - greenUntil) < 0) {
    if (now - lastGreen > 200) { digitalWrite(LED_GREEN_PIN, !digitalRead(LED_GREEN_PIN)); lastGreen = now; }
  } else { digitalWrite(LED_GREEN_PIN, LOW); greenUntil = 0; }

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
