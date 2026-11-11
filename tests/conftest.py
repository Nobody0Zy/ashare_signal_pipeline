"""测试夹具：生成合成 raw 数据，不依赖真实 data/raw/ 文件。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def _make_synthetic_dates(n_days: int = 80, start: int = 20200102) -> list[int]:
    """生成 n_days 个连续整数（模拟 YYYYMMDD，不校验日历有效性）。"""
    dates = []
    d = start
    for _ in range(n_days):
        dates.append(d)
        d += 1
        # 粗略跳过周末（每7天+2），确保够多天
    return dates


@pytest.fixture(scope="session")
def synthetic_datebar_path(tmp_path_factory) -> Path:
    """创建合成 dateBar.pkl 文件，含指数（应被过滤）与股票。"""
    tmp = tmp_path_factory.mktemp("raw_data")
    dates = _make_synthetic_dates(n_days=100, start=20200102)

    # 准备 close 序列：前 50 天平稳，后 50 天上涨（触发布林带突破）
    rng = np.random.default_rng(42)
    rows = []
    syms = {
        "sh600000": "stock",
        "sh600001": "stock",
        "sz000001": "stock",
        "sh000001": "index",   # 应被 sh000 前缀过滤
        "sz399001": "index",   # 应被 sz399 前缀过滤
    }
    for sym, kind in syms.items():
        base = 10.0
        close_prev = base
        for i, d in enumerate(dates):
            if kind == "stock":
                # 前 60 天小幅波动，后 40 天大幅上涨触发突破
                noise = rng.normal(0, 0.05)
                if i >= 60:
                    close = close_prev * (1 + 0.03 + noise)
                else:
                    close = close_prev * (1 + noise)
            else:
                close = base * (1 + rng.normal(0, 0.01))
            close_prev = close
            rows.append({
                "idxs": sym,
                "idxd": d,
                "open": float(close * 0.99),
                "high": float(close * 1.01),
                "low": float(close * 0.98),
                "close": float(close),
                "volume": float(1000.0 + rng.uniform(0, 500)),
                "amount": float(10000.0),
                "xs": 1.0,
                "paused": np.int16(0),
                "marks": np.int16(0),
            })

    df = pd.DataFrame(rows)
    df["idxd"] = df["idxd"].astype("int64")
    df["xs"] = df["xs"].astype("float32")
    df["paused"] = df["paused"].astype("int16")
    df["marks"] = df["marks"].astype("int16")
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = df[col].astype("float32")
    df = df.set_index(["idxs", "idxd"])

    path = tmp / "dateBar.pkl"
    df.to_pickle(path)
    return path


@pytest.fixture(scope="session")
def synthetic_holding_path(tmp_path_factory) -> Path:
    """创建合成 holding 文件（供 legacy_holding 采样测试用）。"""
    tmp = tmp_path_factory.mktemp("raw_holding")
    dates = _make_synthetic_dates(n_days=50, start=20200110)
    rng = np.random.default_rng(99)
    rows = []
    for sym in ["sh600000", "sz000001"]:
        for d in dates[::3]:  # 每3天一条持仓记录
            rows.append({
                "idxs": sym,
                "signal_date": d,
                "holding_return": float(rng.normal(0.01, 0.1)),
                "holding_trade_days": int(rng.integers(5, 30)),
                "is_closed": True,
                "entry_execution_price": float(rng.uniform(8, 15)),
            })
    # 加几条不满足过滤的行（is_closed=False 或 price=0）
    rows.append({
        "idxs": "sh600000", "signal_date": 20200130,
        "holding_return": 0.05, "holding_trade_days": 10,
        "is_closed": False, "entry_execution_price": 10.0,
    })
    df = pd.DataFrame(rows)
    path = tmp / "holding_stats_all.parquet"
    df.to_parquet(path, index=False)
    return path


@pytest.fixture(scope="session")
def synthetic_config_dict(synthetic_datebar_path, synthetic_holding_path, tmp_path_factory) -> dict:
    """生成供 build_samples 使用的合成 config_dict。"""
    prepared_dir = tmp_path_factory.mktemp("prepared")
    return {
        "data": {
            "holding_file": str(synthetic_holding_path),
            "datebar_file": str(synthetic_datebar_path),
            "prepared_dir": str(prepared_dir),
            "labels_dir": str(prepared_dir / "labels"),
        },
        "data_prep": {
            "date_range_start": None,
            "date_range_end": None,
            "universe": {
                "exclude_prefixes": ["sh000", "sz399", "sh950"],
                "min_history_days": 20,
            },
            "samples": {
                "method": "bollinger_breakout",
                "params": {"ma_period": 20.0, "std_mult": 2.0},
                "max_samples": None,
            },
            "labels": {
                "drop_blocked_entries": True,
                "params": {
                    "future_return": {"horizons": [5, 10, 20, 40]},
                    "trend_quality": {"horizon": 20},
                },
            },
        },
    }
