# FPL AI Manager — FPL API 资料

> 更新：2026-08-29。本文档是端点与字段备忘，不是规范；开发时以实际 API 响应为准。

## 1. 公开端点（无需登录）

| 端点 | 用途 | 决策所需关键字段 |
|------|------|------------------|
| `GET /api/bootstrap-static/` | 球员/球队/event/位置类型静态数据 | `elements[].selected_by_percent`（TSB）、`transfers_in_event`、`transfers_out_event`、`chance_of_playing_next_round`、`status`、`now_cost`、`element_type`、`team`、`web_name`；`events[].finished / deadline_time / is_current / is_next` |
| `GET /api/fixtures/` | 全赛季赛程 | 当前阶段仅验证连通性，不落盘 |
| `GET /api/entry/{id}/` | 队伍概况 | `summary_overall_points`、`summary_overall_rank` |
| `GET /api/entry/{id}/history/` | 每轮历史 | `current[].{event, points, rank, overall_rank, bank, event_transfers}`（event_transfers 用于免费转会备用推导）；`chips[]`；`past[]` |
| `GET /api/entry/{id}/event/{gw}/picks/` | 指定轮首发/替补/队长 | `picks[].{element, position, multiplier, is_captain, is_vice_captain}`；`entry_history.bank` |
| `GET /api/event/{gw}/live/` | 实时分数 | 结算阶段核对用（可选） |

## 2. 鉴权端点（Phase 3）

| 端点 | 用途 |
|------|------|
| `GET /api/me/` | 获取 manager_id（初始化用） |
| `GET /api/my-team/{id}/` | 真实当前阵容 / 银行 / Chips / **`transfers.limit`（免费转会数主来源）** |
| `POST /api/my-team/{id}/transfers` | 提交转会 |
| `POST /api/my-team/{id}/picks` | 提交阵容 / 队长 |

## 3. 字段映射备忘（决策用）

| 字段 | 类型 | 说明 |
|------|------|------|
| `selected_by_percent` | string | 持有率 TSB%，如 `"42.3"` |
| `transfers_in_event / transfers_out_event` | int | 本周转进 / 转出量（活跃玩家行为） |
| `chance_of_playing_next_round` | int \| null | `null` 视为健康 |
| `status` | string | `a`=available、`i`=injured、`d`=doubtful、`u`=unavailable、`n`=not in squad |
| `now_cost` | int | 价格 ×10（如 `151` = £15.1m） |
| `element_type` | int | 1=GKP 2=DEF 3=MID 4=FWD（以 `element_types` 映射为准） |

## 4. 登录现状（Phase 3 开发时验证）

- 经典流程：`POST https://users.premierleague.com/accounts/login/`（form: login/password/app/redirect_uri）→ 会话 Cookie `pl_profile`（长有效期），后续请求复用同一会话。
- **注意**：FPL 登录流程在 2025-26 赛季起有调整（SSO 化），Phase 3 开发时必须按官方最新认证流程验证适配器；本项目用可替换 Auth 适配器隔离此风险。
- 凭证只进 GitHub Secrets（`FPL_EMAIL` / `FPL_PASSWORD` / `FPL_SESSION_COOKIE`），绝不进仓库。

## 5. 频率与合规

- 公开端点拉取频率 ≤ 每小时 1 次（本项目实际每 6–8 小时一次）。
- 鉴权端点仅在 Phase 3 执行阶段调用，且每次调用写入审计。
- 单账号、低频调用，符合 FPL 服务条款预期；避免多账号与高频轮询。

## 参考资料

- [FPL 登录认证说明（Stack Overflow）](https://stackoverflow.com/questions/62828619/how-to-login-in-fantasy-premier-league-using-python)
- [pyfpl 认证文档（fpl Python 包）](https://fpl.readthedocs.io/en/latest/_sources/classes/fpl.rst.txt)
- [HaydenMacDonald/fpl — FPL API Python 封装](https://github.com/HaydenMacDonald/fpl)
