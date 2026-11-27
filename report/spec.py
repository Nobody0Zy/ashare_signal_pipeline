"""report/spec.py — executive report spec parsing (design §4.3.2).

Loads executive_default.yaml + optional override, resolves {placeholder} values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]

_DEFAULT_SPEC_PATH = ROOT / "configs/report/executive_default.yaml"

# DERIVED: registered derived quantities (from design Appendix B)
DERIVED_KEYS = {
    "kernel_version", "sample_count", "sample_count_h", "label_coverage_pct",
    "test_start", "test_end", "quintile.head_minus_tail_pp", "median_uplift_pp",
    "artifact_bytes", "total_seconds", "split.decay_brief",
}


class SpecError(ValueError):
    """Raised on spec validation errors (missing locked KPI, unknown placeholder)."""


def load_spec(
    default_path: Optional[Path] = None,
    override_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Load and merge executive spec YAML files.

    Args:
        default_path: path to executive_default.yaml (defaults to configs/report/).
        override_path: optional override YAML; merged on top of default.

    Returns:
        Merged spec dict.

    Raises:
        SpecError if any locked KPI is removed or its value expression changed.
    """
    try:
        import yaml
    except ImportError:
        raise ImportError("PyYAML required for spec loading")

    if default_path is None:
        default_path = _DEFAULT_SPEC_PATH

    if not default_path.exists():
        raise FileNotFoundError(f"executive_default.yaml not found: {default_path}")

    with default_path.open(encoding="utf-8") as f:
        spec = yaml.safe_load(f) or {}

    if override_path and override_path.exists():
        with override_path.open(encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
        spec = _deep_merge(spec, override)

    # Validate locked KPIs
    locked_ids = {kpi["id"] for kpi in spec.get("kpi", []) if kpi.get("locked")}
    required_locked = {"spearman", "head_tail", "samples", "features"}
    missing_locked = required_locked - locked_ids
    if missing_locked:
        raise SpecError(
            f"Locked KPI(s) removed from spec: {missing_locked}. "
            "These KPIs are mandatory and cannot be deleted or unlocked."
        )

    return spec


def resolve_placeholders(spec: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve {placeholder} expressions in spec using summary fields.

    Returns:
        New spec dict with placeholders resolved.

    Raises:
        SpecError if unknown placeholder is found (lists available keys).
    """
    import re
    import copy

    resolved = copy.deepcopy(spec)
    ctx = _build_context(summary)

    def _resolve_str(s: str) -> str:
        def replacer(m: re.Match) -> str:
            expr = m.group(1)
            # Simple field access (no arithmetic)
            key = expr.split(":")[0].strip()
            if key in ctx:
                val = ctx[key]
                # Apply format if present
                fmt_part = expr.split(":", 1)[1] if ":" in expr else None
                if fmt_part:
                    try:
                        return format(val, fmt_part)
                    except Exception:
                        return str(val)
                return str(val)
            # Check if it's a complex expression — try eval in limited context
            try:
                val = eval(expr, {"__builtins__": {}}, ctx)  # noqa: S307
                return str(val)
            except Exception:
                available = sorted(ctx.keys())
                raise SpecError(
                    f"Unknown placeholder: {{{expr!r}}}. "
                    f"Available fields (first 20): {available[:20]}"
                )
        return re.sub(r"\{([^}]+)\}", replacer, s)

    def _resolve_obj(obj: Any) -> Any:
        if isinstance(obj, str):
            return _resolve_str(obj)
        if isinstance(obj, dict):
            return {k: _resolve_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve_obj(v) for v in obj]
        return obj

    return _resolve_obj(resolved)


def _deep_merge(base: Dict, override: Dict) -> Dict:
    result = dict(base)
    for k, v in override.items():
        if k == "kpi" and isinstance(v, list):
            # Merge kpi list by id
            base_kpis = {kpi["id"]: kpi for kpi in base.get("kpi", [])}
            for kpi in v:
                kpi_id = kpi.get("id", "")
                if kpi_id in base_kpis and base_kpis[kpi_id].get("locked"):
                    # Cannot override locked KPI
                    continue
                base_kpis[kpi_id] = kpi
            result["kpi"] = list(base_kpis.values())
        elif isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _build_context(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Build evaluation context for placeholder resolution."""
    ctx: Dict[str, Any] = {}

    # Flatten common summary fields
    ctx["experiment_version"] = summary.get("experiment_version", "")
    rp = summary.get("resolved_params") or {}
    ctx["kernel_version"] = (
        ((rp.get("experiment") or {}).get("base_version") or "v1.1.0")
        if isinstance(rp, dict) else "v1.1.0"
    )

    ii = summary.get("input_identity") or {}
    ctx["input_mode"] = ii.get("input_mode", "")
    sample_count = None
    ps = summary.get("pipeline_steps") or {}
    det1 = ps.get("step_1", {}).get("details", {}) if ps else {}
    sample_count = det1.get("sample_count")
    if sample_count is None:
        split_ov = summary.get("split_overview") or []
        sample_count = sum(rec.get("样本数", 0) for rec in split_ov)
    ctx["sample_count"] = sample_count or 0
    if sample_count and sample_count >= 10000:
        ctx["sample_count_h"] = f"{sample_count/10000:.1f} 万"
    else:
        ctx["sample_count_h"] = f"{sample_count:,}" if sample_count else "—"

    cov = ii.get("label_non_null_ratio")
    ctx["label_coverage_pct"] = f"{cov*100:.1f}%" if cov else "—"

    split_ov = summary.get("split_overview") or []
    test_rec = next((r for r in split_ov if "测试" in r.get("集合", "")), None)
    if test_rec:
        tr = test_rec.get("时间范围", "—")
        parts = tr.split("~") if "~" in tr else [tr, tr]
        ctx["test_start"] = parts[0].strip()[:7]  # YYYY-MM
        ctx["test_end"] = parts[-1].strip()[:7]
    else:
        ctx["test_start"] = "—"
        ctx["test_end"] = "—"

    # step6 metrics
    step6 = ps.get("step_6", {}).get("details", {}) if ps else {}
    ctx["step6.spearman"] = step6.get("spearman", 0.0) or 0.0
    ctx["step6.mae"] = step6.get("mae", 0.0) or 0.0

    # quintile head_tail
    q5 = summary.get("test_quintile") or []
    if len(q5) >= 5:
        sorted_q = sorted(q5, key=lambda r: r.get("test_group", r.get("score_group", 0)))
        head = sorted_q[-1].get("target_raw_mean", 0)
        tail = sorted_q[0].get("target_raw_mean", 0)
        ctx["quintile.head_minus_tail_pp"] = (head - tail) * 100
    else:
        ctx["quintile.head_minus_tail_pp"] = 0.0

    # model_feature_schema
    mfschema = summary.get("model_feature_schema") or {}
    ctx["model_feature_schema.feature_count"] = mfschema.get("feature_count", "—")
    ff = summary.get("feature_funnel") or {}
    ctx["feature_funnel.raw_extracted"] = ff.get("raw_extracted", "—")

    # drift hints
    drift = summary.get("drift_hint") or {}
    test_drift = drift.get("test") or {}
    ctx["drift_hint.test.median_delta_pp"] = test_drift.get("median_delta_pp", 0.0) or 0.0
    ctx["drift_hint.test.win_rate_delta_pp"] = test_drift.get("win_rate_delta_pp", 0.0) or 0.0

    # medial split uplift
    meds = summary.get("test_median_split") or {}
    uplift = meds.get("uplift") or {}
    ctx["median_uplift_pp"] = (uplift.get("target_raw_mean_diff") or 0) * 100

    # train → test 排序能力变化（F7：不得写死）
    smp = summary.get("split_model_performance") or []
    by_label = {rec.get("集合"): rec for rec in smp}
    tr = (by_label.get("训练集") or {}).get("排序 Spearman")
    te = (by_label.get("测试集") or {}).get("排序 Spearman")
    if tr is not None and te is not None and tr:
        rel = (te - tr) / abs(tr) * 100
        if rel <= -10.0:
            ctx["split.decay_brief"] = f"训练集到测试集的排序能力下降约 {abs(rel):.0f}%。"
        elif rel >= 10.0:
            ctx["split.decay_brief"] = f"测试集排序能力高于训练集约 {rel:.0f}%，未见衰减。"
        else:
            ctx["split.decay_brief"] = "训练集到测试集的排序能力基本持平。"
    else:
        ctx["split.decay_brief"] = "训练集到测试集的排序能力变化无法计算。"

    return ctx
