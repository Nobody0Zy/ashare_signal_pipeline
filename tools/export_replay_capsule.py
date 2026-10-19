"""导出 first_round_closeout replay capsule。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools._closeout_common import FROZEN_EXPERIMENTS_BY_ID, get_spec, render_capsule_text, write_capsule


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="为冻结白名单实验导出 replay capsule。",
    )
    parser.add_argument(
        "experiment_version",
        choices=sorted(FROZEN_EXPERIMENTS_BY_ID),
        help="白名单中的 experiment_version。",
    )
    parser.add_argument(
        "--output-dir",
        default="design/first_round_closeout/replay_capsules",
        help="capsule 输出目录。",
    )
    parser.add_argument(
        "--allow-missing-metadata-fallback",
        action="store_true",
        help="允许 failed run 在缺失 metadata manifest/source_config/resolved_params 时回退到 pipeline_state + config。",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="仅输出到标准输出，不写文件。",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    spec = get_spec(args.experiment_version)
    text = render_capsule_text(
        spec,
        allow_missing_metadata_fallback=args.allow_missing_metadata_fallback,
    )
    if args.stdout:
        print(text, end="")
        return 0
    output_path = write_capsule(
        spec,
        Path(args.output_dir),
        allow_missing_metadata_fallback=args.allow_missing_metadata_fallback,
    )
    print(output_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
