"""V1–V14 配置校验规则的正反用例（M3）。"""

from __future__ import annotations

import pytest

from pipeline.config import PipelineParams


# ── 辅助：最小合法 holding_file 配置字典 ──────────────────────────────────────

def _h(**kwargs) -> dict:
    """最小合法 holding_file 配置（带关键字覆盖）。"""
    base: dict = {}
    base.update(kwargs)
    return base


def _samples_dict(**kwargs) -> dict:
    """最小合法 samples + label_file 配置（window.lookback_days=60 满足 V6）。"""
    base: dict = {
        "input": {"mode": "samples"},
        "target": {
            "source": "label_file",
            "label": "target_future_20d",
            "horizon_days": 20,
            "on_missing_samples": "error",
            "on_missing_label": "error",
        },
        "window": {"lookback_days": 60},  # V6: min_history_days(60) >= lookback_days(60)
    }
    base.update(kwargs)
    return base


# ── V1 ───────────────────────────────────────────────────────────────────────

def test_v1_valid_holding_file_mode():
    PipelineParams.from_dict({"input": {"mode": "holding_file"}})


def test_v1_valid_samples_mode():
    # samples mode + holding_return 是合法组合（V13 要求 label 为空）
    PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "holding_return", "label": "", "horizon_days": 0},
        "window": {"lookback_days": 60},
    })


def test_v1_invalid_input_mode():
    with pytest.raises(ValueError, match="input.mode"):
        PipelineParams.from_dict({"input": {"mode": "invalid_mode"}})


def test_v1_invalid_target_source():
    with pytest.raises(ValueError, match="target.source"):
        PipelineParams.from_dict({"target": {"source": "unknown_source"}})


# ── V2 ───────────────────────────────────────────────────────────────────────

def test_v2_holding_file_holding_return_valid():
    PipelineParams.from_dict({
        "input": {"mode": "holding_file"},
        "target": {"source": "holding_return", "label": "", "horizon_days": 0},
    })


def test_v2_holding_file_label_file_rejected():
    with pytest.raises(ValueError, match="label_file"):
        PipelineParams.from_dict({
            "input": {"mode": "holding_file"},
            "target": {"source": "label_file", "label": "target_future_20d", "horizon_days": 20},
        })


def test_v2_holding_file_nonempty_label_rejected():
    with pytest.raises(ValueError, match="label"):
        PipelineParams.from_dict({
            "input": {"mode": "holding_file"},
            "target": {"source": "holding_return", "label": "some_label", "horizon_days": 0},
        })


# ── V3 ───────────────────────────────────────────────────────────────────────

def test_v3_label_file_requires_samples_mode():
    # holding_file + label_file → V2 fires first (also covers V3 path)
    with pytest.raises(ValueError):
        PipelineParams.from_dict({
            "input": {"mode": "holding_file"},
            "target": {"source": "label_file", "label": "target_future_20d", "horizon_days": 20},
        })


def test_v3_empty_label_rejected():
    with pytest.raises(ValueError, match="label"):
        PipelineParams.from_dict({
            "input": {"mode": "samples"},
            "target": {"source": "label_file", "label": "", "horizon_days": 0},
            "window": {"lookback_days": 60},
        })


def test_v3_on_missing_samples_build_rejected():
    with pytest.raises(ValueError, match="build_samples"):
        PipelineParams.from_dict({
            "input": {"mode": "samples"},
            "target": {
                "source": "label_file",
                "label": "target_future_20d",
                "horizon_days": 20,
                "on_missing_samples": "build",
            },
            "window": {"lookback_days": 60},
        })


def test_v3_on_missing_label_build_rejected():
    with pytest.raises(ValueError, match="build_labels"):
        PipelineParams.from_dict({
            "input": {"mode": "samples"},
            "target": {
                "source": "label_file",
                "label": "target_future_20d",
                "horizon_days": 20,
                "on_missing_label": "build",
            },
            "window": {"lookback_days": 60},
        })


# ── V4 ───────────────────────────────────────────────────────────────────────

def test_v4_known_label_resolves():
    PipelineParams.from_dict(_samples_dict())  # target_future_20d, horizon_days=20


def test_v4_unknown_label_raises():
    with pytest.raises(ValueError, match="pattern"):
        PipelineParams.from_dict({
            "input": {"mode": "samples"},
            "target": {"source": "label_file", "label": "nonexistent_xyz_label", "horizon_days": 0},
            "window": {"lookback_days": 60},
        })


def test_v4_horizon_mismatch_raises():
    with pytest.raises(ValueError, match="horizon"):
        PipelineParams.from_dict({
            "input": {"mode": "samples"},
            "target": {"source": "label_file", "label": "target_future_20d", "horizon_days": 5},
            "window": {"lookback_days": 60},
        })


def test_v4_zero_horizon_for_holding_return():
    # target_holding_return has horizon=None → horizon_days must be 0
    PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "label_file", "label": "target_holding_return", "horizon_days": 0},
        "window": {"lookback_days": 60},
    })


def test_v4_nonzero_horizon_for_none_horizon_label_raises():
    with pytest.raises(ValueError, match="horizon"):
        PipelineParams.from_dict({
            "input": {"mode": "samples"},
            "target": {"source": "label_file", "label": "target_holding_return", "horizon_days": 5},
            "window": {"lookback_days": 60},
        })


# ── V5 ───────────────────────────────────────────────────────────────────────

def test_v5_buffer_covers_max_lookback_horizon():
    # buffer=60, lookback=20, horizon=40 → max(20,40)=40, 60>=40 → pass
    PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "label_file", "label": "target_future_40d", "horizon_days": 40},
        "window": {"lookback_days": 20},
        "time_split": {"buffer_trading_days": 60, "enforce_buffer": True},
        "data_prep": {"universe": {"min_history_days": 20}},
    })


def test_v5_buffer_less_than_horizon_raises():
    # buffer=30, lookback=20, horizon=40 → max(20,40)=40, 30<40 → fail
    with pytest.raises(ValueError, match="buffer"):
        PipelineParams.from_dict({
            "input": {"mode": "samples"},
            "target": {"source": "label_file", "label": "target_future_40d", "horizon_days": 40},
            "window": {"lookback_days": 20},
            "time_split": {"buffer_trading_days": 30, "enforce_buffer": True},
            "data_prep": {"universe": {"min_history_days": 20}},
        })


# ── V6 ───────────────────────────────────────────────────────────────────────

def test_v6_min_history_less_than_lookback_raises():
    with pytest.raises(ValueError, match="min_history_days"):
        PipelineParams.from_dict({
            "input": {"mode": "samples"},
            "window": {"lookback_days": 100},
            "data_prep": {"universe": {"min_history_days": 50}},
        })


def test_v6_min_history_equal_lookback_ok():
    PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "holding_return", "label": "", "horizon_days": 0},
        "window": {"lookback_days": 60},
        "data_prep": {"universe": {"min_history_days": 60}},
    })


# ── V7 ───────────────────────────────────────────────────────────────────────

def test_v7_valid_method():
    PipelineParams.from_dict({"data_prep": {"samples": {"method": "bollinger_breakout"}}})


def test_v7_unknown_method_raises():
    with pytest.raises(ValueError, match="unknown_method_xyz"):
        PipelineParams.from_dict({"data_prep": {"samples": {"method": "unknown_method_xyz"}}})


def test_v7_negative_params_raises():
    with pytest.raises(ValueError, match="正数"):
        PipelineParams.from_dict({
            "data_prep": {"samples": {"method": "bollinger_breakout",
                                      "params": {"ma_period": -5.0, "std_mult": 2.0}}}
        })


def test_v7_zero_params_raises():
    with pytest.raises(ValueError, match="正数"):
        PipelineParams.from_dict({
            "data_prep": {"samples": {"method": "bollinger_breakout",
                                      "params": {"ma_period": 0.0, "std_mult": 2.0}}}
        })


# ── V8 ───────────────────────────────────────────────────────────────────────

def test_v8_auto_samples_id_ok():
    PipelineParams.from_dict({"target": {"source": "holding_return", "samples_id": "auto"}})


def test_v8_invalid_format_raises():
    with pytest.raises(ValueError, match="samples_id"):
        PipelineParams.from_dict({
            "target": {"source": "holding_return", "samples_id": "Invalid-ID!"},
        })


def test_v8_uppercase_raises():
    with pytest.raises(ValueError, match="samples_id"):
        PipelineParams.from_dict({
            "target": {"source": "holding_return", "samples_id": "UPPER_CASE"},
        })


# ── V10 ──────────────────────────────────────────────────────────────────────

def test_v10_legacy_max_holding_days_null_ok():
    PipelineParams.from_dict({
        "data_prep": {"samples": {"method": "legacy_holding"}},
        "sample_filter": {"max_holding_days": None},
    })


def test_v10_legacy_max_holding_days_set_raises():
    with pytest.raises(ValueError, match="max_holding_days"):
        PipelineParams.from_dict({
            "data_prep": {"samples": {"method": "legacy_holding"}},
            "sample_filter": {"max_holding_days": 30},
        })


# ── V11 ──────────────────────────────────────────────────────────────────────

def test_v11_warns_for_selection_score_in_samples_mode(capsys):
    PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "holding_return", "label": "", "horizon_days": 0},
        "manual_features": {"include_signal_date_selection_score": True},
        "window": {"lookback_days": 60},
    })
    captured = capsys.readouterr()
    # V11 警告输出到 stderr（不污染 stdout JSON）
    assert "警告" in captured.err or "signal_date_selection_score" in captured.err


def test_v11_no_warning_for_holding_file_mode(capsys):
    PipelineParams.from_dict({
        "input": {"mode": "holding_file"},
        "manual_features": {"include_signal_date_selection_score": True},
    })
    captured = capsys.readouterr()
    # 旧路径不打警告
    assert "signal_date_selection_score" not in captured.err


# ── V12 ──────────────────────────────────────────────────────────────────────

def test_v12_all_builtin_label_specs_valid():
    # 加载默认配置触发 V12 检查；所有内置标签 spec_version >= 1
    PipelineParams.from_dict({})


# ── V13 ──────────────────────────────────────────────────────────────────────

def test_v13_samples_holding_return_empty_label_ok():
    PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "holding_return", "label": "", "horizon_days": 0},
        "window": {"lookback_days": 60},
    })


def test_v13_samples_holding_return_nonempty_label_raises():
    with pytest.raises(ValueError, match="label"):
        PipelineParams.from_dict({
            "input": {"mode": "samples"},
            "target": {"source": "holding_return", "label": "some_label", "horizon_days": 0},
            "window": {"lookback_days": 60},
        })


def test_v13_samples_holding_return_on_missing_build_raises():
    with pytest.raises(ValueError, match="build_labels"):
        PipelineParams.from_dict({
            "input": {"mode": "samples"},
            "target": {"source": "holding_return", "label": "", "horizon_days": 0,
                       "on_missing_label": "build"},
            "window": {"lookback_days": 60},
        })


# ── V14 ──────────────────────────────────────────────────────────────────────

def test_v14_legacy_null_constraints_ok():
    PipelineParams.from_dict({
        "data_prep": {
            "samples": {"method": "legacy_holding", "max_samples": None},
            "date_range_start": None,
            "date_range_end": None,
        },
    })


def test_v14_legacy_max_samples_set_raises():
    with pytest.raises(ValueError, match="max_samples"):
        PipelineParams.from_dict({
            "data_prep": {
                "samples": {"method": "legacy_holding", "max_samples": 1000},
            },
        })


def test_v14_legacy_date_range_start_raises():
    with pytest.raises(ValueError, match="date_range_start"):
        PipelineParams.from_dict({
            "data_prep": {
                "samples": {"method": "legacy_holding"},
                "date_range_start": 20200101,
            },
        })


def test_v14_legacy_date_range_end_raises():
    with pytest.raises(ValueError, match="date_range_end"):
        PipelineParams.from_dict({
            "data_prep": {
                "samples": {"method": "legacy_holding"},
                "date_range_end": 20231231,
            },
        })
