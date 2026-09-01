# FPL Joe 外部预测数据接入设计（v1 草案）

> 版本：v1　日期：2026-08-30　状态：**数据层已实现，决策层待接入（2026-08-31）**
> 目标：把 FPL Joe 的比赛概率数据作为**辅助信号**接入，Market Score 仍是核心信号；外部源可替换；抓取失败可回退。

## 实现进度

| 层 | 状态 | 说明 |
|----|------|------|
| 抓取（数据层） | ✅ 已实现 | `brain/external/fpl_joe.py`：抓取 + 双数据源标准化 + 三文件落盘（`data/external/fpljoe/`） |
| 配置 | ✅ 已实现 | `config/fpl_joe.json`（端点/TTL/超时/输出目录/未来轮数） |
| 新鲜度 | ✅ 已实现 | `brain/external/freshness.py`（fresh/stale/expired/unknown） |
| 管线接入 | ✅ 已实现 | `__main__.py` 每次运行刷新（本地 `npm start` 与云端 GitHub Actions 均生效），失败不阻塞、不覆盖旧数据，history notes 记录来源与新鲜度 |
| 测试 | ✅ 已实现 | `tests/test_fpl_joe.py`（10 项） |
| 决策层接口（ExternalSignalProvider / FixtureContext 消费） | ⏳ 下一阶段 | 数据字段已就绪（三文件 + metadata），接入点见第 6 节 |

---

## 0. 设计原则与约束

| # | 原则 | 含义 |
|---|------|------|
| E1 | 弱耦合 | FPL Joe 是外部比赛概率源，与 FPL 官方 API 完全解耦：官方数据提供 fixture 结构/team id/deadline，FPL Joe 只提供概率字段，二者只通过内部统一的 Fixture Context 汇合 |
| E2 | 源无关 | 外部源的原始字段不得散落到 strategy.py / market.py / lineup.py；一律先转换为内部 FixtureSignal 再向上传递 |
| E3 | 核心不变 | Market Score 公式（TSB+trend）与现有转会/队长逻辑**不修改**；fixture 信号先记录与展示，决策接入在后续阶段、以配置开关启用 |
| E4 | 可替换 | 通过 ExternalSignalSource 抽象基类支持多源；新增源只需实现接口 + 注册，决策层无感知 |
| E5 | 失败回退 | 抓取失败/过期 → 使用上一份有效缓存（标记 stale）；无缓存 → 该 GW 无外部信号，决策纯 Market Score，绝不中断管线 |
| E6 | 可审计 | 每次落盘必须带 meta：source、fetched_at、source_updated_at、freshness、fields_present |
| E7 | 零依赖 | 抓取沿用 urllib（同 api.py 的 `_fetch` 模式）；缓存为 JSON 文件原子写（复用 data_store 模式） |

---

## 1. 与现有架构的关系（含冲突与调整）

### 1.1 需要明确调整的现有约定

1. **"原始数据不落盘"例外条款**（[architecture.md](architecture.md) 4.2 节）：外部源数据**必须**落盘才能实现 E5 回退。调整方式：新增 `data/external/` 目录专存外部源缓存（含 meta），与 `state.json` / `history.json` 主数据结构完全分离，不进 Git 数据主文件；FPL 官方原始数据仍不落盘。需在 architecture.md 4.2 节补充例外条款。
2. **现有管线无 fixture 注入点**：当前 __main__.py 流程为 fetch → market → squad/lineup/captain/transfer → 写 JSON，没有赛程信号环节。调整方式：在 `context.build_state` 之前插入 `fixtures` 层，先只把信号写入 state / history / 前端，不消费。
3. **决策层接口模式统一**：沿用现有 `CaptainSelector` / `FreeTransferProvider` 的"接口在 strategy.py、实现另立模块"模式，新增 `ExternalSignalProvider` 接口（接口在 strategy.py，实现只出现在 external/ 与 fixtures/，原始字段不进入 strategy.py）。
4. **roadmap 无此条目**：Phase 2 增加"外部预测源接入"一行。

### 1.2 现有代码中与此设计相关的事实

- `brain/api.py` 的 `_fetch`（超时 + 重试 + FplApiError(status)）可直接作为 FPL Joe 抓取模板，但**不复用** `api.py`（避免强耦合，E1）。
- `bootstrap.teams`（id / name / short_name）可用于动态构建球队映射，人工修正表只作兜底。
- `market.to_float` 的异常安全转换可复用于外部信号字段标准化。
- `data_store.save_json` 的原子写模式复用于缓存写入。

---

## 2. 推荐目录结构

```
FPL AI Manager/
├── config/
│   ├── strategy.json          # 【改】增加 fixture_signal 预留权重（默认 enabled=false）
│   └── fpl_joe.json           # 【新】FPL Joe 源接入参数（端点/TTL/超时/映射修正表）
├── data/
│   └── external/              # 【新】外部源缓存目录（唯一落盘例外，见 1.1）
│       └── fpl_joe.json       #     最近一次有效抓取 + meta（覆盖式更新）
├── brain/
│   ├── external/              # 【新】外部预测源适配层（E1/E4：可替换、弱耦合）
│   │   ├── __init__.py
│   │   ├── base.py            # ExternalSignalSource 抽象基类 + SourceMeta + Freshness
│   │   ├── cache.py           # 外部源缓存读写（原子写、损坏容忍、meta 持久化）
│   │   ├── freshness.py       # 新鲜度判定（fresh/stale/expired）+ 回退决策
│   │   └── fpl_joe.py         # FPL Joe 适配器（抓取逻辑 TODO，仅骨架 + 字段声明）
│   ├── fixtures/              # 【新】fixture 信号层（E2：源无关的内部统一结构）
│   │   ├── __init__.py
│   │   ├── mapping.py         # 球队映射：bootstrap 动态 + fpl_joe.json 覆盖表
│   │   ├── signals.py         # FixtureContext / FixtureSignal 定义 + 标准化入口
│   │   └── difficulty.py      # 赛程难易度计算（基于概率信号，不依赖官方 FDR）
│   ├── strategy.py            # 【改】增加 ExternalSignalProvider 接口（仅接口）
│   ├── context.py             # 【改】build_state 附加 fixture 摘要（可选、降级友好）
│   └── __main__.py            # 【改】fetch 阶段接入 external 源（失败不阻塞管线）
├── tests/
│   └── test_fpl_joe.py        # 【新】mapping/cache/freshness/标准化/降级/难度测试
├── docs/
│   └── fpl-joe.md             # 【新】本文档
├── docs/architecture.md       # 【改】4.2 缓存例外 + 数据流图 + 目录树
├── docs/decision-engine.md    # 【改】第 10 节数据来源加 FPL Joe 行 + 外部信号接口章节
└── docs/roadmap.md            # 【改】Phase 2 增加外部预测源条目
```

---

## 3. 新增文件职责

### 3.1 brain/external/ — 外部源适配层

| 文件 | 职责 |
|------|------|
| `base.py` | `ExternalSignalSource` 抽象基类：`fetch()`（返回原始 dict）、`source_name()`、`source_updated_at(raw)`、`to_fixture_signals(raw, mapping)`；`SourceMeta` 数据结构（source/fetched_at/source_updated_at/freshness/fields_present）；`Freshness` 枚举（fresh/stale/expired） |
| `cache.py` | 读/写 `data/external/<source>.json`：原子写（复用 data_store 模式）；文件缺失或 JSON 损坏视为无缓存（返回 None，不抛）；读出的数据必须带 meta 才能被回退使用 |
| `freshness.py` | `judge(meta, ttl_hours, max_age_hours) → Freshness`；`resolve(source, config)` 编排：抓取成功→写缓存(fresh)；抓取失败→缓存未 expired 用旧数据(stale) 否则 None(expired)；返回带新鲜度标记的 FixtureContext 或 None |
| `fpl_joe.py` | FPL Joe 适配器骨架：声明源字段到 FixtureSignal 的字段映射表（projected goals / CS / 胜平负 / 比分 / FDR）；`fetch()` 为 TODO 占位（抓取实现下一阶段）；不做任何 FPL 官方 API 调用 |

### 3.2 brain/fixtures/ — 信号层（源无关）

| 文件 | 职责 |
|------|------|
| `mapping.py` | `build_team_mapping(bootstrap_teams, overrides) → {external_name_normalized: fpl_team_id}`：先用 bootstrap.teams 的 name/short_name 动态构建（小写去空格规范化），再套用 `fpl_joe.json.team_mapping_overrides` 人工修正；未命中的外部球队 → 对应信号置 None 并记 warning |
| `signals.py` | `FixtureSignal`（见第 4 节结构）与 `FixtureContext`（按 GW 组织 + meta）；`normalize_raw(source_raw, mapping)` 统一入口：把适配器的字段映射表 + 原始数据转换为 FixtureSignal，缺失字段置 None，不做数值推断 |
| `difficulty.py` | `fixture_difficulty(signal) → float`：基于 projected_goals_against / cs_probability 等概率信号计算的赛程难易度（对己方，数值越低越有利）；纯函数，便于测试；**不依赖 FPL 官方 FDR 规则** |

### 3.3 配置

| 文件 | 内容 |
|------|------|
| `config/fpl_joe.json` | 源接入参数（非决策参数）：`enabled`、`endpoint`（待抓取调研后填）、`timeout_seconds`、`cache_ttl_hours`(默认 6)、`max_age_hours`(默认 48)、`user_agent`、`team_mapping_overrides` |
| `config/strategy.json` | 【改】增加 `fixture_signal` 段（**决策**权重预留，默认 `enabled: false`，见第 7 节） |

---

## 4. Fixture Signal 内部统一结构（源无关）

```jsonc
// 每队每 GW 一个 FixtureSignal（外部源字段 → 标准化后）
{
  "fpl_team_id": 43,              // FPL 官方 team id（主键，由 mapping 保证）
  "opponent_id": 17,
  "gw": 3,
  "is_home": true,
  "projected_goals_for": 1.9,     // 期望进球 / projected goals（FPL Joe 提供时）
  "projected_goals_against": 1.1,
  "cs_probability": 0.31,         // 零封概率（可选）
  "goal_probability": {           // 胜平负概率（可选，缺则 None）
    "home": 0.42, "draw": 0.27, "away": 0.31
  },
  "score_prediction": "2-1",      // 比分预测（可选）
  "difficulty": 2.5,              // 赛程难易度（difficulty.py 计算产物，数值低=好赛程）
  "source": "fpl_joe",
  "source_updated_at": "2026-08-30T12:00:00Z"   // 源数据自带的更新时间
}

// FixtureContext：一次抓取的整体结果（决策层唯一读取对象）
{
  "gw": 3,
  "signals": { "43": { ...FixtureSignal... }, ... },   // key = fpl_team_id
  "meta": {
    "source": "fpl_joe",
    "fetched_at": "2026-08-30T13:00:00Z",   // 本项目抓取时间
    "source_updated_at": "2026-08-30T12:00:00Z",
    "freshness": "fresh",                   // fresh | stale | expired
    "fields_present": ["projected_goals_for", "cs_probability", ...],
    "mapped_team_count": 20,
    "unmapped_teams": []                    // 映射失败的外部球队名
  }
}
```

约定：所有概率字段 0-1 或 0-100 统一由适配器在转换时归一化；字段缺失 = `None`（不推断、不补 0）；`FixtureContext` 缺失（无缓存/过期）时决策层按"无外部信号"处理，不允许抛异常。

---

## 5. 数据流说明

```
┌─────────────┐   ┌───────────────────────────────┐
│ FPL 官方 API │   │ FPL Joe（外部，弱耦合，可替换） │
└──────┬──────┘   └───────────────┬───────────────┘
       │ bootstrap/fixtures       │ external/fpl_joe.py.fetch()   [抓取 TODO]
       │ (结构/team id/deadline)   │
       ▼                          ▼
 bootstrap.teams ──► mapping.py ◄── team_mapping_overrides (config/fpl_joe.json)
       │                          │
       │                    cache.py + freshness.py（写/读缓存，回退决策）
       │                          │
       ▼                          ▼
   context.py             signals.normalize_raw() ──► FixtureSignal
   (球员/阵容上下文)              │
       │                          ▼
       │                fixtures/difficulty.py ──► FixtureContext
       │                          │
       ▼                          ▼
            ┌──── 合并点：state.json ────┐
            │  decision 增加 fixture 摘要（只读展示） │
            └───────────────────────────┘
                         │
                         ▼
      history.json（notes/metrics 记录来源与新鲜度）
      web 前端（本轮建议下方展示赛程信号摘要，可选）
```

关键点：
1. **不合并原始数据**：FPL 官方和 FPL Joe 只在"内部结构"层面汇合——官方给 fixture 骨架与 team id，FPL Joe 只提供概率字段，两者唯一交叉点是 `mapping.py`。
2. **管线不阻塞**：FPL Joe 抓取失败/无缓存时，`resolve()` 返回 None，管线继续（决策仍用 Market Score），并在 history notes 记录"外部信号不可用"。
3. **Meta 必存**：每次写缓存/写 state 摘要都带 SourceMeta（E6）。

---

## 6. 决策层如何读取 fixture signal（未来接入点）

- 新增接口（仅接口，放 strategy.py）：

```python
class ExternalSignalProvider:
    """外部预测信号提供者（FPL Joe / 未来其他源）。

    返回源无关的 FixtureContext；数据不可用时返回 None（决策层降级，不报错）。
    TODO(Phase 2): 接入 fixtures 层实现；当前仅记录与展示，不参与决策公式。
    """

    def get_fixture_signals(self, gw):
        raise NotImplementedError("Phase 2 实现")
```

- **接入阶段（分步，保持 Market Score 核心不变）**：
  1. 第一阶段（本设计落地时）：`__main__.py` 在 context 构建后调用 external 源 → 写入 `state["fixture"]` 摘要 + history notes（来源/新鲜度/字段覆盖），**不参与任何决策公式**；前端可展示。
  2. 第二阶段：fixture signal 作为**次级排序键**（如转会候选同 Gap 时按对手难度微调、captain 并列时参考），由 `config/strategy.json` 的 `fixture_signal.enabled` 开关控制，默认关闭。
  3. 第三阶段（可选）：若验证有效，再讨论是否进入 Market Score 主公式（需重新设计权重分段，本设计不预设）。

- 现有模块（market.py / transfer.py / captain.py）**本轮零改动**，保证 E3。

---

## 7. 推荐新增配置项

`config/strategy.json`（决策参数，预留不激活）：

```jsonc
"fixture_signal": {
  "enabled": false,              // 默认关闭：只记录展示，不参与决策
  "source": "fpl_joe",           // 当前激活的外部源名（未来可多源）
  "weights": {                   // 未来接入决策公式时的权重（本阶段不使用）
    "difficulty": 0.3
  }
}
```

`config/fpl_joe.json`（源接入参数，非决策参数）：

```jsonc
{
  "enabled": true,
  "endpoint": "TBD",             // 待抓取调研确认 FPL Joe 数据端点
  "timeout_seconds": 30,
  "retry_times": 2,
  "cache_ttl_hours": 6,          // 小于此值视为 fresh
  "max_age_hours": 48,           // 超过此值视为 expired，不再回退使用
  "user_agent": "FPL-AI-Manager/0.3",
  "team_mapping_overrides": {    // 人工修正外部球队名（bootstrap 命中失败时兜底）
    "man city": 43,
    "man utd": 1
  }
}
```

---

## 8. 需要修改的现有文件

| 文件 | 修改内容 |
|------|----------|
| `docs/architecture.md` | 4.2 节补充外部源缓存例外条款；1.1 数据流图加外部源分支；第 3 节目录树加 external/、fixtures/、data/external/ |
| `docs/decision-engine.md` | 第 10 节数据来源表加 FPL Joe 行；新增"外部预测信号"章节（第 6 节接口） |
| `docs/roadmap.md` | Phase 2 增加"外部预测源（FPL Joe）接入：缓存/新鲜度/回退/映射" |
| `brain/strategy.py` | 增加 `ExternalSignalProvider` 接口（仅接口，无原始字段） |
| `brain/__main__.py` | fetch 阶段接入 external 源：`resolve()` 失败不阻塞；state 写入 fixture 摘要；history notes 记录来源与新鲜度 |
| `brain/context.py` | `build_state` 可选参数附加 fixture 摘要（缺省不影响现有调用与测试） |
| `config/strategy.json` | 增加 `fixture_signal` 预留段（默认关闭） |
| `README.md` | 架构一节补充外部信号说明 |

**明确不改**：`market.py`（Market Score 公式）、`transfer.py`、`captain.py`、`lineup.py`、`squad_builder.py`、`history_writer.py`、`web/`（前端只读展示，可选扩展）。

---

## 9. 推荐新增测试范围（tests/test_fpl_joe.py）

| # | 用例 | 验证点 |
|---|------|--------|
| 1 | 球队映射 | bootstrap teams → 动态 id 映射；大小写/空格规范化；overrides 修正生效；未知球队 → None + warning |
| 2 | 缓存读写 | 原子写；文件缺失 → None；JSON 损坏 → None（不抛）；无 meta 的缓存不可回退 |
| 3 | 新鲜度边界 | fetched_at 距今 < ttl → fresh；ttl~max_age → stale；> max_age → expired |
| 4 | 失败回退 | 抓取抛错 + 缓存未过期 → 返回 stale 缓存；无缓存 → None（管线不中断） |
| 5 | 信号标准化 | 字段缺失 → None；概率统一归一化；fields_present 正确 |
| 6 | 决策降级 | FixtureContext=None 时现有管线（market/lineup/captain/transfer）行为与无外部源完全一致（回归） |
| 7 | 难度计算 | 高 projected_goals_against / 低 cs_probability → 难度高；纯函数单调性 |
| 8 | 配置开关 | fixture_signal.enabled=false 时 state 摘要仍写入但决策输出与关闭前一致 |

---

## 10. 实施步骤（后续，不在本次范围）

1. 骨架：`external/` + `fixtures/` 空接口 + 配置 + 文档更新（本设计确认后）。
2. 抓取：调研 FPL Joe 数据端点与字段 → 实现 `fpl_joe.py.fetch()`。
3. 接线：`__main__.py` 注入 FixtureContext → state/history/前端展示（第一阶段）。
4. 决策接入（可选）：次级排序键 + 配置开关（第二阶段）。

## 11. 边界与风险

- **数据时效**：FPL Joe 概率是赛前快照，deadline 后抓取无意义；`cache_ttl_hours` 应明显小于两个 deadline 间隔。
- **端点未确认**：FPL Joe 是否有稳定公开 JSON 端点需调研；若只有网页，需解析 HTML（零依赖下更脆弱，应尽量找 JSON 端点）；本设计假设存在 JSON 端点。
- **映射漂移**：球员转会后队名不变，但 FPL team id 稳定；映射表以 bootstrap 动态构建为主，人工表只兜底，赛季内一般不会失效。
- **数据量**：外部缓存单文件覆盖式更新（~几十 KB），Git 仓库增量很小，不违反仓库轻量原则。
