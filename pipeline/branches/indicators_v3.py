"""步骤3 v3 扩展技术指标。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import PipelineParams


def _groupby_ewm_span(series: pd.Series, sample_ids: pd.Series, span: int) -> pd.Series:
    return series.groupby(sample_ids, sort=False).transform(lambda values: values.ewm(span=span).mean())


def _groupby_ewm_alpha(series: pd.Series, sample_ids: pd.Series, alpha: float) -> pd.Series:
    return series.groupby(sample_ids, sort=False).transform(lambda values: values.ewm(alpha=alpha).mean())


def _groupby_rolling_min(series: pd.Series, sample_ids: pd.Series, window: int) -> pd.Series:
    return (
        series.groupby(sample_ids, sort=False)
        .rolling(window)
        .min()
        .reset_index(level=0, drop=True)
    )


def _groupby_rolling_max(series: pd.Series, sample_ids: pd.Series, window: int) -> pd.Series:
    return (
        series.groupby(sample_ids, sort=False)
        .rolling(window)
        .max()
        .reset_index(level=0, drop=True)
    )


def append_enriched_indicators(frame: pd.DataFrame, params: PipelineParams) -> pd.DataFrame:
    enriched = frame.copy()
    sample_ids = enriched["sample_id"]
    close = enriched["close"]
    high = enriched["high"]
    low = enriched["low"]
    volume = enriched["volume"]
    indicators = params.indicators

    ema_fast = _groupby_ewm_span(close, sample_ids, indicators.macd_fast)
    ema_slow = _groupby_ewm_span(close, sample_ids, indicators.macd_slow)
    macd_line = ema_fast - ema_slow
    macd_signal = _groupby_ewm_span(macd_line, sample_ids, indicators.macd_signal)
    enriched["macd_hist"] = macd_line - macd_signal

    boll_band = enriched["boll_upper"] - enriched["boll_lower"]
    enriched["boll_percent_b"] = (close - enriched["boll_lower"]) / boll_band.replace(0, np.nan)

    close_diff = close.groupby(sample_ids, sort=False).diff()
    signed_volume = np.where(close_diff.gt(0), volume, np.where(close_diff.lt(0), -volume, 0.0))
    signed_volume = pd.Series(signed_volume, index=enriched.index, dtype=float)
    enriched["obv"] = signed_volume.groupby(sample_ids, sort=False).cumsum()

    lowest_low = _groupby_rolling_min(low, sample_ids, indicators.kdj_period)
    highest_high = _groupby_rolling_max(high, sample_ids, indicators.kdj_period)
    rsv_denominator = (highest_high - lowest_low).replace(0, np.nan)
    rsv = (close - lowest_low) / rsv_denominator * 100.0
    enriched["kdj_k"] = _groupby_ewm_alpha(rsv, sample_ids, 1.0 / indicators.kdj_k_period)
    enriched["kdj_d"] = _groupby_ewm_alpha(enriched["kdj_k"], sample_ids, 1.0 / indicators.kdj_d_period)
    enriched["kdj_j"] = 3.0 * enriched["kdj_k"] - 2.0 * enriched["kdj_d"]
    return enriched