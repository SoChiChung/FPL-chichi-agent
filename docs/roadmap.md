# FPL AI Manager — 路线图

> 更新：2026-08-29

## 当前状态

| 项目 | 状态 |
|------|------|
| Phase 0 骨架 | ✅ 完成（commit `e33529a`，已推送 GitHub `SoChiChung/FPL-chichi-agent`） |
| 数据管线 | ✅ 拉取 bootstrap/fixtures/entry/history/picks → 生成 `state.json` / `history.json`，原始数据不落盘 |
| 前端 | ✅ 纯静态中文界面（概览 / 阵容 / 历史表格） |
| GitHub Actions | ✅ `update.yml` 已激活（每日 03:23/15:23/21:23 UTC + 手动触发）；**TEAM_ID 未配置时失败但不提交，属预期** |
| Vercel | ⏳ 未部署（等新 FPL 账号 + TEAM_ID 填入后导入） |
| 决策骨架 | ✅ 接口占位（`brain/strategy.py` + `config/strategy.json`），无业务逻辑 |
| 决策设计 | ✅ [decision-engine.md](decision-engine.md) v2.0（Market Consensus Strategy）待确认 |

## 阶段路线

| 阶段 | 内容 | 验收标准 |
|------|------|----------|
| **Phase 0（当前）** | 数据获取、状态保存、历史记录、Vercel 展示、决策骨架 | 每轮数据自动更新；前端完整渲染；决策模块仅有接口与 TODO |
| **Phase 1** | 决策引擎：Market Score 评分、阵容构建、阵型、队长（CaptainSelector）、转会（Market Gap + 伤病）、决策日志（metrics） | 按 [decision-engine.md](decision-engine.md) 第 12 节验收清单 |
| **Phase 2** | 前端增强（决策历史渲染、metrics 图表）；通知（Telegram/邮件）；回测（历史赛季验证参数）；xP API 接入（替换 CaptainSelector 实现）；出售价规则修正 | 参数有数据支撑；异常能及时通知 |
| **Phase 3** | 自动执行：Auth 适配器、FreeTransferProvider 主来源（my-team API）、安全门 G1–G7、审计对账；先 dry-run 后真执行 | 连续 N 轮执行零失误，审计可完整对账 |
| **Phase 4** | 赛季学习：写盘分析、自适应权重、Chip 策略（Wildcard/FH/BB/TC）、Ownership v2 / EO / Differential | 决策质量量化可追踪，策略每赛季迭代 |

## 当前待办

1. 用户确认 [decision-engine.md](decision-engine.md)（特别是 Market Gap 阈值 15 与权重分段）
2. 用户创建新 FPL 账号 → 提供 TEAM_ID → 填入 `brain/config.py` → Vercel 导入部署
3. 进入 Phase 1 实现（按决策文档第 13 节顺序）

## 明确不做（当前阶段）

- 自动登录、自动转会提交、自动阵容/队长提交（Phase 3 前仅接口）
- xG / xA / EV / Monte Carlo / ML / 复杂优化器
- Chip 策略（Wildcard / Free Hit / Bench Boost / Triple Captain）
