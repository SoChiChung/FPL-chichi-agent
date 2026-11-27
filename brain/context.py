"""GW 上下文构建：把原始 API 数据转换为 state / history 结构。

决策系统 (Phase 1+) 将基于这里产出的 context 工作。
"""
from datetime import datetime, timezone

from brain import config
from brain.market import to_float


def build_player_map(bootstrap: dict) -> dict:
    """element_id -> 球员决策字段（位置/球队/价格/持有率/健康/转会热度）。

    Phase 1 起补充：status、chance_of_playing_next_round、transfers_in_event、
    transfers_out_event、selected_by_percent。原始 API 数据只在内存处理，不落盘。
    """
    team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    team_meta = {t["id"]: t for t in bootstrap["teams"]}
    att_vals = [
        to_float(t.get("strength_attack_home"), 1150) + to_float(t.get("strength_attack_away"), 1150)
        for t in bootstrap["teams"]
    ]
    mean_att = (sum(att_vals) / len(att_vals) / 2.0) if att_vals else 1150.0
    pos_short = {
        et["id"]: et["singular_name_short"] for et in bootstrap["element_types"]
    }
    players = {}
    for p in bootstrap["elements"]:
        selected_by = float(p.get("selected_by_percent") or 0)
        t_obj = team_meta.get(p.get("team")) or {}
        att_h = to_float(t_obj.get("strength_attack_home"), mean_att) / max(1.0, mean_att)
        att_a = to_float(t_obj.get("strength_attack_away"), mean_att) / max(1.0, mean_att)
        players[p["id"]] = {
            "name": p["web_name"],
            "pos": pos_short.get(p["element_type"], "?"),
            "team": team_short.get(p["team"], "?"),
            "team_id": p.get("team"),
            "team_att_h": round(att_h, 4),
            "team_att_a": round(att_a, 4),
            "price": round(p["now_cost"] / 10, 1),
            "now_cost": int(to_float(p.get("now_cost"))),
            "cost_change_start": int(to_float(p.get("cost_change_start"))),
            "selected_by": selected_by,
            "selected_by_percent": selected_by,
            "form": p.get("form", "0"),
            "total_points": p.get("total_points", 0),
            "minutes": p.get("minutes", 0),
            "starts": p.get("starts"),
            "expected_goals": (to_float(p.get("expected_goals"))
                               if p.get("expected_goals") is not None else None),
            "expected_assists": (to_float(p.get("expected_assists"))
                                 if p.get("expected_assists") is not None else None),
            "expected_goal_involvements": (
                to_float(p.get("expected_goal_involvements"))
                if p.get("expected_goal_involvements") is not None else None
            ),
            "clean_sheets": int(to_float(p.get("clean_sheets"))),
            "bonus": int(to_float(p.get("bonus"))),
            "defensive_contribution": (
                to_float(p.get("defensive_contribution"))
                if p.get("defensive_contribution") is not None else None
            ),
            "penalties_order": p.get("penalties_order"),
            "corners_and_indirect_freekicks_order": p.get(
                "corners_and_indirect_freekicks_order"),
            "direct_freekicks_order": p.get("direct_freekicks_order"),
            "status": p.get("status", ""),
            "chance_of_playing_next_round": p.get("chance_of_playing_next_round"),
            "transfers_in_event": p.get("transfers_in_event", 0),
            "transfers_out_event": p.get("transfers_out_event", 0),
            "points_per_game": to_float(p.get("points_per_game")),
        }
    return players


def official_fixture_fallback(fixtures: list, teams: list, gw: int) -> dict:
    """Build team-level FDR inputs and readable opponent labels from official fixtures."""
    team_by_id = {t["id"]: t for t in teams or []}
    grouped = {}
    for fixture in fixtures or []:
        if fixture.get("event") != gw:
            continue
        home_id, away_id = fixture.get("team_h"), fixture.get("team_a")
        home, away = team_by_id.get(home_id), team_by_id.get(away_id)
        if not home or not away:
            continue
        grouped.setdefault(home_id, []).append({
            "opponent": away.get("short_name"), "venue": "H",
            "difficulty": fixture.get("team_h_difficulty"),
        })
        grouped.setdefault(away_id, []).append({
            "opponent": home.get("short_name"), "venue": "A",
            "difficulty": fixture.get("team_a_difficulty"),
        })
    ratings = {}
    for team_id, rows in grouped.items():
        values = [to_float(r["difficulty"]) for r in rows if r.get("difficulty") is not None]
        ratings[team_by_id[team_id]["short_name"]] = {
            "fixture": sum(values) / len(values) if values else None,
        }
    return {"teams": ratings, "by_team_id": grouped}


_GOAL_POINTS = {"GKP": 6.0, "DEF": 6.0, "MID": 5.0, "FWD": 4.0}
_CS_POINTS = {"GKP": 4.0, "DEF": 4.0, "MID": 1.0, "FWD": 0.0}
_POS_PPG_PRIOR = {"GKP": 3.4, "DEF": 3.3, "MID": 3.5, "FWD": 3.6}
_POS_XG_SHARE_PRIOR = {"GKP": 0.0, "DEF": 0.04, "MID": 0.14, "FWD": 0.26}
_POS_XA_SHARE_PRIOR = {"GKP": 0.005, "DEF": 0.06, "MID": 0.16, "FWD": 0.10}


def _availability_factor(info: dict, start_prob, gw_offset: int = 0) -> float:
    """Estimate probability of playing in target_gw + gw_offset."""
    status = str(info.get("status") or "").lower()
    chance = info.get("chance_of_playing_next_round")

    # Permanent or indefinite absences (departed/loaned/not in squad) stay 0
    if status in ("u", "n"):
        return 0.0

    if status in ("i", "s"):
        if chance is not None and to_float(chance) > 0:
            avail = min(1.0, to_float(chance) / 100.0 + 0.18 * gw_offset)
        else:
            # Red-flagged injury/suspension: 0 in immediate GW, gradual return risk later
            avail = 0.0 if gw_offset == 0 else min(0.55, 0.18 * gw_offset)
    elif chance is not None:
        base_c = max(0.0, min(1.0, to_float(chance) / 100.0))
        avail = min(1.0, base_c + 0.20 * gw_offset)
    elif status == "d":
        avail = min(1.0, 0.60 + 0.20 * gw_offset)
    else:
        avail = 1.0

    if start_prob is None:
        play_factor = 1.0
    else:
        minutes = to_float(info.get("minutes"))
        if minutes <= 0 and start_prob < 0.15:
            play_factor = 0.03
        else:
            play_factor = 0.15 + 0.85 * to_float(start_prob)
    return round(max(0.0, min(1.0, avail * play_factor)), 4)


def project_five_gw_points(players: dict, model_scores_by_gw: dict,
                           fixtures: list, start_gw: int,
                           team_forecasts_by_gw: dict = None,
                           team_matches_played_by_team: dict = None) -> dict:
    """Compute individual, availability-adjusted 5-GW expected FPL points (xP v2).

    Combines:
      1) Individual goal/assist involvement share (xG/xA share + penalty/set-piece role)
         applied to per-fixture expected team goals & clean-sheet probabilities;
      2) 2026/27 defensive contribution & bonus/save baseline + shrunk historical PPG;
      3) Per-round playing/starting probability & injury recovery curve across 5 GWs;
      4) Per-position calibration metadata for transparency and Free Hit fallback.
    """
    # Count completed matches per club if caller did not pass them
    if team_matches_played_by_team is None:
        team_matches_played_by_team = {}
        for f in fixtures or []:
            ev = f.get("event")
            if ev is not None and ev < start_gw and f.get("started"):
                for tid in (f.get("team_h"), f.get("team_a")):
                    if tid is not None:
                        team_matches_played_by_team[tid] = (
                            team_matches_played_by_team.get(tid, 0) + 1
                        )

    # Club-level season totals of xG and xA + empirical attacking power ratio
    team_xg_sum, team_xa_sum, team_att_meta = {}, {}, {}
    for info in players.values():
        tid = info.get("team_id")
        if tid is None:
            continue
        team_xg_sum[tid] = team_xg_sum.get(tid, 0.0) + max(0.0, to_float(info.get("expected_goals")))
        xa_val = info.get("expected_assists")
        if xa_val is None and info.get("expected_goal_involvements") is not None:
            xa_val = max(0.0, to_float(info.get("expected_goal_involvements")) -
                         max(0.0, to_float(info.get("expected_goals"))))
        team_xa_sum[tid] = team_xa_sum.get(tid, 0.0) + max(0.0, to_float(xa_val))
        if tid not in team_att_meta:
            team_att_meta[tid] = (
                to_float(info.get("team_att_h"), 1.0),
                to_float(info.get("team_att_a"), 1.0),
            )

    xg_per_match_by_team = {}
    for tid, tot_xg in team_xg_sum.items():
        mp = max(1, int(to_float(team_matches_played_by_team.get(tid, 0))))
        if tot_xg > 0 and mp >= 3:
            xg_per_match_by_team[tid] = tot_xg / mp
    league_avg_xg_pm = (
        sum(xg_per_match_by_team.values()) / len(xg_per_match_by_team)
        if xg_per_match_by_team else 1.42
    )

    # Build per-(team_id, event) fixture list with official FDR & venue
    fixture_rows = {}
    for f in fixtures or []:
        ev = f.get("event")
        if ev is None or not start_gw <= ev < start_gw + 5:
            continue
        h_id, a_id = f.get("team_h"), f.get("team_a")
        if h_id is not None:
            fixture_rows.setdefault((h_id, ev), []).append({
                "venue": "H",
                "fdr": to_float(f.get("team_h_difficulty"), 3.0),
                "opp_fdr": to_float(f.get("team_a_difficulty"), 3.0),
            })
        if a_id is not None:
            fixture_rows.setdefault((a_id, ev), []).append({
                "venue": "A",
                "fdr": to_float(f.get("team_a_difficulty"), 3.0),
                "opp_fdr": to_float(f.get("team_h_difficulty"), 3.0),
            })

    # Also compute linear calibration metadata (intercept/slope) for compatibility
    by_position = {}
    for pid, info in players.items():
        score = model_scores_by_gw.get(start_gw, {}).get(pid)
        ppg = info.get("points_per_game")
        if (score is None or ppg is None or to_float(ppg) <= 0
                or to_float(info.get("minutes")) < 90):
            continue
        by_position.setdefault(info.get("pos"), []).append(
            (to_float(score), to_float(ppg)))

    calibrations = {}
    for pos, rows in by_position.items():
        mean_score = sum(x for x, _ in rows) / len(rows)
        mean_points = sum(y for _, y in rows) / len(rows)
        variance = sum((x - mean_score) ** 2 for x, _ in rows)
        covariance = sum((x - mean_score) * (y - mean_points) for x, y in rows)
        slope = max(0.025, covariance / variance if variance > 1e-9 else 0.025)
        calibrations[pos] = {
            "intercept": round(mean_points - slope * mean_score, 4),
            "slope": round(slope, 4),
            "samples": len(rows),
        }

    event_ids = list(range(start_gw, start_gw + 5))
    for pid, info in players.items():
        pos = info.get("pos") or "MID"
        tid = info.get("team_id")
        abbr = info.get("team")
        played_matches = int(to_float(team_matches_played_by_team.get(tid, 0)))
        starts = info.get("starts")
        if starts is not None and played_matches >= 3:
            start_prob = max(0.02, min(1.0, (to_float(starts) + 0.5) / (played_matches + 1.0)))
            info["starting_probability"] = round(start_prob, 4)
        else:
            start_prob = None
            info["starting_probability"] = None

        minutes = max(0.0, to_float(info.get("minutes")))
        nineties = minutes / 90.0
        # Price-aware prior only matters before sufficient minutes accumulate;
        # once >= 270 mins played, actual form + PPG + per-90 xGI dominate (no £15.7m free pass!).
        price = to_float(info.get("price"), 5.0)
        price_excess = max(0.0, price - (4.5 if pos in ("GKP", "DEF") else 5.0))
        ppg_prior = _POS_PPG_PRIOR.get(pos, 3.4) + (
            0.16 * price_excess if pos in ("MID", "FWD") else 0.14 * price_excess
        )
        raw_ppg = to_float(info.get("points_per_game"))
        sample_w = minutes / (minutes + 240.0) if minutes > 0 else 0.0
        shrunk_ppg = sample_w * raw_ppg + (1.0 - sample_w) * ppg_prior
        form_val = to_float(info.get("form"))
        # Core philosophy: "进攻球员看状态，防守球员看赛程"
        # Attackers (MID/FWD) respond strongly to recent Form; DEF/GKP ignore fluke goal form.
        if minutes >= 90:
            if pos in ("MID", "FWD"):
                clamped_form = max(1.2, min(9.5, form_val))
                shrunk_ppg = 0.55 * shrunk_ppg + 0.45 * clamped_form
                form_attack_mult = max(0.76, min(1.22, 0.80 + 0.048 * clamped_form))
            else:
                shrunk_ppg = 0.90 * shrunk_ppg + 0.10 * min(7.0, max(2.0, form_val))
                form_attack_mult = 1.0
        else:
            form_attack_mult = 1.0

        # Per-90 share of club expected goals (prevents penalizing stars on deep-rotation top clubs
        # and prevents inflating "big fish in a tiny pond" strikers on low-xG clubs).
        club_xg_pm = xg_per_match_by_team.get(tid, league_avg_xg_pm)
        has_xg_data = info.get("expected_goals") is not None and team_xg_sum.get(tid, 0.0) > 0
        if has_xg_data and nineties >= 1.0:
            xg_per_90 = max(0.0, to_float(info.get("expected_goals"))) / nineties
            raw_xg_share = min(0.50, xg_per_90 / max(1.15, club_xg_pm))
            xg_w = minutes / (minutes + 270.0)
            xg_share = xg_w * raw_xg_share + (1.0 - xg_w) * _POS_XG_SHARE_PRIOR.get(pos, 0.10)
        else:
            xg_share = _POS_XG_SHARE_PRIOR.get(pos, 0.10) + 0.018 * price_excess

        if info.get("penalties_order") == 1:
            xg_share = min(0.55, xg_share + 0.04)

        xa_raw = info.get("expected_assists")
        if xa_raw is None and info.get("expected_goal_involvements") is not None:
            xa_raw = max(0.0, to_float(info.get("expected_goal_involvements")) -
                         max(0.0, to_float(info.get("expected_goals"))))
        if xa_raw is not None and team_xa_sum.get(tid, 0.0) > 0 and nineties >= 1.0:
            xa_per_90 = max(0.0, to_float(xa_raw)) / nineties
            raw_xa_share = min(0.38, xa_per_90 / max(1.05, club_xg_pm))
            xa_w = minutes / (minutes + 270.0)
            xa_share = xa_w * raw_xa_share + (1.0 - xa_w) * _POS_XA_SHARE_PRIOR.get(pos, 0.08)
        else:
            xa_share = _POS_XA_SHARE_PRIOR.get(pos, 0.08) + 0.010 * price_excess

        if info.get("corners_and_indirect_freekicks_order") == 1 or info.get("direct_freekicks_order") == 1:
            xa_share = min(0.42, xa_share + 0.025)

        info["xgi_share"] = round(xg_share + xa_share, 4)

        # Club relative attacking power (1.0 = league average; >1.15 = elite attack; <0.90 = weak attack)
        att_h_ratio, att_a_ratio = team_att_meta.get(tid, (1.0, 1.0))
        emp_att_ratio = (club_xg_pm / max(0.8, league_avg_xg_pm)) if tid in xg_per_match_by_team else 1.0

        # Bonus + 2026/27 Defensive Contribution per-90 baseline
        eff_apps = max(3.0, nineties)
        bonus_rate = min(1.2, max(0.0, to_float(info.get("bonus")) / eff_apps))
        dc_val = info.get("defensive_contribution")
        # In 2026/27 bootstrap, defensive_contribution is total DC actions; ~10-12 per match = 2 pts
        dc_pts_rate = 0.0
        if dc_val is not None and pos in ("DEF", "MID", "FWD") and nineties >= 1.0:
            dc_per_90 = to_float(dc_val) / nineties
            threshold = 10.0 if pos == "DEF" else 12.0
            if dc_per_90 >= threshold * 0.65:
                dc_pts_rate = min(1.3, max(0.0, (dc_per_90 / threshold) ** 1.5 * 0.9))

        event_points = []
        event_model_scores = []
        for offset, event in enumerate(event_ids):
            matches = fixture_rows.get((tid, event), [])
            match_count = len(matches)
            model_score = model_scores_by_gw.get(event, {}).get(pid)
            event_model_scores.append(
                round(to_float(model_score), 2) if model_score is not None else None
            )
            if not match_count:
                event_points.append(0.0)
                continue

            # Resolve team projected goals & clean sheet probability for this GW
            gw_team_data = ((team_forecasts_by_gw or {}).get(event) or {}).get("teams") or {}
            gw_neutral = ((team_forecasts_by_gw or {}).get(event) or {}).get("neutral") or {}
            club_row = gw_team_data.get(abbr) or {}
            proj_goals = None if gw_neutral.get("projection") else club_row.get("projection")
            cs_prob = None if gw_neutral.get("clean_sheet") else club_row.get("clean_sheet")
            if cs_prob is not None and to_float(cs_prob) > 1.0:
                cs_prob = to_float(cs_prob) / 100.0

            gw_full90_total = 0.0
            for m_idx, m_info in enumerate(matches):
                fdr = m_info["fdr"]
                is_home = m_info["venue"] == "H"
                venue_boost = 1.07 if is_home else 0.93
                club_att_power = 0.55 * (att_h_ratio if is_home else att_a_ratio) + 0.45 * emp_att_ratio
                # Use FPL Joe team expected goals when available; otherwise scale by REAL club attack power
                # (so Liverpool projects ~1.9+ goals while Everton/Sunderland project ~1.0-1.15 goals!).
                if proj_goals is not None:
                    lam = max(0.35, to_float(proj_goals) / match_count)
                else:
                    fdr_att_factor = max(0.52, 1.0 + 0.18 * (3.0 - fdr))
                    lam = max(0.40, 1.42 * (club_att_power ** 1.25) * fdr_att_factor * venue_boost)

                if cs_prob is not None:
                    p_cs = max(0.03, min(0.68, to_float(cs_prob) / match_count if match_count == 1 else to_float(cs_prob)))
                else:
                    p_cs = max(0.06, min(0.54, (0.28 + 0.085 * (3.0 - fdr)) * venue_boost))

                # Non-linear underdog squeeze: non-elite attacks (e.g. Barry at EVE, Le Fée at SUN)
                # suffer severe open-play service drought when facing tough defenses (FDR >= 4)!
                underdog_squeeze = 1.0
                if pos in ("MID", "FWD") and fdr >= 4.0 and club_att_power < 1.05:
                    underdog_squeeze = 0.62 if fdr >= 4.5 else 0.72

                import math
                lam_opp = min(3.2, max(0.4, -math.log(max(0.04, p_cs))))

                app_pts = 2.0
                att_pts = lam * underdog_squeeze * form_attack_mult * (
                    xg_share * _GOAL_POINTS.get(pos, 5.0)
                    + xa_share * 3.0 * 0.90
                )
                cs_pts = p_cs * _CS_POINTS.get(pos, 0.0)
                gc_pen = (0.45 * max(0.0, lam_opp - 0.85)) if pos in ("GKP", "DEF") else 0.0
                save_pts = min(1.3, 0.28 * lam_opp + 0.35) if pos == "GKP" else 0.0

                physics_xp = max(0.8, app_pts + att_pts + cs_pts - gc_pen + save_pts + bonus_rate + dc_pts_rate)

                # Fixture-adjusted empirical PPG anchor (steeper penalty for weak teams vs strong defenses)
                fdr_slope = 0.14 if (pos in ("GKP", "DEF") or club_att_power < 1.0) else 0.09
                fdr_mult = max(0.58, min(1.32, (1.0 + fdr_slope * (3.0 - fdr)) * venue_boost))
                if pos in ("MID", "FWD") and fdr >= 4.0 and club_att_power < 1.05:
                    fdr_mult *= 0.82
                ppg_xp = shrunk_ppg * fdr_mult

                # If model_score exists (synthetic tests without xG), incorporate gentle tilt
                if not has_xg_data and model_score is not None and raw_ppg <= 0:
                    cal = calibrations.get(pos, {})
                    per_match_raw = max(
                        0.0,
                        to_float(cal.get("intercept"), 2.0) + to_float(cal.get("slope"), 0.04) * to_float(model_score),
                    )
                else:
                    per_match_raw = 0.65 * physics_xp + 0.35 * ppg_xp

                gw_full90_total += max(0.0, min(13.0, per_match_raw))

            avail_mult = _availability_factor(info, start_prob, gw_offset=offset)
            event_points.append(round(gw_full90_total * avail_mult, 2))

        info["model_scores_by_gw"] = event_model_scores
        info["projection_gws"] = event_ids
        info["horizon_points"] = event_points
        info["five_gw_points"] = round(sum(event_points), 2)
    return calibrations


def resolve_current_gw(events: list) -> int:
    """当前 GW = 最近的未开始/进行中的轮次；赛季结束则返回最后一轮。"""
    upcoming = [e for e in events if not e.get("finished")]
    if upcoming:
        return min(e["id"] for e in upcoming)
    finished = [e for e in events if e.get("finished")]
    return max(e["id"] for e in finished) if finished else 1


def _next_deadline(events: list) -> str:
    """下一截止时间 = 第一个尚未到期的轮次；赛季结束则取最后一轮。"""
    upcoming = []
    for e in sorted(events, key=lambda x: x.get("id", 0)):
        dt = e.get("deadline_time", "")
        if not dt:
            continue
        parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if parsed > datetime.now(timezone.utc):
            return dt
        upcoming.append(dt)
    return upcoming[-1] if upcoming else ""


def resolve_target_gw(events: list) -> int:
    """目标 GW（Phase 2 评分针对的轮次）= next_deadline 对应的 event id。

    即第一个 deadline_time 仍在未来的轮次（当前轮进行中也往前看一场）；
    赛季结束（无未来截止）时返回最后一个 finished 轮次作兜底。
    """
    deadline = _next_deadline(events)
    for e in events:
        if e.get("deadline_time") == deadline:
            return e["id"]
    finished = [e for e in events if e.get("finished")]
    return max(e["id"] for e in finished) if finished else 1


def build_formation(team: list) -> str:
    """由首发 11 人按 DEF-MID-FWD 计数得到阵型，如 "343"。"""
    starters = [t for t in team if t.get("starting")]
    if len(starters) != 11:
        return ""
    counts = {}
    for t in starters:
        counts[t["pos"]] = counts.get(t["pos"], 0) + 1
    return f"{counts.get('DEF', 0)}{counts.get('MID', 0)}{counts.get('FWD', 0)}"


def _infer_prices_from_transfers(element_id: int, info: dict, latest_in_cost: dict) -> tuple:
    """Derive exact FPL (purchase_price, selling_price) in £m without authentication.

    1) If player was transferred in via /entry/{id}/transfers/, element_in_cost is exact (0.1m int).
    2) Otherwise (initial squad member), approximate purchase cost via now_cost - cost_change_start.
    FPL rule: manager keeps floor((now_cost - purchase_cost) / 2) when now_cost > purchase_cost,
    and takes full loss when now_cost <= purchase_cost.
    """
    now_tenths = int(round(to_float(info.get("price")) * 10))
    if element_id in latest_in_cost and latest_in_cost[element_id] is not None:
        buy_tenths = int(to_float(latest_in_cost[element_id]))
    elif info.get("now_cost") is not None:
        buy_tenths = max(35, now_tenths - int(to_float(info.get("cost_change_start"))))
    else:
        return None, None
    if now_tenths > buy_tenths:
        sell_tenths = buy_tenths + (now_tenths - buy_tenths) // 2
    else:
        sell_tenths = now_tenths
    return round(buy_tenths / 10.0, 1), round(sell_tenths / 10.0, 1)


def build_state(
    bootstrap: dict, entry: dict, entry_history: dict, picks: dict, gw: int,
    market_scores: dict = None, actual_transfers: list = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    players = build_player_map(bootstrap)

    # Latest purchase price (in 0.1m) from official transfer history (sorted oldest -> newest)
    latest_in_cost = {}
    for tr in sorted(actual_transfers or [], key=lambda x: (to_float(x.get("event")), str(x.get("time") or ""))):
        in_id = tr.get("element_in")
        if in_id is not None and tr.get("element_in_cost") is not None:
            latest_in_cost[int(in_id)] = int(to_float(tr["element_in_cost"]))

    team = []
    for pk in sorted(picks.get("picks", []), key=lambda x: x.get("position", 99)):
        info = players.get(pk["element"])
        if not info:
            continue
        player = {
            "id": pk["element"],
            **info,
            "starting": pk.get("position", 99) <= 11,
            "multiplier": pk.get("multiplier", 1),
            "is_captain": bool(pk.get("is_captain")),
            "is_vice_captain": bool(pk.get("is_vice_captain")),
        }
        if pk.get("selling_price") is not None:
            player["selling_price"] = round(to_float(pk["selling_price"]) / 10.0, 1)
        if pk.get("purchase_price") is not None:
            player["purchase_price"] = round(to_float(pk["purchase_price"]) / 10.0, 1)
        if "selling_price" not in player and actual_transfers is not None:
            buy_p, sell_p = _infer_prices_from_transfers(pk["element"], info, latest_in_cost)
            if sell_p is not None:
                player["selling_price"] = sell_p
                player["purchase_price"] = buy_p
        if market_scores:
            player["market_score"] = market_scores.get(pk["element"], 0.0)
        team.append(player)

    # 银行优先取 picks 接口的实时值（进行中的 GW），否则取最近已完成轮次。
    # 单位说明：FPL API 的 bank 是 0.1m 整数（如 20 = £2.0m），必须 /10 归一为百万
    # 浮点，才能与球员 price（now_cost/10，如 150 → 15.0）同单位做预算比较。
    # 历史上曾直接把 API 原始值写入 state.bank，导致引擎误把 £2.0m 当 £20m，
    # 推荐了根本买不起的球员（如 bank=20 时仍建议买 Haaland）。
    raw_bank = picks.get("entry_history", {}).get("bank")
    if raw_bank is None:
        raw_bank = 0.0
        for row in entry_history.get("current", []):
            if row.get("event") == gw - 1:
                raw_bank = row.get("bank", raw_bank)
    bank = round(to_float(raw_bank) / 10.0, 1)

    gameweek3_league = next((league for league in
                             ((entry.get("leagues") or {}).get("classic") or [])
                             if str(league.get("name", "")).strip().casefold() == "gameweek 3"
                             or league.get("short_name") == "event-3"), None)

    return {
        "season": config.SEASON,
        "current_gw": gw,
        # 赛季累计口径：FPL 未返回（如尚未参赛/赛季前）时保留 null，
        # 前端据此显示「待结算」，而不是把缺失值 or 成 0 误导为真实 0。
        "points": entry.get("summary_overall_points"),
        "rank": entry.get("summary_overall_rank"),
        "gameweek3_league": ({
            "id": gameweek3_league.get("id"),
            "name": gameweek3_league.get("name", "Gameweek 3"),
            "rank": gameweek3_league.get("entry_rank"),
            "rank_count": gameweek3_league.get("rank_count"),
        } if gameweek3_league else None),
        # bank 单位 = 百万（£m），与球员 price 一致；2.0 表示 £2.0m
        "bank": bank,
        "manager_name": (entry.get("name") or "").strip(),
        "formation": build_formation(team),
        "captain": next((t["name"] for t in team if t.get("is_captain")), ""),
        "vice": next((t["name"] for t in team if t.get("is_vice_captain")), ""),
        "next_deadline": _next_deadline(bootstrap["events"]),
        "last_update": now,
        "team": team,
    }


def build_history(bootstrap: dict, entry_history: dict) -> dict:
    """历史记录只收录已完结的轮次（未完结时积分/排名不稳定）。"""
    finished_ids = {e["id"] for e in bootstrap["events"] if e.get("finished")}
    rows = []
    for row in entry_history.get("current", []):
        if row.get("event") in finished_ids:
            rows.append(
                {
                    "gw": row["event"],
                    "points": row.get("points", 0),
                    "rank": row.get("rank", 0),
                    "overall_rank": row.get("overall_rank", 0),
                }
            )
    return {"season": config.SEASON, "history": rows}
