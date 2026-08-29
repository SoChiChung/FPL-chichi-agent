# FPL AI Manager — 未来功能与扩展设计

> 更新：2026-08-29。本文档记录明确不做但已预留接口的功能，以及 Phase 3 自动执行模块的完整设计。

## 1. 预留接口（当前仅占位）

`brain/strategy.py` 中保留以下接口，均无业务逻辑：

```python
class CaptainSelector:     # Phase 1 实现：get_captain_score()，当前=Market Score，未来=xP
class FreeTransferProvider:# Phase 1 实现：优先 FPL API（my-team.transfers.limit），备用 event_transfers 推导
class OwnershipStrategy:   # Phase 2：Ownership v2 / EO / Differential
class ChipStrategy:        # Phase 2：Wildcard / Free Hit / Bench Boost / Triple Captain
class Executor:            # Phase 3：自动登录 + 提交转会/阵容/队长（含安全门与审计）
```

## 2. xP（Expected Points）接入规划（Phase 2）

- 目标：`Captain Score = xP`（替换当前 Market Score 实现即可，接口不变）。
- 数据源：外部 xP API（用户自选，届时评估提供方与格式）。
- 影响面：仅 `CaptainSelector.get_captain_score` 一个实现点；history `metrics` 增加 `captain_xp` 等键（结构已预留）。

## 3. Ownership Strategy v2（Phase 2+）

概念设计（届时以独立配置承载，如 `config/ownership-rules.json`）：

- 模式：`high_ownership`（跟随主流）/ `differential`（追求差异）/ `balanced`（均衡）。
- 赛季内切换计划（如中段转 differential 冲排名）。
- EO（Effective Ownership）队长权衡、热球员出售保护线。
- 与 Market Consensus 的关系：Market Score 是"市场共识"，Ownership v2 是"与共识的偏离策略"，两者在 Phase 2 组合使用。

## 4. Chip 策略（Phase 2+）

- Wildcard / Free Hit / Bench Boost / Triple Captain 均为配置驱动的规则（如"WC 第 10 轮附近"），不硬编码。
- 决策日志预留 `chips_used` 字段。

## 5. 自动执行模块设计（Phase 3，本阶段只设计不实现）

### 5.1 登录与会话

```
┌─ Auth 适配器（可替换）────────────────────────────────┐
│  FPLAuthProvider (interface)                          │
│   ├─ login(email, password) → session                 │
│   ├─ get_session() → 从 Secret 恢复                    │
│   └─ refresh() → 会话过期时自动重登                    │
│  基线实现：POST users.premierleague.com/accounts/login/│
│  会话 = Cookie: pl_profile（长有效期）                 │
│  ⚠ 登录流程已 SSO 化，开发时按最新流程验证              │
└──────────────────────────────────────────────────────┘
```

- 凭证只进 GitHub Secrets（`FPL_EMAIL` / `FPL_PASSWORD` / `FPL_SESSION_COOKIE`），绝不进仓库。
- 手动方式：登录一次后把 `pl_profile` 存入 Secret，直接复用；自动方式：存账号密码，过期自动重登。

### 5.2 自动提交动作

| 动作 | API | 说明 |
|------|-----|------|
| 转会 | `POST /api/my-team/{id}/transfers` | 单笔/双笔；扣分与 hit 由 FPL 端结算 |
| 阵容/队长 | `POST /api/my-team/{id}/picks` | 首发 XI + 队长 + 副队长 + 替补排序 |
| 免费转会数 | `GET /api/my-team/{id}/` | `transfers.limit`（FreeTransferProvider 主来源） |
| 核对 | `GET /api/my-team/{id}/` | 提交前读真实状态，提交后校验结果 |

### 5.3 安全门（执行前必须全部通过）

```
G1 时间门   距 deadline ≥ 15 分钟（可配），否则不执行
G2 信心门   各类操作满足配置阈值
G3 合法性门 预检：银行≥0、位置数、同队≤3、Chip 可用性、免费转会额度
G4 热球员保护 卖出高持有球员需证据权重达标，否则拒绝并标记 risky
G5 幂等门   POST 前 GET 真实状态，只提交差异；无差异则跳过
G6 试运行门 默认 dry_run=true，只把"将执行的操作"写入历史，不调 API
G7 审计门   每个操作写入 data/actions.json（时间/载荷/响应/risky），
            下一轮结算时对账
```

**部分失败处理**：步骤按序执行、逐步校验；任一步失败即中止后续、保留已成功步骤，标记 `partial_failure` 等待人工处理（不自动重试，防止重复扣分）。

**回滚**：同 GW 内 deadline 前可反向转会恢复（代价 = 4 分 hit），人工确认后触发；不提供自动回滚。

### 5.4 审计

`data/actions.json`（独立于决策历史，只记实际执行操作）：

```json
{
  "actions": [
    {
      "at": "2026-09-05T17:00:00Z", "gw": 5,
      "action": "transfer", "payload": {"out": 5, "in": 8},
      "api_response": "success", "success": true,
      "risky": false
    }
  ]
}
```

原则：**决策历史 ≠ 操作历史**。决策可随意回看，操作必须可审计、可对账。

## 6. 明确不实现清单

- xG / xA / EV / Monte Carlo / ML / 复杂优化器（当前策略不依赖）
- 自动登录、自动转会提交、自动阵容提交、自动队长提交（Phase 3 前）
- 多账号支持
- 付费 API / 数据服务（除非用户明确引入）
