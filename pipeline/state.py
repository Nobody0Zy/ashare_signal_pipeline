"""管道状态读写与幂等控制。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Sequence

import numpy as np

from .config import Paths


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def format_duration_hms(total_seconds: float) -> str:
    rounded_seconds = max(0, int(round(float(total_seconds))))
    hours, remainder = divmod(rounded_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def normalize_for_json(value):
    if isinstance(value, dict):
        return {str(key): normalize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_for_json(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    return value


def _legacy_root_state_file(paths: Paths) -> Path | None:
    legacy_path = paths.root / paths.state_file.name
    if legacy_path == paths.state_file:
        return None
    if legacy_path.exists():
        return legacy_path
    return None


def load_state(paths: Paths, mode: str) -> Dict[str, object]:
    state_path = paths.state_file
    legacy_path = _legacy_root_state_file(paths)
    if not state_path.exists() and legacy_path is not None:
        print(f"检测到旧版根目录状态文件，优先读取 {legacy_path.name} 并在后续写入版本目录。")
        state_path = legacy_path

    if state_path.exists():
        try:
            with state_path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except json.JSONDecodeError:
            state = {"mode": mode, "steps": {}}
    else:
        state = {"mode": mode, "steps": {}}
    state.setdefault("mode", mode)
    existing_version = state.get("experiment_version")
    if existing_version and existing_version != paths.experiment_version:
        raise RuntimeError(
            f"状态文件 {paths.state_file.name} 绑定版本 {existing_version}，"
            f"与当前实验版本 {paths.experiment_version} 不一致，请更换 --state-file 或清理旧状态。"
        )
    state.setdefault("experiment_version", paths.experiment_version)
    state.setdefault("steps", {})
    return state


def save_state(paths: Paths, state: Dict[str, object]) -> None:
    state["updated_at"] = now_iso()
    paths.state_file.parent.mkdir(parents=True, exist_ok=True)
    with paths.state_file.open("w", encoding="utf-8") as handle:
        json.dump(normalize_for_json(state), handle, ensure_ascii=False, indent=2)


def step_key(step: int) -> str:
    return f"step_{step}"


def update_step_state(
    paths: Paths,
    state: Dict[str, object],
    step: int,
    status: str,
    output_files: Sequence[str],
    checks_passed: bool,
    details: Dict[str, object],
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    duration_seconds: float | None = None,
    duration_hms: str | None = None,
) -> None:
    state.setdefault("steps", {})
    later_keys = [key for key in list(state["steps"].keys()) if key.startswith("step_")]
    for key in later_keys:
        try:
            existing_step = int(str(key).split("_")[1])
        except (IndexError, ValueError):
            continue
        if existing_step > step:
            state["steps"].pop(key, None)
    step_state = {
        "status": status,
        "output_files": list(output_files),
        "checks_passed": checks_passed,
        "timestamp": finished_at or now_iso(),
        "details": details,
    }
    if started_at is not None:
        step_state["started_at"] = started_at
    if duration_seconds is not None:
        step_state["duration_seconds"] = round(float(duration_seconds), 3)
    if duration_hms is not None:
        step_state["duration_hms"] = duration_hms
    state["steps"][step_key(step)] = step_state
    save_state(paths, state)


def require_previous_step(state: Dict[str, object], step: int) -> None:
    if step == 1:
        return
    previous = state.get("steps", {}).get(step_key(step - 1), {})
    if previous.get("status") != "completed" or previous.get("checks_passed") is not True:
        raise RuntimeError(
            f"前置步骤 {step - 1} 未完成或自检未通过，当前步骤 {step} 禁止启动。"
        )