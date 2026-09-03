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
# 2026-09-03 01:00 UTC = 北京时间 09:00（北京 = UTC+8，无 DST）
T = datetime(2026, 9, 3, 1, 0, 0, tzinfo=timezone.utc)
FAR = T + timedelta(days=10)  # 距 deadline > 24h


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


class TestDailySlot(unittest.TestCase):
    """距 deadline >24h：每天北京时间 09:00 一次，锚定 last_update 不漂移。"""

    def test_first_slot_boundary(self):
        last = T - DAY + timedelta(minutes=1)  # 昨天 09:01 跑过
        state = mk_state(last, FAR)
        d = scheduler.decide(state, T - timedelta(minutes=1))  # 今天 08:59
        self.assertFalse(d.due)
        self.assertEqual(d.reason, "daily")
        d = scheduler.decide(state, T)  # 今天 09:00
        self.assertTrue(d.due)
        self.assertEqual(d.next_due, iso(T))

    def test_slot_rolls_to_next_day_after_run(self):
        state = mk_state(T + timedelta(minutes=1), FAR)  # 今天 09:01 跑完
        d = scheduler.decide(state, T + DAY - timedelta(minutes=1))
        self.assertFalse(d.due)
        d = scheduler.decide(state, T + DAY)  # 次日 09:00
        self.assertTrue(d.due)
        self.assertEqual(d.next_due, iso(T + DAY))

    def test_last_exactly_on_slot_no_refire(self):
        state = mk_state(T, FAR)  # last_update 恰为 09:00:00
        d = scheduler.decide(state, T)
        self.assertFalse(d.due)
        self.assertEqual(d.next_due, iso(T + DAY))


class TestDeadlineTiers(unittest.TestCase):
    def test_hourly_within_24h(self):
        now = T
        deadline = now + timedelta(hours=12)
        state = mk_state(now - timedelta(minutes=59), deadline)
        self.assertFalse(scheduler.decide(state, now).due)
        state = mk_state(now - timedelta(minutes=61), deadline)
        d = scheduler.decide(state, now)
        self.assertTrue(d.due)
        self.assertEqual(d.reason, "hourly")

    def test_ten_minutes_within_1h(self):
        now = T
        deadline = now + timedelta(minutes=30)
        state = mk_state(now - timedelta(minutes=9), deadline)
        self.assertFalse(scheduler.decide(state, now).due)
        state = mk_state(now - timedelta(minutes=11), deadline)
        d = scheduler.decide(state, now)
        self.assertTrue(d.due)
        self.assertEqual(d.reason, "deadline_soon")


class TestPastDeadline(unittest.TestCase):
    def test_recovery_when_engine_never_succeeded_after_deadline(self):
        now = T
        deadline = now - timedelta(minutes=5)
        state = mk_state(now - timedelta(minutes=29), deadline)  # last < deadline
        d = scheduler.decide(state, now)
        self.assertFalse(d.due)
        self.assertEqual(d.reason, "recovery")
        state = mk_state(now - timedelta(minutes=31), deadline)
        d = scheduler.decide(state, now)
        self.assertTrue(d.due)
        self.assertEqual(d.reason, "recovery")

    def test_season_end_probe_when_engine_succeeded_after_deadline(self):
        last = T - timedelta(hours=1)  # 08:00 北京跑过（DDL 之后）
        deadline = T - timedelta(hours=2)
        state = mk_state(last, deadline)
        d = scheduler.decide(state, T)
        self.assertFalse(d.due)
        self.assertEqual(d.reason, "probe_season_end")
        # 探测点 = max(last+24h, 下一个北京 09:00) = last+24h
        d = scheduler.decide(state, last + timedelta(hours=24))
        self.assertTrue(d.due)


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
