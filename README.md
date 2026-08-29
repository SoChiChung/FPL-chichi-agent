# FPL AI Manager

由 AI 管理的 Fantasy Premier League（FPL）账号。当前为 **Phase 0 骨架**：自动拉取数据、生成状态与历史 JSON、GitHub Actions 定时更新、Vercel 静态展示。

完整系统设计见 [DESIGN.md](DESIGN.md)。

## 架构

```
GitHub Actions (定时) ──► brain/ (Python, 仅标准库) ──► data/*.json ──► Git push ──► Vercel 前端
        ▲                                                                              │
        └──────────────────────── 读取 data/*.json（同源） ◄──────────────────────────┘
```

原始数据（bootstrap/fixtures 等）只在内存中处理，**不落盘**，仓库只保留提炼后的 `state.json` / `history.json`。

## 目录结构

```
├── .github/workflows/update.yml   # 定时运行 + 自动提交
├── brain/                         # Python 引擎（零依赖）
│   ├── __main__.py                # 入口
│   ├── api.py                     # FPL API 客户端
│   ├── config.py                  # 全局配置（TEAM_ID 在这里）
│   ├── data_store.py              # JSON 原子读写 + 校验
│   ├── context.py                 # GW 上下文构建
│   └── strategy.py                # Phase 1+ 策略空接口（TODO）
├── config/settings.json           # 人工可编辑配置（Phase 1 起生效）
├── data/
│   ├── state.json                 # 当前状态（GW/积分/排名/阵容）
│   └── history.json               # 每轮历史（积分/排名）
├── web/                           # Vercel 静态前端
├── vercel.json
└── README.md
```

## 快速开始（本地）

要求 Python 3.10+（无需安装任何依赖）。

1. 在 `brain/config.py` 中修改 `TEAM_ID`（见下方说明）。
2. 在仓库根目录运行：

```bash
python -m brain
```

3. 查看生成的 `data/state.json` 与 `data/history.json`。
4. 本地预览前端：

```bash
python -m http.server 8000 --directory web
```

浏览器打开 `http://localhost:8000`（需先将 `data/*.json` 复制到 `web/data/`，Vercel 构建时会自动完成）。

## 如何找到 TEAM_ID

登录 FPL 官网，打开任意包含自己队伍内容的页面（如「My Team」），URL 形如：

```
https://fantasy.premierleague.com/entry/123456/event/1
```

`/entry/` 与 `/event/` 之间的数字（如 `123456`）就是 TEAM_ID。未来切换账号只需修改 `brain/config.py` 中的这一个值。

## GitHub Actions

推送仓库到 GitHub 后：

- 每天 03:23 / 15:23 / 21:23 UTC 自动运行，更新 `data/` 并自动提交；
- 也可在 Actions 页面手动触发（Workflow dispatch）。

> 注意：Actions 首次运行前请确认 `TEAM_ID` 已配置；未配置时运行会失败且不产生提交，这是预期行为。

## Vercel 部署

1. Vercel 控制台 → Import 本仓库（无需登录鉴权，公开仓库即可）。
2. Framework 选择 **Other**，其余保持默认（`vercel.json` 已配置构建命令）。
3. 部署完成后，`data/*.json` 会在构建时自动复制到静态目录，前端直接读取。

## 阶段规划

| 阶段 | 状态 | 内容 |
|------|------|------|
| Phase 0 | ✅ 当前 | 数据获取、state/history 生成、Actions、Vercel 展示 |
| Phase 1 | 预留 | AI 决策：阵容/队长/阵型/转会建议 + Ownership 策略（`brain/strategy.py` 接口已占位） |
| Phase 2 | 预留 | 前端增强（趋势图表）、通知、离线回测 |
| Phase 3 | 预留 | 自动登录与自动执行（含安全门与审计） |

## 常见问题

- **Actions 运行报 403/429**：FPL API 偶发限流，等待下一次定时运行或手动触发重试即可；必要时可在仓库 Settings → Secrets 中配置代理环境变量（本项目暂不依赖）。
- **数据长时间不更新**：检查 Actions 运行日志是否失败，以及 `data/` 是否有新的 commit。
