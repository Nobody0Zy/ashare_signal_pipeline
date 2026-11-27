"""加载 dateBar、规范化与逐 symbol 数组构建。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from .ids import derive_datebar_fingerprint


def load_datebar(path: str | Path) -> Tuple[pd.DataFrame, str]:
    """加载 dateBar.pkl，规范化后返回 (df, fingerprint)。

    规范化：reset_index → 列名小写 → idxd int32 → 按 (idxs, idxd) mergesort。
    fingerprint 在规范化前计算（基于原始文件字节 + 加载后的统计量）。
    """
    path = Path(path)
    fingerprint = derive_datebar_fingerprint(path)

    df = pd.read_pickle(path)
    df = df.reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    df["idxd"] = df["idxd"].astype("int32")
    df = df.sort_values(["idxs", "idxd"], kind="mergesort").reset_index(drop=True)
    return df, fingerprint


def build_symbol_arrays(df: pd.DataFrame) -> Dict[str, dict]:
    """为每个 symbol 构建非停牌 close 序列与位置映射（供采样使用）。

    返回: {sym: {"close": np.ndarray, "idxd": np.ndarray, "pos_map": {idxd: pos}}}
    """
    result: Dict[str, dict] = {}
    for sym, group in df.groupby("idxs", sort=False):
        g = group.reset_index(drop=True)
        non_paused_mask = g["paused"].ne(1)
        np_rows = g[non_paused_mask].reset_index(drop=True)
        close_arr = np_rows["close"].to_numpy(dtype="float32")
        idxd_arr = np_rows["idxd"].to_numpy(dtype="int32")
        pos_map: dict[int, int] = {int(d): i for i, d in enumerate(idxd_arr)}
        result[str(sym)] = {"close": close_arr, "idxd": idxd_arr, "pos_map": pos_map}
    return result
