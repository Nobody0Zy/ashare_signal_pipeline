"""M1 守卫：input.mode=samples 在 step1 顶部显式拒绝（M4 才开放双模式）。"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


def test_step1_raises_not_implemented_for_samples_mode():
    """compute_step1 对 input.mode=samples 应立即抛出 NotImplementedError。"""
    from pipeline.config import PipelineParams
    from pipeline.step1_clean import compute_step1

    params = PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "holding_return", "label": "", "horizon_days": 0},
        "window": {"lookback_days": 60},
    })
    mock_paths = MagicMock()

    with pytest.raises(NotImplementedError, match="samples"):
        compute_step1(mock_paths, "smoke", params)


def test_step1_raises_contains_m4_hint():
    """错误消息应提示 M4 或工具命令，便于用户定位。"""
    from pipeline.config import PipelineParams
    from pipeline.step1_clean import compute_step1

    params = PipelineParams.from_dict({
        "input": {"mode": "samples"},
        "target": {"source": "holding_return", "label": "", "horizon_days": 0},
        "window": {"lookback_days": 60},
    })

    with pytest.raises(NotImplementedError) as exc_info:
        compute_step1(MagicMock(), "full", params)

    msg = str(exc_info.value)
    assert "samples" in msg


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
