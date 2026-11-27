"""内置标签：target_future_{h}d（§4.6）。

一次 compute 对 params["horizons"] 中的全部 h 生成标签，分别落盘。
入场日 = signal_date 之后第一个非停牌交易日；出场日 = 入场日 + h（非停牌序列）。
"""

from __future__ import annotations

import re
from typing import Dict, List

import numpy as np
import pandas as pd

from .base import LabelContext, LabelDefinition
from .registry import register_label


@register_label
class FutureReturnDefinition(LabelDefinition):
    """未来 h 日收益率标签。"""

    name_pattern: str = r"^target_future_(\d+)d$"
    params_key: str = "future_return"
    spec_version: int = 1
    default_params: dict = {"horizons": [5, 10, 20, 40], "drop_blocked_entries": True}

    def label_names(self, params: dict) -> List[str]:
        horizons = params.get("horizons", self.default_params["horizons"])
        return [f"target_future_{h}d" for h in horizons]

    def horizon_of(self, name: str, params: dict) -> int:
        m = re.fullmatch(r"^target_future_(\d+)d$", name)
        if not m:
            raise ValueError(f"{name!r} 不匹配 name_pattern {self.name_pattern!r}")
        h = int(m.group(1))
        horizons = list(params.get("horizons", self.default_params.get("horizons", [])))
        if h not in horizons:
            raise ValueError(
                f"标签名 {name!r} 中的 h={h} 不在 params['horizons']={horizons} 中；"
                f"请在配置 data_prep.labels.params.future_return.horizons 中添加 {h}"
            )
        return h

    def compute(
        self,
        samples: pd.DataFrame,
        ctx: LabelContext,
        params: dict,
    ) -> Dict[str, pd.DataFrame]:
        horizons: list = list(params.get("horizons", self.default_params["horizons"]))
        drop_blocked: bool = bool(params.get("drop_blocked_entries", True))

        n = len(samples)
        sample_ids = samples["sample_id"].to_numpy(dtype="int64")
        idxs_arr = samples["idxs"].to_numpy()
        signal_dates = samples["idxd"].to_numpy(dtype="int64")

        # Per-horizon output arrays
        targets: dict = {h: np.full(n, np.nan, dtype="float64") for h in horizons}
        is_complete: dict = {h: np.zeros(n, dtype=bool) for h in horizons}
        exit_idxd_arr: dict = {h: np.full(n, -1, dtype="int32") for h in horizons}
        is_entry_blocked_arr = np.zeros(n, dtype=bool)
        entry_idxd_arr = np.full(n, -1, dtype="int32")

        # Vectorized per-symbol processing
        unique_syms = pd.unique(idxs_arr)
        for sym in unique_syms:
            mask = idxs_arr == sym
            idx_pos = np.where(mask)[0]
            sym_dates = signal_dates[idx_pos]

            arr = ctx.get_arrays(sym)
            if arr is None:
                continue

            idxd_np = arr["idxd"].astype("int64")  # non-paused idxd (sorted)
            close_np = arr["close"].astype("float64")
            open_np = arr["open"].astype("float64")
            marks_np = arr["marks"].astype("int64")
            n_arr = len(idxd_np)
            if n_arr == 0:
                continue

            # Entry position = first non-paused day AFTER signal_date
            e_arr = np.searchsorted(idxd_np, sym_dates, side="right").astype("int64")
            valid_entry = e_arr < n_arr
            e_clipped = np.minimum(e_arr, n_arr - 1)

            entry_open = np.where(valid_entry, open_np[e_clipped], np.nan)
            entry_marks = np.where(valid_entry, marks_np[e_clipped], 0)
            blocked = (entry_marks & (1 << 11)) != 0  # 开盘涨停 bit 11

            is_entry_blocked_arr[idx_pos] = valid_entry & blocked
            entry_idxd_arr[idx_pos] = np.where(
                valid_entry, idxd_np[e_clipped].astype("int32"), np.int32(-1)
            )

            for h in horizons:
                exit_e = e_arr + h
                valid_exit = valid_entry & (exit_e < n_arr)
                exit_clipped = np.minimum(exit_e, n_arr - 1)
                exit_close = np.where(valid_exit, close_np[exit_clipped], np.nan)

                # target = exit_close / entry_open - 1
                valid_div = valid_exit & (entry_open != 0)
                t = np.where(valid_div, exit_close / entry_open - 1, np.nan)
                if drop_blocked:
                    t = np.where(valid_entry & blocked, np.nan, t)

                targets[h][idx_pos] = t
                is_complete[h][idx_pos] = valid_exit
                exit_idxd_arr[h][idx_pos] = np.where(
                    valid_exit, idxd_np[exit_clipped].astype("int32"), np.int32(-1)
                )

        result: Dict[str, pd.DataFrame] = {}
        for h in horizons:
            lname = f"target_future_{h}d"
            result[lname] = pd.DataFrame({
                "sample_id": sample_ids,
                "idxs": idxs_arr,
                "signal_date": signal_dates.astype("int32"),
                "target": targets[h],
                "entry_idxd": entry_idxd_arr.astype("int32"),
                "exit_idxd": exit_idxd_arr[h].astype("int32"),
                "is_complete": is_complete[h],
                "is_entry_blocked": is_entry_blocked_arr,
                "label_name": lname,
            })
        return result
