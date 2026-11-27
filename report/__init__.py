"""report package — rendering layer for ashare_signal_pipeline v1.1.1.

Renders report_summary.json into engineering.md / experiment.md / executive.html
and writes datacard + reference pages.

Public API (M1+ version):
  render_all(summary_path, out_dir, types, layout, spec_path=None) -> dict[str, Path]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Set


def render_all(
    summary_path: Path,
    out_dir: Path,
    types: Set[str] | None = None,
    layout: str = "v1",
    spec_path: Optional[Path] = None,
) -> Dict[str, Path]:
    """Orchestrate rendering of all requested report types.

    M1: only 'experiment' type is supported (delegates to report.experiment).
    M2+: 'datacard' and 'reference' added.
    M3+: 'engineering' added.
    M4+: 'experiment' fully refactored (X0-X10 structure).
    M5+: 'executive' (spec-driven HTML) + 'manifest' added.

    Returns dict mapping report type → output path.
    """
    from report.load import load_summary
    from report.build import write_all, write_manifest, resolve_external_paths

    if types is None:
        types = {"engineering", "experiment", "executive"}

    summary = load_summary(summary_path)
    outputs: Dict[str, str] = {}

    if "experiment" in types:
        from report.experiment import render_experiment
        outputs["experiment"] = render_experiment(summary)

    if "engineering" in types:
        try:
            from report.engineering import render_engineering
            outputs["engineering"] = render_engineering(summary)
        except (ImportError, NotImplementedError):
            pass

    if "datacard" in types:
        try:
            from report.datacard import render_datacard
            render_datacard(summary)  # writes to data/reference/ internally
        except (ImportError, NotImplementedError):
            pass

    if "reference" in types:
        try:
            from report.reference import render_reference
            render_reference(summary)  # writes to data/reference/ internally
        except (ImportError, NotImplementedError):
            pass

    if "executive" in types:
        try:
            from report.executive import render_executive
            from report.spec import load_spec
            # Always load default spec; optionally override
            try:
                spec_obj = load_spec(override_path=spec_path)
            except Exception:
                spec_obj = {}
            outputs["executive"] = render_executive(summary, spec_obj)
        except (ImportError, NotImplementedError):
            pass

    paths = write_all(outputs, out_dir, aliases=(layout == "v2"))
    if layout == "v2":
        write_manifest(paths, summary, layout)
    return paths
