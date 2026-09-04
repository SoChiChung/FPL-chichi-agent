"""Actions 空跑闸门：只读 data/state.json，判断距上次更新是否已到更新节奏。

用法（仓库根目录）: python -m brain.scheduler
只依赖标准库与 brain.config/data_store，不请求任何外部 API。

节奏规则（统一 30 分钟）：
- 有 next_deadline（赛季中）→ 统一每 30 分钟更新一次，不再按 deadline 远近分档
- 无 next_deadline（休赛期）→ 每天至多探测一次（或下一个北京 09:00）

返回码恒为 0：skip 是正常状态不是失败；workflow_dispatch 手动触发 = 强制 due。
"""
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from brain import config, data_store

BEIJING_OFFSET = timedelta(hours=8)  # 北京 = UTC+8，全年无 DST
CADENCE = timedelta(minutes=30)  # 赛季中统一更新节奏：每 30 分钟
PROBE_AGE = timedelta(hours=24)  # 休赛期探测频率上限


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

    # 赛季中（有 deadline）：统一每 30 分钟更新一次，不再按 deadline 远近分档
    return Decision(age >= CADENCE, "every_30min", age_minutes=_minutes(age),
                    to_deadline_minutes=_minutes(deadline - now))


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
