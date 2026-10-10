"""Synthetic-data tests for FPL chip availability and recommendations."""
import unittest

from brain import chips


class TestChipStrategy(unittest.TestCase):
    def test_chips_refresh_for_second_half(self):
        history = {"chips": [{"name": "wildcard", "event": 10},
                             {"name": "triple_captain", "event": 22}]}
        first = chips._availability(history, 15)
        second = chips._availability(history, 25)
        self.assertFalse(first["wildcard"])
        self.assertTrue(first["triple_captain"])
        self.assertTrue(second["wildcard"])
        self.assertFalse(second["triple_captain"])

    def test_double_gameweek_triple_captain_recommendation(self):
        teams = [{"id": i, "short_name": f"T{i}"} for i in range(1, 16)]
        events = [{"id": 12, "finished": False}, {"id": 13, "finished": False}, {"id": 14, "finished": False}]
        fixtures = [{"event": 12, "team_h": 1, "team_a": 2,
                     "team_h_difficulty": 2, "team_a_difficulty": 4},
                    {"event": 12, "team_h": 1, "team_a": 3,
                     "team_h_difficulty": 2, "team_a_difficulty": 4},
                    {"event": 12, "team_h": 4, "team_a": 5,
                     "team_h_difficulty": 3, "team_a_difficulty": 3},
                    {"event": 13, "team_h": 6, "team_a": 7,
                     "team_h_difficulty": 3, "team_a_difficulty": 3}]
        squad = [{"id": i, "team": f"T{i}", "starting": i <= 11,
                  "lineup_score": 20.0, "status": "a"} for i in range(1, 16)]
        captain = {"id": 1, "team": "T1", "captain_score": 80}
        result = chips.evaluate(squad, captain, {"chips": []}, events,
                                fixtures, teams, 12, {"chip_strategy": {"planning_horizon": 3,
                                                                        "season_half_horizon": 3,
                                                                        "free_hit_weak_player_threshold": 13}})
        self.assertEqual(result["current_recommendation"]["chip"], "triple_captain")
        self.assertEqual(result["signals"]["captain_fixture_count"], 2)

    def test_free_hit_targets_many_difficult_fixtures(self):
        teams = [{"id": i, "short_name": f"T{i}"} for i in range(1, 16)]
        squad = [{"id": i, "team": f"T{i}", "starting": i <= 11,
                  "lineup_score": 20.0, "status": "a"} for i in range(1, 16)]
        fixtures = [{"event": 5, "team_h": i, "team_a": 20 + i,
                     "team_h_difficulty": 5, "team_a_difficulty": 2}
                    for i in range(1, 9)]
        events = [{"id": 5, "finished": False}, {"id": 6, "finished": False}]
        result = chips.evaluate(squad, {"team": "T1", "captain_score": 50}, {"chips": []},
                                events, fixtures, teams, 5,
                                {"chip_strategy": {"planning_horizon": 2,
                                                   "free_hit_weak_player_threshold": 7}})
        self.assertEqual(result["current_recommendation"]["chip"], "free_hit")
        self.assertIn("赛程偏弱", result["current_recommendation"]["reason"])

    def test_wildcard_includes_follow_up_schedule(self):
        teams = [{"id": i, "short_name": f"T{i}"} for i in range(1, 16)]
        squad = [{"id": i, "team": f"T{i}", "starting": i <= 11,
                  "lineup_score": 10.0, "status": "i" if i <= 5 else "a"} for i in range(1, 16)]
        events = [{"id": i, "finished": False} for i in range(1, 10)]
        result = chips.evaluate(squad, None, {"chips": []}, events, [], teams, 1,
                                {"chip_strategy": {"planning_horizon": 9}})
        self.assertEqual(result["current_recommendation"]["chip"], "wildcard")
        self.assertEqual(result["current_recommendation"]["follow_up_plan"]["bench_boost_gw"], 4)
        self.assertEqual(result["current_recommendation"]["follow_up_plan"]["triple_captain_gw"], 6)

    def test_only_one_chip_per_gameweek(self):
        result = chips.evaluate([], None, {"chips": [{"name": "bench_boost", "event": 8}]},
                                [{"id": 8, "finished": False}], [], [], 8, {})
        self.assertIsNone(result["current_recommendation"])
        self.assertTrue(result["chip_already_used_this_gw"])

    def test_guarantee_mode_when_chips_match_remaining_gws(self):
        availability = chips._availability({"chips": []}, 18)
        # Four chips remain and only two deadlines remain, so guard mode must activate.
        result = chips.evaluate([], None, {"chips": []},
                                [{"id": 18, "finished": False}, {"id": 19, "finished": False}],
                                [], [], 18,
                                {"chip_strategy": {"chip_guarantee_start_gw": 16,
                                                   "guarantee_priority": ["free_hit", "triple_captain", "bench_boost", "wildcard"]}})
        self.assertEqual(len([v for v in availability.values() if v]), 4)
        self.assertTrue(result["guarantee"]["active"])
        self.assertEqual(result["guarantee"]["mode"], "must_schedule")
        self.assertTrue(result["current_recommendation"]["safety_net"])
        self.assertEqual(result["current_recommendation"]["chip"], "free_hit")

    def test_no_chip_wasted_after_half_season_reset(self):
        result = chips.evaluate([], None, {"chips": []},
                                [{"id": 19, "finished": False}, {"id": 20, "finished": False}],
                                [], [], 19, {"chip_strategy": {"chip_guarantee_start_gw": 16}})
        self.assertEqual(result["guarantee"]["mode"], "must_schedule")


if __name__ == "__main__":
    unittest.main()
