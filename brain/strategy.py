"""决策接口骨架（Market Consensus Strategy）。

仅保留接口与 TODO，不包含任何业务逻辑。
所有策略参数来自 config/strategy.json，禁止在代码中写死
权重 / 阈值 / GW 分段 / 阵型列表。
"""


class MarketConsensusStrategy:
    """TODO(Phase 1): 决策编排 —— 基于 Market Score 的阵容/阵型/队长/转会决策管线。"""


class TransferStrategy:
    """TODO(Phase 1): 转会策略。

    触发条件（满足任一即评估转会）：
      - Market Gap 触发：同位置最佳 Market Score − 持有者 Market Score
        > market_gap_threshold（配置项）
      - 伤病/存疑：chance_of_playing < injury_threshold 或 status != available
    仅使用免费转会（allow_hits = false），免费数来自 FreeTransferProvider。
    """


class CaptainSelector:
    """队长选择器。

    当前版本：Captain Score = Market Score
    未来版本：Captain Score = xP（Expected Points）
    未来接入 xP API 后，只需替换 get_captain_score 的实现。
    """

    def get_captain_score(self, player):
        """返回队长评分。

        当前实现：返回 player 的 market_score。
        TODO(未来): 返回 xP（Expected Points），由 xP API 提供。
        """
        raise NotImplementedError("Phase 1 实现")


class FreeTransferProvider:
    """免费转会数提供者。

    优先来源：FPL API（鉴权端点 my-team.transfers.limit，Phase 3 可用）。
    无鉴权时的备用方案：由 entry history 的 event_transfers 累计推导。
    禁止把免费转会逻辑写死。
    """

    def get_free_transfers(self, entry_id):
        """返回当前可用免费转会数。

        优先调用 FPL API；API 不可用时回退备用推导。
        TODO(Phase 3): 切换为鉴权 API 主来源。
        """
        raise NotImplementedError("Phase 1 实现")


class OwnershipStrategy:
    """TODO(Phase 2): Ownership Strategy v2 / EO / Differential。"""


class ChipStrategy:
    """TODO(Phase 2): Wildcard / Free Hit / Bench Boost / Triple Captain。"""


class Executor:
    """TODO(Phase 3): 自动登录 + 提交转会/阵容/队长（含安全门与审计）。

    本阶段不实现任何 FPL 账号写操作（登录 / 转会 / 阵容 / 队长提交）。
    """
