"""Fixture-aware FPL chip planning and current-week recommendation."""

from brain.market import to_float

CHIP_NAMES = {
    "wildcard": "Wildcard",
    "free_hit": "Free Hit",
    "bench_boost": "Bench Boost",
    "triple_captain": "Triple Captain",
}


def _availability(entry_history: dict, gw: int, api_chip_rules: list = None) -> dict:
    """Track each chip in the applicable season half, preferring FPL's chip windows."""
    half = 1 if gw <= 19 else 2
    available = {key: True for key in CHIP_NAMES}
    if api_chip_rules:
        matching = [c for c in api_chip_rules if str(c.get("name", "")).lower().replace("-", "_").replace(" ", "_") in CHIP_NAMES
                    and to_float(c.get("start_event"), 1) <= gw <= to_float(c.get("stop_event"), 38)]
        if matching:
            # Bootstrap chip entries describe whether that specific seasonal instance was played.
            for rule in matching:
                name = str(rule.get("name", "")).lower().replace("-", "_").replace(" ", "_")
                if rule.get("played"):
                    available[name] = False
    if not api_chip_rules or not any(
            str(c.get("name", "")).lower().replace("-", "_").replace(" ", "_") in CHIP_NAMES
            and to_float(c.get("start_event"), 1) <= gw <= to_float(c.get("stop_event"), 38)
            for c in api_chip_rules):
        for used in entry_history.get("chips") or []:
            name = str(used.get("name", "")).lower().replace("-", "_").replace(" ", "_")
            used_gw = int(to_float(used.get("event")))
            if name in CHIP_NAMES and (used_gw <= 19) == (half == 1):
                available[name] = False
    return available


def _fixture_summary(fixtures: list, team_ids: dict, start_gw: int, horizon: int) -> dict:
    by_gw = {}
    for fixture in fixtures or []:
        gw = fixture.get("event")
        if gw is None or gw < start_gw or gw >= start_gw + horizon:
            continue
        for side, opponent in (("team_h", "team_a"), ("team_a", "team_h")):
            team_id = fixture.get(side)
            if team_id is None:
                continue
            row = by_gw.setdefault(gw, {}).setdefault(team_id, {"count": 0, "difficulty": 0.0})
            row["count"] += 1
            diff = fixture.get("team_h_difficulty" if side == "team_h" else "team_a_difficulty")
            row["difficulty"] += to_float(diff, 3)
    return by_gw


def evaluate(squad: list, captain: dict, entry_history: dict, events: list,
             fixtures: list, teams: list, target_gw: int, cfg: dict,
             free_hit_net_gain: float = None, free_hit_confidence: float = 1.0,
             free_hit_assessed: bool = False) -> dict:
    """Return chip availability, current recommendation and rest-of-season plan."""
    chip_cfg = cfg.get("chip_strategy") or {}
    horizon = max(1, int(chip_cfg.get("planning_horizon", 8)))
    plan_horizon = max(horizon, int(chip_cfg.get("season_half_horizon", 19)))
    teams_by_id = {t.get("id"): t.get("short_name", str(t.get("id"))) for t in teams or []}
    fixture_map = _fixture_summary(fixtures, teams_by_id, target_gw, plan_horizon)
    availability = _availability(entry_history or {}, target_gw, cfg.get("_api_chips"))
    half = 1 if target_gw <= 19 else 2
    all_future_gws = sorted(e.get("id") for e in (events or [])
                            if e.get("id", 0) >= target_gw and not e.get("finished"))[:plan_horizon]
    future_gws = all_future_gws[:horizon]

    player_scores = {p.get("id"): to_float(p.get("lineup_score")) for p in squad}
    doubles, blanks = [], []
    for gw in future_gws:
        # Team short code is how squad snapshots identify clubs; resolve to team id.
        id_by_short = {v: k for k, v in teams_by_id.items()}
        counts = [fixture_map.get(gw, {}).get(id_by_short.get(p.get("team")), {}).get("count", 0)
                  for p in squad]
        double_strength = sum(player_scores.get(p.get("id"), 0) for p, count in zip(squad, counts) if count > 1)
        weak_fixture_players = []
        for p in squad:
            team_id = id_by_short.get(p.get("team"))
            fixture = fixture_map.get(gw, {}).get(team_id)
            if not fixture:
                # No scheduled fixture and a difficult fixture are both low-upside FH targets.
                weak_fixture_players.append({"id": p.get("id"), "score": player_scores.get(p.get("id"), 0),
                                             "reason": "空赛"})
            elif fixture["count"] == 1 and fixture["difficulty"] >= to_float(chip_cfg.get("free_hit_fixture_difficulty_threshold"), 4):
                weak_fixture_players.append({"id": p.get("id"), "score": player_scores.get(p.get("id"), 0),
                                             "reason": "困难赛程"})
        blank_strength = sum(player_scores.get(p.get("id"), 0) for p, count in zip(squad, counts) if count == 0)
        if sum(count > 1 for count in counts) >= int(chip_cfg.get("double_gameweek_player_threshold", 1)):
            doubles.append({"gw": gw, "players": sum(count > 1 for count in counts), "strength": round(double_strength, 2)})
        threshold = int(chip_cfg.get("free_hit_weak_player_threshold", 7))
        if len(weak_fixture_players) >= threshold:
            blanks.append({"gw": gw, "players": len(weak_fixture_players),
                           "strength": round(sum(p["score"] for p in weak_fixture_players), 2),
                           "reasons": {"blank": sum(p["reason"] == "空赛" for p in weak_fixture_players),
                                       "difficult": sum(p["reason"] == "困难赛程" for p in weak_fixture_players)}})

    # BB value is the strength of four bench players, adjusted down for doubtful/unavailable players.
    bench = sorted((p for p in squad if not p.get("starting")), key=lambda p: to_float(p.get("lineup_score")), reverse=True)
    bench_value = sum(to_float(p.get("lineup_score")) * (0.35 if p.get("status") in ("i", "u", "n") else 0.75 if p.get("status") == "d" else 1.0) for p in bench)
    bb_threshold = to_float(chip_cfg.get("bench_boost_score_threshold"), 145)
    tc_threshold = to_float(chip_cfg.get("triple_captain_score_threshold"), 70)
    current_counts = fixture_map.get(target_gw, {})
    id_by_short = {v: k for k, v in teams_by_id.items()}
    cap_count = current_counts.get(id_by_short.get((captain or {}).get("team")), {}).get("count", 0)
    cap_fixture_diff = current_counts.get(id_by_short.get((captain or {}).get("team")), {}).get("difficulty", 3.0)
    cap_value = to_float((captain or {}).get("captain_score"))
    used_this_gw = any(to_float(used.get("event")) == target_gw for used in (entry_history or {}).get("chips", []))
    recommendations = []
    weak_count = sum(1 for p in squad if p.get("status") in ("i", "u", "n") or
                     (p.get("chance_of_playing_next_round") is not None and to_float(p.get("chance_of_playing_next_round")) < 50))
    if not used_this_gw and availability["bench_boost"] and bench_value >= bb_threshold:
        recommendations.append({"chip": "bench_boost", "score": round(bench_value, 2), "reason": f"替补四人合计预期评分 {bench_value:.1f}，达到开卡阈值 {bb_threshold:.1f}"})
    if not used_this_gw and availability["triple_captain"] and cap_count >= 2 and cap_value >= tc_threshold:
        recommendations.append({"chip": "triple_captain", "score": round(cap_value, 2), "reason": f"队长所在球队本轮双赛，Captain Score {cap_value:.1f}"})
    weak_window = next((item for item in blanks if item["gw"] == target_gw), None)
    min_free_hit_gain = to_float(chip_cfg.get("free_hit_min_net_gain"), 8.0)
    future_premium = to_float(chip_cfg.get("free_hit_future_window_premium"), 2.0)
    uncertainty_margin = to_float(chip_cfg.get("free_hit_uncertainty_margin"), 3.0)
    confidence = max(0.0, min(1.0, to_float(free_hit_confidence, 1.0)))
    risk_adjusted_gain = (to_float(free_hit_net_gain) -
                          (1.0 - confidence) * uncertainty_margin
                          if free_hit_net_gain is not None else None)
    stronger_future_window = bool(
        weak_window and any(item["gw"] > target_gw and
                            item["strength"] > weak_window["strength"]
                            for item in blanks))
    required_free_hit_gain = min_free_hit_gain + (
        future_premium if stronger_future_window else 0.0)
    if not used_this_gw and availability["free_hit"] and weak_window:
        reasons = weak_window["reasons"]
        if not free_hit_assessed:
            recommendations.append({
                "chip": "free_hit", "score": round(weak_window["strength"], 2),
                "reason": f"本轮有 {weak_window['players']} 名球员赛程偏弱（{reasons['blank']} 人空赛、{reasons['difficult']} 人困难赛程），正在评估 Free Hit 整队收益"})
        elif (risk_adjusted_gain is not None and
              risk_adjusted_gain >= required_free_hit_gain):
            future_note = ("；后续暂未发现更强的 Free Hit 候选轮"
                           if not stronger_future_window
                           else "；后续有更强候选轮，本轮收益仍达到保留门槛")
            recommendations.append({
                "chip": "free_hit", "score": round(risk_adjusted_gain, 2),
                "reason": (f"本轮有 {weak_window['players']} 名球员赛程偏弱；Free Hit 相较当前阵容预计净增 {to_float(free_hit_net_gain):.2f} 分，"
                           f"数据可信度调整后 {risk_adjusted_gain:.2f} 分，达到 {required_free_hit_gain:.2f} 分门槛{future_note}")})
    if not used_this_gw and availability["wildcard"]:
        if weak_count >= int(chip_cfg.get("wildcard_weak_squad_threshold", 5)):
            recommendations.append({"chip": "wildcard", "score": float(weak_count), "reason": f"阵容中有 {weak_count} 名伤停或出场存疑球员"})

    preferred = chip_cfg.get("priority", ["wildcard", "free_hit", "bench_boost", "triple_captain"])
    recommendations.sort(key=lambda r: (preferred.index(r["chip"]) if r["chip"] in preferred else len(preferred), -r["score"]))
    current = recommendations[0] if recommendations and not used_this_gw else None
    half_end = 19 if half == 1 else 38
    remaining_gws = max(0, half_end - target_gw + 1)
    used_this_half = {
        str(used.get("name", "")).lower().replace("-", "_").replace(" ", "_")
        for used in (entry_history or {}).get("chips", [])
        if (int(to_float(used.get("event"))) <= 19) == (half == 1)
    }
    available_chips = [key for key in CHIP_NAMES if key not in used_this_half and availability.get(key)]
    guarantee_mode = len(available_chips) >= remaining_gws and remaining_gws > 0
    guard_start = int(chip_cfg.get("chip_guarantee_start_gw", 16 if half == 1 else 35))
    guard_active = target_gw >= guard_start and bool(available_chips)
    if current is None and not used_this_gw and (guarantee_mode or guard_active):
        fallback_order = chip_cfg.get("guarantee_priority", ["wildcard", "free_hit", "triple_captain", "bench_boost"])
        fallback_scores = {"wildcard": float(weak_count), "free_hit": 0.0,
                           "triple_captain": cap_value, "bench_boost": bench_value}
        fallback_chips = [
            chip for chip in available_chips
            if not (chip == "free_hit" and free_hit_assessed and
                    (risk_adjusted_gain is None or
                     risk_adjusted_gain < required_free_hit_gain))
        ]
        # Use the strongest scheduled opportunity when present; otherwise select the least
        # conditional chip as deadline approaches. This is a safety net, not an early trigger.
        candidate = next((chip for chip in fallback_order if chip in fallback_chips), None)
        if candidate:
            reason = (f"上半程只剩 {remaining_gws} 轮、仍有 {len(available_chips)} 张筹码可用；"
                      "进入保底排程，避免筹码过期。请在本轮候选中使用。")
            current = {"chip": candidate, "name": CHIP_NAMES[candidate],
                       "score": round(fallback_scores.get(candidate, 0.0), 2),
                       "reason": reason, "safety_net": True}
    # Show the follow-up schedule when Wildcard is recommended this week.
    if current and current["chip"] == "wildcard":
        # Re-evaluate all announced upcoming GWs for post-Wildcard targets.
        doubles, blanks = [], []
        for gw in all_future_gws:
            id_by_short = {v: k for k, v in teams_by_id.items()}
            counts = [fixture_map.get(gw, {}).get(id_by_short.get(p.get("team")), {}).get("count", 0)
                      for p in squad]
            double_strength = sum(player_scores.get(p.get("id"), 0) for p, count in zip(squad, counts) if count > 1)
            if sum(count > 1 for count in counts) >= int(chip_cfg.get("double_gameweek_player_threshold", 1)):
                doubles.append({"gw": gw, "players": sum(count > 1 for count in counts), "strength": round(double_strength, 2)})
            weak = 0
            for p in squad:
                fixture = fixture_map.get(gw, {}).get(id_by_short.get(p.get("team")))
                if not fixture or fixture["count"] == 1 and fixture["difficulty"] >= to_float(chip_cfg.get("free_hit_fixture_difficulty_threshold"), 4):
                    weak += 1
            if weak >= int(chip_cfg.get("free_hit_weak_player_threshold", 7)):
                blanks.append({"gw": gw, "players": weak})
        lookahead = [g for g in sorted(e.get("id") for e in (events or [])
                                       if e.get("id", 0) > target_gw and not e.get("finished"))]
        bb_candidate = next((d for d in doubles if d["gw"] in lookahead), None)
        tc_candidate = next((d for d in doubles if d["gw"] in lookahead and
                             (bb_candidate is None or d["gw"] != bb_candidate["gw"])), None)
        fh_candidate = next((b for b in blanks if b["gw"] in lookahead), None)
        default_lead = int(chip_cfg.get("wildcard_default_lead", 2))
        post_gws = [g for g in lookahead if g <= half_end]
        occupied = {target_gw}

        def reserve(candidate, offset):
            desired = candidate["gw"] if candidate else target_gw + default_lead + offset
            chosen = next((g for g in post_gws if g >= desired and g not in occupied), None)
            if chosen is None:
                chosen = next((g for g in reversed(post_gws) if g not in occupied), None)
            if chosen is not None:
                occupied.add(chosen)
            return chosen

        fh_gw = reserve(fh_candidate, 0)
        tc_gw = reserve(tc_candidate, int(chip_cfg.get("post_wildcard_triple_captain_gap", 3)))
        bb_gw = reserve(bb_candidate, int(chip_cfg.get("post_wildcard_bench_boost_gap", 1)))
        current["follow_up_plan"] = {
            "free_hit_gw": fh_gw,
            "triple_captain_gw": tc_gw,
            "bench_boost_gw": bb_gw,
            "free_hit_status": "fixture_candidate" if fh_candidate else "awaiting_fixture_window",
            "triple_captain_status": "fixture_candidate" if tc_candidate else "provisional",
            "bench_boost_status": "fixture_candidate" if bb_candidate else "provisional",
            "note": "Wildcard 后每轮最多开一张；三张卡分配到不同轮次，标注为暂定的轮次需在赛程公布后复核。",
        }
    plan = []
    for chip in ("wildcard", "free_hit", "bench_boost", "triple_captain"):
        if not availability[chip]:
            plan.append({"chip": chip, "name": CHIP_NAMES[chip], "status": "used", "target_gw": None, "reason": f"本半赛季已使用（GW{20 if half == 2 else 1}–{38 if half == 2 else 19} 每种筹码各一张）"})
            continue
        target = None
        reason = "暂未发现明确优势轮次；继续观察赛程更新"
        if chip == "wildcard":
            if weak_count >= int(chip_cfg.get("wildcard_weak_squad_threshold", 5)):
                target, reason = target_gw, f"当前有 {weak_count} 名伤停或出场存疑球员，考虑重建阵容"
            else:
                target = target_gw + int(chip_cfg.get("wildcard_default_lead", 2))
                reason = f"围绕 GW{target} 重建阵容，并在其后衔接 BB/FH/TC 机会"
        elif chip == "free_hit" and blanks:
            target, reason = blanks[0]["gw"], f"候选轮有 {blanks[0]['players']} 名球员赛程偏弱，适合临时换阵冲击上限"
        elif chip == "bench_boost":
            if doubles:
                target = max(doubles, key=lambda x: (x["players"], x["strength"]))["gw"]
                reason = "选择预计有较多主力双赛、且替补四人状态可用的轮次"
            else:
                target = target_gw + int(chip_cfg.get("wildcard_default_lead", 2)) + int(chip_cfg.get("post_wildcard_bench_boost_gap", 1))
                reason = f"建议先 Wildcard 整理完整 15 人，再于 GW{target} 左右评估 Bench Boost"
        elif chip == "triple_captain":
            if doubles:
                target = doubles[0]["gw"]
                reason = "优先安排在核心队长有双赛且 Captain Score 达标的轮次"
            else:
                target = target_gw + int(chip_cfg.get("wildcard_default_lead", 2)) + int(chip_cfg.get("post_wildcard_triple_captain_gap", 3))
                reason = f"暂定 GW{target} 附近，届时按队长双赛和评分再确认"
        plan.append({"chip": chip, "name": CHIP_NAMES[chip], "status": "available", "target_gw": target, "reason": reason})
    return {
        "target_gw": target_gw,
        "available": availability,
        "season_half": 1 if target_gw <= 19 else 2,
        "guarantee": {"active": guarantee_mode or guard_active,
                      "mode": "must_schedule" if guarantee_mode else "deadline_guard" if guard_active else "normal",
                      "remaining_gws": remaining_gws,
                      "available_chip_count": len(available_chips),
                      "start_gw": guard_start,
                      "message": (f"保底机制已启动：GW{target_gw} 距半程截止仅 {remaining_gws} 轮，"
                                  f"尚有 {len(available_chips)} 张筹码可用。") if (guarantee_mode or guard_active) else ""},
        "chip_already_used_this_gw": used_this_gw,
        "current_recommendation": ({**current, "name": CHIP_NAMES[current["chip"]]} if current else None),
        "planning_horizon": horizon,
        "plan": plan,
        "chip_sequence": [item["chip"] for item in sorted(
            (p for p in plan if p["status"] == "available" and p["target_gw"] is not None),
            key=lambda item: (item["target_gw"],
                              ["wildcard", "free_hit", "triple_captain", "bench_boost"].index(item["chip"]) ))],
        "windows": {"double_gameweeks": doubles, "blank_gameweeks": blanks},
        "signals": {"bench_score": round(bench_value, 2), "captain_score": round(cap_value, 2),
                    "captain_fixture_count": cap_count, "captain_fixture_difficulty": round(cap_fixture_diff, 2),
                    "free_hit_net_gain": (round(to_float(free_hit_net_gain), 2)
                                           if free_hit_net_gain is not None else None),
                    "free_hit_risk_adjusted_gain": (round(risk_adjusted_gain, 2)
                                                    if risk_adjusted_gain is not None else None),
                    "free_hit_required_gain": round(required_free_hit_gain, 2),
                    "free_hit_confidence": round(confidence, 2)},
    }
