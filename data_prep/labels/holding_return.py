"""内置标签：target_holding_return（§4.8，诊断专用）。

从 data.holding_file（raw）按 (idxs, signal_date) 对齐 holding_return。
前置过滤与 step1 一致：is_closed=True, entry_execution_price>0, holding_return 非空。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .base import LabelContext, LabelDefinition
from .registry import register_label


def _derive_holding_fingerprint(holding_path: str) -> str:
    """计算 holding 文件指纹（§3.4）。"""
    path = Path(holding_path)
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    sha = h.hexdigest()[:12]

    raw_df = pd.read_parquet(path, columns=["signal_date"])
    rows = len(raw_df)
    min_sd = int(raw_df["signal_date"].min())
    max_sd = int(raw_df["signal_date"].max())
    return f"{sha}_{rows}_{min_sd}_{max_sd}"


@register_label
class HoldingReturnDefinition(LabelDefinition):
    """旧持仓收益率标签（诊断用）。"""

    name_pattern: str = r"^target_holding_return$"
    params_key: str = "holding_return"
    spec_version: int = 1
    default_params: dict = {}

    def label_names(self, params: dict) -> List[str]:
        return ["target_holding_return"]

    def horizon_of(self, name: str, params: dict) -> Optional[int]:
        return None

    def prepare_params(self, config_dict: dict, raw_params: dict) -> dict:
        """注入 holding 文件路径与指纹（内部参数，以 _ 开头）。"""
        p = dict(raw_params)
        holding_path = config_dict.get("data", {}).get("holding_file", "")
        p["_holding_file"] = str(holding_path)
        p["_holding_fingerprint"] = _derive_holding_fingerprint(str(holding_path)) if holding_path else ""
        return p

    def compute(
        self,
        samples: pd.DataFrame,
        ctx: LabelContext,
        params: dict,
    ) -> Dict[str, pd.DataFrame]:
        holding_path = str(params.get("_holding_file", ""))
        if not holding_path or not Path(holding_path).exists():
            raise FileNotFoundError(
                f"target_holding_return: 找不到 holding 文件: {holding_path!r}；"
                "请检查配置 data.holding_file"
            )

        # 加载并过滤 holding（与 step1._clean_holding 一致）
        holding_df = pd.read_parquet(holding_path)
        holding_df = holding_df[
            holding_df["is_closed"].eq(True)
            & holding_df["entry_execution_price"].gt(0)
            & holding_df["holding_return"].notna()
        ].copy()
        holding_df["signal_date"] = holding_df["signal_date"].astype("int64")

        # 向量化对齐：left join on (idxs, signal_date)
        n = len(samples)
        samples_key = pd.DataFrame({
            "idxs": samples["idxs"].to_numpy(),
            "signal_date": samples["idxd"].astype("int64").to_numpy(),
        })
        merged = samples_key.merge(
            holding_df[["idxs", "signal_date", "holding_return"]].drop_duplicates(
                subset=["idxs", "signal_date"], keep="last"
            ),
            on=["idxs", "signal_date"],
            how="left",
        )
        target_arr = merged["holding_return"].to_numpy(dtype="float64")
        is_complete_arr = ~np.isnan(target_arr)

        return {
            "target_holding_return": pd.DataFrame({
                "sample_id": samples["sample_id"].to_numpy(dtype="int64"),
                "idxs": samples["idxs"].to_numpy(),
                "signal_date": samples["idxd"].to_numpy(dtype="int32"),
                "target": target_arr,
                "entry_idxd": np.full(n, -1, dtype="int32"),
                "exit_idxd": np.full(n, -1, dtype="int32"),
                "is_complete": is_complete_arr,
                "is_entry_blocked": np.zeros(n, dtype=bool),
                "label_name": "target_holding_return",
            })
        }
