"""15 人阵容构建（仅当前阵容为空时触发，如新号首轮）。

两阶段贪心：
  阶段 1：各位置按 Market Score 降序取配额（GKP×2/DEF×5/MID×5/FWD×3），
          跳过违反「同队 ≤ 3」的球员；
  阶段 2：总价超预算时循环降级——把 Market Score 最低的球员换成「同位置、
          未入选、同队未超限、且价格更低」的 Market Score 最高者；
          循环结束仍超预算则接受当前最优并写 warning。
"""
import math

from brain.market import to_float


def _team_entry(pid: int, info: dict, market_score: float) -> dict:
    return {
        "id": pid,
        "name": info.get("name", "?"),
        "pos": info.get("pos", "?"),
        "team": info.get("team", "?"),
        "price": info.get("price", 0.0),
        "selected_by": info.get("selected_by", 0.0),
        "selected_by_percent": info.get("selected_by_percent", 0.0),
        "form": info.get("form", "0"),
        "total_points": info.get("total_points", 0),
        "status": info.get("status", ""),
        "chance_of_playing_next_round": info.get("chance_of_playing_next_round"),
        "transfers_in_event": info.get("transfers_in_event", 0),
        "transfers_out_event": info.get("transfers_out_event", 0),
        "starting": False,
        "multiplier": 1,
        "is_captain": False,
        "is_vice_captain": False,
        "market_score": round(to_float(market_score), 2),
    }


def build_squad(players_map: dict, market_scores: dict, cfg: dict):
    """返回 (squad, warnings)。squad 为 15 名球员条目（含 market_score）。"""
    quota = cfg.get("position_quota") or {}
    budget = to_float(cfg.get("budget"), 100.0)
    max_per_team = int(cfg.get("max_players_per_team", 3))

    def score(pid: int) -> float:
        return to_float(market_scores.get(pid))

    ranked = sorted(players_map.items(), key=lambda kv: (-score(kv[0]), kv[0]))

    team = []
    team_counts = {}
    for pos, need in quota.items():
        picked = 0
        for pid, info in ranked:
            if info.get("pos") != pos:
                continue
            if team_counts.get(info.get("team"), 0) >= max_per_team:
                continue
            team.append(_team_entry(pid, info, score(pid)))
            team_counts[info.get("team")] = team_counts.get(info.get("team"), 0) + 1
            picked += 1
            if picked >= need:
                break

    warnings = []
    if len(team) < int(cfg.get("squad_size", 15)):
        warnings.append(f"可构建球员不足（{len(team)}/{cfg.get('squad_size')}），请检查数据")

    # 阶段 2：预算降级
    while sum(p["price"] for p in team) > budget + 1e-9:
        in_squad = {p["id"] for p in team}
        swapped = False
        for out_p in sorted(team, key=lambda p: (p["market_score"], p["id"])):
            best_in = None
            for pid, info in ranked:
                if info.get("pos") != out_p["pos"] or pid in in_squad:
                    continue
                if team_counts.get(info.get("team"), 0) >= max_per_team:
                    continue
                if to_float(info.get("price")) >= out_p["price"] - 1e-9:
                    continue
                best_in = (pid, info)
                break
            if best_in is None:
                continue
            pid, info = best_in
            team_counts[out_p["team"]] -= 1
            team_counts[info.get("team")] = team_counts.get(info.get("team"), 0) + 1
            team.remove(out_p)
            team.append(_team_entry(pid, info, score(pid)))
            swapped = True
            break
        if not swapped:
            warnings.append(
                f"预算降级无法满足 budget={budget}，接受当前阵容（总价 {sum(p['price'] for p in team):.1f}）"
            )
            break
    return team, warnings


def _fixture_opponents(info: dict, fixtures_by_team_id: dict) -> set:
    team_id = info.get("team_id")
    return {fixture.get("opponent") for fixture in
            fixtures_by_team_id.get(team_id, []) if fixture.get("opponent")}


def starting_probability(info: dict, team_matches_played: int = 0):
    """Estimate start likelihood from this season's starts when the sample is useful."""
    starts = info.get("starts")
    if starts is None or int(to_float(team_matches_played)) < 3:
        return None
    matches = max(1, int(to_float(team_matches_played)))
    # A small prior avoids treating one recent start as certainty while keeping
    # a player with no league starts out of the likely XI after several rounds.
    return round(max(0.02, min(1.0, (to_float(starts) + 0.5) / (matches + 1.0))), 4)


def apply_start_probability(squad: list) -> None:
    """Discount lineup scores for likely non-starters and unavailable players."""
    for player in squad:
        probability = player.get("starting_probability")
        factor = 1.0 if probability is None else 0.2 + 0.8 * probability
        chance = player.get("chance_of_playing_next_round")
        status = str(player.get("status") or "").lower()
        if chance is not None:
            factor *= max(0.0, min(1.0, to_float(chance) / 100.0))
        elif status == "d":
            factor *= 0.5
        if status in ("i", "s", "u", "n"):
            factor = 0.0
        if factor >= 1.0:
            continue
        original = to_float(player.get("lineup_score"))
        player["lineup_score_before_availability_adjustment"] = round(original, 2)
        player["lineup_score"] = round(original * factor, 2)


def _fpljoe_role_reference(info: dict, team_data: dict,
                           team_player_xg: dict):
    teams = (team_data or {}).get("teams") or {}
    signals = (team_data or {}).get("neutral") or {}
    team = teams.get(info.get("team")) or {}
    position = info.get("pos")
    if position in ("MID", "FWD") and not signals.get("projection"):
        expected_goals = team.get("projection")
        if expected_goals is not None:
            # Split FPL Joe's team xG by this player's share of season xG, then
            # convert individual xG to a Poisson probability of scoring.
            team_xg = to_float(team_player_xg.get(info.get("team_id")))
            player_xg = info.get("expected_goals")
            if player_xg is not None and team_xg > 0:
                individual_xg = max(0.0, to_float(expected_goals)) * max(
                    0.0, to_float(player_xg)) / team_xg
                probability = 1.0 - math.exp(-individual_xg)
                return probability, "个人进球概率参考", individual_xg
            probability = 1.0 - math.exp(-max(0.0, to_float(expected_goals)))
            return probability, "球队进球概率参考", to_float(expected_goals)
    if position in ("GKP", "DEF") and not signals.get("clean_sheet"):
        clean_sheet = team.get("clean_sheet")
        if clean_sheet is not None:
            probability = to_float(clean_sheet)
            if probability > 1.0:
                probability /= 100.0
            return max(0.0, min(1.0, probability)), "球队零封概率", probability
    return None, None, None


def expected_points(info: dict, target_score: float, calibration: dict,
                    team_matches_played: int = 0) -> float:
    """Return the model's one-GW point estimate, adjusted for availability."""
    horizon = info.get("horizon_points") or []
    if horizon and horizon[0] is not None:
        points = max(0.0, to_float(horizon[0]))
    else:
        fit = calibration.get(info.get("pos"), {})
        points = max(0.0, to_float(fit.get("intercept")) +
                     to_float(fit.get("slope")) * target_score)

    status = str(info.get("status") or "").lower()
    start_chance = starting_probability(info, team_matches_played)
    if start_chance is not None:
        # Leave a small allowance for substitute appearances.
        points *= 0.2 + 0.8 * start_chance
    chance = info.get("chance_of_playing_next_round")
    if chance is not None:
        points *= max(0.0, min(1.0, to_float(chance) / 100.0))
    elif status == "d":
        points *= 0.5
    if status in ("i", "s", "u", "n"):
        points = 0.0
    return round(points, 4)


def build_free_hit_squad(players_map: dict, target_scores: dict, market_scores: dict,
                         cfg: dict, budget: float, fixtures_by_team_id: dict,
                         point_calibration: dict, team_data: dict = None,
                         team_matches_played_by_team: dict = None,
                         conflict_penalty: float = 0.75, beam_width: int = 512,
                         fpljoe_reference_weight: float = 0.5):
    """Find a legal one-week squad with a budget-aware, fixture-aware beam search.

    The objective uses calibrated expected FPL points. It also applies a soft
    penalty when selected defenders face each other, so one fixture cannot
    dominate defensive upside merely because both FDR values look good.
    """
    quota = cfg.get("position_quota") or {}
    max_per_team = int(cfg.get("max_players_per_team", 3))
    budget = max(0.0, to_float(budget))
    candidates_by_pos = {}
    warnings = []
    team_player_xg = {}
    for info in players_map.values():
        if info.get("team_id") is not None:
            team_player_xg[info["team_id"]] = (
                team_player_xg.get(info["team_id"], 0.0) +
                max(0.0, to_float(info.get("expected_goals"))))

    for pos, need in quota.items():
        candidates = []
        for pid, info in players_map.items():
            if info.get("pos") != pos:
                continue
            target_score = to_float(target_scores.get(pid))
            player = _team_entry(pid, info, to_float(market_scores.get(pid)))
            player["team_id"] = info.get("team_id")
            player["target_score"] = target_score
            played = (team_matches_played_by_team or {}).get(info.get("team_id"), 0)
            player["starting_probability"] = starting_probability(info, played)
            player["projected_points"] = expected_points(
                info, target_score, point_calibration or {}, played)
            (reference_probability, reference_label,
             reference_value) = _fpljoe_role_reference(
                 info, team_data or {}, team_player_xg)
            player["fpljoe_reference_probability"] = reference_probability
            player["fpljoe_reference_label"] = reference_label
            player["fpljoe_reference_value"] = reference_value
            if reference_probability is not None:
                reference_factor = (player["starting_probability"]
                                    if player["starting_probability"] is not None else 1.0)
                chance = info.get("chance_of_playing_next_round")
                status = str(info.get("status") or "").lower()
                if chance is not None:
                    reference_factor *= max(0.0, min(1.0, to_float(chance) / 100.0))
                elif status == "d":
                    reference_factor *= 0.5
                if status in ("i", "s", "u", "n"):
                    reference_factor = 0.0
                player["_reference_adjustment"] = (
                    4.0 * (reference_probability - 0.5) *
                    max(0.0, to_float(fpljoe_reference_weight)) * reference_factor)
            else:
                player["_reference_adjustment"] = 0.0
            player["fixtures"] = fixtures_by_team_id.get(info.get("team_id"), [])
            player["_opponents"] = _fixture_opponents(info, fixtures_by_team_id)
            if info.get("horizon_points") is not None:
                player["horizon_points"] = info.get("horizon_points")
            if info.get("five_gw_points") is not None:
                player["five_gw_points"] = info.get("five_gw_points")
            if info.get("model_scores_by_gw") is not None:
                player["model_scores_by_gw"] = info.get("model_scores_by_gw")
            candidates.append(player)
        candidates.sort(key=lambda p: (-p["projected_points"],
                                       -p["target_score"], p["price"], p["id"]))
        if len(candidates) < int(need):
            warnings.append(f"{pos} 可用球员不足（{len(candidates)}/{need}）")
        candidates_by_pos[pos] = candidates

    if warnings:
        return [], warnings, {}

    slots = [pos for pos, count in quota.items() for _ in range(int(count))]
    if len(slots) != int(cfg.get("squad_size", 15)):
        return [], ["Free Hit 位置配额与 15 人阵容人数不一致"], {}

    # A safe lower bound for the remaining budget (the bound may reuse a
    # candidate, which only makes it more permissive).
    min_remaining = [0.0] * (len(slots) + 1)
    for i in range(len(slots) - 1, -1, -1):
        options = candidates_by_pos.get(slots[i], [])
        if not options:
            return [], [f"{slots[i]} 没有可用球员"], {}
        min_remaining[i] = min_remaining[i + 1] + min(
            to_float(player.get("price")) for player in options)

    # State: utility, target-score tie breaker, cost, selected players,
    # per-team counts, last candidate index for the current position.
    states = [(0.0, 0.0, 0.0, (), {}, -1)]
    previous_pos = None
    for slot_index, pos in enumerate(slots):
        candidates = candidates_by_pos[pos]
        same_position = pos == previous_pos
        expanded = []
        for utility, score_sum, spent, selected, team_counts, last_index in states:
            first_index = last_index + 1 if same_position else 0
            for candidate_index in range(first_index, len(candidates)):
                player = candidates[candidate_index]
                team = player.get("team")
                if team_counts.get(team, 0) >= max_per_team:
                    continue
                next_spent = spent + to_float(player.get("price"))
                if next_spent + min_remaining[slot_index + 1] > budget + 1e-9:
                    continue

                clash_count = 0
                if pos == "DEF":
                    opponents = player.get("_opponents") or set()
                    clash_count = sum(
                        1 for other in selected
                        if other.get("pos") == "DEF" and
                        (team in (other.get("_opponents") or set()) or
                         other.get("team") in opponents))
                next_utility = utility + player["projected_points"] + player["_reference_adjustment"] - (
                    max(0.0, to_float(conflict_penalty)) * clash_count)
                next_counts = dict(team_counts)
                next_counts[team] = next_counts.get(team, 0) + 1
                expanded.append((next_utility,
                                 score_sum + player["target_score"],
                                 next_spent, selected + (player,),
                                 next_counts, candidate_index))

        if not expanded:
            return [], [f"预算 £{budget:.1f}m 下找不到合法 Free Hit 阵容"], {}
        expanded.sort(key=lambda row: (-row[0], -row[1], row[2],
                                       tuple(p["id"] for p in row[3])))
        states = expanded[:max(1, int(beam_width))]
        previous_pos = pos

    best = states[0]
    squad = []
    for player in best[3]:
        player.pop("_opponents", None)
        player.pop("_reference_adjustment", None)
        squad.append(player)
    total_cost = round(best[2], 2)
    if total_cost > budget + 1e-9:
        return [], [f"预算超限（£{total_cost:.1f}m / £{budget:.1f}m）"], {}
    return squad, [], {
        "projected_squad_points": round(sum(p["projected_points"] for p in squad), 2),
        "selection_utility": round(best[0], 2),
        "budget": budget,
        "total_cost": total_cost,
    }
