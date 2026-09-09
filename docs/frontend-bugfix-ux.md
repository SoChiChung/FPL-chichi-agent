# Frontend Bug Fix & UX Revision — 更新文档

> 本轮前端缺陷修复与 UX 修订的**设计/更新说明**。文档先行，未包含实现代码；待评审通过后在 `test` 分支落地。

| 项 | 值 |
|---|---|
| 文档版本 | **v1.0** |
| 日期 | 2026-09-09 |
| 目标分支 | `test`（提交到 test 即可） |
| 禁动分支 | `main` |
| 本次重点 | 修复数据状态问题 / 提升转会可视化体验 / 增强日志可追溯性 / 优化信息层级 |
| 边界 | **不做大规模 UI 重构**；优先保证 数据正确 > 逻辑正确 > 信息清晰 |
| 关联文档 | `docs/design.md`（v1.1 内容架构）、`docs/architecture.md`、`docs/decide.md` |

---

## 0. 修订记录

| 版本 | 变更 |
|---|---|
| v1.0 | 初版：5 项需求（GW 结算状态 / 转会阵容对比 / 思考日志时间 / External Links 层级）+ 现状诊断 + 数据契约变更 + 验收标准。 |

---

## 1. 现状诊断（先于方案的事实核查）

排查对象：`data/state.json`、`data/history.json`、`web/app.js`、`brain/*` 与 git 两分支状态。结论先行：

### 1.1 数据事实

| 事实 | 数据 | 影响 |
|---|---|---|
| `state.current_gw = 4`（本地与 `main` 线上最新快照一致） | 9/9 两次快照均为 4 | **GW 判断本身正确**：`context.resolve_current_gw()` 取「最近未 finished 的最小 event」，GW3 finished 后返回 4 |
| `history.json` 三条目（GW2/3/4）的 `points / rank / overall_rank` **全部为 null** | GW2、GW3 早已完结 | 历史区恒「待结算」、Gameweek Rank 卡恒「暂无已结算轮次」、趋势曲线永远无点 |
| `history` 条目无 `decided_at` | 全部缺失 | 思考时间无从显示（前端已具备渲染，属后端未落数据） |
| `state.points = 37` / `state.rank ≈ 10.33M` | 来自 `entry.summary_overall_points / summary_overall_rank` | 语义 = **赛季累计 Total**，与前端 Total Points / Overall Rank 卡匹配，正确 |
| `main` 分支 9/9 05:04 UTC 前快照为 GW3 | commit `938083b` 才推进到 GW4 | 白天线上长时间显示 GW3 **是正常的时点差**，非 current_gw 计算错误 |

### 1.2 根因（三层，按严重度）

1. **结算回填断链（核心缺陷）**
   `brain/context.py` 中存在 `build_history()`（读取 `entry_history["current"]`，按 `event.finished` 过滤生成 points/rank/overall_rank），`history_writer.upsert_decision()` 的 docstring 也声明「结算回填的 points/rank/overall_rank 保持不变」——但 **`brain/__main__.py` 从未调用回填**，只把 `entry_history` 用于转会额度判定。写 history 时 `points/rank/overall_rank` 恒为 None 且此后无人更新。→ 呈现为「永远等待结算」。

2. **当前 GW 展示的来源混淆（非代码缺陷，需确认口径）**
   前端 `当前 GW` 卡取 `state.current_gw`；而历史/曲线依赖回填后的 history。用户感知的「GW3 + 排名积分待结算」= 结算回填缺失（永远待结算）+ 观测时点线上快照仍在 GW3 的叠加结果。

3. **空态文案字段语义不区分（UX 缺陷）**
   现有空态占位统一为 `"待结算"`，未区分「积分待结算」与「排名待结算」。当某轮 `points` 与 `rank` 其中一项先结算时（FPL 偶发不同步），会误导为分数待结算。

> 说明：仓库当前（test）代码中无「待结算分数」字面文案；用户所见旧文案来自 `main` 分支历史表格版（表头「积分/当轮排名/总排名」共用同一占位）。本次修订统一为**字段感知的空态文案**，彻底消除歧义。

---

## 2. 需求一：GW 结算状态异常

### 2.1 目标行为

- GW3 已 finished → 页面历史区/曲线/当轮排名卡出现 GW3 的真实积分与排名；GW4 为当前轮。
- 「当前 GW：GW4」依赖 `state.current_gw`，数据链路已正确，本项核心是**把已完结轮次回填进 history**，并修正空态措辞。

### 2.2 方案 A：结算回填（backend，`test` 分支）

新增幂等回填逻辑，建议放 `brain/history_writer.py` 新函数（如 `backfill_settlement(history, entry_history, events)`），由 `brain/__main__.py` 在决策写库后调用：

1. 判定集合：`bootstrap.events` 中 `finished == true` 的 event id（FPL 官方标志，不做本地推导）。
2. 数据源：`entry_history["current"]` 中同 event 行的 `points / rank / overall_rank`。
3. 写入规则（幂等）：
   - history 中**已存在该 gw 条目** → 补/覆盖 null 字段（已有非 null 值则不回退）。
   - 不存在条目 → 本轮不新建（新建会带出无 decision 的孤儿条目，Accordion/曲线需兼容，见 2.4）。
4. 触发时点：**每次 brain 运行都执行**（30 分钟节奏天然覆盖结算窗口），而非等到 target 轮结束——从机制上消灭「永远等待」。

### 2.3 方案 B：空态文案字段感知（frontend，`web/app.js`）

`fmtNumber` 占位按字段类别区分（新增 helper，如 `nullText(kind)`）：

| 字段类别 | 空态文案 | 出现位置 |
|---|---|---|
| 排名类 `rank / overall_rank` | `待结算排名` | 历史 Accordion 折叠条「当轮排名/总排名」、比赛结果区、Gameweek Rank 卡、Sidebar 战绩、趋势无数据提示 |
| 积分类 `points` | `待结算`（或 `待结算积分`，二选一全局统一） | 同上对应积分栏位 |
| 完全无已结算轮次 | `暂无已结算轮次` | Gameweek Rank 卡 note（保留） |

> 交付验收第 4 条「'待结算分数'改为'待结算排名'」：凡**指向 Rank 栏位**的空态一律显示「待结算排名」，不再与积分占位共用文案。

### 2.4 边界与取舍

- 缺失 gw 的孤儿结算条目：**v1.0 不做**（GW1 无决策不显示为历史，保持 Accordion 语义「每条历史=一次 AI 决策」）。
- FPL 结算首窗偶发 `points` 先到、`rank` 后到：可容忍下一 cycle 自动补齐；前端空态按 2.3 分别措辞即可。
- `state.points / state.rank`（赛季累计）语义**保持不变**，不与非空态 history 混淆；若担心歧义，可在 Summary 卡 label 注明「总积分/总排名」（见需求四样式，非必做）。

---

## 3. 需求二：转会阵容对比

### 3.1 现状缺口

- `decision.starting_xi / bench` 只反映**基础阵容**（Calvert-Lewin 仍在阵）。
- `recommended_transfers[].in` 仅含 `{id, name, pos, price, market_score}`，**缺 team / form / selected_by / score_breakdown**；in 球员（如 Gakpo id=367）不在 `state.team` 内 → 前端无法独立渲染一张完整球员卡。

### 3.2 目标行为

- 阵容区提供 **Current Squad / 转会后阵容** 双视图切换；
- 「转会后阵容」直接展示应用全部建议转会后的 XI + 替补（如建议转出 Calvert-Lewin → 转入 Haaland，则视图显示 Haaland），用户不需脑补。

### 3.3 数据契约变更（backend 推荐主方案）

为避免前端二次推导与字段残缺，由后端在 state 落一份**应用转会后的完整 15 人快照**：

```
state["suggested_squad"] = [   # 与 state.team 同构的 15 个球员对象（完整字段 + score_breakdown）
  { id, name, pos, team, price, selected_by, form, score_breakdown, market_score,
    lineup_score, captain_score, starting, multiplier, ... }
]
```

- 生成规则：以 `decision.starting_xi/bench` 所在的基础 squad 为底，逐笔应用 `recommended_transfers`（out 命中位置 → in 顶替；同队/预算约束已由引擎校验，前端不再重算）；`in` 球员对象由全池 `players_map + market_scores + score_squad` 产物补齐。
- 无建议转会或建议为空 → 不写该字段（前端自然退化为单视图）。
- 落点：`brain/__main__.py` decision 组装处；序列化体积 ≈ 额外一份 team（可接受，数据正确优先）。

备选方案（不推荐单用）：前端按 out/in 就地替换 —— 因 in 缺 breakdown，球员卡会缺 3 条进度条，违背「数据正确优先」。

### 3.4 前端交互（frontend）

- `web/index.html` ③ 区标题行增加 segmented 容器（两个按钮：`当前阵容` / `转会后阵容`）。
- `web/app.js`：`renderTeam(state)` 参数化 —— 传入 `{ players: state.team | state.suggested_squad, decision }`，其余（按位置分行、替补门将恒首位、C/V 徽章、球员卡四指标）**完全复用现有逻辑，不新增一套渲染**。
- 切换仅换数据源 + 高亮按钮；无 `suggested_squad` 时按钮组隐藏、仅显示当前阵容。
- 文案：视图标题「转会后阵容」下加一行小字 `按 AI 建议：转出 X → 转入 Y（n 笔）`（复用 decision.recommended_transfers 摘要），与 §3.2 示例的 Calvert-Lewin→Haaland 场景对齐。

---

## 4. 需求三：思考日志增加时间

### 4.1 契约变更（backend）

`brain/history_writer.py` `upsert_decision()`：

- 新增条目时写 `entry["decided_at"] = <now UTC ISO-8601, seconds 精度>`；
- **已存在条目不覆盖** `decided_at`（保留该轮「决策首次生成时间」，避免 30 分钟节奏的 bot 每次重跑把时间刷新成"刚刚"）。
- 可选升级字段（记录备注，v1.0 可不做）：`decided_updated_at`（最近一次改写），供未来展示「最近修订」。

> 对齐 `docs/design.md v1.1 §6.1`（本属 Phase 2.5 内容，实现阶段落地缺失），非新设计。

### 4.2 展示（frontend）

- Main ④「AI 思考日志（本轮）」：标题下新增时间行 —— `决策时间 <fmtTime(entry.decided_at)>`（本地时区；格式沿用现有 `fmtTime`，如 `2026/9/9 18:40:52`）。
- 历史 Accordion「决策时间」区：渲染逻辑已存在（`fmtTime(r.decided_at)`），数据到位自动显示，无需改动；缺字段仍显示 `-`（符合 design.md §6.3 兜底）。
- Sidebar「最近一次思考」可选：正文上方小字时间。
- 单条 note 级时间戳（notes[i].ts）：**v1.0 不做**——同轮 notes 同一批次生成，以条目级 `decided_at` 为时间锚点即可满足追溯；如后续需要可扩展 note 结构。

---

## 5. 需求四：External Links 区域优化

### 5.1 视觉层级目标

```
用户名 / 品牌名（最大）   ← FPL紫葱酱 · @zcj
核心数据（次大）         ← Overall Rank / Total Points / Season（实时引用 state）
平台名称（辅助文字）
图标（最弱：缩小 / 降对比 / 仅导航）
```

「FPL Analytics / Fantasy Research / Data Tracking」定位为**品牌 tagline 副标语行**，不当作导航数据。

### 5.2 数据契约（`web/config.js`）

扩展结构（保持向后兼容——旧字段缺失时按旧逻辑渲染或隐藏）：

```js
window.SITE_CONFIG = {
  brand: {
    name: "FPL紫葱酱",          // 主品牌名，视觉最大
    handle: "@zcj",             // 用户名
    tagline: "FPL Analytics · Data Tracking", // 副标语
  },
  heroMetrics: ["rank", "points"],  // 从 state 读取的核心数据 key 白名单
  externalLinks: [               // 平台导航：保留，仅降权
    { label: "小红书", url: "#", icon: "img/小红书icon.png", note: "紫葱酱" },
    { label: "B站", url: "#", icon: "img/bilibili.png", note: "…" },
    { label: "懂球帝", url: "#", icon: "img/dqd.png", note: "…" },
    { label: "微信", url: "#", icon: "img/wechat-logo.png", note: "…" },
  ],
};
```

### 5.3 DOM / 渲染（`web/app.js`）

`renderExternalLinks()` 参数化接收 `state/rows`：

- 区块结构升级：`品牌头（name + handle + tagline）` → `核心数据行（按 heroMetrics 从 state 取 rank/points，fmtNumber 展示）` → `平台导航（图标 + 名称小字，链接可点）`。
- 平台导航复用现有双挂载点（Sidebar 桌面 / footer 移动），数据同源。
- 空数组语义调整：`externalLinks` 空 → 只隐藏平台导航行；只要配置了 `brand` 就渲染品牌卡（避免整块消失）。`ext-empty` 判定随之收窄。
- 核心数据实时性：随 state 快照更新（rank/points 与 ① Summary 卡同源），无需新后端字段。

### 5.4 样式（`web/style.css`，仅层级，非主题重构）

| 对象 | 调整 |
|---|---|
| 品牌名 | 现有 `.ext-brand` 升级：字号放大（≈1.05–1.15rem）、去 muted、加粗；无品牌数据时降级旧文案 |
| tagline/handle | 独立小元素 muted 排布 |
| 核心数据 | 复用 `.rec-row / .rec-value` 视觉语言，rank/points 数值 tabular-nums |
| 平台图标 | 现尺寸缩小（≤20px → 16–18px 档），`opacity` ≈0.55，hover 提至 0.9–1；图标与文字整体降为次级 muted |
| 区块标题 | 「External Links」→ 语义化（如「关于我 / Follow」）可选，保持 h2 结构 |

> 边界：仅调整该区块内部层级与 icon 权重，**不动 Dashboard 布局与其它区块**，符合「不做大规模 UI 重构」。

---

## 6. 涉及文件清单（改动落点，按分支实施）

| 文件 | 改动点 | 归属 |
|---|---|---|
| `brain/history_writer.py` | `upsert_decision()` 写 `decided_at`；新增 `backfill_settlement()`（幂等回填 points/rank/overall_rank） | 需求 1+3 |
| `brain/__main__.py` | 调用回填；生成并写入 `state.suggested_squad` | 需求 1+2 |
| `brain/data_store.py` | `validate_state` 可选校验新字段类型（弱校验即可） | 需求 2 |
| `web/config.js` | 品牌/核心数据/平台导航新结构 | 需求 4 |
| `web/app.js` | 空态文案字段感知；阵容双视图渲染与切换；思考日志时间行；External 品牌卡渲染 | 需求 1–4 |
| `web/style.css` | segmented 按钮、External 层级与图标降权、时间行小字 | 需求 2–4 |
| `web/index.html` | ③ 区切换按钮容器（DOM 占位） | 需求 2 |

不改动：`context.py` 的 GW 判定（正确）、`scheduler.py`、`main` 分支一切内容、`docs/design.md`（其修订记录建议随实现合并另行追加 v1.2 摘要，本次不动）。

---

## 7. 验收标准（映射用户 10 条）

| # | 验收项 | 方案落点 |
|---|---|---|
| 1 | 页面正确显示 GW4 | 数据链路已正确；验证线上快照 ≥ 05:04 UTC 版本（见 §8 风险 1） |
| 2 | GW3 已结束自动结算 | §2.2 回填（GW3 finished → 下一 cycle 自动补 points/rank/overall_rank） |
| 3 | 修复 Rank / Points 状态异常 | §2.2 + 空态区分 §2.3 |
| 4 | 「待结算分数」改为「待结算排名」 | §2.3 字段感知空态 |
| 5 | 转会建议阵容直接显示转会后结果 | §3.3 suggested_squad（Calvert-Lewin→Haaland 场景直接显 Haaland） |
| 6 | 「当前阵容 / 转会后阵容」切换 | §3.4 segmented |
| 7 | 每条思考日志显示时间 | §4（decided_at + ④ 时间行 + ⑤ 自动显示） |
| 8 | External Links 调整视觉层级 | §5.3/§5.4 |
| 9 | 用户名与核心数据优先展示 | §5.1/§5.3 品牌头 + heroMetrics |
| 10 | 图标降级为辅助元素 | §5.4 icon 缩小降对比 |
| 附 | 仅提交 `test` 分支 | ✓ |

手工验证建议：`python -m brain` 跑一次（本地）→ 检查 `data/history.json` GW2/GW3 出现 points/rank、GW4 条目出现 `decided_at`、`state.json` 出现 `suggested_squad`；`npm run build` 同步 web/data 后用无头 Chrome（`file://` + `--allow-file-access-from-files`，1440/400 双宽度）截图核对 ①–⑥ 与双视图/External 层级。

---

## 8. 风险与注意

1. **线上展示滞后 ≠ 前端 bug**：Vercel 走 `main` 分支（Actions `push HEAD:main` 触发）。本地 9/9 18:54 快照（test 领先）未提交；`data/` 存在未提交改动与未完成的合并风险点——实施前先 `git pull --rebase origin main`（本地与 origin/main 避免分叉），合并冲突遵循 `data/** -merge` 保留 HEAD 规则。
2. **回填一致性**：回填必须幂等且不回退已有非 null 值；GW 推进/API 修正波动时以 FPL 官方当前值为准。
3. **suggested_squad 体积与新鲜度**：是决策时点快照，不与下一 cycle 的实时价格联动——语义上即「当时建议」，可接受；文档注释需写明。
4. **测试账号状态**：GW2 establish、GW3 picks、GW4 auto_pick 混用使 `suggested_squad` 的底必须取 `decision.starting_xi/bench`（决策产物），不要取最新官方 picks（两者在 auto_pick 轮会不同）。
5. **UI 边界**：除需求 2/4 明确的新增控件与层级外，不触碰其它视觉；避免借本次顺手动 Phase 3 主题。

---

## 9. 待主人拍板项

1. 积分类空态文案：统一「待结算」还是「待结算积分」？（默认前者，仅排名类区分）
2. `decided_at` 展示时区：本地时间（现有 fmtTime 行为，推荐）还是显式 UTC？
3. 阵容双视图按钮文案：`当前阵容 / 转会后阵容`（推荐）或英文 `Current / Suggested`。
4. External 区块标题是否从「External Links」更名（如「关于我」），仅文案，不影响结构。
