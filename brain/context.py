"""GW 上下文构建：把原始 API 数据转换为 state / history 结构。

决策系统 (Phase 1+) 将基于这里产出的 context 工作。
"""
from datetime import datetime, timezone

from brain import config
from brain.market import to_float


def build_player_map(bootstrap: dict) -> dict:
    """element_id -> 球员决策字段（位置/球队/价格/持有率/健康/转会热度）。

    Phase 1 起补充：status、chance_of_playing_next_round、transfers_in_event、
    transfers_out_event、selected_by_percent。原始 API 数据只在内存处理，不落盘。
    """
    team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    pos_short = {
        et["id"]: et["singular_name_short"] for et in bootstrap["element_types"]
    }
    players = {}
    for p in bootstrap["elements"]:
        selected_by = float(p.get("selected_by_percent") or 0)
        players[p["id"]] = {
            "name": p["web_name"],
            "pos": pos_short.get(p["element_type"], "?"),
            "team": team_short.get(p["team"], "?"),
            "price": round(p["now_cost"] / 10, 1),
            "selected_by": selected_by,
            "selected_by_percent": selected_by,
            "form": p.get("form", "0"),
            "total_points": p.get("total_points", 0),
            "status": p.get("status", ""),
            "chance_of_playing_next_round": p.get("chance_of_playing_next_round"),
            "transfers_in_event": p.get("transfers_in_event", 0),
            "transfers_out_event": p.get("transfers_out_event", 0),
        }
    return players


def resolve_current_gw(events: list) -> int:
    """当前 GW = 最近的未开始/进行中的轮次；赛季结束则返回最后一轮。"""
    upcoming = [e for e in events if not e.get("finished")]
    if upcoming:
        return min(e["id"] for e in upcoming)
    finished = [e for e in events if e.get("finished")]
    return max(e["id"] for e in finished) if finished else 1


def _next_deadline(events: list) -> str:
    """下一截止时间 = 第一个尚未到期的轮次；赛季结束则取最后一轮。"""
    upcoming = []
    for e in sorted(events, key=lambda x: x.get("id", 0)):
        dt = e.get("deadline_time", "")
        if not dt:
            continue
        parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if parsed > datetime.now(timezone.utc):
            return dt
        upcoming.append(dt)
    return upcoming[-1] if upcoming else ""


def resolve_target_gw(events: list) -> int:
    """目标 GW（Phase 2 评分针对的轮次）= next_deadline 对应的 event id。

    即第一个 deadline_time 仍在未来的轮次（当前轮进行中也往前看一场）；
    赛季结束（无未来截止）时返回最后一个 finished 轮次作兜底。
    """
    deadline = _next_deadline(events)
    for e in events:
        if e.get("deadline_time") == deadline:
            return e["id"]
    finished = [e for e in events if e.get("finished")]
    return max(e["id"] for e in finished) if finished else 1


def build_formation(team: list) -> str:
    """由首发 11 人按 DEF-MID-FWD 计数得到阵型，如 "343"。"""
    starters = [t for t in team if t.get("starting")]
    if len(starters) != 11:
        return ""
    counts = {}
    for t in starters:
        counts[t["pos"]] = counts.get(t["pos"], 0) + 1
    return f"{counts.get('DEF', 0)}{counts.get('MID', 0)}{counts.get('FWD', 0)}"


def build_state(
    bootstrap: dict, entry: dict, entry_history: dict, picks: dict, gw: int,
    market_scores: dict = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    players = build_player_map(bootstrap)

    team = []
    for pk in sorted(picks.get("picks", []), key=lambda x: x.get("position", 99)):
        info = players.get(pk["element"])
        if not info:
            continue
        player = {
            "id": pk["element"],
            **info,
            "starting": pk.get("position", 99) <= 11,
            "multiplier": pk.get("multiplier", 1),
            "is_captain": bool(pk.get("is_captain")),
            "is_vice_captain": bool(pk.get("is_vice_captain")),
        }
        if market_scores:
            player["market_score"] = market_scores.get(pk["element"], 0.0)
        team.append(player)

    # 银行优先取 picks 接口的实时值（进行中的 GW），否则取最近已完成轮次。
    # 单位说明：FPL API 的 bank 是 0.1m 整数（如 20 = £2.0m），必须 /10 归一为百万
    # 浮点，才能与球员 price（now_cost/10，如 150 → 15.0）同单位做预算比较。
    # 历史上曾直接把 API 原始值写入 state.bank，导致引擎误把 £2.0m 当 £20m，
    # 推荐了根本买不起的球员（如 bank=20 时仍建议买 Haaland）。
    raw_bank = picks.get("entry_history", {}).get("bank")
    if raw_bank is None:
        raw_bank = 0.0
        for row in entry_history.get("current", []):
            if row.get("event") == gw - 1:
                raw_bank = row.get("bank", raw_bank)
    bank = round(to_float(raw_bank) / 10.0, 1)

    return {
        "season": config.SEASON,
        "current_gw": gw,
        "points": entry.get("summary_overall_points") or 0,
        "rank": entry.get("summary_overall_rank") or 0,
        # bank 单位 = 百万（£m），与球员 price 一致；2.0 表示 £2.0m
        "bank": bank,
        "manager_name": (entry.get("name") or "").strip(),
        "formation": build_formation(team),
        "captain": next((t["name"] for t in team if t.get("is_captain")), ""),
        "vice": next((t["name"] for t in team if t.get("is_vice_captain")), ""),
        "next_deadline": _next_deadline(bootstrap["events"]),
        "last_update": now,
        "team": team,
    }


def build_history(bootstrap: dict, entry_history: dict) -> dict:
    """历史记录只收录已完结的轮次（未完结时积分/排名不稳定）。"""
    finished_ids = {e["id"] for e in bootstrap["events"] if e.get("finished")}
    rows = []
    for row in entry_history.get("current", []):
        if row.get("event") in finished_ids:
            rows.append(
                {
                    "gw": row["event"],
                    "points": row.get("points", 0),
                    "rank": row.get("rank", 0),
                    "overall_rank": row.get("overall_rank", 0),
                }
            )
    return {"season": config.SEASON, "history": rows}
