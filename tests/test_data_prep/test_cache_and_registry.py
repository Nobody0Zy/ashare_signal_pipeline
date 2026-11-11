"""测试缓存命中/失效、resolve_label 三类异常（§7 M2）。"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data_prep.ids import canonical_json
from data_prep.labels import _fr, _hr, _tq  # noqa: F401 确保注册
from data_prep.labels.base import LabelContext, LabelDefinition, build_label_sym_arrays
from data_prep.labels.cache import INDEX_FILE, is_valid, save
from data_prep.labels.registry import _REGISTRY, resolve_label


# ─── resolve_label 三类异常 ────────────────────────────────────────────────────

class TestResolveLabelExceptions:
    def test_unknown_label_name(self):
        """未命中任何 pattern → ValueError，列出可用 pattern。"""
        with pytest.raises(ValueError, match="未命中任何注册 pattern"):
            resolve_label("target_nonexistent_label_xyz")

    def test_h_not_in_horizons(self):
        """target_future_30d：h=30 不在 default horizons=[5,10,20,40] 中 → ValueError。"""
        with pytest.raises(ValueError, match="30"):
            resolve_label("target_future_30d")

    def test_double_hit_raises(self):
        """两个定义同时命中同一名称 → ValueError（注册冲突）。"""

        class _ConflictDef(LabelDefinition):
            name_pattern = r"^target_future_5d$"  # 与 future_return 重叠
            params_key = "_test_conflict"
            spec_version = 999
            default_params = {}

            def label_names(self, params):
                return ["target_future_5d"]

            def horizon_of(self, name, params):
                return 5

            def compute(self, samples, ctx, params):
                return {}

        conflict = _ConflictDef()
        _REGISTRY[conflict.name_pattern] = conflict
        try:
            with pytest.raises(ValueError, match="命中多个"):
                resolve_label("target_future_5d")
        finally:
            _REGISTRY.pop(conflict.name_pattern, None)

    def test_valid_resolve_returns_tuple(self):
        """正常解析返回 (definition, name_params, horizon)。"""
        defn, name_params, horizon = resolve_label("target_future_5d")
        assert horizon == 5
        assert name_params is not None


# ─── 缓存命中与失效 ────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_labels_dir(tmp_path):
    return tmp_path / "labels"


def _make_min_frame(label_name: str, n: int = 5) -> pd.DataFrame:
    """创建满足全部 13 列契约的最小标签 DataFrame。"""
    return pd.DataFrame({
        "sample_id": np.arange(n, dtype="int64"),
        "idxs": [f"sh{i:06d}" for i in range(n)],
        "signal_date": np.array([20200101 + i for i in range(n)], dtype="int32"),
        "target": np.random.default_rng(0).uniform(-0.1, 0.1, n),
        "entry_idxd": np.ones(n, dtype="int32") * 20200102,
        "exit_idxd": np.ones(n, dtype="int32") * 20200110,
        "is_complete": np.ones(n, dtype=bool),
        "is_entry_blocked": np.zeros(n, dtype=bool),
        "label_name": label_name,
        "label_params_hash": "abc12345",
        "samples_id": "test_sid",
        "datebar_fingerprint": "deadbeef1234_100_20200101_20201231_10",
        "created_at": "2026-01-01T00:00:00+00:00",
    })


class TestCacheIsValid:
    def test_cache_miss_when_no_file(self, tmp_labels_dir):
        assert not is_valid(tmp_labels_dir, "sid1", "target_future_5d", "abc12345", "ff11223344556677")

    def test_cache_hit_after_save(self, tmp_labels_dir):
        frame = _make_min_frame("target_future_5d")
        save(tmp_labels_dir, "sid1", "target_future_5d", frame, "abc12345", "ff11223344556677")
        assert is_valid(tmp_labels_dir, "sid1", "target_future_5d", "abc12345", "ff11223344556677")

    def test_cache_miss_on_params_hash_change(self, tmp_labels_dir):
        frame = _make_min_frame("target_future_5d")
        save(tmp_labels_dir, "sid1", "target_future_5d", frame, "abc12345", "ff11223344556677")
        # 修改 params_hash
        assert not is_valid(tmp_labels_dir, "sid1", "target_future_5d", "00000000", "ff11223344556677")

    def test_cache_miss_on_samples_set_hash_change(self, tmp_labels_dir):
        frame = _make_min_frame("target_future_5d")
        save(tmp_labels_dir, "sid1", "target_future_5d", frame, "abc12345", "ff11223344556677")
        # 修改 samples_set_hash
        assert not is_valid(tmp_labels_dir, "sid1", "target_future_5d", "abc12345", "0000000000000000")

    def test_cache_miss_when_index_missing(self, tmp_labels_dir):
        """index.json 被删除后缓存失效。"""
        frame = _make_min_frame("target_future_5d")
        save(tmp_labels_dir, "sid1", "target_future_5d", frame, "abc12345", "ff11223344556677")
        (tmp_labels_dir / "sid1" / INDEX_FILE).unlink()
        assert not is_valid(tmp_labels_dir, "sid1", "target_future_5d", "abc12345", "ff11223344556677")

    def test_cache_miss_when_parquet_missing(self, tmp_labels_dir):
        """parquet 被删除后缓存失效。"""
        frame = _make_min_frame("target_future_5d")
        save(tmp_labels_dir, "sid1", "target_future_5d", frame, "abc12345", "ff11223344556677")
        (tmp_labels_dir / "sid1" / "target_future_5d.parquet").unlink()
        assert not is_valid(tmp_labels_dir, "sid1", "target_future_5d", "abc12345", "ff11223344556677")


class TestIndexJson:
    def test_index_has_required_fields(self, tmp_labels_dir):
        frame = _make_min_frame("target_future_5d")
        save(tmp_labels_dir, "sid1", "target_future_5d", frame, "abc12345", "ff11223344556677",
             holding_fingerprint="")
        index = json.loads((tmp_labels_dir / "sid1" / INDEX_FILE).read_text(encoding="utf-8"))
        entry = index["target_future_5d"]
        for field in ("params_hash", "rows", "non_null_rows", "coverage", "created_at", "holding_fingerprint"):
            assert field in entry, f"缺少字段 {field!r}"

    def test_coverage_computed_correctly(self, tmp_labels_dir):
        frame = _make_min_frame("target_future_5d")
        # 手动设置部分 target 为 NaN
        frame.loc[0, "target"] = float("nan")
        frame.loc[2, "target"] = float("nan")
        save(tmp_labels_dir, "sid1", "target_future_5d", frame, "abc12345", "ff11223344556677")
        index = json.loads((tmp_labels_dir / "sid1" / INDEX_FILE).read_text(encoding="utf-8"))
        entry = index["target_future_5d"]
        expected_coverage = 3 / 5  # 5 rows, 2 NaN
        assert abs(entry["coverage"] - expected_coverage) < 1e-9
