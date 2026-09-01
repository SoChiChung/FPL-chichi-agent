"""市场共识评分：Market Score = TSB% × tsb_weight + trend_score × trend_weight。

trend_score 为全体球员净转会（transfers_in_event − transfers_out_event）的
min-max 归一化到 0-100；全市场净转会相同时全部取 50。
所有结果保留 2 位小数；None / 字符串数字 / 异常数据统一安全转换。
"""


def to_float(value, default=0.0) -> float:
    """None / 字符串数字 / 异常数据 → float；无法解析时返回 default。"""
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_trend_scores(players: dict) -> dict:
    """全体球员净转会 min-max 归一化。

    players: {element_id: 球员信息} → {element_id: trend_score(0-100, 2 位小数)}
    """
    nets = {
        pid: to_float(p.get("transfers_in_event")) - to_float(p.get("transfers_out_event"))
        for pid, p in players.items()
    }
    if not nets:
        return {}
    values = list(nets.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        return {pid: 50.0 for pid in nets}
    span = hi - lo
    return {pid: round((net - lo) / span * 100, 2) for pid, net in nets.items()}


def compute_market_scores(players: dict, weights: dict) -> dict:
    """全体球员 Market Score（2 位小数）。

    players: {element_id: 球员信息}；weights: {"tsb": 0.8, "trend": 0.2}
    """
    trend = compute_trend_scores(players)
    tsb_w = to_float(weights.get("tsb"), 0.8)
    trend_w = to_float(weights.get("trend"), 0.2)
    scores = {}
    for pid, p in players.items():
        tsb = to_float(p.get("selected_by_percent"))
        scores[pid] = round(tsb * tsb_w + trend.get(pid, 50.0) * trend_w, 2)
    return scores
