"""tools/compare_runs.py — compare two report_summary.json files (design §10).

Does NOT go into any report. Output is a one-off comparison file.

Usage:
  python tools/compare_runs.py \\
      --left  outputs/<ev_a>/analysis/report_summary.json \\
      --right outputs/<ev_b>/analysis/report_summary.json \\
      --output outputs/_compare/<a>__vs__<b>.md

Exit codes: 0 = success (comparable or not), 1 = input unreadable, 2 = arg error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

_IDENTITY_KEYS = [
    "input_identity.input_mode",
    "input_identity.coupling_mode",
    "input_identity.samples_id",
    "input_identity.label_name",
    "fingerprints.samples_set",
    "fingerprints.label_params",
    "fingerprints.datebar",
]

_DELTA_FIELDS: List[Tuple[str, str, str]] = [
    # (json_path, label, fmt)
    ("pipeline_steps.step_1.details.sample_count", "step1.sample_count", "int"),
    ("pipeline_steps.step_2.details.valid_samples", "step2.valid_samples", "int"),
    ("pipeline_steps.step_4.details.samples", "step4.samples", "int"),
    ("pipeline_steps.step_5.details.train_rows", "step5.train_rows", "int"),
    ("pipeline_steps.step_5.details.val_rows", "step5.val_rows", "int"),
    ("pipeline_steps.step_5.details.test_rows", "step5.test_rows", "int"),
    ("pipeline_steps.step_6.details.best_iteration", "step6.best_iteration", "int"),
    ("pipeline_steps.step_6.details.mae", "step6.mae", "float"),
    ("pipeline_steps.step_6.details.spearman", "step6.spearman", "float"),
    ("pipeline_steps.step_6.details.pearson", "step6.pearson", "float"),
]

_COST_FIELDS: List[Tuple[str, str, str]] = [
    ("smoke_vs_full.total_seconds.full", "total_seconds", "float"),
]


def _get(d: Dict, path: str) -> Any:
    obj = d
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _fmt_delta(v: Any, fmt: str) -> str:
    if v is None:
        return "—"
    try:
        if fmt == "int":
            return f"{int(v):+,}"
        return f"{float(v):+.6f}"
    except Exception:
        return str(v)


def _fmt_val(v: Any, fmt: str) -> str:
    if v is None:
        return "—"
    try:
        if fmt == "int":
            return f"{int(v):,}"
        return f"{float(v):.6f}"
    except Exception:
        return str(v)


def _check_comparable(left: Dict, right: Dict) -> Tuple[bool, List[str]]:
    diffs = []
    for key in _IDENTITY_KEYS:
        lv = _get(left, key)
        rv = _get(right, key)
        if lv != rv:
            diffs.append(f"  - `{key}`: left={lv!r} vs right={rv!r}")
    return len(diffs) == 0, diffs


def _build_delta_table(left: Dict, right: Dict, fields: List[Tuple[str, str, str]]) -> str:
    rows = ["| 指标 | 左 | 右 | Δ | Δ% |", "|:---|---:|---:|---:|---:|"]
    for path, label, fmt in fields:
        lv = _get(left, path)
        rv = _get(right, path)
        if lv is None and rv is None:
            continue
        delta = None
        delta_pct = None
        if lv is not None and rv is not None:
            try:
                delta = float(rv) - float(lv)
                delta_pct = delta / abs(float(lv)) * 100 if float(lv) != 0 else None
            except Exception:
                pass
        rows.append(
            f"| {label} | {_fmt_val(lv, fmt)} | {_fmt_val(rv, fmt)} | "
            f"{_fmt_delta(delta, fmt) if delta is not None else '—'} | "
            f"{f'{delta_pct:+.2f}%' if delta_pct is not None else '—'} |"
        )
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two report_summary.json files.")
    parser.add_argument("--left", required=True, help="Path to left report_summary.json")
    parser.add_argument("--right", required=True, help="Path to right report_summary.json")
    parser.add_argument("--output", default=None,
                        help="Output markdown path (default: outputs/_compare/<a>__vs__<b>.md)")
    args = parser.parse_args()

    left_path = Path(args.left).resolve()
    right_path = Path(args.right).resolve()

    if not left_path.exists():
        print(f"[compare_runs] ERROR: --left not found: {left_path}", file=sys.stderr)
        return 2
    if not right_path.exists():
        print(f"[compare_runs] ERROR: --right not found: {right_path}", file=sys.stderr)
        return 2

    try:
        with left_path.open(encoding="utf-8") as f:
            left = json.load(f)
    except Exception as e:
        print(f"[compare_runs] ERROR: cannot read left: {e}", file=sys.stderr)
        return 1

    try:
        with right_path.open(encoding="utf-8") as f:
            right = json.load(f)
    except Exception as e:
        print(f"[compare_runs] ERROR: cannot read right: {e}", file=sys.stderr)
        return 1

    # Determine output path
    if args.output:
        out_path = Path(args.output)
    else:
        a_name = left_path.parents[1].name
        b_name = right_path.parents[1].name
        out_path = ROOT / "outputs" / "_compare" / f"{a_name}__vs__{b_name}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    left_ev = left.get("experiment_version", "left")
    right_ev = right.get("experiment_version", "right")
    ga = datetime.now().isoformat(timespec="seconds")

    comparable, diffs = _check_comparable(left, right)

    if comparable:
        delta_section = f"""
## 2. 关键指标 Δ

{_build_delta_table(left, right, _DELTA_FIELDS)}

> 表下注：缺失字段显示 —；Δ% = (右 − 左) / |左| × 100。

## 3. 成本 Δ

{_build_delta_table(left, right, _COST_FIELDS)}
"""
    else:
        delta_section = """
## 2. 关键指标 Δ

> **不可比较**：两次运行的口径不一致（见第 1 节），不输出指标 Δ，以免产生误导性结论。
> 如需对比，请先对齐 samples_id / label_name / input_mode 等关键口径。
"""

    lines = [
        "> 本文件由 tools/compare_runs.py 生成，是一次性对比产物，不属于任何实验报告。",
        "",
        f"# 运行间对比：{left_ev.split('/')[-1]} vs {right_ev.split('/')[-1]}",
        "",
        f"生成时间: {ga}",
        f"- 左：{left_ev}",
        f"- 右：{right_ev}",
        "",
        "## 1. 口径校验",
        "",
        f"**结论：{'可比' if comparable else '不可直接比较'}**",
        "",
    ]
    if diffs:
        lines.append("**差异项**：")
        lines.extend(diffs)
    else:
        lines.append("全部 7 个口径字段完全一致，两次运行可直接比较。")

    lines.append(delta_section)
    content = "\n".join(lines)
    out_path.write_text(content, encoding="utf-8")
    print(f"[compare_runs] written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
