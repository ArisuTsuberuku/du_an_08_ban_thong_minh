# -*- coding: utf-8 -*-
"""Ca học thành công: chấm điểm, chuỗi ngày, đổi quà, CSV (cloud/study.py) + vòng đời ca trên máy (services/study_sessions.py)."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "cloud"))

import study  # noqa: E402


class TestStudyCloud(unittest.TestCase):
    def setUp(self):
        self.cfg = study.clean_cfg({})

    def test_success_and_points(self):
        ok, why, pts = study.evaluate({"minutes": 30, "violations": {}, "focus": 80}, self.cfg)
        self.assertTrue(ok)
        self.assertEqual(why, [])
        self.assertEqual(pts, 10)
        ok, _w, pts = study.evaluate({"minutes": 80, "violations": {}, "focus": None}, self.cfg)  # 3 khối 25'
        self.assertTrue(ok)
        self.assertEqual(pts, 30)
        ok, _w, pts = study.evaluate({"minutes": 500, "violations": {}}, self.cfg)                 # tối đa 3 khối
        self.assertEqual(pts, 30)

    def test_fail_reasons(self):
        ok, why, pts = study.evaluate({"minutes": 12, "violations": {"GAME": 2}, "focus": 40}, self.cfg)
        self.assertFalse(ok)
        self.assertEqual(pts, 0)
        self.assertEqual(len(why), 3)
        self.assertIn("12/25", why[0])
        self.assertIn("game", why[1])

    def test_focus_ignored_without_camera(self):
        ok, _w, _p = study.evaluate({"minutes": 26, "violations": {}, "focus": None}, self.cfg)
        self.assertTrue(ok)

    def test_clean_cfg_limits(self):
        c = study.clean_cfg({"min_minutes": 1, "max_violations": -3, "min_focus": 300, "webhook_url": "http://x",
                             "rewards": [{"name": " Kem ", "points": "20"}, {"name": ""}, "rác"]})
        self.assertEqual((c["min_minutes"], c["max_violations"], c["min_focus"]), (5, 0, 100))
        self.assertEqual(c["webhook_url"], "")
        self.assertEqual(c["rewards"], [{"name": "Kem", "points": 20}])

    def test_streak(self):
        st = study.update_stats({}, "2026-11-01", "2026-10-31", 10, 30)
        st = study.update_stats(st, "2026-11-01", "2026-10-31", 10, 30)   # cùng ngày: chuỗi giữ nguyên
        self.assertEqual((st["streak"], st["points"], st["total_success"]), (1, 20, 2))
        st = study.update_stats(st, "2026-11-02", "2026-11-01", 10, 30)
        self.assertEqual(st["streak"], 2)
        st = study.update_stats(st, "2026-11-05", "2026-11-04", 10, 30)   # đứt chuỗi
        self.assertEqual((st["streak"], st["best_streak"]), (1, 2))
        self.assertEqual(study.live_streak(st, "2026-11-07", "2026-11-06"), 0)

    def test_redeem(self):
        cfg = study.clean_cfg({"rewards": [{"name": "Kem", "points": 30}]})
        new, msg = study.redeem({"points": 20}, cfg, 0, 1)
        self.assertIsNone(new)
        new, msg = study.redeem({"points": 45}, cfg, 0, 1)
        self.assertEqual(new["points"], 15)
        self.assertEqual(new["redeemed"][0]["name"], "Kem")

    def test_csv(self):
        recs = {"0000000100-a": {"start": 100, "end": 1900, "minutes": 30, "violations": {"GAME": 1},
                                 "success": False, "reasons": ["1 vi phạm"], "points": 0}}
        out = study.to_csv(recs, {"student_code": "HS01", "student_name": "An", "class_name": "8A"}, 7 * 3600)
        self.assertTrue(out.startswith("﻿ngay,"))
        self.assertIn("HS01,An,8A", out)

    def test_focus_ratio_two_thirds(self):
        self.assertEqual(self.cfg["min_focus"], 67)
        self.assertTrue(self.cfg["water_on_success"])
        ok, _w, _p = study.evaluate({"minutes": 30, "violations": {}, "focus_ratio": 70, "focus": 50}, self.cfg)
        self.assertTrue(ok)                                     # xét % thời gian tập trung, không xét điểm TB
        ok, why, _p = study.evaluate({"minutes": 30, "violations": {}, "focus_ratio": 60}, self.cfg)
        self.assertFalse(ok)
        self.assertIn("thời gian", why[0])
        c = study.clean_cfg({"min_focus": 80, "water_on_success": False, "water_seconds": 99})
        self.assertEqual((c["min_focus"], c["water_on_success"], c["water_seconds"]), (80, False, 10.0))

    def test_badge(self):
        self.assertEqual(study.badge(3), "")
        self.assertIn("Vàng", study.badge(25))


class TestStudyClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["SMART_DESK_APP_DIR"] = self.tmp
        from core import config_manager
        self.cm = config_manager
        self._orig = (config_manager.load_runtime_state, config_manager.save_runtime_state)
        self.state = {}
        import services.study_sessions as ss
        self.ss = ss
        ss.load_runtime_state = lambda: dict(self.state)
        ss.save_runtime_state = lambda st: (self.state.clear(), self.state.update(st))

    def test_lifecycle(self):
        ss = self.ss
        ss.start_session(ai_seconds=60, phone_in_box=True)
        ss.note_violation("GAME")
        ss.note_violation("POWER")            # không phải vi phạm
        ss.tick(cfi=80, ai_seconds=180)
        ss.tick(cfi=60, ai_seconds=240)
        ss.tick(on_break=True, dt=30)
        s = ss.current()
        s["start"] -= 1800
        self.state["study_session"] = s
        rep = ss.end_session("test")
        self.assertEqual(rep["violations"], {"GAME": 1})
        self.assertEqual(rep["focus"], 70.0)
        self.assertEqual(rep["ai_min"], 3.0)
        self.assertAlmostEqual(rep["minutes"], 29.5, delta=0.2)
        self.assertTrue(ss.has_pending())
        q = ss.pop_reports()
        self.assertEqual(len(q), 1)
        self.assertFalse(ss.has_pending())
        ss.requeue(q)
        self.assertTrue(ss.has_pending())
        self.assertIsNone(ss.end_session("không có ca"))

    def test_focus_ratio_client(self):
        ss = self.ss
        ss.start_session()
        for cfi in [90] * 10 + [30] * 5:                       # 50 s tập trung / 75 s quan sát
            ss.tick(cfi=cfi, dt=5)
        ss.tick(on_break=True, dt=30)                          # nghỉ vệ sinh không tính
        rep = ss.end_session("test")
        self.assertAlmostEqual(rep["focus_ratio"], 66.7, delta=0.1)

    def test_stale_session_closed_at_last_tick(self):
        ss = self.ss
        ss.start_session()
        s = ss.current()
        s["start"], s["last"] = time.time() - 7200, time.time() - 3600
        self.state["study_session"] = s
        rep = ss.close_stale()
        self.assertAlmostEqual(rep["minutes"], 60, delta=0.5)


if __name__ == "__main__":
    unittest.main()
