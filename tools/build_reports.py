"""tools/build_reports.py — thin CLI for report rendering (design §2.2).

Usage:
  python tools/build_reports.py --manifest PATH [--types engineering,experiment,executive]
      [--layout {v1,v2}] [--spec PATH] [--dry-run]
  python tools/build_reports.py --experiment-version STR ...

Exit codes: 0 success, 1 render error, 2 argument error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _resolve_summary_path(manifest_path: Path) -> Path:
    return manifest_path.parent.parent / "analysis" / "report_summary.json"


def _find_manifest(root: Path, experiment_version: str) -> Path:
    p = root / "outputs" / experiment_version / "metadata" / "experiment_manifest.json"
    if p.exists():
        return p
    raise FileNotFoundError(f"manifest not found for ev={experiment_version!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render reports from report_summary.json")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", help="Path to experiment_manifest.json")
    group.add_argument("--experiment-version", help="Experiment version string")
    parser.add_argument(
        "--types", default="engineering,experiment,executive",
        help="Comma-separated report types (engineering,experiment,executive,datacard,reference)",
    )
    parser.add_argument("--layout", choices=["v1", "v2"], default="v1",
                        help="v1=reports only; v2=reports+manifest+aliases (M5+)")
    parser.add_argument("--spec", default=None, help="Path to executive spec YAML override")
    parser.add_argument("--dry-run", action="store_true", help="Print paths without writing")
    args = parser.parse_args()

    if args.manifest:
        manifest_path = Path(args.manifest).resolve()
    else:
        try:
            manifest_path = _find_manifest(ROOT, args.experiment_version)
        except FileNotFoundError as e:
            print(f"[build_reports] ERROR: {e}", file=sys.stderr)
            return 2

    if not manifest_path.exists():
        print(f"[build_reports] ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    summary_path = _resolve_summary_path(manifest_path)
    if not summary_path.exists():
        print(f"[build_reports] ERROR: report_summary.json not found at {summary_path}", file=sys.stderr)
        print("[build_reports] Hint: run analyze_experiment_results.py --emit-summary-only first", file=sys.stderr)
        return 1

    types_set = {t.strip() for t in args.types.split(",") if t.strip()}
    out_dir = summary_path.parent
    spec_path = Path(args.spec).resolve() if args.spec else None

    if args.dry_run:
        print(f"[build_reports] DRY RUN: would render {types_set} from {summary_path}")
        print(f"[build_reports] output dir: {out_dir}/reports/")
        return 0

    sys.path.insert(0, str(ROOT))
    from report import render_all
    try:
        paths = render_all(summary_path, out_dir, types=types_set,
                           layout=args.layout, spec_path=spec_path)
    except Exception as e:
        print(f"[build_reports] ERROR during rendering: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    for rtype, rpath in paths.items():
        print(f"[build_reports] {rtype}: {rpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
