from __future__ import annotations

import argparse
from pathlib import Path

from pipeline.ensemble.bagging import run_bagging_experiment


def main() -> int:
    parser = argparse.ArgumentParser(description="bagging experiment runner")
    parser.add_argument("--config", required=True, help="bagging YAML path")
    parser.add_argument("--dry-run", action="store_true", help="resolve source and planned versions only")
    parser.add_argument(
        "--prepare-source-only",
        action="store_true",
        help="prepare or validate source experiment and stop before lab training",
    )
    parser.add_argument("--max-labs", type=int, default=None, help="limit number of labs to run")
    parser.add_argument("--skip-analysis", action="store_true", help="skip ensemble analysis and final bagging report")
    parser.add_argument("--only-aggregate", action="store_true", help="aggregate existing lab outputs only")
    args = parser.parse_args()

    return run_bagging_experiment(
        root=Path(__file__).resolve().parent,
        config_path=args.config,
        dry_run=args.dry_run,
        prepare_source_only=args.prepare_source_only,
        max_labs=args.max_labs,
        skip_analysis=args.skip_analysis,
        only_aggregate=args.only_aggregate,
    )


if __name__ == "__main__":
    raise SystemExit(main())