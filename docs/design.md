# FPL AI Manager 前端重构设计

> 把网站从「AI 数据面板」改造成「AI FPL 玩家养成日志」。

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 日期 | 2026-09-09 |
| 目标分支 | `test` |
| 禁动分支 | `main` |
| 本轮交付 | **仅本文档**（不写代码） |
| 关联需求 | Frontend Revision Request（17 条验收） |
| 关联文档 | `docs/decide.md`（评分公式）、`docs/architecture.md`（整体架构） |

---

## 1. 目标与范围

### 1.1 产品定位转变

| 维度 | 现状 | 目标 |
|---|---|---|
| 核心叙事 | AI 算出了什么数字 | AI 这周做了什么、为什么这么做、成绩如何 |
| 观感 | 数据调试工具 / BI 看板 | 拥有人格的 AI FPL Manager |
| 信息密度 | 高（技术指标堆砌） | 低（一眼看懂、竖屏友好） |
| 用户心智 | 看模型输出 | 追踪一个真实 FPL 玩家 |

### 1.2 本轮交付边界

- **产出**：本设计文档 + 后续前端实现（`web/index.html`、`web/app.js`、`web/style.css` 重写）。
- **最小后端改动**：仅 `brain/history_writer.py` 新增 `decided_at` 时间戳字段（见 §7.1），其余评分/抓取逻辑不动。
- **不改动**：`brain/context.py` 的 bank 取值逻辑（量纲问题见 §6.1 风险说明）、`brain/transfer.py`、评分引擎。
- **分支约束**：全部提交至 `test`，`main` 保持不动。

---

## 2. 现状分析

### 2.1 前端现状（`web/`）

当前为无构建工具的纯静态站：原生 HTML + 单文件 `app.js`（fetch `data/state.json` 与 `data/history.json` 后渲染）。

| 区块 | 现状 | 问题 |
|---|---|---|
| Header | 标题 + 赛季 | 无 AI 人格、无头像 |
| Overview 卡片网格 | 8 张数据卡（GW/积分/排名/银行/阵型/队长/副队长/AI首发总分） | 技术指标暴露、Bank 单位错、AI首发总分待删 |
| 本轮建议 | 转会卡 + Market Score | 偏调试视角，缺「本周动态」聚合视角 |
| 首发建议 | 按位置分行 pitch + 球员卡（M/本轮/爆发 chips） | 球员卡字段普通用户不可读 |
| 历史记录 | 简单表格（GW/积分/当轮排名/总排名/AI首发总分/C爆发分） | 无折叠、无转会、无思考日志、无时间、无曲线 |
| 配色 | 深色暗夜主题（`#0f1220` 底） | 偏金融后台，与目标风格相反 |

### 2.2 数据契约现状

**`data/state.json`（本轮实时状态）**

```
season, current_gw, points, rank, bank, formation, captain, vice,
next_deadline, last_update, team[15], manager_id, market,
decision{formation,captain,vice,starting_xi,bench,squad_source,
         transfer_status,free_transfers,recommended_transfers,transfer_notes},
lineup_scores[15], captain_scores[15], score_meta{target_gw,recent_rounds,
         score_source,warnings}
```

`team[i]` 关键字段：`id, name, pos, team, price, selected_by(TSB%), form(官方字符串),
status, starting, is_captain, is_vice_captain, market_score, lineup_score,
captain_score, score_breakdown{projection,form,streak,clean_sheet,fixture,
attack,attack_potential}`。

**`data/history.json`（历史决策）**

```
{season, manager_id, history:[
  {gw, points, rank, overall_rank,
   decision{formation,captain,vice,starting_xi,bench,squad_source,
            transfer_status,free_transfers,recommended_transfers,strategy_snapshot},
   notes[{topic, detail|player}],
   metrics{team_market_score,captain_market_score,formation_market_score,...}}
]}
```

### 2.3 已确认的问题根因

| # | 问题 | 根因 | 定位 |
|---|---|---|---|
| P1 | Bank 显示 £20m（应 £2.0m） | FPL API `entry_history.bank` 单位为 **0.1M**，`context.py:113` 原样落盘（`state.bank=20`），前端 `app.js:202` 直接 `£${bank}m` 未换算 | 前端 `/10` 修正（见 §6.1） |
| P2 | 球员卡 M/本轮/爆发 不可读 | `scoreChips()` 直接暴露 `market_score/lineup_score/captain_score` 原始分 | 重构为 Form/GoalPotential/Fixture/TSB（见 §6.2） |
| P3 | 历史无思考日志 | `history.notes` 已含结构化决策理由，但前端 `renderHistory()` 只画表格，未消费 notes | Accordion 内渲染 notes（见 §6.5/6.6） |
| P4 | 历史无决定时间 | `history_writer.upsert_decision()` 不写任何时间戳 | 新增 `decided_at`（见 §7.1） |

---

## 3. 设计原则

1. **动物森友会风格**：可爱、轻松、治愈、圆润、像素风、游戏感、卡通化。
2. **移动优先**：竖屏阅读、大卡片间距、低数据密度、清晰层级；主要传播场景为微信群 / 朋友圈 / 小红书。
3. **人格化表达**：网站口吻是「这个 AI 本周做了什么」，而非「模型输出了什么」。
4. **降低技术暴露**：移除 Market Score / Lineup Score / Captain Score / AI首发总分 / C爆发分 等原始分值的直接展示；保留可追溯性（hover/折叠区可选）。
5. **零新增依赖倾向**：沿用无构建静态站架构；图表与进度条优先手绘 SVG/CSS，避免引入重型库。

---

## 4. 视觉设计系统

### 4.1 配色 Token（动森调色板）

| Token | 色值 | 用途 |
|---|---|---|
| `--grass` | `#7CB342` | 主色 / 进度条填充 / 强调 |
| `--grass-deep` | `#558B2F` | 主色暗调 / 文字强调 |
| `--sky` | `#A6D8FF` | 次色 / 信息条 |
| `--sky-deep` | `#5BA3D0` | 链接 / 可交互 |
| `--cream` | `#FFF8E7` | 主背景 |
| `--wood` | `#D7B377` | 边框 / 分隔 / 木牌 |
| `--wood-deep` | `#A87E3D` | 木牌描边 |
| `--sun` | `#FFD54F` | 队长 / 高亮 / 奖牌 |
| `--berry` | `#E57373` | 危险 / 转出 / 警示 |
| `--ink` | `#3E2723` | 主文字（暖棕而非纯黑） |
| `--ink-soft` | `#6D4C41` | 次文字 |
| `--panel` | `#FFFFFF` | 卡片底（奶油白偏白） |
| `--panel-alt` | `#FFF3CC` | 次级面板 / 日记纸 |

> 全部浅色底 + 暖色文字，彻底替换当前暗夜主题。深色模式作为 P2 增强不在本轮范围。

### 4.2 字体 / 圆角 / 阴影

- **字体**：标题用 `Fredoka` / `Baloo 2`（Google Fonts CDN，圆润游戏感）；正文中文用系统圆体兜底（`"PingFang SC","Microsoft YaHei",system-ui`）；数字 `font-variant-numeric: tabular-nums`。
- **圆角**：卡片 `16px`、按钮/徽章 `999px`、面板 `20px`，强化圆润感。
- **阴影**：`0 2px 0 var(--wood-deep)`（下沿木牌投影）+ 轻投影 `0 4px 12px rgba(62,39,35,.08)`，模拟贴纸/木牌质感。
- **像素感**：进度条、头像、装饰用 `image-rendering: pixelated` 的 SVG 像素格子，避免抗锯齿模糊。

### 4.3 像素风进度条组件（通用）

玩家属性条统一形态：圆角条 + 像素格子刻度 + 填充色随阈值分级。

| 阈值 | 填充色 | 语义 |
|---|---|---|
| ≥ 75 | `--grass` | 优秀（绿） |
| 50–74 | `--sun` | 中等（黄） |
| < 50 | `--berry` | 偏弱（红） |

实现：外层 `.stat-bar`（木牌底 + 刻度背景），内层 `.stat-fill`（`width: {value}%` + 分级色）。刻度用 `repeating-linear-gradient` 画 8 等分像素格。无值（null）时渲染占位条 + 「-」。

### 4.4 卡片 / 面板风格

- **球员卡**：白底圆角贴纸，顶部一条位置色带（GKP 蓝 / DEF 绿 / MID 黄 / FWD 红），左上像素小图标，右下 TSB 印章。
- **日记信件**（AI Thoughts）：奶油黄信纸底 + 木牌边 + 手写体语气，模拟动森村民信。
- **本周动态卡**：游戏任务卡风格，带「本周任务」木牌标题 + 勾选样式。

---

## 5. 信息架构（首页）

按需求推荐结构，自上而下：

```
1. AI 头像 + 经理名 + 赛季                    （人格入口）
2. 本轮成绩                                   （积分 / 当轮排名 / 总排名 / Bank）
3. AI 本周动态（This Week）                   （队长 / 阵型 / 转会摘要）
4. 当前首发阵容                               （pitch + 球员卡）
5. 替补席                                     （4 人卡）
6. 经理日志（AI Thoughts，本轮）              （动森信件）
7. 历史记录（Accordion）                      （每 GW 折叠展开）
8. 历史排名曲线                               （Overall Rank Trend）
```

> 「本轮建议（转会详情卡）」不再独立成顶级区块，其信息并入 §3「AI 本周动态」；详细的 out/in/Market Gap 仍可在历史 Accordion 的「实际操作」里展示，但移除 Market Score 原始值，改为 reason 自然语言。

---

## 6. 模块设计

### 6.1 Bank 显示修复（验收 #1）

**根因**：`state.bank` 取自 FPL API `entry_history.bank`，单位 0.1M（`20` ⇒ £2.0m）。

**修复（前端层）**：

```js
// 旧
["银行", `£${state.bank}m`, ""]
// 新
["银行", `£${(state.bank / 10).toFixed(1)}m`, ""]
```

**为何只改前端**：后端 `state.bank` 同时被 `brain/transfer.py:111` 用于预算比较（`bank + out.price ≥ in.price`）。`player.price` 已是 M 单位（如 `4.5`），而 `bank` 是 0.1M 单位——二者量纲本就不一致，是既存技术债。若在后端把 bank 转 M 单位，会连锁影响 transfer 预算逻辑，超出本轮范围。故本轮**仅前端换算显示**，后端量纲统一作为独立技术债另行处理（见 §10）。

> 需求文档推测「200 → 20.0M」；实测 `state.bank=20`，即 API 单位为 0.1M，前端除以 10 即得 £2.0m，结论一致。

### 6.2 球员卡片重构（验收 #2–6）

**移除**：`M` / `本轮` / `爆发` 三个 chip（对应 `market_score` / `lineup_score` / `captain_score`）。

**新展示字段映射**：

| 展示项 | 数据源 | 单位/处理 | 备注 |
|---|---|---|---|
| 近期状态 Form% | `score_breakdown.form` | 0–100 归一化分，直接 `%` | GKP 不参与 Form（decide.md 3.1/3.5），null 不渲染 |
| 进球预测 Goal Potential% | `score_breakdown.projection` | 0–100，直接 `%` | GKP/部分 DEF 无值，null 不渲染 |
| 赛程难度 Fixture% | `score_breakdown.fixture` | 0–100，**越高越友好**（已取负归一，见 decide.md 3.3） | 全位置有值 |
| 持有率 TSB% | `selected_by` | 已是百分比，直接显示 1 位小数 | 全位置有值 |

> 三项子分均已在 `lineup_score.py` 归一化到 0–100（decide.md §3.0/§3.3），**前端无需重算**，直接映射为进度条。Fixture 的「越高越友好」方向与需求完全一致，无需翻转。

**队长/副队长标识**：保留 `C` / `V` 徽章，改为金色木牌 / 银色木牌样式。

**卡片布局（移动优先）**：
- 单列竖排（< 480px）；两列（≥ 480px）；pitch 内按位置行自适应。
- 每张卡：顶部位置色带 + 名字 + C/V 徽章 → 球队·价格 → 三条进度条（Form/Goal/Fixture）→ 底部 TSB 印章。
- null 项整条不渲染，避免门将卡出现空 Form 条。

### 6.3 AI 本周动态模块（验收 #10）

首页新增「This Week / AI 本周动态」游戏任务卡，聚合 `state.decision`：

| 子项 | 数据源 | 展示 |
|---|---|---|
| GW | `state.current_gw` | 「GW4 本周任务」木牌标题 |
| 队长 | `decision.captain.name` | 「队长：João Pedro」+ 金牌图标 |
| 副队长 | `decision.vice.name` | 「副队长：B.Fernandes」+ 银牌 |
| 阵型 | `decision.formation` | 「阵型 3-5-2」 |
| 转会摘要 | `decision.recommended_transfers[]` | 列出 out→in；无则「本轮未进行转会」 |
| 转会理由 | `transfer[i].reason` | 自然语言一行 |

**数据边界说明**：本 AI 为建议型（不自动提交 FPL），故「本周动态」展示的是**本轮采纳的建议**。`transfer_status=unlimited`（新账号）时标注「新账号：转会暂不受限」。

### 6.4 首发阵容 / 替补席

- 首发沿用 pitch 按位置分行（GKP/DEF/MID/FWD），但底色改草地纹理，球员卡换 §4.4 风格。
- 替补席 4 张卡，门将恒列第 1，其余按决策优先级。替补编号用木牌数字。
- 移除「AI 首发总分」「首发 X 人 · AI首发总分」字样（验收 #7）。

### 6.5 经理日志（AI Thoughts，本轮）（验收 #13）

**本轮版本**：在首页「首发阵容」下方渲染 `state` 当前决策的可读日志。
> 注：`state.json` 不直接存 notes（notes 仅写 history）。本轮日志取自最近一次 brain 运行的 `decision.transfer_notes` + `score_meta.warnings`，按 topic 分组渲染为动森信件气泡。

**历史版本**（§6.6 内）：每个 GW Accordion 展开后的「AI 思考日志」取自 `history[i].notes`。

**渲染规则**（notes → 可读日志）：

| topic | 渲染策略 |
|---|---|
| `squad_source` | 「阵容来源：…」 |
| `transfer_status` | 「转会额度：…」 |
| `no_transfer` | 「本轮未转会：…」 |
| `transfer_out` / `transfer_in` | 「转出/转入：{player} — {detail}」 |
| `captain` | 「队长选择：{player} — {detail}」 |
| `lineup` | 「排兵布阵：…」 |
| `external_source` | 「数据更新：…」（次要，可折叠） |
| `warning` / `data_missing` | 警示色信件 |
| 其他 | 直接展示 `detail` |

**信件样式**：奶油信纸底 + 木牌边 + 手写引号，多条堆叠如一沓信。

> **不强求合成连贯段落**：当前 notes 为条目式结构化日志，前端如实分条展示，不编造「最近布伦特福德赛程不错……」这类整段叙述（避免幻觉）。未来可由后端新增 `thoughts` 自然语言字段（P1 增强，见 §10）。

### 6.6 历史记录 Accordion（验收 #11–14）

**结构**：每个 GW 一个折叠条，默认全部收起，点击展开。

**折叠头（收起态）**：
```
GW4 · 积分 74 · 当轮排名 1,243,555 · 总排名 523,112      [▼]
```
未结算轮次（`points=null`）显示「待结算」木牌。

**展开内容（4 区）**：

1. **比赛结果**（验收 #8 删 AI首发总分；保留积分/当轮排名/总排名）
   - 积分 / Gameweek Rank / Overall Rank 三联卡。
2. **实际操作 Transfers**（验收 #12）
   - 取 `decision.recommended_transfers`；有则列 out→in + reason；无则「本轮未进行转会」。
3. **AI 思考日志**（验收 #13）
   - 按 §6.5 规则渲染 `notes`。
4. **思考时间 Decision Date**（验收 #14）
   - 取 `decided_at`（见 §7.1）；无值（历史数据）显示「-」。

**移除**：`AI 首发总分` 列（验收 #7）、`C 爆发分` 列。

### 6.7 历史排名曲线（验收 #9）

**Overall Rank Trend** 折线图：

- 横轴：GW1, GW2, …（`history[i].gw`）。
- 纵轴：`history[i].overall_rank`。
- **反向 Y 轴**：排名数字越小 = 成绩越好 = 显示在上方。实现时 Y 轴用 `max - value` 映射，标签显示真实排名。
- 数值过大时格式化：`1,243,555` → `1.24M`；刻度自动分档（1k / 10k / 100k / 1M）。
- **手绘 SVG**（零依赖）：草地色折线 + 圆点节点 + 木牌坐标标签；移动端横向可滚动或自适应缩放。
- 未结算轮次（`overall_rank=null`）不画点。

---

## 7. 数据契约变更

### 7.1 新增 `decided_at`（后端最小改动）

**改动点**：`brain/history_writer.py` `upsert_decision()`。

```python
# 在 entry 新建时补字段
entry = {"gw": gw, "points": None, "rank": None, "overall_rank": None,
         "decided_at": now_iso}   # 新增
# 已存在 entry 时：若 decided_at 缺失则回填（不覆盖已有值）
entry.setdefault("decided_at", now_iso)
```

`now_iso` 由 `brain/__main__.py` 调用时传入（复用已有的 `now` 变量，ISO 8601 UTC）。

**前端消费**：`history[i].decided_at` → 格式化为 `2026-09-05 18:40 UTC`（或按需转 JST）。历史数据无此字段时显示「-」。

> 此为本轮唯一后端改动，属实现阶段任务，本文档阶段不落地。

### 7.2 前端字段映射总表

| 展示 | state 字段 | history 字段 | 处理 |
|---|---|---|---|
| Bank | `bank` | — | `/10` + `m` |
| 本轮积分/排名 | `points` / `rank` | `points` / `rank` / `overall_rank` | 千分位 |
| 球员 Form% | `team[i].score_breakdown.form` | — | 直接 `%`，null 跳过 |
| 球员 Goal Potential% | `team[i].score_breakdown.projection` | — | 直接 `%`，null 跳过 |
| 球员 Fixture% | `team[i].score_breakdown.fixture` | — | 直接 `%` |
| 球员 TSB% | `team[i].selected_by` | — | 直接 |
| 本周队长/阵型/转会 | `decision.*` | — | 见 §6.3 |
| 历史 AI 日志 | — | `notes[]` | 见 §6.5 规则 |
| 历史转会 | — | `decision.recommended_transfers` | 见 §6.6 |
| 思考时间 | — | `decided_at`（新） | ISO 格式化 |

### 7.3 null / 缺失兜底

- `score_breakdown.*` 为 null：该进度条整条不渲染（不画空条）。
- `overall_rank` 为 null：曲线跳过该点；Accordion 头标「待结算」。
- `decided_at` 缺失：显示「-」。
- `recommended_transfers` 为空：显示「本轮未进行转会」。

---

## 8. 技术实现策略

### 8.1 架构延续

沿用无构建工具静态站：`index.html` + `app.js` + `style.css`，`fetch` 同目录 `data/*.json`。不引入 npm 打包。部署链路（Vercel 静态 + GitHub Actions 推 data）不变。

### 8.2 图表方案

排名曲线**手绘 SVG**（零依赖、贴合像素风），不引 Chart.js。实现要点：反向 Y 轴映射、千/百万格式化、响应式 viewBox。

### 8.3 字体引入

Google Fonts CDN 引入 `Fredoka` / `Baloo 2`（仅标题/数字），正文中文走系统圆体兜底；单次 `<link>` 预连接，不阻塞首屏。

### 8.4 AI 头像

内嵌 SVG 像素风「小葱」头像（绿色葱头 + 表情），与品牌一致，无外部图片依赖。放置首页顶部与每个日记信件落款。

### 8.5 响应式断点

| 断点 | 布局 |
|---|---|
| < 480px | 单列、大间距、卡片全宽 |
| 480–768px | 球员卡 2 列、其余单列 |
| > 768px | pitch 按位置行、球员卡 3+ 列、max-width 720px 居中 |

> 桌面也收窄至 720px，保持「养成日志」的亲密尺度，避免变成宽屏看板。

### 8.6 Accordion 交互

原生 `<details>` / `<summary>` 或 JS 控制的 `details` 风格元素，无依赖、无障碍友好。默认全收起；可加「展开全部」开关（P2）。

---

## 9. 验收标准对照

| # | 验收项 | 落点 | 状态 |
|---|---|---|---|
| 1 | 修复 Bank 单位 | §6.1 | ✓ 前端 /10 |
| 2 | 删除 M/本轮/爆发 | §6.2 | ✓ 移除 scoreChips |
| 3 | 增加近期状态 Form | §6.2 | ✓ score_breakdown.form |
| 4 | 增加进球预测 Goal Potential | §6.2 | ✓ score_breakdown.projection |
| 5 | 增加赛程难度 Fixture | §6.2 | ✓ score_breakdown.fixture |
| 6 | 保留持有率 TSB | §6.2 | ✓ selected_by |
| 7 | 删除 AI首发总分 | §6.4/§6.6 | ✓ |
| 8 | 删除 C爆发分 | §6.6 | ✓ |
| 9 | 排名曲线图 | §6.7 | ✓ 手绘 SVG 反向 Y |
| 10 | AI 本周动态模块 | §6.3 | ✓ |
| 11 | Accordion 历史 | §6.6 | ✓ |
| 12 | 每轮转会记录 | §6.6 区2 | ✓ |
| 13 | AI 思考日志 | §6.5/§6.6 区3 | ✓ notes 渲染 |
| 14 | 思考时间 | §6.6 区4 / §7.1 | ✓ 新增 decided_at |
| 15 | 动森视觉风格 | §4 | ✓ |
| 16 | 移动端优先 | §4.4/§8.5 | ✓ |
| 17 | 仅提交 test | §1.2 | ✓ |

---

## 10. 不在本轮范围 / 风险 / 待决

### 10.1 不在本轮范围

- **后端 bank 量纲统一**：`state.bank`（0.1M）与 `player.price`（M）单位不一致，是既存技术债，影响 `transfer.py` 预算比较。本轮仅前端换算显示，后端统一留作独立任务。
- **AI 思考连贯段落**：当前 notes 为条目式，前端如实分条展示；若要生成「最近布伦特福德赛程不错……」式连贯叙述，需后端新增 `thoughts` 自然语言字段（P1 增强）。
- **深色模式**：本轮仅浅色动森主题。
- **展开全部 / 筛选 GW**：Accordion 增强交互（P2）。

### 10.2 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 字体 CDN 失败 | 标题回退系统圆体 | 系统兜底已配置，可接受 |
| 历史 `decided_at` 缺失 | 早期 GW 显示「-」 | 兜底已设计（§7.3） |
| notes topic 新增 | 新 topic 无渲染规则 | §6.5 表末「其他」兜底直接展示 detail |
| `score_breakdown` 子分 null 多 | 球员卡进度条稀疏 | null 整条不渲染，门将卡仅显示 Fixture/TSB 属正常 |

### 10.3 待决项

1. **AI 经理名/人格设定**：首页「AI 头像 + 经理名」中的经理名待定（建议沿用「小葱」品牌或另起 FPL 向人设）。
2. **转会「实际 vs 建议」措辞**：当前 AI 不自动提交 FPL，展示文案是「本周采纳的建议」还是「本周操作」需主人定调，避免误导读者。
3. **排名曲线是否包含当轮（未结算）**：默认不画未结算点，是否改为虚线占位待定。
