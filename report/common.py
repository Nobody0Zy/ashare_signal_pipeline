"""report/common.py — readability primitives (design §6)."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence


def build_toc(sections: Sequence[str]) -> str:
    """Generate a Markdown table-of-contents from a list of section headers."""
    lines = []
    for s in sections:
        # e.g., "## E0 运行标识" → anchor "e0-运行标识"
        title = s.lstrip("#").strip()
        level = len(s) - len(s.lstrip("#"))
        indent = "  " * max(0, level - 2)
        anchor = title.lower().replace(" ", "-")
        lines.append(f"{indent}- [{title}](#{anchor})")
    return "\n".join(lines)


def render_table(
    rows: List[dict],
    cols: Optional[List[str]] = None,
    align: Optional[List[str]] = None,
    caption: str = "",
) -> str:
    """Render a list-of-dicts as a Markdown table.

    cols: column names (defaults to keys of first row).
    align: list of 'left', 'right', 'center' per column.
    """
    if not rows:
        return ""
    if cols is None:
        cols = list(rows[0].keys())
    if align is None:
        align = ["right" if _is_numeric_col(col, rows) else "left" for col in cols]

    header = "| " + " | ".join(cols) + " |"
    sep_parts = []
    for a in align:
        if a == "right":
            sep_parts.append("---:")
        elif a == "center":
            sep_parts.append(":---:")
        else:
            sep_parts.append(":---")
    sep = "| " + " | ".join(sep_parts) + " |"

    data_rows = []
    for row in rows:
        cells = [str(row.get(c, "—")) for c in cols]
        data_rows.append("| " + " | ".join(cells) + " |")

    parts = [header, sep] + data_rows
    if caption:
        parts.append(f"\n*{caption}*")
    return "\n".join(parts)


def _is_numeric_col(col: str, rows: List[dict]) -> bool:
    for row in rows[:3]:
        v = row.get(col)
        if v is not None:
            try:
                float(str(v).replace(",", "").replace("%", ""))
                return True
            except ValueError:
                return False
    return False


def details_block(title: str, body: str) -> str:
    """Wrap body in a collapsible <details> block."""
    return f"<details><summary>{title}</summary>\n\n{body}\n\n</details>"


def figure(path: str, caption: str, read_hint: str = "") -> str:
    """Render a Markdown image with caption.

    path should be relative to the reports/ directory (e.g., '../assets/xxx.png').
    Caption appears below the image per design §6.5.
    """
    img = f"![]({path})"
    cap_line = f"图：{caption}"
    if read_hint:
        cap_line += f"｜怎么读：{read_hint}"
    return f"{img}\n\n*{cap_line}*"


def section(title: str, lead_sentence: str, body: str) -> str:
    """Render a section with a mandatory first-line conclusion (design §6.1).

    Prepends '> 本章结论：{lead_sentence}' before the body.
    """
    lead = f"> 本章结论：{lead_sentence}" if lead_sentence else ""
    parts = [title]
    if lead:
        parts.extend(["", lead])
    if body:
        parts.extend(["", body])
    return "\n".join(parts)


def explain_simple(text: str) -> str:
    """Render a '> 简单说：' hint block (design §6.8).

    Must only appear at chapter end and not introduce new numbers.
    """
    return f"> 简单说：{text}"


def render_throughput_table(workload: dict) -> str:
    """Render E8 workload attribution table from workload dict."""
    rows = []
    for step, data in workload.items():
        rows.append({
            "步骤": step,
            "输入单位": f"{data.get('input_units', '—'):,}" if isinstance(data.get('input_units'), int) else str(data.get('input_units', '—')),
            "每单位计算量": str(data.get('ops_per_unit', '—')),
            "总计算量": f"{data.get('total_ops', '—'):,}" if isinstance(data.get('total_ops'), int) else str(data.get('total_ops', '—')),
            "并行度": str(data.get('parallelism', 1)),
            "吞吐（单位/秒）": str(data.get('throughput_per_sec', '—')),
            "归因": data.get('attribution', '—'),
        })
    return render_table(rows, caption="口径：input_units 为各步骤处理的基本单元数")


def render_artifact_table(artifact_sizes: list) -> str:
    """Render E6 artifact sizes table, grouped by directory."""
    if not artifact_sizes:
        return "> 产物目录扫描结果为空。"
    group_summary: dict = {}
    for rec in artifact_sizes:
        g = rec.get("group", "unknown")
        if g not in group_summary:
            group_summary[g] = {"files": 0, "bytes": 0}
        group_summary[g]["files"] += 1
        group_summary[g]["bytes"] += rec.get("bytes", 0)

    rows = []
    from report.load import fmt_bytes
    for group, stats in sorted(group_summary.items()):
        rows.append({
            "目录组": group,
            "文件数": f"{stats['files']:,}",
            "总体积": fmt_bytes(stats["bytes"]),
        })
    return render_table(rows, caption="")
