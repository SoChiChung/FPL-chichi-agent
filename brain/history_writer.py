"""history.json 决策条目写入（幂等 upsert）。

同一 GW 只保留一个条目：重复运行覆盖 decision/notes/metrics，
结算回填的 points/rank/overall_rank 保持不变。
"""


def init_history_for_account(history, manager_id: int, season: str):
    """按账号归属初始化历史文件，避免不同账号历史混在一起。

    返回 (history, replaced)：文件缺失或 manager_id 与当前账号不符时，
    返回空历史 + replaced=True（宁可重新开始，也不沿用旧账号历史）。
    """
    if history is None or history.get("manager_id") != manager_id:
        return {"season": season, "manager_id": manager_id, "history": []}, True
    return history, False


def _summary(player: dict) -> dict:
    return {"id": player.get("id"), "name": player.get("name", "?")}


def upsert_decision(history: dict, gw: int, decision: dict, notes: list, metrics: dict,
                    strategy_snapshot: dict) -> None:
    """把本轮决策写入 history（同一 GW 幂等覆盖，不重复追加）。"""
    entry = next((h for h in history["history"] if h.get("gw") == gw), None)
    if entry is None:
        entry = {"gw": gw, "points": None, "rank": None, "overall_rank": None}
        history["history"].append(entry)
    entry["decision"] = {
        "formation": decision["formation"],
        "captain": _summary(decision["captain"]),
        "vice": _summary(decision["vice"]),
        "starting_xi": [_summary(p) for p in decision["starting_xi"]],
        "bench": [_summary(p) for p in decision["bench"]],
        "squad": [_summary(p) for p in decision.get("squad", [])],
        "squad_source": decision.get("squad_source"),
        "transfer_status": decision.get("transfer_status"),
        "free_transfers": decision.get("free_transfers"),
        "recommended_transfers": decision["recommended_transfers"],
        "strategy_snapshot": strategy_snapshot,
    }
    entry["notes"] = notes
    entry["metrics"] = metrics
