"""独立 FPL 官方结算历史回填任务。"""
from brain import api, config, data_store, history_writer


def run():
    bootstrap = api.get_bootstrap()
    entry_history = api.get_entry_history(config.TEAM_ID)
    history, replaced = history_writer.init_history_for_account(
        data_store.load_json(config.HISTORY_FILE, None), config.TEAM_ID, config.SEASON)
    if replaced:
        raise RuntimeError("history.json 缺失或账号不匹配；拒绝以空文件覆盖现有历史")

    updated = history_writer.backfill_settlement(
        history, entry_history.get("current") or [], bootstrap.get("events") or [])
    added = history_writer.backfill_missing_finished(
        history, entry_history.get("current") or [], bootstrap.get("events") or [])
    data_store.validate_history(history)
    data_store.save_json(config.HISTORY_FILE, history)
    print(f"[backfill] 完成：补入结算字段 {updated} 项，新增已完赛轮次 {added} 轮")


if __name__ == "__main__":
    run()
