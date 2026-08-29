"""Phase 0 入口：拉取 FPL 数据 -> 生成 data/state.json + data/history.json。

用法（仓库根目录）:  python -m brain

原始数据（bootstrap/fixtures 等）只在内存中处理，处理完即丢弃，
绝不落盘，避免仓库越来越大。
"""
import sys
import time

from brain import api, config, context, data_store


def main():
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

    picks = api.get_picks(config.TEAM_ID, gw)
    if not picks.get("picks") and gw > 1:
        picks = api.get_picks(config.TEAM_ID, gw - 1)

    state = context.build_state(bootstrap, entry, entry_history, picks, gw)
    history = context.build_history(bootstrap, entry_history)

    data_store.validate_state(state)
    data_store.validate_history(history)
    data_store.save_json(config.STATE_FILE, state)
    data_store.save_json(config.HISTORY_FILE, history)

    print(
        f"[brain] 完成: GW{state['current_gw']} 积分={state['points']} 排名={state['rank']} "
        f"阵型={state['formation'] or '-'} 队长={state['captain'] or '-'} "
        f"历史 {len(history['history'])} 轮 (耗时 {time.time() - t0:.1f}s)"
    )
    if config.DEBUG:
        print(
            f"[brain] debug: players={len(bootstrap['elements'])} "
            f"fixtures={len(fixtures)} team_players={len(state['team'])}"
        )


if __name__ == "__main__":
    main()
