"""测试 ids.py 的编解码、不冲突性与 int64 安全性。"""

from __future__ import annotations

import pytest

from data_prep.ids import canonical_json, derive_sample_id, derive_samples_id, parse_sample_id


class TestDeriveAndParse:
    """derive_sample_id / parse_sample_id 往返测试。"""

    @pytest.mark.parametrize("idxs,idxd", [
        ("sh600000", 20200102),
        ("sz000001", 20200102),
        ("bj430001", 20210315),
        ("sh000001", 20200102),  # 指数（格式有效，解码不报错）
    ])
    def test_roundtrip(self, idxs, idxd):
        sid = derive_sample_id(idxs, idxd)
        decoded_idxs, decoded_idxd = parse_sample_id(sid)
        assert decoded_idxs == idxs
        assert decoded_idxd == idxd

    def test_sh000001_sz000001_no_collision(self):
        """sh000001 与 sz000001 在相同 idxd 下 sample_id 不同（市场前缀不同）。"""
        sid_sh = derive_sample_id("sh000001", 20200102)
        sid_sz = derive_sample_id("sz000001", 20200102)
        assert sid_sh != sid_sz

    def test_different_idxd_no_collision(self):
        sid1 = derive_sample_id("sh600000", 20200101)
        sid2 = derive_sample_id("sh600000", 20200102)
        assert sid1 != sid2

    def test_int64_no_overflow(self):
        """最大合法输入下 sample_id 不超出 int64 范围。"""
        import numpy as np
        # bj + code6=999999 + idxd=99999999（> 实际最大日期，仅用于溢出测试）
        sid = derive_sample_id("bj999999", 99999999)
        assert 0 < sid < 2 ** 63 - 1  # int64 max
        # 确保 numpy int64 也不溢出
        sid_np = np.int64(sid)
        assert int(sid_np) == sid

    def test_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="未知市场前缀"):
            derive_sample_id("hk000001", 20200102)


class TestCanonicalJson:
    def test_sort_keys(self):
        s = canonical_json({"b": 1, "a": 2})
        assert s.index('"a"') < s.index('"b"')

    def test_none_to_null(self):
        s = canonical_json({"x": None})
        assert "null" in s

    def test_deterministic(self):
        obj = {"method": "bollinger_breakout", "max_samples": None, "params": {"ma_period": 20.0}}
        assert canonical_json(obj) == canonical_json(obj)


class TestDeriveSamplesId:
    def test_format(self):
        sid = derive_samples_id("bollinger_breakout", "abc12345", "294b05049234_15547065_20070104_20250114_6090")
        assert sid == "bollinger_breakout_abc12345_294b0504"
