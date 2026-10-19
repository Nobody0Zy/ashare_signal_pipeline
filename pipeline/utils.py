"""跨步骤复用的通用工具函数。"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd

from .config import Paths


def file_suffix(mode: str) -> str:
    return "_smoke" if mode == "smoke" else ""


def log_step_start(step: int, mode: str, operation: str) -> None:
    mode_text = "冒烟" if mode == "smoke" else "全量"
    print(f"步骤 {step} [{mode_text}] 开始：{operation}")


def replace_inf_and_fill(df: pd.DataFrame, label: str, fillna: bool = True) -> Tuple[pd.DataFrame, int, int]:
    numeric = df.select_dtypes(include=[np.number]).columns
    if len(numeric) == 0:
        return df, 0, 0
    inf_mask = np.isinf(df[numeric].to_numpy())
    inf_count = int(inf_mask.sum())
    if inf_count:
        df.loc[:, numeric] = df[numeric].replace([np.inf, -np.inf], np.nan)
    nan_count = int(df[numeric].isna().sum().sum())
    if nan_count and fillna:
        df.loc[:, numeric] = df[numeric].fillna(0)
    print(f"{label}: inf/-inf 替换数量={inf_count}, NaN 填充数量={nan_count}")
    return df, inf_count, nan_count


def validate_common_no_nan_inf(df: pd.DataFrame, columns: Sequence[str]) -> bool:
    subset = df.loc[:, list(columns)]
    if subset.isna().any().any():
        return False
    numeric = subset.select_dtypes(include=[np.number])
    if not numeric.empty and np.isinf(numeric.to_numpy()).any():
        return False
    return True


def chunk_by_ids(sample_ids: Sequence[int], chunk_size: int) -> List[np.ndarray]:
    sample_ids = np.asarray(sample_ids)
    if len(sample_ids) == 0:
        return []
    group_count = max(1, math.ceil(len(sample_ids) / chunk_size))
    return [chunk for chunk in np.array_split(sample_ids, group_count) if len(chunk) > 0]


def load_holding_clean(paths: Paths, mode: str) -> pd.DataFrame:
    if mode == "smoke":
        file_path = paths.processed_dir / "holding_clean_smoke.parquet"
        if file_path.exists():
            return pd.read_parquet(file_path)
    return pd.read_parquet(paths.processed_dir / "holding_clean.parquet")