"""Transfer Engine v2 预算验收测试（仅标准库 unittest + 本地合成数据，无网络）。

覆盖 Transfer Engine 修复需求：
  - Case 1：Bank £2.0m + 卖出 £6.0m + £4.5m = £12.5m < Haaland £15.0m
            → 绝不推荐 Haaland（预算校验丢弃买不起的候选）。
  - Case 2：Bank £5.0m + 卖出 £8.0m = £13.0m ≥ £12.5m → 允许推荐。
  - Case 3：所有推荐整包自动预算/同队/位置结构校验，100% 真实可执行。
  - bank 单位：entry_history.bank=20（FPL 0.1M）→ state.bank 应为 2.0（£m）。
  - history 顶层 budget_before / budget_after / transfer_cost 落盘。

运行：python -m unittest tests.test_transfer_budget -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import context, data_store, history_writer, transfer

CFG = {
    "market_gap_threshold": 15,
    "injury_threshold": 75,
    "max_players_per_team": 3,
    "max_free_transfers": 5,
}
TS = lambda n: {"status": "limited", "free_transfers": n}


def mk_player(pid, name, pos, team, price, market, chance=None, status="a"):
    return {
        "id": pid, "name": name, "pos": pos, "team": team, "price": float(price),
        "market_score": float(market), "chance_of_playing_next_round": chance,
        "status": status,
    }


def run(squad, pool, bank, free=5, cfg=None):
    """调用引擎：squad 为在册 15 人列表；pool 为额外换入池。返回 suggestions/notes。"""
    players = {p["id"]: p for p in squad}
    for p in pool:
        players[p["id"]] = p
    scores = {p["id"]: float(p["market_score"]) for p in players.values()}
    return transfer.evaluate_transfers(
        squad, players, scores, cfg or CFG, bank, TS(free))


def assert_feasible(testcase, suggestions, bank):
    """整包预算必然可行：Σin ≤ bank + Σout（Case 3 要求）。"""
    out_sum = sum(s["out"]["price"] for s in suggestions)
    in_sum = sum(s["in"]["price"] for s in suggestions)
    testcase.assertLessEqual(in_sum, bank + out_sum + 1e-9)
    for s in suggestions:
        testcase.assertEqual(s["in"]["pos"], s["out"]["pos"])
        testcase.assertNotEqual(s["in"]["id"], s["out"]["id"])
        # reason 与 market_gap 结构完整
        testcase.assertTrue(s["reason"])
        testcase.assertGreater(s["market_gap"], 0)
    pkg = transfer.summarize_package(bank, suggestions)
    testcase.assertAlmostEqual(pkg["total_out_price"], round(out_sum, 1), places=1)
    testcase.assertAlmostEqual(pkg["total_in_price"], round(in_sum, 1), places=1)
    testcase.assertAlmostEqual(pkg["budget_after"], round(bank + out_sum - in_sum, 1), places=1)
    testcase.assertGreaterEqual(pkg["budget_after"], -1e-9)


class TestCase1CannotBuyHaaland(unittest.TestCase):
    """用户报告场景：Bank £2.0m，卖 Calvert-Lewin £6.0m + Sangaré £4.5m，
    合计 £12.5m < Haaland £15.0m → 不得推荐 Haaland。"""

    def _scenario(self):
        # 阵型无关紧要，引擎按位置池匹配；为隔离预算，其余球员设高分不动
        squad = [
            mk_player(1, "A", "FWD", "EVE", 6.0, 20.0),    # Calvert-Lewin 位
            mk_player(2, "B", "FWD", "CRY", 4.5, 15.0),    # 低价前锋
            mk_player(3, "C", "FWD", "ARS", 5.5, 80.0),    # 高分不动
            mk_player(4, "D", "MID", "CHE", 9.0, 80.0),    # 高分不动
            mk_player(5, "E", "MID", "LIV", 6.0, 60.0),
            mk_player(6, "F", "MID", "MCI", 6.0, 60.0),
            mk_player(7, "G", "MID", "TOT", 5.0, 55.0),
            mk_player(8, "H", "MID", "NEW", 5.0, 55.0),
            mk_player(9, "I", "DEF", "AVL", 5.0, 55.0),
            mk_player(10, "J", "DEF", "BHA", 5.0, 55.0),
            mk_player(11, "K", "DEF", "EVE", 5.0, 55.0),
            mk_player(12, "L", "DEF", "FUL", 5.0, 55.0),
            mk_player(13, "M", "DEF", "BRE", 5.0, 55.0),
            mk_player(14, "N", "GKP", "BOU", 4.0, 55.0),
            mk_player(15, "O", "GKP", "WHU", 4.0, 55.0),
        ]
        pool = [
            mk_player(900, "Haaland", "FWD", "MCI", 15.0, 90.0),   # 买不起
            mk_player(901, "BudgetFwd", "FWD", "SOU", 5.5, 45.0),  # 买得起
        ]
        return squad, pool

    def test_haaland_never_recommended(self):
        squad, pool = self._scenario()
        suggestions, _ = run(squad, pool, bank=2.0, free=5)
        in_names = {s["in"]["name"] for s in suggestions}
        self.assertNotIn("Haaland", in_names,
                         "Bank2.0+卖6.0+4.5=12.5 < 15.0，推荐 Haaland 即预算 bug")
        assert_feasible(self, suggestions, bank=2.0)
        # 若最优包非空，必然走便宜的 BudgetFwd
        for s in suggestions:
            if s["out"]["id"] in (1, 2):
                self.assertLessEqual(s["in"]["price"], 5.5 + 1e-9)

    def test_even_with_many_free_transfers_haaland_banned(self):
        squad, pool = self._scenario()
        suggestions, _ = run(squad, pool, bank=2.0, free=5)
        self.assertNotIn(900, {s["in"]["id"] for s in suggestions})

    def test_notes_reason_when_budget_gap(self):
        # 把便宜候选也去掉 → 只能“放弃转会”并输出预算缺口说明（可调试）
        squad, pool = self._scenario()
        pool = [mk_player(900, "Haaland", "FWD", "MCI", 15.0, 90.0)]
        _, notes = run(squad, pool, bank=2.0, free=5)
        detail = next((n["detail"] for n in notes if n["topic"] == "no_transfer"), "")
        self.assertTrue(detail, "预算不足时应给出 no_transfer 说明")
        self.assertIn("预算不足", detail)
        self.assertIn("Bank £2.0m", detail)     # before bank 原样出现在说明里
        self.assertIn("缺口", detail)           # 给出“还差多少钱”的可调试信息


class TestCase2AffordableAllows(unittest.TestCase):
    """Bank £5.0m + 卖出 £8.0m = £13.0m ≥ £12.5m → 允许推荐。"""

    def _scenario(self):
        squad = [
            mk_player(1, "X", "FWD", "EVE", 8.0, 20.0),     # 待卖（高潜替代触发）
            mk_player(2, "K1", "GKP", "BOU", 4.0, 55.0),
            mk_player(3, "K2", "GKP", "WHU", 4.0, 55.0),
            mk_player(4, "D1", "DEF", "AVL", 5.0, 55.0),
            mk_player(5, "D2", "DEF", "BHA", 5.0, 55.0),
            mk_player(6, "D3", "DEF", "FUL", 5.0, 55.0),
            mk_player(7, "D4", "DEF", "BRE", 5.0, 55.0),
            mk_player(8, "D5", "DEF", "CRY", 5.0, 55.0),
            mk_player(9, "M1", "MID", "CHE", 7.0, 55.0),
            mk_player(10, "M2", "MID", "LIV", 7.0, 55.0),
            mk_player(11, "M3", "MID", "MCI", 6.0, 55.0),
            mk_player(12, "M4", "MID", "TOT", 6.0, 55.0),
            mk_player(13, "M5", "MID", "NEW", 6.0, 55.0),
            mk_player(14, "F2", "FWD", "ARS", 6.0, 55.0),
            mk_player(15, "F3", "FWD", "SOU", 5.0, 55.0),
        ]
        pool = [mk_player(800, "AffordableStar", "FWD", "LIV", 12.5, 62.0)]  # 13.0 ≥ 12.5
        return squad, pool

    def test_affordable_star_allowed(self):
        squad, pool = self._scenario()
        suggestions, _ = run(squad, pool, bank=5.0, free=1)
        self.assertEqual([s["in"]["id"] for s in suggestions], [800])
        assert_feasible(self, suggestions, bank=5.0)
        # 精确执行预算：5.0 + 8.0 − 12.5 = 0.5
        pkg = transfer.summarize_package(5.0, suggestions)
        self.assertAlmostEqual(pkg["budget_after"], 0.5, places=1)
        self.assertAlmostEqual(pkg["transfer_cost"], 4.5, places=1)  # 12.5 − 8.0


class TestCase3PackageIntegrity(unittest.TestCase):
    """多笔包：预算、同队 ≤3、in 互斥、同位置结构全部自动满足。"""

    def _scenario(self):
        # T1 已有 3 名球员全部留队 → 禁止再进 T1 球员；引擎应绕开诱惑候选
        squad = [
            mk_player(1, "F_A", "FWD", "MUN", 5.0, 10.0),     # 待卖
            mk_player(2, "F_B", "FWD", "CHE", 5.0, 12.0),     # 待卖
            mk_player(3, "F_C", "FWD", "T1", 5.5, 70.0),      # T1 高分留队
            mk_player(4, "T1a", "DEF", "T1", 5.0, 65.0),      # T1
            mk_player(5, "T1b", "MID", "T1", 6.0, 65.0),      # T1（凑满 3 人）
            mk_player(6, "D2", "DEF", "FUL", 5.0, 55.0),
            mk_player(7, "D3", "DEF", "BRE", 5.0, 55.0),
            mk_player(8, "D4", "DEF", "CRY", 5.0, 55.0),
            mk_player(9, "M1", "MID", "EVE", 6.0, 50.0),
            mk_player(10, "M2", "MID", "AVL", 6.0, 50.0),
            mk_player(11, "M3", "MID", "BHA", 6.0, 50.0),
            mk_player(12, "M4", "MID", "SOU", 6.0, 50.0),
            mk_player(13, "G1", "GKP", "BOU", 4.0, 55.0),
            mk_player(14, "G2", "GKP", "WHU", 4.0, 55.0),
            mk_player(15, "M5", "MID", "NFO", 5.0, 50.0),
        ]
        pool = [
            mk_player(700, "TemptingT1", "FWD", "T1", 6.0, 88.0),   # 同队诱惑 → 禁
            mk_player(701, "FairA", "FWD", "LIV", 5.0, 60.0),
            mk_player(702, "FairB", "FWD", "ARS", 5.0, 62.0),
        ]
        return squad, pool

    def test_package_valid_with_team_and_budget(self):
        squad, pool = self._scenario()
        suggestions, _ = run(squad, pool, bank=0.5, free=2)
        self.assertTrue(suggestions, "应能产出至少一笔可执行建议")
        assert_feasible(self, suggestions, bank=0.5)
        self.assertNotIn(700, {s["in"]["id"] for s in suggestions})   # 同队诱惑被排除
        # in 互斥 & out 互斥
        in_ids = [s["in"]["id"] for s in suggestions]
        out_ids = [s["out"]["id"] for s in suggestions]
        self.assertEqual(len(in_ids), len(set(in_ids)))
        self.assertEqual(len(out_ids), len(set(out_ids)))
        # 免费转会上限
        self.assertLessEqual(len(suggestions), 2)
        # 精确预算：卖 5.0+5.0、买 5.0+5.0 → after = 0.5
        pkg = transfer.summarize_package(0.5, suggestions)
        self.assertAlmostEqual(pkg["budget_after"], 0.5, places=1)

    def test_free_transfer_limit_respected_in_package(self):
        squad, pool = self._scenario()
        suggestions, _ = run(squad, pool, bank=0.5, free=1)
        self.assertLessEqual(len(suggestions), 1)
        assert_feasible(self, suggestions, bank=0.5)


class TestBankUnit(unittest.TestCase):
    """FPL API bank=20（0.1m）→ state.bank 必须归一为 2.0（£m）。"""

    def _state(self, bank):
        bootstrap = {
            "teams": [], "element_types": [], "elements": [],
            "events": [
                {"id": 1, "finished": True, "deadline_time": "2026-01-01T00:00:00Z"},
                {"id": 2, "finished": False, "deadline_time": "2999-01-01T00:00:00Z"},
            ],
        }
        entry = {"name": "t", "summary_overall_points": 10, "summary_overall_rank": 9}
        entry_history = {"current": [{"event": 1, "bank": bank}]}
        picks = {"picks": [], "entry_history": {"bank": bank}}
        return context.build_state(bootstrap, entry, entry_history, picks, gw=2)

    def test_bank_divided_by_ten(self):
        state = self._state(20)      # 官方口径：20 = £2.0m
        self.assertEqual(state["bank"], 2.0)
        state2 = self._state(5)      # 5 = £0.5m
        self.assertEqual(state2["bank"], 0.5)

    def test_bank_fallback_from_history(self):
        # picks 无 entry_history 时回退 entry_history current 最近完成轮
        bootstrap = {
            "teams": [], "element_types": [], "elements": [],
            "events": [
                {"id": 1, "finished": True, "deadline_time": "2026-01-01T00:00:00Z"},
                {"id": 2, "finished": False, "deadline_time": "2999-01-01T00:00:00Z"},
            ],
        }
        state = context.build_state(bootstrap, {"name": "t"}, {"current": [{"event": 1, "bank": 30}]},
                                    {"picks": []}, gw=2)
        self.assertEqual(state["bank"], 3.0)

    def test_validate_state_rejects_unnormalized_bank(self):
        # 防回归：0.1m 原始整数若漏除（如 500 = £50m+）应被 schema 拦下
        with self.assertRaises(ValueError):
            data_store.validate_state({"manager_id": 1, "season": "s", "current_gw": 2,
                                       "points": 0, "rank": 0, "team": [], "bank": 500})
        # 正常 £m 值放行
        data_store.validate_state({"manager_id": 1, "season": "s", "current_gw": 2,
                                   "points": 0, "rank": 0, "team": [], "bank": 2.0})


class TestHistoryBudgetFields(unittest.TestCase):
    """history 条目顶层应平铺 budget_before / budget_after / transfer_cost。"""

    def _decision(self, pkg):
        return {
            "formation": "343", "captain": {"id": 1, "name": "A"},
            "vice": {"id": 2, "name": "B"},
            "starting_xi": [], "bench": [], "squad": [],
            "recommended_transfers": [],
            "transfer_package": pkg,
        }

    def test_budget_flat_into_entry_top_level(self):
        history = {"season": "2026-27", "manager_id": 1, "history": []}
        pkg = transfer.summarize_package(2.0, [])
        history_writer.upsert_decision(history, 5, self._decision(pkg), [],
                                       {"team_market_score": 1.0}, {"tsb_weight": 0.8})
        entry = history["history"][0]
        self.assertEqual(entry["budget_before"], 2.0)
        self.assertEqual(entry["transfer_cost"], 0.0)
        self.assertEqual(entry["budget_after"], 2.0)
        self.assertEqual(entry["decision"]["transfer_package"]["gain"], 0.0)

    def test_legacy_entry_without_package_unchanged(self):
        history = {"season": "2026-27", "manager_id": 1, "history": [
            {"gw": 4, "points": 60}]}
        history_writer.upsert_decision(history, 4, self._decision(None), [],
                                       {"team_market_score": 1.0}, {"tsb_weight": 0.8})
        entry = history["history"][0]
        self.assertNotIn("budget_before", entry)   # 老数据不伪造预算
        self.assertIsNone(entry["decision"]["transfer_package"])


if __name__ == "__main__":
    unittest.main()
