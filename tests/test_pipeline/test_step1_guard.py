"""M1 守卫测试（已更新至 M4）：samples 模式不再抛 NotImplementedError，改为实际执行。"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


def test_step1_samples_mode_no_longer_raises_not_implemented():
    """M4 实现了 samples 模式：compute_step1 不再对 input.mode=samples 抛 NotImplementedError。"""
    from pipeline.config import PipelineParams
    from pipeline.step1_clean import compute_step1

    params = PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "holding_return", "label": "", "horizon_days": 0},
        "window": {"lookback_days": 60},
    })
    mock_paths = MagicMock()

    # M4 后 samples 模式已实现：会尝试加载数据文件，不会抛 NotImplementedError
    with pytest.raises(Exception) as exc_info:
        compute_step1(mock_paths, "smoke", params)

    assert not isinstance(exc_info.value, NotImplementedError), (
        "M4 后 samples 模式已实现，不应再抛 NotImplementedError（guard 已改为 early-return 分派）"
    )


def test_step1_samples_mode_raises_on_missing_data():
    """samples 模式缺少 raw 数据时抛出文件/数据相关错误，而非 NotImplementedError。"""
    from pipeline.config import PipelineParams
    from pipeline.step1_clean import compute_step1

    params = PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "holding_return", "label": "", "horizon_days": 0},
        "window": {"lookback_days": 60},
    })

    with pytest.raises(Exception) as exc_info:
        compute_step1(MagicMock(), "full", params)

    assert not isinstance(exc_info.value, NotImplementedError)


def test_step1_holding_file_mode_does_not_raise_guard(tmp_path):
    """holding_file 模式不触发 samples 守卫（会因文件缺失而在后面报错，不是 NotImplementedError）。"""
    from pathlib import Path
    from pipeline.config import PipelineParams, build_paths

    params = PipelineParams.from_dict({
        "input": {"mode": "holding_file"},
        "target": {"source": "holding_return"},
        "data": {
            "holding_file": str(tmp_path / "nonexistent.parquet"),
            "datebar_file": str(tmp_path / "nonexistent.pkl"),
            "prepared_dir": str(tmp_path / "prepared"),
            "labels_dir": str(tmp_path / "labels"),
        },
    })
    paths = build_paths(Path.cwd(), "test_guard.json", params)

    # 应抛出文件缺失或读取错误，而不是 NotImplementedError
    with pytest.raises(Exception) as exc_info:
        from pipeline.step1_clean import compute_step1
        compute_step1(paths, "smoke", params)

    assert not isinstance(exc_info.value, NotImplementedError), (
        "holding_file 模式不应触发 samples 守卫"
    )
