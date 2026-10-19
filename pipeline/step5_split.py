"""步骤5：合并特征并划分训练/验证/测试集。"""

from __future__ import annotations

import json
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

from .config import Paths, PipelineParams
from .state import load_state
from .utils import file_suffix, replace_inf_and_fill, validate_common_no_nan_inf


def _split_smoke_dataset(df: pd.DataFrame, params: PipelineParams) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    shuffled = df.sample(frac=1.0, random_state=params.seed).copy()
    n = len(shuffled)
    train_end = int(n * params.split.smoke_train_ratio)
    val_end = train_end + int(n * params.split.smoke_val_ratio)
    return shuffled.iloc[:train_end].copy(), shuffled.iloc[train_end:val_end].copy(), shuffled.iloc[val_end:].copy()


def _validate_split_columns(frames: Sequence[pd.DataFrame]) -> bool:
    if not frames:
        return False
    reference = list(frames[0].columns)
    return all(list(frame.columns) == reference for frame in frames)


def _check_buffer(datebar: pd.DataFrame, start_date: int, end_date: int, min_days: int) -> int:
    mask = datebar["idxd"].gt(start_date) & datebar["idxd"].lt(end_date) & datebar["paused"].eq(False)
    trading_days = int(datebar.loc[mask, "idxd"].nunique())
    if trading_days < min_days:
        raise ValueError(f"缓冲区 {start_date} 到 {end_date} 之间仅有 {trading_days} 个交易日，不足{min_days}！")
    print(f"缓冲区 {start_date} -> {end_date}: {trading_days} 个交易日，通过校验。")
    return trading_days


def compute_step5(paths: Paths, mode: str, params: PipelineParams) -> Dict[str, object]:
    suffix = file_suffix(mode)
    manual = params.manual_features
    state = load_state(paths, mode)
    step4_feature_file = state["steps"]["step_4"]["details"]["output_feature_file"]
    feature_path = paths.root / step4_feature_file
    holding_path = paths.processed_dir / ("holding_clean_smoke.parquet" if mode == "smoke" else "holding_clean.parquet")
    non_trading_path = paths.slices_dir / f"non_trading_ratio{suffix}.csv"

    df = pd.read_parquet(feature_path)
    holding = pd.read_parquet(holding_path)

    join_cols = ["sample_id"]
    extra_cols: List[str] = []
    if manual.include_signal_date_selection_score and "signal_date_selection_score" in holding.columns:
        extra_cols.append("signal_date_selection_score")
    if extra_cols:
        df = df.merge(holding[join_cols + extra_cols], on="sample_id", how="left")

    if manual.include_non_trading_ratio:
        non_trading = pd.read_csv(non_trading_path)
        df = df.merge(non_trading[["sample_id", "non_trading_ratio"]], on="sample_id", how="left")

    if "signal_date_selection_score" in df.columns:
        df["signal_date_selection_score"] = df["signal_date_selection_score"].fillna(manual.selection_score_fill_value)
    if manual.include_non_trading_ratio:
        df["non_trading_ratio"] = df["non_trading_ratio"].fillna(manual.non_trading_ratio_fill_value)
    if manual.include_date:
        df["date"] = (df["signal_date"] % 100).astype(int)
    if manual.include_year:
        df["year"] = (df["signal_date"] // 10000).astype(int)
    if manual.include_month:
        df["month"] = ((df["signal_date"] % 10000) // 100).astype(int)
    df["target_raw"] = df["target"]

    if mode != "smoke" and params.feature_selection.selection_mode != "none":
        clip_path = paths.features_dir / "clip_percentiles.json"
        with clip_path.open("r", encoding="utf-8") as handle:
            clip_values = json.load(handle)
        df["target"] = df["target"].clip(lower=float(clip_values["p1"]), upper=float(clip_values["p99"]))

    df, inf_count, nan_count = replace_inf_and_fill(df, "步骤5最终清洗")
    target_invalid = df["target"].isna() | df["target"].isin([np.inf, -np.inf])
    dropped_target_rows = int(target_invalid.sum())
    if dropped_target_rows:
        df = df.loc[~target_invalid].copy()

    if mode == "smoke":
        train_df, val_df, test_df = _split_smoke_dataset(df, params)
        buffer_check_status = "smoke_not_applicable"
        train_val_buffer_days = None
        val_test_buffer_days = None
    else:
        if params.time_split.enforce_buffer:
            datebar = pd.read_parquet(paths.processed_dir / "datebar.parquet", columns=["idxd", "paused"])
            train_val_buffer_days = _check_buffer(
                datebar,
                params.time_split.train_end,
                params.time_split.val_start,
                params.time_split.buffer_trading_days,
            )
            val_test_buffer_days = _check_buffer(
                datebar,
                params.time_split.val_end,
                params.time_split.test_start,
                params.time_split.buffer_trading_days,
            )
            buffer_check_status = "enforced"
        else:
            print("步骤5 full 模式 enforce_buffer=false，跳过缓冲带校验。")
            train_val_buffer_days = None
            val_test_buffer_days = None
            buffer_check_status = "skipped"
        train_df = df[df["signal_date"].le(params.time_split.train_end)].copy()
        val_df = df[df["signal_date"].between(params.time_split.val_start, params.time_split.val_end)].copy()
        test_df = df[df["signal_date"].ge(params.time_split.test_start)].copy()

    meta_cols = ["sample_id", "signal_date", "target", "target_raw"]
    feature_cols = sorted([column for column in df.columns if column not in meta_cols])
    final_cols = meta_cols + feature_cols
    train_df = train_df[final_cols].copy()
    val_df = val_df[final_cols].copy()
    test_df = test_df[final_cols].copy()

    train_path = paths.splits_dir / f"train{suffix}.parquet"
    val_path = paths.splits_dir / f"val{suffix}.parquet"
    test_path = paths.splits_dir / f"test{suffix}.parquet"
    train_df.to_parquet(train_path, index=False)
    val_df.to_parquet(val_path, index=False)
    test_df.to_parquet(test_path, index=False)

    total_rows = len(train_df) + len(val_df) + len(test_df)
    checks = {
        "split_files_exist": train_path.exists() and val_path.exists() and test_path.exists(),
        "columns_aligned": _validate_split_columns([train_df, val_df, test_df]),
        "has_target_raw": "target_raw" in train_df.columns and "target_raw" in val_df.columns and "target_raw" in test_df.columns,
        "no_nan_inf": all(validate_common_no_nan_inf(frame, frame.columns) for frame in [train_df, val_df, test_df]),
        "smoke_ratio_reasonable": mode != "smoke" or (
            abs(len(train_df) / total_rows - params.split.smoke_train_ratio) <= 0.02
            and abs(len(val_df) / total_rows - params.split.smoke_val_ratio) <= 0.02
            and abs(len(test_df) / total_rows - (1.0 - params.split.smoke_train_ratio - params.split.smoke_val_ratio)) <= 0.02
        ),
    }
    if buffer_check_status == "enforced":
        checks["buffer_enforced"] = True
    elif buffer_check_status == "skipped":
        checks["buffer_skipped"] = True
    else:
        checks["buffer_smoke_not_applicable"] = True
    return {
        "output_files": [
            str(train_path.relative_to(paths.root)).replace("\\", "/"),
            str(val_path.relative_to(paths.root)).replace("\\", "/"),
            str(test_path.relative_to(paths.root)).replace("\\", "/"),
        ],
        "checks": checks,
        "details": {
            "train_rows": int(len(train_df)),
            "val_rows": int(len(val_df)),
            "test_rows": int(len(test_df)),
            "manual_features": [
                column
                for column in ["date", "year", "month", "non_trading_ratio", "signal_date_selection_score"]
                if column in df.columns
            ],
            "inf_replaced": inf_count,
            "nan_filled": nan_count,
            "target_rows_dropped": dropped_target_rows,
            "buffer_check_status": buffer_check_status,
            "buffer_trading_days_required": int(params.time_split.buffer_trading_days),
            "buffer_train_val_days": train_val_buffer_days,
            "buffer_val_test_days": val_test_buffer_days,
        },
    }