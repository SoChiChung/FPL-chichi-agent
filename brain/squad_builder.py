"""15 人阵容构建（仅当前阵容为空时触发，如新号首轮）。

两阶段贪心：
  阶段 1：各位置按 Market Score 降序取配额（GKP×2/DEF×5/MID×5/FWD×3），
          跳过违反「同队 ≤ 3」的球员；
  阶段 2：总价超预算时循环降级——把 Market Score 最低的球员换成「同位置、
          未入选、同队未超限、且价格更低」的 Market Score 最高者；
          循环结束仍超预算则接受当前最优并写 warning。
"""
from brain.market import to_float


def _team_entry(pid: int, info: dict, market_score: float) -> dict:
    return {
        "id": pid,
        "name": info.get("name", "?"),
        "pos": info.get("pos", "?"),
        "team": info.get("team", "?"),
        "price": info.get("price", 0.0),
        "selected_by": info.get("selected_by", 0.0),
        "selected_by_percent": info.get("selected_by_percent", 0.0),
        "form": info.get("form", "0"),
        "total_points": info.get("total_points", 0),
        "status": info.get("status", ""),
        "chance_of_playing_next_round": info.get("chance_of_playing_next_round"),
        "transfers_in_event": info.get("transfers_in_event", 0),
        "transfers_out_event": info.get("transfers_out_event", 0),
        "starting": False,
        "multiplier": 1,
        "is_captain": False,
        "is_vice_captain": False,
        "market_score": round(to_float(market_score), 2),
    }


def build_squad(players_map: dict, market_scores: dict, cfg: dict):
    """返回 (squad, warnings)。squad 为 15 名球员条目（含 market_score）。"""
    quota = cfg.get("position_quota") or {}
    budget = to_float(cfg.get("budget"), 100.0)
    max_per_team = int(cfg.get("max_players_per_team", 3))

    def score(pid: int) -> float:
        return to_float(market_scores.get(pid))

    ranked = sorted(players_map.items(), key=lambda kv: (-score(kv[0]), kv[0]))

    team = []
    team_counts = {}
    for pos, need in quota.items():
        picked = 0
        for pid, info in ranked:
            if info.get("pos") != pos:
                continue
            if team_counts.get(info.get("team"), 0) >= max_per_team:
                continue
            team.append(_team_entry(pid, info, score(pid)))
            team_counts[info.get("team")] = team_counts.get(info.get("team"), 0) + 1
            picked += 1
            if picked >= need:
                break

    warnings = []
    if len(team) < int(cfg.get("squad_size", 15)):
        warnings.append(f"可构建球员不足（{len(team)}/{cfg.get('squad_size')}），请检查数据")

    # 阶段 2：预算降级
    while sum(p["price"] for p in team) > budget + 1e-9:
        in_squad = {p["id"] for p in team}
        swapped = False
        for out_p in sorted(team, key=lambda p: (p["market_score"], p["id"])):
            best_in = None
            for pid, info in ranked:
                if info.get("pos") != out_p["pos"] or pid in in_squad:
                    continue
                if team_counts.get(info.get("team"), 0) >= max_per_team:
                    continue
                if to_float(info.get("price")) >= out_p["price"] - 1e-9:
                    continue
                best_in = (pid, info)
                break
            if best_in is None:
                continue
            pid, info = best_in
            team_counts[out_p["team"]] -= 1
            team_counts[info.get("team")] = team_counts.get(info.get("team"), 0) + 1
            team.remove(out_p)
            team.append(_team_entry(pid, info, score(pid)))
            swapped = True
            break
        if not swapped:
            warnings.append(
                f"预算降级无法满足 budget={budget}，接受当前阵容（总价 {sum(p['price'] for p in team):.1f}）"
            )
            break
    return team, warnings
