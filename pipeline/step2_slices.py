"""步骤2：生成非停牌行情切片与 non_trading_ratio。"""

from __future__ import annotations

import random
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from .config import Paths, PipelineParams
from .utils import chunk_by_ids, file_suffix


def _sample_smoke_holdings(holding: pd.DataFrame, smoke_size: int) -> pd.DataFrame:
    if smoke_size <= 0:
        raise ValueError("--smoke 必须大于 0")
    if smoke_size >= len(holding):
        sampled = holding.copy()
    else:
        sampled_ids = random.sample(holding["sample_id"].tolist(), smoke_size)
        sampled = holding[holding["sample_id"].isin(sampled_ids)].copy()
    return sampled.sort_values("sample_id", kind="mergesort").copy()


def _compute_non_trading_ratio_from_dates(
    first_dt: np.datetime64,
    signal_dt: np.datetime64,
    sample_id: int,
    lookback_days: int,
) -> float:
    calendar_span = int((signal_dt - first_dt) / np.timedelta64(1, "D")) + 1
    if calendar_span <= 0:
        raise ValueError(f"sample_id={sample_id} 的 calendar_span={calendar_span}，切片排序异常")
    value = 1.0 - float(lookback_days) / calendar_span
    return max(0.0, min(1.0, float(value)))


def _build_symbol_lookup(datebar: pd.DataFrame) -> Dict[str, Dict[str, np.ndarray]]:
    datebar = datebar.copy()
    datebar["idxd_dt"] = pd.to_datetime(datebar["idxd"].astype(str), format="%Y%m%d").to_numpy(dtype="datetime64[D]")
    grouped: Dict[str, Dict[str, np.ndarray]] = {}
    value_columns = ["open", "high", "low", "close", "volume"]

    for symbol, frame in datebar.groupby("idxs", sort=False):
        idxd = frame["idxd"].to_numpy(dtype=np.int32, copy=False)
        paused = frame["paused"].to_numpy(dtype=bool, copy=False)
        symbol_data: Dict[str, np.ndarray] = {
            "idxd": idxd,
            "idxd_dt": frame["idxd_dt"].to_numpy(dtype="datetime64[D]", copy=False),
            "paused": paused,
            "valid_positions": np.flatnonzero(~paused),
        }
        for column in value_columns:
            symbol_data[column] = frame[column].to_numpy(copy=False)
        grouped[symbol] = symbol_data

    return grouped


def _build_slice_chunk(
    chunk_holding: pd.DataFrame,
    grouped: Dict[str, Dict[str, np.ndarray]],
    lookback_days: int,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[int], Dict[str, int]]:
    time_steps = np.arange(1, lookback_days + 1, dtype=np.int16)
    slice_arrays: Dict[str, List[np.ndarray]] = {
        "sample_id": [],
        "time_step": [],
        "open": [],
        "high": [],
        "low": [],
        "close": [],
        "volume": [],
    }
    non_trading_rows: List[Dict[str, float]] = []
    invalid_sample_ids: List[int] = []
    invalid_reasons = {"missing_signal_date": 0, "paused_signal_date": 0, "insufficient_history": 0}

    for row in chunk_holding.itertuples(index=False):
        sample_id = int(row.sample_id)
        signal_date = int(row.signal_date)
        symbol_data = grouped.get(row.idxs)
        if symbol_data is None:
            invalid_sample_ids.append(sample_id)
            invalid_reasons["missing_signal_date"] += 1
            continue

        idxd = symbol_data["idxd"]
        signal_position = int(np.searchsorted(idxd, signal_date, side="left"))
        if signal_position >= len(idxd) or int(idxd[signal_position]) != signal_date:
            invalid_sample_ids.append(sample_id)
            invalid_reasons["missing_signal_date"] += 1
            continue

        paused = symbol_data["paused"]
        if bool(paused[signal_position]):
            invalid_sample_ids.append(sample_id)
            invalid_reasons["paused_signal_date"] += 1
            continue

        valid_positions = symbol_data["valid_positions"]
        valid_cut = int(np.searchsorted(valid_positions, signal_position, side="right"))
        if valid_cut < lookback_days:
            invalid_sample_ids.append(sample_id)
            invalid_reasons["insufficient_history"] += 1
            continue

        window_positions = valid_positions[valid_cut - lookback_days:valid_cut]
        first_dt = symbol_data["idxd_dt"][window_positions[0]]
        signal_dt = symbol_data["idxd_dt"][signal_position]
        non_trading_rows.append(
            {
                "sample_id": sample_id,
                "non_trading_ratio": _compute_non_trading_ratio_from_dates(
                    first_dt,
                    signal_dt,
                    sample_id,
                    lookback_days,
                ),
            }
        )

        slice_arrays["sample_id"].append(np.full(lookback_days, sample_id, dtype=np.int64))
        slice_arrays["time_step"].append(time_steps)
        for column in ["open", "high", "low", "close", "volume"]:
            slice_arrays[column].append(symbol_data[column][window_positions])

    if slice_arrays["sample_id"]:
        slices = pd.DataFrame({column: np.concatenate(parts) for column, parts in slice_arrays.items()})
    else:
        slices = pd.DataFrame(columns=["sample_id", "time_step", "open", "high", "low", "close", "volume"])

    non_trading = pd.DataFrame(non_trading_rows)
    return slices, non_trading, invalid_sample_ids, invalid_reasons


def _build_slices_for_holdings(
    holding: pd.DataFrame,
    datebar: pd.DataFrame,
    lookback_days: int,
    chunk_size: int,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[int], Dict[str, int]]:
    selected_symbols = holding["idxs"].unique().tolist()
    datebar = datebar[datebar["idxs"].isin(selected_symbols)].copy()
    grouped = _build_symbol_lookup(datebar)

    chunks = chunk_by_ids(holding["sample_id"].to_numpy(), chunk_size=chunk_size)
    slice_frames: List[pd.DataFrame] = []
    non_trading_frames: List[pd.DataFrame] = []
    invalid_sample_ids: List[int] = []
    invalid_reasons = {"missing_signal_date": 0, "paused_signal_date": 0, "insufficient_history": 0}

    for index, chunk in enumerate(chunks):
        print(f"步骤2 处理分块 {index + 1}/{len(chunks)}，样本数={len(chunk)}")
        chunk_holding = holding[holding["sample_id"].isin(chunk)].copy()
        chunk_slices, chunk_non_trading, chunk_invalid_sample_ids, chunk_invalid_reasons = _build_slice_chunk(
            chunk_holding,
            grouped,
            lookback_days,
        )
        if not chunk_slices.empty:
            slice_frames.append(chunk_slices)
        if not chunk_non_trading.empty:
            non_trading_frames.append(chunk_non_trading)
        invalid_sample_ids.extend(chunk_invalid_sample_ids)
        for reason, count in chunk_invalid_reasons.items():
            invalid_reasons[reason] += count

    slices = pd.concat(slice_frames, ignore_index=True) if slice_frames else pd.DataFrame(columns=["sample_id", "time_step", "open", "high", "low", "close", "volume"])
    non_trading = pd.concat(non_trading_frames, ignore_index=True) if non_trading_frames else pd.DataFrame(columns=["sample_id", "non_trading_ratio"])
    return slices, non_trading, invalid_sample_ids, invalid_reasons


def compute_step2(paths: Paths, mode: str, params: PipelineParams) -> Dict[str, object]:
    holding = pd.read_parquet(paths.processed_dir / "holding_clean.parquet")
    if mode == "smoke":
        holding = _sample_smoke_holdings(holding, params.smoke_size)
    else:
        holding = holding.sort_values("sample_id", kind="mergesort").copy()

    datebar = pd.read_parquet(paths.processed_dir / "datebar.parquet")
    lookback_days = params.window.lookback_days
    slices, non_trading, invalid_sample_ids, invalid_reasons = _build_slices_for_holdings(
        holding,
        datebar,
        lookback_days,
        params.window.chunk_size_slices,
    )

    valid_holding = holding[~holding["sample_id"].isin(invalid_sample_ids)].copy()
    valid_holding = valid_holding.sort_values("sample_id", kind="mergesort").copy()

    suffix = file_suffix(mode)
    slices_path = paths.slices_dir / f"slices_{lookback_days}d{suffix}.parquet"
    non_trading_path = paths.slices_dir / f"non_trading_ratio{suffix}.csv"
    holding_path = paths.processed_dir / ("holding_clean_smoke.parquet" if mode == "smoke" else "holding_clean.parquet")

    slices.to_parquet(slices_path, index=False)
    non_trading.to_csv(non_trading_path, index=False)
    valid_holding.to_parquet(holding_path, index=False)

    grouped_counts = slices.groupby("sample_id").size() if not slices.empty else pd.Series(dtype=int)
    checks = {
        "slices_exists": slices_path.exists(),
        "non_trading_exists": non_trading_path.exists(),
        "holding_exists": holding_path.exists(),
        "slice_columns": list(slices.columns) == ["sample_id", "time_step", "open", "high", "low", "close", "volume"],
        "exactly_60_rows_per_sample": bool(not grouped_counts.empty and grouped_counts.eq(lookback_days).all()),
        "slice_no_missing": bool(not slices.isna().any().any()),
        "non_trading_range": bool(non_trading["non_trading_ratio"].between(0, 1).all()) if not non_trading.empty else False,
        "holding_matches_valid_samples": int(valid_holding["sample_id"].nunique()) == int(slices["sample_id"].nunique()),
    }

    return {
        "output_files": [
            str(slices_path.relative_to(paths.root)).replace("\\", "/"),
            str(non_trading_path.relative_to(paths.root)).replace("\\", "/"),
            str(holding_path.relative_to(paths.root)).replace("\\", "/"),
        ],
        "checks": checks,
        "details": {
            "input_samples": int(len(holding)),
            "valid_samples": int(valid_holding["sample_id"].nunique()),
            "dropped_samples": int(len(invalid_sample_ids)),
            "invalid_reasons": invalid_reasons,
            "slice_rows": int(len(slices)),
        },
    }