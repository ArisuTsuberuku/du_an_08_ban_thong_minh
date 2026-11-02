/*
  HOP KHOA DIEN THOAI - ESP32-S3 (firmware cho Ban Hoc Thong Minh / FocusDesk)
  ---------------------------------------------------------------------------
  Ket noi may tinh: cap USB vao cong "USB" (native USB-CDC) cua ESP32-S3, 115200 baud.
  Arduino IDE: Board "ESP32S3 Dev Module", USB CDC On Boot = Enabled. Thu vien: ArduinoJson (v7).

  Phan cung mac dinh (doi chan o phan CAU HINH):
    - Khoa:  servo SG90/MG90 (chan PIN_LOCK) hoac solenoid 12V qua MOSFET (LOCK_IS_SOLENOID = 1)
    - Nap:   cong tac tu (reed switch) + nam cham, noi GND -> LOW = nap dong
    - Dien thoai: cam bien hong ngoai / cong tac hanh trinh o day hop, noi GND -> LOW = co dien thoai
    - Nut:   nut BOOT (GPIO0): bam ngan = xin lay dien thoai; giu 5 giay (khi hop DANG MO) = che do ghep 60 giay
    - Coi:   buzzer chu dong (PIN_BUZZ); den RGB tren board (GPIO48)

  Giao thuc (moi dong 1 JSON): xem services/phone_box.py va firmware/GIAO_THUC.md
  Bao mat: lock/unlock/cfg phai co "mac" = HMAC-SHA256(key, "t|id|nonce|sec")[:16]; nonce doi sau moi lenh.
*/
#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include "mbedtls/md.h"

// ---------------- CAU HINH ----------------
#define FW_VERSION        "1.0.0"
#define PIN_LOCK          4
#define PIN_LID           5
#define PIN_PHONE         6
#define PIN_BTN           0
#define PIN_BUZZ          7
#define PIN_RGB           48
#define LOCK_IS_SOLENOID  0      // 0 = servo, 1 = solenoid (HIGH = mo chot)
#define SERVO_LOCK_DEG    0
#define SERVO_OPEN_DEG    90
#define PAIR_WINDOW_MS    60000UL
// ------------------------------------------

Preferences prefs;
String key, nonce, boxId, rx;
bool locked = false, phoneIn = false, lidClosed = true, pairMode = false;
unsigned long pairUntil = 0, relockAt = 0, lastPing = 0, btnDownAt = 0, lastSensor = 0;

void rgb(uint8_t r, uint8_t g, uint8_t b) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  rgbLedWrite(PIN_RGB, r, g, b);
#else
  neopixelWrite(PIN_RGB, r, g, b);
#endif
}

void beep(int n, int ms = 80) {
  for (int i = 0; i < n; i++) { digitalWrite(PIN_BUZZ, HIGH); delay(ms); digitalWrite(PIN_BUZZ, LOW); delay(ms); }
}

void servoWrite(int deg) {               // xung 50Hz: 0.5-2.5 ms
  uint32_t duty = map(deg, 0, 180, 1638, 8192);   // 16-bit, 20 ms
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PIN_LOCK, duty);
#else
  ledcWrite(0, duty);
#endif
}

void setLock(bool on) {
  locked = on;
#if LOCK_IS_SOLENOID
  digitalWrite(PIN_LOCK, on ? LOW : HIGH);
#else
  servoWrite(on ? SERVO_LOCK_DEG : SERVO_OPEN_DEG);
#endif
  prefs.putBool("locked", on);
  rgb(on ? 40 : 0, on ? 0 : 30, 0);
}

String newNonce() { char b[9]; snprintf(b, sizeof(b), "%08lx", (unsigned long)esp_random()); return String(b); }

String hmac16(const String& msg) {
  uint8_t out[32];
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_hmac(info, (const uint8_t*)key.c_str(), key.length(), (const uint8_t*)msg.c_str(), msg.length(), out);
  char hex[17];
  for (int i = 0; i < 8; i++) sprintf(hex + 2 * i, "%02x", out[i]);
  hex[16] = 0;
  return String(hex);
}

void send(JsonDocument& d) { serializeJson(d, Serial); Serial.print('\n'); }

void sendState(const char* t) {
  JsonDocument d;
  d["t"] = t;
  if (strcmp(t, "hello") == 0) { d["dev"] = "focusdesk-box"; d["fw"] = FW_VERSION; d["box_id"] = boxId; d["paired"] = key.length() > 0; }
  d["phone"] = phoneIn; d["lid"] = lidClosed ? "closed" : "open"; d["lock"] = locked ? "locked" : "unlocked";
  d["nonce"] = nonce;
  if (relockAt) d["unlock_left"] = (long)((relockAt - millis()) / 1000);
  send(d);
}

void event(const char* e) {
  JsonDocument d;
  d["t"] = "event"; d["e"] = e; d["phone"] = phoneIn; d["lid"] = lidClosed ? "closed" : "open";
  d["lock"] = locked ? "locked" : "unlocked"; d["nonce"] = nonce;
  send(d);
}

void ack(int id, bool ok, const char* err = nullptr) {
  JsonDocument d;
  d["t"] = "ack"; d["id"] = id; d["ok"] = ok; d["nonce"] = nonce;
  if (err) d["err"] = err;
  send(d);
}

void handle(const String& line) {
  JsonDocument m;
  if (deserializeJson(m, line)) return;                 // dong rac -> bo qua
  const char* t = m["t"] | "";
  int id = m["id"] | 0;
  if (!strcmp(t, "hello")) { sendState("hello"); return; }
  if (!strcmp(t, "ping") || !strcmp(t, "get")) { lastPing = millis(); sendState("state"); return; }
  if (!strcmp(t, "beep")) { beep(m["n"] | 2); ack(id, true); return; }
  if (!strcmp(t, "setkey")) {
    if (!pairMode || millis() > pairUntil) { ack(id, false, "not_in_pair_mode"); return; }
    key = (const char*)(m["key"] | ""); prefs.putString("key", key); pairMode = false; beep(3); ack(id, true);
    return;
  }
  if (!strcmp(t, "lock") || !strcmp(t, "unlock") || !strcmp(t, "cfg")) {
    long sec = m["sec"] | 0;
    String want = hmac16(String(t) + "|" + id + "|" + nonce + "|" + sec);
    if (key.length() == 0 || want != String((const char*)(m["mac"] | ""))) { ack(id, false, "bad_mac"); return; }
    nonce = newNonce();                                  // chong phat lai lenh cu
    if (!strcmp(t, "lock")) { setLock(true); relockAt = 0; ack(id, true); event("locked"); beep(1); }
    else if (!strcmp(t, "unlock")) {
      setLock(false); relockAt = sec > 0 ? millis() + sec * 1000UL : 0; ack(id, true); event("unlocked"); beep(2);
    } else { ack(id, true); }
    return;
  }
  ack(id, false, "unknown");
}

void readSensors() {
  bool lid = digitalRead(PIN_LID) == LOW, phone = digitalRead(PIN_PHONE) == LOW;
  if (lid != lidClosed) {
    lidClosed = lid;
    if (!lid && locked) { event("tamper"); beep(10, 60); }   // nap bi mo khi dang khoa = cay hop
    else event(lid ? "lid_close" : "lid_open");
  }
  if (phone != phoneIn) {
    phoneIn = phone;
    event(phone ? "phone_in" : (locked ? "tamper" : "phone_out"));
  }
}

void readButton() {
  bool down = digitalRead(PIN_BTN) == LOW;
  if (down && !btnDownAt) btnDownAt = millis();
  if (!down && btnDownAt) {
    unsigned long held = millis() - btnDownAt;
    btnDownAt = 0;
    if (held >= 5000 && !locked) { pairMode = true; pairUntil = millis() + PAIR_WINDOW_MS; event("pair_mode"); beep(1, 400); }
    else if (held > 50) event("button");                // xin phu huynh cho lay dien thoai
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LID, INPUT_PULLUP); pinMode(PIN_PHONE, INPUT_PULLUP); pinMode(PIN_BTN, INPUT_PULLUP);
  pinMode(PIN_BUZZ, OUTPUT);
#if LOCK_IS_SOLENOID
  pinMode(PIN_LOCK, OUTPUT);
#else
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(PIN_LOCK, 50, 16);
#else
  ledcSetup(0, 50, 16); ledcAttachPin(PIN_LOCK, 0);
#endif
#endif
  prefs.begin("smartbox", false);
  key = prefs.getString("key", "");
  uint64_t mac = ESP.getEfuseMac();
  char id[13]; snprintf(id, sizeof(id), "%04X%08X", (uint16_t)(mac >> 32), (uint32_t)mac); boxId = String(id);
  nonce = newNonce();
  setLock(prefs.getBool("locked", false));             // mat dien roi co lai van giu trang thai khoa
  lidClosed = digitalRead(PIN_LID) == LOW; phoneIn = digitalRead(PIN_PHONE) == LOW;
  beep(1);
  sendState("hello");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handle(rx); rx = ""; } else if (rx.length() < 400) rx += c;
  }
  if (millis() - lastSensor > 50) { lastSensor = millis(); readSensors(); readButton(); }
  if (relockAt && millis() > relockAt && lidClosed) { relockAt = 0; setLock(true); event("locked"); }
  if (pairMode && millis() > pairUntil) pairMode = false;
  // Mat may tinh > 60s: GIU NGUYEN trang thai (an toan), den nhap nhay vang
  if (lastPing && millis() - lastPing > 60000UL) rgb((millis() / 500) % 2 ? 40 : 0, (millis() / 500) % 2 ? 30 : 0, 0);
}
