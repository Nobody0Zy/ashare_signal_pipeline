"""samples.parquet 结构契约测试（列集合、升序、前缀排除、确定性）。"""

from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from data_prep.build_samples import build


EXPECTED_COLS = {
    "sample_id", "idxs", "idxd", "open", "high", "low", "close",
    "volume", "amount", "is_paused", "marks", "sampling_method",
    "sampling_params_hash", "datebar_fingerprint", "created_at",
}
EXCLUDE_PREFIXES = ["sh000", "sz399", "sh950"]


@pytest.fixture(scope="module")
def built_samples(synthetic_config_dict):
    """构建一次合成样本集，返回 (samples_id, parquet_df, manifest)。"""
    import json
    from pathlib import Path

    samples_id = build(synthetic_config_dict)
    prepared_dir = synthetic_config_dict["data"]["prepared_dir"]
    df = pd.read_parquet(Path(prepared_dir) / samples_id / "samples.parquet")
    with open(Path(prepared_dir) / samples_id / "manifest.json", encoding="utf-8") as fh:
        manifest = json.load(fh)
    return samples_id, df, manifest


class TestColumnContract:
    def test_columns_exact(self, built_samples):
        _, df, _ = built_samples
        assert set(df.columns) == EXPECTED_COLS

    def test_no_target_label_columns(self, built_samples):
        _, df, _ = built_samples
        bad = [c for c in df.columns if c.startswith(("target", "label"))]
        assert bad == [], f"不应含 target/label 列: {bad}"

    def test_sample_id_dtype_int64(self, built_samples):
        _, df, _ = built_samples
        assert str(df["sample_id"].dtype) == "int64"

    def test_is_paused_bool(self, built_samples):
        _, df, _ = built_samples
        assert df["is_paused"].dtype == bool

    def test_idxd_int32(self, built_samples):
        _, df, _ = built_samples
        assert str(df["idxd"].dtype) == "int32"


class TestSampleIdConstraints:
    def test_unique(self, built_samples):
        _, df, _ = built_samples
        assert df["sample_id"].nunique() == len(df), "sample_id 不唯一"

    def test_strictly_ascending(self, built_samples):
        _, df, _ = built_samples
        if len(df) > 1:
            assert (df["sample_id"].diff().dropna() > 0).all(), "sample_id 非严格升序"

    def test_no_excluded_prefix(self, built_samples):
        _, df, _ = built_samples
        for prefix in EXCLUDE_PREFIXES:
            count = df["idxs"].str.startswith(prefix).sum()
            assert count == 0, f"存在被排除前缀 {prefix!r} 的 idxs: {count} 行"


class TestManifest:
    MANIFEST_17_KEYS = {
        "samples_id", "sampling_method", "sampling_params", "params_hash",
        "datebar_fingerprint", "samples_set_hash", "universe", "date_range",
        "min_history_days", "max_samples", "rows_before_cap", "sampling_rate",
        "year_quota", "rows", "n_symbols", "created_at", "code_version",
    }

    def test_manifest_has_17_keys(self, built_samples):
        _, _, manifest = built_samples
        missing = self.MANIFEST_17_KEYS - set(manifest.keys())
        assert not missing, f"manifest 缺少键: {missing}"

    def test_rows_consistent(self, built_samples):
        _, df, manifest = built_samples
        assert manifest["rows"] == len(df)

    def test_samples_set_hash_recomputed(self, built_samples):
        import numpy as np
        _, df, manifest = built_samples
        sid_arr = df["sample_id"].to_numpy().astype("<i8")
        computed = hashlib.sha256(np.sort(sid_arr).tobytes()).hexdigest()[:16]
        assert computed == manifest["samples_set_hash"]


class TestDeterminism:
    def test_same_output_on_rerun(self, synthetic_config_dict):
        """同配置重跑，忽略 created_at 后逐行相等。"""
        from pathlib import Path

        sid1 = build(synthetic_config_dict)
        sid2 = build(synthetic_config_dict)
        assert sid1 == sid2

        prepared_dir = synthetic_config_dict["data"]["prepared_dir"]
        df = pd.read_parquet(Path(prepared_dir) / sid1 / "samples.parquet")
        df2 = pd.read_parquet(Path(prepared_dir) / sid2 / "samples.parquet")

        compare_cols = [c for c in df.columns if c != "created_at"]
        pd.testing.assert_frame_equal(
            df[compare_cols].reset_index(drop=True),
            df2[compare_cols].reset_index(drop=True),
        )
