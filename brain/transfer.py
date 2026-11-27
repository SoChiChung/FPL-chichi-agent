"""转会建议（Transfer Package Search v2.5，只产生建议，绝不执行任何 FPL 写操作）。

升级要点：
  - 真实卖出价校验：优先使用 FPL selling_price（涨价球员仅回收一半涨幅），
    满足 Σ(in.price) ≤ bank + Σ(out.selling_price)；并对高增值锁死资产施加轻微换回损耗惩罚。
  - 解锁「一降一升（Downgrade + Upgrade）」两步联动搜索：允许将替补门将/冗余高价轮换降级为
    底价可用球员，以释放预算升级首发核心，整包按未来 5 轮首发 11 人 + 队长预期分全局寻优。
  - 换入池质量过滤：在真实预测模式下硬过滤伤停/红黄牌与低首发率轮换死钱（纯 £4.0m 腾预算槽位除外）。
  - 免费转会滚存门槛（FT Rollover Hurdle）：全员健康且未达攒卡上限时，单笔微弱正收益自动选择攒卡。
  - 扣分转会双周期门槛：除 5 轮净增达标外，还要求前 2 轮追回足够短期积分。
  - Wildcard / Free Hit 轮次保留已攒免费转会额度（适配 2024/25–2026/27 官方新规）。
"""
from collections import Counter

from brain.market import to_float
from brain.strategy import FreeTransferProvider

PACKAGE_OUT_CANDIDATES_CAP = 11
PACKAGE_IN_CANDIDATES_CAP = 5
EPS = 1e-9
POSITIONS = ("GKP", "DEF", "MID", "FWD")

_PRESERVE_FT_CHIPS = {"wildcard", "wc", "freehit", "free_hit", "fh"}


class HistoryFreeTransferProvider(FreeTransferProvider):
    """无鉴权时的备用推导：由 entry history 的 event_transfers 与 chips 累计免费转会数。"""

    def __init__(self, entry_history: dict, max_free_transfers: int, current_gw: int = None):
        rows = (entry_history or {}).get("current", [])
        if current_gw is not None:
            rows = [r for r in rows if int(r.get("event", 0)) < current_gw]
        self.rows = sorted(rows, key=lambda r: r.get("event", 0))
        self.max_free = max(0, int(max_free_transfers))
        self.chip_events = {
            int(to_float(c.get("event")))
            for c in ((entry_history or {}).get("chips") or [])
            if str(c.get("name") or "").strip().lower().replace("-", "_").replace(" ", "_")
            in _PRESERVE_FT_CHIPS
        }

    def get_free_transfers(self, entry_id=None):
        carried = 0
        for index, row in enumerate(self.rows):
            ev = int(row.get("event", 0))
            if index == 0:
                carried = 0
                continue
            if ev in self.chip_events:
                # 2024/25–2026/27 rule: WC / FH preserves previously banked FTs
                # without accruing an extra +1 during the chip week itself.
                continue
            free = min(self.max_free, 1 + carried)
            used = max(0, int(row.get("event_transfers", 0)))
            carried = max(0, free - used)
        return min(self.max_free, 1 + carried)


def resolve_transfer_status(entry_history: dict, current_gw: int, max_free_transfers: int,
                            establish: bool = False) -> dict:
    """确定当前账号转会状态。"""
    if establish:
        return {"status": "unlimited", "free_transfers": None}
    rows = entry_history.get("current", []) if isinstance(entry_history, dict) else []
    completed = [r for r in rows if int(r.get("event", 0)) < current_gw]
    if not completed:
        return {"status": "unlimited", "free_transfers": None}
    provider = HistoryFreeTransferProvider(entry_history, max_free_transfers, current_gw)
    return {"status": "limited", "free_transfers": provider.get_free_transfers()}


def _money(x) -> float:
    return round(to_float(x), 1)


def _sell_price(player: dict) -> float:
    """FPL 真实可回收售价：优先取 picks.selling_price，缺失时回退现价 price。"""
    if player.get("selling_price") is not None:
        return _money(player.get("selling_price"))
    return _money(player.get("price"))


def _summary(player: dict, use_sell_price: bool = False) -> dict:
    """建议条目的 out/in 摘要：id/name/pos/price/market_score/5GW points。"""
    effective_price = _sell_price(player) if use_sell_price else _money(player.get("price"))
    summary = {
        "id": player.get("id"),
        "name": player.get("name", "?"),
        "pos": player.get("pos", "?"),
        "price": effective_price,
        "market_score": round(to_float(player.get("market_score")), 2),
    }
    if use_sell_price and player.get("price") is not None:
        now_price = _money(player.get("price"))
        if abs(now_price - effective_price) > EPS:
            summary["now_price"] = now_price
    if player.get("five_gw_points") is not None:
        summary["five_gw_points"] = round(to_float(player.get("five_gw_points")), 2)
    return summary


def _xi_projection(squad):
    """Best legal FPL XI by projected points, including captain's extra multiplier."""
    by_pos = {pos: [p for p in squad if p.get("pos") == pos] for pos in POSITIONS}
    if len(by_pos["GKP"]) < 1:
        return None
    best = None
    for d in range(3, 6):
        for m in range(2, 6):
            for f in range(1, 4):
                if d + m + f != 10 or len(by_pos["DEF"]) < d or len(by_pos["MID"]) < m or len(by_pos["FWD"]) < f:
                    continue
                picked = [sorted(by_pos[pos], key=lambda p: -to_float(p.get("horizon_points", [0])[0] if p.get("horizon_points") else 0))[:n]
                          for pos, n in (("DEF", d), ("MID", m), ("FWD", f))]
                gk = sorted(by_pos["GKP"], key=lambda p: -to_float(p.get("horizon_points", [0])[0] if p.get("horizon_points") else 0))[:1]
                xi = gk + sum(picked, [])
                score = sum(to_float(p.get("horizon_points", [0])[0] if p.get("horizon_points") else 0) for p in xi)
                key = (score, tuple(-int(p.get("id") or 0) for p in xi))
                if best is None or key > best[0]:
                    best = (key, xi)
    if best is None:
        return None
    xi = best[1]
    captain = max(xi, key=lambda p: to_float(p.get("horizon_points", [0])[0] if p.get("horizon_points") else 0))
    return {"points": round(best[0][0] + to_float(captain.get("horizon_points", [0])[0] if captain.get("horizon_points") else 0), 2),
            "xi": [p.get("id") for p in xi], "captain": captain.get("id")}


def project_squad_horizon(squad):
    """Project legal starting XI + captain over each event in the horizon."""
    gws = next((p.get("projection_gws") for p in squad if p.get("projection_gws")), [])
    rounds = []
    for i, gw in enumerate(gws):
        indexed = []
        for p in squad:
            copy = dict(p)
            values = p.get("horizon_points") or []
            copy["horizon_points"] = [values[i] if i < len(values) else 0]
            indexed.append(copy)
        row = _xi_projection(indexed)
        if row is None:
            return None
        rounds.append({"gw": gw, **row})
    return {"rounds": rounds, "points": round(sum(r["points"] for r in rounds), 2)} if rounds else None


def _sell_reason(player: dict, injury_threshold: float):
    """返回 (reason, forced)。forced=True 表示伤病/状态等必须优先处理的换出。"""
    chance = player.get("chance_of_playing_next_round")
    if chance is not None and to_float(chance) < injury_threshold:
        return f"出场概率不足（{chance}% < {injury_threshold:.0f}%）", True
    if player.get("status") not in (None, "", "a"):
        return f"状态 {player['status']} 非 available", True
    return None, False


def _is_viable_buy_candidate(info: dict, min_start_prob: float, allow_enabler: bool = False) -> bool:
    """Filter out injured, suspended, or rotation deadweight from real-mode buy pools."""
    status = str(info.get("status") or "").lower()
    if status not in ("", "a"):
        return False
    chance = info.get("chance_of_playing_next_round")
    if chance is not None and to_float(chance) < 100:
        return False
    start_prob = info.get("starting_probability")
    if start_prob is None:
        return True
    if to_float(start_prob) >= min_start_prob:
        return True
    if allow_enabler:
        pos = info.get("pos")
        price = _money(info.get("price"))
        if (pos == "GKP" and price <= 4.1) or (pos == "DEF" and price <= 4.0):
            return True
    return False


def _select_diverse_candidates(raw_cands: list, out_sell_price: float, bank: float,
                               cap: int = PACKAGE_IN_CANDIDATES_CAP) -> list:
    """Keep both absolute top scorers and best affordable / mid-tier value picks."""
    if len(raw_cands) <= cap:
        return raw_cands
    chosen = []
    seen = set()

    def _add(items, count):
        for item in items:
            if len(chosen) >= cap:
                break
            if item[1] not in seen:
                seen.add(item[1])
                chosen.append(item)
                count -= 1
                if count <= 0:
                    break

    # 1) Top 2 absolute highest gain (for multi-transfer upgrades)
    _add(raw_cands, 2)
    # 2) Top 2 directly affordable with current bank + out_sell_price
    affordable = [c for c in raw_cands if _money(c[2].get("price")) <= out_sell_price + bank + EPS]
    _add(affordable, 2)
    # 3) Top 1 moderate upgrade within +£1.8m (reachable via minor 2nd swap)
    mid_reach = [c for c in raw_cands if _money(c[2].get("price")) <= out_sell_price + bank + 1.8 + EPS]
    _add(mid_reach, 1)
    # Fill remaining slots by raw gain
    _add(raw_cands, cap - len(chosen))
    chosen.sort(key=lambda c: (-c[3], c[1]))
    return chosen


def _best_package(outs: list, limit: int, bank: float, max_per_team: int,
                  squad_team_count: Counter, with_budget: bool, free_limit=None,
                  base_squad=None, base_projection=None, gain_multiplier=1.0,
                  minimum_hit_return_multiple=2.0, min_hit_short_term_gain=2.5,
                  value_lock_penalty_weight=1.5):
    """DFS 搜索预计首发积分净收益最高的转会包（含一降一升组合与双周期扣分门槛）。"""
    n = len(outs)
    suffix_gain = [0.0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_gain[i] = suffix_gain[i + 1] + max(0.0, outs[i]["best_gain"])

    best = None
    hit_threshold_blocked = False

    def _validate(chosen):
        nonlocal hit_threshold_blocked
        if not chosen:
            return None
        in_sum = round(sum(_money(p["in"].get("price")) for p in chosen), 1)
        out_sum = round(sum(_sell_price(p["out"]) for p in chosen), 1)
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
        before = base_projection if base_squad is not None else None
        after_squad = (
            [p for p in base_squad if p.get("id") not in {x["out"].get("id") for x in chosen}]
            if base_squad is not None else None
        )
        if after_squad is not None:
            after_squad.extend({**pair["in"], "id": pair["pid"]} for pair in chosen)
        after = project_squad_horizon(after_squad) if after_squad is not None else None
        if before and after:
            gain = round(after["points"] - before["points"], 2)

        hits = max(0, len(chosen) - (free_limit if free_limit is not None else limit))

        # Penalize selling players with substantial locked-in price rises (only in 5-GW xP mode)
        value_lock_loss = 0.0
        if before and after and value_lock_penalty_weight > 0:
            for p in chosen:
                locked = max(0.0, _money(p["out"].get("price")) - _sell_price(p["out"]))
                if locked >= 0.2 - EPS and not p["forced"]:
                    value_lock_loss += locked * value_lock_penalty_weight

        calibrated_gain = gain * gain_multiplier - value_lock_loss
        if hits:
            if calibrated_gain + EPS < 4 * minimum_hit_return_multiple * hits:
                hit_threshold_blocked = True
                return None
            # Dual-horizon check: hits must also pay back meaningfully in the first 2 GWs
            if before and after and min_hit_short_term_gain > 0:
                b_rounds = before.get("rounds") or []
                a_rounds = after.get("rounds") or []
                if len(b_rounds) >= 2 and len(a_rounds) >= 2:
                    two_gw_gain = (
                        sum(to_float(r.get("points")) for r in a_rounds[:2])
                        - sum(to_float(r.get("points")) for r in b_rounds[:2])
                    ) * gain_multiplier
                    if two_gw_gain + EPS < hits * min_hit_short_term_gain:
                        hit_threshold_blocked = True
                        return None

        net_gain = round(calibrated_gain - hits * 4, 2)
        return {"chosen": list(chosen), "covered": covered, "gain": gain,
                "hits": hits, "net_gain": net_gain,
                "in_sum": in_sum, "out_sum": out_sum,
                "projection_before": before, "projection_after": after}

    def dfs(i, chosen, taken_in, covered, gain):
        nonlocal best
        if chosen:
            leaf = _validate(chosen)
            if leaf is not None:
                # Prefer higher net_gain, fewer transfers on ties, and lower spend
                key = (leaf["net_gain"], leaf["covered"], -len(chosen), leaf["gain"], -leaf["in_sum"])
                if best is None or key > best[0]:
                    best = (key, leaf)
        if i >= n or len(chosen) >= limit:
            return
        if best is not None and base_squad is None:
            ceil_gain = gain + suffix_gain[i]
            if ceil_gain <= best[0][3]:
                return
        out_info = outs[i]
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
        dfs(i + 1, chosen, taken_in, covered, gain)

    dfs(0, [], set(), 0, 0.0)
    if (best is None or not best[1]["chosen"]) and hit_threshold_blocked:
        return {"chosen": [], "threshold_blocked": True}
    return best[1] if best else None


def evaluate_transfers(squad, players_map, market_scores, cfg, bank, transfer_status,
                       gain_multiplier=1.0, evaluation_meta=None):
    """返回 (suggestions, notes)。"""
    suggestions = []
    notes = []
    unlimited = transfer_status.get("status") == "unlimited"
    free = transfer_status.get("free_transfers")
    if not unlimited and (free is None or free <= 0) and not cfg.get("allow_hits", False):
        notes.append({"topic": "no_transfer", "detail": "免费转会数为 0，本轮不进行转会"})
        return suggestions, notes
    max_free_cfg = int(cfg.get("max_free_transfers", 5))
    if unlimited:
        limit = max(1, min(max_free_cfg, len(squad)))
        free_limit = limit
    else:
        free_limit = max(0, int(free or 0))
        limit = free_limit + (int(cfg.get("max_hit_transfers", 2)) if cfg.get("allow_hits") else 0)
        limit = max(1, limit)

    threshold = to_float(cfg.get("market_gap_threshold"), 15.0)
    injury_threshold = to_float(cfg.get("injury_threshold"), 75.0)
    max_per_team = max(1, int(cfg.get("max_players_per_team", 3)))
    min_hit_return_multiple = max(1.0, to_float(cfg.get("minimum_hit_return_multiple"), 2.0))
    min_hit_short_term_gain = to_float(cfg.get("min_hit_short_term_gain"), 2.5)
    min_free_gain = to_float(cfg.get("min_free_transfer_gain"), 3.5)
    value_lock_weight = to_float(cfg.get("value_lock_penalty_weight"), 1.5)
    min_buy_start_prob = to_float(cfg.get("min_buy_start_probability"), 0.70)
    bank = _money(bank)

    squad_ids = {p["id"] for p in squad}
    squad_team_count = Counter(str(p.get("team")) for p in squad)

    base_projection = project_squad_horizon(squad)
    has_five_gw = bool(base_projection) or any(
        to_float(p.get("five_gw_points")) > 0 for p in squad
    )
    if evaluation_meta is not None and base_projection:
        evaluation_meta.update({"projection_before": base_projection,
                                "projection_after": base_projection,
                                "predicted_gain": 0.0,
                                "gain_multiplier": gain_multiplier,
                                "calibrated_gain": 0.0,
                                "hit_cost": 0})

    # 构建换入候选池（真实 5GW 模式下过滤伤停与低首发率死钱）
    ranked = sorted(market_scores.items(), key=lambda kv: (-to_float(kv[1]), kv[0]))
    pools = {}
    enabler_pools = {}
    for pid, ms in ranked:
        info = players_map.get(pid)
        if not info or pid in squad_ids:
            continue
        pos = info.get("pos")
        if pos not in POSITIONS:
            continue
        if has_five_gw:
            if _is_viable_buy_candidate(info, min_buy_start_prob, allow_enabler=False):
                pools.setdefault(pos, []).append((pid, info, to_float(ms)))
            elif _is_viable_buy_candidate(info, min_buy_start_prob, allow_enabler=True):
                enabler_pools.setdefault(pos, []).append((pid, info, to_float(ms)))
        else:
            pools.setdefault(pos, []).append((pid, info, to_float(ms)))

    gk_total_cost = sum(_sell_price(p) for p in squad if p.get("pos") == "GKP")

    # ---- 1) 生成候选 out 及其换入候选（含一降一升预算腾挪腿） ----
    outs = []
    for p in squad:
        pos = p.get("pos")
        if pos not in POSITIONS:
            continue
        p_ms = to_float(p.get("market_score"))
        p_sell = _sell_price(p)
        reason, forced = _sell_reason(p, injury_threshold)
        raw_cands = []
        for pid, info, in_ms in pools.get(pos, []):
            out_projection = to_float(p.get("five_gw_points"))
            in_projection = to_float(info.get("five_gw_points"))
            gain = (round(in_projection - out_projection, 2)
                    if (out_projection or in_projection)
                    else round(in_ms - p_ms, 2))
            if gain <= 0:
                continue
            raw_cands.append((in_ms, pid, info, gain))

        raw_cands.sort(key=lambda c: (-c[3], c[1]))
        cands = _select_diverse_candidates(raw_cands, p_sell, bank, PACKAGE_IN_CANDIDATES_CAP)

        # 若允许 2+ 笔转会（且在真实 5GW xP 模式下），为高价替补门将/非核心槽位加入 1 名低价腾预算候选
        can_downgrade = (
            has_five_gw and limit >= 2 and p_sell >= 4.7 and (
                (pos == "GKP" and gk_total_cost >= 9.0)
                or not p.get("starting", True)
                or (p.get("starting_probability") is not None
                    and to_float(p.get("starting_probability")) < 0.70)
            )
        )
        if can_downgrade:
            cheap_pool = sorted(
                list(pools.get(pos, [])) + list(enabler_pools.get(pos, [])),
                key=lambda row: (_money(row[1].get("price")), -to_float(row[1].get("five_gw_points")), row[0]),
            )
            for pid, info, in_ms in cheap_pool:
                if _money(info.get("price")) <= p_sell - 0.7 + EPS:
                    if all(c[1] != pid for c in cands):
                        gain = round(to_float(info.get("five_gw_points")) - to_float(p.get("five_gw_points")), 2)
                        cands.append((in_ms, pid, info, gain))
                    break

        if not cands:
            if reason:
                notes.append({"topic": "no_transfer", "player": p.get("name", "?"),
                              "detail": f"{reason}，但未找到更优的同位置健康替代者，暂不换出"})
            continue

        best_gain = max(c[3] for c in cands)
        if not reason:
            has_projection = bool(p.get("five_gw_points") or any(
                c[2].get("five_gw_points") for c in cands))
            if (not has_projection and best_gain <= threshold + EPS
                    and not (cfg.get("allow_hits") and free_limit == 0)):
                continue
            if best_gain > 0:
                reason = (f"未来 5 轮预计增加 {best_gain:.2f} 分" if has_projection else
                          f"同位置 Market Score 差距超过阈值（最优替代 +{best_gain:.2f}）")
            else:
                reason = f"释放 £{p_sell:.1f}m 冗余资金以升级首发核心"
        outs.append({"player": p, "reason": reason, "forced": bool(forced),
                     "best_gain": best_gain, "cands": cands})

    if not outs:
        if not notes:
            notes.append({"topic": "no_transfer",
                          "detail": "市场共识未明显转向，全员健康，不进行转会"})
        return suggestions, notes

    outs.sort(key=lambda o: (not o["forced"], -o["best_gain"], o["player"].get("id", 0)))
    outs = outs[:PACKAGE_OUT_CANDIDATES_CAP]

    # ---- 2) 包搜索（含真实售价预算校验 + 双周期扣分校验）----
    best = _best_package(outs, limit, bank, max_per_team, squad_team_count,
                         with_budget=True, free_limit=free if not unlimited else limit,
                         base_squad=squad if base_projection else None,
                         base_projection=base_projection,
                         gain_multiplier=gain_multiplier,
                         minimum_hit_return_multiple=min_hit_return_multiple,
                         min_hit_short_term_gain=min_hit_short_term_gain,
                         value_lock_penalty_weight=value_lock_weight)
    if best and best.get("threshold_blocked"):
        notes.append({"topic": "no_transfer", "detail":
                      f"扣分转会门槛未达到：每笔 hit 扣 4 分，要求未来 5 轮至少增 "
                      f"{4 * min_hit_return_multiple:.0f} 分且前 2 轮至少追回 "
                      f"{min_hit_short_term_gain:.1f} 分"})
        return suggestions, notes
    if best is None or not best["chosen"]:
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

    if best["net_gain"] <= EPS:
        notes.append({"topic": "no_transfer", "detail":
                      f"按首发 11 人及队长计算，未来 5 轮预计净增 {best['net_gain']:.2f} 分（已扣除 {best['hits'] * 4} 分 hit），不值得转会"})
        return suggestions, notes

    # 免费转会滚存门槛（仅在真实 5GW xP 模式、无强制伤停换人、无扣分且未满转会额度上限时启用）
    if (base_projection and not unlimited and best["hits"] == 0 and best["covered"] == 0):
        effective_hurdle = 1.2 if free_limit >= max(1, max_free_cfg - 1) else min_free_gain
        if best["net_gain"] + EPS < effective_hurdle:
            notes.append({
                "topic": "no_transfer",
                "detail": (f"当前首发健康，最优转会未来 5 轮仅净增 {best['net_gain']:.2f} 分"
                           f"（低于攒卡门槛 {effective_hurdle:.1f} 分），建议保留免费转会滚存至下轮联动发力"),
            })
            return suggestions, notes

    if evaluation_meta is not None:
        evaluation_meta.update({"projection_before": best.get("projection_before"),
                                "projection_after": best.get("projection_after"),
                                "predicted_gain": best["gain"],
                                "gain_multiplier": gain_multiplier,
                                "calibrated_gain": round(best["gain"] * gain_multiplier, 2),
                                "hit_cost": best["hits"] * 4,
                                "minimum_hit_return_multiple": min_hit_return_multiple})
    # ---- 3) 输出最优包 ----
    for pair in best["chosen"]:
        out_p, pid = pair["out"], pair["pid"]
        in_score = pair["in_score"]
        info = pair["in"]
        out_entry = dict(out_p)
        out_entry["market_score"] = to_float(out_p.get("market_score"))
        in_entry = {**info, "id": pid, "market_score": in_score}
        pair_gain = round(pair["gain"], 2)
        forced_reason, is_forced = _sell_reason(out_p, injury_threshold)
        if is_forced and forced_reason:
            reason = forced_reason
        elif pair_gain > 0:
            reason = (f"未来 5 轮预计增加 {pair_gain:.2f} 分"
                      if (out_p.get("five_gw_points") is not None or info.get("five_gw_points") is not None)
                      else f"同位置 Market Score 提升 +{pair_gain:.2f}")
        else:
            saved = round(_sell_price(out_p) - _money(info.get("price")), 1)
            reason = f"联动腾出 £{saved:.1f}m 预算以升级首发核心"
        suggestions.append({
            "out": _summary(out_entry, use_sell_price=True),
            "in": _summary(in_entry, use_sell_price=False),
            "market_gap": pair_gain,
            "hit_cost": 4 if best["hits"] and len(suggestions) >= int(free or 0) else 0,
            "reason": reason,
        })
        notes.append({"topic": "transfer_out",
                      "player": out_p.get("name", "?"),
                      "detail": reason})
        notes.append({
            "topic": "transfer_in",
            "player": info.get("name", "?"),
            "detail": f"未来 5 轮个人预估 {to_float(info.get('five_gw_points')):.1f} 分（Market Score {in_score:.2f}），满足真实售价预算与同队约束"
                      if info.get("five_gw_points") is not None else
                      f"未来 5 轮预估收益更高（Market Score {in_score:.2f}），满足预算与同队约束",
        })
    if best.get("hits"):
        notes.append({"topic": "transfer_hit", "detail":
                      f"未来 5 轮预计净增 {best['net_gain']:.2f} 分，扣除 {best['hits']} 笔 hit（{best['hits'] * 4} 分）后仍值得转会"})
    return suggestions, notes


def summarize_package(bank, suggestions: list, evaluation_meta=None) -> dict:
    """由建议列表汇总转会包的真实预算口径（state/history 落盘用）。"""
    bank = _money(bank)
    meta = evaluation_meta or {}
    out_sum = round(sum(_sell_price(s["out"]) for s in suggestions), 1)
    in_sum = round(sum(_money(s["in"].get("price")) for s in suggestions), 1)
    gain = round(to_float(meta.get("predicted_gain", sum(
        to_float(s.get("market_gap")) for s in suggestions))), 2)
    five_gw_before = round(sum(to_float(s.get("out", {}).get("five_gw_points"))
                               for s in suggestions), 2)
    five_gw_after = round(sum(to_float(s.get("in", {}).get("five_gw_points"))
                              for s in suggestions), 2)
    hit_cost = sum(to_float(s.get("hit_cost")) for s in suggestions)
    cost = round(in_sum - out_sum, 1)
    before = meta.get("projection_before") or {}
    after = meta.get("projection_after") or {}
    projected_rounds = before.get("rounds") or after.get("rounds") or []
    round_count = len(projected_rounds)
    average_before = before.get("points", five_gw_before) / round_count if round_count else None
    average_after = after.get("points", five_gw_after) / round_count if round_count else None
    return {
        "gain": gain,
        "five_gw_before": before.get("points", five_gw_before),
        "five_gw_after": after.get("points", five_gw_after),
        "five_gw_net_gain": round(to_float(meta.get("calibrated_gain", five_gw_after - five_gw_before)) - hit_cost, 2),
        "per_gw_average_before": round(average_before, 2) if average_before is not None else None,
        "per_gw_average_after": round(average_after, 2) if average_after is not None else None,
        "per_gw_average_net_gain": round((to_float(meta.get("calibrated_gain", five_gw_after - five_gw_before)) - hit_cost) / round_count, 2) if round_count else None,
        "hit_cost": hit_cost,
        "projection_before": before,
        "projection_after": after,
        "gain_multiplier": meta.get("gain_multiplier", 1.0),
        "minimum_hit_return_multiple": meta.get("minimum_hit_return_multiple", 2.0),
        "calibration_samples": 0,
        "projection_method": "个人级 xP v2 模型：融合球员 xG/xA 队内份额、点球/定位球角色、2026/27 防守贡献与出场概率曲线，不使用官方 ep_next",
        "out_count": len(suggestions),
        "total_out_price": out_sum,
        "total_in_price": in_sum,
        "budget_before": bank,
        "transfer_cost": cost,
        "budget_after": round(bank - cost, 1),
        "feasible": True,
        "point_model_version": 2,
        "score_base": "fpl_starting_xi_points_with_captain",
    }
