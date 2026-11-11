"""标签基类、LabelContext 与行契约校验（§4.5 / §3.2）。"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

LABEL_REQUIRED_COLUMNS: set[str] = {
    "sample_id", "idxs", "signal_date", "target",
    "entry_idxd", "exit_idxd", "is_complete", "is_entry_blocked",
    "label_name", "label_params_hash", "samples_id",
    "datebar_fingerprint", "created_at",
}


def build_label_sym_arrays(df: pd.DataFrame) -> Dict[str, dict]:
    """为标签计算构建逐 symbol 的非停牌数组。

    返回 {sym: {"close", "open", "marks", "idxd"}}，
    其中所有数组仅包含非停牌行，按 idxd 升序排列。
    """
    result: Dict[str, dict] = {}
    for sym, group in df.groupby("idxs", sort=False):
        g = group.reset_index(drop=True)
        non_paused = g[g["paused"].ne(1)].reset_index(drop=True)
        if non_paused.empty:
            continue
        marks_col = non_paused["marks"] if "marks" in non_paused.columns else pd.Series(
            np.zeros(len(non_paused), dtype="int16"), name="marks"
        )
        result[str(sym)] = {
            "close": non_paused["close"].to_numpy(dtype="float32"),
            "open": non_paused["open"].to_numpy(dtype="float32"),
            "marks": marks_col.fillna(0).astype("int32").to_numpy(),
            "idxd": non_paused["idxd"].to_numpy(dtype="int32"),
        }
    return result


class LabelContext:
    """前瞻上下文：只提供信号日之后的窗口访问，禁止访问历史数据（§3.6 / §4.5）。

    与 SampleContext 是两个不可互换的类型：此类不提供任何过去窗口方法。
    """

    def __init__(self, sym_arrays: Dict[str, dict]) -> None:
        self._sym_arrays = sym_arrays

    def get_arrays(self, symbol: str) -> Optional[dict]:
        """返回该 symbol 的非停牌序列数组，不存在返回 None。"""
        return self._sym_arrays.get(symbol)


class LabelDefinition:
    """标签定义基类。

    子类必须声明 name_pattern / params_key / spec_version / default_params，
    并实现 label_names() / horizon_of() / compute()。
    """

    name_pattern: str = ""
    params_key: str = ""
    spec_version: int = 1
    default_params: dict = {}

    def label_names(self, params: dict) -> List[str]:
        """返回当前 params 下此定义会产出的标签名列表（用于缓存检查）。"""
        raise NotImplementedError

    def horizon_of(self, name: str, params: dict) -> Optional[int]:
        """由名称 + 参数解析前视跨度（非停牌交易日）；无前视返回 None。"""
        raise NotImplementedError

    def compute(
        self,
        samples: pd.DataFrame,
        ctx: LabelContext,
        params: dict,
    ) -> Dict[str, pd.DataFrame]:
        """计算标签，返回 {标签名: frame}。

        每个 frame 须包含除 label_params_hash / samples_id / datebar_fingerprint /
        created_at 之外的所有 §3.2 列（这四列由 _materialize 填充）。
        """
        raise NotImplementedError

    def prepare_params(self, config_dict: dict, raw_params: dict) -> dict:
        """在 compute 之前对 params 做补充（例如注入文件路径）。

        默认不修改。子类可以 override 以注入内部参数（以 _ 开头的键）。
        """
        return raw_params


def validate_label_frame(frame: pd.DataFrame) -> None:
    """校验 §3.2 列契约：缺列即抛 ValueError。"""
    missing = LABEL_REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"标签帧缺少必需列: {sorted(missing)}")
