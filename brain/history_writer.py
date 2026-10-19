"""history.json 决策条目写入（幂等 upsert）+ 结算回填。

同一 GW 只保留一个条目：重复运行覆盖 decision/notes/metrics，
结算回填的 points/rank/overall_rank 保持不变。

时间字段语义（两者并存，勿混用）：
- decided_at：该 GW **首次**产出决策的时刻（UTC ISO）。新条目或旧数据缺失时
  写入，已存在的值永不覆盖——避免 30 分钟节奏的 bot 每次重跑把时间刷新成
  "刚刚"，破坏"当初是什么时候决定的"这一复盘基准。
- updated_at：该 GW 决策**最近一次重算**的时刻（UTC ISO）。每次运行无条件覆盖，
  供前端展示"AI 最近何时思考过"。旧数据（升级前）缺该字段时，下一次重算即补齐，
  此时它等于 decided_at，属预期行为。
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
    # 时间戳只在本次运行取一次，保证首次写入时 decided_at 与 updated_at 严格相等
    now_iso = (datetime.now(timezone.utc).isoformat(timespec="seconds")
               .replace("+00:00", "Z"))
    # decided_at：首次（新条目或旧数据缺字段）记录，已存在不覆盖 —— 复盘基准
    entry.setdefault("decided_at", now_iso)
    # updated_at：每次重算都刷新 —— 前端展示"AI 最近何时思考过"
    entry["updated_at"] = now_iso
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
        "chip_plan": decision.get("chip_plan"),
        "recommended_chip": decision.get("recommended_chip"),
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


def backfill_missing_finished(history: dict, entry_history_current: list, events: list) -> int:
    """Create result-only history rows for finished GWs omitted by the decision run."""
    existing = {h.get("gw") for h in history.get("history", [])}
    rows = {r.get("event"): r for r in (entry_history_current or [])}
    added = 0
    for event in events or []:
        gw = event.get("id")
        row = rows.get(gw)
        if not event.get("finished") or gw in existing or not row:
            continue
        history["history"].append({
            "gw": gw,
            "points": row.get("points"),
            "rank": row.get("rank"),
            "overall_rank": row.get("overall_rank"),
            "decision": None,
            "notes": [{"topic": "settlement_backfill",
                       "detail": "该轮此前没有历史决策，已从 FPL 官方历史补录结算数据"}],
            "metrics": {},
        })
        existing.add(gw)
        added += 1
    history["history"].sort(key=lambda h: h.get("gw", 0))
    return added


def backfill_actual_transfers(history: dict, transfers: list, elements: list,
                              entry_history_current: list = None) -> int:
    """把 FPL 官方实际转会记录按 event 回填到历史条目。"""
    by_id = {p.get("id"): p for p in (elements or [])}
    by_event = {}
    for transfer in transfers or []:
        gw = transfer.get("event")
        if gw is None:
            continue
        out = by_id.get(transfer.get("element_out"), {})
        incoming = by_id.get(transfer.get("element_in"), {})
        by_event.setdefault(gw, []).append({
            "out": {"id": transfer.get("element_out"), "name": out.get("web_name", "?")},
            "in": {"id": transfer.get("element_in"), "name": incoming.get("web_name", "?")},
            "time": transfer.get("time"),
            "cost": transfer.get("element_in_cost"),
        })

    current_rows = {r.get("event"): r for r in (entry_history_current or [])}
    entries = {h.get("gw"): h for h in history.get("history", [])}
    for gw in by_event:
        if gw in entries:
            continue
        result = current_rows.get(gw, {})
        entry = {
            "gw": gw,
            "points": result.get("points"),
            "rank": result.get("rank"),
            "overall_rank": result.get("overall_rank"),
            "decision": None,
            "notes": [{"topic": "transfer_backfill",
                       "detail": "该轮此前没有历史决策，已从 FPL 官方转会记录补录实际操作"}],
            "metrics": {},
        }
        history.setdefault("history", []).append(entry)
        entries[gw] = entry

    updated = 0
    for gw, entry in entries.items():
        actual = by_event.get(gw, [])
        if entry.get("actual_transfers") != actual:
            entry["actual_transfers"] = actual
            updated += 1
    history["history"].sort(key=lambda h: h.get("gw", 0))
    return updated


def _transfer_packages(history):
    for entry in history.get("history", []):
        decision = entry.get("decision") or {}
        package = decision.get("transfer_package") or {}
        if package.get("projection_before", {}).get("rounds") and package.get("projection_after", {}).get("rounds"):
            yield entry, package


def pending_recommended_outcome_ids(history: dict, events: list) -> set:
    """IDs needed to settle stored recommendation forecasts whose horizon finished."""
    finished = {e.get("id") for e in events or [] if e.get("finished")}
    needed = set()
    for entry, package in _transfer_packages(history):
        if package.get("recommended_outcome"):
            continue
        before = package["projection_before"]["rounds"]
        after = package["projection_after"]["rounds"]
        if not before or not all(r.get("gw") in finished for r in before):
            continue
        for row in before + after:
            needed.update(pid for pid in row.get("xi", []) if pid is not None)
            if row.get("captain") is not None:
                needed.add(row["captain"])
    return needed


def backfill_recommended_transfer_outcomes(history: dict, points_by_id: dict, events: list) -> int:
    """Compare the saved final recommendation's projected XI with official FPL points."""
    finished = {e.get("id") for e in events or [] if e.get("finished")}
    updated = 0
    for entry, package in _transfer_packages(history):
        if package.get("recommended_outcome"):
            continue
        before, after = package["projection_before"]["rounds"], package["projection_after"]["rounds"]
        if not before or not all(r.get("gw") in finished for r in before):
            continue
        if any(pid not in points_by_id for row in before + after
               for pid in list(row.get("xi", [])) + ([row["captain"]] if row.get("captain") is not None else [])):
            continue
        def actual(rows):
            total = 0
            for row in rows:
                gw = row["gw"]
                total += sum((points_by_id.get(pid) or {}).get(gw, 0) for pid in row.get("xi", []))
                captain = row.get("captain")
                if captain is not None:
                    total += (points_by_id.get(captain) or {}).get(gw, 0)
            return total
        package["recommended_outcome"] = {
            "before_points": actual(before),
            "after_points": actual(after),
            "actual_gain": actual(after) - actual(before),
            "settled_gws": [r["gw"] for r in before],
        }
        updated += 1
    return updated


def learned_transfer_calibration(history: dict) -> dict:
    """Conservative online calibration from settled final recommendations (>=3 samples)."""
    ratios = []
    for _, package in _transfer_packages(history):
        outcome = package.get("recommended_outcome") or {}
        predicted = package.get("five_gw_after", 0) - package.get("five_gw_before", 0)
        actual = outcome.get("actual_gain")
        if actual is not None and predicted > 0:
            ratios.append(max(0.5, min(1.25, actual / predicted)))
    if len(ratios) < 3:
        return {"multiplier": 1.0, "samples": len(ratios)}
    return {"multiplier": round(max(0.5, min(1.25, sum(ratios) / len(ratios))), 3),
            "samples": len(ratios)}
