"""Fixture-aware & xP-driven FPL chip planning and current-week recommendation."""

from brain.market import to_float

CHIP_NAMES = {
    "wildcard": "Wildcard",
    "free_hit": "Free Hit",
    "bench_boost": "Bench Boost",
    "triple_captain": "Triple Captain",
}

CHIP_ALIASES = {
    "wildcard": "wildcard",
    "wc": "wildcard",
    "freehit": "free_hit",
    "free_hit": "free_hit",
    "fh": "free_hit",
    "bboost": "bench_boost",
    "bench_boost": "bench_boost",
    "bb": "bench_boost",
    "3xc": "triple_captain",
    "triple_captain": "triple_captain",
    "tc": "triple_captain",
}


def normalize_chip_name(raw) -> str:
    """Normalize FPL API chip identifiers (bboost/freehit/3xc/wildcard) to internal keys."""
    key = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return CHIP_ALIASES.get(key, key)


def _chip_windows(gw: int, api_chip_rules: list = None) -> dict:
    """Resolve (start_event, stop_event) window per chip for the current season half."""
    default_window = (1, 19) if gw <= 19 else (20, 38)
    windows = {chip: default_window for chip in CHIP_NAMES}
    for rule in api_chip_rules or []:
        name = normalize_chip_name(rule.get("name"))
        if name not in CHIP_NAMES:
            continue
        start_ev = int(to_float(rule.get("start_event"), default_window[0]))
        stop_ev = int(to_float(rule.get("stop_event"), default_window[1]))
        if start_ev <= gw <= stop_ev:
            windows[name] = (start_ev, stop_ev)
    return windows


def _used_chips_in_window(entry_history: dict, gw: int,
                          api_chip_rules: list = None,
                          active_chip: str = None,
                          active_chip_gw: int = None) -> dict:
    """Return {chip_key: used_gw} for chips already played in the current half window."""
    windows = _chip_windows(gw, api_chip_rules)
    used_map = {}
    for rule in api_chip_rules or []:
        name = normalize_chip_name(rule.get("name"))
        if name not in CHIP_NAMES:
            continue
        start_ev, stop_ev = windows[name]
        if start_ev <= gw <= stop_ev and rule.get("played"):
            used_map[name] = int(to_float(rule.get("event"), gw))
    for used in (entry_history or {}).get("chips") or []:
        name = normalize_chip_name(used.get("name"))
        used_gw = int(to_float(used.get("event")))
        if name not in CHIP_NAMES or used_gw <= 0:
            continue
        start_ev, stop_ev = windows[name]
        half_start = 1 if stop_ev <= 19 else start_ev
        if half_start <= used_gw <= stop_ev:
            used_map[name] = used_gw
    if active_chip:
        norm_active = normalize_chip_name(active_chip)
        ev = int(to_float(active_chip_gw, gw))
        if norm_active in CHIP_NAMES:
            start_ev, stop_ev = windows[norm_active]
            half_start = 1 if stop_ev <= 19 else start_ev
            if half_start <= ev <= stop_ev:
                used_map.setdefault(norm_active, ev)
    return used_map


def _availability(entry_history: dict, gw: int, api_chip_rules: list = None,
                  active_chip: str = None, active_chip_gw: int = None) -> dict:
    """Track each chip in the applicable season half using real entry_history.chips."""
    used_map = _used_chips_in_window(
        entry_history, gw, api_chip_rules,
        active_chip=active_chip, active_chip_gw=active_chip_gw,
    )
    return {key: (key not in used_map) for key in CHIP_NAMES}


def _fixture_summary(fixtures: list, team_ids: dict, start_gw: int, horizon: int) -> dict:
    by_gw = {}
    for fixture in fixtures or []:
        gw = fixture.get("event")
        if gw is None or gw < start_gw or gw >= start_gw + horizon:
            continue
        for side, _ in (("team_h", "team_a"), ("team_a", "team_h")):
            team_id = fixture.get(side)
            if team_id is None:
                continue
            row = by_gw.setdefault(gw, {}).setdefault(
                team_id, {"count": 0, "difficulty": 0.0, "home_count": 0}
            )
            row["count"] += 1
            if side == "team_h":
                row["home_count"] += 1
            diff = fixture.get("team_h_difficulty" if side == "team_h" else "team_a_difficulty")
            row["difficulty"] += to_float(diff, 3)
    return by_gw


def _best_single_gw_tc_window(squad: list, fixture_map: dict, id_by_short: dict,
                              half_future_gws: list) -> dict:
    """Find the best single-GW captaincy ceiling spot in the remaining season half."""
    attackers = [
        p for p in squad
        if p.get("pos") in ("FWD", "MID")
        and (p.get("starting_probability") is None or to_float(p.get("starting_probability")) >= 0.80)
        and str(p.get("status") or "a").lower() == "a"
    ]
    if not attackers:
        return None
    # Rank core captaincy candidates by five_gw_points / market_score / price
    core = sorted(
        attackers,
        key=lambda p: (-to_float(p.get("five_gw_points")), -to_float(p.get("price")), -to_float(p.get("market_score"))),
    )[:3]
    best = None
    for gw in half_future_gws:
        for p in core:
            tid = id_by_short.get(p.get("team"))
            fix = fixture_map.get(gw, {}).get(tid)
            if not fix or fix["count"] < 1:
                continue
            avg_diff = fix["difficulty"] / max(1, fix["count"])
            # Estimate GW upside from horizon_points if within 5-GW window, else fixture ease
            proj_gws = p.get("projection_gws") or []
            h_pts = p.get("horizon_points") or []
            if gw in proj_gws:
                idx = proj_gws.index(gw)
                est_xp = to_float(h_pts[idx]) if idx < len(h_pts) else 0.0
            else:
                base_ppg = max(4.5, to_float(p.get("five_gw_points")) / 5.0 if p.get("five_gw_points") else 5.0)
                est_xp = base_ppg * (1.0 + 0.14 * (3.0 - avg_diff)) * (1.06 if fix["home_count"] > 0 else 0.94)
            score_key = (
                fix["count"],
                round(est_xp + (0.4 if fix["home_count"] > 0 else 0.0) - 0.35 * avg_diff, 3),
                -gw,
            )
            if best is None or score_key > best[0]:
                best = (score_key, {"gw": gw, "player": p.get("name", "?"),
                                    "est_xp": round(est_xp, 2), "avg_diff": round(avg_diff, 1),
                                    "home": fix["home_count"] > 0})
    return best[1] if best else None


def evaluate(squad: list, captain: dict, entry_history: dict, events: list,
             fixtures: list, teams: list, target_gw: int, cfg: dict,
             free_hit_net_gain: float = None, free_hit_confidence: float = 1.0,
             free_hit_assessed: bool = False,
             wildcard_five_gw_gain: float = None,
             active_chip: str = None, active_chip_gw: int = None) -> dict:
    """Return chip availability, current recommendation and rest-of-half plan."""
    chip_cfg = cfg.get("chip_strategy") or {}
    half = 1 if target_gw <= 19 else 2
    half_end = 19 if half == 1 else 38
    remaining_gws = max(0, half_end - target_gw + 1)

    horizon = max(1, int(chip_cfg.get("planning_horizon", 8)))
    plan_horizon = max(horizon, int(chip_cfg.get("season_half_horizon", 19)), remaining_gws)
    teams_by_id = {t.get("id"): t.get("short_name", str(t.get("id"))) for t in teams or []}
    id_by_short = {v: k for k, v in teams_by_id.items()}
    fixture_map = _fixture_summary(fixtures, teams_by_id, target_gw, plan_horizon)

    used_map = _used_chips_in_window(
        entry_history or {}, target_gw, cfg.get("_api_chips"),
        active_chip=active_chip, active_chip_gw=active_chip_gw,
    )
    availability = {key: (key not in used_map) for key in CHIP_NAMES}

    all_future_gws = sorted(
        e.get("id") for e in (events or [])
        if e.get("id", 0) >= target_gw and not e.get("finished")
    )[:plan_horizon]
    future_gws = [g for g in all_future_gws if g <= half_end][:horizon] or all_future_gws[:horizon]
    half_future_gws = [g for g in all_future_gws if g <= half_end]

    # In real xP mode, evaluate Free Hit fixture weakness on the core 12 rotation
    # rather than letting 3 deep bench fodders artificially inflate "weak fixtures".
    has_xp = any(p.get("projected_points") is not None for p in squad)
    if has_xp and len(squad) > 12:
        fh_eval_squad = sorted(
            squad,
            key=lambda p: (
                1 if p.get("starting") else 0,
                to_float(p.get("five_gw_points")),
                to_float(p.get("lineup_score")),
            ),
            reverse=True,
        )[:12]
    else:
        fh_eval_squad = list(squad)

    player_scores = {p.get("id"): to_float(p.get("lineup_score")) for p in squad}
    doubles, blanks = [], []
    for gw in future_gws:
        if not fixture_map.get(gw):
            continue
        counts = [fixture_map.get(gw, {}).get(id_by_short.get(p.get("team")), {}).get("count", 0)
                  for p in squad]
        double_strength = sum(player_scores.get(p.get("id"), 0) for p, count in zip(squad, counts) if count > 1)
        weak_fixture_players = []
        for p in fh_eval_squad:
            team_id = id_by_short.get(p.get("team"))
            fixture = fixture_map.get(gw, {}).get(team_id)
            if not fixture:
                weak_fixture_players.append({"id": p.get("id"), "score": player_scores.get(p.get("id"), 0),
                                             "reason": "空赛"})
            elif fixture["count"] == 1 and fixture["difficulty"] >= to_float(chip_cfg.get("free_hit_fixture_difficulty_threshold"), 4):
                weak_fixture_players.append({"id": p.get("id"), "score": player_scores.get(p.get("id"), 0),
                                             "reason": "困难赛程"})
        if sum(count > 1 for count in counts) >= int(chip_cfg.get("double_gameweek_player_threshold", 1)):
            doubles.append({"gw": gw, "players": sum(count > 1 for count in counts), "strength": round(double_strength, 2)})
        threshold = int(chip_cfg.get("free_hit_weak_player_threshold", 6 if has_xp else 7))
        if len(weak_fixture_players) >= threshold:
            blanks.append({"gw": gw, "players": len(weak_fixture_players),
                           "strength": round(sum(p["score"] for p in weak_fixture_players), 2),
                           "reasons": {"blank": sum(p["reason"] == "空赛" for p in weak_fixture_players),
                                       "difficult": sum(p["reason"] == "困难赛程" for p in weak_fixture_players)}})

    # Bench Boost evaluation: use real bench expected FPL points + start probability in xP mode;
    # fall back to lineup_score sum on synthetic unit tests without projected_points.
    bench = sorted((p for p in squad if not p.get("starting")), key=lambda p: to_float(p.get("lineup_score")), reverse=True)
    bench_value = sum(
        to_float(p.get("lineup_score")) * (0.35 if p.get("status") in ("i", "u", "n") else 0.75 if p.get("status") == "d" else 1.0)
        for p in bench
    )
    bench_xp = round(sum(to_float(p.get("projected_points")) for p in bench), 2) if has_xp else None
    bb_threshold = to_float(chip_cfg.get("bench_boost_score_threshold"), 145)
    bb_min_xp = to_float(chip_cfg.get("bench_boost_min_bench_xp"), 16.0)
    bb_min_start = to_float(chip_cfg.get("bench_boost_min_start_prob"), 0.75)

    tc_threshold = to_float(chip_cfg.get("triple_captain_score_threshold"), 70)
    tc_single_xp = to_float(chip_cfg.get("triple_captain_single_gw_xp_threshold"), 7.2)
    tc_single_score = to_float(chip_cfg.get("triple_captain_single_gw_score_threshold"), 76)

    current_counts = fixture_map.get(target_gw, {})
    cap_fix = current_counts.get(id_by_short.get((captain or {}).get("team")), {})
    cap_count = cap_fix.get("count", 0)
    cap_fixture_diff = cap_fix.get("difficulty", 3.0)
    cap_value = to_float((captain or {}).get("captain_score"))
    cap_xp = to_float((captain or {}).get("projected_points")) if (captain or {}).get("projected_points") is not None else None

    used_this_gw = (
        any(int(to_float(used.get("event"))) == target_gw for used in (entry_history or {}).get("chips", []))
        or (bool(active_chip) and int(to_float(active_chip_gw, target_gw)) == target_gw)
    )

    recommendations = []
    weak_count = sum(
        1 for p in squad
        if p.get("status") in ("i", "s", "u", "n")
        or (p.get("chance_of_playing_next_round") is not None and to_float(p.get("chance_of_playing_next_round")) < 50)
    )
    deadwood_count = sum(
        1 for p in squad
        if p.get("starting_probability") is not None and to_float(p.get("starting_probability")) < 0.55
    )

    # 1) Bench Boost trigger
    if not used_this_gw and availability["bench_boost"]:
        if has_xp:
            bench_ready = (
                len(bench) == 4
                and bench_xp is not None
                and bench_xp >= bb_min_xp
                and all(
                    str(p.get("status") or "a").lower() == "a"
                    and (p.get("starting_probability") is None or to_float(p.get("starting_probability")) >= bb_min_start)
                    for p in bench
                )
            )
            if bench_ready:
                recommendations.append({
                    "chip": "bench_boost",
                    "score": bench_xp,
                    "reason": f"替补四人全员主力可用，单轮预计合计贡献 {bench_xp:.1f} 分（≥ {bb_min_xp:.1f} 分开卡门槛）",
                })
        elif bench_value >= bb_threshold:
            recommendations.append({
                "chip": "bench_boost",
                "score": round(bench_value, 2),
                "reason": f"替补四人合计预期评分 {bench_value:.1f}，达到开卡阈值 {bb_threshold:.1f}",
            })

    # 2) Triple Captain trigger (DGW primary; Half-1 elite single-GW peak fallback when no DGW exists)
    tc_peak_window = _best_single_gw_tc_window(squad, fixture_map, id_by_short, half_future_gws) if availability["triple_captain"] else None
    if not used_this_gw and availability["triple_captain"]:
        if cap_count >= 2 and cap_value >= tc_threshold:
            recommendations.append({
                "chip": "triple_captain",
                "score": round(cap_value, 2),
                "reason": f"队长 {(captain or {}).get('name', '')} 所在球队本轮双赛，Captain Score {cap_value:.1f}",
            })
        elif (not doubles and cap_xp is not None and cap_count == 1
              and cap_xp >= tc_single_xp and cap_value >= tc_single_score
              and cap_fixture_diff <= 2.5
              and (tc_peak_window is None or tc_peak_window["gw"] == target_gw
                   or cap_xp + 0.25 >= tc_peak_window["est_xp"])):
            recommendations.append({
                "chip": "triple_captain",
                "score": round(cap_value, 2),
                "reason": (f"本半程剩余轮次暂无双赛；队长 {(captain or {}).get('name', '?')} 本轮单场预计 "
                           f"{cap_xp:.1f} 分（Captain Score {cap_value:.1f}），处于半程最佳单赛爆发窗口"),
            })

    # 3) Free Hit trigger
    weak_window = next((item for item in blanks if item["gw"] == target_gw), None)
    # Taper FH threshold slightly in final 3 GWs of a half so valuable chips don't expire unused
    base_min_fh_gain = to_float(chip_cfg.get("free_hit_min_net_gain"), 8.0)
    if remaining_gws <= 3:
        min_free_hit_gain = max(5.0, base_min_fh_gain - 2.5)
    else:
        min_free_hit_gain = base_min_fh_gain
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
                "reason": f"本轮有 {weak_window['players']} 名核心球员赛程偏弱（{reasons['blank']} 人空赛、{reasons['difficult']} 人困难赛程），正在评估 Free Hit 整队收益"})
        elif (risk_adjusted_gain is not None and
              risk_adjusted_gain >= required_free_hit_gain):
            future_note = ("；后续暂未发现更强的 Free Hit 候选轮"
                           if not stronger_future_window
                           else "；后续有更强候选轮，本轮收益仍达到保留门槛")
            recommendations.append({
                "chip": "free_hit", "score": round(risk_adjusted_gain, 2),
                "reason": (f"本轮有 {weak_window['players']} 名球员赛程偏弱；Free Hit 相较当前阵容预计净增 {to_float(free_hit_net_gain):.2f} 分，"
                           f"数据可信度调整后 {risk_adjusted_gain:.2f} 分，达到 {required_free_hit_gain:.2f} 分门槛{future_note}")})

    # 4) Wildcard trigger: severe injury crisis OR high 5-GW structural xP gain
    wc_min_gain = to_float(chip_cfg.get("wildcard_min_five_gw_gain"), 16.0)
    if remaining_gws <= 4:
        wc_min_gain = max(10.0, wc_min_gain - 4.0)
    if not used_this_gw and availability["wildcard"]:
        if weak_count >= int(chip_cfg.get("wildcard_weak_squad_threshold", 5)):
            recommendations.append({
                "chip": "wildcard",
                "score": float(weak_count * 10),
                "reason": f"阵容中有 {weak_count} 名伤停或出场存疑球员，建议开启 Wildcard 重建",
            })
        elif wildcard_five_gw_gain is not None and to_float(wildcard_five_gw_gain) >= wc_min_gain:
            recommendations.append({
                "chip": "wildcard",
                "score": round(to_float(wildcard_five_gw_gain), 2),
                "reason": (f"全池最优重建相比当前阵容未来 5 轮预计净增 {to_float(wildcard_five_gw_gain):.1f} 分"
                           f"（≥ {wc_min_gain:.1f} 分重建门槛，可一次性释放门将冗余资金并清理 {deadwood_count} 名低出场率球员）"),
            })

    preferred = chip_cfg.get("priority", ["wildcard", "free_hit", "triple_captain", "bench_boost"])
    recommendations.sort(key=lambda r: (preferred.index(r["chip"]) if r["chip"] in preferred else len(preferred), -r["score"]))
    current = recommendations[0] if recommendations and not used_this_gw else None

    available_chips = [key for key in CHIP_NAMES if availability.get(key)]
    guarantee_mode = len(available_chips) >= remaining_gws and remaining_gws > 0
    guard_start = int(chip_cfg.get("chip_guarantee_start_gw", 16 if half == 1 else 35))
    guard_active = target_gw >= guard_start and bool(available_chips)
    if current is None and not used_this_gw and (guarantee_mode or guard_active):
        fallback_order = chip_cfg.get("guarantee_priority", ["wildcard", "free_hit", "triple_captain", "bench_boost"])
        fallback_scores = {
            "wildcard": round(to_float(wildcard_five_gw_gain, weak_count), 2),
            "free_hit": round(to_float(risk_adjusted_gain, 0.0), 2),
            "triple_captain": cap_value,
            "bench_boost": bench_xp if bench_xp is not None else bench_value,
        }
        fallback_chips = [
            chip for chip in available_chips
            if not (chip == "free_hit" and free_hit_assessed and not guarantee_mode and
                    (risk_adjusted_gain is None or
                     risk_adjusted_gain < required_free_hit_gain))
        ]
        candidate = next((chip for chip in fallback_order if chip in fallback_chips), None)
        if candidate:
            half_label = "上半程" if half == 1 else "下半程"
            reason = (f"{half_label}只剩 {remaining_gws} 轮、仍有 {len(available_chips)} 张筹码可用；"
                      "进入保底排程，避免筹码过期作废。请在本轮候选中使用。")
            current = {"chip": candidate, "name": CHIP_NAMES[candidate],
                       "score": round(to_float(fallback_scores.get(candidate, 0.0)), 2),
                       "reason": reason, "safety_net": True}

    # Show the follow-up schedule when Wildcard is recommended this week (only for still-available chips!)
    if current and current["chip"] == "wildcard":
        all_doubles, all_blanks = [], []
        for gw in all_future_gws:
            if not fixture_map.get(gw):
                continue
            counts = [fixture_map.get(gw, {}).get(id_by_short.get(p.get("team")), {}).get("count", 0)
                      for p in squad]
            double_strength = sum(player_scores.get(p.get("id"), 0) for p, count in zip(squad, counts) if count > 1)
            if sum(count > 1 for count in counts) >= int(chip_cfg.get("double_gameweek_player_threshold", 1)):
                all_doubles.append({"gw": gw, "players": sum(count > 1 for count in counts), "strength": round(double_strength, 2)})
            weak = 0
            for p in fh_eval_squad:
                fixture = fixture_map.get(gw, {}).get(id_by_short.get(p.get("team")))
                if not fixture or fixture["count"] == 1 and fixture["difficulty"] >= to_float(chip_cfg.get("free_hit_fixture_difficulty_threshold"), 4):
                    weak += 1
            if weak >= int(chip_cfg.get("free_hit_weak_player_threshold", 6 if has_xp else 7)):
                all_blanks.append({"gw": gw, "players": weak})
        lookahead = [g for g in sorted(e.get("id") for e in (events or [])
                                       if e.get("id", 0) > target_gw and not e.get("finished"))]
        bb_candidate = next((d for d in all_doubles if d["gw"] in lookahead), None) if availability["bench_boost"] else None
        tc_candidate = (
            next((d for d in all_doubles if d["gw"] in lookahead and
                  (bb_candidate is None or d["gw"] != bb_candidate["gw"])), None)
            if availability["triple_captain"] else None
        )
        if tc_candidate is None and availability["triple_captain"] and tc_peak_window and tc_peak_window["gw"] > target_gw:
            tc_candidate = {"gw": tc_peak_window["gw"]}
        fh_candidate = next((b for b in all_blanks if b["gw"] in lookahead), None) if availability["free_hit"] else None
        default_lead = int(chip_cfg.get("wildcard_default_lead", 2))
        post_gws = [g for g in lookahead if g <= half_end]
        occupied = {target_gw}

        def reserve(candidate, offset, is_avail=True):
            if not is_avail:
                return None
            desired = candidate["gw"] if candidate else target_gw + default_lead + offset
            chosen = next((g for g in post_gws if g >= desired and g not in occupied), None)
            if chosen is None:
                chosen = next((g for g in reversed(post_gws) if g not in occupied), None)
            if chosen is not None:
                occupied.add(chosen)
            return chosen

        fh_gw = reserve(fh_candidate, 0, availability["free_hit"])
        tc_gw = reserve(tc_candidate, int(chip_cfg.get("post_wildcard_triple_captain_gap", 3)), availability["triple_captain"])
        bb_gw = reserve(bb_candidate, int(chip_cfg.get("post_wildcard_bench_boost_gap", 1)), availability["bench_boost"])
        current["follow_up_plan"] = {
            "free_hit_gw": fh_gw,
            "triple_captain_gw": tc_gw,
            "bench_boost_gw": bb_gw,
            "free_hit_status": "used" if not availability["free_hit"] else ("fixture_candidate" if fh_candidate else "awaiting_fixture_window"),
            "triple_captain_status": "used" if not availability["triple_captain"] else ("fixture_candidate" if tc_candidate else "provisional"),
            "bench_boost_status": "used" if not availability["bench_boost"] else ("fixture_candidate" if bb_candidate else "provisional"),
            "note": "Wildcard 后每轮最多开一张；剩余可用筹码已错峰分配到不同轮次。",
        }

    plan = []
    for chip in ("wildcard", "free_hit", "bench_boost", "triple_captain"):
        if not availability[chip]:
            used_ev = used_map.get(chip)
            used_text = f"GW{used_ev} 已使用" if used_ev else "本半赛季已使用"
            plan.append({
                "chip": chip,
                "name": CHIP_NAMES[chip],
                "status": "used",
                "used_gw": used_ev,
                "target_gw": None,
                "reason": f"{used_text}（GW{20 if half == 2 else 1}–{38 if half == 2 else 19} 每半程各一张）",
            })
            continue
        target = None
        reason = "暂未发现明确优势轮次；继续观察赛程更新"
        if chip == "wildcard":
            if current and current["chip"] == "wildcard":
                target, reason = target_gw, current["reason"]
            elif weak_count >= int(chip_cfg.get("wildcard_weak_squad_threshold", 5)):
                target, reason = target_gw, f"当前有 {weak_count} 名伤停或出场存疑球员，考虑重建阵容"
            else:
                target = min(half_end, target_gw + int(chip_cfg.get("wildcard_default_lead", 2)))
                gain_hint = f"（当前评估5轮潜在增益 +{to_float(wildcard_five_gw_gain):.1f} 分）" if wildcard_five_gw_gain is not None else ""
                reason = f"围绕 GW{target} 前后评估结构重建{gain_hint}，衔接剩余半程筹码窗口"
        elif chip == "free_hit":
            if blanks:
                best_blank = max(blanks, key=lambda x: (x["players"], x["strength"]))
                target = best_blank["gw"]
                reason = f"GW{target} 有 {best_blank['players']} 名核心球员赛程偏弱，适合临时换阵冲击单轮上限"
            else:
                target = min(half_end, max(target_gw + 1, half_end - 2))
                reason = f"关注 GW{target} 前后的强强对话扎堆轮次临时换阵冲分"
        elif chip == "bench_boost":
            if doubles:
                target = max(doubles, key=lambda x: (x["players"], x["strength"]))["gw"]
                reason = "选择较多主力双赛、且替补四人稳健首发的轮次"
            else:
                target = min(half_end, target_gw + int(chip_cfg.get("wildcard_default_lead", 2)) + int(chip_cfg.get("post_wildcard_bench_boost_gap", 1)))
                reason = f"建议在 Wildcard 整理完整 15 名主力后于 GW{target} 左右评估 Bench Boost"
        elif chip == "triple_captain":
            if doubles:
                target = doubles[0]["gw"]
                reason = "优先安排在核心队长有双赛且 Captain Score 达标的轮次"
            elif tc_peak_window:
                target = tc_peak_window["gw"]
                venue_str = "主场" if tc_peak_window["home"] else "客场"
                reason = (f"上半程暂无双赛，锁定 GW{target} {tc_peak_window['player']} "
                          f"{venue_str}优势赛程（单轮预计 {tc_peak_window['est_xp']:.1f} 分）")
            else:
                target = min(half_end, target_gw + int(chip_cfg.get("wildcard_default_lead", 2)) + int(chip_cfg.get("post_wildcard_triple_captain_gap", 3)))
                reason = f"暂定 GW{target} 附近，届时按头牌前锋主场弱旅窗口确认"
        plan.append({"chip": chip, "name": CHIP_NAMES[chip], "status": "available", "target_gw": target, "reason": reason})

    return {
        "target_gw": target_gw,
        "available": availability,
        "used_chips": used_map,
        "season_half": half,
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
                              ["wildcard", "free_hit", "triple_captain", "bench_boost"].index(item["chip"])))],
        "windows": {"double_gameweeks": doubles, "blank_gameweeks": blanks},
        "signals": {"bench_score": round(bench_value, 2),
                    "bench_xp": bench_xp,
                    "wildcard_five_gw_gain": round(to_float(wildcard_five_gw_gain), 2) if wildcard_five_gw_gain is not None else None,
                    "captain_score": round(cap_value, 2),
                    "captain_xp": round(cap_xp, 2) if cap_xp is not None else None,
                    "captain_fixture_count": cap_count,
                    "captain_fixture_difficulty": round(cap_fixture_diff, 2),
                    "free_hit_net_gain": (round(to_float(free_hit_net_gain), 2)
                                           if free_hit_net_gain is not None else None),
                    "free_hit_risk_adjusted_gain": (round(risk_adjusted_gain, 2)
                                                    if risk_adjusted_gain is not None else None),
                    "free_hit_required_gain": round(required_free_hit_gain, 2),
                    "free_hit_confidence": round(confidence, 2)},
    }
