"""labels 包声明；import 触发内置定义注册。"""
from . import future_return as _fr  # noqa: F401 触发注册
from . import trend_quality as _tq  # noqa: F401 触发注册
from . import holding_return as _hr  # noqa: F401 触发注册
