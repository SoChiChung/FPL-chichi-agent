# FPL AI Manager 项目长期记忆

## Git 工作流约定

- 仓库：`origin = git@github.com:SoChiChung/FPL-chichi-agent.git`，主开发分支 `main`，另有 `test` 分支用于阶段性开发。
- **`data/` 目录全部是抓取缓存与运行时状态（fpljoe 快照、history.json、state.json），不重要。**
  - 合并/拉取时若 `data/` 产生冲突，**一律保留 main 本地版本**（`git checkout --ours`），舍弃对方分支的 data。
  - 用户倾向于让 `data/` 不进版本控制。
- 推送前注意检查是否有**未完成的 merge** 挂在 HEAD 上（`cat .git/MERGE_HEAD`）。
- **test 同步到 main**：main 每次 merge test 后，test 的历史已含于 main，同步只需 `git checkout test && git merge --ff-only main`（零冲突），再 `git push origin test`。
- **SSH 访问 GitHub 需在沙箱外执行**：沙箱会拦截 `~/.ssh`，git fetch/pull/push 要请求授权（escalation）。

## 定时更新架构（重要）

- 定时更新**不靠 Vercel**，靠 **GitHub Actions**（`.github/workflows/update.yml`）：cron 每 30 分钟自触发 → 闸门 `brain/scheduler.py`（只读 `data/state.json` 判断是否 due）→ `python -m brain` 拉数据生成 state/history → `git add data/` commit+push → Vercel 收到 push 自动重部署静态站。
- 调度节奏（2026-09-04 起统一）：赛季中（有 next_deadline）**统一每 30 分钟更新一次**，不再按 deadline 远近分档；休赛期（无 deadline）每天探测一次。
- **GitHub schedule 是"尽力而为"，cron 不保证准点**，高负载时延迟/丢弃。曾用 `*/10` 实际只触发 3-4 次/天（被限流），改 `*/30` 后压力减小但仍不保证严格 30 分钟。要严格准点需外部调度器（cron-job.org / Cloud Scheduler）调 workflow_dispatch。
- workflow 已修好两个坑：①`python`→`python3`（ubuntu-latest 无 python）；②`git push`→`git push origin HEAD:main`（checkout 是 detached HEAD）。并加 `permissions: contents: write` + `fetch-depth: 0`。
- 依赖前提：仓库需 **public**；Actions 需启用；Vercel Git 集成需连 main。

## 已知待办 / 小瑕疵

- `data/` 已加入 `.gitignore` 的条目被**刻意移除**（`2836dde`）：`data/state.json` 是 Actions 闸门的输入、必须留在版本控制内，ignore 反而会挡住 workflow 的 `git add data/`。所以 data 保持被 git 跟踪，合并冲突靠 `.gitattributes` 的 `data/** -merge` 保留 HEAD 版本。
- 注意：bot 每次自动更新会往 origin/main push 一个 `chore(data)` 提交，本地若不同步会分叉；推送代码前先 `git pull --rebase origin main`。

## Phase 2.5 内容架构（2026-09-09，已 commit `345ea6f` 在 test）

按 `docs/design.md` v1.1 落地：Dashboard Layout（Sidebar 25% + Main 75%）+ Main ①-⑥ 区块。视觉延后 Phase 3，本阶段保留深色 token，不做动森皮肤。

- ① 5 卡：当前 GW / Overall Rank / Gameweek Rank / Total / Bank（`state.bank` 0.1M → `/10` 显示 ` £2.0m`）。
- ② 本周动态：阵型、队长/副队长、转会 out→in + reason，无转会提示。
- ③ 阵容：按位置分行 + 替补门将恒首位；球员卡 `Form / Goal Potential / Fixture / TSB` 4 进度条（无值整条不渲染，GKP 仅 Fixture/TSB）。已删除 `M/本轮/爆发 chips、AI首发总分、C爆发分`。
- ④ 思考日志聚合：top3 段（50-120 字截断），次要 topic 进 `<details> 更多细节`。转会按 `transfer_out`/`transfer_in` 原始顺序配对，再按叙事序合并。
- ⑤ 历史 Accordion：每轮 4 区（结果/转会选择/思考日志/决策时间）；未结算字段统一「待结算」；`decided_at` 缺失显示 `-`。
- ⑥ 趋势曲线：一个 SVG 容器 + 三 Tab（Overall Rank / GW Points / GW Rank）切换；rank 反向 Y 轴 + `k/M` 压缩；null 不画点。
- External Links 数据驱动：新增 `web/config.js`（`window.SITE_CONFIG.externalLinks[]`），桌面挂 Sidebar 底部、移动端收进页脚（同一渲染函数双挂载点，CSS 控制显隐）；空数组时 `ext-empty` 类隐藏。

## 无头浏览器验证方式（沙箱内 Chrome 渲染坑）

- `python -m http.server` 起在本机可 curl 200（`127.0.0.1` + `0.0.0.0` 都试过），但 Chrome headless 通过 HTTP 访问时 `ERR_CONNECTION_REFUSED`（沙箱强制代理，Chrome 即使加 `--no-proxy-server` 也仍走代理 hook）。
- **解决方案**：用 `file://` 协议直接加载本地文件，必须同时给 Chrome 加 `--disable-web-security --allow-file-access-from-files`，否则 file→file fetch 被 CORS 拦。
- 截图：`chrome.exe --headless=new --disable-gpu --hide-scrollbars --no-proxy-server --disable-web-security --allow-file-access-from-files --user-data-dir=<tmp> --window-size=1440,2600 --virtual-time-budget=8000 --screenshot=<absPath> file:///D:/.../web/index.html`
- 截图输出路径必须是 Windows 绝对路径（`C:\Users\ddead\AppData\Local\Temp\xxx.png`），用 Git Bash `/tmp` 不被识别。
- 桌面 1440 宽度、移动端用 400 宽度即可触发 `@media ≤768px` 折叠规则。

## Frontend BugFix UX v1.0（test commit 6d308e5）

后端契约新增（docs/frontend-bugfix-ux.md v1.0）：
- `history[i].decided_at` UTC ISO；upsert_decision setdefault 写；已存在不覆盖。
- `history_writer.backfill_settlement(history, current_rows, events)` 幂等：按 `events.finished` 回填已有 history 条目的 points/rank/overall_rank（仅补 null、不回退、不建孤儿）。
- `state.suggested_squad` 15 人完整快照（带 starting/is_captain/multiplier/breakdown/lineup_score），无建议转会时不写；由 `_build_suggested_squad` 在 __main__ 生成，in 球员用 `lineup_score.score_squad([incoming], ...)` 单点补全（recent_points 缺自动按 0）。
- `context.build_state`：`points/rank` 缺失保留 null 而非 `or 0`（前端显示「待结算」）。

前端（app.js 字段感知 + 视图切换）：
- 空态按字段区分：rank→「待结算排名」、points→「待结算」；helper `nullText(kind)`；`fmtNumber(n, kind)` / `accText(v, kind)`。
- `fmtTime` 强制 `timeZone: "Asia/Shanghai"`。
- 阵容双视图：module `squadView` ∈ {"cur","sug"}；`playerResolve(state, ctx)` 加 `ctx.suggested` 分支（suggested 池用 starting/flags 决定 XI/bench/C/V）；`renderSquadToggle(state)` + 一次绑定 `bindSquadToggle(state)`；URL `?view=sug|cur` 支持直达。
- `renderExternalLinks(state)` 升级「关于我」品牌卡（brand{name,handle,tagline} + heroMetrics 白名单 + 平台导航）；图标 16px/opacity .55 降权。
- `web/config.js` 结构：brand / heroMetrics / externalLinks 三段（外部新增项只用动 config，无需改 JS）。
- `index.html` ③ 区加 `.squad-bar`（formation + seg 同行）+ `#squad-note`；CSS 新增 .seg/.seg-btn、.ext-* 层级化、#thoughts-gw flex 行（标题 + 时间）。

测试：tests/test_settlement_backfill.py 7 项单测，全量 91/91 OK。

经验沉淀：
- FPL API `entry_history.current` 仅返回实际参赛轮次（账号首次真赛行），GW2 等 FPL 无行无法回填 → history 永远 null（属数据自然限制，非缺陷，文档已说明）。
- `lineup_score.score_squad` 对单元素 list 也安全，in 球员 `recent_points` 缺即按 0 处理（streak_map 查询空位串默认 0）→ suggested_squad 评分无副作用。
- data 文件头部状态 `transfer_out`/`transfer_in` notes 经 buildThoughts 的 transfer 配对算法天然产生 Calvert-Lewin→Haaland 类（GW3）/ M.Sangaré→Gakpo（GW4）示例。

未推送：test 领先 origin/test 6 commits（39ec862/ea0d4ab/fdb4998/345ea6f/8055599/6d308e5），push 待用户授权。

## Phase 3 前置 IA/内容/UX 设计（2026-09-09 晚，docs/design-ia-ux.md v1.0）

主人拍板：**Main 主区顺序＝阵容在思考日志前**（本周动态→本轮成绩→阵容→思考日志→历史→趋势，仅成绩与本周动态对调）；**设计文档独立成文**，不覆盖 design.md。视觉(Phase 3) 启动前以此档为基准。

关键口径（快照实证 `state.formation=442` vs `decision.formation=352`）：`decision.*`=AI 决策（本周动态/转会后视图用），`state.*`=FPL 官方实际（当前阵容视图用），同页同口径只出现一次。GW 三态「未开始/进行中/已结算」全局标签；Header 时间戳=last_update。现 config tagline「FPL Analytics · Data Tracking」与养成叙事冲突待换。待拍板：AI 人格名 / 转会建议措辞 / tagline / gw_meta 增量等（档 §10）。

## Phase 3 动森皮肤换皮（2026-09-09 晚，web/ 工作区改动未 commit）

`docs/design-ia-ux.md` §8 落地为视觉：奶油纸/木牌/草地 token；h2 木牌徽章 🍃；球场行草皮+条纹；C/V/新/出四色徽章；Accordion 木纹历史册；天空→远丘背景。App.js 仅增量：fmtFormation/gwStateLabel/lastSettledInfo、renderSummaryCards 改 4 卡（去 Overall Rank 防重复）、renderThisWeek 加 GW 徽章+三态+截止+🍃CTA 联动、playerCard 新增 diff 角标（outIds/inIds Set）、TREND_META 换叶橙/叶绿/天蓝三色。

## v1.3 布局口径（2026-09-09）：抬头品牌 ZCJenius / 桌面三栏 215-1fr-270 / 阵容每位置单行(≤768换行) / 页脚 .foot-promo 强调 FPL紫葱酱
- 坑：WorkBuddy 预览器会往 index.html 注入 data-page-node-id 属性；html 改动遇此需整份重写。

## v1.4 关键口径（2026-09-09 21:2x）
- **转会后阵容 = 前端推导**：以 `decision.starting_xi/bench` 原位替换 out→in；C/V 沿用 `decision.captain/vice`（被转出时按 `captain_scores` 顶替）；`state.suggested_squad` 仅作为入队球员卡面数据源；squadViewEnabled 改为「有转会即启用」。
- **桌面布局回到两栏**：215px 身份栏 + 主区；container max 1280px；趋势曲线在「本轮阵容」之上、历史记录在「AI 思考日志」之上。
- **关于我**：桌面左栏；移动端主区末尾挂载（`.ext-mobile` ≤768 显示）；不再展示 Overall Rank/总分；heroMetrics 配置项删除。
- **阵容卡 176px 上限 + flex 不放大 + space-evenly**：5 人放得下、1 GK 单卡居中不拉长；移动端 45% 可换行。

## v1.5 关键口径（2026-09-09 21:36）
- **DDL 倒计时**：① 本周动态 `.tw-ddl` 行 = 「🕒 GWx 截止 M月D日 HH:MM（北京时间）」+ 黄色 `.tw-cd` 胶囊（tabular-nums 秒级走字）。过期转灰定格。源 `state.next_deadline` ISO。
- **历史过滤 `historyVisible`**：结果任一非 null OR `decided_at` OR `budget_before/after`；GW2（空洞轮）整条隐藏。趋势图同步过滤（X 轴只显示有真赛行的轮次）。
- **历史「进行中」**：summary 用「本轮进行中」状态徽章（gw-state.st-live 样式）；body「比赛结果」区显示说明行，不写「待结算」占位。
- **关于我 = 头像 + 品牌名 + 平台导航**；tagline 已废弃不再渲染；avatar 路径 `img/ava.png` 由 Vercel build `cp img/*.png` 复制到 web/img/。
