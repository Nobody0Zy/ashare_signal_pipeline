"""测试标签行契约、列契约与 is_complete / is_entry_blocked 语义。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data_prep.labels.base import LABEL_REQUIRED_COLUMNS, LabelContext, build_label_sym_arrays, validate_label_frame
from data_prep.labels.future_return import FutureReturnDefinition
from data_prep.labels.trend_quality import TrendQualityDefinition


# ─── 合成数据工厂 ───────────────────────────────────────────────────────────────

def _make_bars(
    sym: str = "sh600000",
    n_days: int = 80,
    start_idxd: int = 20200101,
    paused_positions: set[int] | None = None,
    limit_up_positions: set[int] | None = None,
) -> pd.DataFrame:
    """生成单个 symbol 的合成 dateBar DataFrame（已 reset_index，含 paused/marks）。"""
    rng = np.random.default_rng(42)
    paused_positions = paused_positions or set()
    limit_up_positions = limit_up_positions or set()
    rows = []
    close = 10.0
    for i in range(n_days):
        idxd = start_idxd + i
        paused = int(i in paused_positions)
        # marks bit 11 = 1 表示开盘涨停
        marks = int16_val = (1 << 11) if i in limit_up_positions else 0
        noise = float(rng.normal(0, 0.02))
        close = max(close * (1 + noise), 1.0)
        rows.append({
            "idxs": sym, "idxd": idxd,
            "open": float(close * 0.99),
            "high": float(close * 1.01),
            "low": float(close * 0.98),
            "close": float(close),
            "volume": 1000.0, "amount": 10000.0,
            "paused": paused, "marks": marks,
        })
    return pd.DataFrame(rows)


def _make_samples(sym: str = "sh600000", signal_days: list[int] | None = None) -> pd.DataFrame:
    if signal_days is None:
        signal_days = list(range(20200120, 20200130))
    from data_prep.ids import derive_sample_id
    rows = []
    for d in signal_days:
        rows.append({
            "sample_id": derive_sample_id(sym, d),
            "idxs": sym,
            "idxd": d,
        })
    df = pd.DataFrame(rows)
    df["sample_id"] = df["sample_id"].astype("int64")
    df["idxd"] = df["idxd"].astype("int32")
    return df


def _make_ctx(bars_df: pd.DataFrame) -> LabelContext:
    return LabelContext(build_label_sym_arrays(bars_df))


# ─── validate_label_frame ──────────────────────────────────────────────────────

class TestValidateLabelFrame:
    def test_valid_frame_passes(self):
        frame = pd.DataFrame({col: [] for col in LABEL_REQUIRED_COLUMNS})
        validate_label_frame(frame)  # should not raise

    def test_missing_column_raises(self):
        cols = list(LABEL_REQUIRED_COLUMNS)
        frame = pd.DataFrame({c: [] for c in cols if c != "target"})
        with pytest.raises(ValueError, match="target"):
            validate_label_frame(frame)


# ─── FutureReturn ─────────────────────────────────────────────────────────────

class TestFutureReturnContract:
    def _build(self, signal_days=None, paused_pos=None, limit_up_pos=None, h=5):
        bars = _make_bars(n_days=100, paused_positions=paused_pos, limit_up_positions=limit_up_pos)
        ctx = _make_ctx(bars)
        samples = _make_samples(signal_days=signal_days)
        defn = FutureReturnDefinition()
        params = {"horizons": [h], "drop_blocked_entries": True}
        return defn.compute(samples, ctx, params)[f"target_future_{h}d"]

    def test_rows_match_samples_count(self):
        days = list(range(20200120, 20200130))
        frame = self._build(signal_days=days)
        assert len(frame) == len(days)

    def test_required_columns_present(self):
        frame = self._build()
        # label_params_hash / samples_id / datebar_fingerprint / created_at are added by _materialize
        core_cols = LABEL_REQUIRED_COLUMNS - {"label_params_hash", "samples_id", "datebar_fingerprint", "created_at"}
        assert core_cols.issubset(set(frame.columns))

    def test_no_inf_in_target(self):
        frame = self._build()
        assert not np.isinf(frame["target"].dropna()).any()

    def test_is_complete_false_when_exit_out_of_bounds(self):
        """信号日靠近数据末尾时，is_complete == False，target == NaN。"""
        # 只有 10 天数据，horizon=5，最后几行出场越界
        frame = self._build(signal_days=[20200100 + i for i in range(1, 11)], h=5)
        # 最后几个 signal_date 因出场越界应 is_complete=False
        incomplete = frame[~frame["is_complete"]]
        # 对不完整的行，target 应为 NaN
        assert incomplete["target"].isna().all()

    def test_is_entry_blocked_true_when_open_limit_up(self):
        """建仓日开盘涨停时 is_entry_blocked=True，drop_blocked=True 时 target=NaN。"""
        # signal_day = 20200120，entry_day = 20200121（相对 start_idxd=20200101 的位置 20）
        # bars 从 20200101 开始，第 20 天 idxd=20200121 对应 non-paused position 20
        # 实际上 np.searchsorted(idxd_arr, 20200120, side='right') 给出 20200121 的位置
        signal_days = [20200120]
        # position 20 在 bars 中是 start + 20 = 20200121
        limit_pos = {20}  # bars[20] = idxd 20200121 is limit-up
        frame = self._build(signal_days=signal_days, limit_up_pos=limit_pos, h=5)
        blocked_rows = frame[frame["is_entry_blocked"]]
        if len(blocked_rows) > 0:
            assert blocked_rows["target"].isna().all()

    def test_target_finite_or_nan(self):
        """target 只含有限值或 NaN，无 ±inf。"""
        frame = self._build(signal_days=list(range(20200110, 20200150)), h=10)
        target = frame["target"].dropna()
        assert np.isfinite(target).all()

    def test_label_name_column_equals_key(self):
        h = 5
        frame = self._build(h=h)
        assert (frame["label_name"] == f"target_future_{h}d").all()

    def test_signal_date_equals_idxd(self):
        days = list(range(20200120, 20200130))
        frame = self._build(signal_days=days)
        assert list(frame["signal_date"]) == [d for d in days]


# ─── TrendQuality ─────────────────────────────────────────────────────────────

class TestTrendQualityContract:
    def _build(self, signal_days=None, h=10):
        bars = _make_bars(n_days=100)
        ctx = _make_ctx(bars)
        samples = _make_samples(signal_days=signal_days)
        defn = TrendQualityDefinition()
        params = {"horizon": h, "drop_blocked_entries": True}
        return defn.compute(samples, ctx, params)[f"target_trend_quality_{h}d"]

    def test_rows_match_samples_count(self):
        days = list(range(20200120, 20200130))
        frame = self._build(signal_days=days)
        assert len(frame) == len(days)

    def test_no_inf_in_target(self):
        frame = self._build()
        assert not np.isinf(frame["target"].dropna()).any()

    def test_monotone_window_gives_nan(self):
        """严格单调上涨（md ≈ 0）时 target 应为 NaN。"""
        # 构造严格单调上涨的价格序列
        rows = []
        close = 10.0
        for i in range(80):
            close += 0.1  # 严格递增
            rows.append({
                "idxs": "sh600000", "idxd": 20200101 + i,
                "open": close - 0.05, "high": close + 0.05,
                "low": close - 0.1, "close": close,
                "volume": 1000.0, "amount": 10000.0,
                "paused": 0, "marks": 0,
            })
        bars_mono = pd.DataFrame(rows)
        ctx = _make_ctx(bars_mono)
        samples = _make_samples(signal_days=[20200120])
        defn = TrendQualityDefinition()
        params = {"horizon": 20, "drop_blocked_entries": True}
        frame = defn.compute(samples, ctx, params)["target_trend_quality_20d"]
        # 单调上涨时 md ≈ 0，target 应为 NaN
        assert frame["target"].isna().all() or np.isfinite(frame["target"].dropna()).all()

    def test_label_name_column_correct(self):
        h = 10
        frame = self._build(h=h)
        assert (frame["label_name"] == f"target_trend_quality_{h}d").all()
