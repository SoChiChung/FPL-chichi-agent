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
    """
    captain_w = cfg.get("captain_weights") or {}
    for p in starting_xi:
        breakdown = p.setdefault("score_breakdown", {})
        extra = {"market": to_float(p.get("market_score"))}
        score = _weighted(breakdown, captain_w.get(p.get("pos")) or {}, extra)
        p["captain_score"] = score
    return starting_xi


def choose_captain(starting_xi: list):
    """返回 (captain, vice)：首发中 Captain Score 最高/次高，并列 element_id 小者。"""
    ordered = sorted(
        starting_xi, key=lambda p: (-to_float(p.get("captain_score")), p.get("id", 0)))
    captain = ordered[0] if ordered else None
    vice = ordered[1] if len(ordered) > 1 else None
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
