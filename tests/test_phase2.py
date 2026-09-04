"""Phase 2 Lineup & Captain Engine 验证（仅标准库 unittest + 本地合成数据，无网络）。

覆盖 docs/decide.md §8：全局最优枚举 / 合法性 / 替补顺序 / C-V 归属 /
Streak 全序与位置门槛 / 归一化边界（含 Form GW1 全员 50）/ fpljoe 缺失降级 /
配置驱动 / 确定性并列 / 输出 schema / element-summary 解析。

运行：python -m unittest tests.test_phase2 -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import api, captain_score, context, lineup_score, strategy_config
from brain.external import fpl_joe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = strategy_config.load()["lineup_engine"]


def mk_player(pid, pos, team="ARS", market=50.0, form="5.0", pos_extra=None):
    return {
        "id": pid, "name": f"P{pid}", "pos": pos, "team": team,
        "price": 6.0, "market_score": float(market), "form": form,
    }


def full_squad():
    """15 人标准合成 squad（2/5/5/3），每队市场分递增以利于可预测。"""
    rows = []
    specs = [("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]
    pid = 1
    for pos, count in specs:
        for i in range(count):
            rows.append(mk_player(pid, pos, market=30 + pid * 2, form=str(3 + pid % 9)))
            pid += 1
    return rows


def league_form_map(squad, hi=10.0, lo=0.0):
    """把 squad 外的联盟池补足，保证 Form 归一化区间稳定。"""
    m = {p["id"]: p["form"] for p in squad}
    m[9999] = str(lo)
    m[9998] = str(hi)
    return m


def default_team_data(squad, proj=1.5, cs=0.35, diff=3, neutral=None):
    """按 squad 球队构造 fpljoe read_target_gw 返回结构。"""
    teams, neutral = {}, neutral or {}
    for abbr in {p["team"] for p in squad}:
        teams[abbr] = {"projection": proj, "clean_sheet": cs, "fixture": diff}
    return {"teams": teams, "neutral": neutral, "notes": [], "sources": {}}


def score_and_pick(squad, team_data=None, cfg=CFG, forms=None):
    squad, notes = lineup_score.score_squad(
        squad, team_data if team_data is not None else default_team_data(squad),
        forms if forms is not None else league_form_map(squad), {}, cfg)
    return squad, notes


class TestNormalizationAndForm(unittest.TestCase):
    def test_league_minmax_two_decimals(self):
        # DEF 行可同时覆盖 projection/clean_sheet/fixture 三项成分
        squad = [mk_player(1, "DEF", "AAA"), mk_player(2, "DEF", "BBB"),
                 mk_player(3, "DEF", "CCC")]
        td = {"teams": {
            "AAA": {"projection": 1.0, "clean_sheet": 0.2, "fixture": 1},
            "BBB": {"projection": 2.0, "clean_sheet": 0.5, "fixture": 3},
            "CCC": {"projection": 3.0, "clean_sheet": 0.8, "fixture": 5},
        }, "neutral": {}, "notes": [], "sources": {}}
        squad, _ = score_and_pick(squad, td, forms={1: "0", 2: "5", 3: "10"})
        aaa = squad[0]["score_breakdown"]
        bbb = squad[1]["score_breakdown"]
        self.assertEqual(aaa["projection"], 0.0)
        self.assertEqual(bbb["projection"], 50.0)
        self.assertEqual(squad[2]["score_breakdown"]["projection"], 100.0)
        # fixture 取负归一：难度低 → 分数高
        self.assertEqual(aaa["fixture"], 100.0)
        self.assertEqual(bbb["fixture"], 50.0)
        self.assertEqual(squad[2]["score_breakdown"]["fixture"], 0.0)
        # clean_sheet 与 projection 同区间语义
        self.assertEqual(aaa["clean_sheet"], 0.0)
        self.assertEqual(bbb["clean_sheet"], 50.0)

    def test_blank_team_zero_and_notes(self):
        squad = [mk_player(1, "MID", "AAA"), mk_player(2, "MID", "ZZZ")]
        td = {"teams": {
            "AAA": {"projection": 3.0, "clean_sheet": 0.8, "fixture": 1},
            "BBB": {"projection": 1.0, "clean_sheet": 0.2, "fixture": 5},
        }, "neutral": {}, "notes": [], "sources": {}}
        squad, notes = score_and_pick(squad, td,
                                      forms={1: "5", 2: "5", 999: "0", 998: "10"})
        self.assertEqual(squad[0]["score_breakdown"]["projection"], 100.0)
        self.assertEqual(squad[1]["score_breakdown"]["projection"], 0.0)  # ZZZ blank
        self.assertTrue(any("ZZZ" in n["detail"] for n in notes))

    def test_neutral_metric_when_whole_round_missing(self):
        squad = [mk_player(1, "DEF", "AAA"), mk_player(2, "DEF", "BBB")]
        td = {"teams": {
            "AAA": {"projection": 3.0, "clean_sheet": 0.8, "fixture": 1},
            "BBB": {"projection": 1.0, "clean_sheet": 0.2, "fixture": 5},
        }, "neutral": {"projection": True}, "notes": [], "sources": {}}
        squad, _ = score_and_pick(squad, td, forms={1: "5", 2: "5", 999: "0", 998: "10"})
        # projection 整轮中立 → 全员 50；clean_sheet/fixture 正常归一
        self.assertEqual(squad[0]["score_breakdown"]["projection"], 50.0)
        self.assertEqual(squad[1]["score_breakdown"]["projection"], 50.0)
        self.assertEqual(squad[0]["score_breakdown"]["clean_sheet"], 100.0)
        self.assertEqual(squad[1]["score_breakdown"]["clean_sheet"], 0.0)

    def test_form_all_equal_is_50(self):
        squad = [mk_player(1, "MID", "AAA"), mk_player(2, "FWD", "BBB")]
        squad, _ = score_and_pick(squad, forms={1: "6", 2: "6", 999: "6", 998: "6"})
        self.assertEqual(squad[0]["score_breakdown"]["form"], 50.0)
        self.assertEqual(squad[1]["score_breakdown"]["form"], 50.0)


class TestStreak(unittest.TestCase):
    def test_order_8_patterns(self):
        sm = CFG["streak_map"]
        patterns = sorted(sm.items(), key=lambda kv: -kv[1])
        self.assertEqual([k for k, _ in patterns],
                         ["111", "110", "101", "100", "011", "010", "001", "000"])
        self.assertEqual(sm["111"], 100.0)
        self.assertEqual(sm["000"], 0.0)

    def test_threshold_by_position(self):
        sm = CFG["streak_map"]
        self.assertEqual(lineup_score.streak_score([5], 5, sm), 57.14)  # MID 5 分算回报
        self.assertEqual(lineup_score.streak_score([5], 6, sm), 0.0)    # DEF 5 分不算
        self.assertEqual(lineup_score.streak_score([6], 6, sm), 57.14)  # DEF 6 分算
        self.assertEqual(lineup_score.streak_score([4], 5, sm), 0.0)

    def test_missing_history_zero(self):
        sm = CFG["streak_map"]
        self.assertEqual(lineup_score.streak_score([], 5, sm), 0.0)
        self.assertEqual(lineup_score.streak_score([None, 8, None], 5, sm), 28.57)  # 0 1 0

    def test_streak_uses_position_threshold_in_scoring(self):
        squad = [mk_player(1, "MID", "AAA"), mk_player(2, "DEF", "BBB")]
        td = default_team_data([mk_player(1, "MID", "AAA"), mk_player(2, "DEF", "BBB")])
        squad, _ = lineup_score.score_squad(
            squad, td, league_form_map(squad), {1: [5], 2: [5]}, CFG)
        self.assertEqual(squad[0]["score_breakdown"]["streak"], 57.14)  # MID 5=回报
        self.assertEqual(squad[1]["score_breakdown"]["streak"], 0.0)    # DEF 5=非回报


class TestLineupSelect(unittest.TestCase):
    def _scored(self):
        squad, _ = score_and_pick(full_squad())
        return squad

    def test_lineup_11_and_position_counts(self):
        squad = self._scored()
        formation, xi, bench = lineup_score.select_starting_xi(squad, CFG)
        self.assertEqual(len(xi), 11)
        self.assertEqual(len(bench), 4)
        self.assertEqual(sum(1 for p in xi if p["pos"] == "GKP"), 1)
        self.assertGreaterEqual(sum(1 for p in xi if p["pos"] == "DEF"), 3)
        self.assertGreaterEqual(sum(1 for p in xi if p["pos"] == "MID"), 2)
        self.assertGreaterEqual(sum(1 for p in xi if p["pos"] == "FWD"), 1)
        self.assertEqual(formation, f"{sum(1 for p in xi if p['pos']=='DEF')}"
                                    f"{sum(1 for p in xi if p['pos']=='MID')}"
                                    f"{sum(1 for p in xi if p['pos']=='FWD')}")

    def test_brute_force_optimal(self):
        # 手工构造：DEF 高分多、MID 低分，最优 = 上满 5 DEF（541）并带 3 高 MID
        squad, _ = score_and_pick([
            mk_player(1, "GKP", market=40), mk_player(2, "GKP", market=39),
            mk_player(3, "DEF", market=99), mk_player(4, "DEF", market=98),
            mk_player(5, "DEF", market=97), mk_player(6, "DEF", market=96),
            mk_player(7, "DEF", market=95),
            mk_player(8, "MID", market=30), mk_player(9, "MID", market=29),
            mk_player(10, "MID", market=28), mk_player(11, "MID", market=27),
            mk_player(12, "MID", market=26),
            mk_player(13, "FWD", market=60), mk_player(14, "FWD", market=55),
            mk_player(15, "FWD", market=50),
        ])
        formation, xi, _ = lineup_score.select_starting_xi(squad, CFG)
        # 单队同值归一化下各位置核心分相等 → 枚举等价于"受限市场最优"：
        # 5 DEF(99..95) + 3 FWD(60..50) + 2 MID(30..29) + 1 GK → 阵型字面量 "523"
        # （formation = f"{DEF}{MID}{FWD}"，decide.md §4）
        self.assertEqual(formation, "523")
        # 完整性对照：自行独立穷举同目标函数，确认筛选一致
        import itertools as it
        best = None
        for combo in it.combinations([p["id"] for p in squad], 11):
            sel = [p for p in squad if p["id"] in combo]
            if sum(1 for p in sel if p["pos"] == "GKP") != 1:
                continue
            if not (sum(1 for p in sel if p["pos"] == "DEF") >= 3
                    and sum(1 for p in sel if p["pos"] == "MID") >= 2
                    and sum(1 for p in sel if p["pos"] == "FWD") >= 1):
                continue
            total = sum(p["lineup_score"] for p in sel)
            if best is None or total > best[0]:
                best = (total, sorted(combo))
        sel = [p for p in squad if p["id"] in best[1]]
        self.assertEqual([p["id"] for p in xi], sorted([p["id"] for p in xi]))  # id 唯一性
        self.assertEqual(len(xi), 11)
        self.assertEqual(round(sum(p["lineup_score"] for p in xi), 2), round(best[0], 2))

    def test_weak_position_still_meets_minimum(self):
        # DEF 全部垫底：最优解仍必须 ≥3 DEF、≥2 MID、≥1 FWD、恰 1 GK
        squad, _ = score_and_pick([
            mk_player(1, "GKP", market=80), mk_player(2, "GKP", market=79),
            mk_player(3, "DEF", market=1), mk_player(4, "DEF", market=1),
            mk_player(5, "DEF", market=1), mk_player(6, "DEF", market=1),
            mk_player(7, "DEF", market=1),
            mk_player(8, "MID", market=90), mk_player(9, "MID", market=89),
            mk_player(10, "MID", market=88), mk_player(11, "MID", market=87),
            mk_player(12, "MID", market=86),
            mk_player(13, "FWD", market=70), mk_player(14, "FWD", market=69),
            mk_player(15, "FWD", market=68),
        ])
        formation, xi, _ = lineup_score.select_starting_xi(squad, CFG)
        self.assertEqual(sum(1 for p in xi if p["pos"] == "DEF"), 3)
        self.assertEqual(sum(1 for p in xi if p["pos"] == "MID"), 5)
        self.assertEqual(sum(1 for p in xi if p["pos"] == "FWD"), 2)
        self.assertEqual(formation, "352")

    def test_bench_order_gk_last(self):
        squad = self._scored()
        _, _, bench = lineup_score.select_starting_xi(squad, CFG)
        non_gk = [p for p in bench if p["pos"] != "GKP"]
        gk = [p for p in bench if p["pos"] == "GKP"]
        self.assertEqual(len(gk), 1)
        self.assertEqual(bench[-1]["pos"], "GKP")
        scores = [p["lineup_score"] for p in non_gk]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_tie_break_lexicographic(self):
        # 两套同分阵容：所有分数相同 → element_id 组合字典序小者胜（最小编号者必在场）
        squad, _ = score_and_pick([
            mk_player(1, "GKP", market=50), mk_player(2, "GKP", market=50),
            mk_player(3, "DEF", market=50), mk_player(4, "DEF", market=50),
            mk_player(5, "DEF", market=50), mk_player(6, "DEF", market=50),
            mk_player(7, "DEF", market=50),
            mk_player(8, "MID", market=50), mk_player(9, "MID", market=50),
            mk_player(10, "MID", market=50), mk_player(11, "MID", market=50),
            mk_player(12, "MID", market=50),
            mk_player(13, "FWD", market=50), mk_player(14, "FWD", market=50),
            mk_player(15, "FWD", market=50),
        ])
        _, xi, _ = lineup_score.select_starting_xi(squad, CFG)
        self.assertIn(1, {p["id"] for p in xi})

    def test_requires_15_players(self):
        with self.assertRaises(ValueError):
            lineup_score.select_starting_xi([mk_player(1, "GKP")], CFG)


class TestCaptain(unittest.TestCase):
    def _scored_starter(self, pos, breakdown, market=50.0):
        p = mk_player(1, pos, market=market)
        p["score_breakdown"] = breakdown
        return p

    def test_captain_formula_by_position(self):
        cfg = CFG
        cases = [
            ("DEF", {"attack_potential": 80, "clean_sheet": 60, "fixture": 50},
             40, 0.2 * 80 + 0.5 * 60 + 0.3 * 50),     # 61.0
            ("MID", {"attack_potential": 70, "form": 60}, 40, 0.9 * 70 + 0.1 * 40),
            ("FWD", {"attack_potential": 80}, 40, 0.95 * 80 + 0.05 * 40),   # 78.0
            ("GKP", {"clean_sheet": 60}, 40, 0.5 * 60 + 0.5 * 40),
        ]
        for i, (pos, breakdown, market, expected) in enumerate(cases):
            p = self._scored_starter(pos, breakdown, market)
            p["id"] = i + 1
            captain_score.captain_scores([p], cfg)
            self.assertEqual(p["captain_score"], round(expected, 2), pos)

    def test_captain_and_vice_from_starting(self):
        ps = [mk_player(1, "MID", market=50), mk_player(2, "MID", market=40),
              mk_player(3, "FWD", market=30)]
        for p in ps:
            p["score_breakdown"] = {"attack_potential": p["market_score"]}
        captain_score.captain_scores(ps, CFG)
        cap, vice = captain_score.choose_captain(ps)
        self.assertEqual(cap["id"], 1)
        self.assertEqual(vice["id"], 2)
        self.assertGreaterEqual(cap["captain_score"], vice["captain_score"])

    def test_tie_break_smaller_element_id(self):
        p1, p2 = mk_player(1, "MID"), mk_player(2, "MID")
        for p in (p1, p2):
            p["score_breakdown"] = {"attack_potential": 70}
        captain_score.captain_scores([p1, p2], CFG)
        cap, vice = captain_score.choose_captain([p2, p1])
        self.assertEqual((cap["id"], vice["id"]), (1, 2))

    def test_captain_independent_of_market(self):
        # 口径验证：Phase 1 的队长=Market 最高；新引擎若市场高的 2 号
        # 因爆发口径落后，必须选 1 号（证明走 Captain Score 而非 Market）
        p1 = mk_player(1, "FWD", market=40)
        p1["score_breakdown"] = {"attack_potential": 90}
        p2 = mk_player(2, "FWD", market=95)
        p2["score_breakdown"] = {"attack_potential": 50}
        captain_score.captain_scores([p1, p2], CFG)
        self.assertGreater(p1["captain_score"], p2["captain_score"])  # 0.95*90+2 < 0.95*50+4.75
        cap, vice = captain_score.choose_captain([p1, p2])
        self.assertEqual(cap["id"], 1)
        self.assertEqual(vice["id"], 2)


class TestSchema(unittest.TestCase):
    def test_all_rows_have_scores_and_breakdown(self):
        squad, _ = score_and_pick(full_squad())
        formation, xi, bench = lineup_score.select_starting_xi(squad, CFG)
        captain_score.captain_scores(xi, CFG)
        for p in squad:
            if p not in xi:
                p["captain_score"] = 0.0
        keys = ("projection", "form", "streak", "clean_sheet", "fixture",
                "attack", "attack_potential")
        for p in squad:
            self.assertIsInstance(p["lineup_score"], float)
            self.assertIsInstance(p["captain_score"], float)
            self.assertLessEqual(p["lineup_score"], 100.0)
            self.assertGreaterEqual(p["lineup_score"], 0.0)
            bd = p["score_breakdown"]
            self.assertEqual(set(bd), set(keys))  # schema 恒定 7 键，null = 未参与
            if p["pos"] == "GKP":
                self.assertIsNone(bd["attack_potential"])
                self.assertIsNone(bd["projection"])
                self.assertIsNotNone(bd["clean_sheet"])
            else:
                self.assertIsNotNone(bd["attack_potential"])
                self.assertIsNotNone(bd["projection"])
        self.assertEqual(len(xi) + len(bench), 15)

    def test_config_driven_weights_change_scores(self):
        squad, _ = score_and_pick(full_squad())
        baseline = [p["lineup_score"] for p in squad]
        custom = strategy_config.get_lineup_engine(
            {"lineup_engine": {"lineup_weights": {
                "MID": {"attack": 1.0, "form": 0, "fixture": 0, "market": 0}}}})
        squad2, _ = score_and_pick(full_squad(), cfg=custom)
        self.assertNotEqual(baseline, [p["lineup_score"] for p in squad2])

    def test_determinism(self):
        a, _ = score_and_pick(full_squad())
        b, _ = score_and_pick(full_squad())
        self.assertEqual([p["lineup_score"] for p in a],
                         [p["lineup_score"] for p in b])


class TestFplJoeReader(unittest.TestCase):
    def _write_docs(self, folder, meta_overrides=None, rows=None):
        def meta(gws=(1, 2, 3), freshness="fresh"):
            m = {"requested_gameweek_start": gws[0], "requested_gameweek_end": gws[1],
                 "freshness": freshness,
                 "data_sources": {"odds_market": [1, 2, 3], "elevenify": [1, 2, 3]}}
            m.update(meta_overrides or {})
            return m

        fixtures = rows if rows is not None else [
            {"gameweek": 2, "fpl_home_team_id": 1, "fpl_away_team_id": 2,
             "home_projected_goals": 1.9, "away_projected_goals": 1.0,
             "home_projected_goals_elevenify": 1.8, "away_projected_goals_elevenify": 1.1,
             "home_clean_sheet_probability": 0.35, "away_clean_sheet_probability": 0.2,
             "home_clean_sheet_elevenify": 0.33, "away_clean_sheet_elevenify": 0.21,
             "home_difficulty": 2, "away_difficulty": 3,
             "home_difficulty_sort_rating": 1.0, "away_difficulty_sort_rating": 2.5},
            {"gameweek": 2, "fpl_home_team_id": 3, "fpl_away_team_id": 4,
             "home_projected_goals": 0.7, "away_projected_goals": 2.3,
             "home_projected_goals_elevenify": 0.8, "away_projected_goals_elevenify": 2.1,
             "home_clean_sheet_probability": 0.5, "away_clean_sheet_probability": 0.1,
             "home_clean_sheet_elevenify": 0.48, "away_clean_sheet_elevenify": 0.12,
             "home_difficulty": 1, "away_difficulty": 4,
             "home_difficulty_sort_rating": 0.5, "away_difficulty_sort_rating": 3.5}]
        for name in fpl_joe.OUTPUT_FILES:
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                json.dump({"metadata": meta(), "fixtures": fixtures}, f, ensure_ascii=False)
        return fixtures

    def test_reader_odds_preferred_and_full_league(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_docs(folder)
            team_map = {1: "ARS", 2: "AVL", 3: "BHA", 4: "BRE"}
            out = fpl_joe.read_target_gw(2, team_map, folder)
            self.assertEqual(out["sources"]["projection"], "odds_market")
            self.assertEqual(out["sources"]["clean_sheet"], "odds_market")
            self.assertEqual(out["sources"]["fixture"], "sort_rating")
            self.assertEqual(out["teams"]["ARS"]["projection"], 1.9)
            self.assertEqual(out["teams"]["AVL"]["clean_sheet"], 0.2)
            self.assertEqual(out["teams"]["BRE"]["fixture"], 3.5)

    def test_reader_neutral_when_gw_out_of_window(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_docs(folder)
            team_map = {1: "ARS", 2: "AVL", 3: "BHA", 4: "BRE"}
            out = fpl_joe.read_target_gw(9, team_map, folder)
            self.assertTrue(out["neutral"]["projection"])
            self.assertTrue(any("data_missing" in (n.get("topic") or "") for n in out["notes"]))

    def test_reader_neutral_when_expired(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_docs(folder, meta_overrides={"freshness": "expired"})
            team_map = {1: "ARS", 2: "AVL", 3: "BHA", 4: "BRE"}
            out = fpl_joe.read_target_gw(2, team_map, folder)
            self.assertTrue(out["neutral"]["clean_sheet"])

    def test_reader_elevenify_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_docs(
                folder,
                meta_overrides={"data_sources": {"odds_market": [], "elevenify": [2]}})
            team_map = {1: "ARS", 2: "AVL", 3: "BHA", 4: "BRE"}
            out = fpl_joe.read_target_gw(2, team_map, folder)
            self.assertEqual(out["sources"]["projection"], "elevenify")
            self.assertEqual(out["teams"]["ARS"]["projection"], 1.8)


class TestApiElementSummary(unittest.TestCase):
    def test_points_by_round_extraction(self):
        summary = {"history": [
            {"round": 1, "points": 8}, {"round": 2, "points": -1},
            {"round": 3, "points": "6"}, {"round": 4, "points": None},
            {"round": "x", "points": 2}, {"round": 5}, {"round": 6, "minutes": 90},
        ]}
        out = api.points_by_round(summary)
        self.assertEqual(out, {1: 8, 2: -1, 3: 6})

    def test_points_by_round_empty(self):
        self.assertEqual(api.points_by_round({}), {})
        self.assertEqual(api.points_by_round({"history": []}), {})


class TestContextTargetGw(unittest.TestCase):
    def test_target_gw_is_first_future_deadline_event(self):
        # 截止时间为公元 9999 年 → 恒在未来，无需 mock 时间
        events = [
            {"id": 1, "finished": True, "deadline_time": "2026-08-14T17:30:00Z"},
            {"id": 2, "finished": False, "deadline_time": "9999-08-28T17:30:00Z"},
            {"id": 3, "finished": False, "deadline_time": "9999-09-04T17:30:00Z"},
        ]
        self.assertEqual(context.resolve_target_gw(events), 2)

    def test_no_future_deadline_falls_back_to_finished(self):
        events = [
            {"id": 1, "finished": True, "deadline_time": "2020-08-14T17:30:00Z"},
            {"id": 2, "finished": True, "deadline_time": "2020-08-28T17:30:00Z"},
        ]
        self.assertEqual(context.resolve_target_gw(events), 2)


if __name__ == "__main__":
    unittest.main()
