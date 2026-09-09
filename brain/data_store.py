"""统一 JSON 存储层：原子写入 + 基础校验。

所有落盘都经过这里；不允许其他模块直接写文件。
"""
import json
import os
import tempfile

from brain import config


def load_json(path: str, default):
    """读取 JSON；文件不存在时返回 default。"""
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data) -> None:
    """原子写入：先写临时文件再替换，避免中途失败留下半截文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def validate_state(state: dict) -> None:
    required = {"manager_id", "season", "current_gw", "points", "rank", "team"}
    missing = required - set(state)
    if missing:
        raise ValueError(f"state.json 缺少字段: {sorted(missing)}")
    bank = state.get("bank")
    if bank is not None:
        if isinstance(bank, bool) or not isinstance(bank, (int, float)):
            raise ValueError(f"state.bank 类型异常: {bank!r}")
        if not (0 <= float(bank) <= 300):
            raise ValueError(
                f"state.bank 超合理范围: {bank}（应为 £m 单位浮点，如 2.0 = £2.0m；"
                f"出现 ≥300 多半是 FPL 0.1m 整数未 /10）")


def validate_history(history: dict) -> None:
    required = {"manager_id", "season", "history"}
    missing = required - set(history)
    if missing:
        raise ValueError(f"history.json 缺少字段: {sorted(missing)}")
