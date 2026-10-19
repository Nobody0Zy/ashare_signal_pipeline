"""步骤4：tsfresh 特征提取与筛选。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    from tsfresh import extract_features
except ImportError:  # pragma: no cover
    extract_features = None

try:
    from tsfresh.feature_extraction.settings import EfficientFCParameters, from_columns
except ImportError:  # pragma: no cover
    from tsfresh.feature_extraction import EfficientFCParameters
    from tsfresh.feature_extraction.settings import from_columns

from .config import Paths, PipelineParams
from .monitoring import monitor_step
from .utils import chunk_by_ids, file_suffix, replace_inf_and_fill, validate_common_no_nan_inf


COMPACT_KEYS = {
    "abs_energy",
    "absolute_maximum",
    "absolute_sum_of_changes",
    "benford_correlation",
    "cid_ce",
    "count_above_mean",
    "count_below_mean",
    "first_location_of_maximum",
    "first_location_of_minimum",
    "kurtosis",
    "last_location_of_maximum",
    "last_location_of_minimum",
    "length",
    "maximum",
    "mean",
    "mean_abs_change",
    "mean_change",
    "mean_second_derivative_central",
    "median",
    "minimum",
    "root_mean_square",
    "skewness",
    "standard_deviation",
    "sum_values",
    "variance",
    "variation_coefficient",
}

HIGH_COST = {
    "ar_coefficient",
    "cwt_coefficients",
    "fft_aggregated",
    "fft_coefficient",
    "max_langevin_fixed_point",
    "number_cwt_peaks",
    "partial_autocorrelation",
    "query_similarity_count",
    "spkt_welch_density",
}

MID_COST = {
    "agg_autocorrelation",
    "augmented_dickey_fuller",
    "autocorrelation",
    "binned_entropy",
    "fourier_entropy",
    "friedrich_coefficients",
    "lempel_ziv_complexity",
    "permutation_entropy",
}


MASTER_VARIANCE_TOLERANCE = 1e-12


def _sanitize_feature_names(columns: Sequence[str]) -> List[str]:
    cleaned = [re.sub(r"[^A-Za-z0-9_]+", "_", column).strip("_") or "feature" for column in columns]
    seen: Dict[str, int] = {}
    unique_columns: List[str] = []
    for column in cleaned:
        count = seen.get(column, 0)
        if count == 0 and column not in seen:
            unique_columns.append(column)
            seen[column] = 1
        else:
            unique_columns.append(f"{column}_{count}")
            seen[column] = count + 1
    return unique_columns


def _build_full_tsfresh_fc_parameters(profile: str = "compact") -> Dict[str, object]:
    base = EfficientFCParameters()
    base_keys = set(base.keys())

    if profile == "compact":
        selected_keys = COMPACT_KEYS
    elif profile == "medium":
        selected_keys = base_keys - HIGH_COST - MID_COST
    elif profile == "broad":
        selected_keys = base_keys - HIGH_COST
    elif profile == "full":
        selected_keys = base_keys
    else:
        raise ValueError(f"未知 fc_profile: {profile}，可选: compact|medium|broad|full")

    return {key: base[key] for key in base.keys() if key in selected_keys}


def _count_theoretical_features_per_kind(fc_parameters: Dict[str, object]) -> int:
    total = 0
    for calculator in fc_parameters.values():
        if isinstance(calculator, list) and calculator:
            total += len(calculator)
        else:
            total += 1
    return total


def _resolve_tsfresh_jobs(mode: str, config_n_jobs: int = 0) -> int:
    if config_n_jobs > 0:
        return config_n_jobs
    cpu_count = os.cpu_count() or 1
    return max(1, min(cpu_count, 8))


def _extract_tsfresh_chunk(
    chunk_df: pd.DataFrame,
    n_jobs: int,
    disable_progressbar: bool,
    default_fc_parameters: Optional[Dict[str, object]] = None,
    kind_to_fc_parameters: Optional[Dict[str, object]] = None,
) -> pd.DataFrame:
    value_columns = [column for column in chunk_df.columns if column not in ["sample_id", "time_step"]]
    long_df = chunk_df.melt(
        id_vars=["sample_id", "time_step"],
        value_vars=value_columns,
        var_name="kind",
        value_name="value",
    )
    nan_rows = int(long_df["value"].isna().sum())
    if nan_rows:
        print(f"步骤4 tsfresh 输入含 {nan_rows} 条 NaN，提取前删除这些长表行。")
        long_df = long_df.loc[long_df["value"].notna()].copy()
    extract_kwargs = {
        "column_id": "sample_id",
        "column_sort": "time_step",
        "column_kind": "kind",
        "column_value": "value",
        "n_jobs": n_jobs,
        "disable_progressbar": disable_progressbar,
    }
    if kind_to_fc_parameters is not None:
        extract_kwargs["kind_to_fc_parameters"] = kind_to_fc_parameters
    else:
        extract_kwargs["default_fc_parameters"] = default_fc_parameters or EfficientFCParameters()
    return extract_features(long_df, **extract_kwargs)


def _extract_tsfresh_full(
    ts_features: pd.DataFrame,
    sample_ids: np.ndarray,
    n_jobs: int,
    disable_progressbar: bool,
    default_fc_parameters: Optional[Dict[str, object]] = None,
    kind_to_fc_parameters: Optional[Dict[str, object]] = None,
) -> pd.DataFrame:
    """全量一次提取 tsfresh 特征，不写磁盘，不走分块。"""
    subset = ts_features[ts_features["sample_id"].isin(sample_ids)].copy()
    return _extract_tsfresh_chunk(
        subset,
        n_jobs=n_jobs,
        disable_progressbar=disable_progressbar,
        default_fc_parameters=default_fc_parameters,
        kind_to_fc_parameters=kind_to_fc_parameters,
    )


def _extract_tsfresh(
    ts_features: pd.DataFrame,
    sample_ids: np.ndarray,
    chunk_size: int,
    n_jobs: int,
    disable_progressbar: bool,
    paths: Paths,
    suffix: str,
    chunk_prefix: str,
    stage_label: str,
    default_fc_parameters: Optional[Dict[str, object]] = None,
    kind_to_fc_parameters: Optional[Dict[str, object]] = None,
) -> pd.DataFrame:
    """统一入口：chunk_size > 0 走分块路径，否则全量一次提取。"""
    if chunk_size > 0:
        return _extract_tsfresh_by_chunks(
            ts_features=ts_features,
            sample_ids=sample_ids,
            chunk_size=chunk_size,
            n_jobs=n_jobs,
            disable_progressbar=disable_progressbar,
            paths=paths,
            suffix=suffix,
            chunk_prefix=chunk_prefix,
            stage_label=stage_label,
            default_fc_parameters=default_fc_parameters,
            kind_to_fc_parameters=kind_to_fc_parameters,
        )
    print(f"步骤4 {stage_label} 全量一次提取，sample_id 数={len(sample_ids)}，n_jobs={n_jobs}")
    return _extract_tsfresh_full(
        ts_features=ts_features,
        sample_ids=sample_ids,
        n_jobs=n_jobs,
        disable_progressbar=disable_progressbar,
        default_fc_parameters=default_fc_parameters,
        kind_to_fc_parameters=kind_to_fc_parameters,
    )


def _extract_tsfresh_by_chunks(
    ts_features: pd.DataFrame,
    sample_ids: np.ndarray,
    chunk_size: int,
    n_jobs: int,
    disable_progressbar: bool,
    paths: Paths,
    suffix: str,
    chunk_prefix: str,
    stage_label: str,
    default_fc_parameters: Optional[Dict[str, object]] = None,
    kind_to_fc_parameters: Optional[Dict[str, object]] = None,
) -> pd.DataFrame:
    chunk_files: List[Path] = []
    chunks = chunk_by_ids(sample_ids, chunk_size)

    for index, chunk_ids in enumerate(chunks):
        print(
            f"步骤4 {stage_label}分块 {index + 1}/{len(chunks)}，"
            f"sample_id 数={len(chunk_ids)}，n_jobs={n_jobs}"
        )
        subset = ts_features[ts_features["sample_id"].isin(chunk_ids)].copy()
        extracted = _extract_tsfresh_chunk(
            subset,
            n_jobs=n_jobs,
            disable_progressbar=disable_progressbar,
            default_fc_parameters=default_fc_parameters,
            kind_to_fc_parameters=kind_to_fc_parameters,
        )
        chunk_path = paths.tsfresh_chunks_dir / f"{chunk_prefix}_{index:03d}{suffix}.parquet"
        extracted.to_parquet(chunk_path)
        chunk_files.append(chunk_path)
        print(f"步骤4 {stage_label}分块 {index + 1} 提取完成，输出 {chunk_path.name}")

    if not chunk_files:
        return pd.DataFrame()

    merged = pd.concat([pd.read_parquet(path) for path in chunk_files], axis=0, join="outer", sort=False)
    for path in chunk_files:
        path.unlink(missing_ok=True)
    return merged


def _master_library_dir(paths: Paths) -> Path:
    return paths.tsfresh_master_root_dir / paths.resolved_master_library_version


def _master_library_chunks_dir(paths: Paths) -> Path:
    return _master_library_dir(paths) / "chunks"


def _master_library_schema_path(paths: Paths) -> Path:
    return _master_library_dir(paths) / "schema.json"


def _master_library_zero_variance_path(paths: Paths) -> Path:
    return _master_library_dir(paths) / "zero_variance_columns.json"


def _prepare_extracted_feature_frame(
    extracted: pd.DataFrame,
    label: str,
    *,
    fillna: bool,
) -> Tuple[pd.DataFrame, List[str], int, int]:
    extracted = extracted.rename_axis("sample_id").reset_index()
    raw_feature_cols = [column for column in extracted.columns if column != "sample_id"]
    extracted.columns = ["sample_id"] + _sanitize_feature_names(raw_feature_cols)
    extracted, inf_count, nan_count = replace_inf_and_fill(extracted, label, fillna=fillna)
    feature_cols = [column for column in extracted.columns if column != "sample_id"]
    return extracted, feature_cols, inf_count, nan_count


def _select_nonzero_variance_columns(
    feature_df: pd.DataFrame,
    feature_cols: List[str],
    *,
    tolerance: float = MASTER_VARIANCE_TOLERANCE,
) -> Tuple[List[str], int]:
    variances = feature_df[feature_cols].var(axis=0)
    selected = variances[variances > tolerance].index.tolist()
    return selected, len(feature_cols) - len(selected)


def _write_selected_features(selected_path: Path, selected_features: List[str]) -> None:
    with selected_path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(selected_features))
        handle.write("\n")


def _validate_no_inf(df: pd.DataFrame, columns: Sequence[str]) -> bool:
    subset = df.loc[:, list(columns)]
    numeric = subset.select_dtypes(include=[np.number])
    if numeric.empty:
        return True
    return not np.isinf(numeric.to_numpy()).any()


def _build_schema_entries(feature_columns: List[str]) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    for column_name in feature_columns:
        parts = column_name.split("__")
        if len(parts) < 2:
            raise RuntimeError(f"无法解析 tsfresh 列名: {column_name}")
        entries.append(
            {
                "name": column_name,
                "kind": parts[0],
                "calculator": parts[1],
                "params": "__".join(parts[2:]),
            }
        )
    return entries


def _accumulate_feature_stats(
    stats: Optional[Dict[str, object]],
    feature_df: pd.DataFrame,
    feature_columns: List[str],
) -> Dict[str, object]:
    column_frame = feature_df.loc[:, feature_columns]
    chunk_counts = column_frame.count(axis=0).to_numpy(dtype=np.int64, copy=False)
    chunk_sums = column_frame.sum(axis=0, skipna=True).to_numpy(dtype=np.float64, copy=False)
    chunk_sumsq = column_frame.apply(
        lambda series: float(np.square(series.dropna().to_numpy(dtype=np.float64, copy=False)).sum())
    ).to_numpy(dtype=np.float64, copy=False)

    if stats is None:
        return {
            "columns": list(feature_columns),
            "counts": chunk_counts.astype(np.int64, copy=True),
            "sums": chunk_sums.astype(np.float64, copy=True),
            "sumsq": chunk_sumsq.astype(np.float64, copy=True),
        }

    expected_columns = stats.get("columns", [])
    if list(expected_columns) != list(feature_columns):
        raise RuntimeError("流式方差累计收到的列顺序与 schema 不一致")

    stats["counts"] = np.asarray(stats["counts"], dtype=np.int64) + chunk_counts
    stats["sums"] = np.asarray(stats["sums"], dtype=np.float64) + chunk_sums
    stats["sumsq"] = np.asarray(stats["sumsq"], dtype=np.float64) + chunk_sumsq
    return stats


def _finalize_zero_variance_columns(stats: Dict[str, object]) -> List[str]:
    columns = list(stats.get("columns", []))
    counts = np.asarray(stats.get("counts", []), dtype=np.int64)
    sums = np.asarray(stats.get("sums", []), dtype=np.float64)
    sumsq = np.asarray(stats.get("sumsq", []), dtype=np.float64)
    zero_variance_columns: List[str] = []

    for index, column in enumerate(columns):
        count = int(counts[index])
        if count <= 1:
            zero_variance_columns.append(column)
            continue
        numerator = float(sumsq[index] - ((sums[index] * sums[index]) / count))
        variance = numerator / float(count - 1)
        if (not np.isfinite(variance)) or variance <= MASTER_VARIANCE_TOLERANCE:
            zero_variance_columns.append(column)
    return zero_variance_columns


def _build_master_library(
    ts_features: pd.DataFrame,
    holding: pd.DataFrame,
    params: PipelineParams,
    paths: Paths,
    mode: str,
    tsfresh_jobs: int,
    fc_profile_details: Dict[str, object],
) -> Dict[str, object]:
    master_dir = _master_library_dir(paths)
    chunk_dir = _master_library_chunks_dir(paths)
    schema_path = _master_library_schema_path(paths)
    zero_variance_path = _master_library_zero_variance_path(paths)
    master_dir.mkdir(parents=True, exist_ok=True)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for stale_chunk in chunk_dir.glob("chunk_*.parquet"):
        stale_chunk.unlink(missing_ok=True)

    sample_ids = np.sort(ts_features["sample_id"].unique())
    if len(sample_ids) == 0:
        raise RuntimeError("步骤4 母库构建没有可用 sample_id")
    chunk_size = params.tsfresh.chunk_size_tsfresh or len(sample_ids)
    fc_params = _build_full_tsfresh_fc_parameters("full")
    disable_progressbar = mode == "full"
    chunks = chunk_by_ids(sample_ids, chunk_size)

    schema_entries: List[Dict[str, str]] = []
    expected_columns: Optional[List[str]] = None
    stats: Optional[Dict[str, object]] = None
    total_inf_replaced = 0
    total_nan_observed = 0
    chunk_files: List[Path] = []

    for index, chunk_ids in enumerate(chunks):
        print(
            f"步骤4 母库构建分块 {index + 1}/{len(chunks)}，"
            f"sample_id 数={len(chunk_ids)}，n_jobs={tsfresh_jobs}"
        )
        subset = ts_features[ts_features["sample_id"].isin(chunk_ids)].copy()
        extracted = _extract_tsfresh_chunk(
            subset,
            n_jobs=tsfresh_jobs,
            disable_progressbar=disable_progressbar,
            default_fc_parameters=fc_params,
        )
        prepared, feature_columns, inf_count, nan_count = _prepare_extracted_feature_frame(
            extracted,
            f"步骤4 母库分块 {index + 1}",
            fillna=False,
        )
        total_inf_replaced += inf_count
        total_nan_observed += nan_count

        if expected_columns is None:
            expected_columns = feature_columns
            schema_entries = _build_schema_entries(feature_columns)
        else:
            if set(feature_columns) != set(expected_columns):
                missing_columns = sorted(set(expected_columns) - set(feature_columns))
                extra_columns = sorted(set(feature_columns) - set(expected_columns))
                raise RuntimeError(
                    "母库分块列集合不一致: "
                    f"missing={missing_columns[:5]}, extra={extra_columns[:5]}"
                )
            if feature_columns != expected_columns:
                prepared = prepared[["sample_id"] + expected_columns].copy()
                feature_columns = expected_columns

        stats = _accumulate_feature_stats(stats, prepared, feature_columns)
        chunk_path = chunk_dir / f"chunk_{index:03d}.parquet"
        prepared.to_parquet(chunk_path, index=False)
        chunk_files.append(chunk_path)
        print(f"步骤4 母库分块 {index + 1} 输出 {chunk_path.name}")

    if expected_columns is None or stats is None:
        raise RuntimeError("步骤4 母库构建未生成任何有效特征列")

    schema_payload = {
        "master_version": paths.resolved_master_library_version,
        "master_library_role": paths.master_library_role,
        "mode": mode,
        "columns": schema_entries,
    }
    schema_path.write_text(json.dumps(schema_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    zero_variance_columns = _finalize_zero_variance_columns(stats)
    zero_variance_payload = {
        "master_version": paths.resolved_master_library_version,
        "variance_tolerance": MASTER_VARIANCE_TOLERANCE,
        "zero_variance_columns": zero_variance_columns,
    }
    zero_variance_path.write_text(
        json.dumps(zero_variance_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    checks = {
        "master_dir_exists": master_dir.exists(),
        "chunk_files_exist": len(chunk_files) == len(chunks) and all(path.exists() for path in chunk_files),
        "schema_exists": schema_path.exists(),
        "zero_variance_exists": zero_variance_path.exists(),
        "sample_count_matches": int(len(sample_ids)) == int(holding["sample_id"].nunique()),
        "clean_column_names": all(
            re.fullmatch(r"[A-Za-z0-9_]+", column) for column in expected_columns
        ),
    }
    return {
        "output_files": [
            str(chunk_dir.relative_to(paths.root)).replace("\\", "/"),
            str(schema_path.relative_to(paths.root)).replace("\\", "/"),
            str(zero_variance_path.relative_to(paths.root)).replace("\\", "/"),
        ],
        "checks": checks,
        "details": {
            "samples": int(len(sample_ids)),
            "columns": int(len(expected_columns) + 1),
            "selected_features": int(len(expected_columns) - len(zero_variance_columns)),
            "tsfresh_jobs": tsfresh_jobs,
            "output_feature_file": "",
            "tsfresh_inf_replaced": total_inf_replaced,
            "tsfresh_nan_filled": 0,
            "final_inf_replaced": 0,
            "final_nan_filled": 0,
            "invalid_targets_dropped": 0,
            "raw_extracted_feature_columns": int(len(expected_columns)),
            "zero_variance_dropped": int(len(zero_variance_columns)),
            "master_chunk_count": int(len(chunk_files)),
            "master_chunk_size": int(chunk_size),
            "built_master_version": paths.resolved_master_library_version,
            "source_master_version": paths.resolved_master_library_version,
            "master_library_role": paths.master_library_role,
            **fc_profile_details,
        },
    }


def _load_master_schema(paths: Paths) -> List[Dict[str, str]]:
    schema_path = _master_library_schema_path(paths)
    if not schema_path.exists():
        raise RuntimeError(f"母库 schema 不存在: {schema_path}")
    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    columns = payload.get("columns", []) if isinstance(payload, dict) else []
    if not isinstance(columns, list) or not columns:
        raise RuntimeError(f"母库 schema 内容为空: {schema_path}")
    return columns


def _load_zero_variance_columns(paths: Paths) -> List[str]:
    zero_variance_path = _master_library_zero_variance_path(paths)
    if not zero_variance_path.exists():
        raise RuntimeError(f"零方差清单不存在: {zero_variance_path}")
    payload = json.loads(zero_variance_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        columns = payload.get("zero_variance_columns", [])
    else:
        columns = payload
    if not isinstance(columns, list):
        raise RuntimeError(f"零方差清单格式非法: {zero_variance_path}")
    return [str(column) for column in columns]


def _project_master_library(
    ts_features: pd.DataFrame,
    holding: pd.DataFrame,
    params: PipelineParams,
    paths: Paths,
    mode: str,
    tsfresh_jobs: int,
    fc_profile_details: Dict[str, object],
) -> Dict[str, object]:
    del ts_features
    del tsfresh_jobs
    schema_entries = _load_master_schema(paths)
    zero_variance_columns = set(_load_zero_variance_columns(paths))
    chunk_dir = _master_library_chunks_dir(paths)
    chunk_files = sorted(chunk_dir.glob("chunk_*.parquet"))
    if not chunk_files:
        raise RuntimeError(f"母库分块目录为空: {chunk_dir}")

    allowed_calculators = set(_build_full_tsfresh_fc_parameters(params.tsfresh.fc_profile).keys())
    projected_columns = [
        entry["name"]
        for entry in schema_entries
        if isinstance(entry, dict) and str(entry.get("calculator", "")) in allowed_calculators
    ]
    if not projected_columns:
        raise RuntimeError("母库投影未命中任何特征列")

    selected_columns = [column for column in projected_columns if column not in zero_variance_columns]
    if not selected_columns:
        raise RuntimeError("母库投影后无可用非零方差特征列")

    projected_frames: List[pd.DataFrame] = []
    for chunk_path in chunk_files:
        projected_frames.append(pd.read_parquet(chunk_path, columns=["sample_id", *projected_columns]))
    projected_df = pd.concat(projected_frames, axis=0, ignore_index=True, sort=False)
    projected_df = projected_df[["sample_id"] + selected_columns].copy()

    holding_sample_ids = set(holding["sample_id"].tolist())
    projected_sample_ids = set(projected_df["sample_id"].tolist())
    missing_sample_ids = sorted(holding_sample_ids - projected_sample_ids)
    if missing_sample_ids:
        raise RuntimeError(
            f"母库投影缺少 sample_id，示例={missing_sample_ids[:5]}，缺失数={len(missing_sample_ids)}"
        )

    final_df = holding.merge(projected_df, on="sample_id", how="inner")
    final_df, final_inf, final_nan = replace_inf_and_fill(final_df, "步骤4最终特征表", fillna=False)

    suffix = file_suffix(mode)
    selected_features = sorted(selected_columns)
    selected_path = paths.features_dir / f"selected_features{suffix}.txt"
    _write_selected_features(selected_path, selected_features)
    feature_output_path = paths.features_dir / f"features_selected_all{suffix}.parquet"
    final_df.to_parquet(feature_output_path, index=False)

    checks = {
        "feature_file_exists": feature_output_path.exists(),
        "selected_exists": selected_path.exists(),
        "row_count_matches": int(final_df["sample_id"].nunique()) == int(holding["sample_id"].nunique()),
        "unique_columns": len(final_df.columns) == len(set(final_df.columns)),
        "clean_column_names": all(
            re.fullmatch(r"[A-Za-z0-9_]+", column) for column in final_df.columns if column != "sample_id"
        ),
        "no_nan_inf": _validate_no_inf(final_df, final_df.columns),
        "selected_count_valid": len(selected_features) > 0,
    }
    return {
        "output_files": [
            str(selected_path.relative_to(paths.root)).replace("\\", "/"),
            str(feature_output_path.relative_to(paths.root)).replace("\\", "/"),
        ],
        "checks": checks,
        "details": {
            "samples": int(final_df["sample_id"].nunique()),
            "columns": int(len(final_df.columns)),
            "selected_features": int(len(selected_features)),
            "tsfresh_jobs": 1,
            "output_feature_file": str(feature_output_path.relative_to(paths.root)).replace("\\", "/"),
            "tsfresh_inf_replaced": 0,
            "tsfresh_nan_filled": 0,
            "final_inf_replaced": final_inf,
            "final_nan_filled": final_nan,
            "invalid_targets_dropped": 0,
            "raw_extracted_feature_columns": int(len(projected_columns)),
            "zero_variance_dropped": int(len(projected_columns) - len(selected_columns)),
            "source_master_version": paths.resolved_master_library_version,
            "master_library_role": paths.master_library_role,
            "master_chunk_count": int(len(chunk_files)),
            **fc_profile_details,
        },
    }


def _select_features_smoke(
    feature_df: pd.DataFrame,
    feature_cols: List[str],
    params: PipelineParams,
) -> Tuple[List[str], int]:
    variance = feature_df[feature_cols].var(axis=0)
    usable_cols = variance[variance > 0].index.tolist()
    zero_variance_dropped = len(feature_cols) - len(usable_cols)
    correlations = {}
    target = feature_df["target"]
    for column in usable_cols:
        corr = feature_df[column].corr(target)
        correlations[column] = 0.0 if pd.isna(corr) else abs(float(corr))
    top_candidates = [
        name
        for name, _ in sorted(correlations.items(), key=lambda item: item[1], reverse=True)[:params.feature_selection.top_candidates]
    ]
    if not top_candidates:
        return []
    corr_matrix = feature_df[top_candidates].corr().abs().fillna(0)
    selected: List[str] = []
    for column in top_candidates:
        if all(corr_matrix.loc[column, keep] <= params.feature_selection.corr_threshold for keep in selected):
            selected.append(column)
        if params.feature_selection.max_features is not None and len(selected) == params.feature_selection.max_features:
            break
    return selected or top_candidates[:params.feature_selection.max_features], zero_variance_dropped


def _select_features_full(
    train_df: pd.DataFrame,
    feature_cols: List[str],
    params: PipelineParams,
) -> Tuple[List[str], float, float, int]:
    p_low = float(train_df["target"].quantile(params.feature_selection.target_clip_low))
    p_high = float(train_df["target"].quantile(params.feature_selection.target_clip_high))
    clipped_target = train_df["target"].clip(lower=p_low, upper=p_high)
    variance = train_df[feature_cols].var(axis=0)
    usable_cols = variance[variance > 0].index.tolist()
    zero_variance_dropped = len(feature_cols) - len(usable_cols)
    correlations = {}
    for column in usable_cols:
        corr = train_df[column].corr(clipped_target)
        correlations[column] = 0.0 if pd.isna(corr) else abs(float(corr))
    top_candidates = [
        name
        for name, _ in sorted(correlations.items(), key=lambda item: item[1], reverse=True)[:params.feature_selection.top_candidates]
    ]
    corr_matrix = train_df[top_candidates].corr().abs().fillna(0)
    selected: List[str] = []
    for column in top_candidates:
        if all(corr_matrix.loc[column, keep] <= params.feature_selection.corr_threshold for keep in selected):
            selected.append(column)
        if params.feature_selection.max_features is not None and len(selected) == params.feature_selection.max_features:
            break
    return selected[:params.feature_selection.max_features], p_low, p_high, zero_variance_dropped


def compute_step4(paths: Paths, mode: str, params: PipelineParams) -> Dict[str, object]:
    if extract_features is None:
        raise RuntimeError("当前环境缺少 tsfresh，无法执行步骤4")

    suffix = file_suffix(mode)
    ts_path = paths.ts_features_dir / f"ts_features_{params.window.lookback_days}d{suffix}.parquet"
    holding_path = paths.processed_dir / ("holding_clean_smoke.parquet" if mode == "smoke" else "holding_clean.parquet")
    ts_features = pd.read_parquet(ts_path)
    holding = pd.read_parquet(holding_path)[["sample_id", "signal_date", "target"]].copy()
    sample_ids = np.sort(ts_features["sample_id"].unique())
    kind_count = len([column for column in ts_features.columns if column not in ["sample_id", "time_step"]])
    tsfresh_jobs = _resolve_tsfresh_jobs(mode, params.tsfresh.tsfresh_n_jobs)
    disable_progressbar = mode == "full"
    fc_params = _build_full_tsfresh_fc_parameters(params.tsfresh.fc_profile)
    fc_profile_theoretical_features_per_kind = _count_theoretical_features_per_kind(fc_params)
    fc_profile_details = {
        "fc_profile": params.tsfresh.fc_profile,
        "fc_profile_calculators": len(fc_params),
        "fc_profile_theoretical_features_per_kind": fc_profile_theoretical_features_per_kind,
        "fc_profile_theoretical_features_total": fc_profile_theoretical_features_per_kind * kind_count,
    }
    master_role = str(params.tsfresh.master_library_role)

    with monitor_step(paths, params, 4, mode, "tsfresh_extraction"):
        if master_role == "build":
            return _build_master_library(
                ts_features=ts_features,
                holding=holding,
                params=params,
                paths=paths,
                mode=mode,
                tsfresh_jobs=tsfresh_jobs,
                fc_profile_details=fc_profile_details,
            )

        if master_role == "project":
            return _project_master_library(
                ts_features=ts_features,
                holding=holding,
                params=params,
                paths=paths,
                mode=mode,
                tsfresh_jobs=tsfresh_jobs,
                fc_profile_details=fc_profile_details,
            )

        if params.feature_selection.selection_mode == "none":
            # ── V4.1 pure 模式：全量一次提取，不做特征筛选 ──
            all_merged = _extract_tsfresh(
                ts_features=ts_features,
                sample_ids=sample_ids,
                default_fc_parameters=fc_params,
                chunk_size=params.tsfresh.chunk_size_tsfresh,
                n_jobs=tsfresh_jobs,
                disable_progressbar=True,
                paths=paths,
                suffix=suffix,
                chunk_prefix="tsfresh_all_chunk",
                stage_label="全样本一次提取(pure)",
            )
            all_merged, raw_feature_cols, inf_count, nan_count = _prepare_extracted_feature_frame(
                all_merged,
                "步骤4 tsfresh(pure)",
                fillna=False,
            )

            # 方差=0 删除（安全网，非特征筛选）
            selected, zero_variance_dropped = _select_nonzero_variance_columns(all_merged, raw_feature_cols)
            if not selected:
                raise RuntimeError("未筛选出任何有效特征")

            final_df = all_merged[["sample_id"] + selected].copy()
            final_df = holding.merge(final_df, on="sample_id", how="inner")
            final_df, final_inf, final_nan = replace_inf_and_fill(final_df, "步骤4最终特征表", fillna=False)

            selected_features = sorted(selected)
            selected_path = paths.features_dir / f"selected_features{suffix}.txt"
            _write_selected_features(selected_path, selected_features)

            max_feat_label = "all"
            feature_output_path = paths.features_dir / f"features_selected_{max_feat_label}{suffix}.parquet"
            final_df.to_parquet(feature_output_path, index=False)
            clip_output = []

            checks = {
                "feature_file_exists": feature_output_path.exists(),
                "selected_exists": selected_path.exists(),
                "row_count_matches": int(final_df["sample_id"].nunique()) == int(pd.read_parquet(holding_path)["sample_id"].nunique()),
                "unique_columns": len(final_df.columns) == len(set(final_df.columns)),
                "clean_column_names": all(re.fullmatch(r"[A-Za-z0-9_]+", column) for column in final_df.columns if column != "sample_id"),
                "no_nan_inf": _validate_no_inf(final_df, final_df.columns),
                "selected_count_valid": len(selected_features) > 0,
            }
            return {
                "output_files": [
                    str(selected_path.relative_to(paths.root)).replace("\\", "/"),
                    *clip_output,
                    str(feature_output_path.relative_to(paths.root)).replace("\\", "/"),
                ],
                "checks": checks,
                "details": {
                    "samples": int(final_df["sample_id"].nunique()),
                    "columns": int(len(final_df.columns)),
                    "selected_features": len(selected_features),
                    "tsfresh_jobs": tsfresh_jobs,
                    "output_feature_file": str(feature_output_path.relative_to(paths.root)).replace("\\", "/"),
                    "tsfresh_inf_replaced": inf_count,
                    "tsfresh_nan_filled": 0,
                    "final_inf_replaced": final_inf,
                    "final_nan_filled": 0,
                    "invalid_targets_dropped": 0,
                    "raw_extracted_feature_columns": len(raw_feature_cols),
                    "zero_variance_dropped": zero_variance_dropped,
                    "master_library_role": master_role,
                    **fc_profile_details,
                },
            }

        if mode == "smoke":
            merged = _extract_tsfresh(
                ts_features=ts_features,
                sample_ids=sample_ids,
                default_fc_parameters=fc_params,
                chunk_size=params.tsfresh.chunk_size_tsfresh,
                n_jobs=tsfresh_jobs,
                disable_progressbar=disable_progressbar,
                paths=paths,
                suffix=suffix,
                chunk_prefix="tsfresh_chunk",
                stage_label="全样本候选提取",
            )
            merged = merged.rename_axis("sample_id").reset_index()
            raw_feature_cols = [column for column in merged.columns if column != "sample_id"]
            merged.columns = ["sample_id"] + _sanitize_feature_names(raw_feature_cols)
            merged, inf_count, nan_count = replace_inf_and_fill(merged, "步骤4 tsfresh 特征")

            df = merged.merge(holding, on="sample_id", how="inner")
            invalid_mask = df["target"].isna() | df["target"].isin([np.inf, -np.inf])
            invalid_targets = int(invalid_mask.sum())
            if invalid_targets:
                print(f"丢弃标签异常样本数: {invalid_targets}")
                df = df.loc[~invalid_mask].copy()

            meta_cols = ["sample_id", "signal_date", "target"]
            feature_cols = [column for column in df.columns if column not in meta_cols]
            raw_feature_cols = feature_cols
            selected, zero_variance_dropped = _select_features_smoke(df, feature_cols, params)
            if not selected:
                raise RuntimeError("未筛选出任何有效特征")

            selected_path = paths.features_dir / f"selected_features{suffix}.txt"
            with selected_path.open("w", encoding="utf-8") as handle:
                handle.write("\n".join(selected))
                handle.write("\n")

            final_df = df[["sample_id", "signal_date", "target"] + selected].copy()
            final_df, final_inf, final_nan = replace_inf_and_fill(final_df, "步骤4最终特征表")
        else:
            train_ids = np.sort(
                holding.loc[holding["signal_date"].le(params.time_split.train_end), "sample_id"].to_numpy()
            )
            if len(train_ids) == 0:
                raise RuntimeError("训练集为空，无法执行全量模式特征筛选")

            train_merged = _extract_tsfresh(
                ts_features=ts_features,
                sample_ids=train_ids,
                chunk_size=params.tsfresh.chunk_size_tsfresh,
                n_jobs=tsfresh_jobs,
                disable_progressbar=True,
                paths=paths,
                suffix=suffix,
                chunk_prefix="tsfresh_train_chunk",
                stage_label="训练集候选提取",
                default_fc_parameters=fc_params,
            )
            train_merged = train_merged.rename_axis("sample_id").reset_index()
            train_merged, inf_count, nan_count = replace_inf_and_fill(train_merged, "步骤4 训练集候选特征")

            train_df = train_merged.merge(holding, on="sample_id", how="inner")
            invalid_mask = train_df["target"].isna() | train_df["target"].isin([np.inf, -np.inf])
            invalid_targets = int(invalid_mask.sum())
            if invalid_targets:
                print(f"丢弃标签异常样本数: {invalid_targets}")
                train_df = train_df.loc[~invalid_mask].copy()

            raw_feature_cols = [column for column in train_df.columns if column not in ["sample_id", "signal_date", "target"]]
            selected, p_low, p_high, zero_variance_dropped = _select_features_full(train_df, raw_feature_cols, params)
            clip_path = paths.features_dir / "clip_percentiles.json"
            with clip_path.open("w", encoding="utf-8") as handle:
                json.dump({"p1": p_low, "p99": p_high}, handle, ensure_ascii=False, indent=2)
            if not selected:
                raise RuntimeError("未筛选出任何有效特征")

            selected_feature_names = _sanitize_feature_names(selected)
            selected_mapping = dict(zip(selected, selected_feature_names))
            selected_path = paths.features_dir / f"selected_features{suffix}.txt"
            with selected_path.open("w", encoding="utf-8") as handle:
                handle.write("\n".join(selected_feature_names))
                handle.write("\n")

            selected_kind_parameters = from_columns(selected)
            all_merged = _extract_tsfresh(
                ts_features=ts_features,
                sample_ids=sample_ids,
                chunk_size=params.tsfresh.chunk_size_tsfresh,
                n_jobs=tsfresh_jobs,
                disable_progressbar=True,
                paths=paths,
                suffix=suffix,
                chunk_prefix="tsfresh_selected_chunk",
                stage_label="全样本入选特征提取",
                kind_to_fc_parameters=selected_kind_parameters,
            )
            all_merged = all_merged.rename_axis("sample_id").reset_index()
            missing_selected = [column for column in selected if column not in all_merged.columns]
            if missing_selected:
                raise RuntimeError(f"全样本特征提取缺少入选特征: {missing_selected[:5]}")
            final_df = all_merged[["sample_id"] + selected].rename(columns=selected_mapping)
            final_df, final_inf, final_nan = replace_inf_and_fill(final_df, "步骤4最终特征表")
            final_df = holding.merge(final_df, on="sample_id", how="inner")

        max_feat_label = params.feature_selection.max_features if params.feature_selection.max_features is not None else "all"
        feature_output_path = paths.features_dir / f"features_selected_{max_feat_label}{suffix}.parquet"
        final_df.to_parquet(feature_output_path, index=False)

        if params.feature_selection.max_features is None:
            selected_count_valid = len(selected) > 0
        else:
            selected_count_valid = 0 < len(selected) <= params.feature_selection.max_features

        clip_output = []
        if mode != "smoke":
            clip_output.append(
                str((paths.features_dir / "clip_percentiles.json").relative_to(paths.root)).replace("\\", "/")
            )

        checks = {
            "feature_file_exists": feature_output_path.exists(),
            "selected_exists": selected_path.exists(),
            "row_count_matches": int(final_df["sample_id"].nunique()) == int(pd.read_parquet(holding_path)["sample_id"].nunique()),
            "unique_columns": len(final_df.columns) == len(set(final_df.columns)),
            "clean_column_names": all(re.fullmatch(r"[A-Za-z0-9_]+", column) for column in final_df.columns if column != "sample_id"),
            "no_nan_inf": validate_common_no_nan_inf(final_df, final_df.columns),
            "selected_count_valid": selected_count_valid,
        }
        return {
            "output_files": [
                str(selected_path.relative_to(paths.root)).replace("\\", "/"),
                *clip_output,
                str(feature_output_path.relative_to(paths.root)).replace("\\", "/"),
            ],
            "checks": checks,
            "details": {
                "samples": int(final_df["sample_id"].nunique()),
                "columns": int(len(final_df.columns)),
                "selected_features": int(len(selected)),
                "tsfresh_jobs": tsfresh_jobs,
                "output_feature_file": str(feature_output_path.relative_to(paths.root)).replace("\\", "/"),
                "tsfresh_inf_replaced": inf_count,
                "tsfresh_nan_filled": nan_count,
                "final_inf_replaced": final_inf,
                "final_nan_filled": final_nan,
                "invalid_targets_dropped": invalid_targets,
                "raw_extracted_feature_columns": len(raw_feature_cols),
                "zero_variance_dropped": zero_variance_dropped,
                **fc_profile_details,
            },
        }