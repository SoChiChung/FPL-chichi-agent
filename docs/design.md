# FPL AI Manager 前端重构设计

> 把网站从「AI 数据面板」改造成「AI FPL 玩家养成日志」。

| 项 | 值 |
|---|---|
| 文档版本 | **v1.1**（内容架构优先，视觉延后） |
| 日期 | 2026-09-09 |
| 目标分支 | `test` |
| 禁动分支 | `main` |
| 关联需求 | Frontend Revision Request（v1.0）+ 补充要求（v1.1） |
| 关联文档 | `docs/decide.md`（评分公式）、`docs/architecture.md`（整体架构） |

---

## 0. 修订记录

| 版本 | 变更 |
|---|---|
| v1.0 | 初版：动森视觉 + 人格化 + Accordion + 排名曲线，17 条验收。 |
| v1.1 | **优先级重排**：暂缓视觉设计（动森/配色/插画/动画/像素风），先做内容架构（Phase 2.5）。布局从单栏纵向堆叠改为 **Dashboard Layout（Sidebar + Main）**；AI 思考日志增加**字数限制**（50–120 字、≤3 段、10 秒读完）；历史曲线从单一 Overall Rank 扩为**三条趋势**（Overall Rank / GW Points / GW Rank）；新增 **Sidebar 内容规划**（Profile / 当前战绩 / 最近思考）与 **External Links 预留区**。视觉设计系统（v1.0 §4）整体移入 Phase 3 附录，仅保留 token 备用。 |

---

## 1. 阶段规划与目标

整体演进路径（主人定调）：

```
Phase 1  数据          （已完成：抓取管线 + state/history）
Phase 2  决策          （已完成：Market/Lineup/Captain 引擎）
Phase 2.5 内容架构      ← **本阶段**
Phase 3  视觉设计      （动森风格等，后续版本）
Phase 4  人格化        （thoughts 自然语言等，后续版本）
```

**本阶段目标：把网站内容组织好，而不是把网站做漂亮。**

验收口径——用户第一次打开网站时能够理解：

1. 这个 AI 是谁
2. 这个 AI 本周干了什么
3. 这个 AI 成绩怎么样
4. 这个 AI 为什么这么做
5. 这个 AI 过去表现如何

---

## 2. 本阶段范围

### 2.1 做什么

| 类别 | 内容 |
|---|---|
| 布局 | Dashboard Layout（Sidebar 25% + Main 75%），移动端折叠为顶部摘要 + 主内容 |
| Sidebar | AI Manager Profile、当前战绩、最近一次思考摘要、External Links 预留区 |
| Main | 本轮阵容、本轮成绩、AI 本周动态、历史记录（Accordion）、趋势曲线 |
| 内容修正 | Bank 单位修复、球员卡字段替换（M/本轮/爆发 → Form/GoalPotential/Fixture/TSB）、删除 AI首发总分 / C爆发分 |
| 日志 | AI 思考日志渲染 + 字数限制 + 思考时间 |
| 图表 | 三条趋势曲线（三独立图或 Tab 切换，表现形式后定） |

### 2.2 不做什么（延后至 Phase 3+）

- Animal Crossing 风格、配色体系、插画、动画、像素风、美术资源
- 深色模式、字体引入、头像精修
- 连贯段落式 AI 叙述（`thoughts` 自然语言字段，Phase 4）
- 后端 bank 量纲统一（独立技术债）

> v1.0 的视觉设计系统整体后移；本阶段样式只做「能看、层级清楚」的默认样式，不做主题皮肤。

---

## 3. 信息架构：Dashboard Layout

### 3.1 桌面布局（> 768px）

```
┌──────────────┬──────────────────────────────┐
│ Sidebar 25%  │ Main Content 75%             │
│              │                              │
│ AI Profile   │ ① 本轮成绩（摘要卡）          │
│ 当前战绩      │ ② AI 本周动态                │
│ 最近思考摘要  │ ③ 本轮阵容（首发 + 替补）     │
│ External     │ ④ AI 思考日志（本轮）         │
│   Links      │ ⑤ 历史记录（Accordion）       │
│              │ ⑥ 趋势曲线（三条）            │
└──────────────┴──────────────────────────────┘
```

- 容器 max-width 1080px 居中；Sidebar 粘性定位（`position: sticky`），滚动时侧栏常驻。
- Sidebar 内容少、Main 内容长，纵向滚动主体在 Main。

### 3.2 移动端（≤ 768px）

自动折叠为：

```
顶部摘要（原 Sidebar 压缩为横条）
  ├ AI 头像 + 名字 + 赛季
  ├ 战绩三联（Overall Rank / GW Rank / Total Points）横滑或三列
  └ （External Links 收进页脚）
↓
Main Content（① → ⑥ 纵向排列）
```

- Sidebar 整体在移动端隐藏（`display:none`），改为 header 下的摘要条渲染同一份数据——**数据源相同，仅展示形态不同**，避免双份数据逻辑。
- 卡片间距加大、单列布局，保证竖屏阅读层级。

### 3.3 Sidebar 内容规划

#### A. AI Manager Profile

| 展示项 | 数据源（现有字段推导，无后端改动） |
|---|---|
| Manager Style | `history[i].decision.strategy_snapshot.strategy`（当前 `market_consensus`）→ 文案映射「市场共识型」 |
| Risk Level | `strategy_snapshot.allow_hits=false` + `max_free_transfers` 保守 → 映射「Low」；映射表见 §6.2 |
| Season | `state.season`（`2026/27`） |
| 阵型偏好（可选） | 近 N 轮 `decision.formation` 众数 |

#### B. 当前战绩

| 展示项 | 数据源 |
|---|---|
| Overall Rank | `state.rank` |
| Gameweek Rank | 最近已结算 `history[i].rank`（state 无当轮 GW rank） |
| Total Points | `state.points` |

#### C. 最近一次思考

- 取最新 `history` 条目的 notes，按 §5.4 规则压缩为 **1 条摘要（≤ 60 字）**。
- 点击可跳转到 Main ④ 本轮思考日志（锚点）。

#### D. External Links（预留结构，本阶段不设计样式）

- 空容器 `<aside id="external-links">` + `<ul>` 占位，预留插入：公众号、小红书、GitHub、FPL 页面、个人主页、赞助链接。
- 数据驱动：预留 `web/config.json`（或 index.html 内 JS 常量）`externalLinks: [{label, url, icon}]`，空数组时整个区块不渲染。
- 本阶段不设计任何样式，仅保证 DOM 结构与渲染逻辑存在。

---

## 4. Main Content 区块

### 4.1 本轮成绩（摘要卡）

- Overall Rank / Gameweek Rank / Total Points / Bank / 当前 GW，5 张摘要卡（数字大、标签小）。
- **Bank 修复**（保留 v1.0 §6.1 结论）：`state.bank` 为 FPL API 0.1M 单位，前端 `/10` 显示 `£2.0m`。

### 4.2 AI 本周动态（This Week）

保留 v1.0 §6.3 设计：GW 木牌标题、队长/副队长/阵型、转会摘要（out→in + reason）、无转会则「本轮未进行转会」。本阶段用默认样式卡片实现，不做任务卡美术。

### 4.3 本轮阵容

- 首发按位置分行（GKP/DEF/MID/FWD）+ 替补 4 人（门将恒第 1）。
- **球员卡字段**（保留 v1.0 §6.2 数据映射，视觉延后）：

| 展示项 | 数据源 | 处理 |
|---|---|---|
| 近期状态 Form% | `score_breakdown.form` | 0–100 直接 `%`；GKP 无值不渲染 |
| 进球预测 Goal Potential% | `score_breakdown.projection` | 0–100 直接 `%`；无值不渲染 |
| 赛程难度 Fixture% | `score_breakdown.fixture` | 0–100，越高越友好；全位置有值 |
| 持有率 TSB% | `selected_by` | 已是百分比 |

- 移除 M/本轮/爆发 chips、AI 首发总分、C 爆发分。
- 本阶段进度条用简单 CSS 条（圆角 + 单色填充），不做像素格子/阈值配色。

### 4.4 AI 思考日志（本轮 + 历史）

数据源：`history[i].notes`（结构化 `{topic, detail}`）。渲染规则沿用 v1.0 §6.5 topic 映射表，**新增字数限制**（见 §5.4）。

### 4.5 历史记录（Accordion）

保留 v1.0 §6.6 结构：每 GW 折叠条（收起态显示 GW/积分/当轮排名/总排名），展开 4 区：

1. 比赛结果（积分 / GW Rank / Overall Rank）
2. 实际操作 Transfers（out→in + reason；无则「本轮未进行转会」）
3. AI 思考日志（§5.4 规则）
4. 思考时间 Decision Date（`decided_at`）

删除 AI首发总分、C爆发分。

### 4.6 趋势曲线（三条）★ v1.1 升级

不再只展示 Overall Rank，新增三个维度：

| 图表 | 数据源 | Y 轴方向 |
|---|---|---|
| **Overall Rank Trend**（总排名变化） | `history[i].overall_rank` | 反向（数字越小越靠上） |
| **Gameweek Points Trend**（每轮得分变化） | `history[i].points` | 正向（越大越靠上） |
| **Gameweek Rank Trend**（单轮排名变化） | `history[i].rank` | 反向（数字越小越靠上） |

**表现形式**（本阶段先落地一种，最终形态后定）：

- **首选：一个图表容器 + 三个 Tab 切换**（Overall / GW Points / GW Rank），切换仅换数据集与 Y 轴方向，SVG 重绘。移动端省空间。
- 备选：三个独立小图纵排。
- 未结算轮次（字段为 null）不画点；GW Points 图中 null 轮次跳过。
- 排名类 Y 轴反向映射 + 千/百万格式化（`1.24M`）；GW Points 正向 + 整数刻度。
- 实现继续手绘 SVG，零依赖。

---

## 5. AI 思考日志：字数限制 ★ v1.1 新增

**目标**：每条日志 50–120 字、最多 3 段、10 秒内读完；避免长篇分析报告。

### 5.1 限制策略（前端聚合层）

`notes[]` 条目数不定、detail 长短不一，直接全量渲染会失控。聚合规则：

1. **选取**：按优先级取 notes——`transfer_in/transfer_out` > `captain` > `no_transfer` > `lineup` > `transfer_status` > `squad_source` > 其他；最多取 **3 条**（= 最多 3 段）。
2. **压缩**：每条渲染为一段，正文 = `detail`（`{player}` 类 note 拼入球员名）；单段超过 **120 字**截断加「…」；不足 50 字不强制扩写（如实展示）。
3. **顺序**：按叙事逻辑排序——转会操作 → 队长选择 → 排兵/额度，而非原始 notes 顺序。
4. **次要信息折叠**：`external_source` / `data_missing` / `warning` 不进正文，收进「更多细节」折叠（`<details>`），点开才显示——保证 10 秒读完正文。
5. **Sidebar 摘要**：取上述第 1 条（最高优先级）截断至 **60 字**。

### 5.2 示例

输入 notes（真实数据结构）：

```json
[
  {"topic": "no_transfer", "detail": "市场共识未明显转向，全员健康，不进行转会"},
  {"topic": "captain", "player": "João Pedro", "detail": "队长评分最高（63.4），把握大"},
  {"topic": "external_source", "detail": "FPL Joe 已刷新: ... 写入 3 个文件"}
]
```

渲染结果（3 段内、10 秒可读）：

> 本轮未进行转会——市场共识未明显转向，全员健康。
> 队长交给 João Pedro，他的评分最高，把握大。
> ▸ 更多细节（数据更新记录）

---

## 6. 数据契约变更

### 6.1 `decided_at`（保留 v1.0 §7.1）

`brain/history_writer.py` `upsert_decision()` 新增 `decided_at`（ISO 8601 UTC），实现阶段落地；前端缺失时显示「-」。

### 6.2 Sidebar Profile 映射（纯前端推导）

| 展示 | 推导逻辑 |
|---|---|
| Manager Style | `strategy_snapshot.strategy`：`market_consensus` → 「市场共识型」；未知值 → 原样展示 |
| Risk Level | `allow_hits=true` → 「High」；`max_free_transfers ≥ 5` 或 `allow_hits=false` → 「Low」；其余 → 「Medium」。映射表前端常量，后续可调 |
| Season | `state.season` |

> 均从 `history` 最新条目的 `strategy_snapshot` 读取；字段缺失时 Profile 对应行显示「-」，不阻塞渲染。

### 6.3 兜底（保留 v1.0 §7.3）

`score_breakdown.*` null → 进度条整条不渲染；`overall_rank/points/rank` null → 曲线跳点、Accordion 头标「待结算」；`decided_at` 缺失 → 「-」；`recommended_transfers` 空 → 「本轮未进行转会」；`externalLinks` 空 → External Links 区块不渲染。

---

## 7. 技术实现策略

- **架构延续**：无构建静态站（`index.html` + `app.js` + `style.css`），fetch `data/*.json`；Vercel + Actions 链路不变。
- **布局实现**：CSS Grid 两栏（`grid-template-columns: 25% 1fr`，间距用 `gap`）；Sidebar `position: sticky`；≤768px 切单列、Sidebar 隐藏、摘要条显示（同一渲染函数，两个挂载点）。
- **图表**：一个 SVG 容器 + Tab 切换（三数据集），手绘零依赖。
- **Accordion**：原生 `<details>/<summary>`，无障碍友好。
- **External Links**：`web/config.js` 常量数组（或后续改 fetch config.json），空则不渲染区块。

---

## 8. 验收标准

### 8.1 内容理解验收（本阶段核心）

用户第一次打开网站时能够理解：

| # | 问题 | 落点 |
|---|---|---|
| 1 | 这个 AI 是谁 | Sidebar Profile（Style/Risk/Season）+ 顶部名字 |
| 2 | 这个 AI 本周干了什么 | Main ② AI 本周动态 |
| 3 | 这个 AI 成绩怎么样 | Sidebar 当前战绩 + Main ① 摘要卡 + ⑥ 曲线 |
| 4 | 这个 AI 为什么这么做 | Main ④ 思考日志 + Accordion 内日志 |
| 5 | 这个 AI 过去表现如何 | Main ⑤ Accordion + ⑥ 三条趋势 |

### 8.2 功能验收（继承 v1.0 中内容相关项）

| # | 验收项 | 状态 |
|---|---|---|
| 1 | Bank 单位修复（/10） | ✓ 本阶段 |
| 2 | 删除 M/本轮/爆发 | ✓ 本阶段 |
| 3–6 | Form / Goal Potential / Fixture / TSB 展示 | ✓ 本阶段 |
| 7–8 | 删除 AI首发总分 / C爆发分 | ✓ 本阶段 |
| 9 | 排名曲线 | ✓ 本阶段，升级为三条（§4.6） |
| 10 | AI 本周动态模块 | ✓ 本阶段 |
| 11 | Accordion 历史 | ✓ 本阶段 |
| 12 | 每轮转会记录 | ✓ 本阶段 |
| 13 | AI 思考日志 | ✓ 本阶段，加字数限制（§5.1） |
| 14 | 思考时间 | ✓ 本阶段（需 decided_at） |
| 15 | 动森视觉风格 | ⏸ Phase 3 |
| 16 | 移动端优化 | ✓ 本阶段仅布局层（Dashboard 折叠）；美术层 Phase 3 |
| 17 | 仅提交 test | ✓ |
| 18 | Dashboard Layout（25/75 + 移动折叠） | ✓ 本阶段新增 |
| 19 | Sidebar：Profile / 战绩 / 最近思考 | ✓ 本阶段新增 |
| 20 | External Links 结构预留 | ✓ 本阶段新增，仅结构 |
| 21 | 思考日志 50–120 字、≤3 段、10 秒读完 | ✓ 本阶段新增 |

---

## 9. 风险与待决

### 9.1 风险

| 风险 | 缓解 |
|---|---|
| notes 条目过多/过长导致日志超限 | §5.1 选取+截断策略；「更多细节」折叠兜底 |
| `strategy_snapshot` 早期数据缺失 | Profile 行显示「-」，不阻塞 |
| 三条曲线数据早期只有 1–2 个点 | SVG 至少两点才画线，单点画圆点+数值 |
| Sidebar 与移动摘要双挂载点数据不一致 | 同一渲染函数、同一数据源（§3.2） |

### 9.2 待决项（继承 v1.0 §10.3 + v1.1 新增）

1. **AI 经理名/人格名**（待主人定调）
2. **转会措辞**：「本周采纳的建议」vs「本周操作」（AI 不自动提交 FPL）
3. **曲线最终形态**：Tab 切换（本阶段首选）vs 三独立图，待实现后看效果定
4. **Risk Level 映射口径**：§6.2 为初版映射，是否需更细策略指标待议

---

## 附录 A：Phase 3 视觉设计预留（v1.0 §4 存档，暂缓执行）

以下内容整体延后至 Phase 3，此处仅存档 token 与组件方向，避免后续重想：

- 配色 token：草地绿 `#7CB342` / 天空蓝 `#A6D8FF` / 奶油白 `#FFF8E7` / 浅木色 `#D7B377` / 暖黄 `#FFD54F` / 莓红 `#E57373` / 暖棕文字 `#3E2723`
- 字体方向：Fredoka / Baloo 2（标题）+ 系统圆体兜底
- 组件方向：像素风进度条（阈值分级）、动森信件（AI Thoughts）、游戏任务卡（This Week）、木牌徽章（C/V）、内嵌 SVG 小葱头像
- 移动端美术：大间距、贴纸质感、草地纹理 pitch

> Phase 3 换任何主题（动森/足球经理/像素游戏/杂志风）都不影响本阶段建立的内容架构——这正是先做 Phase 2.5 的理由。
