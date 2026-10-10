# FPL AI Manager 决策说明

> 当前实现说明。策略主要是规则与评分模型，不是机器学习模型；它根据 FPL 官方数据及当前配置生成建议，不会自动更改 FPL 队伍。

## 1. 这份文档说明什么

每次引擎运行会针对下一场尚未截止的 Gameweek（目标 GW）更新一份建议，主要包括：

- 15 人阵容（仅初次建立账号且没有历史 picks 时构建）；
- 本轮首发、阵型、替补、队长与副队长；
- 免费转会建议；
- Wildcard、Free Hit、Bench Boost、Triple Captain 的当前建议、未来排期与半程保底；
- 决策理由和评分快照，写入 `data/state.json` 与 `data/history.json`。

策略阈值的主要配置在 `config/strategy.json`。更新入口是 `brain/__main__.py`，主要算法分别在 `market.py`、`lineup_score.py`、`captain_score.py`、`transfer.py` 与 `chips.py`。

## 2. 每轮决策流程

```text
读取 FPL bootstrap / fixtures / entry / history / picks
  → 解析目标 GW、筹码状态、预算与阵容
  → 给球员计算 Market Score 与本轮 Lineup Score
  → 枚举合法首发，选择总分最高的阵型
  → 从首发中选择 Captain / Vice
  → 根据伤病、市场分差与免费转会数生成转会包
  → 根据赛程与可用卡生成 Chip 建议、排期和保底状态
  → 写入 state.json / history.json，供前端展示
```

输入数据以 FPL 官方公开数据为主；球队级预测与赛程难度还会使用 FPL Joe 缓存数据。外部预测源失败时，系统按评分模块降级规则继续计算，并记录数据来源/告警。

## 3. 球员市场评分

```text
Market Score = selected_by_percent × TSB 权重
             + 净转会趋势分 × Trend 权重
净转会 = transfers_in_event − transfers_out_event
趋势分 = 净转会在全体球员中的 min-max 归一化（0–100）
```

若全体球员净转会相同，趋势分统一取 50。权重随赛季进度变化，当前默认值：GW1–10 为 TSB/Trend=0.8/0.2，GW11–20 为 0.6/0.4，GW21+ 为 0.3/0.7。该分数用于建队与转会比较，代表市场共识，不等于球员真实预期得分。

## 4. 初始阵容构建

只有在找不到当前或历史 FPL picks 时，才会按 Market Score 从全池建立 15 人阵容。位置配额为 GKP×2、DEF×5、MID×5、FWD×3；还会受总预算 £100m 和单队最多 3 人限制。预算超限时，将低市场分球员换成同位置、更便宜且可行的球员。已有阵容时不重建，由转会模块做有限调整。

## 5. 本轮评分、首发与队长

### 5.1 Lineup Score

球员本轮分由位置专属权重组合而成。特征尽量归一化到 0–100：Projection（球队预测得分）、Clean Sheet（零封预测）、Fixture（赛程难度，越容易分越高）、Form（官方 form 联盟归一分）、Streak（近 3 个已结束 GW 是否达到位置得分门槛）、Attack 与 Attack Potential（以上攻击特征的组合）。

默认 `Attack = 0.60×Projection + 0.25×Form + 0.15×Streak`；`Attack Potential = 0.70×Projection + 0.20×Form + 0.10×Streak`。精确位置权重见 `lineup_engine.lineup_weights`。数据缺失时按中性或缺项降级规则处理，因子与结果存于球员的 `score_breakdown` 和 `lineup_score`。

### 5.2 首发与阵型

枚举当前 15 人中所有 11 人组合，过滤合法阵容（1 门将、至少 3 后卫、至少 2 中场、至少 1 前锋），选择 Lineup Score 总和最高者。并列按球员 ID 确定性决胜。剩余 4 人按分数排序，门将放最后以符合替补换人规则。

### 5.3 Captain / Vice

队长候选只限首发 11 人。各位置按 `lineup_engine.captain_weights` 计算 Captain Score，最高为队长、第二高为副队长，并列按 ID 决胜。当前 MID/FWD 更看重 Attack Potential，DEF/GKP 也考虑零封与赛程因素。

这是特征代理评分，不是外部 xP，也未显式建模出场分钟概率或对手影响。Triple Captain 触发要求队长球队当轮有双赛，且 Captain Score≥70。

## 6. 转会建议

候选只在同位置间匹配，并检查价格预算、单队最多 3 人、每轮可用转会数。健康球员只有在同位置最佳替代者的 Market Score 高出超过 `market_gap_threshold`（默认 15）时才触发。伤病/非 available 状态可强制进入候选；出场概率低于 `injury_threshold`（默认 75%）也会触发处理。最终通过包级搜索最大化强制伤病覆盖与总分增益，并校验卖出回款后买得起。

当前不建议扣分 hit。免费转会数由 entry history 的 event_transfers 推导；新账号缺少历史时视作暂不受限，再由配置上限控制建议规模。

## 7. Chip 策略

2026/27 官方规则为上下半赛季各一套 Wildcard、Free Hit、Bench Boost、Triple Captain；上半程须在 GW19 截止前使用，未用卡不会带入下半程；每个 GW 最多用一张。程序优先用 bootstrap chip 时间窗判断当前半程筹码是否已用，缺少对应规则时回退到 entry history `chips`。

### 7.1 当前开卡条件

| Chip | 当前规则 |
|---|---|
| **Wildcard** | 阵容至少有 5 名伤停/不可用/出场概率低于 50% 的球员时触发。成为本轮建议后，会生成后续 FH/TC/BB 排期。 |
| **Free Hit** | 本轮至少 7 名现有球员属于空赛或 FPL fixture difficulty≥4 的困难赛程时，建议临时换阵冲上限。每人最多计一次。 |
| **Triple Captain** | 队长球队当轮有 2 场或以上比赛，且 Captain Score≥70。 |
| **Bench Boost** | 四名替补的 Lineup Score 经伤停折扣后合计≥145。伤停/不可用折扣为 0.35，存疑球员折算为 0.75。 |

若本轮已经记录使用任何筹码，不会再建议同轮开第二张。多张同时满足时按配置 `priority` 排序，默认 Wildcard→Free Hit→Triple Captain→Bench Boost。

### 7.2 Wildcard 后续排期

Wildcard 成为本轮建议时，计划器扫描本半程剩余已公布赛程：

1. Free Hit 优先匹配至少 7 人空赛/困难赛程轮；否则等待窗口。
2. Bench Boost 与 Triple Captain 优先匹配不同的双赛轮次，避免两张卡排在同一 GW。
3. 已公布窗口不足时，按 Wildcard lead 和后续间隔生成暂定轮次，并标注待复核。

该排期用当前阵容和当前评分近似评估已公布赛程。Wildcard 会改变阵容，后续每轮重跑会根据新阵容重算；暂定轮不是确定预测。

### 7.3 半程保底机制

为避免 GW19 仍有上半程筹码未使用，当前设两道触发：

- **必须排程**：剩余可用筹码数大于或等于本半程截止前剩余 GW 数时，进入 `must_schedule` 模式。
- **临近截止预警**：到 `chip_guarantee_start_gw`（上半程默认 GW16，下半程默认 GW35）起，只要仍有可用卡就启动 deadline guard。

若正常规则没有建议，保底按 `guarantee_priority` 选一张本半程仍可用的卡作为兜底建议，并在页面预警。默认顺序 Wildcard→Free Hit→Triple Captain→Bench Boost，可在配置中调整。不会违反每轮最多一张，也不会重复建议本轮已经使用的卡。

## 8. 配置和落盘

`config/strategy.json` 的 `chip_strategy` 管理开卡阈值、评估窗口、后续间隔、半程预警轮和保底优先级；`lineup_engine` 管理阵容/队长评分；顶层字段管理预算、伤病阈值、Market Gap 和转会数。

- `state.decision.recommended_chip`：当前建议卡，无建议时为 null；
- `state.decision.chip_plan`：筹码可用状态、半程、候选轮、窗口、评分信号及保底状态；
- `history[].decision`：每轮决策快照；`history[].notes`：用户可读理由；`history[].metrics`：评分指标。

## 9. 如何读本轮结果

1. 查看本轮建议中的队长、转会和 Chip 主建议。
2. 检查理由，区分正常阈值触发与保底模式触发。
3. 查看其他筹码的候选 GW 和理由。
4. 已公布赛程支持的为 fixture candidate；按间隔暂排的为 provisional，赛程更新后需要复核。
5. 若有 data_missing 或 score_source 提示，谨慎解读受影响的分数。

所有输出都只是建议，必须由用户自行到 FPL 网站确认和提交。FPL 2026/27 [官方条款](https://fantasy.premierleague.com/help/terms)第 27 条要求密码保密且不得允许他人访问账户；第 28(d) 条禁止自动化系统访问游戏并提取信息。因此本项目不保存账户密码/令牌，也不自动调用账户写接口。

## 10. 模型限制

- 不是 ML、xP 或 Monte Carlo 模型，不保证实际得分。
- Fixture difficulty 是 FPL 难度等级，不是概率预测。
- Free Hit 的七人条件识别阵容整体上限偏低的情况；当前没有计算开 Free Hit 相较正常转会的净预期分差。
- Wildcard 后排期基于当前队伍近似评估，开卡改变阵容后会逐轮重算。
- 赛程尚未公布时只能提供暂定计划。
- 不会登录、开卡、转会或提交阵容。
