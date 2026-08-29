"""全局配置：所有可配置项统一放在这里。

切换账号只需修改 TEAM_ID。Phase 1 起的策略参数也追加在此模块，
并通过 SETTINGS 合并 config/settings.json 中的可编辑项。
"""
import json
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
USER_AGENT = "FPL-AI-Manager/0.1 (Phase 0 skeleton)"
REQUEST_TIMEOUT = 30
RETRY_TIMES = 2
RETRY_DELAY = 3

# ---- 人工可编辑配置 (config/settings.json, 可缺省) ----
SETTINGS_FILE = os.path.join("config", "settings.json")
SETTINGS = {}
if os.path.isfile(SETTINGS_FILE):
    with open(SETTINGS_FILE, encoding="utf-8") as f:
        SETTINGS = json.load(f)
