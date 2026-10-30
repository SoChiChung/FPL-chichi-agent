"""入口：拉取 FPL 数据 → Market Score 评分 → Phase 2 决策（Lineup/Captain Engine）→ 写 JSON。

用法（仓库根目录）:  python -m brain

基础阵容来源（新账号兼容）：
  1. 当前 GW 有 picks → 直接使用；
  2. 当前 GW 无 picks（404/空）→ 向前搜索最近一个有 picks 的 GW（auto-pick/历史阵容）；
  3. 完全找不到 → 执行新账号 establish：按 Market Score 生成合法 15 人阵容。
以上来源都在 notes 中明确记录，绝不沿用旧账号 data/state.json 中的阵容。

Phase 2（docs/decide.md v1.1）：
  - 评分目标 GW = next_deadline 对应的轮次（context.resolve_target_gw），
    FPL Joe 刷新窗口从目标 GW 起算（原为 gw+1，修正覆盖错位）；
  - Form：官方 bootstrap 全员 form 归一（League-wide，不新增请求）；
  - Streak：15 人 element-summary 最近 3 个 finished 轮实际分（失败降级记 0）；
  - 首发：C(15,11) 全枚举全局最优；队长：Captain Score（爆发视角）取首发最高。
  lineup.py / captain.py（Phase 1 规则版）已退出调用链，文件暂留作回归对照。

决策只产生建议并写入 state.json / history.json，
不执行任何 FPL 写操作（自动提交属于 Phase 3 Executor）。
"""
import sys
import time
import copy

from brain import api, captain_score, chips, config, context, data_store
from brain import history_writer, lineup_score, live, market, squad_builder, strategy_config, transfer
from brain.external import fpl_joe
from brain.market import to_float


def _summary(player: dict) -> dict:
    return {"id": player.get("id"), "name": player.get("name", "?")}


def _transfer_notes(suggestions: list, t_notes: list, ts: dict) -> list:
    """前端直接渲染的纯文本说明。"""
    lines = []
    if suggestions:
        if ts["status"] == "unlimited":
            lines.append(f"本轮建议 {len(suggestions)} 笔转会（新账号，次数暂不受限）")
        else:
            hit_count = sum(1 for s in suggestions if to_float(s.get("hit_cost")) > 0)
            free_count = len(suggestions) - hit_count
            label = f"{free_count} 笔免费转会"
            if hit_count:
                hit_points = sum(to_float(s.get("hit_cost")) for s in suggestions)
                label += f" + {hit_count} 笔扣分转会（扣 {hit_points:.0f} 分）"
            lines.append(f"本轮建议 {label}")
    for n in t_notes:
        if n.get("topic") == "no_transfer":
            player = n.get("player")
            lines.append(f"{player}：{n['detail']}" if player else n["detail"])
    return lines


def _build_suggested_squad(squad: list, suggestions: list, players_map: dict,
                           market_scores: dict, team_data: dict, form_by_id: dict,
                           recent_points: dict, engine_cfg: dict,
                           projection_by_id: dict, fixtures_by_team_id: dict):
    """应用全部建议转会后的 15 人完整快照（供前端「转会后阵容」视图）。

    - 底 = 决策用基础 squad；逐笔 out→in 替换后，重新优化首发、替补和 C/V，
      与转会包未来积分评估使用同一套阵容优化逻辑。
    - in 球员从全池 players_map 取基础档案，再经 score_squad 单点补全
      score_breakdown / lineup_score（recent_points 缺该球员 → streak 按 0 降级）。
    - 任何一笔无法解析（out 不在阵 / in 不在全池）→ 返回 None（保守不落盘，
      前端自动退化为单视图，避免给用户看残缺阵容）。
    """
    if not suggestions:
        return None
    pool = [dict(p) for p in squad]
    for s in suggestions:
        out_id, in_id = s["out"]["id"], s["in"]["id"]
        idx = next((j for j, p in enumerate(pool) if p["id"] == out_id), None)
        base = players_map.get(in_id)
        if idx is None or base is None:
            return None
        incoming = {
            **base, "id": in_id,
            "market_score": market_scores.get(in_id, 0.0),
            "starting": False,
            "multiplier": 1,
            "is_captain": False,
            "is_vice_captain": False,
            "fixtures": fixtures_by_team_id.get(base.get("team_id"), []),
        }
        lineup_score.score_squad([incoming], team_data, form_by_id,
                                 recent_points, engine_cfg, projection_by_id)
        pool[idx] = incoming

    # Rebuild the post-transfer XI from the full squad. Keeping the outgoing
    # player's old slot can put a high-value signing such as Haaland on bench.
    for player in pool:
        player.update(starting=False, multiplier=1, is_captain=False,
                      is_vice_captain=False)
    _, starting_xi, bench = lineup_score.select_starting_xi(pool, engine_cfg)
    captain, vice = captain_score.Phase2CaptainSelector(engine_cfg).select(starting_xi)
    for player in starting_xi:
        player["starting"] = True
    for player in bench:
        player["starting"] = False
    if captain:
        captain["is_captain"] = True
        captain["multiplier"] = 2
    if vice:
        vice["is_vice_captain"] = True
    return pool


def _build_free_hit_squad(players_map: dict, target_scores: dict, cfg: dict,
                          budget: float, team_data: dict, form_by_id: dict,
                          engine_cfg: dict, score_cfg: dict,
                          fixtures_by_team_id: dict, market_scores: dict,
                          point_calibration: dict,
                          team_matches_played_by_team: dict):
    """Build and optimize a legal one-week Free Hit squad within the real budget."""
    build_cfg = copy.deepcopy(cfg)
    build_cfg["budget"] = max(0.0, to_float(budget))
    chip_cfg = cfg.get("chip_strategy") or {}
    pool, warnings, selection_meta = squad_builder.build_free_hit_squad(
        players_map, target_scores, market_scores, build_cfg, build_cfg["budget"],
        fixtures_by_team_id, point_calibration, team_data=team_data,
        team_matches_played_by_team=team_matches_played_by_team,
        conflict_penalty=chip_cfg.get("free_hit_opponent_defender_penalty", 0.75),
        beam_width=chip_cfg.get("free_hit_beam_width", 512),
        fpljoe_reference_weight=chip_cfg.get("free_hit_fpljoe_reference_weight", 0.5))
    if warnings or len(pool) != int(build_cfg.get("squad_size", 15)):
        return None
    total_cost = sum(to_float(p.get("price")) for p in pool)
    if total_cost > build_cfg["budget"] + 1e-9:
        return None

    team_id_by_abbr = {p.get("team"): p.get("team_id") for p in players_map.values()}
    for player in pool:
        original = players_map.get(player["id"], {})
        player["team_id"] = original.get("team_id")
        player["horizon_points"] = original.get("horizon_points", [])
        player["five_gw_points"] = original.get("five_gw_points", 0.0)
        player["fixtures"] = fixtures_by_team_id.get(
            team_id_by_abbr.get(player.get("team")), [])
    lineup_score.score_squad(pool, team_data, form_by_id, {}, score_cfg, {})
    squad_builder.apply_start_probability(pool)
    formation, starting_xi, bench = lineup_score.select_starting_xi(pool, engine_cfg)
    captain, vice = captain_score.Phase2CaptainSelector(engine_cfg).select(starting_xi)
    for player in pool:
        player.update(starting=False, multiplier=1, is_captain=False,
                      is_vice_captain=False, bench_priority=None)
    for player in starting_xi:
        player["starting"] = True
    for player in bench:
        player["starting"] = False
        if player.get("pos") == "GKP":
            player["bench_priority"] = 1
    outfield_bench = [p for p in bench if p.get("pos") != "GKP"]
    for priority, player in enumerate(outfield_bench, start=2):
        player["bench_priority"] = priority
    if captain:
        captain["is_captain"] = True
        captain["multiplier"] = 2
    if vice:
        vice["is_vice_captain"] = True
    starting_points = sum(to_float(p.get("projected_points")) for p in starting_xi)
    bench_points = sum(to_float(p.get("projected_points")) for p in bench)
    captain_points = to_float(captain.get("projected_points")) if captain else 0.0
    defender_pairs = _opposing_defender_pairs(starting_xi)
    conflict_penalty = to_float(
        chip_cfg.get("free_hit_opponent_defender_penalty"), 0.75)
    value = (starting_points + captain_points + 0.25 * bench_points -
             conflict_penalty * defender_pairs)
    return {
        "formation": formation,
        "squad": pool,
        "projected_points": round(starting_points + captain_points, 2),
        "projected_value": round(value, 2),
        "opposing_defender_pairs": defender_pairs,
        "selection_meta": selection_meta,
    }


def _opposing_defender_pairs(players: list) -> int:
    """Count starting defenders whose teams meet in the same GW fixture."""
    defenders = [p for p in players if p.get("pos") == "DEF"]
    pairs = 0
    for index, player in enumerate(defenders):
        opponents = {f.get("opponent") for f in player.get("fixtures") or []}
        for other in defenders[index + 1:]:
            other_opponents = {f.get("opponent") for f in other.get("fixtures") or []}
            if other.get("team") in opponents or player.get("team") in other_opponents:
                pairs += 1
    return pairs


def _projected_plan_value(starting_xi: list, bench: list, captain: dict,
                          conflict_penalty: float) -> float:
    starters = sum(to_float(p.get("projected_points")) for p in starting_xi)
    bench_points = sum(to_float(p.get("projected_points")) for p in bench)
    captain_points = to_float(captain.get("projected_points")) if captain else 0.0
    return (starters + captain_points + 0.25 * bench_points -
            to_float(conflict_penalty) * _opposing_defender_pairs(starting_xi))


def _fetch_recent_points(squad: list, events: list):
    """拉 15 人 element-summary，返回 (recent_points, recent_rounds, failures)。

    recent_points: {element_id: [P(GW-1)...P(GW-3)]}（finished 轮近者在前，缺位 None）；
    单个球员请求失败只降级该球员（记 0），不阻塞管线。
    """
    finished = sorted((e["id"] for e in events if e.get("finished")), reverse=True)[:3]
    recent, failed = {}, []
    for p in squad:
        try:
            summary = api.get_element_summary(p["id"])
            by_round = api.points_by_round(summary)
            recent[p["id"]] = [by_round.get(r) for r in finished]
        except api.FplApiError as exc:
            failed.append((p.get("name", p.get("id")), str(exc)))
    return recent, finished, failed


def main():
    _make_console_encoding_safe()
    if config.TEAM_ID <= 0:
        print("错误: 尚未配置队伍 ID。请在 brain/config.py 中修改 TEAM_ID 后重试。")
        sys.exit(1)

    t0 = time.time()
    print(f"[brain] season={config.SEASON} team_id={config.TEAM_ID}")

    bootstrap = api.get_bootstrap()
    fixtures = api.get_fixtures()
    entry = api.get_entry(config.TEAM_ID)
    entry_history = api.get_entry_history(config.TEAM_ID)
    actual_transfers = api.get_entry_transfers(config.TEAM_ID)

    events = bootstrap["events"]
    gw = context.resolve_current_gw(events)
    target_gw = context.resolve_target_gw(events)
    team_matches_played_by_team = {}
    for fixture in fixtures:
        event_id = fixture.get("event")
        if event_id is None or event_id >= target_gw or not fixture.get("started"):
            continue
        for team_id in (fixture.get("team_h"), fixture.get("team_a")):
            if team_id is not None:
                team_matches_played_by_team[team_id] = (
                    team_matches_played_by_team.get(team_id, 0) + 1)

    # 外部预测源：FPL Joe（仅刷新数据；目标 GW 起算窗口，见 decide.md §2.2）
    ext_notes = []
    try:
        ext_notes = fpl_joe.refresh(config.SEASON, target_gw)
    except Exception as exc:  # 外部源任何异常都不应中断主流程
        ext_notes = [{"topic": "external_source", "detail": f"FPL Joe 刷新异常: {exc}"}]
        print(f"[brain] warning: FPL Joe 刷新异常（不影响主流程）: {exc}")

    cfg = strategy_config.load()
    engine_cfg = strategy_config.get_lineup_engine(cfg)
    weights = strategy_config.get_weights(cfg, gw)

    players_map = context.build_player_map(bootstrap)
    market_scores = market.compute_market_scores(players_map, weights)
    previous_state = data_store.load_json(config.STATE_FILE, {}) or {}
    previous_live = (previous_state.get("live_gameweek")
                     if previous_state.get("manager_id") == config.TEAM_ID else None)

    # ---- 基础阵容：当前/最近可用 picks；完全找不到则 establish ----
    picks, picks_gw = api.find_latest_picks(config.TEAM_ID, gw)
    notes = []
    if picks is not None:
        if picks_gw == gw:
            squad_source = {"type": "picks", "gw": gw}
            notes.append({"topic": "squad_source", "detail": f"使用当前 GW{gw} FPL 阵容作为基础阵容"})
        else:
            squad_source = {"type": "auto_pick", "gw": picks_gw}
            notes.append({"topic": "squad_source",
                          "detail": f"使用 GW{picks_gw} FPL auto-pick 阵容作为当前基础阵容"})
        state = context.build_state(bootstrap, entry, entry_history, picks, gw, market_scores)
        squad = state["team"]
    else:
        squad, warnings = squad_builder.build_squad(players_map, market_scores, cfg)
        squad_source = {"type": "establish", "gw": None}
        notes.append({"topic": "squad_source",
                      "detail": "未找到历史 picks，已执行新账号 establish（按 Market Score 生成 15 人阵容）"})
        for w in warnings:
            notes.append({"topic": "warning", "detail": w})
        state = context.build_state(bootstrap, entry, entry_history, {}, gw, market_scores)
        state["team"] = squad

    current_event = next((event for event in events if event.get("id") == gw), None)
    live_snapshot = live.snapshot(
        gw, current_event, fixtures, entry_history, picks, picks_gw,
        config.TEAM_ID, previous=previous_live)
    state["live_gameweek"] = live_snapshot

    # ---- Phase 2 评分输入：官方 form 全员池 + 15 人近 3 轮实际分 ----
    form_by_id = {int(e["id"]): e.get("form", "0") for e in bootstrap["elements"]}
    recent_points, recent_rounds, summary_failures = _fetch_recent_points(squad, events)
    if summary_failures:
        names = ", ".join(str(n) for n, _ in summary_failures)
        notes.append({"topic": "data_missing",
                      "detail": f"element-summary 拉取失败 {len(summary_failures)} 人（{names}），"
                                f"其 Streak 按 0 处理"})

    team_abbr_by_id = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    team_id_by_abbr = {t["short_name"]: t["id"] for t in bootstrap["teams"]}
    team_data = fpl_joe.read_target_gw(target_gw, team_abbr_by_id)
    official_fixtures = context.official_fixture_fallback(
        fixtures, bootstrap.get("teams") or [], target_gw)
    # Keep FPL Joe difficulty primary; only use official FDR when a team row is missing.
    used_official_fdr = False
    for abbr, values in official_fixtures["teams"].items():
        existing = team_data.setdefault("teams", {}).setdefault(abbr, {})
        if values.get("fixture") is not None and existing.get("fixture") is None:
            existing["fixture"] = values["fixture"]
            used_official_fdr = True
    if used_official_fdr:
        team_data.setdefault("neutral", {})["fixture"] = False
        team_data.setdefault("sources", {})["fixture"] = "official_fpl_fdr_fallback"
        team_data.setdefault("notes", []).append({
            "topic": "score_source",
            "detail": f"FPL Joe GW{target_gw} Fixture 无比赛数据，已回退官方 FDR",
        })
    # Turn our own lineup model scores into expected FPL-point units. Historical
    # points_per_game is only the observed calibration label; official ep_next is unused.
    model_cfg = copy.deepcopy(engine_cfg)
    for key in ("attack_weights", "attack_potential_weights"):
        model_cfg.setdefault(key, {})["streak"] = 0.0
    model_scores_by_gw = {}
    model_pool = [
        {**info, "id": pid, "market_score": market_scores.get(pid, 0.0)}
        for pid, info in players_map.items()
    ]
    for event in range(target_gw, target_gw + 5):
        score_data = team_data if event == target_gw else fpl_joe.read_target_gw(event, team_abbr_by_id)
        if event != target_gw:
            event_fallback = context.official_fixture_fallback(
                fixtures, bootstrap.get("teams") or [], event)
            for abbr, values in event_fallback["teams"].items():
                existing = score_data.setdefault("teams", {}).setdefault(abbr, {})
                if values.get("fixture") is not None and existing.get("fixture") is None:
                    existing["fixture"] = values["fixture"]
                    score_data.setdefault("neutral", {})["fixture"] = False
        scored_pool = [dict(p) for p in model_pool]
        scored_pool, _ = lineup_score.score_squad(
            scored_pool, score_data, form_by_id, {}, model_cfg, {})
        model_scores_by_gw[event] = {
            p["id"]: p["lineup_score"] for p in scored_pool
        }
    point_calibration = context.project_five_gw_points(
        players_map, model_scores_by_gw, fixtures, target_gw)

    # ---- Phase 2 决策：本轮评分 → 全局最优首发 → 爆发队长 ----
    squad, score_notes = lineup_score.score_squad(
        squad, team_data, form_by_id, recent_points, engine_cfg, {})
    for p in squad:
        forecast = players_map.get(p.get("id"), {})
        p["five_gw_points"] = forecast.get("five_gw_points", 0.0)
        p["horizon_points"] = forecast.get("horizon_points", [])
        p["projection_gws"] = forecast.get("projection_gws", [])
        p["projected_points"] = squad_builder.expected_points(
            forecast, model_scores_by_gw.get(target_gw, {}).get(p.get("id"), 0.0),
            point_calibration,
            team_matches_played_by_team.get(forecast.get("team_id"), 0))
        p["starting_probability"] = squad_builder.starting_probability(
            forecast, team_matches_played_by_team.get(forecast.get("team_id"), 0))
        p["fixtures"] = official_fixtures["by_team_id"].get(
            team_id_by_abbr.get(p.get("team")), [])
    squad_builder.apply_start_probability(squad)
    formation, starting_xi, bench = lineup_score.select_starting_xi(squad, engine_cfg)
    captain = captain_score.Phase2CaptainSelector(engine_cfg)
    cap, vice = captain.select(starting_xi)
    for p in squad:
        p.setdefault("captain_score", 0.0)
    notes.extend(score_notes)
    notes.append({"topic": "lineup",
                  "detail": (f"全局枚举首发：阵型 {formation}，"
                             f"Total Lineup Score {round(sum(p['lineup_score'] for p in starting_xi), 2)}"
                             f"（替补 4 人按 Lineup Score 降序，门将恒最后）")})
    if cap:
        notes.append({"topic": "captain", "player": cap.get("name", "?"),
                      "detail": f"爆发视角 Captain Score {cap['captain_score']:.2f}"
                                f"（首发 11 人中最高；本轮 Lineup Score {cap.get('lineup_score', '-')}）"})

    chip_cfg = dict(cfg)
    chip_cfg["_api_chips"] = bootstrap.get("chips", [])
    chip_plan = chips.evaluate(squad, cap, entry_history, events, fixtures,
                               bootstrap.get("teams", []), target_gw, chip_cfg)
    active_chip = chip_plan.get("current_recommendation")
    free_hit_squad_plan = None
    free_hit_rejected_detail = None
    free_hit_available = (
        chip_plan.get("available", {}).get("free_hit", False) and
        not chip_plan.get("chip_already_used_this_gw", False)
    )
    if free_hit_available:
        free_hit_budget = state["bank"] + sum(
            to_float(p.get("selling_price", p.get("price"))) for p in squad)
        free_hit_squad_plan = _build_free_hit_squad(
            players_map, model_scores_by_gw.get(target_gw, {}), cfg,
            free_hit_budget, team_data, form_by_id, engine_cfg, model_cfg,
            official_fixtures["by_team_id"], market_scores, point_calibration,
            team_matches_played_by_team)
        if free_hit_squad_plan is None:
            if active_chip and active_chip.get("chip") == "free_hit":
                notes.append({"topic": "warning",
                              "detail": "Free Hit 候选阵容因预算或球员数据不足而无法生成"})
            chip_plan = chips.evaluate(
                squad, cap, entry_history, events, fixtures,
                bootstrap.get("teams", []), target_gw, chip_cfg,
                free_hit_assessed=True)
            active_chip = chip_plan.get("current_recommendation")
        else:
            conflict_penalty = to_float(
                (cfg.get("chip_strategy") or {}).get(
                    "free_hit_opponent_defender_penalty"), 0.75)
            baseline_value = _projected_plan_value(
                starting_xi, bench, cap, conflict_penalty)
            free_hit_net_gain = free_hit_squad_plan["projected_value"] - baseline_value
            neutral = team_data.get("neutral") or {}
            missing_core_inputs = sum(
                1 for key in ("projection", "clean_sheet") if neutral.get(key))
            free_hit_confidence = max(0.55, 1.0 - 0.2 * missing_core_inputs)
            chip_plan = chips.evaluate(
                squad, cap, entry_history, events, fixtures,
                bootstrap.get("teams", []), target_gw, chip_cfg,
                free_hit_net_gain=free_hit_net_gain,
                free_hit_confidence=free_hit_confidence,
                free_hit_assessed=True)
            active_chip = chip_plan.get("current_recommendation")
            if active_chip and active_chip.get("chip") == "free_hit":
                base_captain_note = next(
                    (note for note in notes if note.get("topic") == "captain"), None)
                if base_captain_note:
                    base_captain_note["topic"] = "base_captain"
                free_hit_captain = next(
                    (player for player in free_hit_squad_plan["squad"]
                     if player.get("is_captain")), None)
                if free_hit_captain:
                    notes.append({
                        "topic": "free_hit_captain",
                        "player": free_hit_captain.get("name", "?"),
                        "detail": (f"Captain Score {free_hit_captain.get('captain_score', 0):.2f}；"
                                   "按 Free Hit 临时阵容重新计算"),
                    })
                notes.append({"topic": "free_hit_squad",
                              "detail": (f"GW{target_gw} Free Hit 临时阵容为 {free_hit_squad_plan['formation']}，"
                                         f"相较原阵容预计净增 {free_hit_net_gain:.2f} 分；"
                                         f"首发对阵后卫冲突 {free_hit_squad_plan['opposing_defender_pairs']} 组")})
            elif any(window.get("gw") == target_gw for window in
                     chip_plan.get("windows", {}).get("blank_gameweeks", [])):
                required = to_float(
                    chip_plan.get("signals", {}).get("free_hit_required_gain"))
                adjusted = to_float(
                    chip_plan.get("signals", {}).get("free_hit_risk_adjusted_gain"))
                free_hit_rejected_detail = (
                    f"Free Hit 暂不建议：模型净增经数据可信度调整后为 "
                    f"{adjusted:.2f} 分，低于 {required:.2f} 分门槛")
                free_hit_squad_plan = None
    if active_chip:
        notes.append({"topic": "chip", "chip": active_chip["chip"],
                      "detail": f"建议本轮使用 {active_chip['name']}：{active_chip['reason']}"})
        if free_hit_rejected_detail:
            notes.append({"topic": "chip", "detail": free_hit_rejected_detail})
    else:
        notes.append({"topic": "chip",
                      "detail": free_hit_rejected_detail or
                      "本轮暂不建议开卡；筹码计划已结合未来赛程保留更优窗口"})

    # Transfer allowance belongs to the GW we are making a recommendation for.
    # If the next deadline is GW9 while GW8 is the active event, include GW8's
    # completed transfer and hit record when calculating the GW9 free transfer.
    ts = transfer.resolve_transfer_status(
        entry_history, target_gw, cfg.get("max_free_transfers", 5), establish=(picks is None))
    if ts["status"] == "unlimited":
        notes.append({"topic": "transfer_status",
                      "detail": "新账号场景：无已完结轮次转会历史，转会次数暂不受限"})
    else:
        notes.append({"topic": "transfer_status",
                      "detail": f"已检测到已完结轮次转会历史，按免费转会数限制建议（{ts['free_transfers']} 次）"})

    history_for_learning, _ = history_writer.init_history_for_account(
        data_store.load_json(config.HISTORY_FILE, None), config.TEAM_ID, config.SEASON)
    calibration = history_writer.learned_transfer_calibration(history_for_learning)
    transfer_evaluation = {}
    if active_chip and active_chip.get("chip") == "free_hit":
        # A Free Hit squad reverts after this GW, so a persistent transfer
        # package would conflict with the chip recommendation.
        suggestions = []
        t_notes = [{"topic": "no_transfer",
                    "detail": "本轮建议使用 Free Hit；临时阵容将在本轮后恢复，因此不叠加永久转会建议"}]
    else:
        suggestions, t_notes = transfer.evaluate_transfers(
            squad, players_map, market_scores, cfg, state["bank"], ts,
            gain_multiplier=calibration["multiplier"], evaluation_meta=transfer_evaluation)
    notes.extend(t_notes)
    notes.extend(ext_notes)

    # 预算口径汇总（state.bank 为 £m；包内所有转会 100% 预算可行）
    transfer_package = transfer.summarize_package(state["bank"], suggestions, transfer_evaluation)
    transfer_package["calibration_samples"] = calibration["samples"]
    transfer_package["point_calibration"] = point_calibration

    # ---- 转会后阵容快照（suggested_squad）：应用全部建议转会后的完整 15 人 ----
    # in 球员不在 state.team 内、且缺 score_breakdown 等完整字段，前端无法独立
    # 补全一张球员卡 → 由后端用评分引擎补齐后落盘，前端「转会后阵容」视图直接渲染。
    # 无建议转会时不写该字段（前端隐藏切换入口）。
    suggested_squad = _build_suggested_squad(
        squad, suggestions, players_map, market_scores, team_data,
        form_by_id, recent_points, engine_cfg, {},
        official_fixtures["by_team_id"])

    decision = {
        "formation": formation,
        "captain": cap,
        "vice": vice,
        "starting_xi": [_summary(p) for p in starting_xi],
        "bench": [_summary(p) for p in bench],
        "squad_source": squad_source,
        "transfer_status": ts["status"],
        "free_transfers": ts["free_transfers"],
        "recommended_transfers": suggestions,
        "chip_plan": chip_plan,
        "recommended_chip": active_chip,
        "free_hit_formation": free_hit_squad_plan["formation"] if free_hit_squad_plan else None,
        "free_hit_squad": free_hit_squad_plan["squad"] if free_hit_squad_plan else [],
        "transfer_package": transfer_package,
        "transfer_notes": _transfer_notes(suggestions, t_notes, ts),
    }

    team_lineup_score = round(sum(to_float(p.get("lineup_score")) for p in starting_xi), 2)
    metrics = {
        "team_market_score": round(sum(to_float(p.get("market_score")) for p in squad), 2),
        "captain_market_score": round(to_float(cap.get("market_score")), 2) if cap else 0.0,
        "formation_market_score": round(
            sum(to_float(p.get("market_score")) for p in starting_xi), 2),
        "team_lineup_score": team_lineup_score,
        "captain_score": round(to_float(cap.get("captain_score")), 2) if cap else 0.0,
    }

    state["manager_id"] = config.TEAM_ID
    state["market"] = {"weights": weights}
    state["decision"] = decision
    state["lineup_scores"] = {str(p["id"]): p["lineup_score"] for p in squad}
    state["captain_scores"] = {str(p["id"]): p["captain_score"] for p in squad}
    state["score_meta"] = {
        "target_gw": target_gw,
        "recent_rounds": recent_rounds,
        "score_source": team_data["sources"],
        "point_calibration": point_calibration,
        "warnings": [n["detail"] for n in notes
                     if n.get("topic") in ("data_missing", "score_source")],
    }
    if suggested_squad:
        state["suggested_squad"] = suggested_squad

    data_store.validate_state(state)
    history, replaced = history_writer.init_history_for_account(
        history_for_learning, config.TEAM_ID, config.SEASON)
    if replaced:
        notes.append({"topic": "warning",
                      "detail": "history.json 属于其他账号，已从空历史开始当前账号"})
    history_writer.upsert_decision(history, gw, decision, notes, metrics,
                                   strategy_config.snapshot(cfg, weights, engine_cfg),
                                   live_result=live_snapshot)
    # 结算回填：GW 已 finished 的官方数据（points/rank/overall_rank）补写 history
    filled = history_writer.backfill_settlement(history,
                                                entry_history.get("current") or [],
                                                events)
    added = history_writer.backfill_missing_finished(history,
                                                     entry_history.get("current") or [],
                                                     events)
    history_writer.backfill_actual_transfers(
        history, actual_transfers, bootstrap.get("elements") or [],
        entry_history.get("current") or [])
    pending_ids = history_writer.pending_recommended_outcome_ids(history, events)
    actual_points = {}
    for element_id in pending_ids:
        try:
            actual_points[element_id] = api.points_by_round(api.get_element_summary(element_id))
        except api.FplApiError as exc:
            print(f"[brain] warning: 推荐转会复盘暂缺球员 {element_id}: {exc}")
    history_writer.backfill_recommended_transfer_outcomes(history, actual_points, events)
    data_store.validate_history(history)
    data_store.save_json(config.STATE_FILE, state)
    data_store.save_json(config.HISTORY_FILE, history)

    src_label = {
        "picks": "当前阵容",
        "auto_pick": f"auto-pick GW{picks_gw}",
        "establish": "establish 生成",
    }[squad_source["type"]]
    pkg = transfer_package
    print(
        f"[brain] 完成: GW{state['current_gw']} 目标GW{target_gw} 积分={state['points']} "
        f"排名={state['rank']} 阵型={formation or '-'} "
        f"队长={(cap or {}).get('name') or '-'} "
        f"阵容来源={src_label} 转会={ts['status']} "
        f"建议转会={len(suggestions)} 笔 历史 {len(history['history'])} 轮 "
        f"结算回填={filled} 项 新增历史轮次={added} 建议阵容={'有' if suggested_squad else '无'} "
        f"Bank=£{state['bank']:.1f}m 净花费=£{pkg['transfer_cost']:.1f}m "
        f"转会后Bank=£{pkg['budget_after']:.1f}m "
        f"(耗时 {time.time() - t0:.1f}s)"
    )
    if config.DEBUG:
        print(
            f"[brain] debug: players={len(bootstrap['elements'])} fixtures={len(fixtures)} "
            f"team_players={len(squad)} 目标GW近3轮={recent_rounds} "
            f"fpljoe 覆盖队数={len(team_data['teams'])}"
        )


def _make_console_encoding_safe():
    """Windows GBK 控制台遇到球员名（如 João、Milenković）会 UnicodeEncodeError；
    让输出流用 replace 容错，保证永不因打印崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


if __name__ == "__main__":
    main()
