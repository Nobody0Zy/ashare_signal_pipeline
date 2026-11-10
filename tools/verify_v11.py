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


def _normalize_for_compare(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize_for_compare(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_for_compare(v) for v in value]
    return value


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
        baseline_repo_path = baseline_path.as_posix().lstrip("./")
        rc, stdout, stderr = _run_subprocess(
            ["git", "--no-pager", "show", f"{args.baseline_git_ref}:{baseline_repo_path}"]
        )
        if rc != 0:
            raise RuntimeError(f"git show 失败: {stderr or '<empty>'}")
        old_baseline = json.loads(stdout)
        expected_smoke = _normalize_for_compare(old_baseline.get("smoke"))
        actual_smoke = _normalize_for_compare(baseline.get("smoke"))
        passed = expected_smoke == actual_smoke
        _add_item(items, "A0-15", "baseline_smoke_anti_tamper", expected_smoke, actual_smoke, passed)
    except Exception as exc:
        _append_exception_item(items, "A0-15", "baseline_smoke_anti_tamper", {"baseline_git_ref": args.baseline_git_ref}, exc)

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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="v1.1 里程碑验收脚本。")
    parser.add_argument("--milestone", required=True, choices=MILESTONES)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--baseline-git-ref", default="")
    parser.add_argument("--config", default="configs/kernel_v11/check/refactor_check.yaml")
    parser.add_argument("--state-file", action="append", default=[])
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

    if not parameter_error and args.milestone != "M0":
        parameter_error = f"{args.milestone} 尚未在 M0 阶段实现"

    if parameter_error:
        _add_item(items, "P-01", "parameter_error", "valid_parameters", {"error": parameter_error}, False)
        exit_code = 2
    else:
        assert baseline is not None
        _run_m0_checks(args, baseline, items)
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
