"""新标签定义模板：复制本文件，填写 TODO 项后注册。

使用步骤（§4.10）：
  1. 复制本文件到 data_prep/labels/<your_name>.py
  2. 填写 name_pattern / params_key / spec_version / default_params
  3. 实现 label_names() / horizon_of() / compute()
  4. 在 data_prep/labels/__init__.py 加一行 import
  5. 在 YAML 的 target.label 与 data_prep.labels.params 中使用
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .base import LabelContext, LabelDefinition
from .registry import register_label


# @register_label  # ← 取消注释后自动注册
class TemplateLabelDefinition(LabelDefinition):
    """TODO: 替换为描述性类名。"""

    # 标签名的正则模式，捕获组对应配置参数
    name_pattern: str = r"^target_your_label_(\d+)d$"

    # 在 data_prep.labels.params.<params_key> 下取参数
    params_key: str = "your_label"

    # 计算口径变化时必须 +1
    spec_version: int = 1

    # 配置缺省时的回落值
    default_params: dict = {"horizon": 20, "drop_blocked_entries": True}

    def label_names(self, params: dict) -> List[str]:
        """返回当前 params 会产出的标签名列表（供缓存检查使用）。"""
        h = params.get("horizon", self.default_params["horizon"])
        return [f"target_your_label_{h}d"]

    def horizon_of(self, name: str, params: dict) -> Optional[int]:
        """解析前视跨度；若名称与 params 不兼容则抛 ValueError。"""
        m = re.fullmatch(r"^target_your_label_(\d+)d$", name)
        if not m:
            raise ValueError(f"{name!r} 不匹配 name_pattern")
        h = int(m.group(1))
        expected = int(params.get("horizon", self.default_params["horizon"]))
        if h != expected:
            raise ValueError(f"h={h} != params['horizon']={expected}")
        return h

    def compute(
        self,
        samples: pd.DataFrame,
        ctx: LabelContext,
        params: dict,
    ) -> Dict[str, pd.DataFrame]:
        """计算并返回 {label_name: frame}。

        frame 必须包含 §3.2 规定的全部 13 列（除 label_params_hash /
        samples_id / datebar_fingerprint / created_at，这四列由 _materialize 填充）。
        """
        h = int(params.get("horizon", self.default_params["horizon"]))
        lname = f"target_your_label_{h}d"
        n = len(samples)

        # TODO: 实现计算逻辑
        # 通过 ctx.get_arrays(sym) 获取非停牌序列
        # 使用 np.searchsorted(arr["idxd"], signal_date, side="right") 找入场位置
        target_arr = np.full(n, np.nan, dtype="float64")

        return {
            lname: pd.DataFrame({
                "sample_id": samples["sample_id"].to_numpy(dtype="int64"),
                "idxs": samples["idxs"].to_numpy(),
                "signal_date": samples["idxd"].to_numpy(dtype="int32"),
                "target": target_arr,
                "entry_idxd": np.full(n, -1, dtype="int32"),
                "exit_idxd": np.full(n, -1, dtype="int32"),
                "is_complete": np.zeros(n, dtype=bool),
                "is_entry_blocked": np.zeros(n, dtype=bool),
                "label_name": lname,
            })
        }
