"""代码级常量：切换账号只需修改 TEAM_ID。

策略参数（权重/阈值/阵型列表等）统一在 config/strategy.json，
由 Phase 1 的 strategy_config.py 加载，禁止在此或任何代码中硬编码。
"""
import os

# ---- 账号 ----
# TODO: 填入你的 FPL 队伍 ID (entry id)。获取方式见 README「如何找到 TEAM_ID」。
TEAM_ID = 0
SEASON = "2026-27"

# ---- 运行 ----
DEBUG = True

# ---- 路径 (相对仓库根目录) ----
DATA_DIR = "data"
STATE_FILE = os.path.join(DATA_DIR, "state.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

# ---- FPL API ----
API_BASE = "https://fantasy.premierleague.com/api"
USER_AGENT = "FPL-AI-Manager/0.2 (Market Consensus skeleton)"
REQUEST_TIMEOUT = 30
RETRY_TIMES = 2
RETRY_DELAY = 3
