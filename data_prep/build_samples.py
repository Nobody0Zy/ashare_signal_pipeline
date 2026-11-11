"""samples 层构建编排：生成 samples.parquet + manifest.json（§3.1）。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .datebar import build_symbol_arrays, load_datebar
from .ids import canonical_json, derive_sample_id, derive_samples_id
from .samples.events import bollinger as _bol_mod  # noqa: F401 触发注册
from .samples.events import legacy_holding as _leg_mod  # noqa: F401 触发注册
from .samples.registry import get_sample
from .universe import filter_universe

# 确保 events 注册完毕（import 顺序安全）
from .samples import events as _events_pkg  # noqa: F401


def _get_code_version() -> str:
    try:
        result = subprocess.run(
            ["git", "--no-pager", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, encoding="utf-8",
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def build(config_dict: dict, dry_run: bool = False) -> str:
    """构建样本集，写 samples.parquet + manifest.json，返回 samples_id。

    config_dict 必须包含 "data" 与 "data_prep" 段（与 PipelineParams.from_dict 兼容的字典形式）。
    """
    data_cfg = config_dict.get("data", {})
    dp_cfg = config_dict.get("data_prep", {})
    universe_params: dict = dp_cfg.get("universe", {})
    sample_cfg: dict = dp_cfg.get("samples", {})

    method: str = sample_cfg.get("method", "bollinger_breakout")
    sample_params: dict = sample_cfg.get("params", {})
    max_samples: Optional[int] = sample_cfg.get("max_samples", None)
    date_range_start: Optional[int] = dp_cfg.get("date_range_start", None)
    date_range_end: Optional[int] = dp_cfg.get("date_range_end", None)
    min_history_days: int = int(universe_params.get("min_history_days", 60))
    exclude_prefixes: list = universe_params.get("exclude_prefixes", [])

    # 1. 加载 datebar
    datebar_path: str = data_cfg.get("datebar_file", "")
    df, datebar_fingerprint = load_datebar(datebar_path)

    # 2. 过滤 universe
    df_filtered = filter_universe(df, universe_params)

    # 3. 计算 params_hash（§3.4）
    universe_for_hash = {
        "exclude_prefixes": exclude_prefixes,
        "min_history_days": min_history_days,
    }
    hash_input_obj = {
        "method": method,
        "params": sample_params,
        "universe": universe_for_hash,
        "date_range": {"start": date_range_start, "end": date_range_end},
        "min_history_days": min_history_days,
        "max_samples": max_samples,
    }
    params_hash = hashlib.sha256(canonical_json(hash_input_obj).encode("utf-8")).hexdigest()[:8]

    # 4. 推导 samples_id 与输出路径
    samples_id = derive_samples_id(method, params_hash, datebar_fingerprint)
    prepared_root = data_cfg.get("prepared_dir", "data/prepared")
    out_dir = Path(prepared_root) / samples_id

    if dry_run:
        print(f"[build_samples] dry-run: samples_id={samples_id!r}，不落盘", file=__import__("sys").stderr)
        return samples_id

    # 5. 构建 sym_arrays（非停牌序列）
    sym_arrays = build_symbol_arrays(df_filtered)

    # 6. 生成样本
    from .samples.base import SampleContext
    ctx = SampleContext(sym_arrays)
    definition = get_sample(method)
    all_params = {**sample_params, "holding_file": data_cfg.get("holding_file", "")}
    raw_df = definition.generate(sym_arrays, all_params, ctx)

    # 7. 日期范围过滤
    if date_range_start is not None:
        raw_df = raw_df[raw_df["idxd"] >= int(date_range_start)].copy()
    if date_range_end is not None:
        raw_df = raw_df[raw_df["idxd"] <= int(date_range_end)].copy()

    rows_before_cap_actual = len(raw_df)

    # 8. max_samples 分年等距抽样
    year_quota_dict: dict = {}
    rows_bc = 0
    sampling_rate = 1.0
    if max_samples is not None and rows_before_cap_actual > max_samples:
        from .samples.events.bollinger import _apply_max_samples
        raw_df, year_quota_dict = _apply_max_samples(raw_df, max_samples, rows_before_cap_actual)
        rows_bc = rows_before_cap_actual
        sampling_rate = max_samples / rows_before_cap_actual

    # 9. 合并 datebar OHLCV（使用完整 datebar，含停牌行）
    ohlcv_cols = ["idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused", "marks"]
    datebar_for_merge = df[ohlcv_cols].copy()
    result_df = raw_df.merge(datebar_for_merge, on=["idxs", "idxd"], how="left")

    result_df["is_paused"] = result_df["paused"].ne(0)
    result_df = result_df.drop(columns=["paused"])

    # 10. 计算 sample_id（int64），按升序排序
    result_df["sample_id"] = result_df.apply(
        lambda r: derive_sample_id(r["idxs"], int(r["idxd"])), axis=1
    ).astype("int64")
    result_df = result_df.sort_values("sample_id", kind="mergesort").reset_index(drop=True)

    # 11. 添加元数据列
    created_at = datetime.now(timezone.utc).isoformat()
    result_df["sampling_method"] = method
    result_df["sampling_params_hash"] = params_hash
    result_df["datebar_fingerprint"] = datebar_fingerprint
    result_df["created_at"] = created_at

    # 12. 计算 samples_set_hash（§3.4）
    sid_arr = result_df["sample_id"].to_numpy().astype("<i8")
    samples_set_hash = hashlib.sha256(np.sort(sid_arr).tobytes()).hexdigest()[:16]

    # 13. 整理列顺序并转换 dtype
    final_cols = [
        "sample_id", "idxs", "idxd", "open", "high", "low", "close",
        "volume", "amount", "is_paused", "marks", "sampling_method",
        "sampling_params_hash", "datebar_fingerprint", "created_at",
    ]
    result_df = result_df[final_cols].copy()
    result_df["idxd"] = result_df["idxd"].astype("int32")
    result_df["is_paused"] = result_df["is_paused"].astype(bool)
    result_df["marks"] = result_df["marks"].fillna(0).astype("int16")
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        result_df[col] = result_df[col].astype("float32")

    # 14. 写 samples.parquet
    out_dir.mkdir(parents=True, exist_ok=True)
    result_df.to_parquet(out_dir / "samples.parquet", index=False)

    # 15. 写 manifest.json
    code_version = _get_code_version()
    n_symbols = int(result_df["idxs"].nunique())
    manifest = {
        "samples_id": samples_id,
        "sampling_method": method,
        "sampling_params": sample_params,
        "params_hash": params_hash,
        "datebar_fingerprint": datebar_fingerprint,
        "samples_set_hash": samples_set_hash,
        "universe": universe_for_hash,
        "date_range": {"start": date_range_start, "end": date_range_end},
        "min_history_days": min_history_days,
        "max_samples": max_samples,
        "rows_before_cap": rows_bc,
        "sampling_rate": sampling_rate,
        "year_quota": year_quota_dict,
        "rows": int(len(result_df)),
        "n_symbols": n_symbols,
        "created_at": created_at,
        "code_version": code_version,
    }
    with (out_dir / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(
        f"[build_samples] 完成: samples_id={samples_id!r}  "
        f"rows={len(result_df)}  n_symbols={n_symbols}  "
        f"sampling_rate={sampling_rate:.6f}",
        file=__import__("sys").stderr,
    )
    return samples_id
