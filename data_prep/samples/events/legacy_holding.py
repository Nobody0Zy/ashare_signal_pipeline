"""内置采样：旧 holding_file 样本集复现（§4.3）。

用途：把旧样本集重贴新标签（对照实验 0B），保证与旧路径完全相同。
强制约束（V10/V14）：max_samples/date_range_* 必须为 null。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd

from ..base import SampleContext, SampleDefinition
from ..registry import register_sample


@register_sample
class LegacyHolding(SampleDefinition):
    """从 data.holding_file 重建旧路径样本集（§4.3）。

    过滤条件与 step1_clean._clean_holding 完全一致：
        is_closed == True
        entry_execution_price > 0
        holding_return.notna()
    """

    name = "legacy_holding"
    params_key = "params"
    default_params: dict = {}

    def generate(
        self,
        bars: Dict[str, dict],
        params: dict,
        ctx: SampleContext,
    ) -> pd.DataFrame:
        holding_file = params.get("holding_file", "")
        if not holding_file:
            raise ValueError("legacy_holding 需要 params['holding_file'] 指向 raw holding 路径")
        path = Path(holding_file)
        if not path.exists():
            raise FileNotFoundError(f"holding 文件不存在: {path}")

        holding = pd.read_parquet(path)
        required = {"is_closed", "entry_execution_price", "holding_return", "idxs", "signal_date"}
        missing = required - set(holding.columns)
        if missing:
            raise ValueError(f"holding 文件缺少必需列: {sorted(missing)}")

        holding = holding[
            holding["is_closed"].eq(True)
            & holding["entry_execution_price"].gt(0)
            & holding["holding_return"].notna()
        ].copy()

        result = holding[["idxs", "signal_date"]].copy()
        result = result.rename(columns={"signal_date": "idxd"})
        result["idxd"] = result["idxd"].astype(int)
        return result.reset_index(drop=True)
