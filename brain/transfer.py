"""转会建议（只产生建议，绝不执行任何 FPL 写操作）。

触发条件（满足任一即进入评估）：
  - Market Gap：同位置、不在阵容、同队 ≤ 3、价格可承担的 Market Score 最高者
    与持有者的差值 > market_gap_threshold；
  - 伤病/存疑：chance_of_playing_next_round 非 None 且 < injury_threshold，
    或 status 非 available。
替代者约束：同位置 / 不在当前阵容 / 转入后同队 ≤ 3 /
bank + out.now_cost ≥ in.now_cost（v1 简化出售价规则）。
只使用免费转会；allow_hits=false 时绝不建议超过可用免费转会数。
"""
from brain.market import to_float
from brain.strategy import FreeTransferProvider


class HistoryFreeTransferProvider(FreeTransferProvider):
    """无鉴权时的备用推导：由 entry history 的 event_transfers 累计免费转会数。

    每轮 free = min(max_free_transfers, 1 + carried)；carried = max(0, free - used)。
    只统计已完结轮次（event < current_gw），进行中/未开始轮次不计入。
    Phase 3 切鉴权 API（my-team.transfers.limit）为主来源。
    """

    def __init__(self, entry_history: dict, max_free_transfers: int, current_gw: int = None):
        rows = entry_history.get("current", [])
        if current_gw is not None:
            rows = [r for r in rows if int(r.get("event", 0)) < current_gw]
        self.rows = sorted(rows, key=lambda r: r.get("event", 0))
        self.max_free = max(0, int(max_free_transfers))

    def get_free_transfers(self, entry_id=None):
        carried = 0
        for row in self.rows:
            free = min(self.max_free, 1 + carried)
            used = max(0, int(row.get("event_transfers", 0)))
            carried = max(0, free - used)
        return min(self.max_free, 1 + carried)


def resolve_transfer_status(entry_history: dict, current_gw: int, max_free_transfers: int,
                            establish: bool = False) -> dict:
    """确定当前账号转会状态。

    返回 {"status": "unlimited"|"limited", "free_transfers": int|None}。
    - 新账号（establish 或没有任何已完结轮次转会历史）→ unlimited，free_transfers=None；
      不用大整数冒充无限。
    - 普通账号 → limited，free_transfers 由 event_transfers 推导。
    """
    if establish:
        return {"status": "unlimited", "free_transfers": None}
    rows = entry_history.get("current", []) if isinstance(entry_history, dict) else []
    completed = [r for r in rows if int(r.get("event", 0)) < current_gw]
    if not completed:
        # 没有任何已完结轮次的转会历史 → 新号初期（例如 GW3 才创建账号）
        return {"status": "unlimited", "free_transfers": None}
    provider = HistoryFreeTransferProvider(entry_history, max_free_transfers, current_gw)
    return {"status": "limited", "free_transfers": provider.get_free_transfers()}


def _summary(player: dict) -> dict:
    """建议条目的 out/in 摘要：id/name/pos/price/market_score。"""
    return {
        "id": player.get("id"),
        "name": player.get("name", "?"),
        "pos": player.get("pos", "?"),
        "price": player.get("price", 0.0),
        "market_score": round(to_float(player.get("market_score")), 2),
    }


def evaluate_transfers(squad, players_map, market_scores, cfg, bank, transfer_status):
    """返回 (suggestions, notes)。

    transfer_status: {"status": "unlimited"|"limited", "free_transfers": int|None}。
    unlimited（新账号）时不受免费转会数量限制，但仍遵守位置/预算/同队约束，
    且只产生建议；limited 时最多建议 free_transfers 笔。
    suggestions: [{out, in, market_gap, reason}]，按 Market Gap 降序；
    notes: [{topic, player, detail}]（写 history 的结构化理由）。
    """
    suggestions = []
    notes = []
    unlimited = transfer_status.get("status") == "unlimited"
    free = transfer_status.get("free_transfers")
    if not unlimited and (free is None or free <= 0):
        notes.append({"topic": "no_transfer", "detail": "免费转会数为 0，本轮不进行转会"})
        return suggestions, notes
    limit = None if unlimited else int(free)

    threshold = to_float(cfg.get("market_gap_threshold"), 15.0)
    injury_threshold = to_float(cfg.get("injury_threshold"), 75.0)
    max_per_team = int(cfg.get("max_players_per_team", 3))
    bank = to_float(bank)

    squad_ids = {p["id"] for p in squad}
    team_counts = {}
    for p in squad:
        team_counts[p["team"]] = team_counts.get(p["team"], 0) + 1

    ranked = sorted(market_scores.items(), key=lambda kv: (-to_float(kv[1]), kv[0]))

    candidates = []
    for p in squad:
        pos = p.get("pos")
        best_in = None
        for pid, score in ranked:
            info = players_map.get(pid)
            if not info or info.get("pos") != pos or pid in squad_ids:
                continue
            if team_counts.get(info.get("team"), 0) >= max_per_team:
                continue
            if bank + to_float(p.get("price")) < to_float(info.get("price")) - 1e-9:
                continue
            best_in = (pid, info, to_float(score))
            break

        p_score = to_float(p.get("market_score"))
        reason = None
        if best_in and best_in[2] - p_score > threshold:
            reason = "同位置 Market Score 差距超过阈值"
        chance = p.get("chance_of_playing_next_round")
        if chance is not None and to_float(chance) < injury_threshold:
            reason = f"出场概率不足（{chance}% < {injury_threshold:.0f}%）"
        if p.get("status") not in (None, "", "a"):
            reason = f"状态 {p['status']} 非 available"

        if not reason:
            continue
        if best_in is None:
            notes.append({
                "topic": "no_transfer",
                "player": p.get("name", "?"),
                "detail": "未找到满足位置/预算/同队约束的替代者",
            })
            continue
        pid, info, in_score = best_in
        out_entry = dict(p)
        out_entry["market_score"] = p_score
        in_entry = {**info, "id": pid, "market_score": in_score}
        candidates.append((round(in_score - p_score, 2), out_entry, in_entry, reason))

    candidates.sort(key=lambda c: -c[0])
    taken = set()
    for gap, out_p, in_p, reason in candidates:
        if limit is not None and len(suggestions) >= limit:
            break
        if in_p["id"] in taken:
            continue
        taken.add(in_p["id"])
        suggestions.append({
            "out": _summary(out_p),
            "in": _summary(in_p),
            "market_gap": gap,
            "reason": reason,
        })
        notes.append({"topic": "transfer_out", "player": out_p.get("name", "?"), "detail": reason})
        notes.append({
            "topic": "transfer_in",
            "player": in_p.get("name", "?"),
            "detail": f"同位置 Market Score 最高者（{in_score:.2f}），满足预算与同队约束",
        })

    if not suggestions and not notes:
        notes.append({"topic": "no_transfer", "detail": "市场共识未明显转向，全员健康，不进行转会"})
    return suggestions, notes
