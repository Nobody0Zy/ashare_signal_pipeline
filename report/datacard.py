"""report/datacard.py — T2 data card renderer (design §4.5).

Renders per-samples_id metadata into data/reference/<sid>/datacard.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]

# Design §4.5: one-liner label definitions (must match data_prep label semantics)
LABEL_ONE_LINERS: Dict[str, str] = {
    "target_future_5d": (
        "signal_date 之后首个非停牌交易日开盘入场，持有 5 个非停牌交易日后收盘出场，"
        "target = 收盘价/入场开盘价 − 1"
    ),
    "target_future_10d": (
        "signal_date 之后首个非停牌交易日开盘入场，持有 10 个非停牌交易日后收盘出场，"
        "target = 收盘价/入场开盘价 − 1"
    ),
    "target_future_20d": (
        "signal_date 之后首个非停牌交易日开盘入场，持有 20 个非停牌交易日后收盘出场，"
        "target = 收盘价/入场开盘价 − 1"
    ),
    "target_future_40d": (
        "signal_date 之后首个非停牌交易日开盘入场，持有 40 个非停牌交易日后收盘出场，"
        "target = 收盘价/入场开盘价 − 1"
    ),
    "target_holding_return": (
        "原始持仓记录自带的实际持有收益率（与旧卖出规则耦合，仅诊断用）"
    ),
    "target_trend_quality_20d": (
        "未来 20 日收益率 ÷ 该区间最大回撤（回撤 ≤ 1e-6 时为空）"
    ),
}


def render_datacard(summary: Dict[str, Any]) -> str:
    """Render data card markdown from report_summary.json.

    Returns the full Markdown string.
    Also writes the file to data/reference/<sid>/datacard.md.
    """
    ii = summary.get("input_identity") or {}
    input_mode = ii.get("input_mode", "holding_file")
    sid = ii.get("samples_id", "")

    if input_mode == "holding_file" or not sid:
        content = _render_legacy_datacard(summary)
        out_path = ROOT / "data/reference/_legacy_holding/datacard.md"
    else:
        content = _render_samples_datacard(summary, sid)
        out_path = ROOT / f"data/reference/{sid}/datacard.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return content


def _render_legacy_datacard(summary: Dict[str, Any]) -> str:
    ev = summary.get("experiment_version", "unknown")
    ga = datetime.now().isoformat(timespec="seconds")
    return "\n".join([
        f"# 数据卡 — 持仓文件路径（holding_file 模式）",
        "",
        f"生成时间: {ga}",
        f"实验版本: {ev}",
        "",
        "## D0 身份与指纹",
        "",
        "不适用（样本来自持仓表，不是独立样本集）。",
        "",
        "## D1 采样规范",
        "",
        "不适用（holding_file 模式不使用独立采样方法）。",
        "",
        "## D2 股票池",
        "",
        "不适用。",
        "",
        "## D3 样本统计",
        "",
        "不适用。",
        "",
        "## D4 标签清单",
        "",
        "不适用。",
        "",
        "## D5 已知限制",
        "",
        "不适用。",
    ])


def _render_samples_datacard(summary: Dict[str, Any], sid: str) -> str:
    manifest_path = ROOT / f"data/prepared/{sid}/manifest.json"
    index_path = ROOT / f"data/labels/{sid}/_index.json"

    manifest: Dict[str, Any] = {}
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8") as f:
            manifest = json.load(f)

    label_index: Dict[str, Any] = {}
    if index_path.exists():
        with index_path.open(encoding="utf-8") as f:
            label_index = json.load(f)

    # D3: year distribution
    year_dist_rows = _get_year_distribution(sid, manifest)

    ga = datetime.now().isoformat(timespec="seconds")
    params_hash = manifest.get("params_hash", "—")
    samples_hash = manifest.get("samples_set_hash", "—")
    datebar_fp = manifest.get("datebar_fingerprint", "—")
    created_at = manifest.get("created_at", "—")
    code_version = manifest.get("code_version", "—")

    universe = manifest.get("universe") or {}
    sampling_params = manifest.get("sampling_params") or {}
    sampling_method = manifest.get("sampling_method", "—")
    max_samples = manifest.get("max_samples")
    date_range = manifest.get("date_range") or {}
    rows = manifest.get("rows", "—")
    n_symbols = manifest.get("n_symbols", "—")
    min_history_days = manifest.get("min_history_days", "—")

    # Read symbol counts from samples.parquet
    sh_count = sz_count = unique_symbols = "—"
    try:
        import pandas as pd
        df = pd.read_parquet(ROOT / f"data/prepared/{sid}/samples.parquet", columns=["idxs", "idxd"])
        unique_symbols = df["idxs"].nunique()
        sh_count = int((df["idxs"].str.startswith("sh")).sum())
        sz_count = int((df["idxs"].str.startswith("sz")).sum())
    except Exception:
        pass

    lines = [
        f"# 数据卡 — {sid}",
        "",
        f"生成时间: {ga}",
        "",
        "## D0 身份与指纹",
        "",
        "| 项 | 值 |",
        "|:---|:---|",
        f"| samples_id | `{sid}` |",
        f"| params_hash | `{params_hash}` |",
        f"| samples_set_hash | `{samples_hash}` |",
        f"| datebar_fingerprint | `{datebar_fp}` |",
        f"| created_at | {created_at} |",
        f"| code_version | {code_version} |",
        "",
        "## D1 采样规范",
        "",
        f"- 采样方法: `{sampling_method}`",
        f"- MA 周期: {sampling_params.get('ma_period', '—')}",
        f"- STD 乘数: {sampling_params.get('std_mult', '—')}",
        f"- 最大样本数: {max_samples if max_samples else '不限制'}",
        f"- 日期范围: {date_range.get('start', '全量')} ~ {date_range.get('end', '全量')}",
        "",
        "**构造逻辑**",
        "",
        "| 环节 | 规则 | 为什么这样定 |",
        "|:---|:---|:---|",
        f"| 事件定义 | `close[t] > upper[t]` 且 `close[t-1] ≤ upper[t-1]` | 只取上穿当天，避免突破后连续多日重复计数 |",
        f"| 上轨公式 | `upper = MA(close, {sampling_params.get('ma_period', 20)}) + {sampling_params.get('std_mult', 2.0)} × STD(close, {sampling_params.get('ma_period', 20)})`，STD 用样本标准差（`ddof=1`） | 布林带常用参数，覆盖约 95% 正常波动 |",
        f"| 前置条件 | `upper[t-1]` 非空（至少 {int(sampling_params.get('ma_period', 20)) + 1} 个交易日历史） | 没有前一日上轨就无法判断上穿 |",
        f"| 股票池 | 排除 `{', '.join(universe.get('exclude_prefixes', ['sh000', 'sz399', 'sh950']))}` 前缀；每个 symbol 非停牌天数 ≥ {min_history_days} | 排除指数与次新股，保证后续窗口可构造 |",
        f"| 价格口径 | 后复权价，不乘复权因子 `xs` | 保证跨除权的价格可比 |",
        "",
        "## D2 股票池",
        "",
        "| 项 | 值 |",
        "|:---|:---|",
        f"| 排除前缀 | `{', '.join(universe.get('exclude_prefixes', []))}` |",
        f"| 最小历史天数 | {min_history_days} |",
        f"| 最终 symbol 数 | {unique_symbols} |",
        f"| 其中沪市 (sh) | {sh_count:,} 条样本 |" if isinstance(sh_count, int) else f"| 其中沪市 (sh) | {sh_count} |",
        f"| 其中深市 (sz) | {sz_count:,} 条样本 |" if isinstance(sz_count, int) else f"| 其中深市 (sz) | {sz_count} |",
        "",
        "## D3 样本统计",
        "",
        "| 项 | 值 |",
        "|:---|:---|",
        f"| 总行数 | {rows:,} |" if isinstance(rows, int) else f"| 总行数 | {rows} |",
        f"| 唯一 symbol | {unique_symbols} |",
        f"| 信号日期范围 | 2007-02-01 ~ 2025-01-14 |",
        "",
        "**按年分布**",
        "",
        year_dist_rows,
        "",
        "## D4 标签清单",
        "",
        _render_label_table(label_index),
        "",
        "## D5 已知限制",
        "",
        _render_known_limits(label_index),
    ]
    return "\n".join(lines)


def _get_year_distribution(sid: str, manifest: Dict[str, Any]) -> str:
    try:
        import pandas as pd
        df = pd.read_parquet(ROOT / f"data/prepared/{sid}/samples.parquet", columns=["idxd"])
        yr = (df["idxd"] // 10000).value_counts().sort_index()
        rows_lines = ["| 年份 | 样本数 |", "|---:|---:|"]
        for year, count in yr.items():
            rows_lines.append(f"| {year} | {int(count):,} |")
        rows_lines.append(f"| **合计** | **{len(df):,}** |")
        return "\n".join(rows_lines)
    except Exception as e:
        return f"> 按年分布不可用：{e}"


def _render_label_table(label_index: Dict[str, Any]) -> str:
    if not label_index:
        return "> 标签索引不可用。"
    rows = ["| 标签名 | 一句话定义 | 参数哈希 | 有效行数 | 覆盖率 |",
            "|:---|:---|:---|---:|---:|"]
    for label_name, meta in sorted(label_index.items()):
        defn = LABEL_ONE_LINERS.get(label_name)
        if defn is None:
            defn = "（未登记定义）[WARN]"
        ph = meta.get("params_hash", "—")
        rows_count = int(meta.get("non_null_rows", 0))
        cov = float(meta.get("coverage", 0))
        rows.append(f"| `{label_name}` | {defn} | `{ph}` | {rows_count:,} | {cov:.2%} |")
    return "\n".join(rows)


def _render_known_limits(label_index: Dict[str, Any]) -> str:
    lines = []
    if label_index:
        hr = label_index.get("target_holding_return") or {}
        cov = float(hr.get("coverage", 1.0))
        if cov < 0.9:
            lines.append(
                f"> `target_holding_return` 覆盖率仅 {cov:.2%}：事件采样得到的信号大多没有对应的真实持仓记录，"
                f"因此该标签只用于与旧路径对照，不宜作为主标签。"
            )
    if not lines:
        lines = ["无已知显著限制。"]
    return "\n".join(lines)
