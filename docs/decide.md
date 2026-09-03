# FPL AI Manager — Phase 2 设计：Lineup & Captain Engine（decide.md）

> 版本：v1.0　日期：2026-09-03　目标分支：**test**（不合并 main）
> 本文档只定设计，不写实现。实现前需本文件评审通过。

---

## 0. 背景与目标

Phase 1 已成立：Market Score（市场共识）→ 决定"买谁"。但"**15 人里谁首发 / 谁替补 / 谁当队长**"仍由 Phase 1 的简单规则（首发展示 + 队长 = Market Score 第一/第二）决定，没有利用预测数据。

Phase 2 的目标：引入**本轮价值**评分体系，用数据（预测 + 状态 + 连续性）做首发/替补/队长决策，同时满足 FPL 合法性硬约束。

**核心原则：项目内存在三套互相独立、不可混用的评分系统。**

```
Market Score   → 长期价值   → 决定「买谁」（Phase 1，已成立，继续用于转会）
Lineup Score   → 本轮价值   → 决定「谁首发 / 谁替补」（本阶段）
Captain Score  → 本轮爆发   → 决定「谁当队长」（本阶段，寻找爆发点而非稳定性）
```

- Lineup Score 追求的是"这个 GW 哪个 11 人组合期望总分最高"；
- Captain Score 追求的是"这一轮单点爆发潜力最大"——两者视角不同，公式必须独立，禁止用 Lineup Score 的排名直接当队长依据（或反之）。

### 0.1 明确不做（本阶段范围红线）

| 不做 | 说明 |
|---|---|
| 人工规则 / 硬编码球员名单 | 所有权重、阈值进 `config/strategy.json`（沿用 P2/P4） |
| 外部付费 API | 只用已允许的数据源 |
| 影响"买谁" | 转会仍走 Phase 1 Market Consensus 逻辑，不被本轮评分反向修改阵容构成 |
| 枚举阵型池 | 阵容合法性走约束求解（第 4 节），阵型由结果自动推导 |

---

## 1. 新增模块

```
brain/lineup_score.py     # 子分计算 + 归一化 + Lineup Score + 合法首发/替补选择
brain/captain_score.py    # Attack Potential + Captain Score + 队长/副队长选择
```

职责划分：两个模块都**只读**——读 squad、读允许的数据、读配置；**不写**任何 FPL 账号、不改 squad 构成。输出（阵容/队长/评分明细）由 `__main__` 写入 state/history。Phase 1 的 `brain/lineup.py`、`brain/captain.py` 退出编排调用链（文件暂留作回归对照，实现完成后单独评审是否删除）。

---

## 2. 数据输入与对齐

### 2.1 允许的数据源（就这些，不引入其他）

| # | 数据 | 来源 | 供谁用 |
|---|---|---|---|
| 1 | Market Score（已算好） | `state.json` 球员行 | Lineup、Captain |
| 2 | FPL Joe Projected Goals | `data/external/fpljoe/projected_goals.json` | Projection Score |
| 3 | FPL Joe Clean Sheet Probability | `data/external/fpljoe/clean_sheets.json` | Clean Sheet Score |
| 4 | FPL Joe Fixture Difficulty | `data/external/fpljoe/fixture_difficulty.json` | Fixture Score |
| 5 | 最近 3 轮实际 FPL 得分 | FPL 官方 `/api/element-summary/{element_id}/`（新增拉取，见 2.4） | Form / Streak Score |

### 2.2 「目标 GW」的定义

目标 GW = `state.json.next_deadline` 在官方 `events[]` 中对应的那一轮（`deadline_time == next_deadline` 的 event）。所有"当前目标 GW"的读取都以它为准，不依赖 `current_gw` 的猜测。

> 现状已知偏差（实现时修正）：FPL Joe 抓取窗口目前从"当前 GW + 1"起算，而决策发生在 next_deadline 那一轮，两者可能错位。Phase 2 要求抓取窗口起点 = 目标 GW（保持 ahead=5 轮），由 `__main__` 在调用 `refresh()` 时传入。数据缺失时的降级规则见 2.5。

### 2.3 球队级 → 球员级

FPL Joe 三份文件都是**球队 × 轮次**粒度（`teams[]` 行含 `fpl_team_id`）。换算规则：一名球员拿其所在球队（`state.json` 球员行的 `team` ↔ `fpl_team_id` 映射）该目标 GW 的值。同队所有球员同分。

### 2.4 新增官方拉取：element-summary

Form/Streak 需要"每名球员最近 3 轮实际得分"，官方只在 `/api/element-summary/{id}/` 提供（`history[].round/points`）。设计：

- 只拉**当前 15 人 squad**（每人 1 次请求，共 15 次；公开端点，频率安全）；
- 只保留需要的 `round / points / minutes` 字段，内存使用，不落盘；
- GW-1/2/3 = 最近 3 个 **finished** 的 event（按 `events[].finished` 判定），取每名球员对应 round 的 `points`（含 bonus，负分照算；缺席场次官方会给 0 分，照用）；
- 赛季早期不足 3 轮 finished 时：只用已有轮次，权重按比例重归一（见 3.1）。

### 2.5 缺失 / 空值降级规则（重要，决定鲁棒性）

| 情形 | 处理 |
|---|---|
| 某队目标 GW 无行（无赛程 / blank） | 该队球员的 Projection / CleanSheet / Fixture 三项记 **0**（没比赛就没产出），并进 notes |
| 目标 GW 整个不在文件窗口内 / 文件超 48h 未刷新 | 三项取**中立 50**（不惩罚不偏袒，等待数据恢复），并进 notes `data_missing` |
| 两个 fpljoe 源都有值时 | 按整轮统一选源（见 2.6），**禁止同轮内逐队混源** |
| `chance_of_playing_next_round` 缺失 | 沿用 Phase 1 语义：视为健康（不归本项目管） |
| Market Score 缺失 | 视为 0 并进 notes（不应发生；squad 行由 Phase 1 保证） |

### 2.6 FPL Joe 双源的选源规则

`odds_market`（博彩，0–1/连续）与 `elevenify`（模型，标量）两套并存。归一化时若逐队混源会破坏量纲一致性。设计规则：

- **Projection / Clean Sheet**：若目标 GW 在 odds_market 覆盖内（`fixtures[]` 有值）→ 全轮用 odds_market 值归一化；否则全轮用 `elevenify` 值（`teams[]` 行，即文件现状）归一化。整轮统一。
- **Fixture**：优先用连续值 `difficulty_sort_rating`；该值缺失的轮次整轮退回 `difficulty`（int）。
- 选源决定记入 notes（`score_source`），保证可追溯。

---

## 3. 评分体系

### 3.0 通用约定（所有分数）

- 输出范围 0–100，保留 **2 位小数**；
- min-max 归一化：`(x − min) / (max − min) × 100`；若全体同值 → 全部 **50**（与 `market.py` 惯例一致）；
- 并列打破：element_id 小者优先（与 Phase 1 一致，保证确定性）；
- 归一化集合（数据可得性决定，跨 GW 复盘的读者须知）：
  - Projection / CleanSheet / Fixture：**当轮有赛程的全部 20 队**（League-wide）——得分绝对含义稳定，同队球员同值；
  - Form：**当前 15 人 squad 内**（官方 per-GW 分只拉得到 15 人；不爬 652 人）。局限如实记录：Form 是队内相对量，跨轮比较时只用于同轮内的排序，不用于绝对对比；
  - Streak：离散查表，无需归一化（见 3.3）。

### 3.1 Form Score（近期状态）

```
FormRaw = 0.5 × P(GW-1) + 0.3 × P(GW-2) + 0.2 × P(GW-3)
   P(gw) = 该球员该轮实际 FPL points（取最近 3 个 finished GW）
   ★ 不足 3 轮时：只对已有轮次的权重归一（例：只有 GW-1 → FormRaw = P(GW-1)）
Form Score = min-max(GW-1..3 覆盖下 15 人 FormRaw) → 0–100
```

### 3.2 Streak Score（连续回报，具体实现由本设计定案）

回报位：`b_i = 1` 当该轮 `points ≥ 5`（5 进配置，默认 `streak_min_points`），否则 0。最近三轮依次记 `b1(GW-1) b2(GW-2) b3(GW-3)`，如 `1 1 1`、`1 0 1`。

设计：**位置加权映射**，权重按"越近越重"，同时保证"单次近期回报 > 任意两次旧回报组合"会产生过强的长尾判断，因此取 `(w1, w2, w3) = (4, 2, 1)`，查表法按最大和 7 归一：

| 模式 | 权重和 | Streak Score | 要求 |
|---|---|---|---|
| `1 1 1` | 7 | **100** | |
| `1 1 0` | 6 | 85.71 | |
| `1 0 1` | 5 | 71.43 | |
| `0 1 1` | 3 | 42.86 | |
| `1 0 0` | 4 | **57.14** | |
| `0 1 0` | 2 | 28.57 | |
| `0 0 1` | 1 | 14.29 | |
| `0 0 0` | 0 | **0** | |

→ 严格满足需求链 **111 > 110 > 101 > 100 > 000**，且全序无并列、数值单调、规则简单可测试。

说明：`100 (57.1) > 011 (42.9)` 是有意为之——单次**最近**一轮回报高于两次较早回报，与 Form 的近期优先视角一致；若未来想改成"连击优先"，只需换权重（如 0 连击检测），配置驱动不改结构。

不足 3 轮历史：缺失位按 0 处理（与 Form 的"权重归一"策略不同：Streak 语义是"最近表现真实可见"，缺历史就不给分，更诚实）。

### 3.3 Projection / Clean Sheet / Fixture Score

```
Projection   = League-wide min-max( 目标 GW 该队 projected_goals    ) × 100
CleanSheet   = League-wide min-max( 目标 GW 该队 clean_sheet 概率   ) × 100
Fixture      = League-wide min-max( 目标 GW 该队 -difficulty_sort_rating ) × 100
               # 先取负号再归一：难度越低 → 值越大 → 分数越高（sort_rating 缺失则退回 difficulty，同样取负）
```

> 注意：clean_sheet 概率是 0–1（如 0.29），projected_goals 是 0–5 的期望值——两者都必须各自归一后才有可比性，不要直接混用原始值。

### 3.4 Attack Score（进攻球员的综合本轮值，MID/FWD 用）

```
Attack Score = 0.60 × Projection + 0.25 × Form + 0.15 × Streak
```

设计意图：进攻球员看状态（近期状态放大预测价值），但预测仍是主干（0.60），状态不能完全覆盖预测。

### 3.5 Lineup Score（按位置加权）

| 位置 | 公式 | 校验和 |
|---|---|---|
| **GKP** | `0.50×CleanSheet + 0.30×Fixture + 0.20×Market` | 1.00 |
| **DEF** | `0.45×CleanSheet + 0.25×Fixture + 0.10×Attack + 0.20×Market` | 1.00 |
| **MID** | `0.40×Attack + 0.20×Form + 0.20×Fixture + 0.20×Market` | 1.00 |
| **FWD** | `0.50×Attack + 0.20×Form + 0.10×Fixture + 0.20×Market` | 1.00 |

每个位置的系数**全部进配置**（第 7 节），各位置权重和应为 1，加载时校验并 warning（同 `tsb+trend≈1`）。

### 3.6 Captain Score（独立评分，用于"单轮爆发"）

队长候选的核心是爆发潜力（Attack Potential），稳定性指标（CleanSheet 稳定性只在 GK/DEF 场景按位置加权进入）：

```
Attack Potential = 0.70×Projection + 0.20×Form + 0.10×Streak

MID  Captain Score = 0.80×Attack Potential + 0.20×Market
FWD  Captain Score = 0.90×Attack Potential + 0.10×Market
DEF  Captain Score = 0.30×Attack Potential + 0.70×CleanSheet
GKP  Captain Score = 0.50×CleanSheet + 0.50×Market
```

设计意图：进攻球员的队长价值几乎完全由"这轮能爆多高"（预测+状态+连击）决定；后卫/门将没有高爆发上限，以零封稳定性为主。

---

## 4. 合法首发选择（约束求解，不枚举阵型）

输入：15 人 squad（每人已算好 Lineup Score）。输出：首发 11 + 替补 4 + 自动推导的阵型。

**硬约束（FPL 合法性的充分条件）**：

```
首发中：GKP ≥ 1，DEF ≥ 3，MID ≥ 2，FWD ≥ 1；总人数 = 11
```

**算法**：

- **Step 1 锁位**：各位置取 Lineup Score 最高的 1 GK / 3 DEF / 2 MID / 1 FWD 进入首发（7 人锁死 → 任何后续选择都不可能跌破下限）。
- **Step 2 补满**：其余 8 人按 Lineup Score 降序，依次补入直至 11 人（每轮补入后重新检查位置上限：DEF ≤ 5、MID ≤ 5、FWD ≤ 3，由 squad 构成天然满足，无需额外枚举）。
- **Step 3 阵型**：由最终首发人数自动推导 `formation = f"{DEF 数}{MID 数}{FWD 数}"`。合法结果天然包括 343 / 352 / 433 / 442 / 451 / 532 / 541 等——**不预设阵型池，不限制哪些形状合法**，只要约束满足就合法。
  - 与 Phase 1 的差异：Phase 1 在 7 个预置阵型里挑"首发 Market Score 之和最高"；Phase 2 不预置，直接约束求解，避免"预置池把更好的组合排除在外"。

**替补顺序（Bench Order）**：剩余 4 人 = 1 门将 + 3 非门将。非门将按 Lineup Score 降序依次为 Bench1 / Bench2 / Bench3（Bench1 拥有最高自动替补优先级）；Bench GK 固定最后（FPL 自动替补里 GK 只能替 GK，放最后不损失任何优先级，且语义清晰）。存储顺序 `[Bench1, Bench2, Bench3, BenchGK]`。

**保证**：Step 1 锁位使 DEF≥3 / MID≥2 / FWD≥1 永远成立（验收项 2/3/4）；squad 合法（2/5/5/3 配额）⇒ 算法必定能填满 11 人，不存在无解分支。

---

## 5. 队长与副队长

- 候选域：**仅 Starting XI 11 人**（保证队长一定上场，避免 FPL 规则错误）；
- `Captain = 候选域中 Captain Score 最高者`；`Vice = 次高者`；
- 并列 → element_id 小者胜（确定性）；
- 队长可以是任何位置（含 GKP），规则不限制——数值上 GK/DEF 极少超过爆发型进攻球员，属正常分布。

---

## 6. 输出 schema（state.json / history.json）

### 6.1 state.json

- 每个球员行追加（15 人全部，不只在首发）：

```json
{
  "id": 426,
  "...现有字段...": "...",
  "market_score": 42.92,
  "lineup_score": 83.4,
  "captain_score": 78.2,
  "score_breakdown": {
    "projection": 70.1, "form": 88.0, "streak": 100.0,
    "clean_sheet": null, "fixture": 55.5, "attack": null,
    "attack_potential": 74.1
  }
}
```

  - `score_breakdown` 用于"所有评分可追溯"（验收 8）：只记实际参与计算的成分（GK/DEF 无 attack 就写 null）；已含归一化后分值（0–100），原始输入值不落盘（体积与噪音考虑）；
- 顶层新增（用于前端集中展示 / 图表）：

```json
{
  "lineup_scores":  { "426": 83.4, "...": 0 },
  "captain_scores": { "426": 78.2, "...": 0 },
  "score_meta": {
    "target_gw": 4,
    "score_source": { "projection": "odds_market", "clean_sheet": "elevenify", "fixture": "sort_rating" },
    "warnings": ["..."]
  }
}
```

- `decision` 块（structure 兼容前端，不破坏 Phase 1 展示）：`formation / starting_xi / bench / captain / vice` 改为由新引擎输出（captain/vice 仍为完整球员对象、starting_xi/bench 仍为 `{id,name}` 列表），`transfer_*` 字段逻辑不动。

### 6.2 history.json（每轮条目）

- `metrics` 追加：

```json
{
  "metrics": {
    "...Phase 1 原键...": "...",
    "team_lineup_score": 812.4,
    "captain_score": 78.2
  }
}
```

  - `team_lineup_score` = 首发 11 人 lineup_score 之和（同轮内横向可比较；跨轮受归一化集合影响，复盘时注明口径）；
  - `captain_score` = 队长球员的 captain_score 值；
- `strategy_snapshot` 扩展：追加本轮生效的 lineup/captain 权重快照（与 Phase 1 的 strategy_snapshot 并存或嵌套），供赛季复盘；
- `notes` 新增 topic：`score_source`（fpljoe 选源/目标 GW）、`data_missing`（2.5 降级触发）、`lineup`（首发/替补决策摘要）、`captain`（保留 Phase 1 topic，内容为爆发视角理由）。

---

## 7. 配置化（P2/P4，禁止硬编码）

全部权重与阈值进 `config/strategy.json`（唯一策略配置，不新增文件）。建议结构：

```json
{
  "...Phase 1 字段...": "...",
  "lineup_engine": {
    "streak_min_points": 5,
    "streak_weights": [4, 2, 1],
    "streak_map": {
      "000": 0, "001": 14.29, "010": 28.57, "011": 42.86,
      "100": 57.14, "101": 71.43, "110": 85.71, "111": 100.0
    },
    "form_weights": [0.5, 0.3, 0.2],
    "attack_weights":         { "projection": 0.60, "form": 0.25, "streak": 0.15 },
    "attack_potential_weights": { "projection": 0.70, "form": 0.20, "streak": 0.10 },
    "lineup_weights": {
      "GKP": { "clean_sheet": 0.50, "fixture": 0.30, "market": 0.20 },
      "DEF": { "clean_sheet": 0.45, "fixture": 0.25, "attack": 0.10, "market": 0.20 },
      "MID": { "attack": 0.40, "form": 0.20, "fixture": 0.20, "market": 0.20 },
      "FWD": { "attack": 0.50, "form": 0.20, "fixture": 0.10, "market": 0.20 }
    },
    "captain_weights": {
      "GKP": { "clean_sheet": 0.50, "market": 0.50 },
      "DEF": { "attack_potential": 0.30, "clean_sheet": 0.70 },
      "MID": { "attack_potential": 0.80, "market": 0.20 },
      "FWD": { "attack_potential": 0.90, "market": 0.10 }
    },
    "position_min_starters": { "DEF": 3, "MID": 2, "FWD": 1, "GKP": 1 },
    "fallback_neutral_score": 50
  }
}
```

默认值 = 本文件第 3 节全部数字。加载器沿用 `strategy_config.py` 风格：缺字段用内置默认并 warning；各位置权重和 ≈1 校验。`streak_map` 直接配全表：权重改了 map 也变，代码只查表、不算权重。

---

## 8. 测试计划（tests/test_phase2.py，合成数据，无网络）

| # | 测试 | 对应验收 |
|---|---|---|
| 1 | 给定合成 15 人 → 首发 11 人；DEF/MID/FWD 计数检查 | 验收 1–4 |
| 2 | Step1 锁位极端用例（某位置所有人都垫底）→ 该位置仍 ≥ 下限 | 验收 2–4 |
| 3 | 替补顺序：非门将按 Lineup Score 降序，GK 恒最后 | 验收 5 |
| 4 | Captain/Vice 均 ∈ 首发；= Captain Score 第一/第二 | 验收 6–7 |
| 5 | Streak 全表 8 模式 → 断言全序（111>110>101>100>000 及其余） | 3.2 |
| 6 | 归一化：league-wide 0–100、全体同值→50、2 位小数 | 3.0 |
| 7 | fpljoe 缺失：目标 GW 缺整轮 → 50 + note；单队无行 → 0 + note | 2.5 |
| 8 | 配置驱动：改 `config/strategy.json` 一档权重 → 分数与首发随之变化 | 第 7 节 |
| 9 | 确定性：同一输入跑两遍输出一致；并列取 element_id 小者 | 3.0 |
| 10 | 输出 schema：15 行全有 lineup_score/captain_score/breakdown；metrics 新键存在 | 验收 8–10 |
| 11 | 评分独立性：Captain Score 最高的 11 人 ≠ 直接用 Attack/Lineup 排序得出（构造反例） | 0 核心原则 |
| 12 | element-summary 解析：mock 响应（round/points 提取、缺席、负分、非 finished 过滤） | 2.4 |

---

## 9. 实现顺序（评审通过后）

1. 扩展 `config/strategy.json`（lineup_engine 段）+ 加载函数
2. `api.py` 增加 element-summary 拉取（15 人，缓存字段）
3. `brain/lineup_score.py`：子分 → 位置 Lineup Score → 约束求解首发/替补
4. `brain/captain_score.py`：Attack Potential → Captain Score → C/V
5. `__main__.py` 编排接入（squad 就绪后 → 评分 → 写 state/history；fpljoe 窗口改从目标 GW 起）
6. 测试（第 8 节全绿）→ 本地 `npm start` 对照 state 输出 → 前端兼容性目检
7. 只提交到 **test** 分支；不与 main 合并，直到整体验收

## 10. 已知边界与限制（如实记录）

| 边界 | 说明 |
|---|---|
| Form 为 15 人队内归一化 | 不爬 652 人 per-GW 分的工程取舍；同轮排序语义成立，跨轮绝对值不成立 |
| 赛季前 1–2 轮数据不足 | Form 权重重归一、Streak 缺位记 0；早期决策置信度低属正常 |
| fpljoe 覆盖错位 | 现状窗口从当前 GW+1 起算；实现时修正为"目标 GW 起算"（2.2），此前靠降级规则兜底 |
| 概率与期望值量纲 | CleanSheet 0–1 与 ProjectedGoals 0–5 各自归一后才可比（3.3 已处理） |
| 归一化口径随轮变化 | League-wide 集合每轮含 blank 队而略有不同；影响跨轮 metrics 绝对值比较 |
| 替补仅排序不"预测事件" | 不考虑首发伤病概率（官方无 per-player next-GW 上场率，只有 chance 字段，Phase 1 用其做转会触发而非首发加权）——如需可放 Phase 2.5 讨论 |
