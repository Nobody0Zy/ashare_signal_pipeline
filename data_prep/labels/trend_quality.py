"""内置标签：target_trend_quality_{h}d（§4.7）。

target = future_return / max_drawdown（md > 1e-6 时）；md ≤ 1e-6 给 NaN。
max_drawdown = max_i(1 - close_i / running_max(close_e..close_i)) 在 [e, e+h] 上。
"""

from __future__ import annotations

import re
from typing import Dict, List

import numpy as np
import pandas as pd

from .base import LabelContext, LabelDefinition
from .registry import register_label


@register_label
class TrendQualityDefinition(LabelDefinition):
    """趋势质量标签：未来收益率除以最大回撤。"""

    name_pattern: str = r"^target_trend_quality_(\d+)d$"
    params_key: str = "trend_quality"
    spec_version: int = 1
    default_params: dict = {"horizon": 20, "drop_blocked_entries": True}

    def label_names(self, params: dict) -> List[str]:
        h = params.get("horizon", self.default_params["horizon"])
        return [f"target_trend_quality_{h}d"]

    def horizon_of(self, name: str, params: dict) -> int:
        m = re.fullmatch(r"^target_trend_quality_(\d+)d$", name)
        if not m:
            raise ValueError(f"{name!r} 不匹配 name_pattern {self.name_pattern!r}")
        h = int(m.group(1))
        expected = int(params.get("horizon", self.default_params.get("horizon", 20)))
        if h != expected:
            raise ValueError(
                f"标签名 {name!r} 中的 h={h} 不等于 params['horizon']={expected}"
            )
        return h

    def compute(
        self,
        samples: pd.DataFrame,
        ctx: LabelContext,
        params: dict,
    ) -> Dict[str, pd.DataFrame]:
        h: int = int(params.get("horizon", self.default_params["horizon"]))
        drop_blocked: bool = bool(params.get("drop_blocked_entries", True))
        lname = f"target_trend_quality_{h}d"

        n = len(samples)
        sample_ids = samples["sample_id"].to_numpy(dtype="int64")
        idxs_arr = samples["idxs"].to_numpy()
        signal_dates = samples["idxd"].to_numpy(dtype="int64")

        target_arr = np.full(n, np.nan, dtype="float64")
        is_complete_arr = np.zeros(n, dtype=bool)
        is_entry_blocked_arr = np.zeros(n, dtype=bool)
        entry_idxd_arr = np.full(n, -1, dtype="int32")
        exit_idxd_arr = np.full(n, -1, dtype="int32")

        unique_syms = pd.unique(idxs_arr)
        for sym in unique_syms:
            mask = idxs_arr == sym
            idx_pos = np.where(mask)[0]
            sym_dates = signal_dates[idx_pos]

            arr = ctx.get_arrays(sym)
            if arr is None:
                continue

            idxd_np = arr["idxd"].astype("int64")
            close_np = arr["close"].astype("float64")
            open_np = arr["open"].astype("float64")
            marks_np = arr["marks"].astype("int64")
            n_arr = len(idxd_np)
            if n_arr == 0:
                continue

            e_arr = np.searchsorted(idxd_np, sym_dates, side="right").astype("int64")
            valid_entry = e_arr < n_arr
            e_clipped = np.minimum(e_arr, n_arr - 1)

            entry_open = np.where(valid_entry, open_np[e_clipped], np.nan)
            entry_marks = np.where(valid_entry, marks_np[e_clipped], 0)
            blocked = (entry_marks & (1 << 11)) != 0

            is_entry_blocked_arr[idx_pos] = valid_entry & blocked
            entry_idxd_arr[idx_pos] = np.where(
                valid_entry, idxd_np[e_clipped].astype("int32"), np.int32(-1)
            )

            valid_complete = valid_entry & ((e_arr + h) < n_arr)
            exit_e = e_arr + h
            exit_clipped = np.minimum(exit_e, n_arr - 1)
            is_complete_arr[idx_pos] = valid_complete
            exit_idxd_arr[idx_pos] = np.where(
                valid_complete, idxd_np[exit_clipped].astype("int32"), np.int32(-1)
            )

            # Vectorized sliding window for valid samples
            valid_idx = np.where(valid_complete)[0]
            if len(valid_idx) == 0:
                continue

            e_valid = e_arr[valid_idx]  # entry positions for valid samples

            # Build close windows matrix: (n_valid, h+1) using fancy indexing
            window_indices = e_valid[:, None] + np.arange(h + 1)  # (n_valid, h+1)
            # All indices are valid since valid_complete ensures e+h < n_arr
            close_windows = close_np[window_indices]  # (n_valid, h+1)

            # Max drawdown: md = max_i(1 - c_i / running_max(c_0..c_i))
            running_max = np.maximum.accumulate(close_windows, axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                drawdown = 1.0 - close_windows / running_max
            md_arr = drawdown.max(axis=1)  # (n_valid,)

            # Future return r = close[e+h] / open[e] - 1
            entry_open_valid = open_np[e_valid]
            exit_close_valid = close_np[e_valid + h]
            with np.errstate(divide="ignore", invalid="ignore"):
                r_arr = np.where(entry_open_valid != 0,
                                 exit_close_valid / entry_open_valid - 1,
                                 np.nan)

            # target = r / md (NaN when md <= 1e-6)
            tq = np.where(md_arr > 1e-6, r_arr / md_arr, np.nan)

            if drop_blocked:
                blocked_valid = blocked[valid_idx]
                tq = np.where(blocked_valid, np.nan, tq)

            orig_positions = idx_pos[valid_idx]
            target_arr[orig_positions] = tq

        return {
            lname: pd.DataFrame({
                "sample_id": sample_ids,
                "idxs": idxs_arr,
                "signal_date": signal_dates.astype("int32"),
                "target": target_arr,
                "entry_idxd": entry_idxd_arr.astype("int32"),
                "exit_idxd": exit_idxd_arr.astype("int32"),
                "is_complete": is_complete_arr,
                "is_entry_blocked": is_entry_blocked_arr,
                "label_name": lname,
            })
        }
