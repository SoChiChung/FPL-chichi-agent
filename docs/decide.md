# FPL AI Manager — Phase 2 设计：Lineup & Captain Engine（decide.md）

> 版本：v1.1　日期：2026-09-03　目标分支：**test**（不合并 main）
> 本文档只定设计，不写实现。实现前需本文件评审通过。
>
> **v1.1 修订**：依 Phase 2 Design Review（2026-09-03）落地——①首发改全局最优枚举（P0-1）；②DEF Captain 公式引入 Fixture（P0-2）；③MID/FWD Captain 降低 Market 权重（P0-3）；④Form 改官方全员口径（P1-4）；⑤Streak 门槛按位置区分（P1-5）。逐项对照见第 11 节。

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
| 枚举阵型池 | 阵容合法性走**全组合枚举 + 合法性过滤**（第 4 节），不枚举阵型形状，阵型由结果自动推导 |

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
| 5 | 官方 Form（全体球员） | FPL 官方 `bootstrap-static`（本轮刷新已拉取，`brain/api.py` `get_bootstrap()`，不新增请求） | Form Score |
| 6 | 最近 3 轮实际 FPL 得分 | FPL 官方 `/api/element-summary/{element_id}/`（新增拉取，仅 15 人，见 2.4） | Streak Score |

### 2.2 「目标 GW」的定义

目标 GW = `state.json.next_deadline` 在官方 `events[]` 中对应的那一轮（`deadline_time == next_deadline` 的 event）。所有"当前目标 GW"的读取都以它为准，不依赖 `current_gw` 的猜测。

> 现状已知偏差（实现时修正）：FPL Joe 抓取窗口目前从"当前 GW + 1"起算，而决策发生在 next_deadline 那一轮，两者可能错位。Phase 2 要求抓取窗口起点 = 目标 GW（保持 ahead=5 轮），由 `__main__` 在调用 `refresh()` 时传入。数据缺失时的降级规则见 2.5。

### 2.3 球队级 → 球员级

FPL Joe 三份文件都是**球队 × 轮次**粒度（`teams[]` 行含 `fpl_team_id`）。换算规则：一名球员拿其所在球队（`state.json` 球员行的 `team` ↔ `fpl_team_id` 映射）该目标 GW 的值。同队所有球员同分。

### 2.4 新增官方拉取：element-summary（仅供 Streak）

Streak 需要"每名球员最近 3 轮实际得分"，官方只在 `/api/element-summary/{id}/` 提供（`history[].round/points`）；Form 不再走它（v1.1 改源，见 3.1）。设计：

- 只拉**当前 15 人 squad**（每人 1 次请求，共 15 次；公开端点，频率安全）；
- 只保留需要的 `round / points / minutes` 字段，内存使用，不落盘；
- GW-1/2/3 = 最近 3 个 **finished** 的 event（按 `events[].finished` 判定），取每名球员对应 round 的 `points`（含 bonus，负分照算；缺席场次官方会给 0 分，照用）；
- 赛季早期不足 3 轮 finished 时：缺失位按 0 处理（见 3.2，Streak 不给"想象分"更诚实）。

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
  - Form：**全体球员**（bootstrap-static 全员官方 form，League-wide，见 3.1）——与上述三项口径一致，全联盟可比；
  - Streak：离散查表，无需归一化（见 3.2）。

### 3.1 Form Score（近期状态）★ v1.1：改用官方全员口径（评审 P1-4，采纳方案 A）

```
Form Score = League-wide min-max(全体球员官方 form 字段) × 100
   官方 form = bootstrap-static 各元素 form（解析为 float；官方口径：近 N 场实际得分的均值，
   缺场 / 出场分钟等细节为官方内部规则，作为黑盒直接使用，不自算）
```

v1.0 曾用 `FormRaw = 0.5×P(GW-1) + 0.3×P(GW-2) + 0.2×P(GW-3)` 并在 **15 人 squad 内**归一化。评审指出的缺陷：当 15 人全队状态都差时，队内归一化仍会把"差球员中的第一名"打成 Form=100，让他看起来状态很好。修订决策：

- **采纳方案 A**：直接用官方 `form`。bootstrap-static 在本轮刷新已拉取（`brain/api.py` `get_bootstrap()`，见 2.1 #5），**全员数据零新增请求**；League-wide 归一化后，squad 内最高分也只是"相对全联盟的状态"，不再有 15 人窗口的人为虚高，跨轮绝对值可比。
- **否决方案 B**（保留自定义近因加权、改为全员归一化）：那需要 652 人的逐轮历史分 = 每人 1 次 element-summary，约 652 次请求，与"只拉 15 人"的工程约束冲突。
- **近因信息没有丢失**：自定义加权本要表达"最近一轮最可信"；该职责现由 Streak Score 承担——其权重 `(4,2,1)` 偏重 GW-1（3.2）。Form 与 Streak 分工变为：Form = 官方中周期状态（黑盒、可跨联盟比较），Streak = 近 3 轮的回报节律。
- **特殊值**：赛季首个 finished 轮次出现前，全员 form 同值（多为 0）→ 3.0 兜底全体 50，如实不假装有状态；官方 form 保留 1 位小数，同分并列多，并列打破 element_id 小者优先（3.0），确定性不受影响。

### 3.2 Streak Score（连续回报，具体实现由本设计定案）★ v1.1：回报门槛按位置区分（评审 P1-5）

回报位：`b_i = 1` 当该轮 `points ≥ 该位置门槛`，否则 0。门槛进配置（`streak_min_points`，第 7 节），按位置区分：

| 位置 | 门槛 | 语义 |
|---|---|---|
| GKP / DEF | **6** | ≈ 零封一轮（出场基础分 + 零封 4 分）；无零封的 5 分不是状态回报 |
| MID / FWD | **5** | ≈ 一次进攻回报 |

原因：不同位置得分的"生成结构"不同。统一 5 分会把 DEF"失球日的基础分"误记为状态回报，而 DEF 状态回报的本质是零封；MID/FWD 的 5 分已对应一次真实的进攻贡献（进球/助攻档位）。门槛内置在"该轮是否为回报轮"的判定里，回报位之后的映射表（越近越重）不随位置变化。

最近三轮依次记 `b1(GW-1) b2(GW-2) b3(GW-3)`，如 `1 1 1`、`1 0 1`。

设计：**按时序（b1→b3）加权查表**，权重"越近越重"，同时避免"单次近期回报 > 任意两次旧回报组合"产生过强的长尾判断，因此取 `(w1, w2, w3) = (4, 2, 1)`，按最大和 7 归一：

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

说明：`100 (57.1) > 011 (42.9)` 是有意为之——单次**最近**一轮回报高于两次较早回报，是"最近一轮最可信"的体现（官方 form 的窗口同样聚焦最近场次，3.1）；若未来想改成"连击优先"，只需换权重（如 0 连击检测），配置驱动不改结构。

不足 3 轮历史：缺失位按 0 处理（缺历史就不给分——不给"想象分"，更诚实）。

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

### 3.6 Captain Score（独立评分，用于"单轮爆发"）★ v1.1：公式按评审修订（P0-2 / P0-3）

队长候选的核心是爆发潜力（Attack Potential），稳定性指标（CleanSheet 稳定性只在 GK/DEF 场景按位置加权进入）：

```
Attack Potential = 0.70×Projection + 0.20×Form + 0.10×Streak

MID  Captain Score = 0.90×Attack Potential + 0.10×Market
FWD  Captain Score = 0.95×Attack Potential + 0.05×Market
DEF  Captain Score = 0.20×Attack Potential + 0.50×CleanSheet + 0.30×Fixture
GKP  Captain Score = 0.50×CleanSheet + 0.50×Market
```

设计意图（v1.1 修订后）：

- **队长 ≈ 单轮爆发价值，Market ≈ 长期资产价值**——两者关联弱，队长必须更偏向本轮爆发。MID/FWD 的 Market 权重已下调为 0.10 / 0.05（v1.0 为 0.20 / 0.10）。保留极小非零权重作为与 Phase 1 的连续性软锚，防止单轮预测噪声完全决定队长（评审接受的 "1.00 Attack Potential" 变体仅需配置改 1.00/0 即可达成，结构不变）。
- **DEF 公式体现项目理念"进攻看状态，防守看赛程"**：防守球员的"爆发"= 零封概率（0.50，主干）+ 对手易打程度（Fixture 0.30）+ 参与进攻/定位球的上限（Attack Potential 0.20）。v1.0 的 DEF 公式（0.30 Attack / 0.70 CleanSheet）没有显式赛程项——零封概率虽已隐含赛程因素，但 Fixture 是"对手多难打"的直接度量，防守爆发轮（如对阵降级区球队）的边际信息最直观；项目理念要求赛程因素在队长模型里**显式可见、可调权、可追溯**，而非藏在零封概率里。
- GKP 保持 `0.50×CleanSheet + 0.50×Market`：门将没有"进攻状态"维度，其 Market 同时承载球队防守共识与主力地位，赛程影响经零封概率传导。若评审认为 GKP 应如 DEF 一样显式加入 Fixture，仅需配置一档权重，本轮修订不擅自扩大范围。

---

## 4. 合法首发选择（★ v1.1 评审 P0-1：全局最优枚举，不枚举阵型）

输入：15 人 squad（每人已算好 Lineup Score）。输出：首发 11 + 替补 4 + 自动推导的阵型。

**算法（Bruteforce Lineup Search）**：从 15 人中枚举全部首发组合（`C(15,11) = 1365`），逐组合套合法性过滤，计算 Total Lineup Score，取全局最高者。1365 次 × 11 项求和，Python 毫秒级完成，无需剪枝与启发式。

**合法性过滤**（作用在候选 11 人组合上；位置上限由 squad 配额天然保证，无需检查）：

```
GK 恰 1（首发门将槽位只有 1 个）  且  DEF ≥ 3  且  MID ≥ 2  且  FWD ≥ 1
（组合本身已固定为 11 人；squad 配额 2/5/5/3 ⇒ DEF ≤ 5 / MID ≤ 5 / FWD ≤ 3 不可能被突破）
```

**目标函数**：`Total Lineup Score = 11 名首发球员 Lineup Score 之和`，取最大值。因为枚举覆盖全部合法组合，结果必然是**数学最优解**。

v1.0 的"Step 1 锁位 1 GK/3 DEF/2 MID/1 FWD → Step 2 按 Lineup Score 补满"只保证合法、不保证全局最优：贪心锁位会漏掉"牺牲某位置的 Top 分球员、换成另一位置的次优球员但组合总分更高"这一类方案。评审要求全枚举，理由与收益：

- 数量极小：`C(15,11) = 1365`，Python 可瞬间完成；
- 一定得到数学最优解：无需验证贪心正确性，正确性由枚举定义本身保证（测试只需对照独立实现的暴力穷举）；
- **扩展性**：DGW / BGW / 出场概率等后续维度只需改动过滤谓词或目标函数（如 BGW 轮过滤掉无赛程球员、DGW 轮目标函数加场次因子），算法本体不动——这是选择枚举而非贪心的关键原因。

**并列打破（确定性）**：Total Lineup Score 并列时取 element_id 升序元组**字典序最小**的组合（与 3.0 的 element_id 小者优先一脉相承；1365 组合在 2 位小数下几乎不会并列，规则仅为可复现兜底）。

**无解论证（防御性）**：squad 合法（配额 2/5/5/3）⇒ 过滤结果永不为空——取最高分 1 GK / 3 DEF / 2 MID / 1 FWD（7 人）后，剩余 8 人（1 GK / 2 DEF / 3 MID / 2 FWD）任取 4 人，任一取法都不破位置上限且 GK 恰 1。若枚举结果仍为空 = 程序 bug，直接报警（测试项覆盖）。

**阵型**：由最终首发人数自动推导 `formation = f"{DEF 数}{MID 数}{FWD 数}"`。合法结果天然包括 343 / 352 / 433 / 442 / 451 / 532 / 541 等——**不预设阵型池，不限制哪些形状合法**，只要约束满足就合法。
  - 与 Phase 1 的差异：Phase 1 在 7 个预置阵型里挑"首发 Market Score 之和最高"；Phase 2 不预置，直接在球员层做全组合枚举，既避免"预置池把更好的组合排除在外"，也避免两阶段贪心的次优解。

**替补顺序（Bench Order）**：首发确定后，剩余 4 人 = 1 门将 + 3 非门将。非门将按 Lineup Score 降序依次为 Bench1 / Bench2 / Bench3（Bench1 拥有最高自动替补优先级）；Bench GK 固定最后（FPL 自动替补里 GK 只能替 GK，放最后不损失任何优先级，且语义清晰）。存储顺序 `[Bench1, Bench2, Bench3, BenchGK]`。

**保证**：合法性由过滤谓词逐组合强制，DEF≥3 / MID≥2 / FWD≥1 / GK=1 对所有输出成立（验收项 2/3/4 直接可证）；Total Lineup Score 为精确最大值，无近似。

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
    "streak_min_points": { "GKP": 6, "DEF": 6, "MID": 5, "FWD": 5 },
    "streak_weights": [4, 2, 1],
    "streak_map": {
      "000": 0, "001": 14.29, "010": 28.57, "011": 42.86,
      "100": 57.14, "101": 71.43, "110": 85.71, "111": 100.0
    },
    "form_source": "bootstrap_static",
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
      "DEF": { "attack_potential": 0.20, "clean_sheet": 0.50, "fixture": 0.30 },
      "MID": { "attack_potential": 0.90, "market": 0.10 },
      "FWD": { "attack_potential": 0.95, "market": 0.05 }
    },
    "position_starters_exact": { "GKP": 1 },
    "position_min_starters": { "DEF": 3, "MID": 2, "FWD": 1 },
    "fallback_neutral_score": 50
  }
}
```

默认值 = 本文件第 3 节全部数字。加载器沿用 `strategy_config.py` 风格：缺字段用内置默认并 warning；各位置权重和 ≈1 校验；`streak_min_points` 校验须覆盖 GKP/DEF/MID/FWD 四键（v1.1 位置化）。`streak_map` 直接配全表：权重改了 map 也变，代码只查表、不算权重。`form_source` 仅记录当前 Form 口径（3.1，`bootstrap_static`），min-max 归一化本身无参数；若未来切回加权口径（评审方案 B），在该键旁补 `form_weights` 即可，结构不变。

---

## 8. 测试计划（tests/test_phase2.py，合成数据，无网络）

| # | 测试 | 对应验收 |
|---|---|---|
| 1 | 给定合成 15 人 → 首发 11 人；DEF/MID/FWD 计数检查 | 验收 1–4 |
| 2 | 枚举全局最优：构造"贪心锁位会选错"的反例（放弃某位置高分、多带另一位置反而总分更高）→ 断言输出 = 独立暴力穷举对照的最优组合；某位置全员垫底 → 最优首发仍满足该位置下限 | 验收 2–4 |
| 3 | 替补顺序：非门将按 Lineup Score 降序，GK 恒最后 | 验收 5 |
| 4 | Captain/Vice 均 ∈ 首发；= Captain Score 第一/第二 | 验收 6–7 |
| 5 | Streak 全表 8 模式 → 断言全序（111>110>101>100>000 及其余）；门槛按位置：DEF 单轮 5 分不算回报、MID 5 分算（边界用例） | 3.2 |
| 6 | 归一化：league-wide 0–100、全体同值→50、2 位小数；Form 取全员官方值归一（含 GW1 全员同值→50） | 3.0 / 3.1 |
| 7 | fpljoe 缺失：目标 GW 缺整轮 → 50 + note；单队无行 → 0 + note | 2.5 |
| 8 | 配置驱动：改 `config/strategy.json` 一档权重 → 分数与首发随之变化 | 第 7 节 |
| 9 | 确定性：同一输入跑两遍输出一致；并列取 element_id 小者 | 3.0 |
| 10 | 输出 schema：15 行全有 lineup_score/captain_score/breakdown；metrics 新键存在 | 验收 8–10 |
| 11 | 评分独立性：Captain Score 最高的 11 人 ≠ 直接用 Attack/Lineup 排序得出（构造反例） | 0 核心原则 |
| 12 | element-summary 解析（仅供 Streak）：mock 响应（round/points 提取、缺席、负分、非 finished 过滤） | 2.4 |

---

## 9. 实现顺序（评审通过后）

1. 扩展 `config/strategy.json`（lineup_engine 段）+ 加载函数
2. `api.py` 增加 element-summary 拉取（15 人，仅供 Streak；缓存所需字段）
3. `brain/lineup_score.py`：子分 → 位置 Lineup Score → **全组合枚举**首发（C(15,11)）+ 替补排序（Form 子分直接取同 run `bootstrap` 的全员 form 归一，见 3.1）
4. `brain/captain_score.py`：Attack Potential → Captain Score → C/V
5. `__main__.py` 编排接入（squad 就绪后 → 评分 → 写 state/history；fpljoe 窗口改从目标 GW 起）
6. 测试（第 8 节全绿）→ 本地 `npm start` 对照 state 输出 → 前端兼容性目检
7. 只提交到 **test** 分支；不与 main 合并，直到整体验收

## 10. 已知边界与限制（如实记录）

| 边界 | 说明 |
|---|---|
| Form 为官方黑盒口径 | bootstrap-static `form` 的近 N 场均值、缺场/出场分钟阈值由官方定义（不自算、不可配置）；跨轮/跨联盟可比；近因倾斜由 Streak（3.2）承担 |
| 赛季前 1–2 轮数据不足 | 无 finished 轮次时全员 form 同值 → 全体 50 中立分（3.1）；Streak 缺位记 0；早期决策置信度低属正常 |
| fpljoe 覆盖错位 | 现状窗口从当前 GW+1 起算；实现时修正为"目标 GW 起算"（2.2），此前靠降级规则兜底 |
| 概率与期望值量纲 | CleanSheet 0–1 与 ProjectedGoals 0–5 各自归一后才可比（3.3 已处理） |
| 归一化口径随轮变化 | League-wide 集合每轮含 blank 队而略有不同；影响跨轮 metrics 绝对值比较 |
| 替补仅排序不"预测事件" | 不考虑首发伤病概率（官方无 per-player next-GW 上场率，只有 chance 字段，Phase 1 用其做转会触发而非首发加权）——如需可放 Phase 2.5 讨论 |

---

## 11. 评审修订对照（v1.1，2026-09-03）

| 评审项 | 结论 | 落点 |
|---|---|---|
| P0-1 首发算法改全局最优搜索 | 采纳：`C(15,11)=1365` 全组合枚举 + 合法性过滤（GK 恰 1 / DEF≥3 / MID≥2 / FWD≥1），Total Lineup Score 取最大 | 第 4 节；0.1、8、9 节同步 |
| P0-2 DEF Captain 公式加 Fixture | 采纳：`0.20×Attack Potential + 0.50×CleanSheet + 0.30×Fixture`；GKP 维持原式（理由见 3.6） | 3.6；第 7 节 |
| P0-3 降低 MID/FWD Captain 的 Market 权重 | 采纳：MID `0.90/0.10`、FWD `0.95/0.05`。评审接受的 "1.00 Attack Potential" 变体以配置改 1.00/0 达成；本文取保留极小非零权重作连续性软锚 | 3.6；第 7 节 |
| P1-4 Form 不再 15 人内归一化 | 采纳方案 A：直接使用 `bootstrap-static.form`，League-wide 归一化，零新增请求；方案 B（保留加权、全员归一）因需 652 次 element-summary 被否决 | 3.1；2.1、2.4、3.0、10 节同步 |
| P1-5 Streak 回报门槛按位置区分 | 采纳：GKP/DEF ≥ 6（≈零封），MID/FWD ≥ 5（≈一次进攻回报），进配置 `streak_min_points`（位置对象） | 3.2；第 7、8 节同步 |

评审输出要求对照：①保留整体架构——模块划分 / 数据源 / 输出 schema / 编排均未改动，仅公式、算法与口径修订；②更新评分公式——3.1 / 3.2 / 3.6；③更新首发搜索算法——第 4 节；④更新 Form 与 Streak 定义——3.1 / 3.2；⑤补充设计说明——每处修订均附决策理由，见上表落点。
