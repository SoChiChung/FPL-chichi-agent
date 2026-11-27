"""Regression & unit tests for P0/P1/P2 fixes:
- api.points_by_round reading total_points + DGW summing
- live.snapshot preferring /event/{gw}/live/ over entry_history points=0 + auto-subs + VC fallback
- context.project_five_gw_points individual xP v2 differentiation (superstar vs benchwarmer)
- transfer.py real selling_price budget + 2-step downgrade+upgrade combo + FT rollover hurdle
- history_writer.update_live_result preserving pre-deadline GW decisions
"""
import unittest
from unittest import mock

from brain import api, context, history_writer, live, transfer


class TestPointsByRoundTotalPoints(unittest.TestCase):
    def test_reads_total_points_and_sums_dgw(self):
        summary = {
            "history": [
                {"round": 10, "total_points": 9},
                {"round": 11, "total_points": 6},
                {"round": 11, "total_points": 8},  # DGW second match
                {"round": 12, "total_points": -1},
            ]
        }
        self.assertEqual(api.points_by_round(summary), {10: 9, 11: 14, 12: -1})


class TestLiveScoringAndAutoSub(unittest.TestCase):
    def test_live_prefers_event_live_over_zero_entry_history(self):
        event = {"id": 12, "finished": False, "data_checked": False}
        fixtures = [
            {"event": 12, "team_h": 1, "team_a": 2, "started": True, "finished": True, "finished_provisional": True},
            {"event": 12, "team_h": 3, "team_a": 4, "started": False, "finished": False, "finished_provisional": False},
        ]
        # FPL sets points=0 in entry_history as soon as GW starts
        entry_history = {"current": [{"event": 12, "points": 0, "rank": None, "event_transfers_cost": 4}]}
        picks = {
            "active_chip": None,
            "entry_history": {"event_transfers_cost": 4},
            "picks": [
                {"element": 1, "position": 1, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
                # Captain (id=2, team 1) played 0 mins and match finished -> VC (id=3, team 2) promoted!
                {"element": 2, "position": 2, "multiplier": 2, "is_captain": True, "is_vice_captain": False},
                {"element": 3, "position": 3, "multiplier": 1, "is_captain": False, "is_vice_captain": True},
                {"element": 4, "position": 4, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
                {"element": 5, "position": 5, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
                {"element": 6, "position": 6, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
                {"element": 7, "position": 7, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
                {"element": 8, "position": 8, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
                {"element": 9, "position": 9, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
                {"element": 10, "position": 10, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
                {"element": 11, "position": 11, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
                {"element": 12, "position": 12, "multiplier": 0, "is_captain": False, "is_vice_captain": False},
                # Bench 1 (id=13, MID, team 2) played 90 mins & scored 7 pts -> auto-subs in for id=2!
                {"element": 13, "position": 13, "multiplier": 0, "is_captain": False, "is_vice_captain": False},
                {"element": 14, "position": 14, "multiplier": 0, "is_captain": False, "is_vice_captain": False},
                {"element": 15, "position": 15, "multiplier": 0, "is_captain": False, "is_vice_captain": False},
            ],
        }
        players_map = {
            1: {"pos": "GKP", "team_id": 1},
            2: {"pos": "DEF", "team_id": 1},  # 0 mins finished (still leaves 3 DEFs: 3,4,5)
            3: {"pos": "DEF", "team_id": 2},
            4: {"pos": "DEF", "team_id": 1},
            5: {"pos": "DEF", "team_id": 2},
            6: {"pos": "MID", "team_id": 3},
            7: {"pos": "MID", "team_id": 3},
            8: {"pos": "MID", "team_id": 3},
            9: {"pos": "FWD", "team_id": 4},
            10: {"pos": "FWD", "team_id": 4},
            11: {"pos": "FWD", "team_id": 4},
            12: {"pos": "GKP", "team_id": 2},
            13: {"pos": "MID", "team_id": 2},
            14: {"pos": "MID", "team_id": 2},
            15: {"pos": "DEF", "team_id": 2},
        }
        live_payload = {
            "elements": [
                {"id": 1, "stats": {"total_points": 6, "minutes": 90}},
                {"id": 2, "stats": {"total_points": 0, "minutes": 0}},
                {"id": 3, "stats": {"total_points": 10, "minutes": 90}},  # VC -> 10*2 = 20
                {"id": 4, "stats": {"total_points": 2, "minutes": 90}},
                {"id": 5, "stats": {"total_points": 5, "minutes": 90}},
                {"id": 13, "stats": {"total_points": 7, "minutes": 90}},  # Auto-sub -> +7
            ] + [{"id": pid, "stats": {"total_points": 0, "minutes": 0}} for pid in (6, 7, 8, 9, 10, 11, 12, 14, 15)]
        }
        with mock.patch.object(api, "get_event_live", return_value=live_payload), \
             mock.patch.object(api, "get_live_rank_estimate", side_effect=api.FplApiError("skip")):
            snap = live.snapshot(12, event, fixtures, entry_history, picks, 12, 10049242, players_map=players_map)
        self.assertEqual(snap["points_source"], "fpl_event_live")
        # 6 (id1) + 20 (id3 VCx2) + 2 (id4) + 5 (id5) + 7 (id13 auto-sub) = 40.0 gross, 36.0 net
        self.assertEqual(snap["points"], 40.0)
        self.assertEqual(snap["net_points"], 36.0)
        self.assertTrue(snap["captain_promoted"])
        self.assertEqual(snap["players"]["13"]["auto_sub"], "in")
        self.assertEqual(snap["players"]["2"]["auto_sub"], "out")


class TestIndividualXpModel(unittest.TestCase):
    def test_superstar_striker_projects_far_above_benchwarmer(self):
        players = {
            1: {
                "name": "Haaland", "pos": "FWD", "team": "MCI", "team_id": 1,
                "price": 15.7, "minutes": 990, "starts": 11,
                "expected_goals": 8.1, "expected_assists": 0.9,
                "points_per_game": 6.5, "form": "5.0", "bonus": 15,
                "penalties_order": 1, "status": "a", "chance_of_playing_next_round": None,
            },
            2: {
                "name": "BenchKid", "pos": "MID", "team": "AVL", "team_id": 2,
                "price": 4.9, "minutes": 380, "starts": 4,
                "expected_goals": 0.4, "expected_assists": 0.2,
                "points_per_game": 2.2, "form": "3.0", "bonus": 0,
                "penalties_order": None, "status": "a", "chance_of_playing_next_round": None,
            },
            3: {
                "name": "TeamMate", "pos": "MID", "team": "MCI", "team_id": 1,
                "price": 8.0, "minutes": 900, "starts": 10,
                "expected_goals": 3.0, "expected_assists": 3.0,
                "points_per_game": 5.0, "form": "4.5", "bonus": 6,
                "penalties_order": None, "status": "a", "chance_of_playing_next_round": None,
            },
            4: {
                "name": "VillaStar", "pos": "FWD", "team": "AVL", "team_id": 2,
                "price": 7.5, "minutes": 900, "starts": 10,
                "expected_goals": 5.5, "expected_assists": 1.5,
                "points_per_game": 5.2, "form": "6.0", "bonus": 8,
                "penalties_order": 1, "status": "a", "chance_of_playing_next_round": None,
            },
        }
        fixtures = [
            {"event": gw, "team_h": 1, "team_a": 2, "team_h_difficulty": 3, "team_a_difficulty": 3, "started": False}
            for gw in range(12, 17)
        ]
        scores = {gw: {1: 40.0, 2: 65.0, 3: 50.0, 4: 70.0} for gw in range(12, 17)}
        context.project_five_gw_points(
            players, scores, fixtures, 12,
            team_matches_played_by_team={1: 11, 2: 11},
        )
        self.assertGreater(players[1]["five_gw_points"], players[2]["five_gw_points"] + 12.0)


class TestSellingPriceAndDowngradeCombo(unittest.TestCase):
    def test_selling_price_prevents_overspending_appreciated_asset(self):
        # Player 1 rose from £6.0m to £7.0m -> selling_price is £6.5m!
        # With bank £0.5m, real available is £7.0m, so £7.3m target MUST be rejected.
        squad = [
            {"id": 1, "name": "RisenMid", "pos": "MID", "team": "ARS", "price": 7.0,
             "selling_price": 6.5, "market_score": 20.0, "status": "a", "starting": True},
        ] + [
            {"id": 10 + i, "name": f"P{i}", "pos": pos, "team": f"T{i}", "price": 5.0,
             "market_score": 70.0, "status": "a", "starting": i < 10}
            for i, pos in enumerate(["GKP", "GKP", "DEF", "DEF", "DEF", "DEF", "DEF",
                                     "MID", "MID", "MID", "MID", "FWD", "FWD", "FWD"])
        ]
        players = {p["id"]: p for p in squad}
        players[99] = {"id": 99, "name": "TooPricey", "pos": "MID", "team": "LIV",
                       "price": 7.3, "market_score": 85.0, "status": "a"}
        scores = {pid: p["market_score"] for pid, p in players.items()}
        cfg = {"market_gap_threshold": 15, "injury_threshold": 75, "max_players_per_team": 3, "max_free_transfers": 5}
        sug, notes = transfer.evaluate_transfers(
            squad, players, scores, cfg, bank=0.5,
            transfer_status={"status": "limited", "free_transfers": 1},
        )
        self.assertEqual(sug, [])
        self.assertTrue(any("预算不足" in n.get("detail", "") for n in notes))

    def test_two_transfer_downgrade_gk_to_upgrade_mid(self):
        # Squad has two expensive GKs (£6.2m + £4.7m) and a weak MID (£5.5m).
        # With 2 FTs and £0.0m bank, selling £6.2m bench GK -> £4.0m fodder frees £2.2m
        # to upgrade £5.5m MID -> £7.5m star MID!
        gws = [12, 13, 14, 15, 16]
        def mp(pid, name, pos, team, price, h_pts, starting=True, start_p=0.95):
            return {
                "id": pid, "name": name, "pos": pos, "team": team,
                "price": price, "selling_price": price, "market_score": 50.0,
                "horizon_points": [h_pts] * 5, "five_gw_points": round(h_pts * 5, 2),
                "projection_gws": gws, "starting": starting,
                "starting_probability": start_p, "status": "a",
            }
        squad = [
            mp(1, "Verbruggen", "GKP", "BHA", 4.7, 4.2, starting=True),
            mp(2, "Raya", "GKP", "ARS", 6.2, 4.0, starting=False),
            mp(3, "D1", "DEF", "ARS", 5.5, 4.5),
            mp(4, "D2", "DEF", "LIV", 5.5, 4.5),
            mp(5, "D3", "DEF", "MCI", 5.5, 4.5),
            mp(6, "D4", "DEF", "CHE", 4.5, 3.5, starting=False),
            mp(7, "D5", "DEF", "IPS", 4.0, 2.5, starting=False),
            mp(8, "WeakMid", "MID", "AVL", 5.5, 2.6, starting=True),
            mp(9, "M2", "MID", "BOU", 6.5, 4.8),
            mp(10, "M3", "MID", "CHE", 7.5, 5.0),
            mp(11, "M4", "MID", "MUN", 7.5, 4.8),
            mp(12, "M5", "MID", "FUL", 5.0, 2.5, starting=False),
            mp(13, "Haaland", "FWD", "MCI", 15.0, 7.0),
            mp(14, "F2", "FWD", "BOU", 6.5, 5.0),
            mp(15, "F3", "FWD", "AVL", 7.0, 4.8),
        ]
        players = {p["id"]: p for p in squad}
        players[200] = mp(200, "FodderGK", "GKP", "SOU", 4.0, 0.2, starting=False, start_p=0.05)
        players[201] = mp(201, "Wirtz", "MID", "LIV", 7.5, 6.2, starting=True, start_p=0.95)
        scores = {pid: p["market_score"] for pid, p in players.items()}
        cfg = {
            "allow_hits": False, "market_gap_threshold": 15, "injury_threshold": 75,
            "max_players_per_team": 3, "max_free_transfers": 5, "min_free_transfer_gain": 3.5,
        }
        sug, _ = transfer.evaluate_transfers(
            squad, players, scores, cfg, bank=0.0,
            transfer_status={"status": "limited", "free_transfers": 2},
        )
        in_ids = {s["in"]["id"] for s in sug}
        out_ids = {s["out"]["id"] for s in sug}
        self.assertEqual(out_ids, {2, 8})
        self.assertEqual(in_ids, {200, 201})


class TestLiveHistorySeparation(unittest.TestCase):
    def test_update_live_result_does_not_overwrite_locked_decision(self):
        hist = {
            "season": "2026-27", "manager_id": 1,
            "history": [{
                "gw": 11, "points": None, "rank": None, "overall_rank": None,
                "decided_at": "2026-11-20T10:00:00Z",
                "decision": {"formation": "433", "captain": {"id": 9, "name": "Haaland"}},
                "notes": [{"topic": "captain", "detail": "locked"}],
            }],
        }
        history_writer.update_live_result(hist, 11, {"gw": 11, "status": "live", "points": 42.0})
        self.assertEqual(hist["history"][0]["decision"]["formation"], "433")
        self.assertEqual(hist["history"][0]["live"]["points"], 42.0)


if __name__ == "__main__":
    unittest.main()
