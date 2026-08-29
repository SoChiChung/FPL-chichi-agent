"""未来策略模块的空接口（Phase 1+）。

仅保留类与 TODO 说明，不包含任何业务逻辑。
"""


class DecisionEngine:
    """TODO(Phase 1): 完整决策管线 —— 首发阵容 / 阵型 / 队长 / 副队长 / 转会建议。"""


class TransferStrategy:
    """TODO(Phase 1): 转会建议策略（结合 Ownership / 赛程 / 未来积分投影）。"""


class CaptainStrategy:
    """TODO(Phase 1): 队长 / 副队长选择策略。"""


class Executor:
    """TODO(Phase 3): 自动登录 FPL，提交转会 / 阵容 / 队长（含安全门与审计）。"""
