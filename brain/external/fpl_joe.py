"""FPL Joe 外部预测数据抓取（正式管线，仅数据层，不参与决策）。

数据源（已通过页面 JS 分析确认，非猜测）:
    1. GET /api/odds/projections?competition=premier-league&season=<season>&scope=season:<season>
       — 预测数据主接口；scope 合法值形式为 season:<season> 或 stage:<slug>。
       "goals"/"cleanSheet" 不是 scope 值，是响应中 bookmakers[].projection 的市场类型标签。
    2. GET /api/odds/status — 数据管线状态（非预测数据），仅作新鲜度辅助。

响应含两套数据（页面把两套合并展示，本模块同样处理）：
    - projectionsByPeriod（odds_market）：GW1-N 的每场比赛 lambdaHome/Away（期望进球）、
      pHomeWin/pDraw/pAwayWin（胜平负）、pHomeCs/pAwayCs（零封），主客分队；
      覆盖范围由 availablePeriods 决定（当前仅发布 4 轮）。
    - supplementalProjections（elevenify）：teams[].goals[]/cleanSheets[] 全赛季 38 GW
      球队整体序列 + fixtures[] 全赛季 380 场比赛骨架（主客/kickoff/难度）。
    两来源字段并列命名（_elevenify 后缀），不混用。

输出（config/fpl_joe.json output_dir）:
    clean_sheets.json / projected_goals.json / fixture_difficulty.json
    每个文件自带 metadata（请求/实际 GW 范围、数据源、新鲜度、警告）。
    抓取失败不覆盖上一份有效数据（原子写）。
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from brain import config as brain_config, data_store
from brain.external.freshness import FRESH, judge_freshness


class FplJoeError(Exception):
    """FPL Joe 抓取失败。"""


def _load_cfg() -> dict:
    try:
        with open(brain_config.FPL_JOE_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise FplJoeError(f"config/fpl_joe.json 读取失败: {exc}") from exc
    return cfg


# FPL Joe abbreviation → (FPL 官方 team_id, FPL 官方 name)
# 依据：FPL Joe 的 abbreviation 与 FPL 官方 bootstrap 的 short_name 一致（20 队已核对）。
FPL_TEAM_BY_ABBR = {
    "ARS": (1, "Arsenal"), "AVL": (2, "Aston Villa"), "BOU": (3, "Bournemouth"),
    "BRE": (4, "Brentford"), "BHA": (5, "Brighton"), "CHE": (6, "Chelsea"),
    "COV": (7, "Coventry City"), "CRY": (8, "Crystal Palace"), "EVE": (9, "Everton"),
    "FUL": (10, "Fulham"), "HUL": (11, "Hull City"), "IPS": (12, "Ipswich"),
    "LEE": (13, "Leeds"), "LIV": (14, "Liverpool"), "MCI": (15, "Man City"),
    "MUN": (16, "Man Utd"), "NEW": (17, "Newcastle"), "NFO": (18, "Nott'm Forest"),
    "TOT": (19, "Spurs"), "SUN": (20, "Sunderland"),
}


def _fetch_json(url: str, cfg: dict) -> dict:
    last_error = None
    for attempt in range(int(cfg.get("retry_times", 2)) + 1):
        if attempt:
            time.sleep(3)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": cfg.get("user_agent", "FPL-AI-Manager"),
                     "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=int(cfg.get("timeout_seconds", 30))) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
    raise FplJoeError(f"请求失败: {url} ({last_error})")


def _utc_now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def _map_fpl_team(team_name, abbr, warnings):
    if abbr and abbr.upper() in FPL_TEAM_BY_ABBR:
        return FPL_TEAM_BY_ABBR[abbr.upper()]
    if team_name:
        warnings.append(f"球队映射失败（保留原始名称）: {team_name} (abbr={abbr})")
    return None, None


def _elevenify_map(raw: dict) -> dict:
    result = {}
    for t in (raw.get("supplementalProjections") or {}).get("teams") or []:
        if t.get("abbreviation"):
            result[t["abbreviation"]] = t
    return result


def _market_map(raw: dict) -> dict:
    result = {}
    for period in (raw.get("projectionsByPeriod") or {}).values():
        for f in period.get("fixtures") or []:
            result[f.get("fixtureId")] = f
    return result


def _metadata(raw: dict, start_gw: int, end_gw: int, retrieved_at: str, warnings: list,
              ttl_hours: int = 6, max_age_hours: int = 48) -> dict:
    periods = sorted(int(k) for k in (raw.get("projectionsByPeriod") or {}) if str(k).isdigit())
    supp = raw.get("supplementalProjections") or {}
    elevenify_periods = sorted(int(p) for p in supp.get("periods") or [])
    return {
        "source": "fpljoe",
        "competition": raw.get("competition"),
        "season": raw.get("season"),
        "retrieved_at": retrieved_at,
        "source_updated_at": raw.get("latestSnapshotTs"),
        "requested_gameweek_start": start_gw,
        "requested_gameweek_end": end_gw,
        "actual_gameweek_min": min(periods) if periods else None,
        "actual_gameweek_max": max(periods) if periods else None,
        "missing_gameweeks": [gw for gw in range(start_gw, end_gw + 1) if gw not in periods],
        "freshness": judge_freshness(raw.get("latestSnapshotTs"), retrieved_at,
                                     ttl_hours=ttl_hours, max_age_hours=max_age_hours),
        "data_sources": {"odds_market": periods, "elevenify": elevenify_periods},
        "warnings": warnings,
    }


def normalize(raw: dict, start_gw: int, end_gw: int, retrieved_at: str,
              ttl_hours: int = 6, max_age_hours: int = 48) -> dict:
    """把 projections 响应标准化为三个独立数据块（比赛级 + 球队视角）。

    纯函数，不落盘；供 refresh() 与测试使用。
    """
    warnings = []
    market = _market_map(raw)
    elevenify = _elevenify_map(raw)
    skeleton = (raw.get("supplementalProjections") or {}).get("fixtures") or []

    cs_fixtures, goals_fixtures, diff_fixtures = [], [], []
    cs_teams, goals_teams, diff_teams = [], [], []

    for sf in skeleton:
        gw = sf.get("periodNumber")
        if not gw or not (start_gw <= int(gw) <= end_gw):
            continue
        mf = market.get(sf.get("fixtureId")) or {}
        home_code = sf.get("homeTeamCode")
        away_code = sf.get("awayTeamCode")
        home = sf.get("homeTeamName")
        away = sf.get("awayTeamName")
        g = int(gw) - 1  # Elevenify 序列按 GW 顺序（0 基）
        el_home = elevenify.get(home_code) or {}
        el_away = elevenify.get(away_code) or {}
        fpl_home_id, fpl_home_name = _map_fpl_team(home, home_code, warnings)
        fpl_away_id, fpl_away_name = _map_fpl_team(away, away_code, warnings)

        cs_fixtures.append({
            "gameweek": gw, "home_team": home, "away_team": away,
            "kickoff_time": sf.get("kickoffTsUtc"),
            "fpl_home_team_id": fpl_home_id, "fpl_home_team_name": fpl_home_name,
            "fpl_away_team_id": fpl_away_id, "fpl_away_team_name": fpl_away_name,
            "home_clean_sheet_probability": mf.get("pHomeCs"),
            "away_clean_sheet_probability": mf.get("pAwayCs"),
            "home_clean_sheet_elevenify": (el_home.get("cleanSheets") or [None] * 38)[g],
            "away_clean_sheet_elevenify": (el_away.get("cleanSheets") or [None] * 38)[g],
        })
        goals_fixtures.append({
            "gameweek": gw, "home_team": home, "away_team": away,
            "kickoff_time": sf.get("kickoffTsUtc"),
            "fpl_home_team_id": fpl_home_id, "fpl_home_team_name": fpl_home_name,
            "fpl_away_team_id": fpl_away_id, "fpl_away_team_name": fpl_away_name,
            "home_projected_goals": mf.get("lambdaHome"),
            "away_projected_goals": mf.get("lambdaAway"),
            "home_projected_goals_elevenify": (el_home.get("goals") or [None] * 38)[g],
            "away_projected_goals_elevenify": (el_away.get("goals") or [None] * 38)[g],
        })
        diff_fixtures.append({
            "gameweek": gw, "home_team": home, "away_team": away,
            "kickoff_time": sf.get("kickoffTsUtc"),
            "fpl_home_team_id": fpl_home_id, "fpl_home_team_name": fpl_home_name,
            "fpl_away_team_id": fpl_away_id, "fpl_away_team_name": fpl_away_name,
            "home_difficulty": sf.get("homeFixtureDifficulty"),
            "away_difficulty": sf.get("awayFixtureDifficulty"),
            "home_difficulty_sort_rating": sf.get("homeFixtureDifficultySortRating"),
            "away_difficulty_sort_rating": sf.get("awayFixtureDifficultySortRating"),
            "difficulty_source": "odds_market",
        })

        def team_row(team, opponent, venue, code, fpl_id, fpl_name):
            return {"team": team, "gameweek": gw, "opponent": opponent, "venue": venue,
                    "fpl_team_id": fpl_id, "fpl_team_name": fpl_name}

        h_row = team_row(home, away, "home", home_code, fpl_home_id, fpl_home_name)
        a_row = team_row(away, home, "away", away_code, fpl_away_id, fpl_away_name)
        for row, code in ((h_row, home_code), (a_row, away_code)):
            el = elevenify.get(code) or {}
            row["clean_sheet_probability"] = (el.get("cleanSheets") or [None] * 38)[g]
            row["source"] = "elevenify" if row["clean_sheet_probability"] is not None else None
        cs_teams.extend([h_row, a_row])

        h_row = team_row(home, away, "home", home_code, fpl_home_id, fpl_home_name)
        a_row = team_row(away, home, "away", away_code, fpl_away_id, fpl_away_name)
        for row, code in ((h_row, home_code), (a_row, away_code)):
            el = elevenify.get(code) or {}
            row["projected_goals"] = (el.get("goals") or [None] * 38)[g]
            row["source"] = "elevenify" if row["projected_goals"] is not None else None
        goals_teams.extend([h_row, a_row])

        h_row = team_row(home, away, "home", home_code, fpl_home_id, fpl_home_name)
        a_row = team_row(away, home, "away", away_code, fpl_away_id, fpl_away_name)
        for row, key in ((h_row, "homeFixtureDifficulty"), (a_row, "awayFixtureDifficulty")):
            row["difficulty"] = sf.get(key)
            row["difficulty_label"] = None
            row["source"] = "odds_market" if row["difficulty"] is not None else None
        diff_teams.extend([h_row, a_row])

    metadata = _metadata(raw, start_gw, end_gw, retrieved_at, warnings,
                         ttl_hours=ttl_hours, max_age_hours=max_age_hours)
    return {
        "clean_sheets.json": {"source": "fpljoe", "metric": "clean_sheet_probability",
                              "metadata": metadata, "fixtures": cs_fixtures, "teams": cs_teams},
        "projected_goals.json": {"source": "fpljoe", "metric": "team_projected_goals",
                                 "metadata": metadata, "fixtures": goals_fixtures,
                                 "teams": goals_teams},
        "fixture_difficulty.json": {"source": "fpljoe", "metric": "fixture_difficulty",
                                    "metadata": metadata, "fixtures": diff_fixtures,
                                    "teams": diff_teams},
    }


def refresh(season: str, start_gw: int, cfg: dict = None) -> list:
    """抓取 FPL Joe 并落盘三个 JSON；返回结构化 notes（写 history 用）。

    - 抓取失败：保留上一份有效数据，返回错误 note，不抛异常；
    - 每次运行都抓取（本地 npm start 与云端 Actions 都会触发）；
    - 输出文件：<output_dir>/clean_sheets.json、projected_goals.json、fixture_difficulty.json。
    """
    if cfg is None:
        cfg = _load_cfg()
    if not cfg.get("enabled", True):
        return [{"topic": "external_source", "detail": "FPL Joe 抓取已禁用（config/fpl_joe.json）"}]

    scope = f"season:{season}"
    url = (cfg["projections_url"] + "?" + urllib.parse.urlencode({
        "competition": cfg.get("competition", "premier-league"),
        "season": season,
        "scope": scope,
    }))
    try:
        raw = _fetch_json(url, cfg)
    except FplJoeError as exc:
        return [{"topic": "external_source",
                 "detail": f"FPL Joe 抓取失败，保留上一份有效数据: {exc}"}]

    try:
        _fetch_json(cfg["status_url"], cfg)
    except FplJoeError:
        pass  # 状态接口仅辅助，不影响主数据

    end_gw = start_gw + int(cfg.get("gameweeks_ahead", 5)) - 1
    retrieved_at = _utc_now_iso()
    data = normalize(raw, start_gw, end_gw, retrieved_at,
                     ttl_hours=int(cfg.get("freshness_ttl_hours", 6)),
                     max_age_hours=int(cfg.get("freshness_max_age_hours", 48)))
    output_dir = os.path.join(brain_config.DATA_DIR, cfg.get("output_dir", "external/fpljoe"))
    for name, payload in data.items():
        data_store.save_json(os.path.join(output_dir, name), payload)

    m = data["clean_sheets.json"]["metadata"]
    return [{
        "topic": "external_source",
        "detail": (f"FPL Joe 已刷新: 请求 GW{m['requested_gameweek_start']}-"
                   f"{m['requested_gameweek_end']} 实际 {m['actual_gameweek_min']}-"
                   f"{m['actual_gameweek_max']} 缺失 {m['missing_gameweeks']} "
                   f"新鲜度 {m['freshness']} 写入 {len(data)} 个文件"),
    }] + [{"topic": "external_source", "detail": w} for w in m["warnings"]]
