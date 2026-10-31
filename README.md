# FPL AI Manager

由 AI 管理的 Fantasy Premier League（FPL）账号。当前为 **Phase 2 决策引擎**：自动拉取数据、按 **Market Consensus Strategy**（跟随市场共识）产出阵容/阵型/队长/转会与筹码规划建议、写入状态与历史 JSON、GitHub Actions 定时更新、Vercel 静态展示。决策只产生建议，不执行任何 FPL 写操作。

设计文档统一在 [docs/](docs/)：

- [架构设计](docs/architecture.md)
- [决策层设计（Market Consensus）](docs/decision-engine.md)
- [模型决策详细说明（含 Chip 规划与保底）](docs/decision-engine.md)
- [路线图](docs/roadmap.md)
- [FPL API 资料](docs/api-notes.md)
- [未来功能与自动执行设计](docs/future-features.md)

## 架构

```
GitHub Actions (北京时间 17:00–04:00 每小时触发 + Python 闸门) ──► brain/ (Python, 仅标准库) ──► data/*.json ──► Git push ──► Vercel 前端
        ▲                                                                              │
        └──────────────────────── 读取 data/*.json（同源） ◄──────────────────────────┘
```

原始数据（bootstrap/fixtures 等）只在内存中处理，**不落盘**，仓库只保留提炼后的 `state.json` / `history.json`。

## 目录结构

```
├── .github/workflows/update.yml   # 每天北京时间 17:00–04:00 每小时触发 → 闸门判断 → 引擎 + 自动提交
├── docs/                          # 设计文档统一目录
├── brain/                         # Python 引擎（零依赖）
│   ├── __main__.py                # 入口（决策管线编排）
│   ├── scheduler.py               # 自动更新闸门（只读 state.json，决定该不该跑引擎）
│   ├── api.py                     # FPL API 客户端
│   ├── config.py                  # 代码级常量（TEAM_ID 在这里）
│   ├── data_store.py              # JSON 原子读写 + 校验
│   ├── context.py                 # GW 上下文与模型评分到 FPL 预期积分的映射
│   ├── strategy.py                # 决策接口骨架（CaptainSelector/FreeTransferProvider）
│   ├── strategy_config.py         # strategy.json 加载 + 按 GW 取权重分段
│   ├── market.py                  # Market Score 计算（TSB + 净转会归一化）
│   ├── squad_builder.py           # 空阵容时构建 15 人（两阶段贪心）
│   ├── lineup.py                  # 阵型 + 首发 XI + 替补排序
│   ├── captain.py                 # 队长 / 副队长评分
│   ├── chips.py                   # 按筹码状态与赛程规划 WC/FH/BB/TC
│   ├── transfer.py                # 五轮首发预期积分优化、免费转会与扣分转会评估
│   └── history_writer.py          # history 决策及已结束轮次的转会效果校准
├── config/
│   └── strategy.json              # 策略参数唯一配置（权重/阈值/阵型池）
├── data/
│   ├── state.json                 # 当前状态（GW/积分/排名/阵容/本轮建议）
│   └── history.json               # 每轮历史（决策 + 结果）
├── tests/
│   └── test_phase1.py             # 决策逻辑验证（python -m unittest tests.test_phase1）
├── web/                           # Vercel 静态前端
├── scripts/                       # npm 辅助脚本（start 编排、数据同步、本地静态服务器）
├── package.json                   # npm 命令入口（brain/build/start）
├── vercel.json
└── README.md
```

## 预计积分与转会策略

### 模型预计积分

预计积分来自本项目自己的球员评分模型，不直接读取 FPL 官方 `ep_next`。模型逐轮评估球员的进攻潜力、状态和赛程等特征，再按位置将模型评分映射到 FPL 积分单位。映射使用本赛季球员的实际 `points_per_game` 作为校准标签（至少出场 90 分钟且 PPG 大于 0 的球员）；它用于确定模型分数与积分的尺度，不作为官方预测的替代输入。

未来五轮的赛程数量决定该轮是否有比赛以及是否为双赛轮：无赛程计 0 场，双赛轮按两场累计。转会评估逐轮排出合法首发阵容（1 名门将，后场 3–5 人、中场 2–5 人、前锋 1–3 人），只累计首发 11 人的预计积分，并把队长预计积分再计一次；替补席不计入常规预计分。前端展示五轮合计、单轮平均和逐轮转入前后对比。

### 转会与扣分门槛

引擎比较当前阵容与候选转会后阵容的未来五轮首发及队长预计积分，并在可选范围内寻找收益最高的组合。是否转会看预计收益，而不是只看球员市场评分差。

当前 `config/strategy.json` 允许扣分转会，最多评估 2 笔额外转会。每笔 hit 扣 4 分；`minimum_hit_return_multiple` 为 2，因此每笔 hit 要求五轮校准后预计增益至少 8 分。扣除 hit 后组合仍须有正的净收益才会被推荐；未达到门槛时保留当前阵容/免费转会方案。

### 历史复盘

每轮历史保存 AI 最终推荐的转会组合、转会前后五轮逐轮预计积分，以及赛后实际积分。至少积累 3 组已结束轮次后，模型才开始用历史结果对转会收益做保守校准；校准系数限制在 0.5–1.25，且只使用当前积分模型版本的记录，避免旧模型数据直接影响新决策。历史复盘会随新轮次结算逐步积累，不代表模型会自动修改评分公式或代码。

## 快速开始（本地）

要求 Node 18+ 与 Python 3.10+（npm 仅作命令入口，本身零依赖）。

1. 首次运行执行 `npm install`（生成 lockfile，无需安装任何依赖）。
2. 在 `brain/config.py` 中修改 `TEAM_ID`（见下方说明）。
3. 在仓库根目录运行：

```bash
npm run brain        # 运行决策引擎，生成 data/state.json 与 data/history.json
npm run build        # 将 data/*.json 同步到 web/data/（模拟 Vercel 构建）
npm start            # 启动前端预览，浏览器打开 http://localhost:8000
```

> `npm start` 依次执行：`npm run brain`（拉取数据 + 决策）→ `npm run build`（同步 `data/` 到 `web/data/`）→ 启动静态服务器。任一阶段失败则**不启动服务器**并返回非零退出码，避免旧数据被当作最新数据。服务器运行期间每次请求也会自动同步数据。

> 新账号（GW1/GW2 无阵容、当前 GW 无 picks 时）：自动向前搜索最近有 picks 的 GW（auto-pick/历史阵容）作为基础阵容；完全找不到时执行 **establish**（按 Market Score 生成 15 人阵容）。新号初期转会次数显示为"暂不受限"（unlimited），不会用大整数伪装。

## 如何找到 TEAM_ID

登录 FPL 官网，打开任意包含自己队伍内容的页面（如「My Team」），URL 形如：

```
https://fantasy.premierleague.com/entry/123456/event/1
```

`/entry/` 与 `/event/` 之间的数字（如 `123456`）就是 TEAM_ID。未来切换账号只需修改 `brain/config.py` 中的这一个值。

## GitHub Actions（自动更新）

自动更新由 GitHub Actions 驱动，**不需要 Vercel 参与调度**。仓库需为 **GitHub 公开**（公开仓库的 Actions 定时任务免费且无限分钟；私有仓库有月度分钟配额限制）：

- `update.yml` 每天北京时间 17:00 至次日 04:00 每小时触发一次，先运行只读 `data/state.json` 的 Python 闸门 `brain/scheduler.py`（不请求 FPL API）；
- 闸门判定"该更新"才运行 `python -m brain` 并 push 数据，Vercel 随 push 自动重部署静态站；
- 闸门确保两次计算至少间隔 1 小时；有无未来 deadline 都遵循该时段的小时计算频率；
- 也可在 Actions 页面手动触发（Workflow dispatch，强制更新一次）。

> 注意：Actions 首次运行前请确认 `TEAM_ID` 已配置；未配置时运行会失败且不产生提交，这是预期行为。

## Vercel 部署

1. Vercel 控制台 → Import 本仓库（无需登录鉴权，公开仓库即可）。
2. Framework 选择 **Other**，其余保持默认（`vercel.json` 已配置构建命令）。
3. 部署完成后，`data/*.json` 会在构建时自动复制到静态目录，前端直接读取。

自动更新无需在 Vercel 配置任何环境变量：Actions 每次 push `data/` 后，Vercel 会按 Git 集成自动重新部署。

## 阶段规划

| 阶段 | 状态 | 内容 |
|------|------|------|
| Phase 0 | ✅ | 数据获取、state/history 生成、Actions、Vercel 展示、决策骨架 |
| Phase 1 | ✅ 当前（2026-08-30） | Market Consensus 决策：Market Score 评分、阵容/阵型/队长（CaptainSelector）、转会建议（Market Gap + 伤病）、决策日志（metrics）、前端"本轮建议" |
| Phase 2 | ✅ 部分完成 | 自有模型预计积分、五轮转会评估与历史效果校准、筹码规划；通知与离线回测待做 |
| Phase 3 | 预留 | 自动登录与自动执行（FreeTransferProvider 主来源、安全门与审计） |

## 常见问题

- **Actions 运行报 403/429**：FPL API 偶发限流，等待下一次定时运行或手动触发重试即可；必要时可在仓库 Settings → Secrets 中配置代理环境变量（本项目暂不依赖）。
- **数据长时间不更新**：检查 Actions 运行日志是否失败，以及 `data/` 是否有新的 commit。
