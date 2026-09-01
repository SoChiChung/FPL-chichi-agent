"""FPL Joe 外部数据层验证（仅标准库 unittest + 合成数据）。

运行：python -m unittest tests.test_fpl_joe -v
不访问网络、不写真实 data/ 文件（refresh 的失败路径用 mock）。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import data_store
from brain.external import fpl_joe
from brain.external.freshness import FRESH, STALE, EXPIRED, UNKNOWN, judge_freshness


def mk_raw(periods=(1, 2, 3), teams=("ARS", "LIV", "IPS")):
    """最小合成 projections 响应：市场 GW1-3 + Elevenify 全赛季序列。"""
    elevenify_teams = []
    for i, abbr in enumerate(teams):
        elevenify_teams.append({
            "name": abbr, "slug": abbr.lower(), "abbreviation": abbr, "teamId": 100 + i,
            "goals": [1.0 + i] * 38,
            "cleanSheets": [0.2 + i * 0.1] * 38,
        })
    skeleton = []
    for gw in range(1, 6):  # 骨架覆盖 GW1-5（多于市场范围）
        skeleton.append({
            "fixtureId": f"match:{gw}", "periodNumber": gw,
            "homeTeamCode": "ARS", "homeTeamName": "Arsenal",
            "awayTeamCode": "LIV", "awayTeamName": "Liverpool",
            "kickoffTsUtc": f"2026-09-0{gw}T19:00:00.000Z",
            "homeFixtureDifficulty": 1 + gw % 3, "awayFixtureDifficulty": 2 + gw % 3,
        })
    market = {}
    for gw in periods:
        market[str(gw)] = {"fixtures": [{
            "fixtureId": f"match:{gw}", "gw": gw, "periodNumber": gw, "teamsResolved": True,
            "lambdaHome": 2.0 + gw, "lambdaAway": 1.0 + gw,
            "pHomeWin": 0.5, "pDraw": 0.25, "pAwayWin": 0.25,
            "pHomeCs": 0.3, "pAwayCs": 0.2,
            "homeTeamCode": "ARS", "awayTeamCode": "LIV",
        }]}
    return {
        "scope": {"id": "2026-27", "kind": "season"},
        "season": "2026-27", "competition": "premier-league",
        "currentPeriod": 2, "availablePeriods": list(periods),
        "projectionsByPeriod": market,
        "supplementalProjections": {
            "teams": elevenify_teams, "periods": list(range(1, 39)),
            "fixtures": skeleton, "schemaVersion": 3,
        },
        "latestSnapshotTs": "2026-08-31T10:00:00Z",
        "pipelineStatus": {"lastRunStatus": "succeeded"},
    }


class TestFreshness(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(judge_freshness("2026-08-31T10:00:00Z", "2026-08-31T12:00:00Z",
                                         ttl_hours=6, max_age_hours=48), FRESH)
        self.assertEqual(judge_freshness("2026-08-31T10:00:00Z", "2026-08-31T17:00:00Z",
                                         ttl_hours=6, max_age_hours=48), STALE)
        self.assertEqual(judge_freshness("2026-08-31T10:00:00Z", "2026-09-02T11:00:00Z",
                                         ttl_hours=6, max_age_hours=48), EXPIRED)
        self.assertEqual(judge_freshness(None, "2026-08-31T12:00:00Z"), UNKNOWN)
        self.assertEqual(judge_freshness("bad-date", "2026-08-31T12:00:00Z"), UNKNOWN)


class TestNormalize(unittest.TestCase):
    def _norm(self):
        raw = mk_raw()
        return fpl_joe.normalize(raw, start_gw=3, end_gw=5,
                                 retrieved_at="2026-08-31T12:00:00Z")

    def test_three_files_structure(self):
        data = self._norm()
        self.assertEqual(set(data), {"clean_sheets.json", "projected_goals.json",
                                     "fixture_difficulty.json"})
        for name, payload in data.items():
            self.assertEqual(payload["source"], "fpljoe")
            self.assertIn("metadata", payload)
            self.assertEqual(len(payload["fixtures"]), 3)   # GW3-5（骨架覆盖）
            self.assertEqual(len(payload["teams"]), 6)      # 每场拆主客
            self.assertNotIn("latest.json", data)

    def test_gw_range_and_missing(self):
        m = self._norm()["clean_sheets.json"]["metadata"]
        self.assertEqual(m["requested_gameweek_start"], 3)
        self.assertEqual(m["requested_gameweek_end"], 5)
        self.assertEqual(m["actual_gameweek_min"], 1)
        self.assertEqual(m["actual_gameweek_max"], 3)
        self.assertEqual(m["missing_gameweeks"], [4, 5])    # 市场缺 4-5，Elevenify 有
        self.assertEqual(m["data_sources"]["odds_market"], [1, 2, 3])
        self.assertEqual(len(m["data_sources"]["elevenify"]), 38)

    def test_dual_source_fields_not_mixed(self):
        data = self._norm()
        pg = data["projected_goals.json"]["fixtures"]
        gw3 = next(f for f in pg if f["gameweek"] == 3)
        gw5 = next(f for f in pg if f["gameweek"] == 5)
        # GW3：市场 λ 有值 + Elevenify 有值
        self.assertIsNotNone(gw3["home_projected_goals"])
        self.assertIsNotNone(gw3["home_projected_goals_elevenify"])
        # GW5：市场 λ 为 null（源未发布），Elevenify 有值
        self.assertIsNone(gw5["home_projected_goals"])
        self.assertIsNotNone(gw5["home_projected_goals_elevenify"])
        # 三文件字段不混用
        cs0 = data["clean_sheets.json"]["fixtures"][0]
        self.assertNotIn("home_projected_goals", cs0)
        df0 = data["fixture_difficulty.json"]["fixtures"][0]
        self.assertNotIn("home_projected_goals", df0)
        self.assertNotIn("home_clean_sheet_probability", df0)
        self.assertEqual(df0["difficulty_source"], "odds_market")

    def test_fpl_team_mapping(self):
        data = self._norm()
        f0 = data["projected_goals.json"]["fixtures"][0]
        self.assertEqual(f0["fpl_home_team_id"], 1)     # ARS → 1
        self.assertEqual(f0["fpl_away_team_id"], 14)    # LIV → 14
        t0 = data["projected_goals.json"]["teams"][0]
        self.assertEqual(t0["fpl_team_name"], "Arsenal")

    def test_unmapped_team_keeps_name_with_warning(self):
        raw = mk_raw()
        # 骨架 index 2 = GW3（normalize 范围 3-5 内）
        raw["supplementalProjections"]["fixtures"][2]["awayTeamCode"] = "ZZZ"
        raw["supplementalProjections"]["fixtures"][2]["awayTeamName"] = "Zeta FC"
        data = fpl_joe.normalize(raw, 3, 5, "2026-08-31T12:00:00Z")
        f0 = data["projected_goals.json"]["fixtures"][0]
        self.assertEqual(f0["away_team"], "Zeta FC")          # 不丢弃
        self.assertIsNone(f0["fpl_away_team_id"])
        self.assertTrue(any("Zeta FC" in w for w in data["projected_goals.json"]
                            ["metadata"]["warnings"]))

    def test_elevenify_value_correct_gw_slot(self):
        data = self._norm()
        f4 = next(f for f in data["projected_goals.json"]["fixtures"]
                  if f["gameweek"] == 4)
        # ARS goals 全 1.0（mk_raw: 1.0 + i，i=0 → 1.0）
        self.assertEqual(f4["home_projected_goals_elevenify"], 1.0)


class TestRefresh(unittest.TestCase):
    def test_fetch_failure_keeps_previous_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "enabled": True, "competition": "premier-league",
                "projections_url": "https://example.invalid/projections",
                "status_url": "https://example.invalid/status",
                "timeout_seconds": 1, "retry_times": 0,
                "freshness_ttl_hours": 6, "freshness_max_age_hours": 48,
                "gameweeks_ahead": 5, "user_agent": "test",
            }
            output = os.path.join(tmp, "fpljoe")
            cfg["output_dir"] = output
            # 旧数据存在
            os.makedirs(output)
            with open(os.path.join(output, "clean_sheets.json"), "w", encoding="utf-8") as f:
                f.write('{"old": true}')
            with mock.patch.object(fpl_joe, "_fetch_json",
                                   side_effect=fpl_joe.FplJoeError("network down")):
                notes = fpl_joe.refresh("2026-27", 3, cfg)
            self.assertTrue(any("失败" in n["detail"] for n in notes))
            # 旧数据未被覆盖
            with open(os.path.join(output, "clean_sheets.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"old": True})

    def test_disabled_returns_note_without_fetch(self):
        cfg = {"enabled": False}
        with mock.patch.object(fpl_joe, "_fetch_json", side_effect=AssertionError("不应抓取")):
            notes = fpl_joe.refresh("2026-27", 3, cfg)
        self.assertTrue(any("禁用" in n["detail"] for n in notes))

    def test_refresh_writes_three_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "enabled": True, "competition": "premier-league",
                "projections_url": "https://example.invalid/projections",
                "status_url": "https://example.invalid/status",
                "timeout_seconds": 1, "retry_times": 0,
                "freshness_ttl_hours": 6, "freshness_max_age_hours": 48,
                "gameweeks_ahead": 5, "user_agent": "test",
                "output_dir": "fpljoe",
            }
            with mock.patch.object(fpl_joe, "_fetch_json", return_value=mk_raw()):
                with mock.patch.object(data_store, "save_json") as mock_save:
                    notes = fpl_joe.refresh("2026-27", 3, cfg)
            self.assertEqual(mock_save.call_count, 3)
            self.assertTrue(any("已刷新" in n["detail"] for n in notes))


if __name__ == "__main__":
    unittest.main()
