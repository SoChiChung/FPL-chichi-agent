"""FPL 官方 API 客户端（仅标准库）。

所有请求失败都会抛出 FplApiError，由入口统一处理。
公开端点，无需登录；登录相关逻辑属于 Phase 3 的 Executor 模块。
"""
import json
import time
import urllib.error
import urllib.request

from brain import config


class FplApiError(Exception):
    """FPL API 请求失败。"""


def _fetch(path: str):
    """GET + JSON 解析，带超时与有限重试。"""
    url = config.API_BASE + path
    last_error = None
    for attempt in range(config.RETRY_TIMES + 1):
        if attempt:
            time.sleep(config.RETRY_DELAY)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
    raise FplApiError(f"请求失败: {url} ({last_error})")


def get_bootstrap() -> dict:
    """静态数据：球员/球队/event/位置类型（含持有率、价格、积分）。"""
    return _fetch("/bootstrap-static/")


def get_fixtures() -> list:
    """全赛季赛程。Phase 0 仅验证拉取连通性，处理完即丢弃，不落盘。"""
    return _fetch("/fixtures/")


def get_entry(team_id: int) -> dict:
    """队伍概况：总积分 / 总排名。"""
    return _fetch(f"/entry/{team_id}/")


def get_entry_history(team_id: int) -> dict:
    """每轮历史：积分 / 排名 / 银行；chips；过去赛季。"""
    return _fetch(f"/entry/{team_id}/history/")


def get_picks(team_id: int, gw: int) -> dict:
    """指定 GW 的首发 / 替补 / 队长 / 副队长。"""
    return _fetch(f"/entry/{team_id}/event/{gw}/picks/")
