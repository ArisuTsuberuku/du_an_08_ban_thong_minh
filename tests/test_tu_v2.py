# -*- coding: utf-8 -*-
"""Giao thức v2 (tủ điện thoại ESP32): ca học do tủ bắt đầu/kết thúc, vi phạm trong ca, im lặng sau ca, cfg, CFI,
reset key, tưới cây; cloud chấm ca "hết giờ" và đẩy cấu hình tủ qua heartbeat.  python tests/test_tu_v2.py"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "cloud"))

from fake_tu_v2 import FakeCabinet, FakeTransport  # noqa: E402
from services import phone_box as pb  # noqa: E402
from services import study_sessions as ss  # noqa: E402
import study  # noqa: E402

CFG = {"esp32_com_port": "SIM", "phone_box_enabled": True,
       "study_box_cfg": {"session_min": 45, "led_cfi": 55, "buzz_cfi": 25}}
STATE = {}
pb.load_config = lambda: dict(CFG)
pb.update_runtime_state = lambda **kw: None
pb.load_runtime_state = lambda: dict(STATE)
ss.load_runtime_state = lambda: dict(STATE)
ss.save_runtime_state = lambda st: (STATE.clear(), STATE.update(st))
ss.safe_log = lambda *_a, **_k: None
alerts = []
pb._alert = lambda m: alerts.append(m)


def wait(cond, s=3.0):
    t0 = time.time()
    while time.time() - t0 < s:
        if cond():
            return True
        time.sleep(0.05)
    return False


class TestCabinetV2(unittest.TestCase):
    def setUp(self):
        alerts.clear()
        STATE.clear()
        self.box = FakeCabinet()
        self.link = pb.PhoneBoxLink(transport_factory=lambda port: FakeTransport(self.box),
                                    on_event=pb._default_event_handler)
        pb._link = self.link
        self.link.start()
        self.assertTrue(wait(lambda: self.link.state.get("connected")), "không kết nối được tủ giả lập")

    def tearDown(self):
        self.link.stop()
        pb.set_test_cfi(None)

    def test_hello_proto2_and_cfg_pushed_on_connect(self):
        self.assertEqual(self.link.proto, 2)
        self.assertTrue(wait(lambda: self.box.cfg_seen))
        c = self.box.cfg_seen[-1]
        self.assertEqual((c["session_ms"], c["led_cfi"], c["buzz_cfi"]), (45 * 60000, 55, 25))
        self.assertNotIn("mac", c)                       # v2 không ký HMAC

    def test_full_session_door_charge_time_up(self):
        self.assertTrue(wait(lambda: self.box.cfg_seen))   # chờ cfg tự gửi lúc kết nối xong rồi mới đổi thời lượng
        self.box.session_ms = 60000
        self.box.plug(True)
        self.box.close_door()
        time.sleep(2.5)                                  # chưa ở chế độ Học sinh -> tủ CHƯA vào ca
        self.assertIsNone(ss.current())
        self.box.open_door()
        STATE["current_role"] = "student"                # vào chế độ Học sinh -> ping study=true
        self.assertTrue(wait(lambda: self.box.study, 4))
        self.assertIsNone(ss.current())                  # chưa đóng cửa -> chưa có ca
        self.box.close_door()
        self.assertTrue(wait(lambda: (ss.current() or {}).get("source") == "box"), "đóng cửa + sạc phải mở ca")
        self.assertTrue(any("bắt đầu ca học" in a for a in alerts), alerts)
        # trong ca: mở cửa = vi phạm + báo
        self.box.open_door()
        self.assertTrue(wait(lambda: (ss.current() or {}).get("violations", {}).get("PHONE") == 1))
        self.assertTrue(any("Cửa tủ" in a for a in alerts), alerts)
        self.box.close_door()
        self.box.fast_forward(61)
        self.assertTrue(wait(lambda: STATE.get("session_reports")), "hết giờ phải chốt ca")
        rep = STATE["session_reports"][-1]
        self.assertTrue(rep["time_up"])
        self.assertEqual(rep["source"], "box")
        self.assertGreaterEqual(rep["minutes"], 1.0)     # lấy thời lượng tủ đếm (dur_s), không phải giờ máy tính
        self.assertEqual(rep["violations"], {"PHONE": 1})
        # sau giờ học: mở cửa / rút sạc KHÔNG báo nữa
        n = len(alerts)
        self.box.open_door()
        self.box.plug(False)
        time.sleep(0.6)
        self.assertEqual(len(alerts), n, alerts[n:])
        self.assertIsNone(ss.current())

    def test_reset_key_busy_in_session_then_ok(self):
        STATE["current_role"] = "student"
        self.box.plug(True)
        self.box.close_door()
        self.assertTrue(wait(lambda: self.link.state.get("phase") == "active"))
        r = self.link.reset_key()
        self.assertFalse(r["ok"])
        self.assertIn("Đang trong ca", r["err"])
        self.assertTrue(self.link.end_session("admin")["ok"])
        self.assertTrue(wait(lambda: STATE.get("session_reports")))
        self.assertFalse(STATE["session_reports"][-1]["time_up"])
        self.assertEqual(STATE["session_reports"][-1]["end_reason"], "Kết thúc ca sớm")
        self.assertTrue(self.link.reset_key()["ok"])
        self.assertTrue(wait(lambda: any("reset khóa" in a for a in alerts)), alerts)

    def test_water_and_test_cfi_in_ping(self):
        self.assertTrue(self.link.water(1800)["ok"])
        self.assertEqual(self.box.pumped, [1800])
        pb.set_test_cfi(20, 30)
        self.assertTrue(wait(lambda: self.box.cfi == 20, 5), "ping phải mang CFI xuống tủ")
        pb.set_test_cfi(None)
        self.assertTrue(wait(lambda: self.box.cfi == -1, 5))   # không ở chế độ học sinh -> -1 (tủ tắt đèn/còi)

    def test_test_session_not_recorded(self):
        self.assertTrue(self.link.test_io()["ok"])
        self.assertEqual(self.box.io_count, 1)
        self.assertTrue(self.link.start_test(1)["ok"])
        self.assertTrue(wait(lambda: self.link.state.get("test") and self.link.state.get("phase") == "active"))
        self.assertIsNone(ss.current())                  # ca thử: không mở ca học trên máy
        self.box.close_door()
        self.box.open_door()                             # mở cửa trong ca thử: báo [Thử nghiệm], không ghi vi phạm
        self.assertTrue(wait(lambda: any("Thử nghiệm" in a and "Cửa tủ" in a for a in alerts)), alerts)
        self.box.fast_forward(61)
        self.assertTrue(wait(lambda: any("Hết ca thử" in a for a in alerts)), alerts)
        self.assertFalse(STATE.get("session_reports"))   # không gửi web chấm điểm / tưới cây
        self.assertTrue(wait(lambda: self.link.state.get("phase") == "idle"))   # cửa đang mở -> về chờ luôn

    def test_pair_from_parent_with_6_digit_code(self):
        self.assertTrue(wait(lambda: self.link.state.get("key_fp") == "482913"))
        self.assertTrue(self.link.reset_key(expect="135790")["ok"])
        self.assertEqual(self.box.expect, "135790")
        self.box.phone_offers_key("111222")                  # máy lạ -> không ghép
        self.assertTrue(wait(lambda: any("111 222" in a and "KHÁC" in a for a in alerts)), alerts)
        self.assertFalse(self.box.paired)
        self.box.phone_offers_key("135790")                  # đúng máy phụ huynh nhập
        self.assertTrue(wait(lambda: any("đã ghép điện thoại mã 135 790" in a for a in alerts)), alerts)
        self.assertTrue(wait(lambda: self.link.state.get("key_fp") == "135790"))
        self.assertEqual(self.link.reset_key(expect="12ab")["ok"], True)   # mã sai định dạng -> bỏ qua expect
        self.assertEqual(self.box.expect, "")

    def test_set_studying_does_not_lock_v2(self):
        self.assertEqual(pb.set_studying(True), {"ok": True})
        pb.set_studying(False)


class TestCloudV2(unittest.TestCase):
    def test_cfg_thresholds_and_box_view(self):
        c = study.clean_cfg({"min_minutes": 40, "led_cfi": 45, "buzz_cfi": 70})
        self.assertEqual(c["buzz_cfi"], 45)                     # còi không cao hơn đèn
        self.assertEqual(study.box_cfg(c, 7), {"ver": 7, "session_min": 40, "led_cfi": 45, "buzz_cfi": 45})

    def test_time_up_tolerance(self):
        c = study.clean_cfg({"min_minutes": 25})
        self.assertTrue(study.evaluate({"minutes": 24.95, "violations": {}, "time_up": True}, c)[0])
        self.assertFalse(study.evaluate({"minutes": 24.95, "violations": {}}, c)[0])
        self.assertFalse(study.evaluate({"minutes": 25, "violations": {"PHONE": 1}, "time_up": True}, c)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
