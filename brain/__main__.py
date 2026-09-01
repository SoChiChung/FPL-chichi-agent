"""Phase 1 入口：拉取 FPL 数据 → Market Score 评分 → 决策（阵容/阵型/队长/转会建议）→ 写 JSON。

用法（仓库根目录）:  python -m brain

基础阵容来源（新账号兼容）：
  1. 当前 GW 有 picks → 直接使用；
  2. 当前 GW 无 picks（404/空）→ 向前搜索最近一个有 picks 的 GW（auto-pick/历史阵容）；
  3. 完全找不到 → 执行新账号 establish：按 Market Score 生成合法 15 人阵容。
以上来源都在 notes 中明确记录，绝不沿用旧账号 data/state.json 中的阵容。

决策只产生建议并写入 state.json / history.json，
不执行任何 FPL 写操作（自动提交属于 Phase 3 Executor）。
"""
import sys
import time

from brain import api, captain, config, context, data_store
from brain import history_writer, lineup, market, squad_builder, strategy_config, transfer
from brain.external import fpl_joe
from brain.market import to_float


def _player_score(player) -> float:
    return to_float(player.get("market_score"))


def _summary(player: dict) -> dict:
    return {"id": player.get("id"), "name": player.get("name", "?")}


def _transfer_notes(suggestions: list, t_notes: list, ts: dict) -> list:
    """前端直接渲染的纯文本说明。"""
    lines = []
    if suggestions:
        if ts["status"] == "unlimited":
            lines.append(f"本轮建议 {len(suggestions)} 笔转会（新账号，次数暂不受限）")
        else:
            lines.append(f"本轮建议 {len(suggestions)} 笔免费转会")
    for n in t_notes:
        if n.get("topic") == "no_transfer":
            player = n.get("player")
            lines.append(f"{player}：{n['detail']}" if player else n["detail"])
    return lines


def _make_console_encoding_safe():
    """Windows GBK 控制台遇到球员名（如 João、Milenković）会 UnicodeEncodeError；
    让输出流用 replace 容错，保证永不因打印崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


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

    events = bootstrap["events"]
    gw = context.resolve_current_gw(events)

    # 外部预测源：FPL Joe（数据层，仅刷新三个 JSON，不参与决策；失败不阻塞管线）
    ext_notes = []
    try:
        ext_notes = fpl_joe.refresh(config.SEASON, gw + 1)
    except Exception as exc:  # 外部源任何异常都不应中断主流程
        ext_notes = [{"topic": "external_source", "detail": f"FPL Joe 刷新异常: {exc}"}]
        print(f"[brain] warning: FPL Joe 刷新异常（不影响主流程）: {exc}")

    cfg = strategy_config.load()
    weights = strategy_config.get_weights(cfg, gw)

    players_map = context.build_player_map(bootstrap)
    market_scores = market.compute_market_scores(players_map, weights)

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

    formation, starting_xi, bench = lineup.select_lineup(squad, cfg.get("formations", []))

    selector = captain.MarketCaptainSelector()
    cap, vice = selector.select(starting_xi)

    ts = transfer.resolve_transfer_status(
        entry_history, gw, cfg.get("max_free_transfers", 5), establish=(picks is None))
    if ts["status"] == "unlimited":
        notes.append({"topic": "transfer_status",
                      "detail": "新账号场景：无已完结轮次转会历史，转会次数暂不受限"})
    else:
        notes.append({"topic": "transfer_status",
                      "detail": f"已检测到已完结轮次转会历史，按免费转会数限制建议（{ts['free_transfers']} 次）"})

    suggestions, t_notes = transfer.evaluate_transfers(
        squad, players_map, market_scores, cfg, state["bank"], ts)
    notes.extend(t_notes)
    notes.extend(ext_notes)

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
        "transfer_notes": _transfer_notes(suggestions, t_notes, ts),
    }

    metrics = {
        "team_market_score": round(sum(_player_score(p) for p in squad), 2),
        "captain_market_score": round(_player_score(cap), 2) if cap else 0.0,
        "formation_market_score": round(sum(_player_score(p) for p in starting_xi), 2),
    }

    state["manager_id"] = config.TEAM_ID
    state["market"] = {"weights": weights}
    state["decision"] = decision

    data_store.validate_state(state)
    history, replaced = history_writer.init_history_for_account(
        data_store.load_json(config.HISTORY_FILE, None), config.TEAM_ID, config.SEASON)
    if replaced:
        notes.append({"topic": "warning",
                      "detail": "history.json 属于其他账号，已从空历史开始当前账号"})
    history_writer.upsert_decision(history, gw, decision, notes, metrics,
                                   strategy_config.snapshot(cfg, weights))
    data_store.validate_history(history)
    data_store.save_json(config.STATE_FILE, state)
    data_store.save_json(config.HISTORY_FILE, history)

    src_label = {
        "picks": "当前阵容",
        "auto_pick": f"auto-pick GW{picks_gw}",
        "establish": "establish 生成",
    }[squad_source["type"]]
    print(
        f"[brain] 完成: GW{state['current_gw']} 积分={state['points']} 排名={state['rank']} "
        f"阵型={formation or '-'} 队长={(cap or {}).get('name') or '-'} "
        f"阵容来源={src_label} 转会={ts['status']} "
        f"建议转会={len(suggestions)} 笔 历史 {len(history['history'])} 轮 "
        f"(耗时 {time.time() - t0:.1f}s)"
    )
    if config.DEBUG:
        print(
            f"[brain] debug: players={len(bootstrap['elements'])} fixtures={len(fixtures)} "
            f"team_players={len(squad)}"
        )


if __name__ == "__main__":
    main()
