# -*- coding: utf-8 -*-
"""Hộp điện thoại GIẢ LẬP bằng Python — chạy đúng giao thức như firmware ESP32 để thử phần mềm khi chưa có mạch."""
import hashlib
import hmac
import json
import queue
import secrets
import threading


class FakeBox:
    def __init__(self, key: str = ""):
        self.key = key
        self.nonce = secrets.token_hex(4)
        self.phone = False
        self.lid = "closed"
        self.lock = "unlocked"
        self.pair_mode = False
        self.to_pc: "queue.Queue[bytes]" = queue.Queue()
        self.silent = False

    def _send(self, msg):
        if not self.silent:
            self.to_pc.put((json.dumps(msg) + "\n").encode())

    def _mac_ok(self, m) -> bool:
        if not self.key:
            return False
        want = hmac.new(self.key.encode(), f"{m['t']}|{m['id']}|{self.nonce}|{int(m.get('sec', 0) or 0)}".encode(),
                        hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(want, str(m.get("mac", "")))

    def feed(self, line: bytes):
        m = json.loads(line)
        t, i = m.get("t"), m.get("id")
        if t == "hello":
            self._send({"t": "hello", "dev": "focusdesk-box", "fw": "sim-1.0", "box_id": "SIM01", "nonce": self.nonce,
                        "paired": bool(self.key), "phone": self.phone, "lid": self.lid, "lock": self.lock})
        elif t in ("ping", "get"):
            self._send({"t": "state", "phone": self.phone, "lid": self.lid, "lock": self.lock, "nonce": self.nonce})
        elif t in ("lock", "unlock"):
            if not self._mac_ok(m):
                self._send({"t": "ack", "id": i, "ok": False, "err": "bad_mac"})
                return
            self.nonce = secrets.token_hex(4)
            self.lock = "locked" if t == "lock" else "unlocked"
            self._send({"t": "ack", "id": i, "ok": True, "nonce": self.nonce})
            self._send({"t": "event", "e": "locked" if t == "lock" else "unlocked", "lock": self.lock, "nonce": self.nonce})
        elif t == "setkey":
            if not self.pair_mode:
                self._send({"t": "ack", "id": i, "ok": False, "err": "not_in_pair_mode"})
                return
            self.key, self.pair_mode = m["key"], False
            self._send({"t": "ack", "id": i, "ok": True})
        elif t == "beep":
            self._send({"t": "ack", "id": i, "ok": True})

    # thao tác "vật lý"
    def put_phone(self):
        self.phone = True
        self._send({"t": "event", "e": "phone_in", "phone": True})

    def take_phone(self):
        self.phone = False
        self._send({"t": "event", "e": "tamper" if self.lock == "locked" else "phone_out", "phone": False})


class FakeTransport:
    def __init__(self, box: FakeBox):
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
