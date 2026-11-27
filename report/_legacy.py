"""report/_legacy.py — preserved pre-M1 render implementations.

These functions delegate to the original render logic via sys.modules
to avoid circular imports. M6 deletes this module.

Usage (internal only):
  from report._legacy import render_markdown_report_legacy, render_leadership_html_legacy
"""

from __future__ import annotations

import sys
from typing import Any, Dict


def _get_ae():
    """Return the analyze_experiment_results module via sys.modules."""
    ae = sys.modules.get("__main__")
    if ae is not None and hasattr(ae, "_render_markdown_legacy"):
        return ae
    ae = sys.modules.get("analyze_experiment_results")
    if ae is not None and hasattr(ae, "_render_markdown_legacy"):
        return ae
    raise RuntimeError(
        "analyze_experiment_results not in sys.modules; "
        "ensure the main script is loaded before calling _legacy functions"
    )


def render_markdown_report_legacy(summary: Dict[str, Any]) -> str:
    """Call the original render_markdown_report logic via sys.modules.

    Expects summary["_render_ctx"] to contain all original parameters.
    """
    ae = _get_ae()
    ctx = summary.get("_render_ctx", {})
    return ae._render_markdown_legacy(
        bundle=ctx["bundle"],
        config=ctx["config"],
        clean_stats=ctx["clean_stats"],
        split_frames=ctx["split_frames"],
        quintile_df=ctx["quintile_df"],
        median_df=ctx["median_df"],
        median_uplift=ctx["median_uplift"],
        threshold_df=ctx["threshold_df"],
        threshold_summary=ctx["threshold_summary"],
        monotonic_df=ctx["monotonic_df"],
        monotonic_summary=ctx["monotonic_summary"],
        feature_importance_df=ctx.get("feature_importance_df"),
        feature_importance_summary=ctx["feature_importance_summary"],
        clip_percentiles=ctx["clip_percentiles"],
        state_payload=ctx["state_payload"],
        resolved_params=ctx["resolved_params"],
        model_feature_schema=ctx["model_feature_schema"],
    )


def render_leadership_html_legacy(summary: Dict[str, Any]) -> str:
    """Call the original render_leadership_html logic via sys.modules.

    Expects summary["_render_ctx"] to contain all original parameters.
    """
    ae = _get_ae()
    ctx = summary.get("_render_ctx", {})
    return ae._render_leadership_legacy(
        bundle=ctx["bundle"],
        config=ctx["config"],
        split_frames=ctx["split_frames"],
        quintile_df=ctx["quintile_df"],
        median_df=ctx["median_df"],
        median_uplift=ctx["median_uplift"],
        threshold_df=ctx["threshold_df"],
        threshold_summary=ctx["threshold_summary"],
        monotonic_summary=ctx["monotonic_summary"],
        feature_importance_df=ctx.get("feature_importance_df"),
        state_payload=ctx["state_payload"],
        resolved_params=ctx["resolved_params"],
    )
