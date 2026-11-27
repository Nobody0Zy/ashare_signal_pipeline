"""测试 _apply_max_samples：抽样逻辑的单元测试（§3 小事 A）。

补充背景：M1 Gate 的 A1-10 中 max_samples 为 null，_apply_max_samples 从未被执行；
A1-10 只走了「不适用」分支，未覆盖抽样代码路径。本文件补全该覆盖。
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest

from data_prep.ids import canonical_json
from data_prep.samples.events.bollinger import _apply_max_samples


def _make_df(year_rows: dict[int, int], seed: int = 42) -> pd.DataFrame:
    """构造含 (idxs, idxd) 的测试 DataFrame。

    year_rows: {year: n_rows}，idxd = year * 10000 + 序号（1-based）
    """
    rng = np.random.default_rng(seed)
    rows = []
    for year, n in sorted(year_rows.items()):
        for i in range(1, n + 1):
            rows.append({"idxs": f"sh{(i % 1000):06d}", "idxd": year * 10000 + i})
    df = pd.DataFrame(rows)
    df["idxd"] = df["idxd"].astype("int32")
    return df


class TestApplyMaxSamples:
    """_apply_max_samples 功能测试。"""

    def test_quota_sum_equals_m(self):
        """year_quota 之和 == M，且结果行数 == M。"""
        df = _make_df({2020: 40, 2021: 40})
        result, quota = _apply_max_samples(df, 10, 80)
        assert sum(quota.values()) == 10
        assert len(result) == 10

    def test_deterministic(self):
        """同输入两次调用结果完全相等（逐行逐值）。"""
        df = _make_df({2020: 40, 2021: 40})
        r1, q1 = _apply_max_samples(df, 10, 80)
        r2, q2 = _apply_max_samples(df, 10, 80)
        pd.testing.assert_frame_equal(r1.reset_index(drop=True), r2.reset_index(drop=True))
        assert q1 == q2

    def test_cross_year_distribution_equal_years(self):
        """两年数据等量时，最大余数法应配额各 5（40+40，M=10）。"""
        df = _make_df({2020: 40, 2021: 40})
        result, quota = _apply_max_samples(df, 10, 80)
        assert quota.get("2020", 0) == 5
        assert quota.get("2021", 0) == 5

    def test_cross_year_distribution_unequal_years(self):
        """样本量不等的年份，最大余数法配额合理（总和 == M）。"""
        df = _make_df({2020: 60, 2021: 40})  # total=100, M=10
        result, quota = _apply_max_samples(df, 10, 100)
        assert sum(quota.values()) == 10
        assert len(result) == 10
        # 2020 占 60%，应得 6；2021 占 40%，应得 4
        assert quota.get("2020", 0) == 6
        assert quota.get("2021", 0) == 4

    def test_all_rows_returned_when_m_ge_total(self):
        """M >= total 时返回全部行（不裁剪）。"""
        df = _make_df({2020: 5, 2021: 5})
        result, quota = _apply_max_samples(df, 100, 10)
        assert len(result) == 10

    def test_max_samples_affects_params_hash(self):
        """max_samples=null 与 max_samples=100000 产生不同的 params_hash（§3.4）。"""
        base = {
            "method": "bollinger_breakout",
            "params": {"ma_period": 20.0, "std_mult": 2.0},
            "universe": {"exclude_prefixes": ["sh000", "sz399", "sh950"], "min_history_days": 60},
            "date_range": {"start": None, "end": None},
            "min_history_days": 60,
        }
        obj_null = dict(base, max_samples=None)
        obj_100k = dict(base, max_samples=100000)

        hash_null = hashlib.sha256(canonical_json(obj_null).encode()).hexdigest()[:8]
        hash_100k = hashlib.sha256(canonical_json(obj_100k).encode()).hexdigest()[:8]

        assert hash_null != hash_100k, (
            "max_samples=null 与 max_samples=100000 的 params_hash 相同，"
            "会导致换 cap 时错误复用同一 samples_id（违反 §3.4）"
        )
