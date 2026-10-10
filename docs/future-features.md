# FPL AI Manager — 未来功能与扩展设计

> 更新：2026-08-29。本文档记录明确不做但已预留接口的功能，以及 Phase 3 自动执行模块的完整设计。

## 1. 预留接口（当前仅占位）

`brain/strategy.py` 中保留以下接口，均无业务逻辑：

```python
class CaptainSelector:     # Phase 1 实现：get_captain_score()，当前=Market Score，未来=xP
class FreeTransferProvider:# Phase 1 实现：优先 FPL API（my-team.transfers.limit），备用 event_transfers 推导
class OwnershipStrategy:   # Phase 2：Ownership v2 / EO / Differential
class ChipStrategy:        # Phase 2：Wildcard / Free Hit / Bench Boost / Triple Captain
class Executor:            # Phase 3：自动登录 + 提交转会/阵容/队长（含安全门与审计）
```

## 2. xP（Expected Points）接入规划（Phase 2）

- 目标：`Captain Score = xP`（替换当前 Market Score 实现即可，接口不变）。
- 数据源：外部 xP API（用户自选，届时评估提供方与格式）。
- 影响面：仅 `CaptainSelector.get_captain_score` 一个实现点；history `metrics` 增加 `captain_xp` 等键（结构已预留）。

## 3. Ownership Strategy v2（Phase 2+）

概念设计（届时以独立配置承载，如 `config/ownership-rules.json`）：

- 模式：`high_ownership`（跟随主流）/ `differential`（追求差异）/ `balanced`（均衡）。
- 赛季内切换计划（如中段转 differential 冲排名）。
- EO（Effective Ownership）队长权衡、热球员出售保护线。
- 与 Market Consensus 的关系：Market Score 是"市场共识"，Ownership v2 是"与共识的偏离策略"，两者在 Phase 2 组合使用。

## 4. Chip 策略（Phase 2，已实现）

- 每个半赛季分别读取 Wildcard / Free Hit / Bench Boost / Triple Captain 的可用状态；优先使用 FPL bootstrap 的筹码时间窗，history chips 作为回退。
- Free Hit 用于冲击单轮上限：若至少 7 名现有球员面临空赛或 FPL 困难赛程，建议临时换阵。
- Wildcard 当前建议开出时，会同时排定后续 FH / TC / BB 候选轮；有官方已排赛程时依据双赛/空赛，无明确赛程时提供可调的暂定间隔。
- 半程保底：剩余可用筹码数追平/超过截止前剩余轮数时进入必须排程模式；达到配置的临近截止轮次后，即使没有理想窗口也给出兜底开卡建议，防止筹码过期。
- 按未来赛程、队长评分、替补席评分和伤停人数产出当前轮建议及剩余窗口规划。
- 每轮最多推荐一张筹码；所有阈值、规划范围、优先级来自 `config/strategy.json`。
- 决策写入 `decision.chip_plan` 与 `decision.recommended_chip`，同时在前端本轮建议和历史记录中展示。

## 5. FPL 操作提交（不实现）

FPL 现行 [2026/27 条款](https://fantasy.premierleague.com/help/terms) 的第 27 条要求密码保密、不得允许他人访问账户；第 28(d) 条禁止使用自动化系统访问游戏并提取信息。由本项目服务器或浏览器按钮登录、读取私人队伍并调用写入接口，会触及这些条款。因此本项目只生成决策建议，不保存账号密码或令牌，也不自动提交转会、阵容或 Chip。

账号持有人可以在 FPL 官方网站/App 查看 AI 建议，再自行手动确认操作。若 FPL 将来提供官方授权的管理接口和明确许可，再重新评估自动化方案。

## 6. 明确不实现清单

- xG / xA / EV / Monte Carlo / ML / 复杂优化器（当前策略不依赖）
- 自动登录、API 写入和自动提交（见第 5 节官方条款）
- 多账号支持
- 付费 API / 数据服务（除非用户明确引入）
