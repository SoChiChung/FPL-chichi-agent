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
    """FPL API 请求失败（带 HTTP 状态码，用于区分“无数据”与“故障”）。"""

    def __init__(self, message: str, status: int = None):
        super().__init__(message)
        self.status = status


def _fetch(path: str):
    """GET + JSON 解析，带超时与有限重试。"""
    url = config.API_BASE + path
    last_error = None
    last_status = None
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
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # 资源不存在：立即失败，不重试（例如该 GW 没有阵容）
                raise FplApiError(f"请求失败: {url} (404)", status=404)
            last_error, last_status = exc, exc.code
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
    raise FplApiError(f"请求失败: {url} ({last_error})", status=last_status)


def get_bootstrap() -> dict:
    """静态数据：球员/球队/event/位置类型（含持有率、价格、积分）。"""
    return _fetch("/bootstrap-static/")


def get_element_summary(element_id: int) -> dict:
    """球员详情：history[]（逐轮得分/出场分钟）。供 Phase 2 Streak（仅 15 人）。"""
    return _fetch(f"/element-summary/{element_id}/")


def points_by_round(summary: dict) -> dict:
    """从 element-summary 响应提取 {round: points}（只留决策需要的字段）。

    round/points 缺失或非数值的行忽略；points 可能为负数（红牌扣分），照存。
    """
    result = {}
    for row in summary.get("history") or []:
        rnd = row.get("round")
        pts = row.get("points")
        try:
            result[int(rnd)] = int(pts)
        except (TypeError, ValueError):
            continue
    return result


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


def get_picks_checked(team_id: int, gw: int):
    """获取指定 GW 的 picks；该 GW 没有可用阵容时返回 None（不抛异常）。

    404 / 空响应 / 无 picks 字段 / picks 为空数组 → None（视为“该 GW 无阵容”）；
    其他错误（403/429/5xx 等）仍抛 FplApiError（视为 API 故障，与“无阵容”区分）。
    """
    try:
        data = _fetch(f"/entry/{team_id}/event/{gw}/picks/")
    except FplApiError as exc:
        if exc.status == 404:
            return None
        raise
    if not isinstance(data, dict) or not data.get("picks"):
        return None
    return data


def find_latest_picks(team_id: int, start_gw: int):
    """从 start_gw 向前搜索最近一个有 picks 的 GW。

    返回 (picks, gw)；完全找不到时返回 (None, None)。
    允许 GW1、GW2 等多个连续轮次都没有阵容（新账号 auto-pick 场景）。
    """
    for gw in range(start_gw, 0, -1):
        picks = get_picks_checked(team_id, gw)
        if picks is not None:
            return picks, gw
    return None, None
