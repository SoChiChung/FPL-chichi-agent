"""Phase 2 本轮价值评分 + 合法首发/替补选择（docs/decide.md §3/§4，v1.1）。

模块只读：输入 15 人 squad（含 market_score）、fpljoe 目标 GW 球队级数据、
官方全员 form、球员近 3 轮实际分与配置；不写任何文件、不改 squad 构成。

评分体系（全部 0-100、2 位小数、element_id 小者破并列，见 decide.md §3.0）：
  Projection / CleanSheet / Fixture：目标 GW 有赛程球队间的 League-wide min-max；
    球队无值（blank）记 0，整轮数据缺失记中立 50（降级判定在 fpljoe 读取层）；
  Form：官方 bootstrap form 全员 League-wide min-max（v1.1 方案 A，§3.1）；
  Streak：近 3 个 finished 轮按位置门槛（GKP/DEF ≥ 6、MID/FWD ≥ 5，§3.2）
    判回报位，查 streak_map 得 0-100，缺历史记 0；
  Attack Score = 0.60×Projection + 0.25×Form + 0.15×Streak（DEF/MID/FWD 用）；
  Attack Potential = 0.70×Projection + 0.20×Form + 0.10×Streak；
  Lineup Score：按位置权重表合成（§3.5）。

首发 = Bruteforce Lineup Search（§4）：C(15,11)=1365 全组合枚举，
合法性过滤 GK=1 / DEF≥3 / MID≥2 / FWD≥1，Total Lineup Score 取全局最大
（并列取 element_id 升序元组字典序最小，确定性）。
"""
import itertools

from brain.market import to_float

POS_ORDER = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
BREAKDOWN_KEYS = ("projection", "form", "streak", "clean_sheet", "fixture",
                  "attack", "attack_potential")
# 各位置实际参与评分公式的成分（其余键存 null = 未参与，schema 恒定 7 键）
PARTICIPATING = {
    "GKP": ("clean_sheet", "fixture"),
    "DEF": BREAKDOWN_KEYS,
    "MID": ("projection", "form", "streak", "fixture", "attack", "attack_potential"),
    "FWD": ("projection", "form", "streak", "fixture", "attack", "attack_potential"),
}


def _minmax(values):
    """League-wide min-max 的 (min, max)；空或全体同值返回 (None, None)。"""
    vals = [v for v in values if v is not None]
    if not vals:
        return None, None
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return None, None
    return lo, hi


def _norm(v, lo, hi):
    """归一化到 0-100 并保留 2 位小数；无有效区间时给中立 50。"""
    if lo is None:
        return 50.0
    return round((v - lo) / (hi - lo) * 100, 2)


def _weighted(parts: dict, weights: dict):
    """按权重表合成；结果 2 位小数。"""
    total = 0.0
    for key, weight in (weights or {}).items():
        total += to_float(parts.get(key)) * to_float(weight)
    return round(total, 2)


def _league_scores(raw_by_team: dict, negate=False) -> dict:
    """某原始指标的 League-wide 分数表 {abbr: 0-100}。

    negate=True（fixture）：先取负再归一 → 难度越低分数越高。
    无有效区间（空/全员同值）返回空表，由调用方统一处理为 0/中立。
    """
    raw = {abbr: (-v if negate else v) for abbr, v in raw_by_team.items() if v is not None}
    lo, hi = _minmax(list(raw.values()))
    if lo is None:
        return {}
    return {abbr: _norm(v, lo, hi) for abbr, v in raw.items()}


def streak_score(points, min_point: float, streak_map: dict) -> float:
    """近三轮回报位（最近在前）查表得 0-100。

    位串固定 3 位：历史不足 3 轮的缺失位按 0 处理（如仅 GW-1 得 5 分 → "100"）。
    """
    bits = "".join("1" if to_float(p) >= to_float(min_point) else "0"
                   for p in (points or [])[:3])
    bits = (bits + "000")[:3]
    return float(streak_map.get(bits, 0.0))


def score_squad(squad, team_data: dict, form_by_id: dict, recent_points: dict,
                cfg: dict):
    """给 15 人打本轮分并挂 score_breakdown / lineup_score。

    原地为 squad 球员行补字段，返回 (squad, notes)。
    team_data: fpl_joe.read_target_gw() 返回值（teams/neutral/notes）。
    form_by_id: {element_id: 官方 form 原始值}（覆盖全联盟，取自 bootstrap）。
    recent_points: {element_id: [P(GW-1), P(GW-2), P(GW-3)]}（近者在前，缺位 None）。
    cfg: strategy_config.get_lineup_engine() 结果。
    """
    notes = list(team_data.get("notes") or [])
    neutral = team_data.get("neutral") or {}
    teams = team_data.get("teams") or {}
    neutral_score = to_float(cfg.get("fallback_neutral_score"), 50.0)

    # 球队级三项原始值 → League-wide 分数表（fixture 先取负）
    raw = {"projection": {}, "clean_sheet": {}, "fixture": {}}
    for abbr, parts in teams.items():
        for key in raw:
            raw[key][abbr] = parts.get(key)
    league = {
        "projection": _league_scores(raw["projection"]),
        "clean_sheet": _league_scores(raw["clean_sheet"]),
        "fixture": _league_scores(raw["fixture"], negate=True),
    }

    # Form 全员池（官方口径），无数据/全员同值 → 各球员 50（3.1 GW1 场景）
    form_lo, form_hi = _minmax([to_float(v) for v in form_by_id.values()])

    streak_map = cfg.get("streak_map") or {}
    streak_min = cfg.get("streak_min_points") or {}
    attack_w = cfg.get("attack_weights") or {}
    ap_w = cfg.get("attack_potential_weights") or {}
    lineup_w = cfg.get("lineup_weights") or {}

    blank_note = set()
    for p in squad:
        abbr = p.get("team")
        pos = p.get("pos")
        parts = {}

        for key in ("projection", "clean_sheet", "fixture"):
            if neutral.get(key):
                parts[key] = neutral_score
                continue
            score_map = league[key]
            has_raw = (teams.get(abbr) or {}).get(key) is not None
            if has_raw:
                # 有原始值：正常归一到 0-100；全体同值退化为空表 → 中立 50
                parts[key] = score_map.get(abbr, neutral_score)
            else:
                parts[key] = 0.0
                if abbr not in blank_note and (raw[key] or score_map):
                    blank_note.add(abbr)
                    notes.append({"topic": "data_missing",
                                  "detail": f"{abbr} 目标 GW 无 {key} 数据（blank/缺行），记 0"})

        parts["form"] = _norm(to_float(form_by_id.get(p.get("id"))), form_lo, form_hi)
        min_p = to_float(streak_min.get(pos, 5))
        parts["streak"] = streak_score(
            (recent_points.get(p.get("id")) or [])[:3], min_p, streak_map)
        parts["market"] = to_float(p.get("market_score"))

        if pos in ("DEF", "MID", "FWD"):
            parts["attack"] = _weighted(parts, attack_w)
            parts["attack_potential"] = _weighted(parts, ap_w)

        participating = PARTICIPATING.get(pos, PARTICIPATING["GKP"])
        p["score_breakdown"] = {
            key: parts.get(key) if key in participating else None
            for key in BREAKDOWN_KEYS
        }
        p["lineup_score"] = _weighted(parts, lineup_w.get(pos) or {})
    return squad, notes


def select_starting_xi(squad, cfg: dict):
    """Bruteforce 全局最优首发（decide.md §4）。

    返回 (formation, xi, bench)：xi 按展示顺序（位置组内 Lineup Score 降序），
    bench 非门将在前（替补优先级降序）、门将恒最后；无合法解抛 ValueError。
    """
    exact = cfg.get("position_starters_exact") or {}
    minimums = cfg.get("position_min_starters") or {}
    if len(squad) != 15:
        raise ValueError(f"首发枚举需要 15 人 squad，实际 {len(squad)}")
    by_id = {p["id"]: p for p in squad}

    best_sum, best_ids = None, None
    for combo in itertools.combinations(list(by_id), 11):
        counts = {}
        for pid in combo:
            pos = by_id[pid]["pos"]
            counts[pos] = counts.get(pos, 0) + 1
        if any(counts.get(pos, 0) != num for pos, num in exact.items()):
            continue
        if any(counts.get(pos, 0) < num for pos, num in minimums.items()):
            continue
        total = sum(by_id[pid]["lineup_score"] for pid in combo)
        if best_sum is None or total > best_sum or (
                total == best_sum and combo < best_ids):
            best_sum, best_ids = total, combo

    if best_ids is None:
        raise ValueError("首发枚举无合法解：squad 位置配额异常")

    starter_set = set(best_ids)

    def display_key(p):
        return (POS_ORDER.get(p["pos"], 9), -p.get("lineup_score", 0.0), p["id"])

    def bench_key(p):
        # 替补顺序：非门将按 Lineup Score 降序在前，门将恒最后（FPL GK 只能替 GK）
        return (1 if p["pos"] == "GKP" else 0, -p.get("lineup_score", 0.0), p["id"])

    xi = sorted((p for p in squad if p["id"] in starter_set), key=display_key)
    bench = sorted((p for p in squad if p["id"] not in starter_set), key=bench_key)

    counts = {}
    for p in xi:
        counts[p["pos"]] = counts.get(p["pos"], 0) + 1
    formation = f"{counts.get('DEF', 0)}{counts.get('MID', 0)}{counts.get('FWD', 0)}"
    return formation, xi, bench
