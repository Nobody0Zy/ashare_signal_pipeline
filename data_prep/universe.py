"""样本池过滤：前缀排除与最小历史长度。"""

from __future__ import annotations

from typing import Dict, Set

import pandas as pd


def filter_universe(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """过滤 dateBar，返回满足宇宙约束的子集。

    步骤：
    1. 排除 exclude_prefixes 中的前缀（指数）；
    2. 保留非停牌天数 >= min_history_days 的 symbol。
    """
    exclude_prefixes: list[str] = params.get("exclude_prefixes", [])
    min_history_days: int = int(params.get("min_history_days", 60))

    # 前缀过滤
    if exclude_prefixes:
        mask = pd.Series(False, index=df.index)
        for prefix in exclude_prefixes:
            mask = mask | df["idxs"].str.startswith(prefix)
        df = df[~mask]

    # 最小历史长度（非停牌天数）
    non_paused = df[df["paused"].ne(1)]
    sym_counts = non_paused.groupby("idxs").size()
    valid_syms: Set[str] = set(sym_counts[sym_counts >= min_history_days].index)
    df = df[df["idxs"].isin(valid_syms)]

    return df.reset_index(drop=True)


def valid_positions(sym_arrays: Dict[str, dict]) -> Dict[str, int]:
    """返回每个 symbol 可用的非停牌总天数。"""
    return {sym: len(arr["idxd"]) for sym, arr in sym_arrays.items()}
