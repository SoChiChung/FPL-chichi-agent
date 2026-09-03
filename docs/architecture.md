# FPL AI Manager — 系统架构设计

> 版本：v2.0　日期：2026-08-29
> 本文件为整体架构与数据设计；决策层设计见 [decision-engine.md](decision-engine.md)，路线图见 [roadmap.md](roadmap.md)，API 资料见 [api-notes.md](api-notes.md)，未来功能见 [future-features.md](future-features.md)。

---

## 0. 设计原则

| # | 原则 | 说明 |
|---|------|------|
| P1 | 简单优先 | 无传统服务器；GitHub 即"数据库"，Vercel 只做静态展示 |
| P2 | 配置驱动 | 所有策略参数（权重/阈值/GW 分段/阵型列表）在 `config/strategy.json`，代码禁止硬编码 |
| P3 | 决策-执行分离 | 决策模块只产出建议与日志；任何 FPL 账号写操作（登录/转会/阵容提交）属于 Phase 3，只有接口与 TODO |
| P4 | 统一存储 | 历史只进一个 JSON 文件（`data/history.json`），绝不按 GW 拆文件 |
| P5 | 可审计 | 每个决策带结构化理由；未来每个执行操作都有审计日志 |
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
│  update.yml     │─────────┘   │                             │
│  (每10分钟自触发+闸门)  │──push───┐   └──────────────┬──────────────┘
└─────────────────┘         │                  │ Git push
                            │                  ▼
                            │         ┌─────────────────────┐
                            │         │  Vercel (静态前端)   │
                            └────────►│  直接读取 data/*.json │
                                      │  同源，无需后端       │
                                      └─────────────────────┘
```

- **唯一数据源**：仓库里的 JSON 文件。AI 每次运行结果都以 commit 形式沉淀，Git 天然提供版本回滚。
- **调度**：GitHub Actions 每 10 分钟自触发一次，先跑 Python 闸门 `brain/scheduler.py`（只读 `data/state.json`，不请求 FPL），按距下个 deadline 的距离分档决定是否真正唤醒"大脑"（brain）。档位：>24h → 每天北京 09:00；24h 内 → 每小时；1h 内 → 每 10 分钟；休赛期/无 deadline → 每 24h 探测，不空转。
- **展示**：Vercel 托管纯静态前端，直接 fetch 仓库内的 JSON 渲染，无 API 服务器。

### 1.2 一轮（GW）的生命周期

```
┌─ 决策阶段 ─┐     ┌─ 结算阶段 ─┐     ┌─ 执行阶段(Phase 3) ─┐
│ deadline 前 │     │ GW 结束后   │     │ 决策通过安全门后      │
│ 完整决策管线 │ ──► │ 回填实际得分 │ ──► │ 自动提交转会/阵容/队长│
│ 写 history  │     │ 排名/银行    │     │ 审计日志+次日对账     │
└─────────────┘     └────────────┘     └──────────────────────┘
```

---

## 2. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 决策引擎 | Python 3.12+，**仅标准库** | 零依赖、秒级启动；未来需要优化器可平滑加 PuLP |
| 数据存储 | 仓库内 JSON 文件 + Git | 免费、可回滚、Vercel 可直接读取 |
| 调度 | GitHub Actions 定时 + Python 闸门（`scheduler.py`） | 公开仓库免费无限分钟；闸门空跑只读本地 JSON，不刷 FPL |
| 前端 | 纯静态 HTML + 原生 JS | 需求只是展示，无框架最简单 |
| 托管 | Vercel（Git 集成自动部署） | 免费、全球 CDN、push 即上线 |
| 认证（Phase 3） | 可替换 Auth 适配器 + GitHub Secrets | FPL 登录流程曾多次变更，适配器模式保证可维护 |

**明确不引入**：数据库、消息队列、Redis、后端框架、第三方 Python 包。

---

## 3. 目录结构

```
FPL AI Manager/
├── .github/workflows/
│   ├── update.yml          # 自触发 + 闸门 + 决策流水线（当前）
│   └── execute.yml         # 自动执行流水线（Phase 3 再启用）
├── docs/                   # 所有设计文档统一目录
│   ├── architecture.md     # 本文档
│   ├── decision-engine.md  # 决策层设计（Market Consensus Strategy）
│   ├── roadmap.md          # 路线图
│   ├── api-notes.md        # FPL API 端点与字段资料
│   └── future-features.md  # 未来功能与自动执行设计
├── brain/                  # Python 引擎（零依赖）
│   ├── __main__.py         # 入口：python -m brain
│   ├── scheduler.py        # 自动更新闸门（只读 state.json，python -m brain.scheduler）
│   ├── api.py              # FPL API 客户端
│   ├── config.py           # 代码级常量（TEAM_ID/SEASON/路径），唯一入口
│   ├── data_store.py       # JSON 原子读写 + 校验
│   ├── context.py          # GW 上下文构建
│   └── strategy.py         # 决策接口骨架（Market Consensus，仅接口与 TODO）
├── config/
│   └── strategy.json       # 策略参数唯一配置（权重/阈值/阵型池等）
├── data/                   # 运行时数据（由 bot commit，勿手改）
│   ├── state.json          # 当前赛季快照
│   ├── history.json        # 统一历史（每轮决策 + 结果，单文件）
│   └── actions.json        # 操作审计日志（Phase 3 启用）
├── web/                    # Vercel 前端（纯静态）
│   ├── index.html
│   ├── app.js
│   └── style.css
├── vercel.json
└── README.md
```

**存储约定**：
- `config/`：人工维护，改动即触发 brain 重跑。
- `data/`：bot 维护，每次运行以"原子写 + commit"更新，禁止按 GW 拆分文件。
- `docs/`：所有设计文档唯一存放位置，不得散落在根目录。

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
      "market_score": 68.5,
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
    "made_this_gw": []
  }
}
```

- `market_score` 由决策引擎在 Phase 1 计算并写入，前端直接展示。
- `picks` 记录最终确认的首发/队长/副队长/替补，是前端"当前阵容"页的唯一来源。

### 4.2 `data/players.json` / `data/fixtures.json` — 缓存（当前阶段不落盘）

Phase 0 约定：bootstrap/fixtures 原始数据只在内存处理，**不落盘**，避免仓库膨胀。Phase 1 若前端需要展示全市场排名，再评估提炼缓存（字段裁剪后 ~500KB）。

### 4.3 配置

- `brain/config.py`：代码级常量（TEAM_ID、SEASON、路径、API 参数）。切换账号只改 TEAM_ID。
- `config/strategy.json`：所有策略参数（权重、阈值、GW 分段、阵型列表等）。详见 [decision-engine.md](decision-engine.md) 第 4 节。

---

## 5. 历史记录设计（`data/history.json`）

**核心约定**：全赛季唯一历史文件，每轮一个条目，单文件持续演进。前端直接 fetch 整个文件渲染时间线。

### 5.1 条目生命周期

```
决策阶段（deadline 前）：upsert 条目 → 写入 decision + notes + metrics（结果字段为 null）
结算阶段（GW 结束后）：upsert 条目 → 回填 points / rank / overall_rank
同一 GW 只会有一个条目；重复运行幂等覆盖
```

### 5.2 条目完整结构（Phase 1 起）

```json
{
  "gw": 5,
  "decision": {
    "formation": "343",
    "captain": { "id": 52, "name": "Haaland" },
    "vice": { "id": 147, "name": "Saka" },
    "starting_xi": [ { "id": 52, "name": "Haaland" } ],
    "bench": [ { "id": 19, "name": "Verbruggen" } ],
    "squad": [ "15 名球员 {id, name} 数组" ],
    "transfers": [
      { "out": { "id": 5, "name": "Saka" }, "in": { "id": 8, "name": "Palmer" } }
    ],
    "strategy_snapshot": { "tsb_weight": 0.8, "trend_weight": 0.2, "injury_threshold": 75 }
  },
  "notes": [
    { "topic": "transfer_in", "player": "Palmer", "detail": "同位置 MID 中 Market Score 最高 (45.2)" }
  ],
  "metrics": {
    "team_market_score": 812.3,
    "captain_market_score": 68.5,
    "formation_market_score": 512.3
  },
  "points": null,
  "rank": null,
  "overall_rank": null
}
```

- `squad / starting_xi / bench` 存 `{id, name}`：历史条目**自包含**，不依赖其他文件即可渲染。
- `metrics` 记录当轮市场指标快照，未来扩展 `team_xp / captain_xp / market_rank`（Phase 2+，键名预留，直接加字段即可，结构无需变更）。
- `strategy_snapshot` 记录当轮生效权重，便于复盘策略演进。

### 5.3 Phase 0 现状

当前 `data/history.json` 仅含结果字段（gw/points/rank/overall_rank），随 GW 结算写入；Phase 1 实现时在同一文件内演进为上述完整结构。

---

## 6. GitHub Actions 设计

### 6.1 `update.yml`（当前）

```yaml
on:
  schedule:
    - cron: "*/10 * * * *"   # 每 10 分钟自触发一次；是否真跑由闸门判定
  workflow_dispatch:         # 手动触发 = 闸门强制 due，更新一次
concurrency:
  group: update              # 防止并发运行互相覆盖
```

流水线：checkout → `python -m brain.scheduler`（闸门：只读本地 `data/state.json`，不请求 FPL；永远以 0 退出，skip 是正常状态）→ 闸门输出 `due=yes` 才 `python -m brain` → 有变化才 commit + push（`GITHUB_TOKEN` 同仓库权限足够，以 bot 身份提交）。

**档位规则**（scheduler.py 内置，全部锚定 `last_update`，Actions 触发抖动不会累积漂移）：

| 距下个 deadline | 节奏 |
|---|---|
| > 24h | 每天北京时间 09:00 更新一次 |
| 1h ~ 24h | 每小时更新一次 |
| ≤ 1h | 每 10 分钟更新一次 |
| deadline 已过 / 无未来 deadline | 季末/休赛期收敛：每 24h 探测一次；引擎失败限流 30 分钟重试，不空转刷 FPL |

**防循环**：brain 的 push 只写 `data/**`，本工作流无 `on.push` 触发，不会自激。

### 6.2 `execute.yml`（Phase 3 启用）

引入 Secret（FPL 凭证）、执行安全门、默认 dry-run。详见 [future-features.md](future-features.md) 自动执行模块设计。

### 6.3 免费额度

仓库需为 **GitHub 公开**：公开仓库的 Actions（含定时任务）免费且无限分钟。闸门空跑每次 ~30 秒 × 144 次/天；引擎只在该更新时运行（约 1-2 分钟/次）。私有仓库有 2000 分钟/月配额，本自触发机制不适用。

---

## 7. Vercel 部署方案

- **方案 A（主）**：Vercel Git 集成。push → 自动构建部署 `web/`；bot 每次 commit 数据 → 前端自动拿到最新 JSON。
  - `vercel.json`：`buildCommand` 在构建时把 `data/*.json` 复制到 `web/data/`，`outputDirectory` 为 `web`。
- **方案 B（降级备选）**：若嫌每次 bot push 触发重新部署，前端改为直接 fetch `raw.githubusercontent.com`（带时间戳破缓存），仅作备选。
- **前端读取**：同源 `fetch("data/state.json")`、`fetch("data/history.json")`，纯客户端渲染。

### 7.1 前端页面（单页 + 标签，中文界面）

| Tab | 内容 |
|-----|------|
| 概览 | 当前 GW、阵型、首发 XI、队长/副队长、替补、本周转会、银行、Chip 状态 |
| 决策历史 | 按 GW 时间线：决策卡 + 结构化理由 + metrics + 结果（得分/排名变化） |
| 配置 | 只读展示当前 strategy.json（便于观察策略状态） |
| 审计（Phase 3） | actions.json 操作流水表 |
