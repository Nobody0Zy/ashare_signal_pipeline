"""M4 双模式测试：samples 模式行为、两模式同构、丢空标签、升序、缺标签报错、签名触发重跑。"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# ── 辅助：构建最小合成测试环境 ─────────────────────────────────────────────────


def _make_samples(tmp_dir: Path, samples_id: str, n: int = 20) -> pd.DataFrame:
    """生成合成 samples.parquet + manifest.json。"""
    rng = np.random.default_rng(42)
    sample_ids = np.arange(n, dtype="int64") + 1_00_000_000_000_000  # 合法 sample_id
    df = pd.DataFrame({
        "sample_id": sample_ids,
        "idxs": [f"sh60{i:04d}" for i in range(n)],
        "idxd": np.array([20200101 + i for i in range(n)], dtype="int32"),
        "open": rng.random(n).astype("float32"),
        "high": rng.random(n).astype("float32"),
        "low": rng.random(n).astype("float32"),
        "close": rng.random(n).astype("float32"),
        "volume": rng.random(n).astype("float32"),
        "amount": rng.random(n).astype("float32"),
        "is_paused": np.zeros(n, dtype=bool),
        "marks": np.zeros(n, dtype="int16"),
        "sampling_method": "bollinger_breakout",
        "sampling_params_hash": "00000000",
        "datebar_fingerprint": "aaa_1_2_3_4",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    out_dir = tmp_dir / samples_id
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / "samples.parquet", index=False)

    sid_arr = df["sample_id"].to_numpy().astype("<i8")
    sset_hash = hashlib.sha256(np.sort(sid_arr).tobytes()).hexdigest()[:16]
    manifest = {
        "samples_id": samples_id,
        "sampling_method": "bollinger_breakout",
        "sampling_params": {"ma_period": 20.0, "std_mult": 2.0},
        "params_hash": "00000000",
        "datebar_fingerprint": "aaa_1_2_3_4",
        "samples_set_hash": sset_hash,
        "universe": {"exclude_prefixes": [], "min_history_days": 60},
        "date_range": {"start": None, "end": None},
        "min_history_days": 60,
        "max_samples": None,
        "rows_before_cap": 0,
        "sampling_rate": 1.0,
        "year_quota": {},
        "rows": n,
        "n_symbols": n,
        "created_at": "2026-01-01T00:00:00+00:00",
        "code_version": "",
    }
    with (out_dir / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return df


def _make_labels(tmp_dir: Path, samples_id: str, samples_df: pd.DataFrame,
                 label_name: str = "target_future_20d",
                 null_fraction: float = 0.3) -> None:
    """生成合成标签文件（部分 target 为 NaN）。"""
    rng = np.random.default_rng(99)
    n = len(samples_df)
    target_vals = rng.random(n).astype("float64")
    null_mask = rng.random(n) < null_fraction
    target_vals[null_mask] = float("nan")

    label_df = pd.DataFrame({
        "sample_id": samples_df["sample_id"].values,
        "idxs": samples_df["idxs"].values,
        "signal_date": samples_df["idxd"].values.astype("int32"),
        "target": target_vals,
        "entry_idxd": np.array([20200102 + i for i in range(n)], dtype="int32"),
        "exit_idxd": np.array([20200122 + i for i in range(n)], dtype="int32"),
        "is_complete": np.ones(n, dtype=bool),
        "is_entry_blocked": np.zeros(n, dtype=bool),
        "label_name": label_name,
        "label_params_hash": "abcd1234",
        "samples_id": samples_id,
        "datebar_fingerprint": "aaa_1_2_3_4",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    labels_dir = tmp_dir / "labels" / samples_id
    labels_dir.mkdir(parents=True, exist_ok=True)
    label_df.to_parquet(labels_dir / f"{label_name}.parquet", index=False)

    non_null = int((~null_mask).sum())
    index_data = {
        label_name: {
            "params_hash": "abcd1234",
            "rows": n,
            "non_null_rows": non_null,
            "coverage": non_null / n,
            "created_at": "2026-01-01T00:00:00+00:00",
            "holding_fingerprint": "",
            "samples_set_hash": "dummy",
        }
    }
    with (labels_dir / "_index.json").open("w", encoding="utf-8") as fh:
        json.dump(index_data, fh)


def _make_fake_datebar() -> "pd.DataFrame":
    """返回最小合成 datebar DataFrame（用于 mock _clean_datebar）。"""
    import pandas as pd
    n = 10
    return pd.DataFrame({
        "idxs": [f"sh60{i:04d}" for i in range(n)],
        "idxd": [20200101 + i for i in range(n)],
        "open": [1.0] * n,
        "high": [1.1] * n,
        "low": [0.9] * n,
        "close": [1.05] * n,
        "volume": [1000.0] * n,
        "amount": [5000.0] * n,
        "paused": [False] * n,
    })


def _make_params_and_paths(tmp_path: Path, samples_id: str,
                            mode: str = "samples",
                            label_name: str = "target_future_20d") -> tuple:
    """构造 (PipelineParams, Paths) 对，指向合成数据。"""
    from pipeline.config import PipelineParams, build_paths

    params = PipelineParams.from_dict({
        "input": {"mode": mode},
        "target": {
            "source": "label_file",
            "label": label_name,
            "horizon_days": 20,
            "samples_id": samples_id,  # 显式指定（跳过 auto 推导的 datebar 加载）
        },
        "window": {"lookback_days": 60},
        "data": {
            "holding_file": str(tmp_path / "dummy_holding.parquet"),
            "datebar_file": str(tmp_path / "dummy_datebar.pkl"),
            "prepared_dir": str(tmp_path),
            "labels_dir": str(tmp_path / "labels"),
        },
    })
    paths = build_paths(tmp_path, "test_step1_dual.json", params)
    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    return params, paths


# ── 两模式 checks 结构同构 ─────────────────────────────────────────────────────


REQUIRED_CHECKS = {
    "holding_exists", "holding_columns", "holding_target_non_null",
    "datebar_exists", "datebar_columns", "datebar_sorted",
}

REQUIRED_DETAILS_16 = {
    "input_mode", "coupling_mode", "samples_rows", "sample_count",
    "dropped_no_target", "target_coverage", "signal_date_min", "signal_date_max",
    "target_stats", "datebar_rows", "datebar_fingerprint", "samples_id",
    "samples_set_hash", "label_name", "label_hash", "label_non_null_ratio",
}


def test_holding_file_mode_has_16_detail_keys(tmp_path, synthetic_holding_path, synthetic_datebar_path):
    """holding_file 模式 details 含 16 个必需键。"""
    from pipeline.config import PipelineParams, build_paths
    from pipeline.step1_clean import compute_step1

    params = PipelineParams.from_dict({
        "input": {"mode": "holding_file"},
        "data": {
            "holding_file": str(synthetic_holding_path),
            "datebar_file": str(synthetic_datebar_path),
        },
    })
    paths = build_paths(tmp_path, "hf_16keys.json", params)
    paths.processed_dir.mkdir(parents=True, exist_ok=True)

    result = compute_step1(paths, "smoke", params)
    assert REQUIRED_DETAILS_16.issubset(result["details"].keys()), (
        f"缺失键: {REQUIRED_DETAILS_16 - set(result['details'].keys())}"
    )
    assert set(result["checks"].keys()) == REQUIRED_CHECKS


def test_holding_file_mode_default_values(tmp_path, synthetic_holding_path, synthetic_datebar_path):
    """holding_file 模式下字符串缺省键均为 '' 且 dropped_no_target=0。"""
    from pipeline.config import PipelineParams, build_paths
    from pipeline.step1_clean import compute_step1

    params = PipelineParams.from_dict({
        "input": {"mode": "holding_file"},
        "data": {
            "holding_file": str(synthetic_holding_path),
            "datebar_file": str(synthetic_datebar_path),
        },
    })
    paths = build_paths(tmp_path, "hf_defaults.json", params)
    paths.processed_dir.mkdir(parents=True, exist_ok=True)

    result = compute_step1(paths, "smoke", params)
    d = result["details"]
    assert d["input_mode"] == "holding_file"
    assert d["coupling_mode"] == "path_dependent"
    assert d["dropped_no_target"] == 0
    assert d["target_coverage"] == 1.0
    for k in ("samples_id", "samples_set_hash", "label_name", "label_hash", "datebar_fingerprint"):
        assert d[k] == "", f"{k} 应为空字符串，实为 {d[k]!r}"
    assert d["label_non_null_ratio"] == "", (
        f"label_non_null_ratio 应为 '' (holding_file 模式), 实为 {d['label_non_null_ratio']!r}"
    )


# ── samples 模式：丢空标签 ─────────────────────────────────────────────────────


def _prepare_samples_env(tmp_path: Path, samples_id: str = "test_samp_00000000_12345678"):
    """准备合成 samples + labels 环境，返回 (samples_df, params, paths)。"""
    # patch data_prep.ids.derive_datebar_fingerprint 以避免加载真实 raw 文件
    samples_df = _make_samples(tmp_path, samples_id)
    _make_labels(tmp_path, samples_id, samples_df, null_fraction=0.3)
    params, paths = _make_params_and_paths(tmp_path, samples_id)
    return samples_df, params, paths


def test_samples_mode_drops_null_labels(tmp_path):
    """samples 模式丢弃 target 为空的行，dropped_no_target > 0。"""
    from pipeline.step1_clean import _clean_samples
    import unittest.mock as mock

    samples_id = "test_samp_00000000_12345678"
    samples_df, params, paths = _prepare_samples_env(tmp_path, samples_id)
    n = len(samples_df)

    with mock.patch("data_prep.ids.derive_datebar_fingerprint", return_value="aaa_1_2_3_4"), \
         mock.patch("pipeline.step1_clean._derive_samples_id_for_config", return_value=samples_id), \
         mock.patch("pipeline.step1_clean._clean_datebar", return_value=_make_fake_datebar()):
        result = _clean_samples(paths, params, "smoke")

    d = result["details"]
    assert d["samples_rows"] == n
    assert d["dropped_no_target"] > 0, "null_fraction=0.3 应导致 dropped_no_target > 0"
    assert d["sample_count"] == n - d["dropped_no_target"]
    assert d["target_coverage"] == d["sample_count"] / n
    assert result["checks"]["holding_target_non_null"] is True


def test_samples_mode_ascending_sample_id(tmp_path):
    """samples 模式 holding_clean 按 sample_id 升序。"""
    from pipeline.step1_clean import _clean_samples
    import unittest.mock as mock

    samples_id = "test_samp_00000000_12345678"
    samples_df, params, paths = _prepare_samples_env(tmp_path, samples_id)

    with mock.patch("data_prep.ids.derive_datebar_fingerprint", return_value="aaa_1_2_3_4"), \
         mock.patch("pipeline.step1_clean._derive_samples_id_for_config", return_value=samples_id), \
         mock.patch("pipeline.step1_clean._clean_datebar", return_value=_make_fake_datebar()):
        result = _clean_samples(paths, params, "smoke")

    hc = pd.read_parquet(paths.root / result["output_files"][0])
    assert (hc["sample_id"].diff().dropna() > 0).all(), "holding_clean 应按 sample_id 升序"


def test_samples_mode_has_16_detail_keys(tmp_path):
    """samples 模式 details 含 16 个必需键。"""
    from pipeline.step1_clean import _clean_samples
    import unittest.mock as mock

    samples_id = "test_samp_00000000_12345678"
    _, params, paths = _prepare_samples_env(tmp_path, samples_id)

    with mock.patch("data_prep.ids.derive_datebar_fingerprint", return_value="aaa_1_2_3_4"), \
         mock.patch("pipeline.step1_clean._derive_samples_id_for_config", return_value=samples_id), \
         mock.patch("pipeline.step1_clean._clean_datebar", return_value=_make_fake_datebar()):
        result = _clean_samples(paths, params, "smoke")

    assert REQUIRED_DETAILS_16.issubset(result["details"].keys())
    assert set(result["checks"].keys()) == REQUIRED_CHECKS


# ── samples 模式：缺标签报错 ───────────────────────────────────────────────────


def test_samples_mode_missing_label_raises_file_not_found(tmp_path):
    """缺标签文件时报 FileNotFoundError，不隐式构建（§7 M4）。"""
    from pipeline.step1_clean import _clean_samples
    import unittest.mock as mock

    samples_id = "test_samp_00000000_12345678"
    samples_df = _make_samples(tmp_path, samples_id)
    # 不创建标签文件
    params, paths = _make_params_and_paths(tmp_path, samples_id)

    with mock.patch("data_prep.ids.derive_datebar_fingerprint", return_value="aaa_1_2_3_4"), \
         mock.patch("pipeline.step1_clean._derive_samples_id_for_config", return_value=samples_id), \
         mock.patch("pipeline.step1_clean._clean_datebar", return_value=_make_fake_datebar()):
        with pytest.raises(FileNotFoundError):
            _clean_samples(paths, params, "smoke")


# ── runner 签名：触发重跑 ──────────────────────────────────────────────────────


def test_runner_reruns_step1_on_missing_input_mode():
    """旧 state 缺 input_mode → _completed_step_matches_current_inputs 返回 False（触发重跑）。"""
    from pipeline.runner import _completed_step_matches_current_inputs
    from pipeline.config import PipelineParams

    params = PipelineParams.from_dict({
        "input": {"mode": "holding_file"},
    })
    mock_paths = MagicMock()

    # 旧 state：details 中没有 input_mode 键
    old_details = {"sample_count": 1000, "datebar_rows": 5000}
    current = {"details": old_details}

    result = _completed_step_matches_current_inputs(mock_paths, params, 1, current)
    assert result is False, "缺 input_mode 时应触发重跑（返回 False）"


def test_runner_reruns_step1_on_mode_change():
    """input_mode 从 holding_file 变为 samples → 触发重跑。"""
    from pipeline.runner import _completed_step_matches_current_inputs
    from pipeline.config import PipelineParams

    params = PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "holding_return", "label": "", "horizon_days": 0},
        "window": {"lookback_days": 60},
    })
    mock_paths = MagicMock()

    # State 记录的是旧的 holding_file 模式
    current = {"details": {"input_mode": "holding_file", "samples_id": "", "label_hash": "", "datebar_fingerprint": ""}}

    result = _completed_step_matches_current_inputs(mock_paths, params, 1, current)
    assert result is False, "input_mode 变化时应触发重跑"


def test_runner_skips_step1_holding_file_unchanged():
    """holding_file 模式下，input_mode 未变 → 不触发重跑（返回 True）。"""
    from pipeline.runner import _completed_step_matches_current_inputs
    from pipeline.config import PipelineParams

    params = PipelineParams.from_dict({
        "input": {"mode": "holding_file"},
    })
    mock_paths = MagicMock()

    # State 记录的是相同模式
    current = {"details": {"input_mode": "holding_file"}}

    result = _completed_step_matches_current_inputs(mock_paths, params, 1, current)
    assert result is True, "相同 holding_file 模式时不应触发重跑"
