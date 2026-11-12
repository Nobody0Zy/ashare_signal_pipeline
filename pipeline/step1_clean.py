"""步骤1：清洗持仓与日线原始数据。"""

from __future__ import annotations

import hashlib
import json
from typing import Dict

import numpy as np
import pandas as pd

from .config import Paths, PipelineParams


def _clean_holding(paths: Paths, params: PipelineParams) -> pd.DataFrame:
    holding = pd.read_parquet(paths.raw_holding)
    if "holding_return" not in holding.columns:
        raise ValueError("holding_stats_all.parquet 缺少 holding_return 列")

    holding = holding[
        holding["is_closed"].eq(True)
        & holding["entry_execution_price"].gt(0)
        & holding["holding_return"].notna()
    ].copy()
    holding = holding.rename_axis("sample_id").reset_index()
    holding["signal_date"] = holding["signal_date"].astype(int)
    holding["target"] = holding["holding_return"]
    # V4.1: 持有期过滤（默认 max_holding_days=null 时不过滤）
    if params.sample_filter.max_holding_days is not None:
        max_days = int(params.sample_filter.max_holding_days * params.sample_filter.tolerance)
        before = len(holding)
        holding = holding[holding["holding_trade_days"].le(max_days)].copy()
        dropped = before - len(holding)
        print(f"步骤1 max_holding_days 过滤: 丢弃 {dropped} 条 (holding > {max_days})")
    return holding


def _clean_datebar(paths: Paths) -> pd.DataFrame:
    datebar = pd.read_pickle(paths.raw_datebar)
    datebar = datebar.reset_index()
    datebar.columns = [str(column).lower() for column in datebar.columns]
    required = {"idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused"}
    missing = required.difference(datebar.columns)
    if missing:
        raise ValueError(f"dateBar.pkl 缺少必要列: {sorted(missing)}")
    datebar = datebar[["idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused"]].copy()
    datebar["idxd"] = datebar["idxd"].astype(int)
    datebar["paused"] = datebar["paused"].astype(bool)
    return datebar.sort_values(["idxs", "idxd"], kind="mergesort").reset_index(drop=True)


def _derive_samples_id_for_config(paths: Paths, params: PipelineParams, datebar_fingerprint: str) -> str:
    """同 build_samples 的 samples_id 推导逻辑（§3.4），与 derive_samples_id 同一函数族。"""
    # 惰性导入避免循环依赖
    from data_prep.ids import canonical_json, derive_samples_id  # noqa: PLC0415

    dp = params.data_prep
    sample_cfg = dp.samples
    universe_p = dp.universe

    method: str = str(sample_cfg.method)
    sample_params: dict = dict(sample_cfg.params) if hasattr(sample_cfg, "params") and sample_cfg.params else {}
    max_samples = sample_cfg.max_samples
    min_history_days: int = int(universe_p.min_history_days)
    exclude_prefixes: list = list(universe_p.exclude_prefixes)
    date_range_start = dp.date_range_start
    date_range_end = dp.date_range_end

    universe_for_hash = {
        "exclude_prefixes": exclude_prefixes,
        "min_history_days": min_history_days,
    }
    hash_input_obj = {
        "method": method,
        "params": sample_params,
        "universe": universe_for_hash,
        "date_range": {"start": date_range_start, "end": date_range_end},
        "min_history_days": min_history_days,
        "max_samples": max_samples,
    }
    params_hash = hashlib.sha256(canonical_json(hash_input_obj).encode("utf-8")).hexdigest()[:8]
    return derive_samples_id(method, params_hash, datebar_fingerprint)


def _clean_samples(paths: Paths, params: PipelineParams, mode: str) -> Dict[str, object]:
    """samples 模式：从样本集 + 标签文件组合 holding_clean.parquet（§3.3 / §6.3）。"""
    # 惰性导入（§6.2 不变量 3：pipeline 只在函数内惰性 import data_prep）
    from data_prep.ids import derive_datebar_fingerprint  # noqa: PLC0415
    from data_prep.labels.registry import resolve_label   # noqa: PLC0415

    # 1. 解析标签名（同时验证标签名合法；§3.3 规则 5）
    source = str(params.target.source).strip()
    if source == "holding_return":
        label_name = "target_holding_return"
    else:
        label_name = str(params.target.label).strip()
    resolve_label(label_name)  # 触发注册表校验

    # 2. 计算 datebar_fingerprint（§3.4 双重校验；与 _clean_datebar 的加载显式分离，§6.2 不变量 10）
    datebar_fingerprint = derive_datebar_fingerprint(paths.raw_datebar)

    # 3. 推导 samples_id（调用与 build_samples 同一函数族，§3.4）
    computed_samples_id = _derive_samples_id_for_config(paths, params, datebar_fingerprint)
    explicit_sid = str(params.target.samples_id).strip()
    if explicit_sid and explicit_sid != "auto":
        if explicit_sid != computed_samples_id:
            raise ValueError(
                f"samples_id 配置值 {explicit_sid!r} 与推导值 {computed_samples_id!r} 不一致；"
                "请检查配置是否与当前 data_prep 参数匹配。"
            )
    samples_id = computed_samples_id

    # 4. 读取 manifest，校验 datebar_fingerprint（§3.4）
    prepared_dir = paths.prepared_root_dir / samples_id
    manifest_path = prepared_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"samples manifest 不存在: {manifest_path}；请先运行 tools/build_samples.py"
        )
    with manifest_path.open("r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    if manifest["datebar_fingerprint"] != datebar_fingerprint:
        raise ValueError(
            f"datebar_fingerprint 不一致：manifest={manifest['datebar_fingerprint']!r}，"
            f"当前={datebar_fingerprint!r}；samples 产物与当前 dateBar 不匹配，请重建 samples。"
        )

    # 5. 读取 samples.parquet，校验 samples_set_hash（§3.4）
    samples_path = prepared_dir / "samples.parquet"
    if not samples_path.exists():
        raise FileNotFoundError(f"samples.parquet 不存在: {samples_path}")
    samples = pd.read_parquet(samples_path)

    sid_arr = samples["sample_id"].to_numpy().astype("<i8")
    computed_set_hash = hashlib.sha256(np.sort(sid_arr).tobytes()).hexdigest()[:16]
    if manifest["samples_set_hash"] != computed_set_hash:
        raise ValueError(
            f"samples_set_hash 不一致：manifest={manifest['samples_set_hash']!r}，"
            f"当前={computed_set_hash!r}；samples 可能被替换或损坏。"
        )

    samples_rows = int(len(samples))

    # 6. 读取标签文件（缺失即报错，不隐式构建；§7 M4）
    labels_dir = paths.labels_root_dir
    label_file = labels_dir / samples_id / f"{label_name}.parquet"
    if not label_file.exists():
        raise FileNotFoundError(
            f"标签文件不存在: {label_file}；请先运行 tools/build_labels.py"
        )
    label_df = pd.read_parquet(label_file)

    # 7. 读取 _index.json 获取 label_hash 与 label_non_null_ratio
    index_file = labels_dir / samples_id / "_index.json"
    if not index_file.exists():
        raise FileNotFoundError(f"标签索引文件不存在: {index_file}")
    with index_file.open("r", encoding="utf-8") as fh:
        index_data = json.load(fh)
    label_entry = index_data.get(label_name, {})
    label_hash: str = str(label_entry.get("params_hash", ""))
    label_non_null_ratio: float = float(label_entry.get("coverage", 0.0))

    # 8. join: samples 取 sample_id/idxs，标签取 signal_date/target
    label_subset = label_df[["sample_id", "signal_date", "target"]].copy()
    merged = samples[["sample_id", "idxs"]].merge(label_subset, on="sample_id", how="left")

    # 9. 丢弃 target 为空的行，计入 dropped_no_target（§4.9）
    before_drop = len(merged)
    merged = merged[merged["target"].notna()].copy()
    dropped_no_target = before_drop - len(merged)
    sample_count = len(merged)
    target_coverage = float(sample_count / samples_rows) if samples_rows > 0 else 0.0

    # 10. 类型保证
    merged["signal_date"] = merged["signal_date"].astype(int)
    merged["sample_id"] = merged["sample_id"].astype("int64")

    # 11. 按 sample_id 升序（§6.2 不变量 8；§3.3 规则 2）
    merged = merged.sort_values("sample_id", kind="mergesort").reset_index(drop=True)

    # 12. 写出 holding_clean.parquet
    holding_path = paths.processed_dir / "holding_clean.parquet"
    merged.to_parquet(holding_path, index=False)

    # 13. 写出 datebar.parquet（显式第二次加载；§6.2 不变量 10）
    datebar = _clean_datebar(paths)
    datebar_path = paths.processed_dir / "datebar.parquet"
    datebar.to_parquet(datebar_path, index=False)

    target_stats = merged["target"].describe(percentiles=[0.25, 0.5, 0.75]).to_dict()
    checks = {
        "holding_exists": holding_path.exists(),
        "holding_columns": all(col in merged.columns for col in ["sample_id", "idxs", "signal_date", "target"]),
        "holding_target_non_null": bool(merged["target"].notna().all()),
        "datebar_exists": datebar_path.exists(),
        "datebar_columns": list(datebar.columns) == ["idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused"],
        "datebar_sorted": datebar.equals(datebar.sort_values(["idxs", "idxd"], kind="mergesort").reset_index(drop=True)),
    }

    return {
        "output_files": [
            str(holding_path.relative_to(paths.root)).replace("\\", "/"),
            str(datebar_path.relative_to(paths.root)).replace("\\", "/"),
        ],
        "checks": checks,
        "details": {
            "input_mode": "samples",
            "coupling_mode": "decoupled",
            "samples_rows": samples_rows,
            "sample_count": sample_count,
            "dropped_no_target": dropped_no_target,
            "target_coverage": target_coverage,
            "signal_date_min": int(merged["signal_date"].min()),
            "signal_date_max": int(merged["signal_date"].max()),
            "target_stats": {k: float(v) for k, v in target_stats.items()},
            "datebar_rows": int(len(datebar)),
            "datebar_fingerprint": datebar_fingerprint,
            "samples_id": samples_id,
            "samples_set_hash": computed_set_hash,
            "label_name": label_name,
            "label_hash": label_hash,
            "label_non_null_ratio": label_non_null_ratio,
        },
    }


def compute_step1(paths: Paths, mode: str, params: PipelineParams) -> Dict[str, object]:
    # M4：samples 模式分派；M1 守卫逻辑改为 early-return，原有行不删除
    mode_in = getattr(getattr(params, "input", None), "mode", "holding_file")
    if mode_in == "samples":
        return _clean_samples(paths, params, mode)

    # 旧路径（holding_file）：以下逻辑与 v1.0.13 完全一致
    del mode
    holding = _clean_holding(paths, params)
    holding_path = paths.processed_dir / "holding_clean.parquet"
    holding.to_parquet(holding_path, index=False)

    datebar = _clean_datebar(paths)
    datebar_path = paths.processed_dir / "datebar.parquet"
    datebar.to_parquet(datebar_path, index=False)

    target_stats = holding["target"].describe(percentiles=[0.25, 0.5, 0.75]).to_dict()
    checks = {
        "holding_exists": holding_path.exists(),
        "holding_columns": all(column in holding.columns for column in ["sample_id", "idxs", "signal_date", "target"]),
        "holding_target_non_null": holding["target"].notna().all(),
        "datebar_exists": datebar_path.exists(),
        "datebar_columns": list(datebar.columns) == ["idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused"],
        "datebar_sorted": datebar.equals(datebar.sort_values(["idxs", "idxd"], kind="mergesort").reset_index(drop=True)),
    }

    return {
        "output_files": [
            str(holding_path.relative_to(paths.root)).replace("\\", "/"),
            str(datebar_path.relative_to(paths.root)).replace("\\", "/"),
        ],
        "checks": checks,
        "details": {
            "input_mode": "holding_file",
            "coupling_mode": "path_dependent",
            "samples_rows": int(len(holding)),
            "sample_count": int(len(holding)),
            "dropped_no_target": 0,
            "target_coverage": 1.0,
            "signal_date_min": int(holding["signal_date"].min()),
            "signal_date_max": int(holding["signal_date"].max()),
            "target_stats": {key: float(value) for key, value in target_stats.items()},
            "datebar_rows": int(len(datebar)),
            "datebar_fingerprint": "",
            "samples_id": "",
            "samples_set_hash": "",
            "label_name": "",
            "label_hash": "",
            "label_non_null_ratio": "",
        },
    }