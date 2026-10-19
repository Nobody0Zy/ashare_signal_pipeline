"""步骤1：清洗持仓与日线原始数据。"""

from __future__ import annotations

from typing import Dict

import pandas as pd

from .config import Paths, PipelineParams


def _clean_holding(paths: Paths, params: PipelineParams) -> pd.DataFrame:
    holding = pd.read_parquet(paths.raw_holding)
    if "holding_return" not in holding.columns:
        raise ValueError("holding_stats_all.parquet 缺少 holding_return 列")

    holding = holding[
        holding["is_closed"].eq(True)
        & holding["entry_execution_price"].gt(0)
        & holding["holding_return"].notna()
    ].copy()
    holding = holding.rename_axis("sample_id").reset_index()
    holding["signal_date"] = holding["signal_date"].astype(int)
    holding["target"] = holding["holding_return"]
    # V4.1: 持有期过滤（默认 max_holding_days=null 时不过滤）
    if params.sample_filter.max_holding_days is not None:
        max_days = int(params.sample_filter.max_holding_days * params.sample_filter.tolerance)
        before = len(holding)
        holding = holding[holding["holding_trade_days"].le(max_days)].copy()
        dropped = before - len(holding)
        print(f"步骤1 max_holding_days 过滤: 丢弃 {dropped} 条 (holding > {max_days})")
    return holding


def _clean_datebar(paths: Paths) -> pd.DataFrame:
    datebar = pd.read_pickle(paths.raw_datebar)
    datebar = datebar.reset_index()
    datebar.columns = [str(column).lower() for column in datebar.columns]
    required = {"idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused"}
    missing = required.difference(datebar.columns)
    if missing:
        raise ValueError(f"dateBar.pkl 缺少必要列: {sorted(missing)}")
    datebar = datebar[["idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused"]].copy()
    datebar["idxd"] = datebar["idxd"].astype(int)
    datebar["paused"] = datebar["paused"].astype(bool)
    return datebar.sort_values(["idxs", "idxd"], kind="mergesort").reset_index(drop=True)


def compute_step1(paths: Paths, mode: str, params: PipelineParams) -> Dict[str, object]:
    del mode
    holding = _clean_holding(paths, params)
    holding_path = paths.processed_dir / "holding_clean.parquet"
    holding.to_parquet(holding_path, index=False)

    datebar = _clean_datebar(paths)
    datebar_path = paths.processed_dir / "datebar.parquet"
    datebar.to_parquet(datebar_path, index=False)

    target_stats = holding["target"].describe(percentiles=[0.25, 0.5, 0.75]).to_dict()
    checks = {
        "holding_exists": holding_path.exists(),
        "holding_columns": all(column in holding.columns for column in ["sample_id", "idxs", "signal_date", "target"]),
        "holding_target_non_null": holding["target"].notna().all(),
        "datebar_exists": datebar_path.exists(),
        "datebar_columns": list(datebar.columns) == ["idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused"],
        "datebar_sorted": datebar.equals(datebar.sort_values(["idxs", "idxd"], kind="mergesort").reset_index(drop=True)),
    }

    return {
        "output_files": [
            str(holding_path.relative_to(paths.root)).replace("\\", "/"),
            str(datebar_path.relative_to(paths.root)).replace("\\", "/"),
        ],
        "checks": checks,
        "details": {
            "sample_count": int(len(holding)),
            "signal_date_min": int(holding["signal_date"].min()),
            "signal_date_max": int(holding["signal_date"].max()),
            "target_stats": {key: float(value) for key, value in target_stats.items()},
            "datebar_rows": int(len(datebar)),
        },
    }