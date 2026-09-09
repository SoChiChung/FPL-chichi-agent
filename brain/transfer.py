"""转会建议（Transfer Package Search，只产生建议，绝不执行任何 FPL 写操作）。

触发条件（满足任一即进入评估）：
  - Market Gap：同位置、不在阵容、市场分更高的最佳候选与持有者的
    分差 > market_gap_threshold；
  - 伤病/存疑：chance_of_playing_next_round 非 None 且 < injury_threshold，
    或 status 非 available（forced，优先覆盖）。

设计（v2，修复“局部最优买到买不起”的逻辑错误）：
  - 不再“逐个球员推荐”：改为在一个合法转会包上做全局搜索。
    候选包 = 若干 (out, in) 对；每对同位置替换（保持位置配额不变），
    受免费转会数量、同队 ≤ 3、候选互斥等约束。
  - 包级预算校验（用户验收公式）：
        required_money = Σ(in.price)   ≤   available_money = bank + Σ(out.price)
    预算不足的整包直接丢弃；引擎只输出 100% 真实可执行的方案。
  - 目标函数（字典序）：(已覆盖强制出售人数, 包总 Transfer Gain)，
    Transfer Gain = Σ(in.market_score − out.market_score)。
  - bank 单位：与 state.bank 一致为百万（£m）浮点（如 2.0 = £2.0m），
    与球员 price 同单位。调用方（context.build_state）负责 /10 归一。

只使用免费转会；allow_hits=false 时绝不建议超过可用免费转会数。
"""
from collections import Counter

from brain.market import to_float
from brain.strategy import FreeTransferProvider

# 每个候选 out 保留的换入候选数（按 market_score 降序取前 N，控制搜索规模）
IN_CANDIDATES_CAP = 8
EPS = 1e-9
POSITIONS = ("GKP", "DEF", "MID", "FWD")


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
        "price": round(to_float(player.get("price")), 1),
        "market_score": round(to_float(player.get("market_score")), 2),
    }


def _money(x) -> float:
    return round(to_float(x), 1)


def _sell_reason(player: dict, injury_threshold: float):
    """返回 (reason, forced)。forced=True 表示伤病/状态等必须优先处理的换出。

    无触发理由 → (None, False)。reason 为给人看的中文说明。
    """
    chance = player.get("chance_of_playing_next_round")
    if chance is not None and to_float(chance) < injury_threshold:
        return f"出场概率不足（{chance}% < {injury_threshold:.0f}%）", True
    if player.get("status") not in (None, "", "a"):
        return f"状态 {player['status']} 非 available", True
    return None, False


def _best_package(outs: list, limit: int, bank: float, max_per_team: int,
                  squad_team_count: Counter, with_budget: bool):
    """DFS 搜索字典序最优转会包。

    目标 key = (covered_forced, gain, in_sum)（covered_forced 优先，
    其次总增益，同增益更省钱的包更优）。
    约束：out/in 互斥、同队 ≤ 3、笔数 ≤ limit；with_budget=True 时额外要求
        Σ(in.price) ≤ bank + Σ(out.price)
    （对应 FPL“先卖后买”批量执行：卖出即时回血，总价合法即可真实完成）。

    outs: [{"player", "reason", "forced", "best_gain", "cands"}]，cands 为
    [(in_score, pid, info, gain), ...] 按 in_score 降序。
    返回 {"chosen": [pair...], "covered", "gain", "in_sum", "out_sum"} 或 None。
    pair: {"out": player, "in": info(不含 id), "pid", "gain", "forced", "in_score"}。
    """
    n = len(outs)
    # 乐观后缀和（忽略预算/候选冲突）：用于安全剪枝
    suffix_gain = [0.0] * (n + 1)
    suffix_forced = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_gain[i] = suffix_gain[i + 1] + outs[i]["best_gain"]
        suffix_forced[i] = suffix_forced[i + 1] + (1 if outs[i]["forced"] else 0)

    best = None  # (key, leaf)

    def _validate(chosen):
        """预算 + 同队 ≤ 3 校验（预算只在 with_budget 时强制）。

        同队约束：以「每队 ≤ max_per_team」的合法基线校验最终人数。
        真实 FPL 阵容每队必然 ≤ 3，直接用 squad 计数即可；若数据本身病态
        （合成测试/异常数据初始就超限），先按上限截断基线再校验——既不误伤
        真实场景，也能保留「不换出该队球员就不得换入该队球员」的约束语义。
        """
        in_sum = round(sum(_money(p["in"].get("price")) for p in chosen), 1)
        out_sum = round(sum(_money(p["out"].get("price")) for p in chosen), 1)
        if with_budget and in_sum > bank + out_sum + EPS:
            return None
        team_count = Counter(
            {team: min(count, max_per_team) for team, count in squad_team_count.items()})
        for p in chosen:
            team_count[str(p["out"].get("team"))] -= 1
            team_count[str(p["in"].get("team"))] += 1
        if any(c > max_per_team for c in team_count.values()):
            return None
        covered = sum(1 for p in chosen if p["forced"])
        gain = round(sum(p["gain"] for p in chosen), 2)
        return {"chosen": list(chosen), "covered": covered, "gain": gain,
                "in_sum": in_sum, "out_sum": out_sum}

    def dfs(i, chosen, taken_in, covered, gain):
        nonlocal best
        if i >= n or len(chosen) >= limit:
            leaf = _validate(chosen)
            if leaf is not None:
                key = (leaf["covered"], leaf["gain"], leaf["in_sum"])
                if best is None or key > best[0]:
                    best = (key, leaf)
            return
        if best is not None:
            # 乐观上界仍无法超过已知最优 → 剪枝（不会漏解）
            ceil_cov = covered + suffix_forced[i]
            ceil_gain = gain + suffix_gain[i]
            best_cov, best_gain = best[0][0], best[0][1]
            if ceil_cov < best_cov or (ceil_cov == best_cov and ceil_gain <= best_gain):
                return
        out_info = outs[i]
        # 尝试换入候选（分高者先，先逼近高增益上界）
        for in_score, pid, info, g in out_info["cands"]:
            if pid in taken_in:
                continue
            taken_in.add(pid)
            chosen.append({"out": out_info["player"], "in": info, "pid": pid,
                           "gain": g, "forced": out_info["forced"], "in_score": in_score})
            dfs(i + 1, chosen, taken_in,
                covered + (1 if out_info["forced"] else 0), gain + g)
            chosen.pop()
            taken_in.discard(pid)
        # 跳过该 out
        dfs(i + 1, chosen, taken_in, covered, gain)

    dfs(0, [], set(), 0, 0.0)
    return best[1] if best else None


def evaluate_transfers(squad, players_map, market_scores, cfg, bank, transfer_status):
    """返回 (suggestions, notes)。

    transfer_status: {"status": "unlimited"|"limited", "free_transfers": int|None}。
    unlimited（新账号）时上限按 max_free_transfers 兜底；limited 时最多建议
    free_transfers 笔。输出整包最优解，包内每对同位置替换且预算真实可执行。
    suggestions: [{out, in, market_gap, reason}]；
    notes: [{topic, player, detail}]（写 history 的结构化理由）。
    """
    suggestions = []
    notes = []
    unlimited = transfer_status.get("status") == "unlimited"
    free = transfer_status.get("free_transfers")
    if not unlimited and (free is None or free <= 0):
        notes.append({"topic": "no_transfer", "detail": "免费转会数为 0，本轮不进行转会"})
        return suggestions, notes
    if unlimited:
        # 新账号不限次数，但包搜索仍需一个合理上界（防御），不与免费数冲突
        limit = max(1, min(int(cfg.get("max_free_transfers", 5)), len(squad)))
    else:
        limit = max(1, int(free))

    threshold = to_float(cfg.get("market_gap_threshold"), 15.0)
    injury_threshold = to_float(cfg.get("injury_threshold"), 75.0)
    max_per_team = max(1, int(cfg.get("max_players_per_team", 3)))
    bank = _money(bank)

    squad_ids = {p["id"] for p in squad}
    squad_team_count = Counter(str(p.get("team")) for p in squad)

    # 每位置换入池（不含队内球员），按 market_score 降序
    ranked = sorted(market_scores.items(), key=lambda kv: (-to_float(kv[1]), kv[0]))
    pools = {}
    for pid, ms in ranked:
        info = players_map.get(pid)
        if not info or pid in squad_ids:
            continue
        pos = info.get("pos")
        if pos not in POSITIONS:
            continue
        pools.setdefault(pos, []).append((pid, info, to_float(ms)))

    # ---- 1) 生成候选 out（带触发理由）及其换入候选 ----
    outs = []
    for p in squad:
        pos = p.get("pos")
        if pos not in POSITIONS:
            continue
        p_ms = to_float(p.get("market_score"))
        reason, forced = _sell_reason(p, injury_threshold)
        cands = []
        for pid, info, in_ms in pools.get(pos, []):
            gain = round(in_ms - p_ms, 2)
            if gain <= 0:            # 只保留能提升 squad 分的换入
                continue
            cands.append((in_ms, pid, info, gain))
        if not cands:
            if reason:
                notes.append({"topic": "no_transfer", "player": p.get("name", "?"),
                              "detail": f"{reason}，但未找到市场分更高的同位置替代者，暂不换出"})
            continue
        cands.sort(key=lambda c: (-c[0], c[1]))
        cands = cands[:IN_CANDIDATES_CAP]
        best_gain = cands[0][3]
        if not reason:
            if best_gain <= threshold + EPS:
                continue                    # 最优替代分差未达阈值 → 不触发
            reason = f"同位置 Market Score 差距超过阈值（最优替代 +{best_gain:.2f}）"
        outs.append({"player": p, "reason": reason, "forced": bool(forced),
                     "best_gain": best_gain, "cands": cands})

    if not outs:
        if not notes:
            notes.append({"topic": "no_transfer",
                          "detail": "市场共识未明显转向，全员健康，不进行转会"})
        return suggestions, notes

    # 强制出售（伤病/状态）优先；同类按潜在增益降序，保证确定性
    outs.sort(key=lambda o: (not o["forced"], -o["best_gain"], o["player"].get("id", 0)))

    # ---- 2) 包搜索（含包级预算校验）----
    best = _best_package(outs, limit, bank, max_per_team, squad_team_count,
                         with_budget=True)
    if best is None or not best["chosen"]:
        # 无预算可行的整包 → 生成“差多少钱”的可调试说明，绝不输出买不起的方案
        best_any = _best_package(outs, limit, bank, max_per_team, squad_team_count,
                                 with_budget=False)
        if best_any is not None and best_any["chosen"]:
            short = round(best_any["in_sum"] - (bank + best_any["out_sum"]), 1)
            notes.append({
                "topic": "no_transfer",
                "detail": (f"预算不足：可用 £{bank + best_any['out_sum']:.1f}m"
                           f"（Bank £{bank:.1f}m + 出售 £{best_any['out_sum']:.1f}m），"
                           f"最有利组合需 £{best_any['in_sum']:.1f}m"
                           f"（缺口 £{short:.1f}m），本轮放弃转会"),
            })
        else:
            notes.append({"topic": "no_transfer",
                          "detail": "未能生成满足位置/同队/预算约束的转会组合"})
        return suggestions, notes

    # ---- 3) 输出最优包 ----
    reason_by_out = {o["player"].get("id"): o["reason"] for o in outs}
    for pair in best["chosen"]:
        out_p, pid = pair["out"], pair["pid"]
        in_score = pair["in_score"]
        info = pair["in"]
        out_entry = dict(out_p)
        out_entry["market_score"] = to_float(out_p.get("market_score"))
        in_entry = {**info, "id": pid, "market_score": in_score}
        reason = reason_by_out.get(out_p.get("id"), "")
        suggestions.append({
            "out": _summary(out_entry),
            "in": _summary(in_entry),
            "market_gap": round(pair["gain"], 2),
            "reason": reason,
        })
        notes.append({"topic": "transfer_out",
                      "player": out_p.get("name", "?"),
                      "detail": reason})
        notes.append({
            "topic": "transfer_in",
            "player": info.get("name", "?"),
            "detail": f"同位置市场分最高可用者（{in_score:.2f}），满足预算与同队约束",
        })
    return suggestions, notes


def summarize_package(bank, suggestions: list) -> dict:
    """由建议列表汇总转账包的预算口径（state/history 落盘用）。

    - budget_before：转会前 Bank（£m）
    - transfer_cost：净花费 = Σ(in) − Σ(out)（≥0 表示支出）
    - budget_after：按建议执行后的 Bank = budget_before − transfer_cost
    - gain：包总 Transfer Gain = Σ market_gap
    空建议时全 0 字段，budget_after = budget_before。
    """
    bank = _money(bank)
    out_sum = round(sum(_money(s["out"].get("price")) for s in suggestions), 1)
    in_sum = round(sum(_money(s["in"].get("price")) for s in suggestions), 1)
    gain = round(sum(to_float(s.get("market_gap")) for s in suggestions), 2)
    cost = round(in_sum - out_sum, 1)
    return {
        "gain": gain,
        "out_count": len(suggestions),
        "total_out_price": out_sum,
        "total_in_price": in_sum,
        "budget_before": bank,
        "transfer_cost": cost,
        "budget_after": round(bank - cost, 1),
        "feasible": True,
        "score_base": "market_score",
    }
