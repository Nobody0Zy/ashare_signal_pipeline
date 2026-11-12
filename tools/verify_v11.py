"""v1.1 里程碑验收脚本（M0 先行）。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import Any

import pandas as pd  # noqa: F401  # 允许依赖范围：pandas/pyarrow

REL_TOL_PROBE = 0.01
ABS_TOL_FLOAT = 1e-12
MILESTONES = ("M0", "M1", "M2", "M3", "M4", "M5")


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _relative_close(actual: float, expected: float, tol: float = REL_TOL_PROBE) -> bool:
    base = max(1.0, abs(float(expected)))
    return abs(float(actual) - float(expected)) / base <= tol


def _float_close(actual: float, expected: float, tol: float = ABS_TOL_FLOAT) -> bool:
    return abs(float(actual) - float(expected)) <= tol * max(1.0, abs(float(expected)))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_subprocess(command: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _add_item(
    items: list[dict[str, Any]],
    item_id: str,
    name: str,
    expected: Any,
    actual: Any,
    passed: bool,
    detail: str = "",
) -> None:
    items.append(
        {
            "id": item_id,
            "name": name,
            "expected": expected,
            "actual": actual,
            "pass": bool(passed),
            "detail": detail,
        }
    )


def _append_exception_item(
    items: list[dict[str, Any]],
    item_id: str,
    name: str,
    expected: Any,
    exc: Exception,
) -> None:
    _add_item(
        items,
        item_id,
        name,
        expected,
        {"error": f"{type(exc).__name__}: {exc}"},
        False,
        traceback.format_exc(),
    )


def _baseline_config_path(baseline: dict[str, Any]) -> str:
    value = baseline.get("config", "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("baseline.config 为空，无法定位本地配置")
    return value.strip()


def _resolve_raw_paths_from_config(config_path: str) -> dict[str, str]:
    probe_code = (
        "import json,sys;"
        "from pathlib import Path;"
        "from pipeline.config import load_params, resolve_raw_data_paths;"
        "root=Path.cwd();"
        "params=load_params(sys.argv[1]);"
        "resolved=resolve_raw_data_paths(root, params);"
        "print(json.dumps({'holding':str(resolved['holding']), 'datebar':str(resolved['datebar'])}, ensure_ascii=False))"
    )
    rc, stdout, stderr = _run_subprocess([sys.executable, "-c", probe_code, config_path])
    if rc != 0:
        raise RuntimeError(f"加载配置失败: {config_path}; stderr={stderr or '<empty>'}")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"配置探测输出不是 JSON: {stdout}") from exc
    holding = str(payload.get("holding", "")).strip()
    datebar = str(payload.get("datebar", "")).strip()
    if not holding or not datebar:
        raise RuntimeError(f"配置探测缺少路径字段: {payload}")
    return {"holding": holding, "datebar": datebar}


def _parse_mode_from_config(config_path: str) -> str:
    code = (
        "import json,sys;"
        "from pipeline.config import load_params;"
        "params=load_params(sys.argv[1]);"
        "inp=getattr(params, 'input', None);"
        "mode=getattr(inp, 'mode', 'holding_file');"
        "print(json.dumps({'mode': mode}))"
    )
    rc, stdout, stderr = _run_subprocess([sys.executable, "-c", code, config_path])
    if rc != 0:
        raise RuntimeError(f"load_params 失败: {config_path}; stderr={stderr or '<empty>'}")
    payload = json.loads(stdout)
    mode = str(payload.get("mode", "")).strip()
    if not mode:
        raise RuntimeError(f"配置模式为空: {config_path}")
    return mode


def _build_experiment_version(config_path: str) -> str:
    code = (
        "import sys;"
        "from pipeline.config import load_params;"
        "print(load_params(sys.argv[1]).build_experiment_version())"
    )
    rc, stdout, stderr = _run_subprocess([sys.executable, "-c", code, config_path])
    if rc != 0:
        raise RuntimeError(f"build_experiment_version 失败: {config_path}; stderr={stderr or '<empty>'}")
    value = stdout.strip()
    if not value:
        raise RuntimeError(f"build_experiment_version 输出为空: {config_path}")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _deep_equal(expected: Any, actual: Any, path: str = "") -> tuple[bool, list[str]]:
    diffs: list[str] = []

    def _walk(exp: Any, act: Any, cursor: str) -> None:
        location = cursor or "<root>"
        if isinstance(exp, dict):
            if not isinstance(act, dict):
                diffs.append(f"{location}: type mismatch expected=dict actual={type(act).__name__}")
                return
            exp_keys = set(exp.keys())
            act_keys = set(act.keys())
            if exp_keys != act_keys:
                missing = sorted(exp_keys - act_keys)
                extra = sorted(act_keys - exp_keys)
                if missing:
                    diffs.append(f"{location}: missing keys {missing}")
                if extra:
                    diffs.append(f"{location}: extra keys {extra}")
            for key in sorted(exp_keys & act_keys):
                next_path = f"{location}.{key}" if location != "<root>" else str(key)
                _walk(exp[key], act[key], next_path)
            return
        if isinstance(exp, list):
            if not isinstance(act, list):
                diffs.append(f"{location}: type mismatch expected=list actual={type(act).__name__}")
                return
            if len(exp) != len(act):
                diffs.append(f"{location}: length mismatch expected={len(exp)} actual={len(act)}")
                return
            for idx, (exp_item, act_item) in enumerate(zip(exp, act)):
                _walk(exp_item, act_item, f"{location}[{idx}]")
            return
        if _is_number(exp) and _is_number(act):
            if isinstance(exp, int) and isinstance(act, int):
                if exp != act:
                    diffs.append(f"{location}: value mismatch expected={exp} actual={act}")
            elif not _float_close(float(act), float(exp)):
                diffs.append(f"{location}: float mismatch expected={exp} actual={act}")
            return
        if exp != act:
            diffs.append(f"{location}: value mismatch expected={exp!r} actual={act!r}")

    _walk(expected, actual, path)
    return len(diffs) == 0, diffs


def _resolve_state_path(state_file: str, experiment_version: str) -> Path:
    state_path = Path(state_file)
    if state_path.is_absolute():
        return state_path
    if state_path.parent == Path("."):
        return Path.cwd() / "outputs" / experiment_version / "metadata" / state_path.name
    return Path.cwd() / state_path


def _run_m0_checks(args: argparse.Namespace, baseline: dict[str, Any], items: list[dict[str, Any]]) -> None:
    # A0-01
    try:
        required = ["git_tag", "git_commit", "run_date", "label", "base_version", "version_subdir"]
        actual = {key: baseline.get(key) for key in required}
        missing = [key for key, value in actual.items() if _is_empty(value)]
        _add_item(items, "A0-01", "baseline_identity_keys_non_empty", required, actual, len(missing) == 0, detail="; ".join(missing))
    except Exception as exc:
        _append_exception_item(items, "A0-01", "baseline_identity_keys_non_empty", ["git_tag", "git_commit", "run_date", "label", "base_version", "version_subdir"], exc)

    # A0-02
    try:
        required = ["window", "time_split", "feature_selection", "manual_features", "tsfresh", "lgb", "seed", "smoke_size"]
        actual = {key: baseline.get(key) for key in required}
        missing = [key for key, value in actual.items() if _is_empty(value)]
        _add_item(items, "A0-02", "baseline_param_snapshots_present", required, actual, len(missing) == 0, detail="; ".join(missing))
    except Exception as exc:
        _append_exception_item(items, "A0-02", "baseline_param_snapshots_present", ["window", "time_split", "feature_selection", "manual_features", "tsfresh", "lgb", "seed", "smoke_size"], exc)

    # A0-03
    try:
        raw = baseline.get("raw", {})
        expected_fields = ["sha256", "rows", "min", "max"]
        actual = {
            "holding": {k: raw.get("holding", {}).get(k) if isinstance(raw.get("holding"), dict) else None for k in expected_fields},
            "datebar": {k: raw.get("datebar", {}).get(k) if isinstance(raw.get("datebar"), dict) else None for k in expected_fields},
        }
        missing = [
            f"{part}.{field}"
            for part in ("holding", "datebar")
            for field in expected_fields
            if _is_empty(actual[part][field])
        ]
        _add_item(items, "A0-03", "baseline_raw_identity_fields_present", {"holding": expected_fields, "datebar": expected_fields}, actual, len(missing) == 0, detail="; ".join(missing))
    except Exception as exc:
        _append_exception_item(items, "A0-03", "baseline_raw_identity_fields_present", {"holding": ["sha256", "rows", "min", "max"], "datebar": ["sha256", "rows", "min", "max"]}, exc)

    # A0-04
    try:
        expected_keys = ["count", "mean", "std", "min", "25%", "50%", "75%", "max"]
        target_stats = baseline.get("step1", {}).get("target_stats", {})
        actual_keys = sorted(target_stats.keys()) if isinstance(target_stats, dict) else []
        missing = [key for key in expected_keys if key not in actual_keys or _is_empty(target_stats.get(key))]
        passed = set(actual_keys) == set(expected_keys) and not missing
        _add_item(items, "A0-04", "step1_target_stats_complete", expected_keys, target_stats, passed, detail="; ".join(missing))
    except Exception as exc:
        _append_exception_item(items, "A0-04", "step1_target_stats_complete", ["count", "mean", "std", "min", "25%", "50%", "75%", "max"], exc)

    # A0-05
    try:
        coverage = baseline.get("labels", {}).get("target_holding_return", {}).get("coverage")
        passed = coverage is not None and _float_close(float(coverage), min(max(float(coverage), 0.0), 1.0))
        _add_item(items, "A0-05", "holding_return_coverage_between_0_1", {"min": 0.0, "max": 1.0}, coverage, passed)
    except Exception as exc:
        _append_exception_item(items, "A0-05", "holding_return_coverage_between_0_1", {"min": 0.0, "max": 1.0}, exc)

    # A0-06
    try:
        baseline_config = _baseline_config_path(baseline)
        raw_paths = _resolve_raw_paths_from_config(baseline_config)
        holding_path = Path(raw_paths["holding"])
        datebar_path = Path(raw_paths["datebar"])
        if not holding_path.exists() or not datebar_path.exists():
            missing = []
            if not holding_path.exists():
                missing.append(str(holding_path))
            if not datebar_path.exists():
                missing.append(str(datebar_path))
            raise FileNotFoundError(f"缺失 raw 文件: {missing}")
        actual = {
            "holding": _sha256_file(holding_path),
            "datebar": _sha256_file(datebar_path),
        }
        expected = {
            "holding": baseline.get("raw", {}).get("holding", {}).get("sha256"),
            "datebar": baseline.get("raw", {}).get("datebar", {}).get("sha256"),
        }
        passed = actual == expected
        _add_item(items, "A0-06", "raw_sha256_matches_baseline", expected, actual, passed)
    except Exception as exc:
        _append_exception_item(items, "A0-06", "raw_sha256_matches_baseline", {"holding": "<sha256>", "datebar": "<sha256>"}, exc)

    # A0-07
    try:
        value = str(baseline.get("legacy_holding_samples_set_hash", ""))
        passed = re.fullmatch(r"[0-9a-f]{16}", value) is not None
        _add_item(items, "A0-07", "legacy_samples_set_hash_format", r"^[0-9a-f]{16}$", value, passed)
    except Exception as exc:
        _append_exception_item(items, "A0-07", "legacy_samples_set_hash_format", r"^[0-9a-f]{16}$", exc)

    # A0-08 A0-09 A0-10
    probe_path = Path("version_design/design/refactor/datebar_probe.json")
    try:
        probe = _read_json(probe_path)
        baseline_datebar = baseline.get("raw", {}).get("datebar", {})
        expected_rows = baseline_datebar.get("rows")
        actual_rows = probe.get("rows")
        passed_rows = expected_rows is not None and actual_rows is not None and _relative_close(float(actual_rows), float(expected_rows))
        _add_item(items, "A0-08", "probe_rows_close_to_baseline", expected_rows, actual_rows, passed_rows)

        expected_symbols = baseline_datebar.get("n_symbols")
        actual_symbols = probe.get("n_symbols")
        passed_symbols = expected_symbols is not None and actual_symbols is not None and _relative_close(float(actual_symbols), float(expected_symbols))
        _add_item(items, "A0-09", "probe_n_symbols_close_to_baseline", expected_symbols, actual_symbols, passed_symbols)

        expected_range = {"min_idxd": baseline_datebar.get("min"), "max_idxd": baseline_datebar.get("max")}
        actual_range = {"min_idxd": probe.get("min_idxd"), "max_idxd": probe.get("max_idxd")}
        passed_range = expected_range == actual_range
        _add_item(items, "A0-10", "probe_idxd_range_matches_baseline", expected_range, actual_range, passed_range)
    except Exception as exc:
        _append_exception_item(items, "A0-08", "probe_rows_close_to_baseline", "version_design/design/refactor/datebar_probe.json", exc)
        _append_exception_item(items, "A0-09", "probe_n_symbols_close_to_baseline", "version_design/design/refactor/datebar_probe.json", exc)
        _append_exception_item(items, "A0-10", "probe_idxd_range_matches_baseline", "version_design/design/refactor/datebar_probe.json", exc)

    # A0-11
    try:
        mode = _parse_mode_from_config(args.config)
        _add_item(items, "A0-11", "config_loadable_with_holding_mode", "holding_file", mode, mode == "holding_file")
    except Exception as exc:
        _append_exception_item(items, "A0-11", "config_loadable_with_holding_mode", args.config, exc)

    # A0-12
    docs_path = Path("docs/14_datebar_probe.md")
    try:
        text = docs_path.read_text(encoding="utf-8")
        expected = ["### C1", "### C2", "### C3", "### C4"]
        counts = {heading: len(re.findall(rf"^{re.escape(heading)}\b", text, flags=re.MULTILINE)) for heading in expected}
        passed = all(counts[h] >= 1 for h in expected)
        _add_item(items, "A0-12", "datebar_probe_doc_has_c1_c4", {h: ">=1" for h in expected}, counts, passed)
    except Exception as exc:
        _append_exception_item(items, "A0-12", "datebar_probe_doc_has_c1_c4", "docs/14_datebar_probe.md", exc)

    # A0-13
    try:
        smoke = baseline.get("smoke", {})
        steps = smoke.get("steps", {}) if isinstance(smoke, dict) else {}
        required_step_keys = {"status", "checks_passed", "duration_hms", "checks"}
        parsed_numeric_block = True
        numeric_block: dict[str, Any] = {}
        for key, value in smoke.items():
            if key in {"n", "steps"}:
                continue
            if isinstance(value, (int, float)):
                numeric_block[key] = value
            elif isinstance(value, dict) and all(isinstance(v, (int, float)) for v in value.values()):
                numeric_block[key] = value
            else:
                parsed_numeric_block = False
        step_structure_ok = isinstance(steps, dict) and len(steps) > 0 and all(
            isinstance(step_value, dict) and required_step_keys.issubset(step_value.keys()) and isinstance(step_value.get("checks"), dict)
            for step_value in steps.values()
        )
        passed = parsed_numeric_block and step_structure_ok
        actual = {"numeric_block_keys": sorted(numeric_block.keys()), "step_keys": sorted(steps.keys()) if isinstance(steps, dict) else []}
        expected = {"smoke_numeric_fields_parseable": True, "steps_like_state_structure": True}
        _add_item(items, "A0-13", "baseline_smoke_block_parseable", expected, actual, passed)
    except Exception as exc:
        _append_exception_item(items, "A0-13", "baseline_smoke_block_parseable", "smoke.*", exc)

    # A0-14
    try:
        names = baseline.get("labels", {}).get("builtin_names")
        if not isinstance(names, list):
            raise TypeError("labels.builtin_names 不是数组")
        has_future = any(re.fullmatch(r"target_future_\d+d", str(name)) is not None for name in names)
        has_holding = any(re.fullmatch(r"target_holding_return", str(name)) is not None for name in names)
        all_non_empty = all(isinstance(name, str) and name.strip() for name in names)
        passed = len(names) > 0 and has_future and has_holding and all_non_empty
        expected = {"non_empty_array": True, "future_pattern_hits": ">=1", "holding_pattern_hits": ">=1"}
        actual = {
            "count": len(names),
            "future_hits": sum(1 for name in names if re.fullmatch(r"target_future_\d+d", str(name)) is not None),
            "holding_hits": sum(1 for name in names if re.fullmatch(r"target_holding_return", str(name)) is not None),
        }
        _add_item(items, "A0-14", "builtin_labels_expectations", expected, actual, passed)
    except Exception as exc:
        _append_exception_item(items, "A0-14", "builtin_labels_expectations", "baseline.labels.builtin_names", exc)

    # A0-15
    try:
        if not args.baseline_git_ref:
            raise ValueError("M0 需要 --baseline-git-ref")
        baseline_path = Path(args.baseline)
        if baseline_path.is_absolute():
            baseline_repo_path = baseline_path.resolve().relative_to(Path.cwd().resolve()).as_posix()
        else:
            baseline_repo_path = baseline_path.as_posix().lstrip("./")
        rc, stdout, stderr = _run_subprocess(
            ["git", "--no-pager", "show", f"{args.baseline_git_ref}:{baseline_repo_path}"]
        )
        if rc != 0:
            raise RuntimeError(f"git show 失败: {stderr or '<empty>'}")
        old_baseline = json.loads(stdout)
        if not isinstance(old_baseline, dict):
            raise TypeError("旧 baseline 不是对象")
        old_keys = sorted(old_baseline.keys())
        missing_old_keys = [key for key in old_keys if key not in baseline]
        mismatches: list[str] = []
        for key in old_keys:
            if key not in baseline:
                continue
            _, diffs = _deep_equal(old_baseline[key], baseline[key], key)
            mismatches.extend(diffs)
        passed = len(missing_old_keys) == 0 and len(mismatches) == 0
        expected = {
            "baseline_git_ref": args.baseline_git_ref,
            "protected_old_keys": old_keys,
        }
        actual = {
            "missing_old_keys": missing_old_keys,
            "mismatches": mismatches,
            "added_new_keys": sorted(set(baseline.keys()) - set(old_keys)),
        }
        _add_item(items, "A0-15", "baseline_legacy_keys_anti_tamper", expected, actual, passed)
    except Exception as exc:
        _append_exception_item(items, "A0-15", "baseline_legacy_keys_anti_tamper", {"baseline_git_ref": args.baseline_git_ref}, exc)

    # A0-16
    try:
        local_config = _baseline_config_path(baseline)
        repo_version = _build_experiment_version(args.config)
        local_version = _build_experiment_version(local_config)
        expected = {"repo_config": args.config, "experiment_version": repo_version}
        actual = {"local_config": local_config, "experiment_version": local_version}
        _add_item(items, "A0-16", "repo_vs_local_experiment_version_equal", expected, actual, repo_version == local_version)
    except Exception as exc:
        _append_exception_item(items, "A0-16", "repo_vs_local_experiment_version_equal", {"repo_config": args.config}, exc)

    # A0-17
    try:
        expected_stats_keys = ["count", "mean", "std", "min", "25%", "50%", "75%", "max"]
        baseline_stats = baseline.get("step1", {}).get("target_stats", {})
        smoke_sample_count = baseline.get("smoke", {}).get("step1_sample_count")
        raw_datebar_rows = baseline.get("raw", {}).get("datebar", {}).get("rows")
        smoke_datebar_rows = baseline.get("smoke", {}).get("step1_datebar_rows")

        subchecks: dict[str, dict[str, Any]] = {}

        pass_01 = baseline_stats is not None and smoke_sample_count is not None and _float_close(
            float(baseline_stats.get("count")),
            float(smoke_sample_count),
        )
        subchecks["count_equals_smoke_sample_count"] = {
            "pass": pass_01,
            "expected": smoke_sample_count,
            "actual": baseline_stats.get("count") if isinstance(baseline_stats, dict) else None,
        }

        pass_02 = raw_datebar_rows is not None and smoke_datebar_rows is not None and int(raw_datebar_rows) == int(smoke_datebar_rows)
        subchecks["raw_datebar_rows_equals_smoke_step1_datebar_rows"] = {
            "pass": pass_02,
            "expected": smoke_datebar_rows,
            "actual": raw_datebar_rows,
        }

        baseline_config = _baseline_config_path(baseline)
        built_version = _build_experiment_version(baseline_config)
        baseline_version = baseline.get("experiment_version")
        pass_03 = isinstance(baseline_version, str) and baseline_version == built_version
        subchecks["baseline_version_equals_built_from_baseline_config"] = {
            "pass": pass_03,
            "expected": baseline_version,
            "actual": built_version,
        }

        sub4_detail = ""
        pass_04 = False
        sub4_actual: dict[str, Any] = {"state_file": None}
        sub4_expected: dict[str, Any] = {
            "target_stats_keys": expected_stats_keys,
            "sample_count": smoke_sample_count,
        }
        if not args.state_file:
            sub4_detail = "M0 未提供 --state-file"
        else:
            experiment_version = str(baseline.get("experiment_version", "")).strip()
            resolved_states = [_resolve_state_path(state_file, experiment_version) for state_file in args.state_file]
            existing = [path for path in resolved_states if path.exists()]
            if not existing:
                sub4_detail = f"--state-file 均不存在: {[str(path) for path in resolved_states]}"
            else:
                state_path = existing[0]
                state_json = _read_json(state_path)
                state_stats = (
                    state_json.get("steps", {})
                    .get("step_1", {})
                    .get("details", {})
                    .get("target_stats", {})
                )
                state_sample_count = (
                    state_json.get("steps", {})
                    .get("step_1", {})
                    .get("details", {})
                    .get("sample_count")
                )
                stats_key_match = isinstance(state_stats, dict) and set(state_stats.keys()) == set(expected_stats_keys)
                stats_equal = False
                if stats_key_match and isinstance(baseline_stats, dict) and set(baseline_stats.keys()) == set(expected_stats_keys):
                    stats_equal, _ = _deep_equal(baseline_stats, state_stats, "step1.target_stats")
                sample_count_equal = (
                    state_sample_count is not None
                    and smoke_sample_count is not None
                    and int(state_sample_count) == int(smoke_sample_count)
                )
                pass_04 = stats_key_match and stats_equal and sample_count_equal
                sub4_actual = {
                    "state_file": str(state_path),
                    "state_target_stats_keys": sorted(state_stats.keys()) if isinstance(state_stats, dict) else None,
                    "state_sample_count": state_sample_count,
                    "stats_equal": stats_equal,
                    "sample_count_equal": sample_count_equal,
                }
                if not pass_04:
                    sub4_detail = "state.step_1.details 与 baseline.step1/ smoke 交叉一致性失败"
        subchecks["baseline_step1_vs_state_step1_consistency"] = {
            "pass": pass_04,
            "expected": sub4_expected,
            "actual": sub4_actual,
            "detail": sub4_detail,
        }

        passed = all(entry.get("pass") for entry in subchecks.values())
        _add_item(items, "A0-17", "baseline_internal_cross_consistency", "all_subchecks_pass", subchecks, passed)
    except Exception as exc:
        _append_exception_item(items, "A0-17", "baseline_internal_cross_consistency", "all_subchecks_pass", exc)

    # A0-18
    try:
        exp_hash = baseline.get("legacy_holding_samples_set_hash")
        experiment_version = str(baseline.get("experiment_version", "")).strip()
        if not experiment_version:
            raise ValueError("baseline.experiment_version 为空，无法定位 holding_clean.parquet")
        hc_path = Path("data") / "processed" / experiment_version / "holding_clean.parquet"
        if not hc_path.exists():
            raise FileNotFoundError(f"holding_clean.parquet 不存在: {hc_path}")
        hc_df = pd.read_parquet(hc_path, columns=["idxs", "signal_date"])
        if "idxs" not in hc_df.columns or "signal_date" not in hc_df.columns:
            raise ValueError(f"holding_clean.parquet 缺少必要列 idxs/signal_date，实际列: {list(hc_df.columns)}")
        hash_input = "\n".join(sorted(f"{a},{int(b)}" for a, b in zip(hc_df["idxs"], hc_df["signal_date"])))
        actual_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:16]
        passed = actual_hash == exp_hash
        _add_item(items, "A0-18", "legacy_hash_recomputed", exp_hash, actual_hash, passed)
    except Exception as exc:
        _append_exception_item(items, "A0-18", "legacy_hash_recomputed", baseline.get("legacy_holding_samples_set_hash"), exc)


def _run_m1_checks(args: argparse.Namespace, baseline: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """M1 断言 A1-01…A1-16：samples 层结构验证。"""
    import numpy as np

    samples_ids: list[str] = args.samples_id

    EXPECTED_COLS = {
        "sample_id", "idxs", "idxd", "open", "high", "low", "close",
        "volume", "amount", "is_paused", "marks", "sampling_method",
        "sampling_params_hash", "datebar_fingerprint", "created_at",
    }
    MANIFEST_17_KEYS = {
        "samples_id", "sampling_method", "sampling_params", "params_hash",
        "datebar_fingerprint", "samples_set_hash", "universe", "date_range",
        "min_history_days", "max_samples", "rows_before_cap", "sampling_rate",
        "year_quota", "rows", "n_symbols", "created_at", "code_version",
    }

    # Preload all sample data
    sample_data: dict[str, dict[str, Any]] = {}
    for sid in samples_ids:
        try:
            parquet_path = Path("data") / "prepared" / sid / "samples.parquet"
            manifest_path = Path("data") / "prepared" / sid / "manifest.json"
            df = pd.read_parquet(parquet_path)
            manifest = _read_json(manifest_path)
            sample_data[sid] = {"df": df, "manifest": manifest, "error": None}
        except Exception as exc:
            sample_data[sid] = {"df": None, "manifest": None, "error": f"{type(exc).__name__}: {exc}"}

    # A1-01: column set == EXPECTED_COLS
    try:
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            else:
                actual = set(d["df"].columns.tolist())
                if actual != EXPECTED_COLS:
                    per[sid] = sorted(actual); ok = False
                else:
                    per[sid] = "ok"
        _add_item(items, "A1-01", "samples_columns_match_design", sorted(EXPECTED_COLS),
                  "all_ok" if ok else per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-01", "samples_columns_match_design", sorted(EXPECTED_COLS), exc)

    # A1-02: no target*/label* columns
    try:
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            else:
                bad = [c for c in d["df"].columns if re.match(r"^(target|label)", c)]
                per[sid] = bad
                if bad:
                    ok = False
        _add_item(items, "A1-02", "samples_no_target_label_columns", 0,
                  {sid: len(v) for sid, v in per.items() if isinstance(v, list)}, ok,
                  detail=str({sid: v for sid, v in per.items() if isinstance(v, list) and v}))
    except Exception as exc:
        _append_exception_item(items, "A1-02", "samples_no_target_label_columns", 0, exc)

    # A1-03: sample_id dtype == int64
    try:
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            else:
                dtype_str = str(d["df"]["sample_id"].dtype) if "sample_id" in d["df"].columns else "missing"
                per[sid] = dtype_str
                if dtype_str != "int64":
                    ok = False
        _add_item(items, "A1-03", "sample_id_dtype_int64", "int64", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-03", "sample_id_dtype_int64", "int64", exc)

    # A1-04: sample_id unique
    try:
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            elif "sample_id" not in d["df"].columns:
                per[sid] = "missing_column"; ok = False
            else:
                n = len(d["df"]); u = d["df"]["sample_id"].nunique()
                per[sid] = {"len": n, "nunique": u}
                if n != u:
                    ok = False
        _add_item(items, "A1-04", "sample_id_unique", "nunique==len", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-04", "sample_id_unique", "nunique==len", exc)

    # A1-05: sample_id strictly ascending
    try:
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            elif "sample_id" not in d["df"].columns:
                per[sid] = "missing_column"; ok = False
            else:
                ids = d["df"]["sample_id"]
                asc = True if len(ids) <= 1 else bool((ids.diff().dropna() > 0).all())
                per[sid] = "ok" if asc else "not_ascending"
                if not asc:
                    ok = False
        _add_item(items, "A1-05", "sample_id_strictly_ascending", "diff()>0_all", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-05", "sample_id_strictly_ascending", "diff()>0_all", exc)

    # A1-06: manifest contains 17 required keys
    try:
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            else:
                mkeys = set(d["manifest"].keys()) if d["manifest"] else set()
                missing = sorted(MANIFEST_17_KEYS - mkeys)
                per[sid] = missing if missing else "ok"
                if missing:
                    ok = False
        _add_item(items, "A1-06", "manifest_contains_17_keys", sorted(MANIFEST_17_KEYS), per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-06", "manifest_contains_17_keys", sorted(MANIFEST_17_KEYS), exc)

    # A1-07: params_hash recomputed == stored (8 chars)
    try:
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            else:
                m = d["manifest"]
                hash_input = {
                    "method": m.get("sampling_method"),
                    "params": m.get("sampling_params"),
                    "universe": m.get("universe"),
                    "date_range": m.get("date_range"),
                    "min_history_days": m.get("min_history_days"),
                    "max_samples": m.get("max_samples"),
                }
                canonical = json.dumps(hash_input, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                computed = hashlib.sha256(canonical.encode()).hexdigest()[:8]
                stored = m.get("params_hash", "")
                per[sid] = {"computed": computed, "stored": stored}
                if computed != stored:
                    ok = False
        _add_item(items, "A1-07", "params_hash_recomputed", "computed==stored_8chars", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-07", "params_hash_recomputed", "computed==stored_8chars", exc)

    # A1-08: datebar_fingerprint == reconstructed from baseline sha256 + probe stats
    try:
        bl_datebar = baseline.get("raw", {}).get("datebar", {})
        sha_prefix = str(bl_datebar.get("sha256", ""))[:12]
        probe = _read_json(Path("version_design/design/refactor/datebar_probe.json"))
        expected_fp = (
            f"{sha_prefix}_{probe['rows']}_{probe['min_idxd']}_{probe['max_idxd']}_{probe['n_symbols']}"
        )
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            else:
                stored_fp = d["manifest"].get("datebar_fingerprint", "")
                per[sid] = {"stored": stored_fp}
                if stored_fp != expected_fp:
                    ok = False
        _add_item(items, "A1-08", "datebar_fingerprint_matches", expected_fp, per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-08", "datebar_fingerprint_matches", "reconstructed_fp", exc)

    # A1-09: samples_set_hash recomputed == stored (16 chars)
    try:
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            elif "sample_id" not in d["df"].columns:
                per[sid] = "missing_column"; ok = False
            else:
                sid_arr = d["df"]["sample_id"].to_numpy().astype("<i8")
                computed = hashlib.sha256(np.sort(sid_arr).tobytes()).hexdigest()[:16]
                stored = d["manifest"].get("samples_set_hash", "")
                per[sid] = {"computed": computed, "stored": stored}
                if computed != stored:
                    ok = False
        _add_item(items, "A1-09", "samples_set_hash_recomputed", "computed==stored_16chars", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-09", "samples_set_hash_recomputed", "computed==stored_16chars", exc)

    # A1-10: max_samples effective → rows == max_samples; not effective → "不适用" (must still record item)
    try:
        per = {}
        ok = True
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            else:
                m = d["manifest"]
                max_s = m.get("max_samples")
                rows = m.get("rows", 0)
                rows_bc = m.get("rows_before_cap", 0)
                if max_s is not None and rows_bc > 0:
                    effective = int(rows) == int(max_s)
                    per[sid] = {"rows": rows, "max_samples": max_s, "effective": True, "match": effective}
                    if not effective:
                        ok = False
                else:
                    per[sid] = {"rows": rows, "max_samples": None, "effective": False, "note": "不适用"}
        _add_item(items, "A1-10", "max_samples_cap_effective", None, per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-10", "max_samples_cap_effective", None, exc)

    # A1-11: sampling_rate == rows / rows_before_cap (or 1.0 when no cap)
    try:
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            else:
                m = d["manifest"]
                rows_bc = m.get("rows_before_cap", 0)
                rows = m.get("rows", 0)
                sr = float(m.get("sampling_rate", 1.0))
                expected_sr = float(rows) / float(rows_bc) if rows_bc > 0 else 1.0
                rate_ok = _float_close(sr, expected_sr)
                per[sid] = {"sampling_rate": sr, "expected": expected_sr}
                if not rate_ok:
                    ok = False
        _add_item(items, "A1-11", "sampling_rate_consistent", "rows/rows_before_cap", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-11", "sampling_rate_consistent", "rows/rows_before_cap", exc)

    # A1-12: no excluded_prefixes in idxs (from baseline.data_prep.universe)
    try:
        excl = baseline.get("data_prep", {}).get("universe", {}).get("exclude_prefixes", [])
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            elif "idxs" not in d["df"].columns:
                per[sid] = "missing_column"; ok = False
            else:
                bad = int(sum(d["df"]["idxs"].str.startswith(p).sum() for p in excl))
                per[sid] = bad
                if bad > 0:
                    ok = False
        _add_item(items, "A1-12", "no_excluded_prefix_in_idxs",
                  {"exclude_prefixes": excl, "expected_count": 0}, per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-12", "no_excluded_prefix_in_idxs",
                                {"exclude_prefixes": "baseline.data_prep.universe.exclude_prefixes"}, exc)

    # A1-13: exactly one sample set's (idxs, idxd) fingerprint == legacy hash
    try:
        legacy_hash = str(baseline.get("legacy_holding_samples_set_hash", ""))
        per = {}
        matched: list[str] = []
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}
            elif "idxs" in d["df"].columns and "idxd" in d["df"].columns:
                fp_str = "\n".join(sorted(
                    f"{a},{int(b)}" for a, b in zip(d["df"]["idxs"], d["df"]["idxd"])
                ))
                fp = hashlib.sha256(fp_str.encode("utf-8")).hexdigest()[:16]
                per[sid] = fp
                if fp == legacy_hash:
                    matched.append(sid)
            else:
                per[sid] = "missing_columns"
        exactly_one = len(matched) == 1
        _add_item(items, "A1-13", "exactly_one_legacy_fingerprint_match",
                  {"legacy_hash": legacy_hash, "expected_count": 1},
                  {"fingerprints": per, "matched": matched}, exactly_one)
    except Exception as exc:
        _append_exception_item(items, "A1-13", "exactly_one_legacy_fingerprint_match",
                                {"legacy_hash": baseline.get("legacy_holding_samples_set_hash")}, exc)

    # A1-14: sample_id <-> (idxs, idxd) bijection (vectorized decode)
    try:
        P14, P8, P6 = 10 ** 14, 10 ** 8, 10 ** 6
        MPREFIX = {1: "sh", 2: "sz", 3: "bj"}
        per = {}
        ok = bool(samples_ids)
        for sid, d in sample_data.items():
            if d["error"]:
                per[sid] = {"error": d["error"]}; ok = False
            elif not {"sample_id", "idxs", "idxd"}.issubset(d["df"].columns):
                per[sid] = "missing_columns"; ok = False
            else:
                sids_arr = d["df"]["sample_id"].to_numpy()
                market_arr = sids_arr // P14
                code6_arr = (sids_arr // P8) % P6
                idxd_dec = sids_arr % P8
                decoded_idxs = np.array(
                    [f"{MPREFIX.get(int(m), 'unknown')}{int(c):06d}"
                     for m, c in zip(market_arr, code6_arr)]
                )
                actual_idxs = d["df"]["idxs"].to_numpy()
                actual_idxd = d["df"]["idxd"].to_numpy().astype(np.int64)
                row_mismatch = int(((decoded_idxs != actual_idxs) | (idxd_dec != actual_idxd)).sum())
                per[sid] = {"mismatches": row_mismatch, "total": len(d["df"])}
                if row_mismatch > 0:
                    ok = False
        _add_item(items, "A1-14", "sample_id_idxs_idxd_bijection", "0_mismatches", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A1-14", "sample_id_idxs_idxd_bijection", "0_mismatches", exc)

    # A1-15: verify_v11.py only additions vs m0-ok (delete col == 0, add col > 0)
    try:
        rc_m1, _, _ = _run_subprocess(["git", "--no-pager", "rev-parse", "m1-ok"])
        if rc_m1 == 0:
            cmd = ["git", "--no-pager", "diff", "--numstat", "m0-ok..m1-ok", "--", "tools/verify_v11.py"]
        else:
            cmd = ["git", "--no-pager", "diff", "--numstat", "m0-ok", "--", "tools/verify_v11.py"]
        rc, stdout, _ = _run_subprocess(cmd)
        if rc != 0:
            raise RuntimeError(f"git diff 失败; rc={rc}")
        parts = stdout.strip().split()
        additions = int(parts[0]) if len(parts) >= 2 and parts[0].isdigit() else -1
        deletions = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else -1
        passed = deletions == 0 and additions > 0
        _add_item(items, "A1-15", "verify_v11_only_additions",
                  {"deletions": 0, "additions_gt_0": True},
                  {"raw": stdout, "deletions": deletions, "additions": additions}, passed)
    except Exception as exc:
        _append_exception_item(items, "A1-15", "verify_v11_only_additions",
                                {"deletions": 0, "additions_gt_0": True}, exc)

    # A1-16: at least 2 --samples-id; exactly one fingerprint == legacy hash
    try:
        count = len(samples_ids)
        at_least_2 = count >= 2
        legacy_hash_16 = str(baseline.get("legacy_holding_samples_set_hash", ""))
        matched_count = 0
        for sid, d in sample_data.items():
            if d.get("error"):
                continue
            df = d["df"]
            if "idxs" in df.columns and "idxd" in df.columns:
                fp_str = "\n".join(sorted(f"{a},{int(b)}" for a, b in zip(df["idxs"], df["idxd"])))
                if hashlib.sha256(fp_str.encode("utf-8")).hexdigest()[:16] == legacy_hash_16:
                    matched_count += 1
        passed = at_least_2 and matched_count == 1
        _add_item(items, "A1-16", "samples_id_list_valid",
                  {"min_count": 2, "legacy_hash_matches": 1},
                  {"count": count, "legacy_matches": matched_count}, passed)
    except Exception as exc:
        _append_exception_item(items, "A1-16", "samples_id_list_valid",
                                {"min_count": 2, "legacy_hash_matches": 1}, exc)


def _run_m2_checks(args: argparse.Namespace, baseline: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """M2 断言 A2-01…A2-11：labels 层结构与缓存验证。"""
    import numpy as np
    import pyarrow.parquet as pq

    samples_ids: list[str] = args.samples_id
    builtin_names: list[str] = baseline.get("labels", {}).get("builtin_names", [])
    legacy_hash = str(baseline.get("legacy_holding_samples_set_hash", ""))

    LABEL_REQUIRED_COLS: set[str] = {
        "sample_id", "idxs", "signal_date", "target",
        "entry_idxd", "exit_idxd", "is_complete", "is_entry_blocked",
        "label_name", "label_params_hash", "samples_id",
        "datebar_fingerprint", "created_at",
    }

    # ── 预加载 samples 数据 ──
    sample_data: dict[str, dict[str, Any]] = {}
    for sid in samples_ids:
        try:
            sp = Path("data") / "prepared" / sid / "samples.parquet"
            mp = Path("data") / "prepared" / sid / "manifest.json"
            df = pd.read_parquet(sp)
            manifest = _read_json(mp)
            sample_data[sid] = {"df": df, "manifest": manifest, "error": None}
        except Exception as exc:
            sample_data[sid] = {"df": None, "manifest": None, "error": f"{type(exc).__name__}: {exc}"}

    # 识别 legacy samples_id（sampling_method == "legacy_holding" 的那个）
    def _is_legacy_sid(sid: str) -> bool:
        d = sample_data.get(sid, {})
        m = d.get("manifest") or {}
        return str(m.get("sampling_method", "")) == "legacy_holding"

    # 预加载标签 parquet 的关键列（避免重复 IO）
    # label_meta[sid][label_name] = dict or None on error
    label_meta: dict[str, dict[str, Any]] = {}
    for sid in samples_ids:
        label_meta[sid] = {}
        for lname in builtin_names:
            lpath = Path("data") / "labels" / sid / f"{lname}.parquet"
            if not lpath.exists():
                label_meta[sid][lname] = None
                continue
            try:
                df_m = pd.read_parquet(
                    lpath,
                    columns=["label_name", "label_params_hash", "samples_id", "datebar_fingerprint", "target"],
                )
                label_meta[sid][lname] = {"path": lpath, "df_meta": df_m}
            except Exception as exc:
                label_meta[sid][lname] = {"path": lpath, "error": f"{type(exc).__name__}: {exc}"}

    # ── A2-01：每个标签文件在每个 samples_id 下都存在 ──
    try:
        if not samples_ids:
            _add_item(items, "A2-01", "label_files_exist_per_samples_id",
                      "non_empty_samples_id_list", {"count": 0}, False, detail="--samples-id 为空")
        else:
            per: dict[str, Any] = {}
            ok = True
            for sid in samples_ids:
                per[sid] = {}
                for lname in builtin_names:
                    exists = label_meta[sid].get(lname) is not None
                    per[sid][lname] = exists
                    if not exists:
                        ok = False
            _add_item(items, "A2-01", "label_files_exist_per_samples_id",
                      {"builtin_names": builtin_names, "all_exist": True}, per, ok)
    except Exception as exc:
        _append_exception_item(items, "A2-01", "label_files_exist_per_samples_id", builtin_names, exc)

    # ── A2-02：每个标签文件列集合 == 设计 §3.2（13 列）──
    try:
        per = {}
        ok = bool(samples_ids and builtin_names)
        for sid in samples_ids:
            per[sid] = {}
            for lname in builtin_names:
                meta = label_meta[sid].get(lname)
                if meta is None:
                    per[sid][lname] = "文件不存在"; ok = False
                elif "error" in meta:
                    per[sid][lname] = meta["error"]; ok = False
                else:
                    try:
                        schema = pq.read_schema(meta["path"])
                        actual_cols = set(schema.names)
                        if actual_cols == LABEL_REQUIRED_COLS:
                            per[sid][lname] = "ok"
                        else:
                            per[sid][lname] = {
                                "missing": sorted(LABEL_REQUIRED_COLS - actual_cols),
                                "extra": sorted(actual_cols - LABEL_REQUIRED_COLS),
                            }
                            ok = False
                    except Exception as e2:
                        per[sid][lname] = f"schema error: {e2}"; ok = False
        _add_item(items, "A2-02", "label_columns_match_design",
                  sorted(LABEL_REQUIRED_COLS), per, ok)
    except Exception as exc:
        _append_exception_item(items, "A2-02", "label_columns_match_design", sorted(LABEL_REQUIRED_COLS), exc)

    # ── A2-03：每个标签 rows == 对应 samples.parquet 行数 ──
    try:
        per = {}
        ok = bool(samples_ids and builtin_names)
        for sid in samples_ids:
            per[sid] = {}
            sd = sample_data.get(sid, {})
            if sd.get("error"):
                per[sid] = {"samples_error": sd["error"]}; ok = False
                continue
            expected_rows = len(sd["df"]) if sd.get("df") is not None else -1
            for lname in builtin_names:
                meta = label_meta[sid].get(lname)
                if meta is None:
                    per[sid][lname] = "文件不存在"; ok = False
                elif "error" in meta:
                    per[sid][lname] = meta["error"]; ok = False
                else:
                    actual_rows = len(meta["df_meta"])
                    per[sid][lname] = {"expected": expected_rows, "actual": actual_rows}
                    if actual_rows != expected_rows:
                        ok = False
        _add_item(items, "A2-03", "label_rows_match_samples_rows",
                  "label_rows == samples.parquet_rows", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A2-03", "label_rows_match_samples_rows", "label_rows==samples_rows", exc)

    # ── A2-04：target 无 ±inf ──
    try:
        per = {}
        ok = bool(samples_ids and builtin_names)
        for sid in samples_ids:
            per[sid] = {}
            for lname in builtin_names:
                meta = label_meta[sid].get(lname)
                if meta is None:
                    per[sid][lname] = "文件不存在"; ok = False
                elif "error" in meta:
                    per[sid][lname] = meta["error"]; ok = False
                else:
                    target_col = meta["df_meta"]["target"]
                    inf_count = int(np.isinf(target_col.dropna()).sum())
                    per[sid][lname] = inf_count
                    if inf_count != 0:
                        ok = False
        _add_item(items, "A2-04", "target_no_inf", {"expected_inf_count": 0}, per, ok)
    except Exception as exc:
        _append_exception_item(items, "A2-04", "target_no_inf", 0, exc)

    # ── A2-05：_index.json 每项 params_hash == parquet label_params_hash 列（8 位）──
    try:
        per = {}
        ok = bool(samples_ids and builtin_names)
        for sid in samples_ids:
            per[sid] = {}
            index_path = Path("data") / "labels" / sid / "_index.json"
            if not index_path.exists():
                per[sid] = {"_index.json": "不存在"}; ok = False
                continue
            try:
                index_data = _read_json(index_path)
            except Exception as e2:
                per[sid] = {"_index.json": f"读取失败: {e2}"}; ok = False
                continue
            for lname in builtin_names:
                meta = label_meta[sid].get(lname)
                if meta is None:
                    per[sid][lname] = "文件不存在"; ok = False
                    continue
                if "error" in meta:
                    per[sid][lname] = meta["error"]; ok = False
                    continue
                stored_hash = str(index_data.get(lname, {}).get("params_hash", ""))
                parquet_hashes = meta["df_meta"]["label_params_hash"].unique().tolist()
                if len(parquet_hashes) != 1:
                    per[sid][lname] = {"error": f"label_params_hash 列有多个不同值: {parquet_hashes}"}
                    ok = False
                else:
                    parquet_hash = str(parquet_hashes[0])
                    match = stored_hash == parquet_hash and len(stored_hash) == 8
                    per[sid][lname] = {"index_hash": stored_hash, "parquet_hash": parquet_hash, "match": match}
                    if not match:
                        ok = False
        _add_item(items, "A2-05", "index_params_hash_matches_parquet",
                  {"expected": "index==parquet, 8chars"}, per, ok)
    except Exception as exc:
        _append_exception_item(items, "A2-05", "index_params_hash_matches_parquet",
                               "index_hash==parquet_hash", exc)

    # ── A2-06：_index.json 每项 coverage == non_null_rows / rows ──
    try:
        per = {}
        ok = bool(samples_ids and builtin_names)
        for sid in samples_ids:
            per[sid] = {}
            index_path = Path("data") / "labels" / sid / "_index.json"
            if not index_path.exists():
                per[sid] = {"_index.json": "不存在"}; ok = False
                continue
            try:
                index_data = _read_json(index_path)
            except Exception as e2:
                per[sid] = {"_index.json": f"读取失败: {e2}"}; ok = False
                continue
            for lname in builtin_names:
                entry = index_data.get(lname, {})
                rows = entry.get("rows")
                non_null = entry.get("non_null_rows")
                stored_cov = entry.get("coverage")
                if rows is None or non_null is None or stored_cov is None:
                    per[sid][lname] = {"error": f"_index.json 缺字段: {entry}"}
                    ok = False
                    continue
                expected_cov = float(non_null) / float(rows) if int(rows) > 0 else 0.0
                match = _float_close(float(stored_cov), expected_cov)
                per[sid][lname] = {
                    "stored_coverage": stored_cov,
                    "computed": expected_cov,
                    "match": match,
                }
                if not match:
                    ok = False
        _add_item(items, "A2-06", "index_coverage_consistent",
                  "coverage == non_null_rows / rows", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A2-06", "index_coverage_consistent",
                               "coverage==non_null/rows", exc)

    # ── A2-07：target_holding_return 的 coverage == baseline（仅 legacy 集）──
    HOLDING_LABEL = "target_holding_return"
    expected_cov_baseline = baseline.get("labels", {}).get("target_holding_return", {}).get("coverage")
    try:
        per = {}
        ok = True
        for sid in samples_ids:
            is_leg = _is_legacy_sid(sid)
            index_path = Path("data") / "labels" / sid / "_index.json"
            if not index_path.exists():
                per[sid] = {"error": "_index.json 不存在"}; ok = False
                continue
            try:
                index_data = _read_json(index_path)
            except Exception as e2:
                per[sid] = {"error": f"读取 _index.json 失败: {e2}"}; ok = False
                continue
            entry = index_data.get(HOLDING_LABEL, {})
            actual_cov = entry.get("coverage")
            if is_leg:
                if expected_cov_baseline is None:
                    per[sid] = {"error": "baseline 缺少 coverage 值"}; ok = False
                elif actual_cov is None:
                    per[sid] = {"error": "_index.json 缺少 coverage"}; ok = False
                elif not _float_close(float(actual_cov), float(expected_cov_baseline)):
                    per[sid] = {
                        "expected": expected_cov_baseline,
                        "actual": actual_cov,
                        "pass": False,
                    }
                    ok = False
                else:
                    per[sid] = {"expected": expected_cov_baseline, "actual": actual_cov, "pass": True}
            else:
                # 非 legacy：不检查 coverage 值，仅记录"不适用"
                per[sid] = {"note": "不适用（非 legacy 集）", "actual": actual_cov}
        _add_item(items, "A2-07", "holding_return_coverage_matches_baseline",
                  expected_cov_baseline, per, ok)
    except Exception as exc:
        _append_exception_item(items, "A2-07", "holding_return_coverage_matches_baseline",
                               expected_cov_baseline, exc)

    # ── A2-08：目录内无孤儿文件、索引内无孤儿条目 ──
    try:
        per = {}
        ok = bool(samples_ids)
        for sid in samples_ids:
            labels_dir = Path("data") / "labels" / sid
            if not labels_dir.exists():
                per[sid] = {"error": "labels 目录不存在"}; ok = False
                continue
            index_path = labels_dir / "_index.json"
            if not index_path.exists():
                per[sid] = {"error": "_index.json 不存在"}; ok = False
                continue
            try:
                index_data = _read_json(index_path)
            except Exception as e2:
                per[sid] = {"error": f"读取 _index.json 失败: {e2}"}; ok = False
                continue
            # Files in directory (parquet only, excluding _index.json)
            disk_names = {p.stem for p in labels_dir.glob("*.parquet")}
            index_names = set(index_data.keys())
            orphan_files = sorted(disk_names - index_names)  # on disk but not in index
            orphan_entries = sorted(index_names - disk_names)  # in index but not on disk
            is_ok = not orphan_files and not orphan_entries
            per[sid] = {
                "disk": sorted(disk_names),
                "index": sorted(index_names),
                "orphan_files": orphan_files,
                "orphan_entries": orphan_entries,
            }
            if not is_ok:
                ok = False
        _add_item(items, "A2-08", "no_orphan_files_or_entries",
                  {"orphan_files": 0, "orphan_entries": 0}, per, ok)
    except Exception as exc:
        _append_exception_item(items, "A2-08", "no_orphan_files_or_entries",
                               {"orphan_files": 0, "orphan_entries": 0}, exc)

    # ── A2-09：每行的 label_name 列 == 文件名 ──
    try:
        per = {}
        ok = bool(samples_ids and builtin_names)
        for sid in samples_ids:
            per[sid] = {}
            for lname in builtin_names:
                meta = label_meta[sid].get(lname)
                if meta is None:
                    per[sid][lname] = "文件不存在"; ok = False
                elif "error" in meta:
                    per[sid][lname] = meta["error"]; ok = False
                else:
                    unique_vals = meta["df_meta"]["label_name"].unique().tolist()
                    all_match = len(unique_vals) == 1 and str(unique_vals[0]) == lname
                    per[sid][lname] = {"unique_values": unique_vals, "match": all_match}
                    if not all_match:
                        ok = False
        _add_item(items, "A2-09", "label_name_column_equals_filename",
                  "all_rows_label_name==filename", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A2-09", "label_name_column_equals_filename",
                               "label_name==filename", exc)

    # ── A2-10：label_params_hash / samples_id / datebar_fingerprint 列 == manifest 与 _index ──
    try:
        per = {}
        ok = bool(samples_ids and builtin_names)
        for sid in samples_ids:
            per[sid] = {}
            sd = sample_data.get(sid, {})
            manifest = sd.get("manifest") or {}
            expected_datebar_fp = str(manifest.get("datebar_fingerprint", ""))
            index_path = Path("data") / "labels" / sid / "_index.json"
            if not index_path.exists():
                per[sid] = {"error": "_index.json 不存在"}; ok = False
                continue
            try:
                index_data = _read_json(index_path)
            except Exception as e2:
                per[sid] = {"error": f"读取 _index.json 失败: {e2}"}; ok = False
                continue
            for lname in builtin_names:
                meta = label_meta[sid].get(lname)
                if meta is None:
                    per[sid][lname] = "文件不存在"; ok = False
                    continue
                if "error" in meta:
                    per[sid][lname] = meta["error"]; ok = False
                    continue
                df_m = meta["df_meta"]
                # label_params_hash column vs _index
                index_hash = str(index_data.get(lname, {}).get("params_hash", ""))
                parquet_hashes = df_m["label_params_hash"].unique().tolist()
                hash_match = len(parquet_hashes) == 1 and str(parquet_hashes[0]) == index_hash
                # samples_id column
                parquet_sids = df_m["samples_id"].unique().tolist()
                sid_match = len(parquet_sids) == 1 and str(parquet_sids[0]) == sid
                # datebar_fingerprint column vs manifest
                parquet_dbs = df_m["datebar_fingerprint"].unique().tolist()
                db_match = len(parquet_dbs) == 1 and str(parquet_dbs[0]) == expected_datebar_fp
                all_ok = hash_match and sid_match and db_match
                per[sid][lname] = {
                    "hash_match": hash_match,
                    "sid_match": sid_match,
                    "datebar_fp_match": db_match,
                }
                if not all_ok:
                    ok = False
        _add_item(items, "A2-10", "label_metadata_columns_match_manifest_index",
                  "hash_match and sid_match and db_match", per, ok)
    except Exception as exc:
        _append_exception_item(items, "A2-10", "label_metadata_columns_match_manifest_index",
                               "columns==manifest/_index", exc)

    # ── A2-11：只增不改（verify_v11.py 相对 m1-ok 删除列 == 0，新增列 > 0）──
    try:
        rc_m2, _, _ = _run_subprocess(["git", "--no-pager", "rev-parse", "m2-ok"])
        if rc_m2 == 0:
            cmd = ["git", "--no-pager", "diff", "--numstat", "m1-ok..m2-ok", "--", "tools/verify_v11.py"]
        else:
            cmd = ["git", "--no-pager", "diff", "--numstat", "m1-ok", "--", "tools/verify_v11.py"]
        rc, stdout, _ = _run_subprocess(cmd)
        if rc != 0:
            raise RuntimeError(f"git diff 失败; rc={rc}")
        parts = stdout.strip().split()
        additions = int(parts[0]) if len(parts) >= 2 and parts[0].isdigit() else -1
        deletions = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else -1
        passed = deletions == 0 and additions > 0
        _add_item(items, "A2-11", "verify_v11_only_additions",
                  {"deletions": 0, "additions_gt_0": True},
                  {"raw": stdout, "deletions": deletions, "additions": additions},
                  passed)
    except Exception as exc:
        _append_exception_item(items, "A2-11", "verify_v11_only_additions",
                               {"deletions": 0, "additions_gt_0": True}, exc)


def _run_m3_checks(args: argparse.Namespace, baseline: dict[str, Any], items: list[dict[str, Any]]) -> None:
    import re as _re

    # ── A3-01：越界检查（git diff --name-only HEAD 只含白名单或豁免路径）──
    try:
        _M3_WHITELIST = {
            "pipeline/config.py",
            "pipeline/adapters/sample_family.py",
            "pipeline/step2_slices.py",
            "tools/check_raw_data.py",
            "docs/15_data_contract.md",
            "tests/test_pipeline/test_config_v11.py",
            "tests/test_pipeline/test_step1_guard.py",
            "tools/verify_v11.py",
            # M3-6 运行 pipeline 会重写此文件；不计越界但必须随本里程碑提交
            "outputs/experiment_registry.json",
        }

        def _m3_exempt(path: str) -> bool:
            # version_design/design/refactor/: M3 本里程碑可能修设计文档，显式豁免
            return path.startswith("version_design/design/refactor/")

        rc_diff, diff_out, _ = _run_subprocess(["git", "--no-pager", "diff", "--name-only", "HEAD"])
        changed = {f.strip() for f in diff_out.splitlines() if f.strip()}
        out_of_whitelist = sorted(f for f in changed if f not in _M3_WHITELIST and not _m3_exempt(f))
        exempted = sorted(f for f in changed if _m3_exempt(f))
        _add_item(
            items, "A3-01", "whitelist_check",
            {"whitelist": sorted(_M3_WHITELIST), "exempt_prefixes": ["version_design/design/refactor/"]},
            {"changed": sorted(changed), "out_of_whitelist": out_of_whitelist, "exempted": exempted},
            len(out_of_whitelist) == 0,
            detail=f"越界文件: {out_of_whitelist}" if out_of_whitelist else "",
        )
    except Exception as exc:
        _append_exception_item(items, "A3-01", "whitelist_check", "M3_whitelist", exc)

    # ── A3-02：旧配置 input.mode == "holding_file" ──
    try:
        code = (
            "import json,sys;"
            "from pipeline.config import load_params;"
            "p=load_params(sys.argv[1]);"
            "print(json.dumps({'mode': p.input.mode, 'source': p.target.source}))"
        )
        rc, stdout, stderr = _run_subprocess([sys.executable, "-c", code, args.config])
        if rc != 0:
            raise RuntimeError(f"load_params 失败; rc={rc}; stderr={stderr[:300]}")
        payload = json.loads(stdout)
        mode = payload.get("mode", "")
        passed = mode == "holding_file"
        _add_item(items, "A3-02", "old_config_holding_file_mode",
                  {"mode": "holding_file"}, payload, passed,
                  detail="" if passed else f"mode={mode!r}")
    except Exception as exc:
        _append_exception_item(items, "A3-02", "old_config_holding_file_mode", {"mode": "holding_file"}, exc)

    # ── A3-03：samples 配置 load_params 成功（exit 0）──
    try:
        cfg_path = Path(args.config)
        samples_config = str(cfg_path.parent / (cfg_path.stem + "_samples" + cfg_path.suffix))
        code = (
            "import json,sys;"
            "from pipeline.config import load_params;"
            "p=load_params(sys.argv[1]);"
            # 输出 JSON 到 stdout；V11 警告已输出到 stderr，不污染 JSON
            "print(json.dumps({"
            "'mode': p.input.mode, 'source': p.target.source,"
            "'label': p.target.label, 'horizon_days': p.target.horizon_days"
            "}))"
        )
        rc, stdout, stderr = _run_subprocess([sys.executable, "-c", code, samples_config])
        if rc != 0:
            raise RuntimeError(f"samples 配置被拒绝; rc={rc}; stderr={stderr[:400]}")
        # stdout 应为纯 JSON（警告已重定向到 stderr）
        payload = json.loads(stdout.strip())
        _add_item(items, "A3-03", "samples_config_loads_successfully",
                  {"exit_code": 0}, {"exit_code": rc, "config": samples_config, "result": payload},
                  rc == 0)
    except Exception as exc:
        _append_exception_item(items, "A3-03", "samples_config_loads_successfully", {"exit_code": 0}, exc)

    # ── A3-04：测试文件同时引用 input.mode/samples 与 NotImplementedError（代理断言）──
    try:
        test_files = [
            Path("tests/test_pipeline/test_config_v11.py"),
            Path("tests/test_pipeline/test_step1_guard.py"),
        ]
        hits: list[str] = []
        for tf in test_files:
            if tf.exists():
                content = tf.read_text(encoding="utf-8")
                has_mode_or_samples = (
                    "input.mode" in content
                    or '"samples"' in content
                    or "'samples'" in content
                )
                has_not_impl = "NotImplementedError" in content
                if has_mode_or_samples and has_not_impl:
                    hits.append(str(tf))
        passed = len(hits) >= 1
        _add_item(items, "A3-04", "test_files_reference_samples_and_not_implemented",
                  {"min_hits": 1, "patterns": ["input.mode/samples", "NotImplementedError"]},
                  {"hits": hits, "files_checked": [str(tf) for tf in test_files]},
                  passed,
                  detail="未发现引用 samples 与 NotImplementedError 的测试文件" if not passed else "")
    except Exception as exc:
        _append_exception_item(items, "A3-04", "test_files_reference_samples_and_not_implemented",
                               {"min_hits": 1}, exc)

    # ── A3-05：check_raw_data --config <args.config> 返回 0 ──
    try:
        rc, stdout, stderr = _run_subprocess(
            [sys.executable, "tools/check_raw_data.py", "--config", args.config]
        )
        _add_item(items, "A3-05", "check_raw_data_exits_0",
                  {"exit_code": 0},
                  {"exit_code": rc, "stdout": stdout[:400], "stderr": stderr[:200]},
                  rc == 0)
    except Exception as exc:
        _append_exception_item(items, "A3-05", "check_raw_data_exits_0", {"exit_code": 0}, exc)

    # ── A3-06：SAMPLES_REQUIRED_COLUMNS / LABEL_REQUIRED_COLUMNS 存在且非空且元素为 str ──
    try:
        code = (
            "import json,sys;"
            "from pipeline.adapters.sample_family import SAMPLES_REQUIRED_COLUMNS, LABEL_REQUIRED_COLUMNS;"
            "print(json.dumps({"
            "'samples_cols': list(SAMPLES_REQUIRED_COLUMNS),"
            "'label_cols': list(LABEL_REQUIRED_COLUMNS)"
            "}))"
        )
        rc, stdout, stderr = _run_subprocess([sys.executable, "-c", code])
        if rc != 0:
            raise RuntimeError(f"导入失败: rc={rc}; stderr={stderr[:300]}")
        payload = json.loads(stdout)
        sc = payload.get("samples_cols", [])
        lc = payload.get("label_cols", [])
        sc_ok = len(sc) > 0 and all(isinstance(x, str) for x in sc)
        lc_ok = len(lc) > 0 and all(isinstance(x, str) for x in lc)
        passed = sc_ok and lc_ok
        _add_item(items, "A3-06", "contract_columns_exported",
                  {"SAMPLES_REQUIRED_COLUMNS": "non_empty_str_tuple",
                   "LABEL_REQUIRED_COLUMNS": "non_empty_str_tuple"},
                  {"samples_cols_count": len(sc), "label_cols_count": len(lc),
                   "sc_ok": sc_ok, "lc_ok": lc_ok},
                  passed)
    except Exception as exc:
        _append_exception_item(items, "A3-06", "contract_columns_exported",
                               {"SAMPLES_REQUIRED_COLUMNS": "non_empty",
                                "LABEL_REQUIRED_COLUMNS": "non_empty"}, exc)

    # ── A3-07：step2 invalid_reasons 含且仅含 4 个新键，不含 missing_signal_date ──
    try:
        step2_path = Path("pipeline/step2_slices.py")
        step2_content = step2_path.read_text(encoding="utf-8") if step2_path.exists() else ""
        required_keys = frozenset({"unknown_symbol", "date_not_in_symbol_range",
                                   "paused_signal_date", "insufficient_history"})
        forbidden_keys = frozenset({"missing_signal_date"})
        lines_a7 = step2_content.splitlines()
        init_blocks: list[str] = []
        for i, line in enumerate(lines_a7):
            if _re.search(r'\binvalid_reasons\s*=\s*\{', line):
                block = line
                depth = line.count("{") - line.count("}")
                j = i + 1
                while depth > 0 and j < len(lines_a7):
                    block += " " + lines_a7[j]
                    depth += lines_a7[j].count("{") - lines_a7[j].count("}")
                    j += 1
                init_blocks.append(block)

        def _extract_keys(block: str) -> frozenset:
            found = set(_re.findall(r'"(\w+)"\s*:', block))
            found |= set(_re.findall(r"'(\w+)'\s*:", block))
            return frozenset(found)

        all_blocks_exact = all(_extract_keys(b) == required_keys for b in init_blocks)
        has_forbidden = any(_extract_keys(b) & forbidden_keys for b in init_blocks)
        passed = len(init_blocks) >= 1 and all_blocks_exact and not has_forbidden
        _add_item(items, "A3-07", "step2_invalid_reasons_renamed",
                  {"required_keys": sorted(required_keys), "forbidden": sorted(forbidden_keys)},
                  {"init_blocks_count": len(init_blocks), "has_forbidden": has_forbidden,
                   "all_blocks_exact": all_blocks_exact},
                  passed)
    except Exception as exc:
        _append_exception_item(items, "A3-07", "step2_invalid_reasons_renamed",
                               {"required_keys": sorted({"unknown_symbol", "date_not_in_symbol_range",
                                                         "paused_signal_date", "insufficient_history"})}, exc)

    # ── A3-08：docs/15_data_contract.md 存在且含内置采样与内置标签两节 ──
    try:
        doc_path = Path("docs/15_data_contract.md")
        exists = doc_path.exists()
        has_sampling_section = False
        has_labels_section = False
        if exists:
            doc_text = doc_path.read_text(encoding="utf-8")
            has_sampling_section = "内置采样" in doc_text
            has_labels_section = "内置标签" in doc_text
        passed = exists and has_sampling_section and has_labels_section
        _add_item(items, "A3-08", "data_contract_doc_exists",
                  {"exists": True, "内置采样_section": True, "内置标签_section": True},
                  {"exists": exists, "内置采样_section": has_sampling_section,
                   "内置标签_section": has_labels_section},
                  passed)
    except Exception as exc:
        _append_exception_item(items, "A3-08", "data_contract_doc_exists",
                               {"exists": True, "sections": ["内置采样", "内置标签"]}, exc)

    # ── A3-09：data_prep/**/*.py 中无 import pipeline / from pipeline ──
    try:
        data_prep_path = Path("data_prep")
        hits_a9: list[str] = []
        if data_prep_path.exists():
            for py_file in sorted(data_prep_path.rglob("*.py")):
                try:
                    file_content = py_file.read_text(encoding="utf-8", errors="replace")
                    for lineno, line in enumerate(file_content.splitlines(), 1):
                        if _re.match(r'\s*(import pipeline|from pipeline)', line):
                            hits_a9.append(f"{py_file}:{lineno}: {line.strip()}")
                except Exception:
                    pass
        _add_item(items, "A3-09", "data_prep_no_pipeline_import",
                  {"expected_hits": 0},
                  {"hits": hits_a9},
                  len(hits_a9) == 0,
                  detail="; ".join(hits_a9[:5]) if hits_a9 else "")
    except Exception as exc:
        _append_exception_item(items, "A3-09", "data_prep_no_pipeline_import",
                               {"expected_hits": 0}, exc)

    # ── A3-10：build_samples.py / build_labels.py 无 yaml.safe_load 实际调用──
    # 检查非注释、非 docstring 行中是否有 yaml.safe_load(
    try:
        tools_to_check = ["tools/build_samples.py", "tools/build_labels.py"]
        hits_a10: list[str] = []
        for tool_path in tools_to_check:
            tp = Path(tool_path)
            if tp.exists():
                in_docstring = False
                for lineno, line in enumerate(tp.read_text(encoding="utf-8").splitlines(), 1):
                    stripped = line.strip()
                    # 简单 docstring 跟踪（三引号对）
                    if stripped.startswith('"""') or stripped.startswith("'''"):
                        quote = stripped[:3]
                        # count occurrences in the line (open/close pairs)
                        q_count = stripped.count(quote)
                        if q_count % 2 == 1:  # odd → toggle docstring state
                            in_docstring = not in_docstring
                        continue
                    if in_docstring:
                        continue
                    # Skip comment lines
                    if stripped.startswith("#"):
                        continue
                    # Check for actual call: yaml.safe_load(
                    if "yaml.safe_load(" in line:
                        hits_a10.append(f"{tool_path}:{lineno}: {line.strip()}")
        _add_item(items, "A3-10", "build_tools_no_yaml_safe_load_calls",
                  {"expected_hits": 0, "note": "docstring/comment mentions excluded"},
                  {"hits": hits_a10, "files_checked": tools_to_check},
                  len(hits_a10) == 0,
                  detail="; ".join(hits_a10[:5]) if hits_a10 else "")
    except Exception as exc:
        _append_exception_item(items, "A3-10", "build_tools_no_yaml_safe_load_calls",
                               {"expected_hits": 0}, exc)

    # ── A3-11：verify_v11 只增不改（m2-ok 基线，删除列 == 0，新增列 > 0）──
    try:
        rc_m3ok, _, _ = _run_subprocess(["git", "--no-pager", "rev-parse", "m3-ok"])
        if rc_m3ok == 0:
            cmd = ["git", "--no-pager", "diff", "--numstat", "m2-ok..m3-ok", "--", "tools/verify_v11.py"]
        else:
            cmd = ["git", "--no-pager", "diff", "--numstat", "m2-ok", "--", "tools/verify_v11.py"]
        rc, stdout, _ = _run_subprocess(cmd)
        if rc != 0:
            raise RuntimeError(f"git diff 失败; rc={rc}")
        parts = stdout.strip().split()
        additions = int(parts[0]) if len(parts) >= 2 and parts[0].isdigit() else -1
        deletions = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else -1
        passed = deletions == 0 and additions > 0
        _add_item(items, "A3-11", "verify_v11_only_additions_m2_base",
                  {"deletions": 0, "additions_gt_0": True},
                  {"raw": stdout, "deletions": deletions, "additions": additions},
                  passed)
    except Exception as exc:
        _append_exception_item(items, "A3-11", "verify_v11_only_additions_m2_base",
                               {"deletions": 0, "additions_gt_0": True}, exc)

    # ── A3-12：旧路径 step1 数值未变（需要 --state-file）──
    try:
        if not args.state_file:
            raise RuntimeError("未提供 --state-file，A3-12 判 fail（§5 任务要求）")
        step1_state: dict[str, Any] | None = None
        for sf in args.state_file:
            try:
                p = Path(sf)
                if not p.is_absolute():
                    p = Path.cwd() / p
                if p.exists():
                    candidate = _read_json(p)
                    if isinstance(candidate.get("steps"), dict) and "step_1" in candidate["steps"]:
                        step1_state = candidate
                        break
            except Exception:
                pass
        if step1_state is None:
            raise RuntimeError(f"在 state_files={args.state_file!r} 中未找到含 step_1 的有效状态文件")
        step1_details = step1_state["steps"]["step_1"]["details"]
        actual_sc = step1_details.get("sample_count")
        actual_dr = step1_details.get("datebar_rows")
        smoke_bl = baseline.get("smoke", {})
        expected_sc = smoke_bl.get("step1_sample_count")
        expected_dr = smoke_bl.get("step1_datebar_rows")
        sc_ok = actual_sc == expected_sc
        dr_ok = actual_dr == expected_dr
        passed = sc_ok and dr_ok
        _add_item(items, "A3-12", "step1_values_unchanged_from_baseline",
                  {"step1_sample_count": expected_sc, "step1_datebar_rows": expected_dr},
                  {"step1_sample_count": actual_sc, "step1_datebar_rows": actual_dr},
                  passed,
                  detail=(f"sample_count {actual_sc}!={expected_sc}; " if not sc_ok else "")
                         + (f"datebar_rows {actual_dr}!={expected_dr}" if not dr_ok else ""))
    except Exception as exc:
        _append_exception_item(items, "A3-12", "step1_values_unchanged_from_baseline",
                               {"step1_sample_count": baseline.get("smoke", {}).get("step1_sample_count"),
                                "step1_datebar_rows": baseline.get("smoke", {}).get("step1_datebar_rows")}, exc)


def _run_m4_checks(args: argparse.Namespace, baseline: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """A4-01…A4-19：step1 双模式 + runner 签名 + L1/L2/L3 回归（§6.3 / §6.4）。"""

    # ── 预处理：加载全部 state 文件 ──────────────────────────────────────────
    loaded_states: list[dict[str, Any]] = []
    for sf in (args.state_file or []):
        p = Path(sf)
        if not p.is_absolute():
            p = Path.cwd() / p
        if p.exists():
            try:
                s = _read_json(p)
                s["_file"] = str(p)
                loaded_states.append(s)
            except Exception:
                pass

    # 计算 ver_old（老路径 experiment_version）
    ver_old: str | None = None
    try:
        ver_old = _build_experiment_version(args.config)
    except Exception:
        pass

    def _get_details(state: dict[str, Any], step_key: str) -> dict[str, Any]:
        return state.get("steps", {}).get(step_key, {}).get("details", {}) or {}

    def _find_state(*, mode: str | None = None, needs_step: str | None = None,
                    input_mode: str | None = None, old_path_only: bool = False) -> dict[str, Any] | None:
        for s in loaded_states:
            if mode is not None and s.get("mode") != mode:
                continue
            if old_path_only and ver_old is not None and s.get("experiment_version") != ver_old:
                continue
            if needs_step and needs_step not in s.get("steps", {}):
                continue
            if input_mode is not None:
                d1 = _get_details(s, "step_1")
                if d1.get("input_mode") != input_mode:
                    continue
            return s
        return None

    l3_state = _find_state(mode="smoke", needs_step="step_6", old_path_only=True)
    l1_state = _find_state(mode="full", needs_step="step_1", old_path_only=True)
    new_smoke_state = _find_state(mode="smoke", needs_step="step_6", input_mode="samples")

    smoke_bl = baseline.get("smoke", {})
    step1_bl = baseline.get("step1", {})

    # ── A4-01：L3 计数类字段与 baseline.smoke.* 逐值相等 ─────────────────────
    try:
        if l3_state is None:
            raise RuntimeError("未找到 L3 smoke state（需要旧路径 smoke 全链 state）")
        d1 = _get_details(l3_state, "step_1")
        d2 = _get_details(l3_state, "step_2")
        d3 = _get_details(l3_state, "step_3")
        d4 = _get_details(l3_state, "step_4")
        d5 = _get_details(l3_state, "step_5")
        d6 = _get_details(l3_state, "step_6")
        count_checks = {
            "step1_sample_count": (smoke_bl.get("step1_sample_count"), d1.get("sample_count")),
            "step1_datebar_rows": (smoke_bl.get("step1_datebar_rows"), d1.get("datebar_rows")),
            "step2_input_samples": (smoke_bl.get("step2_input_samples"), d2.get("input_samples")),
            "step2_valid_samples": (smoke_bl.get("step2_valid_samples"), d2.get("valid_samples")),
            "step2_slice_rows": (smoke_bl.get("step2_slice_rows"), d2.get("slice_rows")),
            "step3_rows": (smoke_bl.get("step3_rows"), d3.get("rows")),
            "step3_nan_filled": (smoke_bl.get("step3_nan_filled"), d3.get("nan_filled")),
            "step4_columns": (smoke_bl.get("step4_columns"), d4.get("columns")),
            "step4_selected_features": (smoke_bl.get("step4_selected_features"), d4.get("selected_features")),
            "step5_train_rows": (smoke_bl.get("step5", {}).get("train_rows"), d5.get("train_rows")),
            "step5_val_rows": (smoke_bl.get("step5", {}).get("val_rows"), d5.get("val_rows")),
            "step5_test_rows": (smoke_bl.get("step5", {}).get("test_rows"), d5.get("test_rows")),
            "step6_best_iteration": (smoke_bl.get("step6", {}).get("best_iteration"), d6.get("best_iteration")),
        }
        mismatches = {k: (exp, act) for k, (exp, act) in count_checks.items() if exp != act}
        passed = len(mismatches) == 0
        _add_item(items, "A4-01", "l3_count_fields_match_baseline",
                  {k: v[0] for k, v in count_checks.items()},
                  {k: v[1] for k, v in count_checks.items()},
                  passed,
                  detail=str(mismatches) if mismatches else "")
    except Exception as exc:
        _append_exception_item(items, "A4-01", "l3_count_fields_match_baseline",
                               {"baseline": "smoke.*_count_fields"}, exc)

    # ── A4-02：L3 浮点类字段与 baseline 逐值相等（1e-12 相对） ───────────────
    try:
        if l3_state is None:
            raise RuntimeError("未找到 L3 smoke state")
        d6 = _get_details(l3_state, "step_6")
        float_checks = {
            "step6_mae": (smoke_bl.get("step6", {}).get("mae"), d6.get("mae")),
            "step6_spearman": (smoke_bl.get("step6", {}).get("spearman"), d6.get("spearman")),
            "step6_pearson": (smoke_bl.get("step6", {}).get("pearson"), d6.get("pearson")),
        }
        mismatches_f = {
            k: (exp, act) for k, (exp, act) in float_checks.items()
            if not (_is_number(exp) and _is_number(act) and _float_close(float(act), float(exp)))
        }
        passed = len(mismatches_f) == 0
        _add_item(items, "A4-02", "l3_float_fields_match_baseline_1e12",
                  {k: v[0] for k, v in float_checks.items()},
                  {k: v[1] for k, v in float_checks.items()},
                  passed,
                  detail=str(mismatches_f) if mismatches_f else "")
    except Exception as exc:
        _append_exception_item(items, "A4-02", "l3_float_fields_match_baseline_1e12",
                               {"baseline": "smoke.step6.{mae,spearman,pearson}"}, exc)

    # ── A4-03：step1.sample_count == baseline ─────────────────────────────────
    try:
        # 优先用 L1 full state；L1 不存在则退而用 L3 state
        src_state = l1_state if l1_state is not None else l3_state
        if src_state is None:
            raise RuntimeError("未找到含 step_1 的 state")
        d1 = _get_details(src_state, "step_1")
        actual_sc = d1.get("sample_count")
        expected_sc = smoke_bl.get("step1_sample_count")
        passed = actual_sc == expected_sc
        _add_item(items, "A4-03", "step1_sample_count_matches_baseline",
                  {"sample_count": expected_sc}, {"sample_count": actual_sc},
                  passed, detail="" if passed else f"{actual_sc} != {expected_sc}")
    except Exception as exc:
        _append_exception_item(items, "A4-03", "step1_sample_count_matches_baseline",
                               {"sample_count": smoke_bl.get("step1_sample_count")}, exc)

    # ── A4-04：step1.datebar_rows == baseline ─────────────────────────────────
    try:
        src_state = l1_state if l1_state is not None else l3_state
        if src_state is None:
            raise RuntimeError("未找到含 step_1 的 state")
        d1 = _get_details(src_state, "step_1")
        actual_dr = d1.get("datebar_rows")
        expected_dr = smoke_bl.get("step1_datebar_rows")
        passed = actual_dr == expected_dr
        _add_item(items, "A4-04", "step1_datebar_rows_matches_baseline",
                  {"datebar_rows": expected_dr}, {"datebar_rows": actual_dr},
                  passed, detail="" if passed else f"{actual_dr} != {expected_dr}")
    except Exception as exc:
        _append_exception_item(items, "A4-04", "step1_datebar_rows_matches_baseline",
                               {"datebar_rows": smoke_bl.get("step1_datebar_rows")}, exc)

    # ── A4-05：step1.target_stats 8 项 == baseline（1e-12） ───────────────────
    try:
        src_state = l1_state if l1_state is not None else l3_state
        if src_state is None:
            raise RuntimeError("未找到含 step_1 的 state")
        d1 = _get_details(src_state, "step_1")
        actual_ts = d1.get("target_stats", {})
        expected_ts = step1_bl.get("target_stats", {})
        if not isinstance(actual_ts, dict) or not isinstance(expected_ts, dict):
            raise RuntimeError(f"target_stats 类型错误: actual={type(actual_ts)}, expected={type(expected_ts)}")
        ok, diffs = _deep_equal(expected_ts, actual_ts)
        _add_item(items, "A4-05", "step1_target_stats_matches_baseline_1e12",
                  expected_ts, actual_ts, ok,
                  detail="; ".join(diffs[:3]) if diffs else "")
    except Exception as exc:
        _append_exception_item(items, "A4-05", "step1_target_stats_matches_baseline_1e12",
                               {"target_stats": "8_describe_keys"}, exc)

    # ── A4-06：samples 模式 6 个 checks 全 True，键名与 baseline 同名 ─────────
    try:
        if new_smoke_state is None:
            raise RuntimeError("未找到 samples 模式 smoke state")
        # checks 存储在 details.checks 中（不是 step_1.checks）
        d1_a06 = _get_details(new_smoke_state, "step_1")
        actual_checks = d1_a06.get("checks", {}) or {}
        expected_keys = {
            "holding_exists", "holding_columns", "holding_target_non_null",
            "datebar_exists", "datebar_columns", "datebar_sorted",
        }
        expected_keys_match = set(actual_checks.keys()) == expected_keys
        all_true = all(bool(v) for v in actual_checks.values()) if actual_checks else False
        passed = expected_keys_match and all_true
        _add_item(items, "A4-06", "samples_mode_step1_checks_all_true",
                  {"expected_keys": sorted(expected_keys), "all_values": True},
                  {"keys": sorted(actual_checks.keys()), "values": actual_checks,
                   "keys_match": expected_keys_match, "all_true": all_true},
                  passed)
    except Exception as exc:
        _append_exception_item(items, "A4-06", "samples_mode_step1_checks_all_true",
                               {"expected_keys": "6_check_keys", "all_values": True}, exc)

    # ── A4-07：samples 模式 details 16 键齐全 ────────────────────────────────
    try:
        if new_smoke_state is None:
            raise RuntimeError("未找到 samples 模式 smoke state")
        d1 = _get_details(new_smoke_state, "step_1")
        required_16 = {
            "input_mode", "coupling_mode", "samples_rows", "sample_count",
            "dropped_no_target", "target_coverage", "signal_date_min", "signal_date_max",
            "target_stats", "datebar_rows", "datebar_fingerprint", "samples_id",
            "samples_set_hash", "label_name", "label_hash", "label_non_null_ratio",
        }
        missing = sorted(required_16 - set(d1.keys()))
        passed = len(missing) == 0
        _add_item(items, "A4-07", "samples_mode_details_16_keys_present",
                  {"required_keys": sorted(required_16)},
                  {"present_keys": sorted(d1.keys()), "missing": missing},
                  passed, detail=f"缺失: {missing}" if missing else "")
    except Exception as exc:
        _append_exception_item(items, "A4-07", "samples_mode_details_16_keys_present",
                               {"required_keys": "16_detail_keys"}, exc)

    # ── A4-08：dropped_no_target > 0 ─────────────────────────────────────────
    try:
        if new_smoke_state is None:
            raise RuntimeError("未找到 samples 模式 smoke state")
        d1 = _get_details(new_smoke_state, "step_1")
        actual_drop = d1.get("dropped_no_target")
        passed = isinstance(actual_drop, int) and actual_drop > 0
        _add_item(items, "A4-08", "dropped_no_target_gt_0",
                  {"dropped_no_target": ">0"},
                  {"dropped_no_target": actual_drop},
                  passed, detail="" if passed else f"dropped_no_target={actual_drop} 不满足 > 0")
    except Exception as exc:
        _append_exception_item(items, "A4-08", "dropped_no_target_gt_0",
                               {"dropped_no_target": ">0"}, exc)

    # ── A4-09：守恒恒等式（按 state.mode 选口径 B：smoke）─────────────────────
    try:
        if new_smoke_state is None:
            raise RuntimeError("未找到 samples 模式 smoke state")
        st_mode = new_smoke_state.get("mode")
        if st_mode is None:
            raise RuntimeError(f"state 缺少 mode 字段: {new_smoke_state.get('_file')}")
        d2 = _get_details(new_smoke_state, "step_2")
        d4 = _get_details(new_smoke_state, "step_4")
        for field, dct in [("input_samples", d2), ("dropped_samples", d2), ("invalid_targets_dropped", d4), ("samples", d4)]:
            if field not in dct:
                raise RuntimeError(f"守恒恒等式缺少字段 {field}")
        if st_mode == "smoke":
            # 口径 B：lhs = d2.input_samples - d2.dropped_samples - d4.invalid_targets_dropped
            lhs = d2["input_samples"] - d2["dropped_samples"] - d4["invalid_targets_dropped"]
            rhs = d4["samples"]
            passed = lhs == rhs
            detail_str = f"口径B smoke: {d2['input_samples']}-{d2['dropped_samples']}-{d4['invalid_targets_dropped']}={lhs} == {rhs}"
        else:
            # 口径 A：full — 含前置断言
            d1 = _get_details(new_smoke_state, "step_1")
            d1_samples_rows = d1.get("samples_rows")
            d1_dropped = d1.get("dropped_no_target")
            pre_ok = d2.get("input_samples") == (d1_samples_rows - d1_dropped) if d1_samples_rows is not None and d1_dropped is not None else False
            lhs = d2["input_samples"] - d2["dropped_samples"] - d4["invalid_targets_dropped"]
            rhs = d4["samples"]
            passed = pre_ok and (lhs == rhs)
            detail_str = (
                f"口径A full: pre_ok={pre_ok}({d2.get('input_samples')}=={d1_samples_rows}-{d1_dropped}); "
                f"{d2['input_samples']}-{d2['dropped_samples']}-{d4['invalid_targets_dropped']}={lhs} == {rhs}"
            )
        _add_item(items, "A4-09", "conservation_identity_holds",
                  {"lhs": "d2.input-d2.dropped-d4.invalid", "rhs": "d4.samples"},
                  {"mode": st_mode, "lhs": lhs, "rhs": rhs},
                  passed, detail=detail_str)
    except Exception as exc:
        _append_exception_item(items, "A4-09", "conservation_identity_holds",
                               {"lhs": "conservation_equation"}, exc)

    # ── A4-10：details.input_mode / samples_id / label_hash == 配置推导值 ─────
    try:
        if new_smoke_state is None:
            raise RuntimeError("未找到 samples 模式 smoke state")
        # 读新路径配置
        cfg_path = args.config
        cfg_dir = Path(cfg_path).parent
        samples_cfg_path = str(cfg_dir / (Path(cfg_path).stem + "_samples" + Path(cfg_path).suffix))

        code = (
            "import json,sys,hashlib;"
            "from pipeline.config import load_params;"
            "from data_prep.ids import canonical_json, derive_samples_id, derive_datebar_fingerprint;"
            "params=load_params(sys.argv[1]);"
            "dp=params.data_prep;"
            "sc=dp.samples; uv=dp.universe;"
            "method=sc.method;"
            "universe_for_hash={'exclude_prefixes':list(uv.exclude_prefixes),'min_history_days':int(uv.min_history_days)};"
            "h_input={'method':method,'params':dict(sc.params)if sc.params else {},"
            "'universe':universe_for_hash,"
            "'date_range':{'start':dp.date_range_start,'end':dp.date_range_end},"
            "'min_history_days':int(uv.min_history_days),'max_samples':sc.max_samples};"
            "ph=hashlib.sha256(canonical_json(h_input).encode()).hexdigest()[:8];"
            "fp=derive_datebar_fingerprint(params.data.datebar_file);"
            "sid=derive_samples_id(method,ph,fp);"
            "src=params.target.source;"
            "lname='target_holding_return' if src=='holding_return' else params.target.label;"
            "import pathlib;"
            "idx_path=pathlib.Path(params.data.labels_dir)/sid/'_index.json';"
            "idx=json.loads(idx_path.read_text(encoding='utf-8')) if idx_path.exists() else {};"
            "lhash=str(idx.get(lname,{}).get('params_hash',''));"
            "print(json.dumps({'input_mode':params.input.mode,'samples_id':sid,'label_hash':lhash}))"
        )
        rc, stdout, stderr = _run_subprocess([sys.executable, "-c", code, samples_cfg_path])
        if rc != 0:
            raise RuntimeError(f"配置推导失败: rc={rc}; stderr={stderr[:300]}")
        expected_config = json.loads(stdout)

        d1 = _get_details(new_smoke_state, "step_1")
        actual_map = {
            "input_mode": d1.get("input_mode"),
            "samples_id": d1.get("samples_id"),
            "label_hash": d1.get("label_hash"),
        }
        expected_map = {
            "input_mode": expected_config.get("input_mode"),
            "samples_id": expected_config.get("samples_id"),
            "label_hash": expected_config.get("label_hash"),
        }
        mismatches_10 = {k: (expected_map[k], actual_map[k]) for k in expected_map if expected_map[k] != actual_map[k]}
        passed = len(mismatches_10) == 0
        _add_item(items, "A4-10", "step1_details_match_config_derived_values",
                  expected_map, actual_map, passed,
                  detail=str(mismatches_10) if mismatches_10 else "")
    except Exception as exc:
        _append_exception_item(items, "A4-10", "step1_details_match_config_derived_values",
                               {"input_mode": "samples", "samples_id": "derived", "label_hash": "derived"}, exc)

    # ── A4-11：两模式 holding_clean 文件名不变、必需 4 列齐全 ────────────────
    try:
        required_4 = {"sample_id", "idxs", "signal_date", "target"}
        failures: list[str] = []
        # 从所有 state 中收集 step_1 的 output_files 来定位 holding_clean.parquet
        checked: list[str] = []
        for s in loaded_states:
            st1_files = s.get("steps", {}).get("step_1", {}).get("output_files", []) or []
            for f in st1_files:
                if "holding_clean" in f:
                    pq = Path.cwd() / f
                    checked.append(str(pq))
                    if not pq.exists():
                        failures.append(f"holding_clean.parquet 不存在: {pq}")
                        continue
                    try:
                        df = pd.read_parquet(pq)
                        missing_cols = sorted(required_4 - set(df.columns))
                        if missing_cols:
                            failures.append(f"{pq}: 缺少必需列 {missing_cols}")
                    except Exception as read_exc:
                        failures.append(f"{pq}: 读取失败 {read_exc}")
        if not checked:
            failures.append("未在任何 state 文件中找到 holding_clean.parquet 路径")
        passed = len(failures) == 0
        _add_item(items, "A4-11", "both_modes_holding_clean_filename_and_columns",
                  {"filename": "holding_clean.parquet", "required_columns": sorted(required_4)},
                  {"checked": checked, "failures": failures},
                  passed, detail="; ".join(failures[:3]) if failures else "")
    except Exception as exc:
        _append_exception_item(items, "A4-11", "both_modes_holding_clean_filename_and_columns",
                               {"filename": "holding_clean.parquet"}, exc)

    # ── A4-12：新路径 state step_1…step_6 全 completed 且 checks_passed 全 True ─
    try:
        if new_smoke_state is None:
            raise RuntimeError("未找到 samples 模式 smoke state")
        steps = new_smoke_state.get("steps", {})
        required_steps = [f"step_{i}" for i in range(1, 7)]
        step_results = {}
        for sk in required_steps:
            sv = steps.get(sk, {})
            step_results[sk] = {"status": sv.get("status"), "checks_passed": sv.get("checks_passed")}
        all_ok = all(
            v["status"] == "completed" and v["checks_passed"] is True
            for v in step_results.values()
        )
        _add_item(items, "A4-12", "new_path_state_all_steps_completed",
                  {"all_steps": "completed+checks_passed=True"},
                  step_results, all_ok,
                  detail="" if all_ok else str({k: v for k, v in step_results.items() if not (v["status"] == "completed" and v["checks_passed"] is True)}))
    except Exception as exc:
        _append_exception_item(items, "A4-12", "new_path_state_all_steps_completed",
                               {"all_steps": "completed+checks_passed=True"}, exc)

    # ── A4-13：step2 details 含 dropped_samples，invalid_reasons 含 4 个新键 ──
    try:
        # 用新路径 smoke state（步骤 2 结构）
        src = new_smoke_state if new_smoke_state is not None else l3_state
        if src is None:
            raise RuntimeError("未找到含 step_2 的 state")
        d2 = _get_details(src, "step_2")
        has_dropped = "dropped_samples" in d2
        ir = d2.get("invalid_reasons", {})
        required_ir = {"unknown_symbol", "date_not_in_symbol_range", "paused_signal_date", "insufficient_history"}
        actual_ir_keys = set(ir.keys())
        ir_ok = actual_ir_keys == required_ir
        passed = has_dropped and ir_ok
        _add_item(items, "A4-13", "step2_has_dropped_samples_and_4_invalid_reason_keys",
                  {"dropped_samples": "present", "invalid_reasons_keys": sorted(required_ir)},
                  {"has_dropped_samples": has_dropped, "invalid_reasons_keys": sorted(actual_ir_keys)},
                  passed, detail="" if passed else f"has_dropped={has_dropped}, ir_ok={ir_ok}, extra={sorted(actual_ir_keys-required_ir)}, missing={sorted(required_ir-actual_ir_keys)}")
    except Exception as exc:
        _append_exception_item(items, "A4-13", "step2_has_dropped_samples_and_4_invalid_reason_keys",
                               {"invalid_reasons": "4_new_keys"}, exc)

    # ── A4-14：两模式 holding_clean 的 idxs 为 str、signal_date 为整型、sample_id 为 int64 ─
    try:
        dtype_failures: list[str] = []
        # 从所有 state 中收集 holding_clean.parquet 路径
        for s in loaded_states:
            st1_files = s.get("steps", {}).get("step_1", {}).get("output_files", []) or []
            for f in st1_files:
                if "holding_clean" in f:
                    pq = Path.cwd() / f
                    if not pq.exists():
                        continue
                    try:
                        df = pd.read_parquet(pq)
                        if str(df["idxs"].dtype) not in ("object", "string", "StringDtype"):
                            dtype_failures.append(f"{pq}: idxs dtype={df['idxs'].dtype} 应为 str/object")
                        if not pd.api.types.is_integer_dtype(df["signal_date"]):
                            dtype_failures.append(f"{pq}: signal_date dtype={df['signal_date'].dtype} 应为整型")
                        if str(df["sample_id"].dtype) != "int64":
                            dtype_failures.append(f"{pq}: sample_id dtype={df['sample_id'].dtype} 应为 int64")
                    except Exception as read_exc:
                        dtype_failures.append(f"{pq}: 读取失败 {read_exc}")
        passed = len(dtype_failures) == 0
        _add_item(items, "A4-14", "holding_clean_column_dtypes_correct",
                  {"idxs": "str/object", "signal_date": "int", "sample_id": "int64"},
                  {"failures": dtype_failures},
                  passed, detail="; ".join(dtype_failures[:3]) if dtype_failures else "")
    except Exception as exc:
        _append_exception_item(items, "A4-14", "holding_clean_column_dtypes_correct",
                               {"idxs": "str", "signal_date": "int", "sample_id": "int64"}, exc)

    # ── A4-15：processed/datebar.parquet 恒 9 列（两模式）────────────────────
    try:
        required_9 = ["idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused"]
        col_failures: list[str] = []
        # 从所有 state 中收集 datebar.parquet 路径
        for s in loaded_states:
            st1_files = s.get("steps", {}).get("step_1", {}).get("output_files", []) or []
            for f in st1_files:
                if f.endswith("datebar.parquet"):
                    pq = Path.cwd() / f
                    if not pq.exists():
                        continue
                    try:
                        df = pd.read_parquet(pq)
                        actual_cols = list(df.columns)
                        if actual_cols != required_9:
                            col_failures.append(f"{pq}: columns={actual_cols}")
                    except Exception as read_exc:
                        col_failures.append(f"{pq}: 读取失败 {read_exc}")
        passed = len(col_failures) == 0
        _add_item(items, "A4-15", "datebar_parquet_always_9_columns",
                  {"columns": required_9},
                  {"failures": col_failures},
                  passed, detail="; ".join(col_failures[:3]) if col_failures else "")
    except Exception as exc:
        _append_exception_item(items, "A4-15", "datebar_parquet_always_9_columns",
                               {"columns": "9_cols"}, exc)

    # ── A4-16：docs/14 记录了 datebar 按值比对结论 ────────────────────────────
    try:
        doc14 = Path("docs/14_datebar_probe.md")
        if not doc14.exists():
            raise FileNotFoundError(f"docs/14_datebar_probe.md 不存在")
        content = doc14.read_text(encoding="utf-8")
        keywords = ["data_prep/datebar", "_clean_datebar", "按值", "逐值相等"]
        hits_16 = [kw for kw in keywords if kw in content]
        passed = len(hits_16) >= 1
        _add_item(items, "A4-16", "docs14_records_datebar_comparison_conclusion",
                  {"min_hits": 1, "keywords": keywords},
                  {"hits": hits_16},
                  passed, detail="" if passed else "docs/14 未找到 datebar 比对结论")
    except Exception as exc:
        _append_exception_item(items, "A4-16", "docs14_records_datebar_comparison_conclusion",
                               {"min_hits": 1}, exc)

    # ── A4-17：samples 模式 holding_clean 按 sample_id 升序 ──────────────────
    try:
        if new_smoke_state is None:
            raise RuntimeError("未找到 samples 模式 smoke state")
        st1_files = new_smoke_state.get("steps", {}).get("step_1", {}).get("output_files", []) or []
        hc_path_str = next((f for f in st1_files if "holding_clean" in f), None)
        if not hc_path_str:
            raise RuntimeError("未在 state 的 output_files 中找到 holding_clean.parquet")
        hc_path = Path.cwd() / hc_path_str
        if not hc_path.exists():
            raise FileNotFoundError(f"holding_clean.parquet 不存在: {hc_path}")
        df_hc = pd.read_parquet(hc_path)
        is_ascending = bool((df_hc["sample_id"].diff().dropna() > 0).all())
        _add_item(items, "A4-17", "samples_mode_holding_clean_ascending_sample_id",
                  {"sample_id_order": "strictly_ascending"},
                  {"is_ascending": is_ascending, "file": hc_path_str},
                  is_ascending, detail="" if is_ascending else "sample_id 未严格升序")
    except Exception as exc:
        _append_exception_item(items, "A4-17", "samples_mode_holding_clean_ascending_sample_id",
                               {"sample_id_order": "ascending"}, exc)

    # ── A4-18：data_prep/datebar.py 与 _clean_datebar 的 9 列按值逐值相等 ─────
    try:
        # 获取 datebar 文件路径（通过子进程避免循环导入）
        code_dp = (
            "import sys;"
            "from pipeline.config import load_params;"
            "p=load_params(sys.argv[1]);"
            "print(p.data.datebar_file)"
        )
        rc_dp, datebar_file, _ = _run_subprocess([sys.executable, "-c", code_dp, args.config])
        if rc_dp != 0:
            raise RuntimeError(f"获取 datebar 路径失败: {datebar_file}")
        datebar_file = datebar_file.strip()

        # 获取 step1 输出的 datebar.parquet 路径（来自旧路径 state）
        datebar_pq_str = None
        for s in loaded_states:
            if ver_old is None or s.get("experiment_version") == ver_old:
                for f in s.get("steps", {}).get("step_1", {}).get("output_files", []) or []:
                    if f.endswith("datebar.parquet"):
                        datebar_pq_str = f
                        break
            if datebar_pq_str:
                break
        if datebar_pq_str is None:
            raise RuntimeError("未找到 datebar.parquet 路径（state output_files）")

        datebar_pq_path = Path.cwd() / datebar_pq_str
        if not datebar_pq_path.exists():
            raise FileNotFoundError(f"datebar.parquet 不存在: {datebar_pq_path}")

        # 直接在本进程比对（verify_v11 允许 import data_prep；需确保 repo root 在 sys.path）
        import sys as _sys  # noqa: PLC0415
        _repo_root = str(Path.cwd())
        if _repo_root not in _sys.path:
            _sys.path.insert(0, _repo_root)
        from data_prep.datebar import load_datebar  # noqa: PLC0415
        nine = ["idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused"]
        df_dp, _ = load_datebar(datebar_file)
        df_dp = df_dp[nine].copy()
        df_p1 = pd.read_parquet(datebar_pq_path)
        shape_ok = df_dp.shape == df_p1.shape
        col_ok = list(df_dp.columns) == list(df_p1.columns)
        if not shape_ok or not col_ok:
            raise RuntimeError(f"shape/cols mismatch: {df_dp.shape} vs {df_p1.shape}, cols: {col_ok}")
        diffs_18: list[str] = []
        for c in nine:
            if c == "idxs":
                eq = (df_dp[c].values == df_p1[c].values).all()
            elif c == "paused":
                eq = (df_dp[c].astype(bool).values == df_p1[c].astype(bool).values).all()
            elif c == "idxd":
                eq = (df_dp[c].astype("int64").values == df_p1[c].astype("int64").values).all()
            else:
                eq = (df_dp[c].astype("float64").values == df_p1[c].astype("float64").values).all()
            if not eq:
                diffs_18.append(c)
        passed = len(diffs_18) == 0
        _add_item(items, "A4-18", "data_prep_datebar_vs_clean_datebar_equal",
                  {"columns_equal": True, "values_equal": True},
                  {"datebar_file": datebar_file, "datebar_pq": str(datebar_pq_path),
                   "shape": str(df_dp.shape), "diff_cols": diffs_18},
                  passed, detail=f"不一致列: {diffs_18}" if diffs_18 else "")
    except Exception as exc:
        _append_exception_item(items, "A4-18", "data_prep_datebar_vs_clean_datebar_equal",
                               {"values_equal": True}, exc)

    # ── A4-19：verify_v11 只增不改（m3-ok 基线，删除列 == 0，新增列 > 0）────
    try:
        rc_m4ok, _, _ = _run_subprocess(["git", "--no-pager", "rev-parse", "m4-ok"])
        if rc_m4ok == 0:
            cmd = ["git", "--no-pager", "diff", "--numstat", "m3-ok..m4-ok", "--", "tools/verify_v11.py"]
        else:
            cmd = ["git", "--no-pager", "diff", "--numstat", "m3-ok", "--", "tools/verify_v11.py"]
        rc_19, stdout_19, _ = _run_subprocess(cmd)
        if rc_19 != 0:
            raise RuntimeError(f"git diff 失败; rc={rc_19}")
        parts = stdout_19.strip().split()
        additions_19 = int(parts[0]) if len(parts) >= 2 and parts[0].isdigit() else -1
        deletions_19 = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else -1
        passed = deletions_19 == 0 and additions_19 > 0
        _add_item(items, "A4-19", "verify_v11_only_additions_m3_base",
                  {"deletions": 0, "additions_gt_0": True},
                  {"raw": stdout_19, "deletions": deletions_19, "additions": additions_19},
                  passed)
    except Exception as exc:
        _append_exception_item(items, "A4-19", "verify_v11_only_additions_m3_base",
                               {"deletions": 0, "additions_gt_0": True}, exc)


MILESTONE_RUNNERS: dict[str, Any] = {
    "M0": _run_m0_checks,
    "M1": _run_m1_checks,
    "M2": _run_m2_checks,
    "M3": _run_m3_checks,
    "M4": _run_m4_checks,
    # 后续里程碑在此表中各插入一行，不得修改已有条目
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="v1.1 里程碑验收脚本。")
    parser.add_argument("--milestone", required=True, choices=MILESTONES)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--baseline-git-ref", default="")
    parser.add_argument("--config", default="configs/kernel_v11/check/refactor_check.yaml")
    parser.add_argument("--state-file", action="append", default=[])
    parser.add_argument("--samples-id", action="append", default=[], help="M1 起必填：待校验的样本集 ID，可重复")
    parser.add_argument("--output-dir", action="append", default=[])
    parser.add_argument("--report", default="verify_report.json")
    return parser


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def main() -> int:
    args = _build_parser().parse_args()
    report_path = Path(args.report)
    items: list[dict[str, Any]] = []
    parameter_error = ""
    baseline: dict[str, Any] | None = None

    try:
        baseline_path = Path(args.baseline)
        if not baseline_path.exists():
            parameter_error = f"--baseline 不存在: {args.baseline}"
        else:
            baseline_loaded = _read_json(baseline_path)
            if not isinstance(baseline_loaded, dict):
                parameter_error = "baseline 根节点不是对象"
            else:
                baseline = baseline_loaded
    except Exception as exc:
        parameter_error = f"--baseline 读取失败: {type(exc).__name__}: {exc}"

    if not parameter_error and args.milestone == "M0" and not args.baseline_git_ref.strip():
        parameter_error = "M0 需要 --baseline-git-ref"

    if not parameter_error and args.milestone not in MILESTONE_RUNNERS:
        parameter_error = (
            f"{args.milestone} 尚未实现"
            f"（已实现：{sorted(MILESTONE_RUNNERS)}）"
        )

    if parameter_error:
        _add_item(items, "P-01", "parameter_error", "valid_parameters", {"error": parameter_error}, False)
        exit_code = 2
    else:
        assert baseline is not None
        MILESTONE_RUNNERS[args.milestone](args, baseline, items)
        if len(items) == 0:
            _add_item(items, "A0-00", "non_empty_items_required", "at_least_one_item", {"items": 0}, False)
        failed = [item["id"] for item in items if not item["pass"]]
        exit_code = 0 if len(failed) == 0 else 1

    failed_items = [item["id"] for item in items if not item["pass"]]
    commit_rc, commit_out, _ = _run_subprocess(["git", "--no-pager", "rev-parse", "--short", "HEAD"])
    git_commit = commit_out if commit_rc == 0 else ""

    report = {
        "milestone": args.milestone,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "exit_code": exit_code,
        "items": items,
        "failed": failed_items,
    }
    _write_report(report_path, report)

    print(f"[verify_v11] milestone={args.milestone} exit_code={exit_code} items={len(items)} failed={failed_items}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
