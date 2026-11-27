"""report/schema.py — schema contract for report_summary.json (design §3.1)."""

from __future__ import annotations

from typing import Any, Dict, Optional

SCHEMA_VERSION = "report-summary/1"

REQUIRED_KEYS: tuple[str, ...] = (
    "schema_version", "run_env", "experiment_version", "run_modes",
    "generated_at", "input_artifacts", "analysis_scope", "pipeline_steps",
    "resolved_params", "model_feature_schema", "cleaning", "split_overview",
    "overall_test", "test_quintile", "test_median_split", "test_threshold_filters",
    "monotonic", "feature_importance_top20", "feature_alignment",
    "artifacts_generated", "input_identity", "fingerprints", "buffer",
    "feature_funnel", "clip_percentiles", "target_stats_by_split", "drift_hint",
    "artifact_sizes", "smoke_vs_full", "datacard_path", "reference_path",
    "workload", "param_effective", "determinism", "feature_drift", "label_overlap",
    "group_time_concentration", "monthly_returns",
)

OPTIONAL_KEYS: tuple[str, ...] = ("label_span_stats", "runs")

# Design §3.5 degradation matrix: missing key → behavior description
DEGRADE_MATRIX: Dict[str, str] = {
    "smoke_vs_full": "E7 只显示 full 列，备注「smoke 未执行」",
    "feature_drift": "X9.1 写「未计算：<原因>」",
    "label_overlap": "X9.2 写「未计算：<原因>」",
    "clip_percentiles": "X5 不显示 P1/P99，标 [WARN]",
    "artifact_sizes": "E6 只给目录级汇总",
    "workload": "E8 退化为「耗时 + 规模」，不给归因列",
    "param_effective": "E10 整节省略并标 [WARN]",
    "label_span_stats": "仅 holding_file 允许为 null：X1.3 不显示有效持有期行",
}


class SchemaError(ValueError):
    """Raised when report_summary.json fails schema validation."""


def validate(summary: Dict[str, Any]) -> None:
    """Raise SchemaError if summary is missing any required key.

    Does NOT check optional or conditional keys (label_span_stats is handled by A-29).
    """
    if not isinstance(summary, dict):
        raise SchemaError(f"summary must be dict, got {type(summary)}")
    sv = summary.get("schema_version")
    if sv != SCHEMA_VERSION:
        raise SchemaError(f"schema_version mismatch: expected {SCHEMA_VERSION!r}, got {sv!r}")
    missing = [k for k in REQUIRED_KEYS if k not in summary]
    if missing:
        raise SchemaError(f"Missing required keys: {missing}")
