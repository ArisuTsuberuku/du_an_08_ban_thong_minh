# -*- coding: utf-8 -*-
"""Kiểm thử giao thức hộp điện thoại với hộp giả lập (python tests/test_phone_box.py)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_phone_box import FakeBox, FakeTransport  # noqa: E402
from services import phone_box as pb  # noqa: E402

KEY = "a" * 32
CFG = {"esp32_com_port": "SIM", "phone_box_key": KEY, "phone_box_auto_lock": True}
pb.load_config = lambda: dict(CFG)
pb.update_runtime_state = lambda **kw: None
alerts = []
pb._alert = lambda m: alerts.append(m)


def wait(cond, s=3.0):
    t0 = time.time()
    while time.time() - t0 < s:
        if cond():
            return True
        time.sleep(0.05)
    return False


def make(key=KEY):
    box = FakeBox(key)
    link = pb.PhoneBoxLink(transport_factory=lambda port: FakeTransport(box), on_event=pb._default_event_handler)
    pb._link = link
    link.start()
    assert wait(lambda: link.state.get("connected")), "không kết nối được hộp giả lập"
    return box, link


def test_connect_and_state():
    box, link = make()
    assert link.state["box_id"] == "SIM01" and link.port == "SIM"
    link.stop()


def test_study_phone_in_auto_locks_and_alerts():
    alerts.clear()
    box, link = make()
    pb.set_studying(True)
    box.put_phone()
    assert wait(lambda: box.lock == "locked", 5), "vào học + cất điện thoại phải tự khóa"
    assert any("cất điện thoại" in a for a in alerts) and any("đã khóa" in a for a in alerts), alerts
    box.take_phone()
    assert wait(lambda: any("cạy" in a for a in alerts)), alerts
    pb.set_studying(False)
    link.stop()


def test_unlock_needs_valid_key_and_no_replay():
    box, link = make()
    assert link.unlock(600)["ok"] and box.lock == "unlocked"
    CFG["phone_box_key"] = "b" * 32          # khóa sai (ví dụ phần mềm khác cố mở hộp)
    r = link.lock()
    CFG["phone_box_key"] = KEY
    assert not r["ok"] and r.get("err") == "bad_mac", r
    # phát lại đúng lệnh cũ (nonce đã đổi) -> bị từ chối
    old = pb.make_mac(KEY, "lock", 999, "deadbeef")
    box.feed(b'{"t":"lock","id":999,"mac":"%s"}' % old.encode())
    assert box.lock == "unlocked"
    link.stop()


def test_pairing_only_in_pair_mode():
    box, link = make(key="")
    assert not link.pair()["ok"]
    box.pair_mode = True
    saved = {}
    pb.save_config = lambda c: saved.update(c)
    assert link.pair()["ok"] and box.key == saved.get("phone_box_key")
    link.stop()


def test_disconnect_detected():
    alerts.clear()
    box, link = make()
    pb.set_studying(True)
    pb.LOST_S = 1.0
    box.silent = True
    assert wait(lambda: not link.state.get("connected"), 4)
    assert any("Mất kết nối" in a for a in alerts), alerts
    pb.set_studying(False)
    link.stop()


def test_traffic_log_records_tx_rx():
    box, link = make()
    rows = pb.traffic_since(0)
    assert any(r[2] == "TX" and '"t":"hello"' in r[3] for r in rows), rows[-5:]
    assert any(r[2] == "RX" and "focusdesk-box" in r[3] for r in rows), rows[-5:]
    link.stop()


def test_old_firmware_hello_accepted():
    box = FakeBox(KEY)
    orig = box.feed

    def feed_old(line):
        if b'"hello"' in line:
            box._send({"t": "hello", "dev": "smartdesk-box", "fw": "old", "box_id": "OLD1", "nonce": box.nonce})
        else:
            orig(line)
    box.feed = feed_old
    link = pb.PhoneBoxLink(transport_factory=lambda port: FakeTransport(box))
    link.start()
    assert wait(lambda: link.state.get("connected")), "firmware cũ (smartdesk-box) phải vẫn kết nối được"
    link.stop()


def test_serial_monitor_raw_mode():
    box, link = make()
    link.suspend()
    assert wait(lambda: not link.state.get("connected")) and link.tr is None
    mon = pb.RawSerialMonitor("SIM", 115200, factory=lambda port, baud: FakeTransport(box))
    seq0 = pb.traffic_since(0)[-1][0]
    box.to_pc.put(b"ESP-ROM:esp32s3-20210327 boot log\n")           # dòng rác khi ESP32 khởi động
    mon.send_text('{"t":"get","id":1}')
    assert wait(lambda: mon.last_nonce == box.nonce), "phải đọc được nonce từ state"
    assert any(r[2] == "RX" and "boot log" in r[3] for r in pb.traffic_since(seq0)), "dòng không phải JSON vẫn phải hiện"
    mon.send_signed("unlock", 30)
    assert wait(lambda: box.lock == "unlocked")
    assert wait(lambda: mon.last_nonce == box.nonce), "nonce mới sau lệnh phải được cập nhật"
    mon.send_signed("lock")
    assert wait(lambda: box.lock == "locked"), "lệnh ký từ Serial monitor phải được hộp chấp nhận"
    mon.close()
    link.resume()
    assert wait(lambda: link.state.get("connected"), 8), "resume phải tự kết nối lại"
    link.stop()


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                fails += 1
                print("FAIL", name, exc)
    sys.exit(1 if fails else 0)
