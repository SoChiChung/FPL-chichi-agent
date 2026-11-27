"""Phase 2 队长/副队长选择（docs/decide.md §3.6 / §5，v1.1）。

模块只读：从已打分 squad 行计算 Captain Score（单轮爆发视角）并选 C/V。
Captain 候选域仅首发 11（保证队长一定上场）；并列取 element_id 小者。

公式（v1.1 评审修订后，权重进配置）：
  Attack Potential  = 0.70×Projection + 0.20×Form + 0.10×Streak（lineup_score 已算）
  MID  Captain Score = 0.90×Attack Potential + 0.10×Market
  FWD  Captain Score = 0.95×Attack Potential + 0.05×Market
  DEF  Captain Score = 0.20×Attack Potential + 0.50×CleanSheet + 0.30×Fixture
  GKP  Captain Score = 0.50×CleanSheet + 0.50×Market

与本轮价值（Lineup Score）独立：队长找爆发点，首发找期望总分最高。
"""
from brain.market import to_float
from brain.strategy import CaptainSelector


def _weighted(breakdown: dict, weights: dict, extra: dict) -> float:
    total = 0.0
    for key, weight in (weights or {}).items():
        value = breakdown.get(key) if key in breakdown else extra.get(key)
        total += to_float(value) * to_float(weight)
    return round(total, 2)


def captain_scores(starting_xi: list, cfg: dict):
    """计算首发 11 的 captain_score 并挂回球员行（含 score_breakdown 依据）。

    cfg: strategy_config.get_lineup_engine() 结果。
    当球员具备真实单轮个人预测分（projected_points）时，将团队特征分与个人真实
    xP + 爆发上限（xGI 份额 / 点球权 / EO 保护）+ 出场率安全阀融合，避免纯球队级
    CleanSheet+Fixture 极值让后卫长期垄断队长袖标，同时对纯合成单测保持 100% 兼容。
    """
    captain_w = cfg.get("captain_weights") or {}
    for p in starting_xi:
        breakdown = p.setdefault("score_breakdown", {})
        extra = {"market": to_float(p.get("market_score"))}
        raw_score = _weighted(breakdown, captain_w.get(p.get("pos")) or {}, extra)
        if p.get("projected_points") is None:
            p["captain_score"] = raw_score
            continue

        pos = p.get("pos")
        xp = max(0.0, to_float(p.get("projected_points")))
        xgi_share = max(0.0, to_float(p.get("xgi_share")))
        pen_bonus = 4.0 if p.get("penalties_order") == 1 else 0.0
        tsb_eo = min(8.0, 0.08 * to_float(p.get("selected_by_percent", p.get("selected_by"))))
        # Positional explosive ceiling: attackers have multi-goal + bonus ceiling;
        # defenders mostly cap out at 1 CS unless they also carry elite xGI.
        pos_ceiling = {"FWD": 1.08, "MID": 1.05, "DEF": 0.80, "GKP": 0.65}.get(pos, 1.0)
        xp_component = min(100.0, xp * 12.0 * pos_ceiling + 22.0 * xgi_share + pen_bonus + tsb_eo)

        blended = (0.30 * raw_score * pos_ceiling) + (0.70 * xp_component)

        # Penalize rotation/injury risk heavily for captaincy (1-pt cameo is disastrous)
        start_prob = p.get("starting_probability")
        if start_prob is not None and to_float(start_prob) < 0.80:
            blended *= max(0.25, to_float(start_prob))
        chance = p.get("chance_of_playing_next_round")
        if chance is not None and to_float(chance) < 100:
            blended *= max(0.0, to_float(chance) / 100.0)
        if str(p.get("status") or "").lower() in ("i", "s", "u", "n"):
            blended = 0.0

        p["captain_score"] = round(min(100.0, max(0.0, blended)), 2)
    return starting_xi


def choose_captain(starting_xi: list):
    """队长最高分；副队长优先选不同球队的最高分球员，降低延期赛风险。"""
    ordered = sorted(
        starting_xi, key=lambda p: (-to_float(p.get("captain_score")), p.get("id", 0)))
    captain = ordered[0] if ordered else None
    vice = next((p for p in ordered[1:] if p.get("team") != (captain or {}).get("team")), None)
    if vice is None and len(ordered) > 1:
        vice = ordered[1]
    return captain, vice


class Phase2CaptainSelector(CaptainSelector):
    """新引擎队长选择器（保持 CaptainSelector 接口，供编排替换）。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def get_captain_score(self, player):
        return to_float(player.get("captain_score"))

    def select(self, starting_xi: list):
        captain_scores(starting_xi, self.cfg)
        return choose_captain(starting_xi)
