"""strategy.json 加载器：读取策略参数并按当前 GW 返回权重分段。

所有策略参数（权重/阈值/GW 分段/阵型列表）的唯一来源是 config/strategy.json；
文件缺失或字段缺失时回退到内置默认值（与设计文档一致），并打印 warning。
代码中禁止硬编码任何策略参数。
"""
import json
import os
import warnings

from brain import config

DEFAULT_STRATEGY = {
    "strategy": "market_consensus",
    "allow_hits": False,
    "injury_threshold": 75,
    "market_gap_threshold": 15,
    "max_free_transfers": 5,
    "budget": 100.0,
    "squad_size": 15,
    "max_players_per_team": 3,
    "position_quota": {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3},
    "formations": ["343", "352", "442", "433", "451", "541", "532"],
    "lineup_engine": {
        "streak_min_points": {"GKP": 6, "DEF": 6, "MID": 5, "FWD": 5},
        "streak_weights": [4, 2, 1],
        "streak_map": {
            "000": 0, "001": 14.29, "010": 28.57, "011": 42.86,
            "100": 57.14, "101": 71.43, "110": 85.71, "111": 100.0,
        },
        "form_source": "bootstrap_static",
        "attack_weights": {"projection": 0.60, "form": 0.25, "streak": 0.15},
        "attack_potential_weights": {"projection": 0.70, "form": 0.20, "streak": 0.10},
        "lineup_weights": {
            "GKP": {"clean_sheet": 0.50, "fixture": 0.30, "market": 0.20},
            "DEF": {"clean_sheet": 0.45, "fixture": 0.25, "attack": 0.10, "market": 0.20},
            "MID": {"attack": 0.40, "form": 0.20, "fixture": 0.20, "market": 0.20},
            "FWD": {"attack": 0.50, "form": 0.20, "fixture": 0.10, "market": 0.20},
        },
        "captain_weights": {
            "GKP": {"clean_sheet": 0.50, "market": 0.50},
            "DEF": {"attack_potential": 0.20, "clean_sheet": 0.50, "fixture": 0.30},
            "MID": {"attack_potential": 0.90, "market": 0.10},
            "FWD": {"attack_potential": 0.95, "market": 0.05},
        },
        "position_starters_exact": {"GKP": 1},
        "position_min_starters": {"DEF": 3, "MID": 2, "FWD": 1},
        "fallback_neutral_score": 50,
    },
    "weights": {
        "gw1_10": {"tsb": 0.8, "trend": 0.2},
        "gw11_20": {"tsb": 0.6, "trend": 0.4},
        "gw21_plus": {"tsb": 0.3, "trend": 0.7},
    },
}

LINEUP_POSITIONS = ("GKP", "DEF", "MID", "FWD")

# (权重段键, 命中条件)
WEIGHT_BANDS = [
    ("gw1_10", lambda gw: gw <= 10),
    ("gw11_20", lambda gw: 11 <= gw <= 20),
    ("gw21_plus", lambda gw: gw >= 21),
]


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load() -> dict:
    """加载 strategy.json；文件缺失或解析失败时回退默认值并 warning。"""
    if not os.path.isfile(config.STRATEGY_FILE):
        warnings.warn(f"strategy.json 不存在（{config.STRATEGY_FILE}），使用内置默认参数")
        return dict(DEFAULT_STRATEGY)
    try:
        with open(config.STRATEGY_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        warnings.warn(f"strategy.json 解析失败（{exc}），使用内置默认参数")
        return dict(DEFAULT_STRATEGY)
    if not isinstance(raw, dict):
        warnings.warn("strategy.json 顶层不是 JSON 对象，使用内置默认参数")
        return dict(DEFAULT_STRATEGY)
    return _deep_merge(DEFAULT_STRATEGY, raw)


def get_weights(cfg: dict, gw: int) -> dict:
    """按当前 GW 返回 {tsb, trend} 权重；tsb + trend ≈ 1（warning 不中断）。"""
    weights = cfg.get("weights") or {}
    band = None
    for key, match in WEIGHT_BANDS:
        if match(gw):
            band = weights.get(key) or DEFAULT_STRATEGY["weights"][key]
            break
    if band is None:
        band = DEFAULT_STRATEGY["weights"]["gw21_plus"]
    tsb = float(band.get("tsb", DEFAULT_STRATEGY["weights"]["gw1_10"]["tsb"]))
    trend = float(band.get("trend", DEFAULT_STRATEGY["weights"]["gw1_10"]["trend"]))
    if abs(tsb + trend - 1.0) > 1e-6:
        warnings.warn(f"GW{gw} 权重 tsb({tsb}) + trend({trend}) 之和不等于 1，请检查 strategy.json")
    return {"tsb": round(tsb, 4), "trend": round(trend, 4)}


def get_lineup_engine(cfg: dict) -> dict:
    """返回 lineup_engine 段（缺省时并入内置默认）并校验关键结构，只 warning 不中断。"""
    engine = _deep_merge(DEFAULT_STRATEGY["lineup_engine"], cfg.get("lineup_engine") or {})
    if not isinstance(engine.get("streak_min_points"), dict):
        warnings.warn("lineup_engine.streak_min_points 缺失，使用内置默认")
    else:
        missing = {p for p in LINEUP_POSITIONS if p not in engine["streak_min_points"]}
        if missing:
            warnings.warn(f"lineup_engine.streak_min_points 缺少位置 {sorted(missing)}，使用内置默认")
            engine["streak_min_points"] = dict(DEFAULT_STRATEGY["lineup_engine"]["streak_min_points"])
    for group in ("lineup_weights", "captain_weights"):
        weights = engine.get(group) or {}
        for pos, parts in weights.items():
            if not isinstance(parts, dict):
                continue
            if abs(sum(float(v) for v in parts.values()) - 1.0) > 1e-6:
                warnings.warn(f"lineup_engine.{group}.{pos} 权重之和不为 1，请检查 strategy.json")
    return engine


def snapshot(cfg: dict, weights: dict, lineup_cfg: dict = None) -> dict:
    """当前轮生效的策略快照（写入 history 供复盘）。

    lineup_cfg: Phase 2 传入 get_lineup_engine 的结果，附加 lineup/captain 权重快照。
    """
    snap = {
        "strategy": cfg.get("strategy"),
        "tsb_weight": weights["tsb"],
        "trend_weight": weights["trend"],
        "injury_threshold": cfg.get("injury_threshold"),
        "market_gap_threshold": cfg.get("market_gap_threshold"),
        "max_free_transfers": cfg.get("max_free_transfers"),
        "allow_hits": cfg.get("allow_hits"),
    }
    if lineup_cfg:
        snap["lineup_engine"] = {
            key: lineup_cfg.get(key)
            for key in ("streak_min_points", "streak_weights", "streak_map",
                        "form_source", "attack_weights", "attack_potential_weights",
                        "lineup_weights", "captain_weights",
                        "position_starters_exact", "position_min_starters",
                        "fallback_neutral_score")
        }
    return snap
