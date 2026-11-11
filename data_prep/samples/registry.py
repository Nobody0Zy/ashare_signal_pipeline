"""采样方法注册表。"""

from __future__ import annotations

from typing import Dict, Type

from .base import SampleDefinition

_REGISTRY: Dict[str, SampleDefinition] = {}


def register_sample(cls: Type[SampleDefinition]) -> Type[SampleDefinition]:
    """类装饰器：将采样方法注册到全局注册表。"""
    instance = cls()
    _REGISTRY[instance.name] = instance
    return cls


def get_sample(name: str) -> SampleDefinition:
    """按名称获取采样方法实例；未找到时报错并列出可用名称。"""
    if name not in _REGISTRY:
        raise ValueError(
            f"未知采样方法: {name!r}，可用: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]
