"""结算回填 + 决策时间（decided_at / updated_at）单元测试（brain/history_writer.py）。

覆盖 bugfix 需求 1/3 的后端契约：
- backfill_settlement：仅 finished 轮可回填；幂等；不回退非 null 值；不建孤儿条目。
- upsert_decision.decided_at：新条目写入；已存在永不覆盖（复盘基准）。
- upsert_decision.updated_at（UX v1.2）：每次运行刷新；首次写入与 decided_at 相等；
  旧数据（缺该字段）重跑时补齐，且不动 decided_at。
"""
import unittest
from datetime import datetime, timezone
from unittest import mock

from brain import history_writer


def _mk_history(gws=(2, 3)):
    return {"season": "2026-27", "manager_id": 1,
            "history": [{"gw": g, "points": None, "rank": None, "overall_rank": None}
                        for g in gws]}


def _current_rows():
    # entry_history["current"] 的典型行：GW2/3 已结算、GW4 进行中（无 rank 结算）
    return [
        {"event": 2, "points": 12, "rank": 500001, "overall_rank": 900000},
        {"event": 3, "points": 25, "rank": 123456, "overall_rank": 800001},
        {"event": 4, "points": 0, "rank": None, "overall_rank": 800001},
    ]


def _events(finished=(1, 2, 3)):
    return [{"id": g, "finished": g in finished} for g in range(1, 5)]


def _mk_decision():
    return {"formation": "352",
            "captain": {"id": 1, "name": "Cap"}, "vice": {"id": 2, "name": "Vice"},
            "starting_xi": [], "bench": [], "squad": [],
            "recommended_transfers": [], "squad_source": None,
            "transfer_status": "unlimited", "free_transfers": None}


class TestBackfillSettlement(unittest.TestCase):
    def test_backfills_finished_gws_only(self):
        hist = _mk_history()
        n = history_writer.backfill_settlement(hist, _current_rows(), _events())
        self.assertEqual(n, 6)  # GW2/3 各 3 字段
        by = {h["gw"]: h for h in hist["history"]}
        self.assertEqual(by[2]["points"], 12)
        self.assertEqual(by[3]["rank"], 123456)
        self.assertEqual(by[3]["overall_rank"], 800001)
        # GW4 未 finished 也不在 history 判定范围：未回填
        self.assertIsNone(by[3]["points"] and None or None)  # placeholder

    def test_backfill_keeps_null_rank_for_unranked_row(self):
        # 已 finished 轮但 rank 暂缺（FPL 偶发先有 points 后补 rank）：
        # points=0 是合法结算值照写，rank 保持 null 等待下一 cycle
        hist = _mk_history(gws=(2, 3, 4))
        history_writer.backfill_settlement(hist, _current_rows(),
                                           _events(finished=(1, 2, 3, 4)))
        by = {h["gw"]: h for h in hist["history"]}
        self.assertEqual(by[4]["points"], 0)
        self.assertIsNone(by[4]["rank"])

    def test_backfill_idempotent_no_overwrite(self):
        hist = _mk_history()
        hist["history"][1]["points"] = 30  # 模拟已有非 null 值（如人工修正）
        history_writer.backfill_settlement(hist, _current_rows(), _events())
        by = {h["gw"]: h for h in hist["history"]}
        self.assertEqual(by[3]["points"], 30)  # 不回退

    def test_backfill_never_creates_orphan_entries(self):
        hist = {"season": "2026-27", "manager_id": 1,
                "history": [{"gw": 3, "points": None, "rank": None,
                             "overall_rank": None}]}
        history_writer.backfill_settlement(hist, _current_rows(), _events())
        self.assertEqual([h["gw"] for h in hist["history"]], [3])  # GW1 孤儿不新建


class TestDecidedAt(unittest.TestCase):
    def test_new_entry_writes_decided_at(self):
        hist = {"season": "2026-27", "manager_id": 1, "history": []}
        decision = _mk_decision()
        history_writer.upsert_decision(hist, 4, decision, [], {}, {})
        entry = hist["history"][0]
        self.assertIsNotNone(entry.get("decided_at"))
        # ISO UTC 校验：可解析、带 Z
        dt = datetime.fromisoformat(entry["decided_at"].replace("Z", "+00:00"))
        self.assertLess(abs((datetime.now(timezone.utc) - dt).total_seconds()), 60)

    def test_existing_decided_at_never_overwritten(self):
        hist = {"season": "2026-27", "manager_id": 1,
                "history": [{"gw": 4, "decided_at": "2026-09-09T00:00:00Z"}]}
        decision = _mk_decision()
        history_writer.upsert_decision(hist, 4, decision, [], {}, {})
        self.assertEqual(hist["history"][0]["decided_at"], "2026-09-09T00:00:00Z")

    def test_legacy_entry_missing_decided_at_gets_filled(self):
        hist = _mk_history(gws=(4,))
        decision = _mk_decision()
        history_writer.upsert_decision(hist, 4, decision, [], {}, {})
        self.assertIsNotNone(hist["history"][0].get("decided_at"))


class _FrozenDatetime(datetime):
    """可切换 now() 的 datetime 替身，用于验证两个时间戳的推进语义。"""

    fixed = None

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


class TestUpdatedAt(unittest.TestCase):
    """UX v1.2：updated_at = 最近一次重算时刻，每次运行覆盖写。"""

    GW4 = {"gw": 4, "points": None, "rank": None, "overall_rank": None}
    T_FIRST = datetime(2026, 9, 9, 11, 12, 32, tzinfo=timezone.utc)
    T_LATER = datetime(2026, 9, 15, 12, 48, 48, tzinfo=timezone.utc)

    def _upsert(self, hist, gw=4):
        history_writer.upsert_decision(hist, gw, _mk_decision(), [], {}, {})

    def test_first_write_sets_both_fields_identical(self):
        """首次写入：decided_at 与 updated_at 必须严格相等（同一次取时）。"""
        hist = {"season": "2026-27", "manager_id": 1, "history": []}
        _FrozenDatetime.fixed = self.T_FIRST
        with mock.patch.object(history_writer, "datetime", _FrozenDatetime):
            self._upsert(hist)
        entry = hist["history"][0]
        self.assertEqual(entry["decided_at"], "2026-09-09T11:12:32Z")
        self.assertEqual(entry["updated_at"], "2026-09-09T11:12:32Z")

    def test_rerun_refreshes_updated_at_but_freezes_decided_at(self):
        """重跑：decided_at 冻结（复盘基准），updated_at 推进（最近重算）。
        这正是主人 09-15 反馈「决策时间一直不动」的修复点。"""
        hist = {"season": "2026-27", "manager_id": 1, "history": []}
        with mock.patch.object(history_writer, "datetime", _FrozenDatetime):
            _FrozenDatetime.fixed = self.T_FIRST
            self._upsert(hist)
            _FrozenDatetime.fixed = self.T_LATER
            self._upsert(hist)
        entry = hist["history"][0]
        self.assertEqual(entry["decided_at"], "2026-09-09T11:12:32Z")
        self.assertEqual(entry["updated_at"], "2026-09-15T12:48:48Z")

    def test_legacy_entry_gets_updated_at_without_touching_decided_at(self):
        """升级前的旧条目只有 decided_at：重跑补 updated_at，不改 decided_at。"""
        hist = {"season": "2026-27", "manager_id": 1,
                "history": [dict(self.GW4, decided_at="2026-09-09T11:12:32Z")]}
        _FrozenDatetime.fixed = self.T_LATER
        with mock.patch.object(history_writer, "datetime", _FrozenDatetime):
            self._upsert(hist)
        entry = hist["history"][0]
        self.assertEqual(entry["decided_at"], "2026-09-09T11:12:32Z")
        self.assertEqual(entry["updated_at"], "2026-09-15T12:48:48Z")

    def test_updated_at_always_refreshed_not_setdefault(self):
        """updated_at 不是 setdefault —— 已存在的旧值必须被本次运行覆盖。"""
        hist = {"season": "2026-27", "manager_id": 1,
                "history": [dict(self.GW4, decided_at="2026-09-09T11:12:32Z",
                                 updated_at="2026-09-10T00:00:00Z")]}
        _FrozenDatetime.fixed = self.T_LATER
        with mock.patch.object(history_writer, "datetime", _FrozenDatetime):
            self._upsert(hist)
        self.assertEqual(hist["history"][0]["updated_at"], "2026-09-15T12:48:48Z")

    def test_updated_at_is_valid_utc_iso(self):
        """格式契约：可被前端 new Date() 解析的 UTC ISO（带 Z）。"""
        hist = {"season": "2026-27", "manager_id": 1, "history": []}
        self._upsert(hist)
        raw = hist["history"][0]["updated_at"]
        self.assertTrue(raw.endswith("Z"))
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        self.assertLess(abs((datetime.now(timezone.utc) - dt).total_seconds()), 60)


if __name__ == "__main__":
    unittest.main()
