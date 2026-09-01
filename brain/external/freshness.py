"""外部数据新鲜度判定：fresh / stale / expired / unknown。

阈值来自 config/fpl_joe.json（freshness_ttl_hours / freshness_max_age_hours），
与 docs/fpl-joe.md 设计一致。
"""
import datetime

FRESH = "fresh"
STALE = "stale"
EXPIRED = "expired"
UNKNOWN = "unknown"


def judge_freshness(source_updated_at, retrieved_at, ttl_hours=6, max_age_hours=48) -> str:
    """按源数据更新时间与抓取时间差判定新鲜度。

    < ttl_hours → fresh；ttl ~ max_age → stale；> max_age → expired；
    时间无法解析 → unknown。
    """
    try:
        src = datetime.datetime.fromisoformat(str(source_updated_at).replace("Z", "+00:00"))
        ret = datetime.datetime.fromisoformat(str(retrieved_at).replace("Z", "+00:00"))
        hours = (ret - src).total_seconds() / 3600
    except (TypeError, ValueError):
        return UNKNOWN
    if hours < ttl_hours:
        return FRESH
    if hours <= max_age_hours:
        return STALE
    return EXPIRED
