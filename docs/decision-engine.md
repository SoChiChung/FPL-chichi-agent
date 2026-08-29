# FPL AI Manager — 决策层设计（Phase 1: Market Consensus Strategy）

> 版本：v2.0　日期：2026-08-29
> 整体架构见 [architecture.md](architecture.md)，路线图见 [roadmap.md](roadmap.md)。

---

## 0. 设计原则

| # | 原则 | 含义 |
|---|------|------|
| P1 | 不要预测市场，直接跟随市场 | 市场大多数时候是正确的，复制市场共识即可 |
| P2 | 不做高级模型 | 无 xG / xA / EV / Monte Carlo / ML / 复杂优化器 |
| P3 | 评分 = 市场共识 | TSB + Transfer Trend + 未来其他市场指标 |
| P4 | 配置驱动 | 所有权重、阈值、GW 分段、阵型列表都在 `config/strategy.json`，代码禁止硬编码 |
| P5 | 决策必留痕 | 每轮决策 + 理由 + 指标写入 `data/history.json` |

---

## 1. 策略概述

**Strategy Name**：`Market Consensus Strategy`

AI 的职责只有一个：**找到市场当前最认可的球员，复制市场的选择**。它不做未来预测、不做提前布局、不做任何"我认为"的判断。

**为什么叫 Market Consensus 而非 Crowd Following**：评分来源不只是跟风（TSB），而是市场共识的组合：

```
Market Score = TSB × tsb_weight
             + Transfer Trend × trend_weight
             + （未来）其他市场指标
```

### 1.1 评分

- **基础**：`Player Score = TSB%`（GW1-10 默认权重下 TSB 占 80%，Market Score ≈ TSB）
- **正式**：`Market Score = TSB% × tsb_weight + trend_score × trend_weight`（见第 3 节）
- 本文档统一使用 **Market Score** 作为所有决策的评分输入。

---

## 2. 模块结构

### 2.1 brain/ 规划

| 文件 | 状态 | 职责 |
|------|------|------|
| `brain/__main__.py` | 骨架 | 编排完整管线（Phase 1 接线） |
| `brain/api.py` | 已实现 | FPL API 客户端（字段见 [api-notes.md](api-notes.md)） |
| `brain/config.py` | 已实现 | 代码级常量（TEAM_ID/SEASON/路径），唯一入口 |
| `brain/data_store.py` | 已实现 | JSON 原子读写 + 校验 |
| `brain/context.py` | 已实现 | GW 上下文构建（Phase 1 扩展：健康/预算/免费转会） |
| `brain/strategy.py` | 骨架 | **决策接口骨架**（当前版本，仅接口与 TODO） |
| `brain/strategy_config.py` | Phase 1 | strategy.json 加载器 + 按 GW 取权重分段 |
| `brain/market.py` | Phase 1 | Market Score 计算（TSB + 趋势归一化 + 权重分段） |
| `brain/squad_builder.py` | Phase 1 | 15 人阵容构建 |
| `brain/lineup.py` | Phase 1 | 阵型 + 首发 XI + 替补排序 |
| `brain/captain.py` | Phase 1 | 队长 / 副队长（实现 `CaptainSelector` 接口） |
| `brain/transfer.py` | Phase 1 | 转会决策（Market Gap + 伤病触发） |
| `brain/history_writer.py` | Phase 1 | history 条目 upsert + metrics 写入 |

### 2.2 决策管线（Phase 1）

```
fetch (bootstrap + entry + history + picks)
   ↓
strategy_config.load()                # 读 strategy.json，按当前 GW 取权重
   ↓
context：当前15人阵容 / 健康 / 银行 / 免费转会数
   ↓
market.market_score()                 # 全体球员评分
   ↓
squad 为空? ──是──► squad_builder.build_squad()   # 新号首次全量构建
   │否
   ↓
lineup：阵型 + 首发 XI + 替补排序
   ↓
captain：队长 / 副队长（CaptainSelector.get_captain_score）
   ↓
transfer：Market Gap + 伤病检查 → 转会建议（仅免费转会）
   ↓
history_writer.upsert_decision()      # decision + notes + metrics
state.json 更新
   ↓
（GW 结束后结算阶段 backfill points/rank）
```

---

## 3. 市场评分（Market Score）

### 3.1 公式

```
net_transfer(player) = transfers_in_event − transfers_out_event

trend_score(player)  = min-max 归一化(net_transfer) × 100     # 全体球员中归一化到 0-100
                         = (net − min_net) / (max_net − min_net) × 100
                         若 max_net == min_net → trend_score = 50

Market Score = TSB% × tsb_weight + trend_score × trend_weight
```

- 归一化原因：TSB 是百分比（0-100），net_transfer 是绝对值（可能上千），必须映射到同一量纲才能加权求和。
- 负净转会 → trend_score 低；正净转会 → 高。

### 3.2 权重分段（Season Progression）

| 阶段 | GW | TSB 权重 | Trend 权重 | 原因 |
|------|----|----------|------------|------|
| 赛季前段 | 1–10 | 0.8 | 0.2 | 玩家活跃，TSB 可信 |
| 赛季中段 | 11–20 | 0.6 | 0.4 | 僵尸队出现，转会热度渐重要 |
| 赛季后段 | 21+ | 0.3 | 0.7 | 僵尸队占比高，以活跃玩家行为为准 |

权重值来自 `config/strategy.json.weights`，**禁止硬编码 GW 分段与权重**；修改 JSON 即可调整。

---

## 4. 配置设计（`config/strategy.json`）

```json
{
  "strategy": "market_consensus",

  "allow_hits": false,
  "injury_threshold": 75,
  "market_gap_threshold": 15,
  "max_free_transfers": 5,

  "budget": 100.0,
  "squad_size": 15,
  "max_players_per_team": 3,
  "position_quota": { "GKP": 2, "DEF": 5, "MID": 5, "FWD": 3 },

  "formations": ["343", "352", "442", "433", "451", "541", "532"],

  "weights": {
    "gw1_10":    { "tsb": 0.8, "trend": 0.2 },
    "gw11_20":   { "tsb": 0.6, "trend": 0.4 },
    "gw21_plus": { "tsb": 0.3, "trend": 0.7 }
  }
}
```

**加载规则（strategy_config.py）**：

- 文件缺失或字段缺失 → 使用内置默认值（与上表一致），日志 warning。
- `get_weights(gw)`：`gw ≤ 10` → `gw1_10`；`11 ≤ gw ≤ 20` → `gw11_20`；`gw ≥ 21` → `gw21_plus`。
- 校验 `tsb + trend ≈ 1`（warning，不中断）。
- 权重变化无需改代码：编辑 JSON → push → Actions 自动重跑。

**所有策略参数（权重 / 阈值 / GW 分段 / 阵型列表）必须来自此文件**，代码中禁止出现硬编码值。

---

## 5. 阵容构建（Squad Builder）

**触发时机**：当前阵容为空时（新号首轮）。已有阵容时**不重建**，由转会模块做最小调整。

### 5.1 约束（全部来自配置）

```
- 15 人：GKP×2 + DEF×5 + MID×5 + FWD×3
- 总价 ≤ budget（100.0 £m）
- 同队球员 ≤ 3
```

### 5.2 算法：两阶段贪心

**阶段 1 — 位置配额贪心**：每个位置内按 Market Score 降序取配额人数，跳过违反「同队≤3」的球员。

**阶段 2 — 预算降级**（总价超预算时）：

```
循环（直到总价 ≤ 预算 或 无可替换）：
  1. 取当前阵容中 Market Score 最低的球员 X
  2. 在 X 的位置组内，找「未入选、同队≤3、且替换后总价下降」的 score 最高球员 Y
  3. 找到 → X 换 Y；找不到 → 尝试评分次低的 X'，重复
```

**行为含义**：预算紧张时自动放弃"最不热门的球员"，保住市场最认可的球员。

**极端情况**：循环结束仍超预算 → 接受当前最优，写 warning 进日志，不中断。

---

## 6. 首发与阵型（Lineup Selector）

### 6.1 阵型池（配置驱动）

```
formations = ["343", "352", "442", "433", "451", "541", "532"]
```

### 6.2 阵型选择

```
对每个阵型 f：
  首发 = GKP 取 1（队内 score 最高 GK）
         + 该阵型对应数量的 DEF / MID / FWD（各取 score 前 N）
  总分 = 首发 11 人 Market Score 之和
选择总分最高的阵型；并列时取 formations 配置顺序靠前的（默认 343 优先）
```

### 6.3 首发与替补

```
首发 XI   = 胜出阵型对应的 11 人（每位置 score 降序排列）
替补 4 人 = 15 人中未首发者，按 Market Score 降序
           （分数高的替补优先自动替补，符合跟随市场）
```

**合法性说明**：15 人合法（2 GK）⇒ 首发必含 1 GK，替补必含 1 GK；七种阵型均 ≥3 DEF，满足 FPL 阵型规则。

---

## 7. 队长选择（Captain Selector）

### 7.1 接口预留（xP 兼容）

```python
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
```

### 7.2 选择规则

```
Captain = 首发 XI 中 get_captain_score 最高的球员
Vice    = 首发 XI 中 get_captain_score 第二高的球员
```

**说明**：从首发中取可保证队长不会坐替补席（FPL 规则错误）；分数相同取 element_id 小者，保证确定性。

---

## 8. 转会策略（Transfer Selector）

### 8.1 原则

**不主动优化、不预测未来、不做提前布局**，但**持续跟随市场共识**：市场共识明显转向其他球员时，AI 跟着转向。

### 8.2 触发条件（满足任一即进入转会评估）

**A. Market Gap Trigger**（跟随市场共识的核心机制）：

```
Market Gap = 同位置最佳 Market Score − 当前持有球员 Market Score

触发条件：Gap > market_gap_threshold（默认 15）  且  有免费转会
```

示例：Bruno（MID）Market Score=50，Palmer（MID）=80 → Gap=30 > 15 → 触发转会评估。

**B. 伤病 / 存疑**：

| 条件 | 字段 | 说明 |
|------|------|------|
| `chance_of_playing_next_round < injury_threshold` | `chance_of_playing_next_round` | `None` 视为健康 |
| `status != available` | `status` | 映射：`a`=available、`i`=injured、`d`=doubtful、`u`=unavailable、`n`=not in squad |

### 8.3 替代者选择（Transfer In）

```
候选 = 全市场球员 ∩ 满足：
  - 同位置（GKP/DEF/MID/FWD）
  - 不在当前阵容
  - 同队 ≤ 3（转入后不超限）
  - 价格可承担：bank + out.now_cost ≥ in.now_cost     # v1 简化，见第 11 节
取其中 Market Score 最高的球员为替代者
找不到 → 本轮不转会该球员，记入 notes
```

### 8.4 免费转会（FreeTransferProvider）

```python
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
```

备用推导公式（仅作 fallback 参考）：

```
carried = 0
for 每个已完结 GW（按时间顺序）:
    free    = min(max_free_transfers, 1 + carried)
    used    = 该轮 event_transfers
    carried = max(0, free − used)
本轮可用 = min(max_free_transfers, 1 + carried)
```

### 8.5 转会数量与顺序

```
评估对象 = 满足 8.2 任一条件的球员
按「损失」降序处理：损失 = 目标替代者 Market Score − 当前球员 Market Score（越大越该换）
最多执行 min(候选数, 本轮可用免费转会) 笔
allow_hits = false：绝不扣分转会
```

### 8.6 不转会场景（记入 notes）

- 无球员满足触发条件 → "市场共识未明显转向，全员健康，不进行转会"
- 免费转会 = 0 → "免费转会数为 0，不进行转会"
- 有候选但无合适替代者 → "未找到满足位置/预算/同队约束的替代者"

> Phase 1 转会**只产生建议并写入决策日志**，不提交 FPL API（提交属于 Phase 3 Executor）。

---

## 9. 决策日志（history.json）

条目结构见 [architecture.md](architecture.md) 第 5.2 节。要点：

- **幂等**：同一 GW 一个条目，重复运行覆盖 decision/notes/metrics；结算回填 points/rank。
- **metrics 字段**（每次决策写入）：

```json
"metrics": {
  "team_market_score": 812.3,
  "captain_market_score": 68.5,
  "formation_market_score": 512.3
}
```

- **扩展预留**：未来直接增加键即可，无需结构变更：`team_xp`、`captain_xp`、`market_rank`（Phase 2+）。

### notes 主题清单

| topic | 触发场景 |
|-------|----------|
| `squad_built` | 新号首次构建 15 人 |
| `formation` | 阵型选择结果与次优对比 |
| `captain` | 队长 / 副队长选择 |
| `transfer_out` | 转出原因（Market Gap / 伤病 / 存疑） |
| `transfer_in` | 转入理由（同位置最高分 / 预算 / 同队） |
| `no_transfer` | 无触发 / 免费转会为 0 / 无合适替代者 |
| `warning` | 极端情况（预算无法满足、数据异常） |

---

## 10. 数据来源（字段映射）

全部来自公开端点，无需新增 API 调用（详见 [api-notes.md](api-notes.md)）：

| 需要 | 来源字段 | 端点 |
|------|----------|------|
| TSB% | `elements[].selected_by_percent` | `/bootstrap-static/` |
| 本周转进 / 转出 | `elements[].transfers_in_event / transfers_out_event` | 同上 |
| 健康状态 | `elements[].chance_of_playing_next_round` | 同上 |
| 状态 | `elements[].status` | 同上 |
| 价格 | `elements[].now_cost` | 同上 |
| 位置 / 球队 | `elements[].element_type / team` | 同上 |
| 当前阵容 | `/entry/{id}/event/{gw}/picks/` | 公开 |
| 免费转会（备用） | `history.current[].event_transfers` | `/entry/{id}/history/` |
| 免费转会（主） | `my-team.transfers.limit`（需鉴权） | Phase 3 |

---

## 11. 边界情况与限制（如实说明）

1. **出售价简化**：v1 用 `now_cost` 作为转出价计算可承担性；FPL 实际出售价 = 买入价的一半（四舍五入）。**Phase 2 修正**：track 买入价。
2. **免费转会数据时效**：备用推导依赖 `event_transfers`（GW 结束后才稳定），deadline 前决策可能用到上一轮数据——误差最多 ±1 次，可接受；Phase 3 切主来源后消除。
3. **`chance_of_playing` 为 None**：视为健康。
4. **僵尸队**：TSB 赛季后期失真 → 权重分段自动降权。
5. **同 GW 重复运行**：幂等覆盖，不产生重复条目。
6. **评分并列**：取 element_id 较小者，保证确定性。
7. **Market Gap 误伤**：`market_gap_threshold` 太低会导致频繁转会（每轮消耗免费转会）；通过配置调整，不写死。

---

## 12. 验收标准

| # | 验收项 | 判定 |
|---|--------|------|
| 1 | 15 人阵容合法 | GKP×2/DEF×5/MID×5/FWD×3、预算 ≤100、同队 ≤3 |
| 2 | 阵容跟随市场 | 构建结果 = 全市场按 Market Score 排序的约束最优 |
| 3 | 阵型正确 | 首发总分最高（七种阵型池），替补按分降序 |
| 4 | 队长正确 | Captain/Vice = 首发中 `get_captain_score` 第一/第二 |
| 5 | 转会符合规则 | 仅 Market Gap / 伤病触发、仅免费转会、无 hit、替代者同位置且可承担 |
| 6 | 参数全配置化 | 修改 strategy.json（权重/阈值/阵型/分段）→ 行为随之变化，无需改代码 |
| 7 | 决策日志完整 | 每轮 history 含 decision + notes + metrics；结算回填 points/rank |
| 8 | 幂等 | 同一 GW 重复运行不产生重复条目、不重复建议转会 |
| 9 | 无违规依赖 | 未引入高级模型 / 自动执行 / 任何 FPL 账号写操作 |

---

## 13. 实现顺序建议

1. `strategy_config.py`（加载 + 分段）→ 2. `market.py`（评分）→ 3. `squad_builder.py` → 4. `lineup.py` → 5. `captain.py`（实现 CaptainSelector）→ 6. `transfer.py`（Market Gap + FreeTransferProvider fallback）→ 7. `history_writer.py`（decision + metrics）→ 8. `__main__.py` 编排 → 9. 本地实测 → 10. 前端适配
