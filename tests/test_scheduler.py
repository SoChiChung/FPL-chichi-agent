"""scheduler 闸门决策验证（仅标准库 unittest + 本地合成数据）。

运行：python -m unittest tests.test_scheduler -v
不访问网络、不写真实 data/ 文件。
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import scheduler

DAY = timedelta(days=1)
T = datetime(2026, 9, 3, 1, 0, 0, tzinfo=timezone.utc)
FAR = T + timedelta(days=10)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def mk_state(last, deadline):
    state = {"last_update": iso(last).replace("+00:00", "Z")}
    if deadline is not None:
        state["next_deadline"] = iso(deadline).replace("+00:00", "Z")
    else:
        state["next_deadline"] = ""  # context.py 无事件时的写法
    return state


class TestStateFallbacks(unittest.TestCase):
    def test_no_state(self):
        self.assertEqual(scheduler.decide(None, T).reason, "no_state")
        self.assertEqual(scheduler.decide({}, T).reason, "no_state")
        self.assertEqual(scheduler.decide([], T).reason, "no_state")
        self.assertTrue(scheduler.decide(None, T).due)

    def test_never_updated(self):
        state = {"next_deadline": iso(FAR)}
        self.assertEqual(scheduler.decide(state, T).reason, "never_updated")
        self.assertTrue(scheduler.decide(state, T).due)
        bad = mk_state(T, FAR)
        bad["last_update"] = "not-a-date"
        self.assertTrue(scheduler.decide(bad, T).due)


class TestEvery30Min(unittest.TestCase):
    """赛季中（有 deadline）：统一每 30 分钟，不再按 deadline 远近分档。"""

    def test_not_due_within_30min(self):
        for delta in [timedelta(minutes=0), timedelta(minutes=29)]:
            state = mk_state(T - delta, FAR)
            self.assertFalse(scheduler.decide(state, T).due)

    def test_due_after_30min(self):
        state = mk_state(T - timedelta(minutes=31), FAR)
        d = scheduler.decide(state, T)
        self.assertTrue(d.due)
        self.assertEqual(d.reason, "every_30min")

    def test_same_cadence_regardless_of_deadline_distance(self):
        # deadline 前 10 分钟、前 12 小时、已过 5 分钟，节奏都应一致（统一 30min）
        now = T
        for deadline in [now + timedelta(minutes=10),
                         now + timedelta(hours=12),
                         now - timedelta(minutes=5)]:
            self.assertFalse(scheduler.decide(
                mk_state(now - timedelta(minutes=29), deadline), now).due)
            self.assertTrue(scheduler.decide(
                mk_state(now - timedelta(minutes=31), deadline), now).due)


class TestNoDeadline(unittest.TestCase):
    def test_off_season_probe(self):
        state = mk_state(T - timedelta(hours=2), None)
        d = scheduler.decide(state, T)
        self.assertFalse(d.due)
        self.assertEqual(d.reason, "probe")
        d = scheduler.decide(state, T - timedelta(hours=2) + timedelta(hours=24))
        self.assertTrue(d.due)


if __name__ == "__main__":
    unittest.main()
