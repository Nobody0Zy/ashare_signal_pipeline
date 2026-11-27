"""report/executive.py — leadership HTML report renderer (design §4.3).

Generates a self-contained HTML with KPIs, monotonic views, group tables, risk.
Images are base64-inlined (no external links).
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]


def render_executive(summary: Dict[str, Any], spec: Dict[str, Any]) -> str:
    """Render executive.html from report_summary.json and spec dict.

    Returns self-contained HTML string with base64 images.
    """
    if "_render_ctx" in summary:
        from report._legacy import render_leadership_html_legacy
        return render_leadership_html_legacy(summary)
    return _render_k_sections(summary, spec)


def _fmt_num(v: Any, digits: int = 3, sign: bool = True) -> str:
    if v is None:
        return "—"
    try:
        f_val = float(v)
        return (f"+{f_val:.{digits}f}" if (sign and f_val > 0) else f"{f_val:.{digits}f}")
    except Exception:
        return str(v)


def _fmt_pct(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v)*100:+.{digits}f}%"
    except Exception:
        return str(v)


def _fmt_int(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except Exception:
        return str(v)


def _b64_img(img_path: Path) -> str:
    """Return base64-encoded data URI for an image file."""
    if not img_path.exists():
        return ""
    suffix = img_path.suffix.lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml"}.get(
        suffix.lstrip("."), "image/png"
    )
    data = base64.b64encode(img_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _render_k_sections(summary: Dict[str, Any], spec: Dict[str, Any]) -> str:
    ga = datetime.now().isoformat(timespec="seconds")
    ev = summary.get("experiment_version", "")

    # Build context for spec placeholders
    try:
        from report.spec import resolve_placeholders, _build_context
        resolved_spec = resolve_placeholders(spec, summary)
    except Exception:
        resolved_spec = spec

    kpis = resolved_spec.get("kpi") or []
    sections = resolved_spec.get("sections") or []
    title = resolved_spec.get("title", "策略信号模型 · 实验汇报")
    subtitle = resolved_spec.get("subtitle", ev)

    # Pre-computed data
    ps = summary.get("pipeline_steps") or {}
    step6 = ps.get("step_6", {}).get("details", {}) if ps else {}
    q5 = summary.get("test_quintile") or []
    meds = summary.get("test_median_split") or {}
    thr = summary.get("test_threshold_filters") or {}
    mon = summary.get("monotonic") or {}
    mon_sum = mon.get("summary") or {}
    drift = summary.get("drift_hint") or {}
    test_drift = drift.get("test") or {}

    # K1 KPI HTML
    kpi_html = _build_kpi_html(kpis, step6, q5, summary)

    # Conclusions
    conclusions_html = ""
    for sec in sections:
        if sec.get("type") == "conclusions":
            items = sec.get("items") or []
            conclusions_html = "<ul>" + "".join(f"<li>{h}</li>" for h in items) + "</ul>"
            break

    # K3 monotonic figures (5 images)
    monotonic_html = _build_monotonic_html(summary, mon_sum, sections)

    # K4 group tables
    groups_html = _build_groups_html(q5, meds, thr)

    # K5 risk text
    risk_text = ""
    for sec in sections:
        if sec.get("type") == "risk":
            risk_text = sec.get("text", "")
            break

    # K6 actions
    actions_html = ""
    for sec in sections:
        if sec.get("type") == "actions":
            items = sec.get("items") or []
            actions_html = "<ul>" + "".join(f"<li>{a}</li>" for a in items) + "</ul>"
            break

    spec_note = f"规格来源: {resolved_spec.get('schema', 'executive-report/1')}"
    sha = hashlib.sha256(str(summary.get("experiment_version", "")).encode()).hexdigest()[:8]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
body {{font-family: 'Microsoft YaHei', 'PingFang SC', Arial, sans-serif; margin: 0; padding: 20px; background: #f8f9fa; color: #333;}}
.page {{max-width: 960px; margin: 0 auto; background: white; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,.1);}}
h1 {{color: #1a1a2e; border-bottom: 3px solid #0066cc; padding-bottom: 8px; margin-top: 0;}}
h2 {{color: #0066cc; margin-top: 24px;}}
h3 {{color: #444;}}
.subtitle {{color: #666; font-size: 0.95em; margin-top: -12px; margin-bottom: 20px;}}
.kpi-grid {{display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin: 20px 0;}}
.kpi-card {{background: #f0f7ff; border: 1px solid #cce0ff; border-radius: 8px; padding: 16px; text-align: center;}}
.kpi-value {{font-size: 2em; font-weight: bold; color: #0066cc;}}
.kpi-label {{font-size: 0.9em; color: #555; margin-top: 4px;}}
.kpi-note {{font-size: 0.75em; color: #888; margin-top: 4px;}}
table {{width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 0.9em;}}
th {{background: #0066cc; color: white; padding: 8px 12px; text-align: right;}}
th:first-child {{text-align: left;}}
td {{border-bottom: 1px solid #eee; padding: 8px 12px; text-align: right;}}
td:first-child {{text-align: left;}}
tr:hover {{background: #f8f9fa;}}
.fig-row {{display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 16px 0;}}
.fig-row img {{max-width: 100%; border: 1px solid #ddd; border-radius: 4px;}}
.fig-row-3 {{display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin: 16px 0;}}
.fig-row-3 img {{max-width: 100%; border: 1px solid #ddd; border-radius: 4px;}}
.risk-block {{background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; padding: 12px 16px; margin: 16px 0;}}
.footer {{font-size: 0.75em; color: #aaa; text-align: right; margin-top: 24px; border-top: 1px solid #eee; padding-top: 8px;}}
img.monotonic {{max-width: 100%; border: 1px solid #ddd; border-radius: 4px; margin: 8px 0;}}
.note {{font-size: 0.8em; color: #666; font-style: italic; margin-top: 4px;}}
</style>
</head>
<body>
<div class="page">
<h1>{title}</h1>
<p class="subtitle">{subtitle}</p>

<h2>K1 关键指标</h2>
{kpi_html}

<h2>K2 结论</h2>
{conclusions_html}

{monotonic_html}

<h2>K4 分组与过滤效果</h2>
{groups_html}

<h2>K5 风险提示</h2>
<div class="risk-block">{risk_text}</div>

<h2>K6 行动项</h2>
{actions_html}

<div class="footer">
生成时间: {ga} | {spec_note} | 实验版本: {ev} | sha256: {sha}<br>
{_a24_note()}
</div>
</div>
</body>
</html>"""
    return html


def _build_kpi_html(kpis: List[Dict], step6: Dict, q5: List[Dict], summary: Dict) -> str:
    kpi_cards = []
    for kpi in kpis:
        label = kpi.get("label", "")
        value = kpi.get("value", "—")
        note = kpi.get("note", "")
        kpi_cards.append(
            f'<div class="kpi-card">'
            f'<div class="kpi-value">{value}</div>'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-note">{note}</div>'
            f'</div>'
        )
    return f'<div class="kpi-grid">{"".join(kpi_cards)}</div>'


def _build_monotonic_html(summary: Dict, mon_sum: Dict, sections: List[Dict]) -> str:
    # Find analysis dir from summary (via experiment_version)
    ev = summary.get("experiment_version", "")
    analysis_dir = ROOT / f"outputs/{ev}/analysis"

    # Order: train, val, test, all, pred
    img_configs = [
        ("train", "monotonic_target_train.png", "训练集"),
        ("val", "monotonic_target_val.png", "验证集"),
        ("test", "monotonic_target_test.png", "测试集"),
        ("all", "monotonic_target_all.png", "三集合合并"),
        ("pred", "monotonic_y_pred_groups.png", "预测值分布"),
    ]

    # Build summary table
    per_split = mon_sum.get("per_split") or {}
    sum_rows = []
    for split_name, img_file, label in img_configs[:4]:
        if split_name == "all":
            break
        s = per_split.get(split_name) or {}
        rc = s.get("spearman")
        f10 = s.get("first_group_target_mean")
        l10 = s.get("last_group_target_mean")
        sum_rows.append(
            f"<tr><td>{label}</td>"
            f"<td>{_fmt_num(rc)}</td>"
            f"<td>{_fmt_pct(f10)}</td>"
            f"<td>{_fmt_pct(l10)}</td>"
            f"<td>{_fmt_pct(l10 - f10 if l10 and f10 else None)}</td></tr>"
        )

    sum_table = f"""<table>
<tr><th>集合</th><th>秩相关</th><th>首10组均值</th><th>末10组均值</th><th>差值</th></tr>
{"".join(sum_rows)}
</table>"""

    img_rows = []
    # Row 1: train, val, test
    row1_imgs = []
    for split_name, img_file, label in img_configs[:3]:
        p = analysis_dir / img_file
        if not p.exists():
            p = analysis_dir / "assets" / img_file
        uri = _b64_img(p)
        if uri:
            row1_imgs.append(f'<img src="{uri}" alt="{label}" title="{label}">')
        else:
            row1_imgs.append(f'<div style="text-align:center;padding:20px;color:#aaa;">[{label} 图不可用]</div>')

    # Row 2: all, pred
    row2_imgs = []
    for split_name, img_file, label in img_configs[3:]:
        p = analysis_dir / img_file
        if not p.exists():
            p = analysis_dir / "assets" / img_file
        uri = _b64_img(p)
        if uri:
            row2_imgs.append(f'<img src="{uri}" alt="{label}" title="{label}">')
        else:
            row2_imgs.append(f'<div style="text-align:center;padding:20px;color:#aaa;">[{label} 图不可用]</div>')

    row1_html = f'<div class="fig-row-3">{"".join(row1_imgs)}</div>'
    row2_html = f'<div class="fig-row">{"".join(row2_imgs)}</div>'

    notes = """<p class="note">
① 横轴：样本按预测分低→高分成100组；② 曲线整体上行 = 排序有效；
③ 看的是排序准不准，不是预测涨跌幅准不准；
④ 首尾单组约500条，均值易被极端样本带动；趋势请看整体秩相关与首/末10组均值。
</p>"""

    return f"""<h2>K3 100组完整视图</h2>
{sum_table}
{row1_html}
{row2_html}
{notes}"""


def _build_groups_html(q5: List, meds: Dict, thr: Dict) -> str:
    # Quintile table
    q_rows = ""
    for rec in sorted(q5, key=lambda r: r.get("pred_group", r.get("test_group", r.get("score_group", 0)))):
        grp = rec.get("pred_group", rec.get("test_group", rec.get("score_group", "?")))
        mean = rec.get("target_raw_mean")
        wr = rec.get("target_raw_win_rate", rec.get("win_rate"))
        q_rows += f"<tr><td>{grp}</td><td>{_fmt_pct(mean)}</td><td>{_fmt_pct(wr)}</td></tr>"

    q_table = f"""<h3>五分组</h3>
<table>
<tr><th>分组</th><th>收益均值(raw)</th><th>胜率</th></tr>
{q_rows}
</table>"""

    # Median split
    med_rows = ""
    med_uplift = meds.get("uplift") or {}
    for rec in (meds.get("rows") or []):
        if rec.get("target_raw_mean") is None:
            continue
        med_rows += f"<tr><td>{rec.get('score_group','?')}</td><td>{_fmt_pct(rec.get('target_raw_mean'))}</td></tr>"
    if med_uplift:
        med_rows += f"<tr><td><b>提升</b></td><td><b>{_fmt_pct(med_uplift.get('target_raw_mean_diff'))}</b></td></tr>"
    med_table = f"""<h3>中位数二分</h3>
<table><tr><th>组</th><th>收益均值</th></tr>{med_rows}</table>"""

    # Threshold table
    thr_rows = ""
    for rec in (thr.get("rows") or []):
        q = rec.get("score_quantile", rec.get("quantile", "?"))
        try:
            kept = f"保留前 {(1-float(q))*100:.0f}%"
        except Exception:
            kept = str(q)
        mean = rec.get("target_raw_mean")
        vs_raw = rec.get("target_raw_mean_vs_all")
        vs_txt = f"{vs_raw*100:+.2f}pp" if isinstance(vs_raw, (int, float)) else "—"
        thr_rows += f"<tr><td>{kept}</td><td>{_fmt_pct(mean)}</td><td>{vs_txt}</td></tr>"
    thr_table = f"""<h3>阈值过滤表</h3>
<table>
<tr><th>阈值</th><th>收益均值</th><th>vs 全体</th></tr>
{thr_rows}
</table>
<p class="note">注意：阈值不是越激进越好。只留前10%时收益回落——说明最顶端预测并不可靠，不宜当精选器。</p>"""

    return q_table + med_table + thr_table


def _a24_note() -> str:
    return "报告说明：K3 展示 5 张 100 组单调性图（train/val/test/all/pred），不含逐组明细表；K4 含五分组/中位数/阈值三表。"
