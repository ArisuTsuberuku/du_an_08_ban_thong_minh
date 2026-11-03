# -*- coding: utf-8 -*-
"""TỦ ĐIỆN THOẠI v2 GIẢ LẬP — chạy đúng máy trạng thái + giao thức của firmware/focusdesk_esp32 để thử phần mềm khi chưa
có mạch. Thời gian ca tính bằng giây thật nhưng có thể "tua" bằng fast_forward()."""
import json
import queue
import threading
import time


class FakeCabinet:
    def __init__(self, paired: bool = True):
        self.to_pc: "queue.Queue[bytes]" = queue.Queue()
        self.phase = "idle"
        self.door_closed = False
        self.charging = False
        self.verified = False
        self.paired = paired
        self.session_ms = 25 * 60000
        self.led_cfi, self.buzz_cfi = 50, 30
        self.cfi = -1
        self.viol = 0
        self.start = 0.0
        self.offset = 0.0
        self.pumped = []          # các lần bơm (ms)
        self.cfg_seen = []
        self.test = False
        self.io_count = 0
        self.len_s = None
        self.lock = threading.RLock()

    # ---------------------------------------------------------------- tiện ích
    def now(self) -> float:
        return time.time() + self.offset

    def _send(self, msg):
        self.to_pc.put((json.dumps(msg) + "\n").encode())

    def state(self):
        left = 0
        if self.phase == "active":
            left = max(0, int((self.session_ms / 1000) - (self.now() - self.start)))
        return {"proto": 2, "phase": self.phase, "active": self.phase == "active",
                "door": "closed" if self.door_closed else "open", "lid": "closed" if self.door_closed else "open",
                "phone": self.charging, "ble": True, "verified": self.verified, "paired": self.paired, "lock": "none",
                "session_s": self.session_ms // 1000, "left_s": left, "viol": self.viol, "cfi": self.cfi, "pump": False,
                "test": self.test}

    def _event(self, e, **extra):
        self._send({"t": "event", "e": e, **extra, **self.state()})

    def _ack(self, i, ok=True, err=None):
        if i is None:
            return
        m = {"t": "ack", "id": i, "ok": ok}
        if err:
            m["err"] = err
        self._send(m)

    # ---------------------------------------------------------------- máy trạng thái
    def _check(self):
        with self.lock:
            if self.phase == "idle" and self.door_closed and self.charging and self.verified:
                self.phase, self.start, self.viol = "active", self.now(), 0
                self._event("session_started")
            if self.phase == "active" and self.now() - self.start >= (self.len_s or self.session_ms / 1000):
                self._end("time_up")

    def _end(self, reason):
        dur = int(self.now() - self.start)
        self._event("session_end", reason=reason, dur_s=dur)
        self.phase, self.verified = ("done" if self.door_closed else "idle"), False
        self.test, self.len_s = False, None

    def feed(self, line: bytes):
        m = json.loads(line)
        t, i = m.get("t"), m.get("id")
        with self.lock:
            if t == "hello":
                self._send({"t": "hello", "dev": "focusdesk-box", "fw": "sim-2.1", "box_id": "TU01", "nonce": "1",
                            **self.state()})
            elif t in ("ping", "get"):
                if isinstance(m.get("cfi"), int):
                    self.cfi = m["cfi"]
                self._send({"t": "state", **self.state()})
            elif t == "cfg":
                self.cfg_seen.append(dict(m))
                if m.get("session_ms"):
                    self.session_ms = max(60000, min(240 * 60000, int(m["session_ms"])))
                self.led_cfi = int(m.get("led_cfi", self.led_cfi))
                self.buzz_cfi = int(m.get("buzz_cfi", self.buzz_cfi))
                self._ack(i)
            elif t == "water":
                self.pumped.append(int(m.get("ms", 2500)))
                self._ack(i)
                self._event("watering")
                self._event("watered")
            elif t == "beep":
                self._ack(i)
            elif t == "reset_key":
                if self.phase == "active":
                    self._ack(i, False, "busy")
                else:
                    self.paired = self.verified = False
                    self._ack(i)
                    self._event("key_reset")
            elif t == "start":
                if self.phase == "active":
                    self._ack(i, False, "busy")
                else:
                    self._ack(i)
                    self.phase, self.start, self.viol, self.test = "active", self.now(), 0, True
                    self.len_s = int(m.get("session_ms") or self.session_ms) / 1000
                    self._event("session_started")
            elif t == "charge":
                self._ack(i)
                self.charging = bool(m.get("on", True))
                self._event("phone_in" if self.charging else "phone_out")
            elif t == "io":
                self.io_count += 1
                self._ack(i)
            elif t == "end":
                if self.phase != "active":
                    self._ack(i, False, "no_session")
                else:
                    self._ack(i)
                    self._end(m.get("reason", "pc"))
            else:
                self._ack(i, False, "unknown")

    # ---------------------------------------------------------------- thao tác "vật lý"
    def close_door(self):
        with self.lock:
            self.door_closed = True
            self._event("door_close")
            if self.paired:
                self.verified = True          # app Android ký nonce hợp lệ
        self._check()

    def open_door(self):
        with self.lock:
            self.door_closed = False
            if self.phase == "active":
                self.viol += 1
            self._event("door_open")
            if self.phase == "done":
                self.phase = "idle"

    def plug(self, on: bool = True):
        with self.lock:
            self.charging = on
            if not on and self.phase == "active":
                self.viol += 1
            self._event("phone_in" if on else "phone_out")
        self._check()

    def fast_forward(self, seconds: float):
        self.offset += seconds
        self._check()


class FakeTransport:
    def __init__(self, box):
        self.box = box

    def readline(self) -> bytes:
        try:
            return self.box.to_pc.get(timeout=0.1)
        except queue.Empty:
            return b""

    def write(self, data: bytes):
        threading.Thread(target=self.box.feed, args=(data,), daemon=True).start()

    def close(self):
        pass
