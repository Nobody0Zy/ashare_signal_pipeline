"""CLI：构建全部内置标签（§6.1）。

用法：
  python tools/build_labels.py --config configs/local/refactor_check.yaml [--dry-run] [--json]

遍历所有已注册的标签定义，对每个定义用 params_key 取配置参数
（缺省回落 default_params，只有未知 key 才报错），一次 compute 落盘全部产出名。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

# 允许从仓库根运行（确保 data_prep 可导入）
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_params_as_dict(config_path: str) -> dict:
    """使用 pipeline.config.load_params 解析 YAML，返回 asdict 的结果。

    CLI 必须经由 load_params（§6.2 不变量 9）；禁止自行 yaml.safe_load。
    """
    from pipeline.config import load_params  # noqa: PLC0415

    params = load_params(config_path)
    return dataclasses.asdict(params)


def _build_all_labels(config_dict: dict, dry_run: bool = False) -> list[dict[str, Any]]:
    """遍历所有已注册定义，构建全部标签，返回摘要列表。"""
    from data_prep.datebar import load_datebar
    from data_prep.label_builder import _compute_label_params_hash, _get_public_params, _materialize
    from data_prep.labels import _fr, _hr, _tq  # noqa: F401 确保注册
    from data_prep.labels.base import LabelContext, build_label_sym_arrays
    from data_prep.labels.registry import _REGISTRY

    data_cfg: dict = config_dict.get("data", {})
    dp_cfg: dict = config_dict.get("data_prep", {})
    labels_cfg: dict = dp_cfg.get("labels", {})
    labels_params: dict = labels_cfg.get("params", {})
    drop_blocked: bool = bool(labels_cfg.get("drop_blocked_entries", True))

    prepared_root = data_cfg.get("prepared_dir", "data/prepared")
    labels_dir = Path(data_cfg.get("labels_dir", "data/labels"))

    # 推导 samples_id
    import hashlib
    from data_prep.datebar import load_datebar as _load_db
    from data_prep.ids import canonical_json, derive_samples_id

    datebar_path = data_cfg.get("datebar_file", "")
    df_datebar, datebar_fingerprint = _load_db(datebar_path)
    sym_arrays = build_label_sym_arrays(df_datebar)
    ctx = LabelContext(sym_arrays)

    # 获取 samples_id（从 manifest 目录寻找与当前 datebar 匹配的样本集）
    import data_prep.build_samples as _bs  # noqa: F401
    samples_id = _bs.build(config_dict, dry_run=True)

    # 读取 manifest（若不 dry-run 则必须存在）
    manifest_path = Path(prepared_root) / samples_id / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"samples manifest 不存在: {manifest_path}；"
            "请先运行 tools/build_samples.py 生成样本集"
        )
    with manifest_path.open(encoding="utf-8") as fh:
        manifest = json.load(fh)

    datebar_fingerprint_m = manifest["datebar_fingerprint"]
    samples_set_hash = manifest["samples_set_hash"]

    # 加载 samples
    samples_path = Path(prepared_root) / samples_id / "samples.parquet"
    import pandas as pd
    samples_df = pd.read_parquet(samples_path)

    summary: list[dict[str, Any]] = []

    for pattern, definition in list(_REGISTRY.items()):
        key = definition.params_key
        # 参数解析：取配置中的 params_key 对应值，缺省回落 default_params
        if key and key in labels_params:
            raw_params = dict(labels_params[key])
        elif key == "" or key not in labels_params:
            raw_params = dict(definition.default_params)
        raw_params = {k: v for k, v in raw_params.items() if not k.startswith("_")}
        raw_params["drop_blocked_entries"] = drop_blocked

        try:
            written = _materialize(
                definition, raw_params, config_dict, samples_df, ctx,
                samples_id, datebar_fingerprint_m, samples_set_hash, labels_dir,
                dry_run=dry_run,
            )
            # 确认期望产出名
            params_full = definition.prepare_params(config_dict, raw_params)
            expected = definition.label_names(params_full)
            summary.append({
                "params_key": key,
                "labels": expected,
                "written": [str(p) for p in written],
                "cached": len(written) == 0,
                "error": None,
            })
        except Exception as exc:
            summary.append({
                "params_key": key,
                "labels": [],
                "written": [],
                "cached": False,
                "error": f"{type(exc).__name__}: {exc}",
            })

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建全部内置标签。")
    parser.add_argument("--config", required=True, help="YAML 配置路径")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将构建的标签，不落盘")
    parser.add_argument("--json", action="store_true", dest="json_output", help="以 JSON 输出构建摘要")
    args = parser.parse_args(argv)

    config_dict = _load_params_as_dict(args.config)
    summary = _build_all_labels(config_dict, dry_run=args.dry_run)

    errors = [s for s in summary if s["error"]]
    if errors:
        for e in errors:
            print(f"[build_labels] ERROR params_key={e['params_key']!r}: {e['error']}", file=sys.stderr)

    if args.json_output:
        print(json.dumps({"samples_config": args.config, "definitions": summary}, ensure_ascii=False, indent=2))
    else:
        for s in summary:
            status = "CACHED" if s["cached"] else ("DRY-RUN" if args.dry_run else "WRITTEN")
            if s["error"]:
                status = "ERROR"
            print(f"[build_labels] {status} params_key={s['params_key']!r} labels={s['labels']}")

    if errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
