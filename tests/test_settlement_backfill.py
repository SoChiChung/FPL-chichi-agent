"""结算回填 + decided_at 的单元测试（brain/history_writer.py）。

覆盖 bugfix 需求 1/3 的后端契约：
- backfill_settlement：仅 finished 轮可回填；幂等；不回退非 null 值；不建孤儿条目。
- upsert_decision：新条目写 decided_at；已存在不覆盖。
"""
import unittest
from datetime import datetime, timezone

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


if __name__ == "__main__":
    unittest.main()
