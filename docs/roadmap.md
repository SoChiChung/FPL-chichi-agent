# FPL AI Manager — 路线图

> 更新：2026-08-29

## 当前状态

| 项目 | 状态 |
|------|------|
| Phase 0 骨架 | ✅ 完成（commit `e33529a`，已推送 GitHub `SoChiChung/FPL-chichi-agent`） |
| 数据管线 | ✅ 拉取 bootstrap/fixtures/entry/history/picks → 生成 `state.json` / `history.json`，原始数据不落盘 |
| 前端 | ✅ 纯静态中文界面（概览 / 阵容 / 历史表格） |
| GitHub Actions | ✅ `update.yml` 每天北京时间 17:00 至次日 04:00 每小时触发 + `scheduler.py` 闸门（有无 deadline 都至少间隔 1 小时）；手动触发 = 强制更新；**TEAM_ID 未配置时失败但不提交，属预期** |
| Vercel | ✅ 已部署（纯静态托管，随 push 自动重部署；无需任何环境变量） |
| 决策骨架 | ✅ 接口占位（`brain/strategy.py` + `config/strategy.json`），无业务逻辑 |
| 决策设计 | ✅ [decision-engine.md](decision-engine.md) v2.0（Market Consensus Strategy）待确认 |

## 阶段路线

| 阶段 | 内容 | 验收标准 |
|------|------|----------|
| **Phase 0** | 数据获取、状态保存、历史记录、Vercel 展示、决策骨架 | 每轮数据自动更新；前端完整渲染；决策模块仅有接口与 TODO |
| **Phase 1** | 决策引擎：Market Score 评分、阵容构建、阵型、队长（CaptainSelector）、转会（Market Gap + 伤病）、决策日志（metrics） | 按 [decision-engine.md](decision-engine.md) 第 12 节验收清单 |
| **Phase 2（当前）** | 筹码策略已完成；其余：metrics 图表、通知、回测、xP API 接入、出售价规则修正 | 筹码决策有赛程依据并可追踪 |
| **Phase 3** | 自动执行：Auth 适配器、FreeTransferProvider 主来源（my-team API）、安全门 G1–G7、审计对账；先 dry-run 后真执行 | 连续 N 轮执行零失误，审计可完整对账 |
| **Phase 4** | 赛季学习：写盘分析、自适应权重、Ownership v2 / EO / Differential | 决策质量量化可追踪，策略每赛季迭代 |

## 当前待办

1. 用户确认 [decision-engine.md](decision-engine.md)（特别是 Market Gap 阈值 15 与权重分段）
2. 用户创建新 FPL 账号 → 提供 TEAM_ID → 填入 `brain/config.py` → Vercel 导入部署
3. 进入 Phase 1 实现（按决策文档第 13 节顺序）

## 明确不做（当前阶段）

- 自动登录、自动转会提交、自动阵容/队长提交（Phase 3 前仅接口）
- xG / xA / EV / Monte Carlo / ML / 复杂优化器
- 自动执行等 Phase 3 内容（本阶段只产生建议，不提交到 FPL）
