"""探测 dateBar 基础事实，输出可入库 JSON。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import load_params, resolve_raw_data_paths


def _load_datebar(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        df = pd.read_parquet(path)
    elif suffix in {".pkl", ".pickle"}:
        df = pd.read_pickle(path)
    else:
        raise ValueError(f"不支持的 dateBar 文件扩展名: {path.suffix}")
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"dateBar 对象不是 DataFrame: {type(df)!r}")
    return df.reset_index()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    return float(value)


def probe(config_path: str) -> dict[str, Any]:
    params = load_params(config_path)
    datebar_path = resolve_raw_data_paths(REPO_ROOT, params)["datebar"]
    df = _load_datebar(datebar_path)

    normalized = df.rename(columns={col: str(col).lower() for col in df.columns})
    required = [
        "idxs",
        "idxd",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "xs",
        "paused",
        "marks",
    ]
    missing = [col for col in required if col not in normalized.columns]
    if missing:
        raise ValueError(f"dateBar 缺失必要列: {missing}")

    idxs = normalized["idxs"].astype(str)
    idxd = pd.to_numeric(normalized["idxd"], errors="coerce")
    open_ = pd.to_numeric(normalized["open"], errors="coerce")
    high = pd.to_numeric(normalized["high"], errors="coerce")
    low = pd.to_numeric(normalized["low"], errors="coerce")
    close = pd.to_numeric(normalized["close"], errors="coerce")
    volume = pd.to_numeric(normalized["volume"], errors="coerce")
    amount = pd.to_numeric(normalized["amount"], errors="coerce")
    xs = pd.to_numeric(normalized["xs"], errors="coerce")
    paused = pd.to_numeric(normalized["paused"], errors="coerce")
    marks = pd.to_numeric(normalized["marks"], errors="coerce").fillna(0).astype("int64")

    rows = int(len(normalized))
    n_symbols = int(idxs.nunique(dropna=True))
    trading_days = int(idxd.nunique(dropna=True))
    min_idxd = int(idxd.min())
    max_idxd = int(idxd.max())
    duplicate_pairs = int(normalized.duplicated(subset=["idxs", "idxd"]).sum())

    paused_mask = paused.eq(1)
    paused_rows = int(paused_mask.sum())
    paused_ratio = (paused_rows / rows) if rows else None
    paused_volume_le_zero_ratio = None
    if paused_rows > 0:
        paused_volume_le_zero_ratio = float(volume[paused_mask].le(0).mean())

    marks_high_bits_nonzero = int((marks & ~((1 << 12) - 1)).ne(0).sum())

    valid_ohlc = high.notna() & low.notna()
    high_lt_low = int((high[valid_ohlc] < low[valid_ohlc]).sum())
    high_lt_max_oc = int((high[valid_ohlc] < pd.concat([open_, close], axis=1).max(axis=1)[valid_ohlc]).sum())
    low_gt_min_oc = int((low[valid_ohlc] > pd.concat([open_, close], axis=1).min(axis=1)[valid_ohlc]).sum())

    amount_ratio_mask = close.gt(0) & volume.gt(0)
    amount_ratio = amount[amount_ratio_mask] / (close[amount_ratio_mask] * volume[amount_ratio_mask])

    idxs_pattern = re.compile(r"^(sh|sz|bj)\d{6}$")
    idxs_match_ratio = float(idxs.map(lambda s: bool(idxs_pattern.match(s))).mean())

    report: dict[str, Any] = {
        "config": config_path,
        "datebar_path": str(datebar_path),
        "rows": rows,
        "n_symbols": n_symbols,
        "trading_days": trading_days,
        "min_idxd": min_idxd,
        "max_idxd": max_idxd,
        "duplicate_id_pairs": duplicate_pairs,
        "columns_after_reset_index": [str(c) for c in normalized.columns],
        "dtypes_after_reset_index": {str(c): str(normalized[c].dtype) for c in normalized.columns},
        "idxs_format": {
            "regex": r"^(sh|sz|bj)\d{6}$",
            "match_ratio": idxs_match_ratio,
            "unique_prefix_counts": {
                "sh000": int(idxs.str.startswith("sh000").sum()),
                "sz399": int(idxs.str.startswith("sz399").sum()),
                "sh950": int(idxs.str.startswith("sh950").sum()),
            },
        },
        "paused": {
            "rows": paused_rows,
            "ratio": _to_float(paused_ratio),
            "paused_volume_le_zero_ratio": _to_float(paused_volume_le_zero_ratio),
        },
        "marks": {
            "nunique": int(marks.nunique(dropna=True)),
            "min": int(marks.min()),
            "max": int(marks.max()),
            "bits_ge_12_nonzero_rows": marks_high_bits_nonzero,
        },
        "xs": {
            "non_null_ratio": float(xs.notna().mean()),
            "min": _to_float(xs.min()),
            "max": _to_float(xs.max()),
            "close_div_xs_median": _to_float((close / xs.replace(0, pd.NA)).median(skipna=True)),
        },
        "price_sanity": {
            "high_lt_low_rows": high_lt_low,
            "high_lt_max_open_close_rows": high_lt_max_oc,
            "low_gt_min_open_close_rows": low_gt_min_oc,
        },
        "amount_unit_check": {
            "rows_used": int(amount_ratio_mask.sum()),
            "ratio_median": _to_float(amount_ratio.median(skipna=True)),
            "ratio_p95": _to_float(amount_ratio.quantile(0.95)),
        },
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="探测 dateBar 基础事实。")
    parser.add_argument(
        "--config",
        default="configs/local/refactor_check.yaml",
        help="配置文件路径（用于解析 data.datebar_file）。",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = probe(args.config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        paused_ratio = report["paused"]["ratio"]
        print(f"config      : {report['config']}")
        print(f"datebar_path: {report['datebar_path']}")
        print(f"rows        : {report['rows']}")
        print(f"n_symbols   : {report['n_symbols']}")
        print(f"idxd_range  : {report['min_idxd']} ~ {report['max_idxd']}")
        print(f"paused_ratio: {paused_ratio:.6f}" if paused_ratio is not None else "paused_ratio: null")
        print(f"marks_unique: {report['marks']['nunique']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
