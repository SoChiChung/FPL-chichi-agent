"""队长 / 副队长选择（实现 CaptainSelector 接口）。

Captain = 首发 XI 中 get_captain_score 最高者；Vice = 次高者。
并列取 element_id 小者，保证确定性。
"""
from brain.market import to_float
from brain.strategy import CaptainSelector


class MarketCaptainSelector(CaptainSelector):
    """当前版本：Captain Score = Market Score；未来接入 xP 后替换 get_captain_score。"""

    def get_captain_score(self, player):
        return to_float(player.get("market_score"))

    def select(self, starting_xi: list):
        """返回 (captain, vice)。"""
        ordered = sorted(starting_xi, key=lambda p: (-self.get_captain_score(p), p.get("id", 0)))
        captain = ordered[0] if ordered else None
        vice = ordered[1] if len(ordered) > 1 else None
        return captain, vice
