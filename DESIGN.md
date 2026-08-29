# FPL AI Manager — 系统设计文档

> 版本：v1.0　日期：2026-08-29　状态：待确认
> 目标：为 AI 提供一个完全自主运营的 Fantasy Premier League（FPL）账号，覆盖数据获取、决策、历史记录，并为自动执行预留完整设计。

---

## 0. 设计原则

| # | 原则 | 说明 |
|---|------|------|
| P1 | 简单优先 | 无传统服务器；GitHub 即"数据库"，Vercel 只做静态展示 |
| P2 | 配置驱动 | 所有策略（Ownership、风险、Chip 计划）放独立 JSON 配置，代码零硬编码规则 |
| P3 | 决策-执行分离 | 本阶段只"决策 + 记录"；"执行"是独立模块，通过安全门后才生效 |
| P4 | 统一存储 | 历史只进一个 JSON 文件（`decision_history.json`），绝不按 GW 拆文件 |
| P5 | 可审计 | 每个决策都带结构化理由与信心值；未来每个执行操作都有审计日志 |
| P6 | 零依赖 | Python 只用标准库（urllib/json），GitHub Actions 只用一个官方 action |

---

## 1. 项目架构

### 1.1 总体数据流

```
┌──────────────────────┐         ┌─────────────────────────────┐
│   FPL 官方 API       │         │   GitHub Repository         │
│  (public + auth)     │◄────┐   │   （仓库 = 唯一数据源）      │
└──────────────────────┘     │   │                             │
        ▲                    │   │  config/   策略配置(人可改)  │
        │ fetch             │   │  data/     state/历史/缓存    │
┌───────┴─────────┐         │   │                             │
│  GitHub Actions │         │   │  brain/    Python 决策引擎   │
│  brain.yml      │─────────┘   │                             │
│  (定时 Cron)     │──push───┐   └──────────────┬──────────────┘
└─────────────────┘         │                  │ Git push
                            │                  ▼
                            │         ┌─────────────────────┐
                            │         │  Vercel (静态前端)   │
                            └────────►│  直接读取 data/*.json │
                                      │  同源，无需后端       │
                                      └─────────────────────┘
```

- **唯一数据源**：仓库里的 JSON 文件。AI 的每次运行结果都以 commit 形式沉淀，Git 天然提供版本回滚。
- **调度**：GitHub Actions Cron 定时唤醒"大脑"（brain）。
- **展示**：Vercel 托管纯静态前端，直接 fetch 仓库内的 JSON 渲染，无 API 服务器。

### 1.2 一轮（GW）的生命周期

大脑每次被唤醒时，根据 FPL 官方数据的 `event` 状态自行判断处于哪个阶段：

```
┌─ 结算阶段 ─┐     ┌─ 决策阶段 ─┐     ┌─ 执行阶段(Phase 3) ─┐
│ GW 已结束   │     │ 临近 deadline│     │ 决策通过安全门后      │
│ 拉取实际得分 │ ──► │ 完整决策管线  │ ──► │ 自动提交转会/阵容/队长│
│ 排名/队长分 │     │ 写历史+信心值 │     │ 审计日志+次日对账     │
└────────────┘     └────────────┘     └──────────────────────┘
```

---

## 2. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 决策引擎 | Python 3.12+，**仅标准库**（urllib/json/argparse） | 任务轻量；零依赖避免供应链风险、秒级启动；未来如需优化器可平滑加 PuLP |
| 数据存储 | 仓库内 JSON 文件 + Git | 免费、可回滚、Vercel 可直接读取 |
| 调度 | GitHub Actions Cron（免费 2000 分钟/月） | 无需任何服务器；分钟级任务用不了多少额度 |
| 前端 | 纯静态 HTML + 原生 JS（无框架） | 需求只是展示，无框架最简单 |
| 托管 | Vercel（Git 集成自动部署） | 免费、全球 CDN、push 即上线 |
| 认证（Phase 3） | 可替换的 Auth 适配器 + GitHub Secrets | FPL 登录流程曾多次变更（2025-26 赛季调整为 SSO 流程），适配器模式保证可维护 |

**明确不引入**：数据库、消息队列、Redis、任何后端框架、任何第三方 Python 包。

---

## 3. 目录结构

```
FPL AI Manager/
├── .github/workflows/
│   ├── brain.yml          # 定时决策流水线（本阶段）
│   └── execute.yml        # 自动执行流水线（Phase 3 再启用）
├── brain/                 # Python 决策引擎（零依赖）
│   ├── __main__.py        # 入口：python -m brain [--dry-run]
│   ├── api.py             # FPL API 客户端（fetch + 缓存 + ETag）
│   ├── data_store.py      # 统一 JSON 存储层（读/写/校验）
│   ├── context.py         # GW 上下文构建（当前队伍、deadline、赛程）
│   ├── scorer.py          # 球员未来积分投影
│   ├── ownership.py       # Ownership 策略应用（读 ownership-rules.json）
│   ├── optimizer.py       # 阵容/阵型/队长/转会优化（v1 启发式）
│   ├── explainer.py       # 结构化决策理由生成
│   ├── safety.py          # 校验 + 信心值计算 + 执行门槛（Phase 3 启用）
│   └── config.py          # 配置加载与默认值合并
├── config/                # 策略配置（人可编辑；push 触发重决策）
│   ├── ownership-rules.json   # Ownership 策略（核心可调项）
│   └── strategy.json          # 赛季级策略（阵型池、Chip 计划、信心阈值）
├── data/                  # 运行时数据（由 bot commit，勿手改）
│   ├── state.json             # 当前赛季快照
│   ├── players.json           # 球员数据缓存（提炼后，非原始 dump）
│   ├── fixtures.json          # 赛程缓存 + 每队 FDR
│   ├── actions.json           # 操作审计日志（Phase 3 启用，先占位）
│   └── history/
│       └── decision_history.json   # 统一历史：全赛季每轮决策 + 结果
├── web/                   # Vercel 前端（纯静态）
│   ├── index.html
│   ├── app.js
│   └── style.css
├── vercel.json
└── README.md
```

**存储约定**：
- `config/`：人工维护，改动即触发 brain 重跑。
- `data/`：bot 维护，每次运行以"原子写 + commit"更新，禁止按 GW 拆分文件。
- 仓库体积估算：`players.json` ≈ 400–600KB、`fixtures.json` ≈ 50KB、历史文件全年增长 < 200KB，一个赛季总增量 < 2MB，远低于 GitHub 免费仓库限制。

---

## 4. 数据结构设计

### 4.1 `data/state.json` — 当前状态（单文件覆盖式）

```json
{
  "season": "2026-27",
  "manager_id": 123456,
  "last_update": "2026-08-29T06:00:00Z",
  "current_gw": 4,
  "next_deadline": "2026-08-30T17:30:00Z",
  "bank": 1.2,
  "points": 210,
  "rank": 85000,
  "chips": {
    "wildcard": "available",
    "free_hit": "available",
    "bench_boost": "available",
    "triple_captain": "available"
  },
  "squad": [
    {
      "id": 52, "name": "Haaland", "pos": "FWD", "team": "MCI",
      "price": 15.1, "selected_by": 42.3,
      "form": 7.2, "total_points": 31,
      "proj_next_5": 31.5, "fdr_next_5": 2.2,
      "news": "", "chance_playing": 100
    }
  ],
  "picks": {
    "formation": "343",
    "captain": 52,
    "vice": 147,
    "starting_xi": [52, 147, 31, 88, 12, 5, 77, 9, 3, 41, 62],
    "bench": [19, 24, 55, 70]
  },
  "transfers": {
    "free_available": 2,
    "made_this_gw": [
      {"out": 5, "in": 8, "out_name": "Watkins", "in_name": "Isak", "cost": 0, "at": "2026-08-28T09:00:00Z"}
    ]
  }
}
```

- `squad` 里每个球员附带**大脑算好的投影**（`proj_next_5`、`fdr_next_5`），前端直接展示，无需二次计算。
- `picks` 记录最终确认的首发/队长/副队长/替补，是前端"当前阵容"页的唯一来源。

### 4.2 `data/players.json` — 球员数据缓存

从 FPL 官方 `bootstrap-static` **提炼**后存入（不存原始 dump，控制仓库体积）：

```json
{
  "updated": "2026-08-29T06:00:00Z",
  "players": [
    {
      "id": 52, "name": "Haaland", "pos": "FWD", "team": "MCI", "price": 15.1,
      "selected_by": 42.3, "form": 7.2, "ppg": 6.8, "total_points": 31,
      "minutes": 360, "goals": 4, "assists": 1, "clean_sheets": 0, "bonus": 8,
      "ict": 28.5, "news": "", "chance_playing": 100,
      "proj_next_5": 31.5, "fdr_next_5": 2.2, "transfers_in": 1000, "transfers_out": 200
    }
  ]
}
```

仅保留决策和展示必需的字段，全联盟约 600+ 名球员，总大小控制在 ~500KB。

### 4.3 `data/fixtures.json` — 赛程缓存

```json
{
  "updated": "2026-08-29T06:00:00Z",
  "fixtures": [
    {"id": 1, "gw": 5, "team_h": "NEW", "team_a": "MUN", "kickoff": "2026-09-05T16:30:00Z", "finished": false}
  ],
  "team_fdr": {
    "NEW": {"next_5_avg": 2.1, "next_5": [2, 2, 3, 2, 1]}
  }
}
```

### 4.4 配置文件

见第 6、7 节（`config/strategy.json`、`config/ownership-rules.json`）。

---

## 5. 历史记录设计（`data/history.json`）

**核心约定**：全赛季唯一历史文件，每轮在 `history` 数组中追加一个元素（结算阶段回填 `result`）。前端直接 fetch 整个文件渲染时间线。

> 命名说明：Phase 0 落地文件名为 `data/history.json`（精简字段：gw/points/rank/overall_rank）。Phase 1 增加决策字段（下文完整结构，含 reasoning/confidence/result）时直接在**同一文件**内演进，保持单文件原则，不需要改名。

```json
{
  "season": "2026-27",
  "history": [
    {
      "gw": 5,
      "timestamp": "2026-09-05T17:30:00Z",

      "decision": {
        "formation": "343",
        "captain":  {"id": 52, "name": "Haaland"},
        "vice":     {"id": 147, "name": "Saka"},
        "starting_xi": [52, 147, 31, 88, 12, 5, 77, 9, 3, 41, 62],
        "bench": [19, 24, 55, 70],
        "transfers": {
          "made": [{"out": {"id": 5, "name": "Watkins"}, "in": {"id": 8, "name": "Isak"}, "cost": 0}],
          "considered_but_rejected": []
        },
        "chips_used": null
      },

      "reasoning": {
        "summary": [
          "纽卡未来5轮赛程明显改善(FDR均2.1 vs 维拉3.0)",
          "Isak 未来5轮预计积分 30.2 分，高于 Watkins 的 23.8 分",
          "售出 Watkins 触发低风险确认：其持有率 9.2%，低于保护线 25%"
        ],
        "transfer_in": [
          {"player": "Isak", "weight": 0.82,
           "reasons": ["纽卡赛程改善", "预计未来5轮积分更高", "持有率 8.4%，符合当前 differential 偏好"]}
        ],
        "transfer_out": [
          {"player": "Watkins", "weight": 0.71,
           "reasons": ["未来5轮赛程偏难", "出场时间近3轮下滑", "预计积分被 Isak 反超 6.4 分"]}
        ],
        "captain": [
          {"player": "Haaland", "weight": 0.90,
           "reasons": ["主场对阵升班马，预计得分期望最高", "对手主力中卫伤停"],
           "alternatives": [{"player": "Saka", "projection": 8.1}]}
        ],
        "formation": [
          {"choice": "343", "projected_total": 68.2,
           "reasons": ["三名前锋均有高分投影", "中卫替补深度不足"]}
        ],
        "ownership": [
          {"note": "balanced 模式，未触发高持有保护线", "adjustments": 0}
        ]
      },

      "confidence": {
        "overall": 82,
        "captain": 90,
        "transfers": 75,
        "formation": 80
      },

      "executed": {
        "transfers": false,
        "lineup": false,
        "captain": false,
        "note": "Phase 1 仅决策不执行"
      },

      "result": {
        "points": 85,
        "points_on_bench": 12,
        "captain_points": 14,
        "projected_points": 68.2,
        "rank": 132000,
        "rank_change": -15000,
        "bank": 1.2,
        "hits_taken": 0
      }
    }
  ]
}
```

**设计要点**：
- `reasoning` 是**结构化**的（不是散文），每条带 `weight`（证据权重），前端可以按主题折叠展开：买入/卖出/队长/阵型/Ownership。
- `confidence` 分维度给出，本阶段用于展示，Phase 3 作为执行安全门输入。
- `result` 在 GW 结束后由结算阶段回填；`projected_points` vs `points` 形成"预测 vs 实际"数据，供第 4 阶段做写盘分析（write-off analysis）和学习调参。
- `executed` 字段为 Phase 3 预留，本阶段恒为 false。

### 5.1 操作审计文件（`data/actions.json`，Phase 3 启用）

独立于决策历史，只记**实际对 FPL 账号执行过的操作**：

```json
{
  "actions": [
    {
      "at": "2026-09-05T17:00:00Z", "gw": 5,
      "action": "transfer", "payload": {"out": 5, "in": 8},
      "api_response": "success", "success": true,
      "risky": false, "confidence_at_execution": 82
    }
  ]
}
```

原则：**决策历史 ≠ 操作历史**。决策可以随意回看，操作必须可审计、可对账。

---

## 6. 决策系统设计

### 6.1 决策管线（每次决策运行按序执行）

```
数据层:  api.py 拉取 bootstrap / fixtures / entry / picks
   ↓
上下文:  context.py 组装 GW 上下文（当前队伍、银行、deadline、赛程、持有率）
   ↓
策略加载: config.py 加载 ownership-rules.json + strategy.json → 权重/阈值
   ↓
候选生成: 卖出候选（低投影/伤停/失宠） + 买入候选（各位置按投影排序，受预算和同队≤3限制）
   ↓
评分:     scorer.py 对每个球员算 proj_next_5（未来5轮积分期望）
          ownership.py 按当前 Ownership 模式对投影做调整
   ↓
优化:     optimizer.py 在候选阵型池里找最优首发 XI + 最优 1-2 笔转会（含 hit 成本）
   ↓
队长/副队长: 最高投影期望 + 波动率与 EO 调整
   ↓
Chip 检查: 按 strategy.json 的 chip_plan 判断是否动用（如 WC/BB/TC）
   ↓
解释:     explainer.py 为每个决策生成结构化理由（写入 history）
   ↓
信心值:   safety.py 综合 margin/数据新鲜度/伤病风险 → 0-100
   ↓
持久化:   更新 state.json + 追加 decision_history.json → commit
```

### 6.2 评分模型（`scorer.py`，v1 启发式）

```
proj_pts(player, gw) = base_score × fixture_factor × minutes_factor × ownership_factor

base_score      = 加权(form, ppg, 位置特有指标, 近3场趋势)
fixture_factor  = 由 FDR 与主客场强度(strength_attack/defence)得出
minutes_factor  = chance_playing / 出场时间趋势（伤停、轮换风险）
ownership_factor= ownership.py 输出（见第 7 节）
```

所有权重均为 `strategy.json` 中的可调参数，调整策略 = 改配置，不改代码。

### 6.3 优化器（`optimizer.py`）

- **v1 用贪心 + 枚举**，不引入求解器：在阵型池（默认 `["343","352","442","433"]`）中枚举每个阵型下按投影排序的最优 XI（受同队≤3、预算约束），取总分最高。
- **转会**：比较"当前阵容投影"与"最优可达成阵容投影"，净收益必须覆盖 hit 成本（`hit_cost` 默认 -4）；最多评估 2 笔转会。
- **队长**：`max(E[pts] + aggression × 波动率调整 − EO 惩罚)`，`aggression` 与 EO 权重全部来自配置。
- 优化器模块保留接口，Phase 4 若需要精确解可无痛替换为 MILP（PuLP）。

### 6.4 信心值（`safety.py`）

```
confidence = 100
  − margin_penalty     （最优与次优差距小 → 扣分；差距按投影归一化）
  − news_penalty       （chance_playing < 100 或有伤病新闻的球员数量）
  − freshness_penalty  （数据距今超过 X 小时）
  − volatility_penalty （评分分布标准差过大）
  − ownership_conflict （触发 hot-player 保护 → 强制扣分并标记 risky）
clamp 到 [0, 100]
```

- 本阶段：信心值随决策写入 history，供前端展示与人工观察。
- Phase 3：各操作类型有独立执行门槛（见第 8 节安全门 G2）。

### 6.5 Chip 策略（本阶段为配置驱动的简单规则）

`strategy.json` 中的 `chip_plan` 声明计划（如"WC 第 10 轮附近、BB 第 32 轮附近、TC 第 24 轮附近"，支持 `"player": "auto"`），大脑在对应 GW 评估是否触发并生成理由。复杂动态 chip 决策留到 Phase 4。

---

## 7. Ownership Strategy 设计（`config/ownership-rules.json`）

**要求：持有率是重要因素，但规则绝不写死在代码里。** 全部参数由配置文件控制。

```json
{
  "version": 1,
  "mode": "balanced",
  "mode_plan": [
    {"gw_from": 1, "mode": "balanced"},
    {"gw_from": 15, "mode": "differential"},
    {"gw_from": 32, "mode": "balanced"}
  ],

  "modes": {
    "high_ownership": {
      "label": "跟随主流",
      "weight": 0.5,
      "high_owned_bonus": 0.12,
      "min_share": 20,
      "note": "持有率≥20%的球员投影加成12%；规避极端冷门"
    },
    "differential": {
      "label": "追求差异",
      "weight": 0.5,
      "low_owned_bonus": 0.15,
      "max_share": 15,
      "note": "持有率≤15%的球员投影加成15%；高持有球员自动降权"
    },
    "balanced": {
      "label": "均衡",
      "weight": 0.3,
      "cap_share": 40,
      "note": "超过40%的超高持有球员小幅降权，其余不干预"
    }
  },

  "eo": {
    "enabled": true,
    "captain_eo_weight": 0.2,
    "max_captain_eo": 150,
    "note": "EO=持有率×倍率(队长×2)。队长选择权衡自有加分与EO稀释"
  },

  "hot_player": {
    "protect_share": 25,
    "protect_reason_min_weight": 0.7,
    "note": "卖出持有率≥25%的球员，必须有证据权重≥0.7的理由，否则拒绝并标记 risky"
  },

  "risk": {
    "risk_aversion": 0.5,
    "max_volatility": 0.8,
    "note": "0=激进 1=保守；影响队长与买入的波动率惩罚"
  }
}
```

**如何生效**：
- `ownership.py` 读取当前生效 mode（`mode_plan` 支持**赛季内自动切换**，如赛季中段转 differential 冲排名）。
- 计算 `ownership_factor` 并对 `proj_pts` 做乘法调整；`eo` 影响队长选择；`hot_player` 影响转会安全门。
- 未来调整策略 = 只改这个 JSON（例如"高持有加成从 12% 提到 20%"），commit 后 Actions 自动重跑。

---

## 8. 自动执行模块设计（Phase 3，本阶段只做设计）

### 8.1 登录与会话

```
┌─ Auth 适配器（可替换）────────────────────────────┐
│  FPLAuthProvider (interface)                     │
│   ├─ login(email, password) → session            │
│   ├─ get_session() → 从 Secret 恢复               │
│   └─ refresh() → 会话过期时自动重登               │
│  现行基准实现（需在开发时验证最新流程）：           │
│  POST users.premierleague.com/accounts/login/    │
│    form: login / password / app=plfpl-web /      │
│          redirect_uri=…                          │
│  会话 = Cookie: pl_profile（长有效期）            │
└──────────────────────────────────────────────────┘
```

- **会话保存**：凭证绝不进仓库。方案：
  - 手动方式：登录一次后把 `pl_profile` Cookie 字符串存入 GitHub Secret `FPL_SESSION_COOKIE`，大脑直接复用（最简单、最稳）。
  - 自动方式：存 `FPL_EMAIL` / `FPL_PASSWORD` 两个 Secret，会话过期时自动重登刷新。
- **警示**：FPL 登录流程历史上多次变更（2025-26 赛季已调整为新的 SSO 流程），适配器模式保证换流程时只改一个文件；开发该模块时必须先验证官方当前认证流程。

### 8.2 自动提交的动作

| 动作 | API | 说明 |
|------|-----|------|
| 转会 | `POST /api/my-team/{id}/transfers` | 单笔/双笔；扣分与 hit 逻辑由 FPL 端结算 |
| 阵容/队长 | `POST /api/my-team/{id}/picks` | 提交首发 XI + 队长 + 副队长 + 替补排序 |
| 核对 | `GET /api/my-team/{id}`（鉴权） | 提交前读取真实当前状态，提交后校验结果 |

### 8.3 防止误操作 — 安全门（执行前必须全部通过）

```
G1 时间门   距 deadline ≥ 15 分钟（可配），否则不执行
G2 信心门   各类操作分别满足阈值（如转会≥70、队长≥80，来自 strategy.json）
G3 合法性门 预检：银行≥0（含出售溢价）、位置数合法、同队≤3、
            Chip 可用性、自由转会额度
G4 热球员保护 卖出持有率 ≥ protect_share(25%) 的球员 → 必须证据权重达标，
            否则拒绝；通过则操作标记 "risky": true 入审计
G5 幂等门   POST 前先 GET 真实当前状态，只提交差异；无差异则跳过
G6 试运行门 默认 dry_run=true：只把"将执行的操作"写入决策历史，
            不调 API；人工确认运行经验后才允许关闭
G7 审计门   每个操作写入 actions.json（时间/载荷/响应/是否 risky），
            下一轮结算时对账：转会是否生效、队长是否设置成功
```

**部分失败处理**：步骤按序执行、逐步校验；任一步失败即中止后续步骤、保留已成功步骤，并在 history 与 actions.json 中标记 `partial_failure`，等待人工处理（不自动重试，防止重复扣分）。

**回滚**：同 GW 内在 deadline 前可执行反向转会恢复（代价 = 4 分 hit），由人工确认后触发；不提供自动回滚。

---

## 9. GitHub Actions 设计

### 9.1 `brain.yml`（本阶段启用）

```yaml
on:
  schedule:                    # 每日 3 次（UTC），避开整点
    - cron: "23 3,15,21 * * *"
  workflow_dispatch:           # 手动触发（改配置后调试用）
  push:
    paths: ["config/**"]       # 改策略配置 → 自动重决策
concurrency:
  group: brain                 # 防止并发运行互相覆盖
```

流水线步骤：
1. `actions/checkout@v4`
2. `python -m brain`（环境自带 Python；无依赖安装步骤）
3. 校验：JSON 均通过 schema 校验（脚本内置断言，失败则 abort 且不提交）
4. commit + push（`GITHUB_TOKEN`，同仓库 contents:write 足够；以 bot 身份提交）

**防循环**：brain 的 push 只写 `data/**`，而 `on.push` 只监听 `config/**`，因此不会互相触发无限循环。

**执行调度说明**：FPL 大部分 GW 为周五截止，少数周中赛程；每日 3 次运行覆盖结算（GW 结束后首次运行自动进入结算阶段）与决策（临近 deadline 的运行自动进入决策阶段），大脑自己根据官方数据判断，无需人工指定。

### 9.2 `execute.yml`（Phase 3 启用）

- 与 brain.yml 同源；区别：引入 Secret（`FPL_SESSION_COOKIE` / `FPL_EMAIL` / `FPL_PASSWORD`）、执行安全门 G1–G7、默认 dry-run。
- 执行只发生在"决策运行中，当前阶段 = 执行阶段"时。

### 9.3 免费额度评估

每次运行 ~2 分钟 × 3 次/天 × 38 周 ≈ 4.8 小时/年，远低于 2000 分钟/月免费额度，**零成本**。

---

## 10. Vercel 部署方案

- **方案 A（主）**：Vercel Git 集成。仓库 push → Vercel 自动构建部署 `web/`（静态文件，无构建步骤，秒级完成）。bot 每次 commit 数据 → 前端自动拿到最新 JSON。
  - `vercel.json`：仅声明 `outputDirectory: "web"` 等最小配置。
- **方案 B（降级备选）**：若嫌每次 bot push 都触发重新部署，前端改为直接 fetch `raw.githubusercontent.com/<repo>/<branch>/data/xxx.json`（带时间戳参数破缓存）。代价：依赖第三方 CDN、可能踩 raw 的缓存/限流，仅作备选。
- **前端读取**：同源 `fetch("/data/state.json")`、`fetch("/data/history/decision_history.json")` 等，纯客户端渲染。

### 10.1 前端页面（单页 + 标签，中文界面）

| Tab | 内容 |
|-----|------|
| 概览 | 当前 GW、阵型、首发 XI（位置图）、队长/副队长、替补、本周转会、银行、Chip 状态、本轮信心值条 |
| 决策历史 | 按 GW 时间线：决策卡（阵型/队长/转会）+ 可折叠的结构化理由 + 信心值 + 结果（得分、排名变化、队长实际得分 vs 投影） |
| 配置 | 只读展示当前 ownership-rules.json 与 strategy.json（便于观察策略状态） |
| 审计（Phase 3） | actions.json 操作流水表 |

---

## 11. 后续开发路线图

| 阶段 | 内容 | 验收标准 |
|------|------|----------|
| **Phase 1（当前）** | 数据获取、决策管线、统一历史记录、配置文件、brain.yml、前端 v1 | 每 GW 有完整决策 + 理由 + 信心值；历史单文件可被前端完整渲染 |
| **Phase 2** | 前端增强（积分趋势/排名变化/投影 vs 实际图表）；通知（Telegram/邮件，可配）；离线回测（用历史赛季数据验证 scorer/optimizer 参数） | 参数有数据支撑；异常（伤停、deadline 变化）能及时通知 |
| **Phase 3** | 自动执行：Auth 适配器、安全门 G1–G7、审计对账；先 dry-run 一个赛季后开启真执行 | 连续 N 轮执行零失误、审计可完整对账 |
| **Phase 4** | 赛季学习：写盘分析（哪类决策最亏分）；自适应 Ownership 模式切换；价格变动预测；动态 Chip 决策；优化器升级（可选 MILP） | 决策质量量化可追踪，策略每赛季迭代 |

---

## 附录 A：FPL API 端点清单

| 端点 | 鉴权 | 用途 |
|------|------|------|
| `GET /api/bootstrap-static/` | 无 | 球员/球队/Event 静态数据（含持有率） |
| `GET /api/fixtures/` | 无 | 全赛季赛程 |
| `GET /api/entry/{id}/` | 无 | 队伍信息、总积分、总排名 |
| `GET /api/entry/{id}/history/` | 无 | 每轮积分/排名/银行/chips 历史 |
| `GET /api/entry/{id}/event/{gw}/picks/` | 无 | 指定轮次首发/队长/替补 |
| `GET /api/event/{gw}/live/` | 无 | 实时分数（结算阶段核对） |
| `GET /api/my-team/{id}/` | 需要 | 真实当前阵容/银行/Chips（执行前提） |
| `POST /api/my-team/{id}/transfers` | 需要 | 提交转会（Phase 3） |
| `POST /api/my-team/{id}/picks` | 需要 | 提交阵容/队长（Phase 3） |
| `GET /api/me/` | 需要 | 获取 manager_id（Phase 3 初始化） |

## 附录 B：数据频率与合规

- 公开端点建议拉取频率 ≤ 每小时 1 次（本项目实际每 6–8 小时一次，远低于此）；利用 `bootstrap-static` 的版本号/更新时间为空时跳过重复拉取。
- 鉴权端点仅在执行阶段调用，且每次调用都写入审计。
- 遵守 FPL 服务条款，避免高频轮询与多账号行为；本项目为单账号、低频调用，风险可控。

## 附录 C：参考资料（FPL 登录方式现状）

- [FPL 登录认证说明（Stack Overflow）](https://stackoverflow.com/questions/62828619/how-to-login-in-fantasy-premier-league-using-python)
- [pyfpl 认证文档（fpl 包）](https://fpl.readthedocs.io/en/latest/_sources/classes/fpl.rst.txt)
- [FPL API Python 封装（HaydenMacDonald/fpl）](https://github.com/HaydenMacDonald/fpl)

> 注意：FPL 的登录流程在 2025-26 赛季起有调整（SSO 化），附录 C 资料为经典流程基线，Phase 3 开发时必须按官方最新认证流程验证。
