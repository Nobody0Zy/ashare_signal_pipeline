"""生成新样本族/版本的 design 与 config 入口。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools._closeout_common import ROOT


def sanitize_token(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip()).strip("_")
    return cleaned or "baseline"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="为新谱系生成 design/config 目录与 experiment_index 占位条目。",
    )
    parser.add_argument("base_version", help="新的 base_version。")
    parser.add_argument(
        "--version-subdir",
        default="v0",
        help="该样本族内的版本迭代目录（v0 / v1 ...）。",
    )
    parser.add_argument("--label", default="baseline", help="首个配置的 label。")
    parser.add_argument("--name", default="", help="experiment_index 中的 name；默认自动生成。")
    parser.add_argument("--description", default="TODO: describe this lineage.", help="注册说明。")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要创建的路径和条目。")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的 CONTEXT / YAML 模板。")
    return parser


def build_index_entry(
    base_version: str,
    version_subdir: str,
    label: str,
    name: str,
    config_rel: str,
    description: str,
) -> str:
    entry_name = name or f"{base_version}_{version_subdir}_{sanitize_token(label)}"
    return "\n".join(
        [
            f"  - name: {entry_name}",
            f"    config: {config_rel}",
            f"    description: {description}",
            f"    expected_base_version: {base_version}",
            f"    label: {label}",
        ]
    )


def ensure_design_readme(design_readme: Path, base_version: str, version_subdir: str, config_rel: str) -> None:
    version_line = f"- `{version_subdir}`: `design/{base_version}/{version_subdir}/` ↔ `{config_rel}`"
    if not design_readme.exists():
        design_readme.write_text(
            "\n".join(
                [
                    f"# {base_version} 样本族总览",
                    "",
                    "- 状态: 待规划",
                    "- 维护方式: 每新增一个 `version_subdir`，在下方登记一行。",
                    "",
                    "## 已登记版本",
                    version_line,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return

    content = design_readme.read_text(encoding="utf-8")
    if version_line in content:
        return
    suffix = "" if content.endswith("\n") else "\n"
    design_readme.write_text(f"{content}{suffix}{version_line}\n", encoding="utf-8")


def write_context_template(context_path: Path, base_version: str, version_subdir: str, label: str) -> None:
    context_path.write_text(
        "\n".join(
            [
                f"# {base_version} {version_subdir} CONTEXT",
                "",
                f"- 样本族（base_version）: `{base_version}`",
                f"- 版本迭代（version_subdir）: `{version_subdir}`",
                f"- 首个配置标签（label）: `{label}`",
                "- fork 来源: TODO",
                "- 数据来源: TODO",
                "- 本轮目标: TODO",
                "- 关键约束: 先补充本版本的 experiment_design / implementation_plan / closeout。",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_config_template(config_path: Path, base_version: str, version_subdir: str, label: str) -> None:
    config_path.write_text(
        "\n".join(
            [
                "seed: 42",
                "smoke_size: 5000",
                "",
                "experiment:",
                f"  base_version: {base_version}",
                f"  version_subdir: {version_subdir}",
                "  run_date: auto",
                f"  label: {label}",
                "  include_param_hash: true",
                "  param_hash_length: 8",
                "  auto_eval_summary: false",
                "",
            ]
        ),
        encoding="utf-8",
    )


def append_index_entry(experiment_index: Path, config_rel: str, index_entry: str) -> None:
    index_text = experiment_index.read_text(encoding="utf-8")
    if config_rel in index_text:
        return
    if "experiments: []" in index_text:
        updated = index_text.replace("experiments: []", f"experiments:\n{index_entry}", 1)
    else:
        separator = "" if index_text.endswith("\n") else "\n"
        updated = f"{index_text}{separator}{index_entry}\n"
    experiment_index.write_text(updated, encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    base_version = sanitize_token(args.base_version)
    version_subdir = sanitize_token(args.version_subdir)
    label = sanitize_token(args.label)
    design_base_dir = ROOT / "design" / base_version
    design_dir = design_base_dir / version_subdir
    context_path = design_dir / "CONTEXT.md"
    config_dir = ROOT / "configs" / base_version / version_subdir
    config_path = config_dir / f"{base_version}_{label}.yaml"
    design_readme = design_base_dir / "README.md"
    config_rel = f"configs/{base_version}/{version_subdir}/{config_path.name}"
    index_entry = build_index_entry(base_version, version_subdir, label, args.name, config_rel, args.description)

    if args.dry_run:
        print(design_dir.as_posix())
        print(config_dir.as_posix())
        print(config_path.as_posix())
        print(context_path.as_posix())
        print(index_entry)
        return 0

    design_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    ensure_design_readme(design_readme, base_version, version_subdir, config_rel)

    if not context_path.exists() or args.force:
        write_context_template(context_path, base_version, version_subdir, label)

    if not config_path.exists() or args.force:
        write_config_template(config_path, base_version, version_subdir, label)

    append_index_entry(ROOT / "configs" / "experiment_index.yaml", config_rel, index_entry)

    print(design_dir.as_posix())
    print(config_dir.as_posix())
    print(config_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
