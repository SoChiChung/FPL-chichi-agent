"""Phase 1 决策逻辑验证（仅标准库 unittest + 本地合成数据）。

运行：python -m unittest tests.test_phase1 -v
不访问网络、不写真实 data/ 文件。
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import api, captain, history_writer, lineup, market, squad_builder, strategy_config, transfer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mk_player(pid, name, pos, team, price, tsb, tin=0, tout=0,
              chance=None, status="a", form="0", tp=0):
    return {
        "id": pid, "name": name, "pos": pos, "team": team, "price": float(price),
        "selected_by": None if tsb is None else float(tsb),
        "selected_by_percent": None if tsb is None else float(tsb),
        "form": form, "total_points": tp, "status": status,
        "chance_of_playing_next_round": chance,
        "transfers_in_event": tin, "transfers_out_event": tout,
    }


class TestMarketScore(unittest.TestCase):
    def test_trend_minmax_normalization(self):
        players = {
            1: mk_player(1, "A", "MID", "MCI", 10, 10, tin=100, tout=0),
            2: mk_player(2, "B", "MID", "ARS", 10, 10, tin=50, tout=50),
            3: mk_player(3, "C", "MID", "LIV", 10, 10, tin=0, tout=50),
        }
        trend = market.compute_trend_scores(players)
        self.assertEqual(trend[1], 100.0)          # net=100 → max
        self.assertEqual(trend[2], 33.33)          # net=0 → (0-(-50))/150*100
        self.assertEqual(trend[3], 0.0)            # net=-50 → min

    def test_trend_all_equal_is_50(self):
        players = {1: mk_player(1, "A", "MID", "MCI", 10, 10, tin=20, tout=20),
                   2: mk_player(2, "B", "MID", "ARS", 10, 10, tin=20, tout=20)}
        self.assertEqual(market.compute_trend_scores(players), {1: 50.0, 2: 50.0})

    def test_market_score_formula_and_rounding(self):
        players = {1: mk_player(1, "A", "MID", "MCI", 10, 80, tin=100, tout=0),
                   2: mk_player(2, "B", "MID", "ARS", 10, 40, tin=0, tout=100)}
        weights = {"tsb": 0.8, "trend": 0.2}
        scores = market.compute_market_scores(players, weights)
        # A: 80*0.8 + 100*0.2 = 84.0；B: 40*0.8 + 0*0.2 = 32.0
        self.assertEqual(scores[1], 84.0)
        self.assertEqual(scores[2], 32.0)

    def test_string_numbers_none_and_missing(self):
        players = {
            1: mk_player(1, "A", "MID", "MCI", 10, "55.5", tin="10", tout="3"),
            2: mk_player(2, "B", "MID", "ARS", 10, None),
            3: {"id": 3, "name": "C", "pos": "FWD", "team": "LIV", "price": 5.0,
                "selected_by": "abc"},
        }
        scores = market.compute_market_scores(players, {"tsb": 0.8, "trend": 0.2})
        # 净转会：7 / 0 / 0 → 归一化 100 / 0 / 0
        self.assertEqual(scores[1], round(55.5 * 0.8 + 100.0 * 0.2, 2))  # 字符串数字
        self.assertEqual(scores[2], 0.0)   # tsb=None → 0，trend=0
        self.assertEqual(scores[3], 0.0)   # tsb="abc" → 0，trend=0

    def test_weights_by_gw(self):
        cfg = strategy_config.load()
        self.assertEqual(strategy_config.get_weights(cfg, 1), {"tsb": 0.8, "trend": 0.2})
        self.assertEqual(strategy_config.get_weights(cfg, 10), {"tsb": 0.8, "trend": 0.2})
        self.assertEqual(strategy_config.get_weights(cfg, 11), {"tsb": 0.6, "trend": 0.4})
        self.assertEqual(strategy_config.get_weights(cfg, 20), {"tsb": 0.6, "trend": 0.4})
        self.assertEqual(strategy_config.get_weights(cfg, 21), {"tsb": 0.3, "trend": 0.7})
        self.assertEqual(strategy_config.get_weights(cfg, 38), {"tsb": 0.3, "trend": 0.7})


class TestLineupAndCaptain(unittest.TestCase):
    def _squad(self):
        team = []
        for pid, pos, price, tsb in [
            (1, "GKP", 4.0, 5), (2, "GKP", 4.5, 30),
            (3, "DEF", 5.0, 40), (4, "DEF", 5.5, 50), (5, "DEF", 4.0, 10),
            (6, "DEF", 6.0, 60), (7, "DEF", 4.5, 20),
            (8, "MID", 8.0, 70), (9, "MID", 6.0, 30), (10, "MID", 7.5, 55),
            (11, "MID", 5.0, 15), (12, "MID", 9.0, 80),
            (13, "FWD", 10.0, 65), (14, "FWD", 7.0, 45), (15, "FWD", 5.5, 25),
        ]:
            p = mk_player(pid, f"P{pid}", pos, "MCI", price, tsb)
            p["market_score"] = float(tsb)
            team.append(p)
        return team

    def test_lineup_formation_valid(self):
        formation, starters, bench = lineup.select_lineup(
            self._squad(), ["343", "352", "442", "433", "451", "541", "532"])
        self.assertEqual(len(starters), 11)
        self.assertEqual(len(bench), 4)
        self.assertEqual(sum(1 for p in starters if p["pos"] == "GKP"), 1)
        self.assertEqual(sum(1 for p in starters if p["pos"] == "DEF"), int(formation[0]))
        self.assertEqual(sum(1 for p in starters if p["pos"] == "MID"), int(formation[1]))
        self.assertEqual(sum(1 for p in starters if p["pos"] == "FWD"), int(formation[2]))
        # 首发总分 = 最高（GKP 30 + 343 阵型 150+235+135 = 550）
        self.assertEqual(round(sum(p["market_score"] for p in starters), 2), 550.0)

    def test_captain_and_vice_from_starting(self):
        _, starters, _ = lineup.select_lineup(self._squad(), ["343", "352"])
        sel = captain.MarketCaptainSelector()
        cap, vice = sel.select(starters)
        self.assertIn(cap["id"], {p["id"] for p in starters})
        self.assertLessEqual(cap["market_score"] >= vice["market_score"], True)
        self.assertEqual(cap["market_score"] + vice["market_score"], 80 + 70)
        self.assertEqual(cap["id"], 12)
        self.assertEqual(vice["id"], 8)

    def test_tie_break_by_smaller_id(self):
        p1 = mk_player(1, "A", "FWD", "MCI", 10, 50, chance=None)
        p2 = mk_player(2, "B", "FWD", "ARS", 10, 50, chance=None)
        p1["market_score"], p2["market_score"] = 70.0, 70.0
        cap, vice = captain.MarketCaptainSelector().select([p2, p1])
        self.assertEqual(cap["id"], 1)


class TestSquadBuilder(unittest.TestCase):
    def test_quota_team_limit_and_budget(self):
        players = {}
        for pid in range(1, 61):
            pos = ["GKP", "DEF", "MID", "FWD"][pid % 4]
            team = f"T{pid % 10}"          # 每队最多 6 人候选
            players[pid] = mk_player(pid, f"P{pid}", pos, team, 6.0, 50 - pid % 30)
        scores = {pid: float(p["selected_by"]) for pid, p in players.items()}
        squad, warnings = squad_builder.build_squad(players, scores, strategy_config.load())
        self.assertEqual(len(squad), 15)
        from collections import Counter
        quota = strategy_config.load()["position_quota"]
        pos_count = Counter(p["pos"] for p in squad)
        self.assertEqual(dict(pos_count), quota)
        team_count = Counter(p["team"] for p in squad)
        self.assertLessEqual(max(team_count.values()), 3)
        self.assertLessEqual(round(sum(p["price"] for p in squad), 1), 100.0)
        self.assertFalse(warnings)


class TestApiPicks(unittest.TestCase):
    def test_get_picks_checked_404_returns_none(self):
        with mock.patch.object(api, "_fetch", side_effect=api.FplApiError("404", status=404)):
            self.assertIsNone(api.get_picks_checked(1, 2))

    def test_get_picks_checked_empty_returns_none(self):
        with mock.patch.object(api, "_fetch", return_value={"picks": []}):
            self.assertIsNone(api.get_picks_checked(1, 2))
        with mock.patch.object(api, "_fetch", return_value={}):
            self.assertIsNone(api.get_picks_checked(1, 2))

    def test_get_picks_checked_other_error_raises(self):
        with mock.patch.object(api, "_fetch", side_effect=api.FplApiError("500", status=500)):
            with self.assertRaises(api.FplApiError):
                api.get_picks_checked(1, 2)

    def test_find_latest_picks_searches_backward(self):
        data = {"picks": [{"element": 1}]}
        # GW3、GW2 都无阵容，GW1 有 → 返回 GW1
        with mock.patch.object(api, "get_picks_checked", side_effect=[None, None, data]):
            picks, gw = api.find_latest_picks(1, 3)
        self.assertEqual(gw, 1)
        self.assertEqual(picks, data)

    def test_find_latest_picks_none_found(self):
        with mock.patch.object(api, "get_picks_checked", return_value=None):
            picks, gw = api.find_latest_picks(1, 3)
        self.assertEqual((picks, gw), (None, None))


class TestTransfer(unittest.TestCase):
    def _scenario(self):
        """合成市场：市场分 = tsb。阵容含 MID/DEF/FWD 候选，替代者 100-104。"""
        squad = []
        for pid, pos, tsb in [
            (1, "GKP", 5), (2, "GKP", 30),
            (3, "DEF", 40), (4, "DEF", 50), (5, "DEF", 10),
            (6, "DEF", 60), (7, "DEF", 20),
            (8, "MID", 70), (9, "MID", 30), (10, "MID", 55),
            (11, "MID", 15), (12, "MID", 80),
            (13, "FWD", 65), (14, "FWD", 45), (15, "FWD", 25),
        ]:
            p = mk_player(pid, f"P{pid}", pos, "MCI", 6.0, tsb)
            p["market_score"] = float(tsb)
            squad.append(p)
        players = {p["id"]: p for p in squad}
        players[100] = mk_player(100, "Star", "MID", "ARS", 8.0, 99)
        players[101] = mk_player(101, "Gap", "MID", "LIV", 7.0, 96)
        players[102] = mk_player(102, "Inj", "FWD", "CHE", 9.0, 90)
        players[103] = mk_player(103, "Cheap", "DEF", "NFO", 4.0, 95)
        players[104] = mk_player(104, "SameTeam", "MID", "MCI", 6.0, 97)
        scores = {pid: float(p["selected_by"]) for pid, p in players.items()}
        return squad, players, scores

    def test_gap_threshold(self):
        squad, players, scores = self._scenario()
        ts = {"status": "limited", "free_transfers": 5}
        suggestions, notes = transfer.evaluate_transfers(
            squad, players, scores, strategy_config.load(), 3.0, ts)
        gaps = [s["market_gap"] for s in suggestions]
        self.assertEqual(gaps, sorted(gaps, reverse=True))
        # 触发者按 Gap 降序：5(DEF,85)、11(MID,84)、15(FWD,65)…
        out_ids = {s["out"]["id"] for s in suggestions}
        self.assertIn(11, out_ids)
        self.assertIn(5, out_ids)
        self.assertEqual(len(suggestions), 3)
        for s in suggestions:
            self.assertEqual(s["in"]["pos"], s["out"]["pos"])
            self.assertNotIn(s["in"]["id"], {p["id"] for p in squad})
            self.assertLessEqual(s["in"]["price"], 6.0 + 3.0)  # bank + out 价格

    def test_gap_below_threshold_no_suggestion(self):
        squad, players, scores = self._scenario()
        cfg = strategy_config.load()
        cfg["market_gap_threshold"] = 100.0   # 所有 Gap 都不再触发
        suggestions, _ = transfer.evaluate_transfers(
            squad, players, scores, cfg, 3.0, {"status": "limited", "free_transfers": 5})
        self.assertEqual(suggestions, [])

    def test_injury_triggers(self):
        squad, players, scores = self._scenario()
        # 16 号 FWD：chance 40 < 75 → 触发（Gap 阈值调高，隔离出伤病触发）
        squad.append(dict(mk_player(16, "Sick", "FWD", "TOT", 6.0, 30, chance=40)))
        players[16] = squad[-1]
        scores[16] = 30.0
        cfg = strategy_config.load()
        cfg["market_gap_threshold"] = 100.0
        suggestions, _ = transfer.evaluate_transfers(
            squad, players, scores, cfg, 3.0, {"status": "limited", "free_transfers": 5})
        self.assertIn(16, {s["out"]["id"] for s in suggestions})
        self.assertTrue(any("出场概率" in s["reason"] for s in suggestions if s["out"]["id"] == 16))

    def test_free_transfer_limit(self):
        squad, players, scores = self._scenario()
        suggestions, _ = transfer.evaluate_transfers(
            squad, players, scores, strategy_config.load(), 3.0,
            {"status": "limited", "free_transfers": 1})
        self.assertLessEqual(len(suggestions), 1)

    def test_no_free_transfers(self):
        squad, players, scores = self._scenario()
        suggestions, notes = transfer.evaluate_transfers(
            squad, players, scores, strategy_config.load(), 3.0,
            {"status": "limited", "free_transfers": 0})
        self.assertEqual(suggestions, [])
        self.assertTrue(any(n["topic"] == "no_transfer" for n in notes))

    def test_replacement_unique_and_team_limit(self):
        squad, players, scores = self._scenario()
        suggestions, _ = transfer.evaluate_transfers(
            squad, players, scores, strategy_config.load(), 3.0,
            {"status": "limited", "free_transfers": 5})
        in_ids = [s["in"]["id"] for s in suggestions]
        self.assertEqual(len(in_ids), len(set(in_ids)))          # 替代者不重复
        self.assertNotIn(104, in_ids)                            # 同队限制

    def test_unlimited_not_truncated_by_integer_limit(self):
        squad = []
        players = {}
        for pid, pos, tsb in [
            (1, "GKP", 10), (2, "GKP", 15),
            (3, "DEF", 20), (4, "DEF", 25),
            (5, "MID", 40), (6, "MID", 45),
            (7, "FWD", 60), (8, "FWD", 65),
        ]:
            p = mk_player(pid, f"P{pid}", pos, "AAA", 5.0, tsb)
            p["market_score"] = float(tsb)
            squad.append(p)
            players[pid] = p
        for pid, pos, tsb, team in [(101, "GKP", 90, "BBB"), (102, "DEF", 91, "CCC"),
                                    (103, "MID", 92, "DDD"), (104, "FWD", 93, "EEE")]:
            players[pid] = mk_player(pid, f"S{pid}", pos, team, 5.0, tsb)
        scores = {pid: float(p["selected_by"]) for pid, p in players.items()}
        cfg = strategy_config.load()
        # 4 个位置各 1 名触发者、替代者互不冲突
        unlimited = {"status": "unlimited", "free_transfers": None}
        sug_u, _ = transfer.evaluate_transfers(squad, players, scores, cfg, 0.0, unlimited)
        self.assertEqual(len(sug_u), 4)   # 不被 max_free_transfers=5 截断（unlimited 全出）
        limited = {"status": "limited", "free_transfers": 2}
        sug_l, _ = transfer.evaluate_transfers(squad, players, scores, cfg, 0.0, limited)
        self.assertEqual(len(sug_l), 2)   # limited 截断为 2

    def test_history_provider_fallback(self):
        entry_history = {"current": [
            {"event": 1, "event_transfers": 2},   # GW1 用了 2 笔（1 免费 + 1 扣分）
            {"event": 2, "event_transfers": 0},   # GW2 进行中，不计入
        ]}
        provider = transfer.HistoryFreeTransferProvider(entry_history, 5, current_gw=2)
        self.assertEqual(provider.get_free_transfers(), 1)

    def test_resolve_transfer_status(self):
        # establish → unlimited
        self.assertEqual(
            transfer.resolve_transfer_status({"current": []}, 3, 5, establish=True),
            {"status": "unlimited", "free_transfers": None})
        # 没有任何已完结轮次 → unlimited（新号）
        self.assertEqual(
            transfer.resolve_transfer_status({"current": []}, 3, 5),
            {"status": "unlimited", "free_transfers": None})
        self.assertEqual(
            transfer.resolve_transfer_status(
                {"current": [{"event": 3, "event_transfers": 0}]}, 3, 5),
            {"status": "unlimited", "free_transfers": None})
        # 有已完结轮次 → limited + 推导数量
        st = transfer.resolve_transfer_status(
            {"current": [{"event": 1, "event_transfers": 1}, {"event": 2, "event_transfers": 0}]},
            3, 5)
        self.assertEqual(st["status"], "limited")
        self.assertIsInstance(st["free_transfers"], int)
        self.assertGreaterEqual(st["free_transfers"], 0)


class TestHistoryWriter(unittest.TestCase):
    def test_upsert_idempotent(self):
        history = {"season": "2026-27", "manager_id": 1, "history": []}
        decision = {
            "formation": "352", "captain": {"id": 1, "name": "A"},
            "vice": {"id": 2, "name": "B"},
            "starting_xi": [{"id": 1, "name": "A"}], "bench": [{"id": 3, "name": "C"}],
            "squad": [{"id": 1, "name": "A"}],
            "recommended_transfers": [],
        }
        for _ in range(3):
            history_writer.upsert_decision(
                history, 5, decision, [{"topic": "no_transfer", "detail": "x"}],
                {"team_market_score": 100.0}, {"tsb_weight": 0.8})
        self.assertEqual(len(history["history"]), 1)
        entry = history["history"][0]
        self.assertEqual(entry["gw"], 5)
        self.assertEqual(entry["decision"]["formation"], "352")
        self.assertEqual(entry["decision"]["recommended_transfers"], [])
        self.assertEqual(entry["metrics"]["team_market_score"], 100.0)
        self.assertEqual(entry["decision"]["strategy_snapshot"]["tsb_weight"], 0.8)

    def test_preserves_existing_result_fields(self):
        history = {"season": "2026-27", "manager_id": 1, "history": [
            {"gw": 4, "points": 60, "rank": 100, "overall_rank": 99}
        ]}
        history_writer.upsert_decision(history, 4, {"formation": "343",
            "captain": {"id": 1, "name": "A"}, "vice": {"id": 2, "name": "B"},
            "starting_xi": [], "bench": [], "squad": [], "recommended_transfers": []},
            [], {"team_market_score": 1.0}, {"tsb_weight": 0.8})
        entry = history["history"][0]
        self.assertEqual(entry["points"], 60)
        self.assertEqual(entry["overall_rank"], 99)
        self.assertIn("decision", entry)

    def test_init_history_for_account(self):
        # 旧账号历史 → 重置为空历史
        h_old = {"season": "2026-27", "manager_id": 2076, "history": [{"gw": 1}]}
        h, replaced = history_writer.init_history_for_account(h_old, 10049242, "2026-27")
        self.assertTrue(replaced)
        self.assertEqual(h["manager_id"], 10049242)
        self.assertEqual(h["history"], [])
        # 同账号历史 → 原样保留
        h2, replaced2 = history_writer.init_history_for_account(h, 10049242, "2026-27")
        self.assertFalse(replaced2)
        self.assertIs(h2, h)
        # 文件缺失 → 从空开始
        h3, replaced3 = history_writer.init_history_for_account(None, 10049242, "2026-27")
        self.assertTrue(replaced3)
        self.assertEqual(h3["manager_id"], 10049242)


class TestNpmStartOrder(unittest.TestCase):
    """npm start = brain → build → serve；brain/build 失败时不启动服务器。"""

    def _run_start(self, brain_args, port, timeout_s=20):
        env = dict(os.environ, FPL_BRAIN_CMD="node", FPL_BRAIN_ARGS=brain_args, PORT=str(port))
        outfile = tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8")
        outfile.close()
        proc = subprocess.Popen(
            ["node", os.path.join(ROOT, "scripts", "start.js")],
            cwd=ROOT, env=env, stdout=open(outfile.name, "w", encoding="utf-8"),
            stderr=subprocess.STDOUT)
        content = ""
        started = False
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                with open(outfile.name, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                pass
            if proc.poll() is not None:
                break
            if "[3/3]" in content and "已启动" in content:
                started = True
                break
            time.sleep(0.1)
        running = proc.poll() is None
        if running:
            proc.kill()          # Windows 上 kill 的退出码是 1，不代表失败
            proc.wait()
        return proc.returncode, content, started

    def test_fail_fast_when_brain_fails(self):
        code, out, started = self._run_start("-e process.exit(3)", 8199)
        self.assertEqual(code, 3)                     # 非零退出码
        self.assertFalse(started)                     # 服务器未启动
        self.assertIn("[1/3]", out)
        self.assertNotIn("[3/3]", out)

    def test_brain_then_build_then_serve(self):
        code, out, started = self._run_start("-e process.exit(0)", 8201)
        self.assertTrue(started)                      # 服务器实际启动并保持运行
        pos_brain = out.find("[1/3]")
        pos_build = out.find("[2/3]")
        pos_serve = out.find("[3/3]")
        pos_started = out.find("已启动")
        self.assertGreaterEqual(pos_brain, 0)
        self.assertGreater(pos_build, pos_brain)      # brain 先于 build
        self.assertGreater(pos_serve, pos_build)      # build 先于 serve
        self.assertGreater(pos_started, pos_serve)    # 服务器在最后启动


if __name__ == "__main__":
    unittest.main()
