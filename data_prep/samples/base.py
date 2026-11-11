"""采样基类与上下文接口。"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


class SampleContext:
    """采样上下文：仅提供截至当日的历史序列访问，禁止前瞻。

    由 build_samples 构造，所有采样方法通过此接口取数，不得访问原始 bars 数组。
    """

    def __init__(self, sym_arrays: Dict[str, dict]) -> None:
        self._sym_arrays = sym_arrays

    def history(self, symbol: str, pos: int, n: int) -> np.ndarray:
        """返回 symbol 在 pos（含）及之前 n 个非停牌 bar 的 close 数组（最多 n 行）。"""
        arr = self._sym_arrays.get(symbol)
        if arr is None:
            return np.array([], dtype="float32")
        close = arr["close"]
        start = max(0, pos - n + 1)
        return close[start : pos + 1]

    def index_of(self, symbol: str, idxd: int) -> int:
        """返回 idxd 在该 symbol 非停牌序列中的位置；不存在返回 -1。"""
        arr = self._sym_arrays.get(symbol)
        if arr is None:
            return -1
        return arr["pos_map"].get(int(idxd), -1)


class SampleDefinition:
    """采样方法基类。子类必须声明 name、params_key、default_params 并实现 generate。"""

    name: str = ""
    params_key: str = "params"
    default_params: dict = {}

    def generate(
        self,
        bars: Dict[str, dict],
        params: dict,
        ctx: SampleContext,
    ) -> pd.DataFrame:
        """执行采样，返回仅含 (idxs, idxd) 两列的 DataFrame。

        参数说明：
            bars:   per-symbol 非停牌数组（同 build_symbol_arrays 返回值）
            params: 采样方法参数（来自 data_prep.samples.params）
            ctx:    SampleContext（受控数据入口）
        """
        raise NotImplementedError(f"{self.__class__.__name__}.generate 未实现")
