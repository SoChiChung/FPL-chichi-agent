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
    "weights": {
        "gw1_10": {"tsb": 0.8, "trend": 0.2},
        "gw11_20": {"tsb": 0.6, "trend": 0.4},
        "gw21_plus": {"tsb": 0.3, "trend": 0.7},
    },
}

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


def snapshot(cfg: dict, weights: dict) -> dict:
    """当前轮生效的策略快照（写入 history 供复盘）。"""
    return {
        "strategy": cfg.get("strategy"),
        "tsb_weight": weights["tsb"],
        "trend_weight": weights["trend"],
        "injury_threshold": cfg.get("injury_threshold"),
        "market_gap_threshold": cfg.get("market_gap_threshold"),
        "max_free_transfers": cfg.get("max_free_transfers"),
        "allow_hits": cfg.get("allow_hits"),
    }
