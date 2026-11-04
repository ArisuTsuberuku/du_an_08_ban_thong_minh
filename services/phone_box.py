# -*- coding: utf-8 -*-
"""
Mô-đun ESP32 của FocusDesk (services/phone_box.py) — phía máy tính.
  v2 (hiện hành, firmware/focusdesk_esp32): TỦ điện thoại có công tắc hành trình + cắm sạc + chữ ký TEE, tưới cây thưởng,
      đèn / còi theo mức tập trung. Đặc tả: firmware/GIAO_THUC.md.
  v1 (giai đoạn 1, firmware/hop_dien_thoai_esp32s3): hộp KHÓA điện thoại có HMAC — vẫn hỗ trợ để tương thích.

KẾT NỐI: cáp USB (cổng USB native của ESP32-S3 = cổng COM ảo, hoặc chip CH340/CP210x), 115200 baud.
Tự dò cổng: ưu tiên VID Espressif 0x303A, rồi CH340 / CP210x / FTDI; BỎ QUA cổng COM Bluetooth.
Có thể ghim cổng trong shared_config: "esp32_com_port": "COM7" (mặc định "auto").

GIAO THỨC: mỗi dòng 1 JSON (NDJSON), kết thúc "\\n". Trường chung: "t" = loại tin, "id" = số thứ tự (PC đặt).
  PC  -> Hộp : hello · ping {ts} · state? (t="get") · lock · unlock {sec} · beep {n} · setkey {key} · cfg {...}
  Hộp -> PC  : hello {dev:"focusdesk-box", fw, box_id, nonce, paired} · state {phone, lid, lock, nonce}
               · event {e: phone_in|phone_out|lid_open|lid_close|locked|unlocked|tamper|button|pair_mode}
               · ack {id, ok, err?}
  Ví dụ:  PC  {"t":"unlock","id":12,"sec":600,"mac":"9f2c…"}
          Hộp {"t":"ack","id":12,"ok":true}   rồi   {"t":"event","e":"unlocked"}

AN TOÀN: lệnh lock/unlock/cfg kèm "mac" = HMAC-SHA256(khóa_chung, "t|id|nonce|sec")[:16 hex]. "nonce" do hộp sinh ngẫu
nhiên và đổi sau mỗi lệnh -> học sinh cắm hộp sang máy khác / dùng phần mềm terminal gõ "unlock" cũng không mở được,
và không phát lại được lệnh cũ. Khóa chung tạo lúc "Ghép hộp": hộp chỉ nhận setkey trong 60 giây sau khi giữ nút 5 giây.

TRẠNG THÁI: ghi vào runtime_state["phone_box"] (app học sinh, web nội bộ, cloud agent đọc lại).
"""

import hashlib
import hmac
import json
import queue
import secrets
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from core.config_manager import load_config, load_runtime_state, safe_log, save_config, update_runtime_state

BOX_DEV_IDS = ("focusdesk-box", "smartdesk-box")   # "smartdesk-box" = firmware đời đầu, vẫn chấp nhận

STATE_KEYS = ("phone", "lid", "lock", "nonce", "fw", "box_id", "paired", "unlock_left",
              # v2 (tủ điện thoại)
              "proto", "phase", "door", "ble", "verified", "session_s", "left_s", "viol", "pump", "active", "test",
              "cfi", "sim_charge", "key_fp", "pair_left", "pair_expect", "study", "break_left", "relay")
DEFAULT_BOX_CFG = {"session_min": 25, "led_cfi": 50, "buzz_cfi": 30}

BAUD = 115200               # mặc định; Admin có thể đổi (shared_config "esp32_baud")
PING_S = 2.0
LOST_S = 6.0                # không nghe hộp > 6s = mất kết nối
PREFERRED_VIDS = (0x303A, 0x1A86, 0x10C4, 0x0403)   # Espressif, CH340, CP210x, FTDI


# ---------------------------------------------------------------------- nhật ký serial (cho Serial monitor của Admin)
# Mỗi phần tử: (seq, thời_điểm, chiều "TX"|"RX"|"SYS", nội_dung, cổng). seq tăng dần để giao diện chỉ lấy dòng mới.
TRAFFIC: Deque[Tuple[int, float, str, str, str]] = deque(maxlen=1500)
_traffic_seq = [0]
_traffic_lock = threading.Lock()


def log_traffic(direction: str, text: str, port: str = "") -> None:
    with _traffic_lock:
        _traffic_seq[0] += 1
        TRAFFIC.append((_traffic_seq[0], time.time(), direction, text.rstrip("\r\n"), port))


def traffic_since(seq: int) -> List[Tuple[int, float, str, str, str]]:
    with _traffic_lock:
        return [row for row in TRAFFIC if row[0] > seq]


def configured_baud() -> int:
    try:
        return int(load_config().get("esp32_baud", BAUD) or BAUD)
    except Exception:
        return BAUD


def make_mac(key: str, t: str, msg_id: int, nonce: str, sec: int = 0) -> str:
    return hmac.new(key.encode(), f"{t}|{msg_id}|{nonce}|{sec}".encode(), hashlib.sha256).hexdigest()[:16]


_diag_last: Dict[str, float] = {}


def _diag(key: str, text: str, every_s: float = 30.0) -> None:
    """Ghi lý do KHÔNG kết nối được vào nhật ký Serial monitor (tối đa 1 lần / every_s giây mỗi loại)."""
    if time.time() - _diag_last.get(key, 0) >= every_s:
        _diag_last[key] = time.time()
        log_traffic("SYS", "⚠️ " + text)
        safe_log("[HỘP] " + text)


def find_box_ports() -> List[Dict[str, Any]]:
    """Cổng COM có thể là hộp, đã xếp theo khả năng; bỏ cổng Bluetooth ảo."""
    try:
        from serial.tools import list_ports
    except Exception as exc:
        _diag("noserial", f"Không dùng được serial.tools.list_ports ({exc}). Thường do cài nhầm gói 'serial' thay vì "
                          "'pyserial': chạy  python -m pip uninstall -y serial  rồi  python -m pip install pyserial")
        return []
    out = []
    for p in list_ports.comports():
        hwid = (p.hwid or "").upper()
        if "BTHENUM" in hwid or "BLUETOOTH" in (p.description or "").upper():
            continue
        rank = PREFERRED_VIDS.index(p.vid) if p.vid in PREFERRED_VIDS else 9
        out.append({"port": p.device, "desc": p.description, "vid": p.vid, "pid": p.pid, "rank": rank})
    return sorted(out, key=lambda d: d["rank"])


def list_all_ports() -> List[Dict[str, Any]]:
    """Mọi cổng COM (kể cả Bluetooth) cho Serial monitor; đánh dấu cổng nghi là hộp."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    out = []
    for p in list_ports.comports():
        hwid = (p.hwid or "").upper()
        bt = "BTHENUM" in hwid or "BLUETOOTH" in (p.description or "").upper()
        out.append({"port": p.device, "desc": p.description or "", "vid": p.vid, "pid": p.pid, "bluetooth": bt,
                    "likely_box": (p.vid in PREFERRED_VIDS) and not bt})
    return sorted(out, key=lambda d: (not d["likely_box"], d["bluetooth"], d["port"]))


class _SerialTransport:
    def __init__(self, port: str, baud: Optional[int] = None):
        import serial

        self.port = port
        self.ser = serial.Serial(port, baud or configured_baud(), timeout=0.2, write_timeout=1.0)
        try:
            self.ser.dtr = True   # USB CDC của ESP32-S3 cần DTR để gửi dữ liệu
        except Exception:
            pass

    def readline(self) -> bytes:
        return self.ser.readline()

    def write(self, data: bytes) -> None:
        self.ser.write(data)

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass


class PhoneBoxLink:
    """Luồng nền giữ kết nối với hộp: dò cổng, ping 2s, nhận sự kiện, gửi lệnh có ký HMAC."""

    def __init__(self, transport_factory: Optional[Callable[[str], Any]] = None,
                 on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        self._factory = transport_factory or _SerialTransport
        self.on_event = on_event
        self.tr = None
        self.port = ""
        self.state: Dict[str, Any] = {"connected": False}
        self._id = 0
        self._acks: Dict[int, "queue.Queue"] = {}
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._stop = threading.Event()
        self._last_rx = 0.0
        self._thread: Optional[threading.Thread] = None
        self._suspended = threading.Event()   # Serial monitor mượn cổng ở chế độ thô -> tạm ngừng tự kết nối

    # ------------------------------------------------------------------ vòng đời
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="PhoneBoxThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.tr:
            self.tr.close()

    def suspend(self) -> None:
        """Nhả cổng COM cho Serial monitor (chế độ cổng thô). Gọi resume() để tự kết nối lại."""
        self._suspended.set()
        if self.tr:
            self.tr.close()
            self.tr = None
        if self.state.get("connected"):
            self.state = {"connected": False}
        self.state["suspended"] = True
        log_traffic("SYS", "Tạm ngừng kết nối tự động (Serial monitor đang dùng cổng)", self.port)
        self._publish()

    def resume(self) -> None:
        self._suspended.clear()
        self.state.pop("suspended", None)
        log_traffic("SYS", "Bật lại kết nối tự động")
        self._publish()

    def _publish(self) -> None:
        try:
            update_runtime_state(phone_box={**self.state, "port": self.port, "t": int(time.time())})
        except Exception:
            pass

    def _open(self) -> bool:
        cfg = load_config()
        want = str(cfg.get("esp32_com_port", "auto") or "auto").strip()
        ports = [want] if want.lower() not in ("auto", "", "com3") else [p["port"] for p in find_box_ports()]
        if not ports:
            _diag("noport", "Không thấy cổng USB-serial nào (cáp chưa cắm, cáp chỉ có dây sạc, thiếu driver CH343/CP210x, "
                            "hoặc Python không đọc được danh sách cổng). Kiểm tra Device Manager → Ports (COM & LPT).")
        elif want.lower() not in ("auto", "", "com3"):
            _diag("pinned", f"Đang GHIM cổng {want} (Admin → Cổng kết nối). Nếu Device Manager hiện số COM khác, chọn 'auto'.",
                  every_s=120)
        for port in ports:
            if self._suspended.is_set():
                return False
            try:
                tr = self._factory(port)
            except Exception as exc:
                log_traffic("SYS", f"Không mở được {port}: {exc}", port)
                continue
            self.tr, self.port = tr, port
            self._send_raw({"t": "hello", "id": self._next_id()})
            t0 = time.time()
            while time.time() - t0 < 2.5:
                msg = self._read_one()
                if msg and msg.get("t") == "hello" and msg.get("dev") in BOX_DEV_IDS:
                    if self._suspended.is_set():          # Serial monitor vừa xin cổng -> nhả ra
                        break
                    self._on_msg(msg)
                    safe_log(f"[HỘP] Đã kết nối mô-đun ESP32 {msg.get('box_id')} ở {port} (fw {msg.get('fw')}, giao thức v{msg.get('proto', 1)})")
                    return True
            log_traffic("SYS", f"{port}: không thấy hello hợp lệ (dev phải là {BOX_DEV_IDS[0]}) trong 2,5 giây", port)
            tr.close()
            self.tr, self.port = None, ""
        return False

    def _run(self) -> None:
        last_ping = 0.0
        while not self._stop.is_set():
            if self._suspended.is_set():
                self._stop.wait(0.5)
                continue
            if self.tr is None:
                if not self._open():
                    if self.state.get("connected"):
                        self.state = {"connected": False}
                        self._emit("disconnected", {})
                    self._publish()
                    self._stop.wait(5.0)
                    continue
                self._last_rx = time.time()
            now = time.time()
            if now - last_ping >= PING_S:
                last_ping = now
                ping = {"t": "ping", "id": self._next_id(), "ts": int(now)}
                if self.proto >= 2:
                    ping["cfi"] = current_cfi()          # tủ v2 nháy đèn / kêu còi theo CFI của camera AI
                    ping["study"] = studying_now()       # tủ chỉ bắt đầu ca khi học sinh đang ở chế độ Học sinh
                self._send_raw(ping)
            msg = self._read_one()
            if msg:
                self._last_rx = time.time()
                self._on_msg(msg)
            elif time.time() - self._last_rx > LOST_S and self.tr is not None:
                safe_log("[HỘP] Mất tín hiệu hộp điện thoại — dò lại cổng…")
                log_traffic("SYS", f"Không nghe hộp > {LOST_S:.0f}s -> mất kết nối, dò lại cổng", self.port)
                self.tr.close()
                self.tr = None
                self.state = {"connected": False}
                self._emit("disconnected", {})
                self._publish()

    # ------------------------------------------------------------------ đọc / ghi
    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    def _send_raw(self, msg: Dict[str, Any]) -> None:
        if self.tr is None:
            raise ConnectionError("Chưa kết nối hộp")
        line = json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
        log_traffic("TX", line, self.port)          # ghi trước khi gửi để nhật ký đúng thứ tự TX -> RX
        with self._wlock:
            self.tr.write((line + "\n").encode())

    def send_text(self, line: str) -> None:
        """Serial monitor: gửi nguyên văn 1 dòng (không ký) qua kết nối đang mở."""
        if self.tr is None:
            raise ConnectionError("Chưa kết nối hộp")
        line = line.strip()
        log_traffic("TX", line, self.port)
        with self._wlock:
            self.tr.write((line + "\n").encode())

    def _read_one(self) -> Optional[Dict[str, Any]]:
        try:
            line = self.tr.readline() if self.tr else b""
        except Exception:
            self.tr = None
            return None
        if not line:
            return None
        text = line.decode("utf-8", "ignore").strip()
        if text:
            log_traffic("RX", text, self.port)
        try:
            msg = json.loads(text)
            return msg if isinstance(msg, dict) else None
        except Exception:
            return None   # dòng log / rác từ ESP32 khi khởi động -> bỏ qua

    def _on_msg(self, msg: Dict[str, Any]) -> None:
        t = msg.get("t")
        if t in ("hello", "state"):
            prev = dict(self.state)
            self.state.update(connected=True, **{k: msg[k] for k in STATE_KEYS if k in msg})
            self._track_end(msg)
            if not prev.get("connected"):
                self._emit("connected", self.state)
                if self.proto >= 2:
                    threading.Thread(target=self.push_cfg, daemon=True).start()   # gửi thời gian học + ngưỡng CFI
            self._publish()
        elif t == "event":
            e = str(msg.get("e", ""))
            for k in STATE_KEYS:
                if k in msg and k not in ("fw", "box_id"):
                    self.state[k] = msg[k]
            if e == "session_started":       # mốc chuẩn nhất: đúng lúc tủ bắt đầu đếm
                self.state["end_at"] = round(time.time() + float(msg.get("session_s") or 0), 2)
                self.state["end_phase"] = "active"
            self._track_end(msg)
            self._publish()
            self._emit(e, msg)
        elif t == "ack":
            q = self._acks.get(int(msg.get("id", -1)))
            if q:
                q.put(msg)
            if "nonce" in msg:
                self.state["nonce"] = msg["nonce"]

    def _track_end(self, msg: Dict[str, Any]) -> None:
        """Giữ 1 mốc kết thúc ca ỔN ĐỊNH (end_at, giờ máy) để đồng hồ đếm ngược chạy đều từng giây.
        left_s của tủ là số giây làm tròn xuống và chỉ cập nhật mỗi ~2 s (ping) -> nếu hiển thị thẳng sẽ bị nhảy cóc.
        Chỉ chỉnh lại mốc khi lệch > 2 s (máy vừa mở lại giữa ca, tủ bị trễ...)."""
        ph = self.state.get("phase")
        if ph not in ("active", "break"):
            self.state.pop("end_at", None)
            self.state.pop("end_phase", None)
            return
        if self.state.get("end_phase") != ph:          # đổi từ nghỉ sang học (hoặc ngược lại) -> mốc mới
            self.state.pop("end_at", None)
            self.state["end_phase"] = ph
        key = "left_s" if ph == "active" else "break_left"
        if key not in msg:
            return
        # left_s làm tròn XUỐNG (+0,5 s), break_left làm tròn LÊN (-0,5 s)
        cand = time.time() + float(msg.get(key) or 0) + (0.5 if ph == "active" else -0.5)
        cur = self.state.get("end_at")
        if not isinstance(cur, (int, float)) or abs(cand - cur) > 2.0:
            self.state["end_at"] = round(cand, 2)

    def _emit(self, e: str, data: Dict[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(e, data)
            except Exception as exc:
                safe_log(f"[HỘP] Lỗi xử lý sự kiện {e}: {exc}")

    # ------------------------------------------------------------------ lệnh
    def command(self, t: str, timeout: float = 3.0, **extra: Any) -> Dict[str, Any]:
        """Gửi lệnh có ký HMAC, chờ ack. Trả {"ok": bool, "err"?}."""
        if self.tr is None or not self.state.get("connected"):
            return {"ok": False, "err": "Chưa kết nối hộp điện thoại"}
        key = str(load_config().get("phone_box_key", "") or "")
        msg_id = self._next_id()
        msg = {"t": t, "id": msg_id, **extra}
        if t in ("lock", "unlock", "cfg") and self.proto < 2:      # v1: lệnh khóa ký HMAC; v2 không có khóa cơ khí
            if not key:
                return {"ok": False, "err": "Hộp chưa được ghép (Admin → Hộp điện thoại → Ghép hộp)"}
            msg["mac"] = make_mac(key, t, msg_id, str(self.state.get("nonce", "")), int(extra.get("sec", 0) or 0))
        q: "queue.Queue" = queue.Queue()
        self._acks[msg_id] = q
        try:
            self._send_raw(msg)
            return q.get(timeout=timeout)
        except queue.Empty:
            return {"ok": False, "err": "Hộp không phản hồi"}
        except Exception as exc:
            return {"ok": False, "err": str(exc)}
        finally:
            self._acks.pop(msg_id, None)

    def lock(self) -> Dict[str, Any]:
        return self.command("lock")

    def unlock(self, seconds: int = 0) -> Dict[str, Any]:
        """Mở khóa; seconds > 0 = tự khóa lại sau chừng ấy giây khi nắp đóng lại."""
        return self.command("unlock", sec=int(seconds))

    def beep(self, n: int = 2) -> Dict[str, Any]:
        return self.command("beep", n=int(n))

    def water(self, ms: int = 2500) -> Dict[str, Any]:
        """Thưởng xanh: bật bơm tưới cây ms mili-giây (firmware tự khóa cứng tối đa 10 giây)."""
        return self.command("water", ms=max(500, min(10000, int(ms))))

    # ---------------------------------------------------------------- v2: tủ điện thoại
    @property
    def proto(self) -> int:
        try:
            return int(self.state.get("proto", 1) or 1)
        except (TypeError, ValueError):
            return 1

    def set_cfg(self, session_min: Optional[float] = None, led_cfi: Optional[int] = None,
                buzz_cfi: Optional[int] = None) -> Dict[str, Any]:
        """Gửi thời lượng ca học (phút) + ngưỡng CFI nháy đèn / kêu còi. Tủ lưu vào bộ nhớ, áp dụng từ ca sau."""
        extra: Dict[str, Any] = {}
        if session_min:
            extra["session_ms"] = int(max(1, min(240, float(session_min))) * 60000)
        if led_cfi is not None:
            extra["led_cfi"] = max(0, min(100, int(led_cfi)))
        if buzz_cfi is not None:
            extra["buzz_cfi"] = max(0, min(100, int(buzz_cfi)))
        return self.command("cfg", **extra)

    def push_cfg(self) -> Dict[str, Any]:
        """Gửi cấu hình phụ huynh đặt trên web (đã lưu ở máy: study_box_cfg) xuống tủ."""
        c = box_cfg()
        return self.set_cfg(c["session_min"], c["led_cfi"], c["buzz_cfi"])

    def reset_key(self, expect: str = "") -> Dict[str, Any]:
        """Xóa khóa điện thoại đã ghép, mở cửa sổ ghép 2 phút. expect = mã ghép 6 số trên app (chỉ nhận đúng điện thoại đó).
        Đang trong ca: bị từ chối."""
        extra = {"expect": expect} if expect and len(expect) == 6 and expect.isdigit() else {}
        r = self.command("reset_key", timeout=4.0, **extra)
        if not r.get("ok") and r.get("err") == "busy":
            r["err"] = "Đang trong ca học — chờ hết giờ học rồi reset"
        return r

    def set_relay(self, mode: str) -> Dict[str, Any]:
        """Kiểu kích rơ-le bơm: high | low | low_od (tủ lưu vào bộ nhớ, tắt bơm ngay theo kiểu mới)."""
        mode = mode if mode in ("high", "low", "low_od") else "low"
        return self.command("cfg", relay=mode)

    def start_test(self, session_min: float = 1.0) -> Dict[str, Any]:
        """THỬ NGHIỆM: tủ vào ca ngay (bỏ qua cửa / sạc / chữ ký điện thoại). Ca thử không ghi điểm, không tưới cây."""
        return self.command("start", session_ms=int(max(10, min(240 * 60, float(session_min) * 60)) * 1000))

    def sim_charge(self, on: bool) -> Dict[str, Any]:
        """THỬ NGHIỆM: giả lập điện thoại cắm sạc (khi app Android chưa gửi được "CHARGED")."""
        return self.command("charge", on=bool(on))

    def test_io(self) -> Dict[str, Any]:
        """THỬ NGHIỆM: nháy lần lượt đèn đỏ -> đèn xanh -> còi (1,5 s), không bật bơm."""
        return self.command("io")

    def next_session(self, break_min: float = 0) -> Dict[str, Any]:
        """Sau ca: nghỉ break_min phút rồi tủ tự về chờ ca mới (không cần mở/đóng cửa). 0 = bắt đầu ngay."""
        r = self.command("next", break_ms=int(max(0, min(60, float(break_min))) * 60000))
        if not r.get("ok") and r.get("err") == "busy":
            r["err"] = "Đang trong ca học"
        elif not r.get("ok") and r.get("err") == "unknown":
            r["err"] = "Firmware tủ cũ (cần bản 2.5.0 trở lên)"
        return r

    def end_session(self, reason: str = "pc") -> Dict[str, Any]:
        return self.command("end", reason=reason)

    def pair(self) -> Dict[str, Any]:
        """Tạo khóa chung mới và gửi cho hộp (hộp phải đang ở chế độ ghép: giữ nút 5 giây)."""
        key = secrets.token_hex(16)
        res = self.command("setkey", key=key, timeout=5.0)
        if res.get("ok"):
            cfg = load_config()
            cfg["phone_box_key"] = key
            save_config(cfg)
        return res


class RawSerialMonitor:
    """Serial monitor chế độ CỔNG THÔ: mở bất kỳ cổng/baud nào, ghi mọi dòng nhận được (kể cả log không phải JSON).
    Dùng khi hộp chưa kết nối được (sai baud, sai "dev", firmware in rác...) để người làm phần cứng gỡ lỗi."""

    def __init__(self, port: str, baud: int = BAUD, factory: Optional[Callable[..., Any]] = None):
        self.port, self.baud = port, int(baud)
        self.tr = (factory or _SerialTransport)(port, self.baud)
        self.last_nonce = ""
        self._id = 900000          # id riêng, không trùng id của kết nối tự động
        self._stop = threading.Event()
        log_traffic("SYS", f"Mở cổng thô {port} @ {self.baud} baud", port)
        threading.Thread(target=self._loop, name="RawSerialMonitor", daemon=True).start()

    def _loop(self) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self.tr.readline()
            except Exception as exc:
                log_traffic("SYS", f"Lỗi đọc cổng: {exc}", self.port)
                break
            if not chunk:
                continue
            buf += chunk
            if not buf.endswith(b"\n") and len(buf) < 4096:
                continue
            text = buf.decode("utf-8", "replace").strip()
            buf = b""
            if not text:
                continue
            log_traffic("RX", text, self.port)
            try:
                msg = json.loads(text)
                if isinstance(msg, dict) and msg.get("nonce"):
                    self.last_nonce = str(msg["nonce"])
            except Exception:
                pass

    def send_text(self, line: str) -> None:
        line = line.strip()
        log_traffic("TX", line, self.port)
        self.tr.write((line + "\n").encode())

    def send_signed(self, t: str, sec: int = 0) -> str:
        """Gửi lock/unlock có ký bằng khóa đã ghép + nonce mới nhất vừa nhận (gửi "get" trước để có nonce)."""
        key = str(load_config().get("phone_box_key", "") or "")
        if not key:
            raise ValueError("Chưa có khóa ghép (phone_box_key) — ghép hộp trước")
        if not self.last_nonce:                      # chưa có nonce -> tự hỏi trạng thái rồi chờ tối đa 2 giây
            self._id += 1
            self.send_text(json.dumps({"t": "get", "id": self._id}, separators=(",", ":")))
            t0 = time.time()
            while not self.last_nonce and time.time() - t0 < 2.0:
                time.sleep(0.05)
        if not self.last_nonce:
            raise ValueError("Hộp không trả nonce (không có phản hồi state cho lệnh get)")
        self._id += 1
        msg: Dict[str, Any] = {"t": t, "id": self._id}
        if t == "unlock":
            msg["sec"] = int(sec)
        msg["mac"] = make_mac(key, t, self._id, self.last_nonce, int(sec))
        line = json.dumps(msg, separators=(",", ":"))
        self.send_text(line)
        return line

    def close(self) -> None:
        self._stop.set()
        self.tr.close()
        log_traffic("SYS", f"Đóng cổng thô {self.port}", self.port)


# ---------------------------------------------------------------------- singleton + chính sách
_link: Optional[PhoneBoxLink] = None
_session = {"studying": False, "since_out": None}


def _alert(msg: str) -> None:
    try:
        from services.giam_sat_worker import gui_canh_bao_len_web

        gui_canh_bao_len_web(msg, "PHONE_BOX")
    except Exception:
        safe_log(f"[HỘP] {msg}")


def box_cfg() -> Dict[str, Any]:
    """Thời lượng ca + ngưỡng CFI phụ huynh đặt trên web (cloud agent lưu vào shared_config["study_box_cfg"])."""
    c = dict(DEFAULT_BOX_CFG)
    raw = load_config().get("study_box_cfg") or {}
    if isinstance(raw, dict):
        for k in c:
            try:
                if raw.get(k) is not None:
                    c[k] = type(c[k])(raw[k])
            except (TypeError, ValueError):
                pass
    return c


_test_cfi: Dict[str, Any] = {"v": None, "until": 0.0}


def set_test_cfi(value: Optional[int], seconds: int = 60) -> None:
    """Admin thử nghiệm: ép CFI gửi xuống tủ trong `seconds` giây (None = trả về CFI thật của camera)."""
    if value is None:
        _test_cfi.update(v=None, until=0.0)
    else:
        _test_cfi.update(v=max(-1, min(100, int(value))), until=time.time() + max(5, min(3600, int(seconds))))
    log_traffic("SYS", f"CFI thử: {value if value is not None else 'tắt'}")


def studying_now() -> bool:
    """Học sinh đang ở chế độ Học sinh trên FocusDesk (điều kiện để tủ bắt đầu ca)."""
    try:
        return load_runtime_state().get("current_role") == "student"
    except Exception:
        return False


def current_cfi() -> int:
    """CFI hiện tại cho tủ v2 (0–100), -1 = không có (camera tắt, không ở chế độ học sinh, đang nghỉ vệ sinh)."""
    if _test_cfi["v"] is not None and time.time() < _test_cfi["until"]:
        return int(_test_cfi["v"])
    try:
        st = load_runtime_state()
        pt = st.get("posture_telemetry") or {}
        if st.get("current_role") != "student" or not pt.get("camera_connected"):
            return -1
        if (st.get("toilet_break") or {}).get("active"):
            return -1
        v = pt.get("cfi_score")
        return -1 if v is None else max(0, min(100, int(round(float(v)))))
    except Exception:
        return -1


_V2_TEXT = {
    "session_started": "📦 Đã cất điện thoại vào tủ, cắm sạc, đóng cửa → bắt đầu ca học {m} phút",
    "session_end": "⏰ Hết giờ học — ca {m} phút đã xong (tưới cây nếu ca đạt chuẩn)",
    "door_open": "⚠️ Cửa tủ điện thoại bị mở trong giờ học",
    "phone_out": "⚠️ Điện thoại bị rút sạc / lấy ra khỏi tủ trong giờ học",
    "key_reset": "🔑 Đã reset khóa điện thoại — tủ chờ điện thoại mới ghép trong 2 phút (app: “Liên kết lại tủ”)",
    "paired": "✅ Tủ đã ghép điện thoại mã {code} — kiểm tra trùng với mã trên app của con",
    "pair_mismatch": "⚠️ Điện thoại mã {got} vừa kết nối nhưng KHÁC mã phụ huynh nhập ({exp}) — tủ không ghép",
    "pair_refused": "⚠️ Có điện thoại muốn ghép nhưng tủ đã hết giờ ghép — bấm “Ghép điện thoại mới” trên web",
    "verify_failed": "🔑 Điện thoại kết nối nhưng chữ ký SAI (khóa khác khóa đã ghép: cài lại app / tạo khóa mới?) — "
                     "bấm “Ghép điện thoại mới” trên web",
}


_last_warn: Dict[str, float] = {}


def _fmt_code(c: Any) -> str:
    c = str(c or "")
    return f"{c[:3]} {c[3:]}" if len(c) == 6 else (c or "?")


def _v2_event(e: str, d: Dict[str, Any]) -> None:
    """Tủ v2: tủ tự đếm giờ học. Sự kiện trong ca -> vi phạm + báo; sau giờ học (active=false) -> im lặng."""
    from services import study_sessions

    active = bool(d.get("active"))
    if d.get("test"):                                # ca THỬ NGHIỆM (Admin): không ghi ca, không vi phạm, không tưới
        if e == "session_started":
            sec = int(d.get("session_s") or 0)
            _alert(f"🧪 [Thử nghiệm] Tủ vào ca thử {f'{sec // 60} phút ' if sec >= 60 else ''}"
                   f"{f'{sec % 60} giây' if sec % 60 or sec < 60 else ''}".strip())
        elif e == "session_end":
            _alert(f"🧪 [Thử nghiệm] Hết ca thử ({int(d.get('dur_s') or 0)} giây, {d.get('viol', 0)} lần mở cửa/rút sạc)")
        elif e in ("door_open", "phone_out") and active:
            _alert("🧪 [Thử nghiệm] " + _V2_TEXT[e])
        return
    if e == "session_started":
        mins = round(float(d.get("session_s") or 0) / 60.0, 1)
        st = load_runtime_state()
        study_sessions.start_session(ai_seconds=float(st.get("ai_seconds", 0) or 0), phone_in_box=True, source="box",
                                     planned_min=mins)
        _alert(_V2_TEXT[e].format(m=f"{mins:g}"))
    elif e == "session_end":
        reason = {"time_up": "Hết giờ học", "left_student": "Rời chế độ học sinh trước giờ"}.get(
            str(d.get("reason")), "Kết thúc ca sớm")
        rep = study_sessions.end_session(reason,
                                         dur_s=d.get("dur_s"), time_up=d.get("reason") == "time_up")
        mins = rep.get("minutes") if rep else round(float(d.get("dur_s") or 0) / 60.0, 1)
        if d.get("reason") == "time_up":
            _alert(_V2_TEXT[e].format(m=f"{mins:g}"))
        else:
            _alert(f"⏹️ Ca học kết thúc trước giờ ({reason.lower()}) sau {mins:g} phút — ca chưa đủ thời gian")
    elif e in ("door_open", "phone_out"):
        if active:                                   # sau giờ học: rút sạc / mở tủ KHÔNG báo nữa
            study_sessions.note_violation("PHONE")
            _alert(_V2_TEXT[e])
    elif e == "break_started":
        _alert(f"☕ Nghỉ giữa ca {round(float(d.get('break_left') or 0) / 60):g} phút — lấy điện thoại không bị tính vi phạm; "
               "hết giờ nghỉ tủ kêu nhắc vào ca mới")
    elif e == "break_end":
        _alert("⏰ Hết giờ nghỉ — cất điện thoại vào tủ, cắm sạc, đóng cửa (ở chế độ Học sinh) để vào ca mới")
    elif e == "key_reset":
        _alert(_V2_TEXT[e])
    elif e == "paired":
        _alert(_V2_TEXT[e].format(code=_fmt_code(d.get("key_fp"))))
    elif e in ("pair_mismatch", "pair_refused", "verify_failed"):
        key = f"{e}:{d.get('got', '')}"
        if time.time() - _last_warn.get(key, 0) > 120:      # điện thoại thử lại mỗi vài giây -> báo 1 lần / 2 phút
            _last_warn[key] = time.time()
            _alert(_V2_TEXT[e].format(got=_fmt_code(d.get("got")), exp=_fmt_code(d.get("expect"))))
    elif e == "disconnected":
        if (study_sessions.current() or {}).get("source") == "box":
            _alert("🔌 Mất kết nối tủ điện thoại trong giờ học (rút cáp USB?)")
    elif e in ("ble_connected", "ble_lost", "ble_cmd"):
        safe_log(f"[TỦ] BLE {e}: " + ", ".join(f"{k}={d.get(k)}" for k in ("v", "cmd_notify", "cmd_indicate",
                                                                            "cmd_read", "phone", "verified") if k in d))
    elif e == "connected":
        safe_log(f"[TỦ] Đã kết nối tủ điện thoại v2 ({d.get('box_id')}) — pha {d.get('phase')}")


def _default_event_handler(e: str, d: Dict[str, Any]) -> None:
    """Biến sự kiện phần cứng thành cảnh báo cho phụ huynh (theo ngữ cảnh: đang học hay không)."""
    if int(d.get("proto", 0) or 0) >= 2 or (_link is not None and _link.proto >= 2):
        _v2_event(e, d)
        return
    studying = _session["studying"]
    texts = {
        "phone_in": "📦 Học sinh đã cất điện thoại vào hộp",
        "locked": "🔒 Hộp điện thoại đã khóa",
        "unlocked": "🔓 Hộp điện thoại đã mở khóa",
        "tamper": "🚨 Hộp điện thoại bị cạy / mở nắp khi đang khóa!",
        "button": "🙋 Học sinh bấm nút xin lấy điện thoại",
        "disconnected": "🔌 Mất kết nối hộp điện thoại (rút cáp USB?)" if studying else "",
        "connected": "🔌 Đã kết nối hộp điện thoại",
        "phone_out": "⚠️ Điện thoại bị lấy ra khỏi hộp trong giờ học" if studying else "📱 Điện thoại đã lấy ra khỏi hộp",
    }
    msg = texts.get(e, "")
    if e in ("phone_out", "tamper", "button", "disconnected") or (e in ("phone_in", "locked", "unlocked") and studying):
        if msg:
            _alert(msg)
    if e == "phone_in" and studying and load_config().get("phone_box_auto_lock", True):
        threading.Thread(target=lambda: (time.sleep(1.5), get_box().lock()), daemon=True).start()


def get_box() -> PhoneBoxLink:
    global _link
    if _link is None:
        _link = PhoneBoxLink(on_event=_default_event_handler)
    return _link


def start_phone_box() -> None:
    """Bật kết nối hộp nếu Admin đã bật tính năng (phone_box_enabled) và có pyserial."""
    if not load_config().get("phone_box_enabled", False):
        _diag("off", "Mô-đun ESP32 đang TẮT trên máy này → FocusDesk không mở cổng COM. Admin → 📦 Hộp điện thoại → "
                     "tick “Bật mô-đun ESP32” → Lưu.", every_s=0)
        return
    try:
        import serial  # noqa: F401
        if not hasattr(serial, "Serial"):
            raise ImportError(f"gói 'serial' ở {getattr(serial, '__file__', '?')} không phải pyserial")
    except Exception as exc:
        _diag("noserial", f"Chưa có pyserial dùng được ({exc}) — chạy KIEM_TRA.bat --sua hoặc "
                          "python -m pip uninstall -y serial && python -m pip install pyserial", every_s=0)
        return
    get_box().start()


def set_studying(on: bool) -> Dict[str, Any]:
    """App gọi khi vào / rời chế độ học sinh: vào học -> khóa hộp (nếu điện thoại đã ở trong)."""
    _session["studying"] = on
    box = get_box()
    if box.proto >= 2:          # tủ v2: không có khóa — ca học do tủ bắt đầu (đóng cửa + cắm sạc)
        return {"ok": True}
    if on and box.state.get("connected") and box.state.get("phone") and load_config().get("phone_box_auto_lock", True):
        return box.lock()
    return {"ok": True}
