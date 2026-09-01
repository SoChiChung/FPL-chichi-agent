"""阵型 + 首发 XI + 替补排序。

对每个可用阵型：首发 = 队内 Market Score 最高的 GKP 1 名 + 各位置 score 前 N 名；
选首发总分最高的阵型（并列取 formations 配置顺序靠前的）。
替补 = 15 人中未首发者，按 Market Score 降序（分数高的替补优先自动替补）。
"""
from brain.market import to_float


def _score(player: dict) -> float:
    return to_float(player.get("market_score"))


def select_lineup(team: list, formations: list):
    """返回 (formation, starting_xi, bench)；无法构成任何阵型时返回 ("", [], 全部球员)。

    team: 含 market_score 的球员条目；formations: 如 ["343", "352", ...]。
    """
    team = sorted(team, key=lambda p: (-_score(p), p.get("id", 0)))
    by_pos = {
        "GKP": [p for p in team if p.get("pos") == "GKP"],
        "DEF": [p for p in team if p.get("pos") == "DEF"],
        "MID": [p for p in team if p.get("pos") == "MID"],
        "FWD": [p for p in team if p.get("pos") == "FWD"],
    }

    best = None
    for formation in formations or []:
        if len(formation) != 3:
            continue
        need = {"DEF": int(formation[0]), "MID": int(formation[1]), "FWD": int(formation[2])}
        if not by_pos["GKP"] or any(len(by_pos[pos]) < n for pos, n in need.items()):
            continue
        starters = [by_pos["GKP"][0]]
        for pos, n in need.items():
            starters.extend(by_pos[pos][:n])
        total = sum(_score(p) for p in starters)
        if best is None or total > best[0]:
            best = (total, formation, starters)

    if best is None:
        return "", [], list(team)
    _, formation, starters = best
    start_ids = {p["id"] for p in starters}
    bench = [p for p in team if p["id"] not in start_ids]
    return formation, starters, bench
