"""history.json 决策条目写入（幂等 upsert）+ 结算回填。

同一 GW 只保留一个条目：重复运行覆盖 decision/notes/metrics，
结算回填的 points/rank/overall_rank 保持不变。

decided_at：新条目（或旧数据缺失）在首次写入时记录决策时间（UTC ISO），
已存在的值永不覆盖——避免 30 分钟节奏的 bot 每次重跑把时间刷新成"刚刚"。
"""
from datetime import datetime, timezone


def init_history_for_account(history, manager_id: int, season: str):
    """按账号归属初始化历史文件，避免不同账号历史混在一起。

    返回 (history, replaced)：文件缺失或 manager_id 与当前账号不符时，
    返回空历史 + replaced=True（宁可重新开始，也不沿用旧账号历史）。
    """
    if history is None or history.get("manager_id") != manager_id:
        return {"season": season, "manager_id": manager_id, "history": []}, True
    return history, False


def _summary(player: dict) -> dict:
    """把完整球员对象压缩成 {id, name}；无队长/副队长（None）时原样保留。"""
    if not player:
        return player
    return {"id": player.get("id"), "name": player.get("name", "?")}


def upsert_decision(history: dict, gw: int, decision: dict, notes: list, metrics: dict,
                    strategy_snapshot: dict) -> None:
    """把本轮决策写入 history（同一 GW 幂等覆盖，不重复追加）。

    decision.transfer_package（预算口径）除写入 decision 外，还会把
    budget_before / budget_after / transfer_cost 平铺到该轮条目顶层，
    方便复盘时直接看出“AI 当时为什么买得起/买不起”。
    """
    entry = next((h for h in history["history"] if h.get("gw") == gw), None)
    if entry is None:
        entry = {"gw": gw, "points": None, "rank": None, "overall_rank": None}
        history["history"].append(entry)
    # decided_at：首次（新条目或旧数据缺字段）记录，已存在不覆盖
    entry.setdefault("decided_at",
                     datetime.now(timezone.utc).isoformat(timespec="seconds")
                     .replace("+00:00", "Z"))
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
        "transfer_package": decision.get("transfer_package"),
        "strategy_snapshot": strategy_snapshot,
    }
    entry["notes"] = notes
    entry["metrics"] = metrics
    pkg = decision.get("transfer_package")
    if isinstance(pkg, dict):
        for key in ("budget_before", "budget_after", "transfer_cost"):
            if key in pkg and pkg[key] is not None:
                entry[key] = pkg[key]


def backfill_settlement(history: dict, entry_history_current: list, events: list) -> int:
    """把已完结轮次的官方结算数据回填进 history 条目（幂等，不回退）。

    - 判定"已可结算"：FPL bootstrap `events` 中 `finished == true` 的 event id
      （官方标志，不做本地推导；一场打完即 true，积分/排名随之可查）。
    - 数据源：`entry_history["current"]` 中同 event 行的
      `points / rank / overall_rank`。
    - 写入规则：只补 history **已存在** 的 GW 条目（孤儿结算条目不新建，
      保证 Accordion 语义 = 每条历史对应一次 AI 决策）；已有非 null 值不回退。

    每次 brain 运行都会调用（30 分钟节奏天然覆盖 FPL 结算窗口），
    从机制上消灭「轮次早已结束但永远显示待结算」。
    返回本次补写的字段数（便于日志）。
    """
    finished_ids = {e["id"] for e in events if e.get("finished")}
    by_event = {r.get("event"): r for r in (entry_history_current or [])}
    filled = 0
    for gw in finished_ids:
        row = by_event.get(gw)
        if not row:
            continue
        entry = next((h for h in history["history"] if h.get("gw") == gw), None)
        if entry is None:
            continue
        for key in ("points", "rank", "overall_rank"):
            val = row.get(key)
            if val is not None and entry.get(key) is None:
                entry[key] = val
                filled += 1
    return filled
