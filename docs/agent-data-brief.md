# FPL AI Manager — Agent 逻辑与数据源交接说明

> 版本日期：2026-09-03　用途：供另一个做**数据决策**的 Agent 阅读（只读数据文件即可决策，无需运行本仓库代码）
> 涉及仓库：`SoChiChung/FPL-chichi-agent`（公开）　账号：FPL team_id `10049242`（2026-27 赛季）

---

## 1. 这个 Agent 是什么

一个自主运营 FPL 账号的程序：定时拉取数据 → 按 **Market Consensus（市场共识）** 策略产出一轮建议（阵容 / 首发 / 队长 / 转会）→ 写入 JSON 状态与历史 → 前端静态展示。

**关键边界**：它只做「采集 + 建议 + 留痕」，**不执行任何 FPL 账号写操作**（提交转会 / 阵容 / 队长是未来阶段）。

当前状态（2026-09-03 快照）：赛季 GW3，下个 deadline `2026-09-04T17:30Z`；已建立 15 人阵容（establish，新账号首轮按市场最优生成）；转会状态 `unlimited`（赛季初新号，尚未受限）。

---

## 2. 决策逻辑：Market Consensus（重点）

### 2.1 指导思想

**不预测、不提前布局、不做"我认为"的判断**——只找到"市场当前最认可"的球员并复制市场共识。市场大多数时候是对的。

### 2.2 "市场价值"（Market Value）的定义

本 Agent 的"市场价值"指标叫 **Market Score**（0–100，越大越有价值）：

```
net_transfer(player) = transfers_in_event − transfers_out_event     # FPL 官方"本周"净转会
trend_score(player)  = net_transfer 在全体球员中的 min-max 归一化 × 100
                       （全体净转会相同时全部取 50）
Market Score         = TSB% × tsb_weight + trend_score × trend_weight
                       TSB% = selected_by_percent（玩家持有率）
```

- **TSB（Team Selected By）**：直接被多少人选中持有，是市场共识的"存量"指标；
- **Trend（本周转会热度）**：净转会的归一化分数，是市场共识的"增量/动量"指标；
- 归一化的原因：TSB 是 0–100 百分比、净转会是绝对值（可上千），必须先归一化到同一量纲才能加权。

**权重随赛季演进**（`config/strategy.json`，当前 GW3 用第一段）：

| 阶段 | GW | TSB 权重 | Trend 权重 | 理由 |
|---|---|---|---|---|
| 赛季前段 | 1–10 | 0.8 | 0.2 | 玩家活跃，持有率可信 |
| 赛季中段 | 11–20 | 0.6 | 0.4 | 僵尸队出现，转会热度权重上升 |
| 赛季后段 | 21+ | 0.3 | 0.7 | 僵尸队占比高，以活跃玩家行为为准 |

Market Score 是**所有子决策的唯一评分输入**：阵容构建、阵型、首发、队长、转会全部按它排序/筛选。

### 2.3 每轮决策流水线

```
拉取官方数据（bootstrap + entry + picks + history）
  → 读 config/strategy.json，按当前 GW 取权重分段
  → 给全体球员算 Market Score
  → 阵容为空？→ 是：按 Market Score 两阶段贪心建立 15 人
       （约束：GKP×2/DEF×5/MID×5/FWD×3、预算≤100、同队≤3；超预算时从"最不热门的"开始降级）
     → 否：不重建，只做最小调整（转会模块）
  → 阵型：从 7 种阵型池 [343,352,442,433,451,541,532] 中取"首发 11 人 Market Score 之和"最高者
  → 队长/副队长：首发 XI 中 Market Score 第一/第二
  → 转会评估（见下）→ 生成本轮"建议"
  → 幂等写入 data/history.json（每 GW 一条）+ 更新 data/state.json
```

### 2.4 转会触发条件（市场价值的差异化操作）

只要同时满足「有免费转会」且命中下面任一：

1. **Market Gap**（核心机制）：`同位置最佳 Market Score − 当前持有球员的 Market Score > 15`
   → 例：手上 MID 50 分，同位置市场最高 80 分 → Gap=30 → 触发转会评估。
2. **伤病/存疑**：`chance_of_playing_next_round < 75`，或 `status ≠ available`（`a`=可用、`i`=伤、`d`=存疑…）。

**替代者选择**：候选 = 全市场 ∩ 同位置 ∩ 不在当前阵容 ∩ 转入后同队≤3 ∩ 价格可负担，取其中 **Market Score 最高**者。多笔候选按"损失"（替代者分 − 现持有者分）降序处理。

**硬约束**：只建议**免费转会**（不扣分，`allow_hits=false`）；新账号未完结赛季前期显示 `unlimited`（意为"暂不受限"），不用大整数伪装。

### 2.5 决策输出（前端"本轮建议"，也写进 history）

`state.json.decision` / history 条目含：`formation`、`captain`、`vice`、`starting_xi`、`bench`、`squad_source`、`transfer_status`、`free_transfers`、`recommended_transfers`、`transfer_notes`（不转会的原因），外加 `metrics`（team_market_score / captain_market_score / formation_market_score）。

---

## 3. 数据源介绍

### 3.1 FPL 官方公开 API（决策主数据源，无鉴权）

| 端点 | 内容 | 本 Agent 用到的字段 |
|---|---|---|
| `/api/bootstrap-static/` | 全赛季静态+市场动态 | `elements[]`: `selected_by_percent`（TSB）、`transfers_in_event`/`transfers_out_event`（本周转进转出）、`chance_of_playing_next_round`（出战概率，None 视为健康）、`status`、`now_cost`（价格，单位 0.1m 需 /10）、`element_type`（位置）、`team`、`form`、`total_points`；`events[]` 的 `deadline_time`/`finished`；`teams[]` |
| `/api/entry/{id}/event/{gw}/picks/` | 我的当前阵容 | `picks[]`（起始/队长/副队长/替补席）、`entry_history.bank` |
| `/api/entry/{id}/history/` | 我的历史战绩 | `current[].event_transfers`（免费转会备用推导） |

官方源是 TSB / 转会热度 / 伤病 / 价格的唯一真源，也是 Market Score 的唯一输入。**预测类数据（进球/零封/难度）官方不提供**，来自下面第二个源。

### 3.2 FPL Joe（外部预测层，采集但不参与当前决策）

免费公开接口（无鉴权），供未来 xP 决策使用。本 Agent 目前只负责**抓取、标准化、落盘**——接手的 Agent 可以自由读取这些文件做数据决策。

接口：`https://www.fpljoe.com/api/odds/projections?competition=premier-league&season=<season>&scope=season:<season>`。响应里实际是**两套独立数据**，字段并列命名（`_elevenify` 后缀区分），不要混用：

| 数据源 | 覆盖 | 含义 |
|---|---|---|
| `odds_market`（`projectionsByPeriod`）| 博彩公司开盘的**近 4 轮**（当前 GW1–4）| 每场比赛的主客期望进球（`lambdaHome/Away`）、零封概率（`pHomeCs/pAwayCs`）、胜平负（当前未落盘）|
| `elevenify`（`supplementalProjections`）| **全赛季 38 GW** | 每队每 GW 的整体进球序列（`goals[]`）、零封序列（`cleanSheets[]`）、380 场赛程骨架（主客/时间/难度）|

**球队主键不一致**：FPL Joe 的 `abbreviation` ≠ FPL 官方 `team` id。本 Agent 已按"abbreviation ↔ 官方 short_name"做过映射，所有落盘行同时给出 `team`（名字）、`fpl_team_id`（官方 id）、`fpl_team_name`。

抓取结果落盘为 **3 个文件**（每个自带 `metadata`：请求/实际 GW 范围、各来源覆盖、缺失 GW、新鲜度、警告）：

1. **`data/external/fpljoe/projected_goals.json`（预期进球）**
   - `fixtures[]`（比赛级，每场两条视角）：`home_projected_goals` / `away_projected_goals`（odds_market，float，如 1.63）；`home_projected_goals_elevenify` / `away_projected_goals_elevenify`（如 1.51）
   - `teams[]`（球队级）：`team` / `opponent` / `venue`(home/away) / `fpl_team_id` / `projected_goals` / `source`（当前 = elevenify）
2. **`data/external/fpljoe/clean_sheets.json`（零封概率）**
   - 同上结构：比赛级 `home/away_clean_sheet_probability`（odds_market，float 0–1，如 0.332 = 33.2%）与 `..._elevenify`（如 0.29）；球队级 `clean_sheet_probability`（当前 = elevenify）
3. **`data/external/fpljoe/fixture_difficulty.json`（赛程难度）**
   - `difficulty`：int 难度分（FPL 官方习惯 0–5，**越高越难**，主队 2 / 客队 3 常见）
   - `home/away_difficulty_sort_rating`：**连续值**排序分（可负，如 -0.49 / 0.14），比 int 更细，适合做连续特征

`config/fpl_joe.json` 控制抓取行为：窗口 `gameweeks_ahead=5`（每次请求未来 5 轮，如 GW4–8）、新鲜度判定（≤6h = fresh，>48h = stale）、超时重试。

### 3.3 数据落盘位置总览

```
data/
├── state.json              # 当前状态：赛季/GW/deadline/last_update/15人阵容(每行含 market_score)/decision
├── history.json            # {season, manager_id, history: [每 GW 一条：decision+notes+metrics+结果回填]}
└── external/fpljoe/
    ├── projected_goals.json
    ├── clean_sheets.json
    └── fixture_difficulty.json
```

前端另有 `web/data/` 同步副本（构建时复制），语义相同。

`state.json` 球员行完整字段：`id/name/pos/team/price/selected_by(_percent)/form/total_points/status/chance_of_playing_next_round/transfers_in_event/transfers_out_event/starting/multiplier/is_captain/is_vice_captain/market_score`——**Market Score 已由本 Agent 算好直接给出**，接手方可直接使用。

### 3.4 当前数据快照（2026-09-03）

- 官方数据：GW3 进行中，deadline `2026-09-04T17:30Z`；阵容 15 人（含每人生成时 market_score），决策建议 352 / 队长 João Pedro / 副队长 B.Fernandes / 0 笔转会；
- FPL Joe 最近一次抓取 `2026-09-03T14:10:49Z`（fresh），请求窗口 **GW4–8**：
  - `odds_market` 实际只发布到 **GW4**（metadata `missing_gameweeks=[5,6,7,8]`）——即 GW5+ 目前**没有**博彩零封/期望进球值；
  - `elevenify` 覆盖 1–38 全赛季（序列数组），GW5+ 可用它兜底；
- 新账号无历史战绩可回填，`points/rank` 为 0；history 目前 2 条（决策条目带 `decision`，结算字段待回填）。

### 3.5 新鲜度与更新节奏（了解即可）

仓库 Actions 每 10 分钟自触发一次闸门（`brain/scheduler.py`，只读 state.json 不请求 FPL）：距 deadline >24h 每天北京时间 09:00 全量更新；24h 内每小时；≤1h 每 10 分钟。每次引擎运行都会刷新官方数据 + 重抓 FPL Joe（失败保留上一份有效数据）。

---

## 4. 给接收 Agent 的注意点（读数据前必读）

1. **量纲**：TSB 是 0–100（`selected_by_percent`），odds_market 概率是 0–1 float，`market_score`/`trend_score` 是 0–100——混用前先归一。
2. **两套预测源不要直接混合**：`_elevenify` 后缀与无后缀（odds_market）虽都是"进球/零封"，但来自不同模型（博彩盘 vs 预测模型），横向比较建议先各自归一或固定用同一来源。
3. **难度方向**：`difficulty` 越大越难（对进攻方不利）；`sort_rating` 可负可正，与 difficulty 同向。
4. **球队键**：同一球队在文件里可能出现 `team`（英文全名）、`abbreviation`（未落盘）、`fpl_team_id`（官方数字）三套表达；用 `fpl_team_id` 与官方数据 join 最稳。
5. **免费转会 `unlimited`** 是赛季初新号语义，不是 99/大整数；想转受限次数需等第 2 轮完结或走鉴权 API。
6. **空值语义**：`chance_of_playing_next_round=null` 视为健康；FPL Joe `missing_gameweeks` 表示 odds_market 没出；个别 `_elevenify`/概率为 `null` = 该源该轮无值。
7. **预测数据当前不进决策**：本 Agent 的阵容/队长/转会全部基于市场共识（TSB+趋势+伤病），**没有**使用 xP/进球/零封/难度。接手方若要产出"数据决策"（如按预期进球选队长、按零封概率选后卫），文件已就绪，但需自己定义评分——这是当前唯一未被使用的信息面。
