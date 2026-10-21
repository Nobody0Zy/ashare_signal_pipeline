"""原始数据就绪检查：把「没有数据」与「数据契约不符」分开报告。

模板不强制新谱系在建仓当天就有数据。本工具给出三种明确结果，
让 agent 或人决定是继续 scaffold，还是先补数据：

    退出码 0  数据就绪（文件存在；加 --check-columns 时同时校验列契约）
    退出码 2  数据缺失（允许先建谱系；跑 step1~step6 之前必须补上）
    退出码 3  数据存在但不可用（不可读 / 缺必要列）

用法::

    python tools/check_raw_data.py --config configs/<bv>/<vs>/<name>.yaml
    python tools/check_raw_data.py --config <cfg> --data-dir D:/raw/<family> --check-columns
    python tools/check_raw_data.py --config <cfg> --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.adapters.sample_family import (
    DATEBAR_REQUIRED_COLUMNS,
    HOLDING_REQUIRED_COLUMNS,
    missing_columns,
)
from pipeline.config import PipelineParams, load_params, resolve_raw_data_paths

EXIT_READY = 0
EXIT_MISSING = 2
EXIT_INVALID = 3


def _file_columns(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        import pyarrow.parquet as pq

        return [str(name) for name in pq.read_schema(path).names]
    if suffix in {".pkl", ".pickle"}:
        import pandas as pd

        obj = pd.read_pickle(path)
        if not hasattr(obj, "columns"):
            raise ValueError(f"{path.name} 不是带 columns 的表格对象")
        # 与 step1 的读取方式保持一致：idxs/idxd 可能来自索引
        try:
            obj = obj.reset_index()
        except Exception:
            pass
        return [str(column) for column in obj.columns]
    raise ValueError(f"不支持的原始数据扩展名: {path.suffix}")


def _describe(path: Path, check_columns: bool, required: tuple[str, ...]) -> dict[str, object]:
    info: dict[str, object] = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": 0,
        "readable": None,
        "missing_columns": [],
        "error": "",
    }
    if not path.exists():
        return info
    info["size_bytes"] = path.stat().st_size
    if not check_columns:
        return info
    try:
        columns = _file_columns(path)
    except Exception as exc:  # 文件损坏、依赖缺失、对象类型不符
        info["readable"] = False
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info
    info["readable"] = True
    normalized = [column.lower() for column in columns]
    info["missing_columns"] = missing_columns(normalized, required)
    info["column_count"] = len(columns)
    return info


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查原始数据是否就绪，区分缺失与契约不符。",
    )
    parser.add_argument(
        "--config",
        default="",
        help="YAML 配置路径；留空时使用内核默认路径（data/raw/ 下的约定文件名）。",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="外部数据目录；提供时只取配置里的文件名，不复制文件。",
    )
    parser.add_argument(
        "--check-columns",
        action="store_true",
        help="同时校验列契约（会完整读取 pickle，大文件较慢）。",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出。")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.config:
        params = load_params(args.config)
        source = args.config
    else:
        params = PipelineParams()
        source = "defaults"

    data_dir = Path(args.data_dir) if str(args.data_dir).strip() else None
    if data_dir is not None and not data_dir.exists():
        message = f"数据目录不存在: {data_dir}"
        if args.json:
            print(json.dumps(
                {"status": "invalid", "config": source, "data_dir": str(data_dir), "error": message},
                ensure_ascii=False,
                indent=2,
            ))
        else:
            print(f"status           : invalid")
            print(f"data_dir         : {data_dir}")
            print(f"error            : {message}")
        return EXIT_INVALID

    resolved = resolve_raw_data_paths(REPO_ROOT, params, data_dir)

    holding = _describe(resolved["holding"], args.check_columns, HOLDING_REQUIRED_COLUMNS)
    datebar = _describe(resolved["datebar"], args.check_columns, DATEBAR_REQUIRED_COLUMNS)

    missing_files = [item["path"] for item in (holding, datebar) if not item["exists"]]
    invalid = [
        item["path"]
        for item in (holding, datebar)
        if item["exists"]
        and (item["readable"] is False or item["missing_columns"])
    ]

    if missing_files:
        status = "missing"
        exit_code = EXIT_MISSING
    elif invalid:
        status = "invalid"
        exit_code = EXIT_INVALID
    else:
        status = "ready"
        exit_code = EXIT_READY

    report = {
        "status": status,
        "config": source,
        "data_dir": str(data_dir) if data_dir else "",
        "checked_columns": bool(args.check_columns),
        "holding": holding,
        "datebar": datebar,
        "required_columns": {
            "holding": list(HOLDING_REQUIRED_COLUMNS),
            "datebar": list(DATEBAR_REQUIRED_COLUMNS),
        },
        "next_action": {
            "ready": "可以运行 step1~step6。",
            "missing": "可先 scaffold 与提交；跑 smoke 前把缺失文件放到上述路径。",
            "invalid": "文件存在但不可用：先修数据或改 data.* 指向正确文件，勿硬跑。",
        }[status],
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"status           : {status}")
        print(f"config           : {source}")
        for key in ("holding", "datebar"):
            item = report[key]
            size_mb = item["size_bytes"] / (1024 * 1024)
            print(f"{key:<17}: {item['path']}")
            print(f"{'':<17}  exists={item['exists']} size={size_mb:.1f} MB")
            if item["missing_columns"]:
                print(f"{'':<17}  missing_columns={item['missing_columns']}")
            if item["error"]:
                print(f"{'':<17}  error={item['error']}")
        print(f"next_action      : {report['next_action']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
