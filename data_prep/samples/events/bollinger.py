"""内置采样：布林带向上突破事件（§4.2）。"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from ..base import SampleContext, SampleDefinition
from ..registry import register_sample


def _apply_max_samples(
    df: pd.DataFrame, max_samples: int, total_rows: int
) -> tuple[pd.DataFrame, dict]:
    """按年等距抽样将 df 裁剪到 max_samples（§4.2 分年等距抽样算法）。

    返回 (filtered_df, year_quota_dict)。
    year_quota_dict key 为年份字符串。
    """
    M = max_samples
    R = total_rows
    df = df.copy()
    df["_year"] = df["idxd"] // 10000
    year_counts: Dict[int, int] = df.groupby("_year").size().to_dict()
    years: List[int] = sorted(year_counts)

    # 1) 年度配额（最大余数法）
    quotas: Dict[int, int] = {y: int(M * year_counts[y]) // R for y in years}
    remainders: Dict[int, int] = {y: M * year_counts[y] - quotas[y] * R for y in years}
    deficit = M - sum(quotas.values())
    sorted_years = sorted(years, key=lambda y: (-remainders[y], y))
    for y in sorted_years[:deficit]:
        quotas[y] += 1

    # 2) 组内等距选取
    selected: List[pd.DataFrame] = []
    for y in years:
        q = quotas.get(y, 0)
        if q == 0:
            continue
        year_df = df[df["_year"] == y].sort_values(["idxs", "idxd"]).reset_index(drop=True)
        n = len(year_df)
        if q >= n:
            selected.append(year_df)
        else:
            indices: set[int] = {int(i * n / q) for i in range(q)}
            if len(indices) < q:
                remaining = sorted(set(range(n)) - indices, reverse=True)
                indices.update(remaining[: q - len(indices)])
            selected.append(year_df.iloc[sorted(indices)].reset_index(drop=True))

    result = pd.concat(selected, ignore_index=True) if selected else pd.DataFrame(columns=df.columns)
    result = result.drop(columns=["_year"])
    year_quota_dict = {str(y): int(q) for y, q in quotas.items() if q > 0}
    return result, year_quota_dict


@register_sample
class BollingerBreakout(SampleDefinition):
    """布林带向上突破采样（§4.2）。

    事件：收盘价向上穿越布林带上轨，要求前一日上轨非 NaN。
    价格已后复权，不乘 xs。
    """

    name = "bollinger_breakout"
    params_key = "params"
    default_params = {"ma_period": 20.0, "std_mult": 2.0}

    def generate(
        self,
        bars: Dict[str, dict],
        params: dict,
        ctx: SampleContext,
    ) -> pd.DataFrame:
        ma_period = int(params.get("ma_period", 20))
        std_mult = float(params.get("std_mult", 2.0))

        events: list[dict] = []
        for sym, arr in bars.items():
            close = arr["close"]
            idxd_arr = arr["idxd"]
            n = len(close)
            if n < ma_period + 1:
                continue
            close_s = pd.Series(close, dtype="float64")
            ma = close_s.rolling(ma_period).mean()
            sd = close_s.rolling(ma_period).std(ddof=1)
            upper = ma + std_mult * sd

            for t in range(ma_period, n):
                if np.isnan(upper.iloc[t - 1]) or np.isnan(upper.iloc[t]):
                    continue
                if close[t] > upper.iloc[t] and close[t - 1] <= upper.iloc[t - 1]:
                    events.append({"idxs": sym, "idxd": int(idxd_arr[t])})

        if not events:
            return pd.DataFrame(columns=["idxs", "idxd"])
        return pd.DataFrame(events)
