"""Actions 空跑闸门：只读 data/state.json，按距下个 deadline 的距离决定是否该跑引擎。

用法（仓库根目录）: python -m brain.scheduler
只依赖标准库与 brain.config/data_store，不请求任何外部 API。

档位规则：
- 距 deadline > 24h            → 每天北京时间 09:00 更新一次
- 距 deadline 1h ~ 24h         → 每 1 小时更新一次
- 距 deadline ≤ 1h             → 每 10 分钟更新一次
- deadline 已过/无未来 deadline → 休赛期收敛：每 24h（或下一个北京 09:00）探测一次，
  失败恢复限流 30 分钟，不空转刷接口。

返回码恒为 0：skip 是正常状态不是失败；workflow_dispatch 手动触发 = 强制 due。
"""
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from brain import config, data_store

BEIJING_OFFSET = timedelta(hours=8)  # 北京 = UTC+8，全年无 DST
CADENCE_10MIN = timedelta(minutes=10)
CADENCE_HOUR = timedelta(hours=1)
RECOVERY_AGE = timedelta(minutes=30)  # 引擎连续失败后的重试限流
PROBE_AGE = timedelta(hours=24)  # 休赛期/季末探测频率上限


@dataclass
class Decision:
    due: bool
    reason: str
    age_minutes: int = None
    to_deadline_minutes: int = None
    next_due: str = None


def _parse(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _minutes(td):
    return int(td.total_seconds() // 60)


def _next_beijing_0900(after):
    """严格晚于 after 的下一个北京时间 09:00（返回 UTC aware datetime）。"""
    beijing = after + BEIJING_OFFSET
    anchor = beijing.replace(hour=9, minute=0, second=0, microsecond=0)
    if anchor <= beijing:
        anchor += timedelta(days=1)
    return anchor - BEIJING_OFFSET


def decide(state, now):
    """纯决策函数：state 为 dict（来自 state.json），now 为 aware UTC datetime。"""
    if not isinstance(state, dict) or not state:
        return Decision(True, "no_state")
    last = _parse(state.get("last_update"))
    if last is None:
        return Decision(True, "never_updated")
    age = now - last
    deadline = _parse(state.get("next_deadline"))

    if deadline is None:  # 休赛期/字段缺失：每天至多探测一次
        due_at = max(last + PROBE_AGE, _next_beijing_0900(last))
        return Decision(now >= due_at, "probe", age_minutes=_minutes(age),
                        next_due=due_at.isoformat(timespec="seconds"))

    ttl = deadline - now
    if ttl <= timedelta(0):
        if last >= deadline:
            # 引擎在 DDL 后成功过 => 真没有未来 DDL（季末），按探测档收敛
            due_at = max(last + PROBE_AGE, _next_beijing_0900(last))
            return Decision(now >= due_at, "probe_season_end", age_minutes=_minutes(age),
                            to_deadline_minutes=_minutes(ttl),
                            next_due=due_at.isoformat(timespec="seconds"))
        # 引擎还没在 DDL 后成功过 => 疑似失败，限流重试
        return Decision(age >= RECOVERY_AGE, "recovery", age_minutes=_minutes(age),
                        to_deadline_minutes=_minutes(ttl))

    if ttl <= CADENCE_HOUR:
        return Decision(age >= CADENCE_10MIN, "deadline_soon", age_minutes=_minutes(age),
                        to_deadline_minutes=_minutes(ttl))
    if ttl <= PROBE_AGE:
        return Decision(age >= CADENCE_HOUR, "hourly", age_minutes=_minutes(age),
                        to_deadline_minutes=_minutes(ttl))

    due_at = _next_beijing_0900(last)
    return Decision(now >= due_at, "daily", age_minutes=_minutes(age),
                    to_deadline_minutes=_minutes(ttl),
                    next_due=due_at.isoformat(timespec="seconds"))


def main():
    manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    decision = decide(data_store.load_json(config.STATE_FILE, None), datetime.now(timezone.utc))
    if manual:
        decision = Decision(True, "manual_dispatch")
    print(f"[gate] due={'yes' if decision.due else 'no'} reason={decision.reason} "
          f"age_min={decision.age_minutes} ddl_min={decision.to_deadline_minutes} "
          f"next_due={decision.next_due}")
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(f"due={'yes' if decision.due else 'no'}\n")


if __name__ == "__main__":
    main()
