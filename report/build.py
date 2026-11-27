"""report/build.py — write rendered outputs to disk and generate manifest (design §5.2).

M1: stubs; M2+ adds datacard/reference; M5+ adds manifest and aliases.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def write_all(
    outputs: Dict[str, str],
    out_dir: Path,
    aliases: bool = False,
) -> Dict[str, Path]:
    """Write rendered content to disk under out_dir/reports/.

    Returns mapping from report type to absolute output path.
    """
    reports_dir = out_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    _EXT = {
        "engineering": "md",
        "experiment": "md",
        "executive": "html",
        "datacard": "md",
        "reference": "md",
    }

    paths: Dict[str, Path] = {}
    for type_, content in outputs.items():
        ext = _EXT.get(type_, "md")
        out_path = reports_dir / f"{type_}.{ext}"
        out_path.write_text(content, encoding="utf-8")
        paths[type_] = out_path

    # Deprecated aliases (v2 layout only)
    if aliases:
        if "experiment" in paths:
            alias = out_dir / "experiment_analysis_report.md"
            alias.write_bytes(paths["experiment"].read_bytes())
        if "executive" in paths:
            alias = out_dir / "experiment_summary_for_leadership.html"
            alias.write_bytes(paths["executive"].read_bytes())

    return paths


def resolve_external_paths(summary: Dict[str, Any]) -> Dict[str, str]:
    """Resolve datacard and reference paths from summary (for manifest external[] section)."""
    return {
        "datacard": summary.get("datacard_path", ""),
        "reference": summary.get("reference_path", ""),
    }


def write_manifest(
    paths: Dict[str, Path],
    summary: Dict[str, Any],
    layout: str = "v2",
) -> Optional[Path]:
    """Write reports/manifest.json (layout v2+ only). Returns manifest path or None.

    Accepts summary dict directly (not a path) to satisfy A-7 (no file reads).
    """
    if layout != "v2":
        return None
    out_dir: Optional[Path] = None
    if "experiment" in paths:
        out_dir = paths["experiment"].parents[1]
    elif "engineering" in paths:
        out_dir = paths["engineering"].parents[1]
    elif "executive" in paths:
        out_dir = paths["executive"].parents[1]
    if out_dir is None:
        return None

    reports_dir = out_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = reports_dir / "manifest.json"

    import hashlib
    # Compute sha256 from in-memory summary dict for manifest
    sha = hashlib.sha256(json.dumps(summary, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()[:8]

    _AUDIENCE = {
        "engineering": "engineer",
        "experiment": "researcher",
        "executive": "executive",
    }
    _TITLE = {
        "engineering": "工程详细报告",
        "experiment": "实验详细报告",
        "executive": "管理层汇报",
    }
    _FMT = {"engineering": "md", "experiment": "md", "executive": "html"}

    reports_list = []
    for rtype, rpath in paths.items():
        if rtype not in _AUDIENCE:
            continue
        rel = str(rpath.relative_to(out_dir)).replace("\\", "/")
        reports_list.append({
            "type": rtype,
            "audience": _AUDIENCE[rtype],
            "format": _FMT.get(rtype, "md"),
            "path": rel,
            "title": _TITLE.get(rtype, rtype),
            "deprecated": False,
        })

    # Deprecated aliases
    for alias_name, source_type, alias_ext in [
        ("experiment_analysis_report.md", "experiment", "md"),
        ("experiment_summary_for_leadership.html", "executive", "html"),
    ]:
        if source_type in paths:
            reports_list.append({
                "type": source_type,
                "audience": "legacy",
                "format": alias_ext,
                "path": alias_name,
                "title": f"[已废弃 v1.2 删除] {_TITLE.get(source_type, source_type)}",
                "deprecated": True,
                "alias_of": f"reports/{source_type}.{alias_ext}",
                "removal_version": "v1.2",
            })

    external = []
    for etype, path_key in [("datacard", "datacard_path"), ("reference", "reference_path")]:
        p = summary.get(path_key, "")
        if p:
            external.append({"type": etype, "path": p})

    manifest = {
        "schema_version": "report-summary/1",
        "experiment_version": summary.get("experiment_version", ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_summary_sha256": sha,
        "layout": layout,
        "reports": reports_list,
        "external": external,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
