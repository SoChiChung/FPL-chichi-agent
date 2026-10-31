"""Actions 空跑闸门：只读 data/state.json，判断距上次更新是否已到更新节奏。

用法（仓库根目录）: python -m brain.scheduler
只依赖标准库与 brain.config/data_store，不请求任何外部 API。

节奏规则：
- GitHub Actions 仅在北京时间每天 17:00 至次日 04:00 每小时触发
- 有无 next_deadline 都按每小时节奏计算

返回码恒为 0：skip 是正常状态不是失败；workflow_dispatch 手动触发 = 强制 due。
"""
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from brain import config, data_store

CADENCE = timedelta(hours=1)  # 两次计算的最短间隔：一小时


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


def decide(state, now):
    """纯决策函数：state 为 dict（来自 state.json），now 为 aware UTC datetime。"""
    if not isinstance(state, dict) or not state:
        return Decision(True, "no_state")
    last = _parse(state.get("last_update"))
    if last is None:
        return Decision(True, "never_updated")
    age = now - last
    deadline = _parse(state.get("next_deadline"))
    # 更新窗口由 workflow cron 限定；这里保证每次计算至少间隔一小时。
    return Decision(age >= CADENCE, "every_hour", age_minutes=_minutes(age),
                    to_deadline_minutes=(
                        _minutes(deadline - now) if deadline is not None else None))


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
