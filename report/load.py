"""report/load.py — summary loading and formatting helpers (design §2.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def load_summary(path: Path) -> dict:
    """Load and return report_summary.json as a plain dict."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get(summary: dict, dotted: str, default: Any = None) -> Any:
    """Dot-notation path access into nested dict.

    Example: get(s, "input_identity.input_mode") returns s["input_identity"]["input_mode"].
    """
    obj = summary
    for part in dotted.split("."):
        if not isinstance(obj, dict):
            return default
        obj = obj.get(part, default)
        if obj is default:
            return default
    return obj


def fmt_pct(value: Any, digits: int = 2) -> str:
    """Format a decimal fraction as a percentage string, e.g. 0.123 → '+12.30%'."""
    if value is None or (isinstance(value, float) and not (value == value)):
        return "—"
    try:
        pct = float(value) * 100
        sign = "+" if pct > 0 else ""
        return f"{sign}{pct:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def fmt_num(value: Any, digits: int = 4, sign: bool = False) -> str:
    """Format a numeric value with given decimal places."""
    if value is None or (isinstance(value, float) and not (value == value)):
        return "—"
    try:
        formatted = f"{float(value):.{digits}f}"
        if sign and float(value) > 0:
            return "+" + formatted
        return formatted
    except (TypeError, ValueError):
        return "—"


def fmt_int(value: Any) -> str:
    """Format an integer with thousands separators."""
    if value is None:
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def fmt_hms(seconds: Any) -> str:
    """Format seconds as HH:MM:SS."""
    if seconds is None:
        return "—"
    try:
        total = int(float(seconds))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    except (TypeError, ValueError):
        return "—"


def fmt_bytes(nbytes: Any) -> str:
    """Format byte count as human-readable string (1 decimal place)."""
    if nbytes is None:
        return "—"
    try:
        n = float(nbytes)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"
