"""步骤3：基于切片计算手工技术指标。"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .config import Paths, PipelineParams
from .utils import file_suffix, replace_inf_and_fill, validate_common_no_nan_inf


def _compute_rsi(series: pd.Series, period: int) -> pd.Series:
    diff = series.diff()
    gain = diff.clip(lower=0)
    loss = (-diff).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _compute_atr(frame: pd.DataFrame, period: int) -> pd.Series:
    prev_close = frame["close"].shift(1)
    tr_components = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    tr = tr_components.max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _groupby_rolling_mean(series: pd.Series, sample_ids: pd.Series, window: int) -> pd.Series:
    return (
        series.groupby(sample_ids, sort=False)
        .rolling(window, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )


def _groupby_rolling_std(series: pd.Series, sample_ids: pd.Series, window: int) -> pd.Series:
    return (
        series.groupby(sample_ids, sort=False)
        .rolling(window, min_periods=1)
        .std()
        .reset_index(level=0, drop=True)
    )


def _groupby_ewm_mean(series: pd.Series, sample_ids: pd.Series, period: int) -> pd.Series:
    return (
        series.groupby(sample_ids, sort=False)
        .ewm(alpha=1 / period, adjust=False)
        .mean()
        .reset_index(level=0, drop=True)
    )


def _compute_indicators_vectorized(slices: pd.DataFrame, params: PipelineParams) -> pd.DataFrame:
    ind = params.indicators
    frame = slices.sort_values(["sample_id", "time_step"], kind="mergesort").copy()
    sample_ids = frame["sample_id"]
    close = frame["close"]
    volume = frame["volume"]
    frame["return_1d"] = close.groupby(sample_ids, sort=False).pct_change()
    for window in ind.ma_windows:
        frame[f"ma_{window}"] = _groupby_rolling_mean(close, sample_ids, window)
    boll_mean = _groupby_rolling_mean(close, sample_ids, ind.boll_period)
    frame["boll_std"] = _groupby_rolling_std(close, sample_ids, ind.boll_period)
    frame["boll_upper"] = boll_mean + ind.boll_std_mult * frame["boll_std"]
    frame["boll_lower"] = boll_mean - ind.boll_std_mult * frame["boll_std"]

    diff = close.groupby(sample_ids, sort=False).diff()
    gain = diff.clip(lower=0)
    loss = (-diff).clip(lower=0)
    avg_gain = _groupby_ewm_mean(gain, sample_ids, ind.rsi_period)
    avg_loss = _groupby_ewm_mean(loss, sample_ids, ind.rsi_period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    frame[f"rsi_{ind.rsi_period}"] = 100 - 100 / (1 + rs)

    prev_close = close.groupby(sample_ids, sort=False).shift(1)
    tr_components = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    tr = tr_components.max(axis=1)
    frame[f"atr_{ind.atr_period}"] = _groupby_ewm_mean(tr, sample_ids, ind.atr_period)

    vol_ma = _groupby_rolling_mean(volume, sample_ids, ind.volume_ma_period).replace(0, np.nan)
    frame["volume_ratio"] = volume / vol_ma
    frame[f"momentum_{ind.momentum_period}"] = close / close.groupby(sample_ids, sort=False).shift(ind.momentum_period) - 1
    frame[f"volatility_{ind.volatility_period}"] = _groupby_rolling_std(
        frame["return_1d"],
        sample_ids,
        ind.volatility_period,
    )
    return frame


def compute_step3(paths: Paths, mode: str, params: PipelineParams) -> Dict[str, object]:
    suffix = file_suffix(mode)
    slices_path = paths.slices_dir / f"slices_{params.window.lookback_days}d{suffix}.parquet"
    slices = pd.read_parquet(slices_path)
    if params.indicators.skip_indicators:
        # ── V4.1 pure 模式：多周期 OHLC 编码 ──
        features = _build_multi_lookback_ohlc(slices, params)
        features, inf_count, nan_count = replace_inf_and_fill(
            features, "步骤3 多周期OHLC", fillna=False
        )
        # fillna=False: inf→NaN（始终执行），NaN 不填 0——短 lookback 前导行 NaN 是合法结构特征
        columns = ["sample_id", "time_step"] + _multi_lookback_column_names(params)
        features = features[columns].copy()
        output_path = paths.ts_features_dir / f"ts_features_{params.window.lookback_days}d{suffix}.parquet"
        features.to_parquet(output_path, index=False)
        checks = {
            "features_exists": output_path.exists(),
            "features_columns": list(features.columns) == columns,
            "no_inf": not np.isinf(features.select_dtypes(include=[np.number]).to_numpy()).any(),
        }
        return {
            "output_files": [str(output_path.relative_to(paths.root)).replace("\\", "/")],
            "checks": checks,
            "details": {
                "rows": int(len(features)),
                "samples": int(features["sample_id"].nunique()),
                "inf_replaced": inf_count,
                "nan_filled": nan_count,
            },
        }

    # ── 现有逻辑：手工技术指标计算（完全不变）──
    features = _compute_indicators_vectorized(slices, params) if not slices.empty else pd.DataFrame()
    if params.indicators.profile == "v3_enriched" and not features.empty:
        from .branches.indicators_v3 import append_enriched_indicators

        features = append_enriched_indicators(features, params)

    columns: List[str] = ["sample_id", "time_step", "return_1d"]
    columns.extend([f"ma_{window}" for window in params.indicators.ma_windows])
    columns.extend(
        [
            "boll_std",
            "boll_upper",
            "boll_lower",
            f"rsi_{params.indicators.rsi_period}",
            f"atr_{params.indicators.atr_period}",
            "volume_ratio",
            f"momentum_{params.indicators.momentum_period}",
            f"volatility_{params.indicators.volatility_period}",
        ]
    )
    if params.indicators.profile == "v3_enriched":
        columns.extend(
            [
                "macd_hist",
                "boll_percent_b",
                "obv",
                "kdj_k",
                "kdj_d",
                "kdj_j",
            ]
        )
    features = features[columns].copy()
    features, inf_count, nan_count = replace_inf_and_fill(features, "步骤3技术指标")
    output_path = paths.ts_features_dir / f"ts_features_{params.window.lookback_days}d{suffix}.parquet"
    features.to_parquet(output_path, index=False)
    checks = {
        "features_exists": output_path.exists(),
        "features_columns": list(features.columns) == columns,
        "no_nan_inf": validate_common_no_nan_inf(features, features.columns),
    }
    return {
        "output_files": [str(output_path.relative_to(paths.root)).replace("\\", "/")],
        "checks": checks,
        "details": {
            "rows": int(len(features)),
            "samples": int(features["sample_id"].nunique()),
            "inf_replaced": inf_count,
            "nan_filled": nan_count,
        },
    }


def _build_multi_lookback_ohlc(slices: pd.DataFrame, params: PipelineParams) -> pd.DataFrame:
    """对每个 lookback_period（降序），取每组尾部 L 行做 shift→NaN 编码。

    算法（参照 experiment_design.md §6.2）：
    - periods 降序排列（最长周期在前）
    - 对每个 period L，取每个 sample_id 组的尾部 L 行，向前 shift 使前导行为 NaN
    - 列名统一为 {col}_L{lb} 格式（包括最大周期）
    """
    periods = sorted(params.indicators.lookback_periods, reverse=True)
    columns = params.indicators.lookback_columns
    result = slices[["sample_id", "time_step"]].copy()
    for lb in periods:
        for col in columns:
            col_name = f"{col}_L{lb}"
            # 对每个 sample_id 组：保留尾部 lb 行，shift 到组内对应位置
            shifted = (
                slices.groupby("sample_id", sort=False)[col]
                .transform(lambda x: x.shift(len(x) - lb))
            )
            result[col_name] = shifted
    return result


def _multi_lookback_column_names(params: PipelineParams) -> List[str]:
    """返回多周期 OHLC 编码的列名列表，与 _build_multi_lookback_ohlc 的输出一致。"""
    return [
        f"{col}_L{lb}"
        for col in params.indicators.lookback_columns
        for lb in sorted(params.indicators.lookback_periods, reverse=True)
    ]