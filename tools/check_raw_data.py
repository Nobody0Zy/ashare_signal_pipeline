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
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.adapters.sample_family import (
    DATEBAR_REQUIRED_COLUMNS,
    HOLDING_REQUIRED_COLUMNS,
    LABEL_REQUIRED_COLUMNS,
    SAMPLES_REQUIRED_COLUMNS,
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


def _canonical_json(obj: object) -> str:
    """确定性 JSON 序列化（与 data_prep.ids.canonical_json 等价，不依赖 data_prep）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _derive_params_hash_from_config(params: PipelineParams) -> str:
    """从配置参数计算 params_hash（不需要读取文件，与 build_samples.py 逻辑一致）。"""
    dp = params.data_prep
    universe_for_hash = {
        "exclude_prefixes": list(dp.universe.exclude_prefixes),
        "min_history_days": dp.universe.min_history_days,
    }
    hash_input = {
        "method": dp.samples.method,
        "params": dp.samples.params,
        "universe": universe_for_hash,
        "date_range": {"start": dp.date_range_start, "end": dp.date_range_end},
        "min_history_days": dp.universe.min_history_days,
        "max_samples": dp.samples.max_samples,
    }
    return hashlib.sha256(_canonical_json(hash_input).encode("utf-8")).hexdigest()[:8]


def _find_prepared_dir(params: PipelineParams) -> tuple[Path | None, str | None]:
    """查找与配置匹配的 prepared 目录，返回 (dir_path, samples_id) 或 (None, None)。"""
    target_samples_id = params.target.samples_id
    prepared_root = REPO_ROOT / params.data.prepared_dir

    if target_samples_id != "auto":
        d = prepared_root / target_samples_id
        return (d, target_samples_id) if d.exists() else (None, None)

    # auto 模式：扫描 manifest 找匹配的 params_hash + method
    expected_hash = _derive_params_hash_from_config(params)
    expected_method = params.data_prep.samples.method
    if not prepared_root.exists():
        return None, None
    for manifest_path in sorted(prepared_root.glob("*/manifest.json")):
        try:
            with manifest_path.open("r", encoding="utf-8") as fh:
                m = json.load(fh)
            if (m.get("params_hash") == expected_hash
                    and m.get("sampling_method") == expected_method):
                samples_id = str(m["samples_id"])
                return manifest_path.parent, samples_id
        except Exception:
            continue
    return None, None


def _check_prepared_segment(
    params: PipelineParams,
    check_columns: bool,
    args_json: bool,
) -> dict[str, object]:
    """检查 samples 模式的 prepared 目录、列契约、指纹与标签存在性。"""
    report: dict[str, object] = {
        "mode": "samples",
        "prepared_dir": None,
        "samples_id": None,
        "status": "missing",
        "issues": [],
    }
    issues: list[str] = []

    prepared_dir, samples_id = _find_prepared_dir(params)
    if prepared_dir is None or samples_id is None:
        report["issues"] = ["未找到匹配配置的 prepared 目录（samples_id=auto 时扫描 manifest）"]
        return report

    report["prepared_dir"] = str(prepared_dir)
    report["samples_id"] = samples_id

    samples_path = prepared_dir / "samples.parquet"
    manifest_path = prepared_dir / "manifest.json"

    if not samples_path.exists():
        issues.append(f"samples.parquet 不存在: {samples_path}")
    if not manifest_path.exists():
        issues.append(f"manifest.json 不存在: {manifest_path}")

    if issues:
        report["status"] = "missing"
        report["issues"] = issues
        return report

    # 读取 manifest
    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except Exception as exc:
        issues.append(f"manifest.json 不可读: {exc}")
        report["status"] = "invalid"
        report["issues"] = issues
        return report

    # 验证 params_hash
    expected_hash = _derive_params_hash_from_config(params)
    stored_hash = manifest.get("params_hash", "")
    if stored_hash != expected_hash:
        issues.append(
            f"manifest.params_hash={stored_hash!r} 与当前配置推导值 {expected_hash!r} 不一致"
        )

    # 检查 samples.parquet 列契约与 sample_id
    try:
        import pyarrow.parquet as pq
        schema = pq.read_schema(samples_path)
        actual_cols = [str(n) for n in schema.names]
        required_cols = list(SAMPLES_REQUIRED_COLUMNS)
        missing_cols = [c for c in required_cols if c not in actual_cols]
        if missing_cols:
            issues.append(f"samples.parquet 缺少必要列: {missing_cols}")

        # 读取 sample_id、idxd、idxs 进行进一步检查
        table = pq.read_table(samples_path, columns=["sample_id", "idxd", "idxs"])
        import numpy as np

        sid_arr = table.column("sample_id").to_pylist()
        # sample_id 唯一性
        if len(sid_arr) != len(set(sid_arr)):
            issues.append("samples.parquet 中 sample_id 不唯一")
        # sample_id 类型（int64）
        if schema.field("sample_id").type.id not in (8,):  # pyarrow int64 type id
            # 宽松检查：只要能转为 int 即可
            try:
                int(sid_arr[0])
            except Exception:
                issues.append("sample_id 不是整数类型")
        # idxd 可 int 化
        try:
            idxd_arr = table.column("idxd").to_pylist()
            if idxd_arr:
                int(idxd_arr[0])
        except Exception:
            issues.append("idxd 列无法转换为 int")

        # 验证 samples_set_hash
        sid_np = np.array(sid_arr, dtype="<i8")
        computed_set_hash = hashlib.sha256(np.sort(sid_np).tobytes()).hexdigest()[:16]
        stored_set_hash = manifest.get("samples_set_hash", "")
        if stored_set_hash and computed_set_hash != stored_set_hash:
            issues.append(
                f"samples_set_hash 不匹配: 计算值={computed_set_hash!r} 存储值={stored_set_hash!r}"
            )

        # idxs 交集率检查（与 datebar）
        samples_idxs = set(table.column("idxs").to_pylist())
        resolved = resolve_raw_data_paths(REPO_ROOT, params, None)
        datebar_path = resolved.get("datebar") or resolved["datebar"]
        if Path(str(datebar_path)).exists():
            try:
                import pandas as pd
                datebar_df = pd.read_pickle(str(datebar_path))
                datebar_df = datebar_df.reset_index()
                datebar_df.columns = [str(c).lower() for c in datebar_df.columns]
                datebar_idxs = set(datebar_df["idxs"].unique().tolist())
                intersection = samples_idxs & datebar_idxs
                rate = len(intersection) / len(samples_idxs) if samples_idxs else 1.0
                report["idxs_intersection_rate"] = round(rate, 6)
                if rate < 0.99:
                    unmatched = sorted(samples_idxs - datebar_idxs)[:5]
                    issues.append(
                        f"idxs 交集率 {rate:.4f} < 0.99，前 5 个不匹配样例: {unmatched}"
                    )
            except Exception as exc:
                report["idxs_check_warning"] = f"idxs 交集率检查跳过: {exc}"
        else:
            report["idxs_check_warning"] = f"datebar 不存在，跳过 idxs 交集率检查"

        samples_rows = len(sid_arr)
        report["samples_rows"] = samples_rows

    except Exception as exc:
        issues.append(f"samples.parquet 读取失败: {exc}")

    # 检查标签文件存在性与行数
    labels_root = REPO_ROOT / params.data.labels_dir
    label_name = str(params.target.label).strip()
    if not label_name and params.target.source == "holding_return":
        label_name = "target_holding_return"
    if label_name:
        label_path = labels_root / samples_id / f"{label_name}.parquet"
        if not label_path.exists():
            issues.append(f"标签文件不存在: {label_path}")
        else:
            try:
                import pyarrow.parquet as pq
                label_meta = pq.read_metadata(label_path)
                label_rows = label_meta.num_rows
                s_rows = report.get("samples_rows")
                if s_rows is not None and label_rows != s_rows:
                    issues.append(
                        f"标签文件行数 {label_rows} 与 samples 行数 {s_rows} 不一致"
                    )
                report["label_rows"] = label_rows
            except Exception as exc:
                issues.append(f"标签文件读取失败: {exc}")

    if issues:
        report["status"] = "invalid"
    else:
        report["status"] = "ready"
    report["issues"] = issues
    return report


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

    input_mode = getattr(getattr(params, "input", None), "mode", "holding_file")

    # ── samples 模式：检查 prepared 目录 ──────────────────────────────────────
    if input_mode == "samples":
        prepared_report = _check_prepared_segment(params, args.check_columns, args.json)
        status = prepared_report.get("status", "missing")
        if status == "ready":
            exit_code = EXIT_READY
        elif status == "missing":
            exit_code = EXIT_MISSING
        else:
            exit_code = EXIT_INVALID

        if args.json:
            print(json.dumps({"mode": "samples", "config": source, **prepared_report},
                             ensure_ascii=False, indent=2))
        else:
            print(f"mode             : samples")
            print(f"status           : {status}")
            print(f"config           : {source}")
            print(f"samples_id       : {prepared_report.get('samples_id', '')}")
            print(f"prepared_dir     : {prepared_report.get('prepared_dir', '')}")
            for issue in (prepared_report.get("issues") or []):
                print(f"  issue          : {issue}")
            if prepared_report.get("idxs_intersection_rate") is not None:
                print(f"idxs_intersection: {prepared_report['idxs_intersection_rate']:.6f}")
            if prepared_report.get("idxs_check_warning"):
                print(f"idxs_warning     : {prepared_report['idxs_check_warning']}")
            next_actions = {
                "ready": "可以运行 step1~step6（samples 模式）。",
                "missing": "prepared 目录缺失；请先运行 tools/build_samples.py 与 tools/build_labels.py。",
                "invalid": "prepared 数据存在但不可用；请检查上方 issue 并重建。",
            }
            print(f"next_action      : {next_actions.get(status, '')}")
        return exit_code

    # ── holding_file 模式：原有逻辑 ───────────────────────────────────────────
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
