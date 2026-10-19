"""first_round_closeout 分级回收 dry-run 与校验工具。"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools._closeout_common import (
    FROZEN_EXPERIMENTS,
    ROOT,
    directory_size_bytes,
    master_docs_exist,
    master_library_dir,
    prediction_prune_targets,
    protected_paths,
    raw_data_paths,
    relative_to_root,
    validate_capsule_file,
    validate_config_loadable,
    is_config_registered,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="校验五判据，并对 first_round_closeout 的既定删除范围执行 dry-run 或删除。",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="只输出校验结果与待删清单（默认行为）。",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="在五判据全部通过且命中数正确时执行删除。",
    )
    parser.add_argument(
        "--capsule-dir",
        default="design/first_round_closeout/replay_capsules",
        help="capsule 目录。",
    )
    parser.add_argument(
        "--allow-missing-metadata-fallback",
        action="store_true",
        help="接受当前任务已确认的 failed-run metadata 回退例外。",
    )
    return parser


def validate_five_gates(capsule_dir: Path) -> list[dict[str, object]]:
    raw_holding, raw_datebar = raw_data_paths()
    master_dir = master_library_dir() if FROZEN_EXPERIMENTS else None
    validations: list[dict[str, object]] = []
    for spec in FROZEN_EXPERIMENTS:
        capsule_path = capsule_dir / f"{spec.experiment_version}.md"
        capsule_missing = validate_capsule_file(capsule_path)
        capsule_text = capsule_path.read_text(encoding="utf-8") if capsule_path.exists() else ""
        gate_1 = not capsule_missing
        gate_2 = (ROOT / spec.config_path).exists() and is_config_registered(spec.config_path)
        gate_3 = "- rerun:" in capsule_text and "run_experiment_pipeline.py" in capsule_text
        gate_4 = raw_holding.exists() and raw_datebar.exists() and master_docs_exist() and (
            master_dir.exists() if master_dir is not None else True
        )
        gate_5 = False
        gate_5_error = ""
        try:
            gate_5 = validate_config_loadable(spec.config_path)
        except Exception as exc:  # pragma: no cover - surfaced in output
            gate_5_error = str(exc)
        validations.append(
            {
                "experiment_version": spec.experiment_version,
                "status": spec.status,
                "config": spec.config_path,
                "gates": {
                    "capsule_exists_and_complete": gate_1,
                    "config_exists_and_registered": gate_2,
                    "rerun_documented": gate_3,
                    "raw_data_and_master_docs_ready": gate_4,
                    "config_loadable_via_yaml_path": gate_5,
                },
                "errors": {
                    "capsule": capsule_missing,
                    "config_loadable": gate_5_error,
                },
                "all_passed": gate_1 and gate_2 and gate_3 and gate_4 and gate_5,
            }
        )
    return validations


def build_report(capsule_dir: Path) -> dict[str, object]:
    targets = prediction_prune_targets()
    expected_count = 2 * len(FROZEN_EXPERIMENTS)
    target_records = [
        {
            "path": relative_to_root(path),
            "size_gb": round(path.stat().st_size / (1024**3), 2),
        }
        for path in targets
    ]
    master_dir = master_library_dir() if FROZEN_EXPERIMENTS else None
    validations = validate_five_gates(capsule_dir)
    retain = {
        name: {"path": relative_to_root(path), "exists": path.exists()}
        for name, path in protected_paths().items()
    }
    return {
        "expected_prediction_target_count": expected_count,
        "prediction_target_count": len(targets),
        "prediction_total_gb": round(sum(path.stat().st_size for path in targets) / (1024**3), 2),
        "prediction_targets": target_records,
        "master_library_target": {
            "path": relative_to_root(master_dir) if master_dir is not None else "",
            "exists": master_dir.exists() if master_dir is not None else False,
            "size_gb": round(directory_size_bytes(master_dir) / (1024**3), 2) if master_dir is not None else 0.0,
        },
        "retain_checks": retain,
        "gate_results": validations,
    }


def execute_prune(report: dict[str, object]) -> None:
    expected_count = 2 * len(FROZEN_EXPERIMENTS)
    targets = [Path(item["path"]) for item in report["prediction_targets"]]
    if report["prediction_target_count"] != expected_count:
        raise ValueError(
            f"删除前自检失败：train/val 预测文件命中数 {report['prediction_target_count']}，"
            f"预期 {expected_count}（2 × {len(FROZEN_EXPERIMENTS)} 个实验）。"
        )
    if not all(item["all_passed"] for item in report["gate_results"]):
        raise ValueError("删除前五判据未全部通过。")
    for rel_path in targets:
        (ROOT / rel_path).unlink()
    master_target_path = str(report["master_library_target"]["path"]).strip()
    master_dir = ROOT / master_target_path if master_target_path else None
    if master_dir is not None and master_dir.exists():
        shutil.rmtree(master_dir)


def main() -> int:
    args = build_parser().parse_args()
    capsule_dir = Path(args.capsule_dir)
    report = build_report(capsule_dir)
    expected_count = 2 * len(FROZEN_EXPERIMENTS)
    if args.execute:
        execute_prune(report)
        report["executed"] = True
        report["post_delete_master_exists"] = master_library_dir().exists() if FROZEN_EXPERIMENTS else False
    else:
        report["executed"] = False
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["prediction_target_count"] != expected_count or not all(
        item["all_passed"] for item in report["gate_results"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
