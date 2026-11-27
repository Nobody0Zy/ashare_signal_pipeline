"""标签注册表与名称解析（§4.5 / §6.1）。"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple, Type

from .base import LabelDefinition

_REGISTRY: Dict[str, LabelDefinition] = {}


def register_label(cls: Type[LabelDefinition]) -> Type[LabelDefinition]:
    """类装饰器：将标签定义注册到全局注册表（以 name_pattern 为键）。"""
    instance = cls()
    _REGISTRY[instance.name_pattern] = instance
    return cls


def resolve_label(name: str) -> Tuple[LabelDefinition, dict, Optional[int]]:
    """解析标签名 → (definition, name_params, horizon)。

    name_params: 从名称正则中提取的分组（无命名组时用 {"1": ..., "2": ...}）。
    horizon:     definition.horizon_of(name, definition.default_params) 的结果。

    异常：
      - 未命中任何 pattern → ValueError，列出全部可用 pattern
      - 命中多个 pattern  → ValueError（注册冲突）
      - horizon_of 本身抛错（如 h ∉ horizons）→ 异常直接传播
    """
    matches = []
    for pattern, defn in _REGISTRY.items():
        m = re.fullmatch(pattern, name)
        if m:
            matches.append((defn, m))

    if not matches:
        available = sorted(_REGISTRY.keys())
        raise ValueError(
            f"标签名 {name!r} 未命中任何注册 pattern；"
            f"可用 pattern: {available}"
        )
    if len(matches) > 1:
        conflicts = [defn.name_pattern for defn, _ in matches]
        raise ValueError(
            f"标签名 {name!r} 命中多个注册 pattern（注册冲突）: {conflicts}"
        )

    defn, match = matches[0]
    if match.groupdict():
        name_params: dict = match.groupdict()
    else:
        name_params = {str(i): g for i, g in enumerate(match.groups(), 1)}

    horizon = defn.horizon_of(name, defn.default_params)
    return defn, name_params, horizon
