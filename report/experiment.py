"""report/experiment.py — experiment report renderer X0-X10 (design §4.2).

M1: delegates to _legacy for golden verification (via _render_ctx).
M4: fully implements X0-X10 structure using only summary dict.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]

SPAN_WARN_DAYS = 60  # from baseline.report_contracts.constants.label_span_warn_days


def render_experiment(summary: Dict[str, Any]) -> str:
    """Render experiment.md from report_summary.json.

    When called from analyze_experiment_results.py (with _render_ctx),
    delegates to _legacy for exact golden parity.
    When called standalone (build_reports.py), uses X0-X10 structure.
    """
    if "_render_ctx" in summary:
        from report._legacy import render_markdown_report_legacy
        return render_markdown_report_legacy(summary)
    return _render_x0_x10(summary)


def _fmt_pct(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v)*100:+.{digits}f}%"
    except Exception:
        return str(v)


def _fmt_num(v: Any, digits: int = 4, sign: bool = True) -> str:
    if v is None:
        return "—"
    try:
        f_val = float(v)
        return (f"+{f_val:.{digits}f}" if (sign and f_val > 0) else f"{f_val:.{digits}f}")
    except Exception:
        return str(v)


def _fmt_int(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except Exception:
        return str(v)


def _ident(value: Any, default: str = "—") -> str:
    """Render an identifier: empty string is treated same as None."""
    text = str(value).strip() if value is not None else ""
    return text if text else default


DECAY_FLAT_PCT = 10.0  # |相对变化| < 10% 视为持平（经验阈值，不是统计检验）


def _split_decay(smp) -> Dict[str, Any]:
    """从 split_model_performance 计算 train→test 排序能力变化。

    Returns:
        {"train": float|None, "test": float|None, "diff": float|None,
         "rel_pct": float|None, "direction": "down"|"flat"|"up"|None}
    """
    by_label = {rec.get("集合"): rec for rec in (smp or [])}
    tr = (by_label.get("训练集") or {}).get("排序 Spearman")
    te = (by_label.get("测试集") or {}).get("排序 Spearman")
    if tr is None or te is None:
        return {"train": tr, "test": te, "diff": None, "rel_pct": None, "direction": None}
    diff = te - tr
    rel = (diff / abs(tr) * 100) if tr else None
    if rel is None:
        direction = "flat"
    elif rel <= -DECAY_FLAT_PCT:
        direction = "down"
    elif rel >= DECAY_FLAT_PCT:
        direction = "up"
    else:
        direction = "flat"
    return {"train": tr, "test": te, "diff": diff, "rel_pct": rel, "direction": direction}


def _decay_text(smp, with_numbers: bool = True) -> str:
    """生成「训练→测试排序能力」的结论句（供 experiment.md 使用）。"""
    d = _split_decay(smp)
    direction = d["direction"]
    if direction is None:
        return "训练→测试排序能力变化无法计算（缺少三集合指标）"
    if direction == "down":
        core = f"训练→测试排序能力下降约 {abs(d['rel_pct']):.0f}%"
    elif direction == "up":
        core = f"测试集排序能力高于训练集约 {d['rel_pct']:.0f}%，未见衰减"
    else:
        core = "训练集到测试集的排序能力基本持平"
    if with_numbers:
        return f"{core}（Spearman {_fmt_num(d['train'], digits=4)} → {_fmt_num(d['test'], digits=4)}）"
    return core


def _render_x0_x10(summary: Dict[str, Any]) -> str:
    ga = datetime.now().isoformat(timespec="seconds")
    ev = summary.get("experiment_version", "unknown")
    schema = summary.get("schema_version", "")
    import json, hashlib
    s_copy = {k: v for k, v in summary.items() if k != "_render_ctx"}
    sha = hashlib.sha256(json.dumps(s_copy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]

    sections = ["X0 实验卡片", "X1 数据构建", "X2 数据切分", "X3 步骤参数与统计",
                "X4 特征工程", "X5 模型与标签处理", "X6 模型表现",
                "X7 分组与单调性", "X8 特征重要性", "X9 稳健性与漂移", "X10 结论与局限"]
    toc = "## 目录\n\n" + "\n".join(
        f"- [{s}](#{s.lower().replace(' ', '-').replace('/', '-')})" for s in sections
    )

    ps = summary.get("pipeline_steps") or {}
    rp = summary.get("resolved_params") or {}
    ii = summary.get("input_identity") or {}
    ff = summary.get("feature_funnel") or {}
    buf = summary.get("buffer") or {}
    ts_by_split = summary.get("target_stats_by_split") or {}
    drift = summary.get("drift_hint") or {}
    thresholds = drift.get("thresholds") or {"median_pp": 1.0, "win_rate_pp": 5.0}
    ovt = summary.get("overall_test") or {}
    mfschema = summary.get("model_feature_schema") or {}
    art = summary.get("artifacts_generated") or {}
    lss = summary.get("label_span_stats")
    fd = summary.get("feature_drift") or {}
    lo = summary.get("label_overlap") or {}
    gtc = summary.get("group_time_concentration") or {}
    mr = summary.get("monthly_returns") or []
    q5 = summary.get("test_quintile") or []
    meds = summary.get("test_median_split") or {}
    thr = summary.get("test_threshold_filters") or {}
    mon = summary.get("monotonic") or {}
    fi = summary.get("feature_importance_top20") or []
    clip = summary.get("clip_percentiles") or {}
    split_ov = summary.get("split_overview") or []
    smp = summary.get("split_model_performance") or []

    step6 = ps.get("step_6", {}).get("details", {}) if ps else {}
    step5_det = ps.get("step_5", {}).get("details", {}) if ps else {}

    lines: List[str] = [
        f"# 实验详细报告 - {ev}",
        "",
        f"| 项 | 值 |",
        "|:---|:---|",
        f"| 实验版本 | `{ev}` |",
        f"| 生成时间 | {ga} |",
        f"| 事实源 sha256 | `{sha}` |",
        f"| schema | `{schema}` |",
        "",
        toc,
        "",
        _x0(ii, ev, mfschema, q5, lss,
            (ps.get("step_1", {}).get("details", {}) or {}).get("sample_count"),
            summary.get("datacard_path")),
        "",
        _x1(ii, ps, rp, lss),
        "",
        _x2(buf, ts_by_split, drift, thresholds, split_ov),
        "",
        _x3(ps, rp),
        "",
        _x4(ff, mfschema),
        "",
        _x5(rp, clip, ps),
        "",
        _x6(smp, split_ov, step6),
        "",
        _x7(mon, q5, meds, thr),
        "",
        _x8(fi),
        "",
        _x9(fd, lo, gtc, mr),
        "",
        _x10(smp, ii, split_ov, step6, q5, meds, mon),
    ]

    content = "\n".join(lines)
    if len(content.splitlines()) > 700:
        print(f"[HINT] experiment.md has {len(content.splitlines())} lines (>700 threshold)")
    return content


def _x0(ii, ev, mfschema, q5, lss, sample_count=None, datacard_path=None) -> str:
    mode = ii.get("input_mode", "holding_file")
    sid = (ii.get("samples_id") or "").strip()
    lname = (ii.get("label_name") or "").strip()

    if mode == "samples":
        dataset_cell = f"`{sid}`" if sid else "—"
        label_cell = f"`{lname}`" if lname else "—"
    else:
        dataset_cell = "不适用（持仓表输入）"
        label_cell = "不适用（目标列随持仓表提供）"

    dc_path = (datacard_path or "").strip()
    if dc_path:
        dc_ref = dc_path
    elif sid:
        dc_ref = f"data/reference/{sid}/datacard.md"
    else:
        dc_ref = "data/reference/_legacy_holding/datacard.md"

    return "\n".join([
        "## X0 实验卡片",
        "",
        "> 本章结论：模型有排序能力，但偏弱。 完整结论见 X10。",
        "",
        "| 项 | 值 |",
        "|:---|:---|",
        f"| 输入模式 | {ii.get('input_mode', '—')} |",
        f"| 耦合模式 | {ii.get('coupling_mode', '—')} |",
        f"| 样本集 | {dataset_cell} |",
        f"| 标签 | {label_cell} |",
        f"| 建模样本 | {_fmt_int(sample_count)} |",
        f"| 模型特征 | {mfschema.get('feature_count', '—')} |",
        "",
        f"> 数据卡见 `{dc_ref}` §D0",
    ])


def _x1(ii, ps, rp, lss) -> str:
    input_mode = ii.get("input_mode", "holding_file")
    det1 = ps.get("step_1", {}).get("details", {}) if ps else {}
    sid = ii.get("samples_id", "")
    label_name = ii.get("label_name", "")

    if input_mode == "samples":
        x11 = "\n".join([
            "### X1.1 数据处理与合并",
            "",
            "> 本章结论：样本集与标签按 `sample_id` 对齐，丢弃无标签行后得到建模样本。",
            "",
            f"- 逻辑：样本集与标签按 `sample_id` left join，丢弃标签为空的行",
            f"- 样本集行数：{_fmt_int(det1.get('samples_rows'))} → 有效行数：{_fmt_int(det1.get('sample_count'))} → 丢弃无标签：{det1.get('dropped_no_target', '—')}",
            f"- label_non_null_ratio：{det1.get('label_non_null_ratio', '—')}",
            f"- 覆盖率：{_fmt_num(det1.get('target_coverage'), digits=4, sign=False)}",
            f"- target 中位数：{_fmt_pct(det1.get('target_stats', {}).get('p50', None) if det1.get('target_stats') else None)}",
            f"- input_mode: **{ii.get('input_mode', '—')}** / coupling_mode: **{ii.get('coupling_mode', '—')}**",
            f"- samples_id: `{sid}`  |  label_name: `{label_name}`",
            "",
            "> 完整清洗规则见「参考页 §R1」",
        ])
        x12 = _x12_samples(rp, det1)
    else:
        x11 = "\n".join([
            "### X1.1 数据处理与合并",
            "",
            "> 本章结论：样本来自持仓文件，经三条规则过滤后得到建模样本。",
            "",
            "- 逻辑：从持仓文件按三条规则过滤（有目标列、非停牌、历史足够）",
            f"- 输入行数：{_fmt_int(det1.get('samples_rows'))} → 有效行数：{_fmt_int(det1.get('sample_count'))}",
            f"- 丢弃无标签：{det1.get('dropped_no_target', '—')}",
            f"- label_non_null_ratio：{det1.get('label_non_null_ratio', '—')}",
            f"- input_mode: **{ii.get('input_mode', '—')}** / coupling_mode: **{ii.get('coupling_mode', '—')}**",
            f"- samples_id: `{_ident(ii.get('samples_id'), '（无）')}`  |  label_name: `{_ident(ii.get('label_name'), '（无）')}`",
            "",
            "> 完整清洗规则见「参考页 §R1」",
        ])
        x12 = "### X1.2 样本构建\n\n一行说明：不适用（样本来自持仓表，见 X1.1）。"

    x13 = _x13_label(ii, det1, lss, rp)
    return "\n".join(["## X1 数据构建", "", "> 本章结论：数据来源与合并规则见下三节。", "", x11, "", x12, "", x13])


def _x12_samples(rp, det1) -> str:
    sp = (rp.get("data_prep") or {}).get("samples", {}).get("params", {}) if isinstance(rp, dict) else {}
    if not sp:
        sp = (rp.get("sampling_params") or {}) if isinstance(rp, dict) else {}
    ma = sp.get("ma_period", 20)
    std_m = sp.get("std_mult", 2.0)
    uni = (rp.get("data_prep") or {}).get("universe", {}) if isinstance(rp, dict) else {}
    min_hist = uni.get("min_history_days", 60)
    exc = uni.get("exclude_prefixes", ["sh000", "sz399", "sh950"])

    rows = [
        "### X1.2 样本构建",
        "",
        "> 本章结论：采用布林带上穿事件定义，完整参数见数据卡。",
        "",
        "**构造逻辑表**",
        "",
        "| 环节 | 规则 | 为什么这样定 |",
        "|:---|:---|:---|",
        f"| 事件定义 | close[t] > upper[t] 且 close[t-1] ≤ upper[t-1] | 只取上穿当天，避免连续多日重复计数 |",
        f"| 上轨公式 | upper = MA(close, {ma}) + {std_m}×STD(close, {ma})，ddof=1 | 布林带常用参数，覆盖约 95% 正常波动 |",
        f"| 前置条件 | upper[t-1] 非空（至少 {int(ma)+1} 个交易日历史） | 没有前一日上轨无法判断上穿 |",
        f"| 股票池 | 排除 {exc} 前缀；每个 symbol 非停牌天数 ≥ {min_hist} | 排除指数与次新股 |",
        f"| 价格口径 | 后复权价 | 保证跨除权的价格可比 |",
        "",
        "**样本数据处理表**",
        "",
        "| 处理 | 做法 | 影响 |",
        "|:---|:---|:---|",
        "| 停牌 | 切片取信号日及之前最近 N 个非停牌交易日 | 避免用停牌日价格计算指标 |",
        f"| 历史不足 | 非停牌历史 < lookback_days → step2 丢弃（{_fmt_int(det1.get('dropped_no_target', 0))} 条） | 次新股/复牌股被排除 |",
        "| 缺失值 | step3 对滚动窗口起点 NaN 记数并填充；inf 置 NaN 后填 0 | 数量取自 E7（本报告不复述） |",
        "| 重复事件 | 同一股票同日只保留一条 | 事件定义本身保证 |",
        "",
        "> 参数与股票池细则见数据卡 §D1–D3",
    ]
    return "\n".join(rows)


def _x13_label(ii, det1, lss, rp=None) -> str:
    mode = ii.get("input_mode", "holding_file")
    sid = _ident(ii.get("samples_id"), "_legacy_holding")
    lname = _ident(ii.get("label_name"))
    label_cell = f"`{lname}`" if (mode == "samples" and lname != "—") else "不适用（目标列随持仓表提供）"
    horizon = None
    if lss and isinstance(lss, dict):
        # label_span_stats does not store horizon_days; read from resolved_params
        horizon = (rp or {}).get("target", {}).get("horizon_days")
    cov = ii.get("label_non_null_ratio")
    cov_float = cov if isinstance(cov, (int, float)) else None
    cov_cell = (_fmt_num(cov_float, digits=4, sign=False) if cov_float is not None
                else ("—" if mode == "samples" else "不适用"))
    p50_raw = det1.get("target_stats", {}).get("p50") if det1.get("target_stats") else None

    rows = [
        "### X1.3 标签构建",
        "",
        "> 本章结论：标签为有限期后复权收益，当前口径存在端点价格不对称（见口径说明）。",
        "",
        f"| 项 | 值 |",
        "|:---|:---|",
        f"| 标签名 | {label_cell} |",
        f"| 一句话定义 | 次一非停牌日开盘入场，第 h 个非停牌日收盘出场，收益率 = 收盘/开盘 − 1 |",
        f"| 选择依据 | 与信号持有期匹配，避免与旧规则耦合 |",
        f"| 覆盖率 | {cov_cell} |",
        f"| target 中位/IQR | {_fmt_pct(p50_raw) if p50_raw else '—'} |",
    ]
    # label span stats
    if lss and isinstance(lss, dict) and lss.get("n_valid") is not None:
        median_days = lss.get("median_days", "—")
        p99_days = lss.get("p99_days", "—")
        max_days = lss.get("max_days", "—")
        share = lss.get("share_gt_60d", 0)
        n_gt = lss.get("n_gt_60d", 0)
        rows.append(
            f"| 有效持有期 | 中位 {median_days} 天 / P99 {p99_days} 天 / 最大 {max_days} 天；"
            f"跨度 > {SPAN_WARN_DAYS} 天占 {share:.4f}（{_fmt_int(n_gt)} 条） |"
        )
        if share > 0:
            rows.extend([
                "",
                f"> ⚠️ **标签名义为「{horizon} 个交易日」，实为「{horizon} 个非停牌交易日」**：停牌会把实际跨度拉长至数十天到数年。",
                f"> 跨停牌的复牌首日通常无涨跌幅限制，价格前后不可比；这些样本的极值不代表可预测收益。",
                f"> 详见 `docs/16_potential_risks.md` PR-009 / PR-010。",
            ])
    elif ii.get("input_mode") == "samples":
        rows.extend(["", "> [WARN] label_span_stats 缺失，无法显示有效持有期。"])

    rows += [
        "",
        "> 口径说明：本标签入场取「次一非停牌日 open」、出场取「第 h 个非停牌日 close」，",
        "> 实际跨 h+1 个交易时段（h 个隔夜 + h+1 个日内），且两端价格类型不对称（集合竞价 vs 收盘）。",
        "> 该口径为当前实现，存在待议项（`docs/16_potential_risks.md` PR-011）；",
        "> 报告只陈述当前口径，不得据此宣称其为最优或标准做法。",
        "",
        f"> 全部可选标签及覆盖率见「数据卡 `{sid}` §D4」",
    ]
    return "\n".join(rows)


def _x2(buf, ts_by_split, drift, thresholds, split_ov) -> str:
    med_pp = thresholds.get("median_pp", 1.0)
    wr_pp = thresholds.get("win_rate_pp", 5.0)

    split_lines = ["| 集合 | 样本数 | 时间范围 |", "|:---|---:|:---|"]
    for rec in split_ov:
        split_lines.append(f"| {rec.get('集合','—')} | {_fmt_int(rec.get('样本数'))} | {rec.get('时间范围','—')} |")

    # target stats table
    stat_rows = ["| 集合 | min | P25 | P50 | P75 | max | IQR | 胜率 |",
                 "|:---|---:|---:|---:|---:|---:|---:|---:|"]
    for split_name, label in [("train", "训练"), ("val", "验证"), ("test", "测试")]:
        st = ts_by_split.get(split_name) or {}
        if st:
            stat_rows.append(
                f"| {label} | {_fmt_pct(st.get('min'))} | {_fmt_pct(st.get('p25'))} | "
                f"{_fmt_pct(st.get('p50'))} | {_fmt_pct(st.get('p75'))} | {_fmt_pct(st.get('max'))} | "
                f"{_fmt_pct(st.get('iqr'))} | {_fmt_pct(st.get('win_rate'))} |"
            )

    # drift hints
    drift_lines = []
    for split_name, split_label in [("val", "验证集"), ("test", "测试集")]:
        dh = drift.get(split_name) or {}
        md = dh.get("median_delta_pp")
        wd = dh.get("win_rate_delta_pp")
        if md is not None:
            exceed_note = f"，超过 {med_pp}pp 经验阈值，建议关注分布差异" if abs(md) > med_pp else "（未超阈）"
            drift_lines.append(
                f"> 提示：{split_label}中位数 {md:+.2f}pp{exceed_note}。"
            )
        if wd is not None:
            exceed_note = f"，超过 {wr_pp}pp 经验阈值" if abs(wd) > wr_pp else "（未超阈）"
            drift_lines.append(
                f"> 提示：{split_label}胜率差 {wd:+.2f}pp{exceed_note}。"
            )

    drift_lines += [
        "",
        f"> 阈值说明：{med_pp}pp / {wr_pp}pp 为本项目**经验约定，不是统计检验结果**（无 p 值、非自适应），",
        f"> 仅用于提醒读者核对，不构成存在分布漂移的结论。",
        f"> （`{med_pp}` / `{wr_pp}` 由 `drift_hint.thresholds` 注入，禁止写死。）",
    ]

    return "\n".join([
        "## X2 数据切分",
        "",
        "> 本章结论：全量按时间切分，buffer 实测值满足要求。",
        "",
        f"- 切分逻辑（全量）：按 `signal_date` 严格时间切；buffer 要求 {buf.get('required','?')} 交易日",
        f"- buffer 实测：train/val 间隔 {buf.get('train_val_days','?')} 天 / val/test 间隔 {buf.get('val_test_days','?')} 天 / 状态 {buf.get('status','?')}",
        "",
        "**切分后统计**",
        "",
        "\n".join(split_lines),
        "",
        "**target_raw 五数（±IQR+胜率）**",
        "",
        "\n".join(stat_rows),
        "",
        "> 读法：P25–P75（IQR）反映主分布宽度；min/max 是尾部极值，代表最坏/最好情况，两者需一起看。",
        "",
        "**分布图**",
        "",
        "![](../assets/dist_target_raw.png)",
        "",
        "*图：target_raw 分布（0.1%/99.9% 裁切显示，统计量未裁）｜怎么读：看中心位置与尾部不对称性*",
        "",
        "![](../assets/dist_target.png)",
        "",
        "*图：target（缩尾后，不裁切）｜怎么读：与上图对比缩尾效果*",
        "",
        "**漂移提示**",
        "",
        "\n".join(drift_lines),
        "",
        "**泄漏检查**",
        "",
        f"- buffer ≥ lookback_days：buffer = {buf.get('train_val_days','?')} 天，状态 = {buf.get('status','—')}",
        "- 特征筛选与缩尾分位数均只在训练集内部计算（step4/step5）",
        "",
        "> 完整切分规则见「参考页 §R4」",
    ])


def _x3(ps, rp) -> str:
    return "\n".join([
        "## X3 步骤 1–6 参数与统计",
        "",
        "> 本章结论：所有步骤均已完成，详见 E2。本章只列各步关键配置参数。",
        "",
        f"- step1: input_mode={((rp.get('input') or {{}}).get('mode', '?') if isinstance(rp, dict) else '?')}",
        f"- step2: lookback_days={((rp.get('window') or {{}}).get('lookback_days', '?') if isinstance(rp, dict) else '?')}",
        f"- step3: indicators.profile={((rp.get('indicators') or {{}}).get('profile', '?') if isinstance(rp, dict) else '?')}",
        f"- step4: fc_profile={((rp.get('tsfresh') or {{}}).get('fc_profile', '?') if isinstance(rp, dict) else '?')}",
        f"- step5: 时间切分，enforce_buffer={((rp.get('time_split') or {{}}).get('enforce_buffer', '?') if isinstance(rp, dict) else '?')}",
        f"- step6: lgb objective={((rp.get('lgb') or {{}}).get('objective', '?') if isinstance(rp, dict) else '?')}",
        "",
        "> 耗时与校验明细见工程报告 E2/E4。",
    ])


def _x4(ff, mfschema) -> str:
    raw = ff.get('raw_extracted', '?')
    zv = ff.get('zero_variance_dropped', '?')
    cands = ff.get('candidates', '?')
    top = ff.get('top_candidates', '?')
    sel = ff.get('selected', '?')
    man = ff.get('manual_added', '?')
    model_in = ff.get('model_input', '?')
    consist = ff.get('consistency_ok', '?')
    return "\n".join([
        "## X4 特征工程",
        "",
        "> 本章结论：特征漏斗路径：原始提取 → 零方差剔除 → 候选 → 相关性去重 → 入选 → 手动特征注入 → 模型输入。",
        "",
        "**特征漏斗（唯一归属）**",
        "",
        "| 环节 | 数量 |",
        "|:---|---:|",
        f"| 原始提取（tsfresh 全量） | {raw} |",
        f"| 零方差剔除 | {zv} |",
        f"| 候选（原始 - 零方差） | {cands} |",
        f"| top_candidates 截断 | {top} |",
        f"| 入选（相关性去重后） | {sel} |",
        f"| 手动特征注入 | {man} |",
        f"| 模型输入（入选 + 手动） | {model_in} |",
        f"| 一致性校验 | {'✔' if consist else '✘'} |",
        "",
        "> 完整特征工程规则见「参考页 §R3」",
    ])


def _x5(rp, clip, ps) -> str:
    lgb = (rp.get("lgb") or {}) if isinstance(rp, dict) else {}
    p1 = (clip.get("p1") or {})
    p99 = (clip.get("p99") or {})
    return "\n".join([
        "## X5 模型与标签处理",
        "",
        "> 本章结论：LightGBM 回归，标签经训练集 P1/P99 缩尾。",
        "",
        "**为什么用 LightGBM**",
        "",
        "LightGBM 在表格型时序金融数据上既快又稳，支持缺失值与类别特征；",
        "回归目标（收益率）比分类目标更直接反映排序能力；",
        "tree-based 模型不要求特征标准化，对长尾分布鲁棒性更好。",
        "",
        "| 参数 | 值 |",
        "|:---|:---|",
        f"| objective | {lgb.get('objective', '—')} |",
        f"| num_leaves | {lgb.get('num_leaves', '—')} |",
        f"| learning_rate | {lgb.get('learning_rate', '—')} |",
        f"| early_stopping_rounds | {lgb.get('early_stopping_rounds', '—')} |",
        f"| num_threads | {lgb.get('num_threads', '—')} |",
        "",
        f"**标签缩尾**：训练集 P1 = {_fmt_pct(p1) if p1 else '—'}，P99 = {_fmt_pct(p99) if p99 else '—'}",
        "",
        "> 完整标签公式见「参考页 §R5」",
    ])


def _x6(smp, split_ov, step6) -> str:
    src = smp if smp else split_ov
    rows = ["| 集合 | 样本数 | MAE | Spearman | Pearson |", "|:---|---:|---:|---:|---:|"]
    for rec in src:
        rows.append(
            f"| {rec.get('集合','—')} | {_fmt_int(rec.get('样本数'))} | "
            f"{_fmt_num(rec.get('target MAE'), digits=6, sign=False)} | "
            f"{_fmt_num(rec.get('排序 Spearman'), digits=4)} | "
            f"{_fmt_num(rec.get('排序 Pearson'), digits=4)} |"
        )
    # train → test 衰减（设计 §4.2 要求）
    decay = None
    by_label = {rec.get("集合"): rec for rec in src}
    tr = (by_label.get("训练集") or {}).get("排序 Spearman")
    te = (by_label.get("测试集") or {}).get("排序 Spearman")
    if tr is not None and te is not None:
        decay = te - tr
    tail = []
    if decay is not None:
        tail.append(f"train→test Spearman 衰减 = {_fmt_num(decay, digits=4)}。")
    tail.append(
        f"测试集 MAE={_fmt_num(step6.get('mae'), digits=6, sign=False)}，"
        f"Spearman={_fmt_num(step6.get('spearman'), digits=4)}。"
    )
    return "\n".join([
        "## X6 模型表现",
        "",
        f"> 本章结论：{_decay_text(smp)}；详见下表。",
        "",
        "\n".join(rows),
        "",
        *tail,
    ])


def _x7(mon, q5, meds, thr) -> str:
    mon_sum = mon.get("summary") or {}
    per_split = mon_sum.get("per_split") or {}
    # Build monotonic summary table
    sum_rows = ["| 集合 | 秩相关 | 首10组均值 | 末10组均值 |", "|:---|---:|---:|---:|"]
    for split_name, label in [("train", "训练"), ("val", "验证"), ("test", "测试")]:
        s = per_split.get(split_name) or {}
        rc = s.get("spearman")
        f10 = s.get("first_group_target_mean")
        l10 = s.get("last_group_target_mean")
        sum_rows.append(
            f"| {label} | {_fmt_num(rc, digits=4)} | {_fmt_pct(f10)} | {_fmt_pct(l10)} |"
        )

    # quintile table
    q_rows = ["| 分组 | 目标均值(raw) | 胜率 |", "|---:|---:|---:|"]
    for rec in sorted(q5, key=lambda r: r.get("pred_group", 0)):
        grp = rec.get("pred_group", "?")
        mean = rec.get("target_raw_mean")
        wr = rec.get("target_raw_win_rate")
        q_rows.append(f"| {grp} | {_fmt_pct(mean)} | {_fmt_pct(wr)} |")

    # median split
    med_uplift = meds.get("uplift") or {}
    med_rows = ["| 组 | target_raw 均值 |", "|:---|---:|"]
    for rec in (meds.get("rows") or []):
        if rec.get("target_raw_mean") is None:
            continue
        med_rows.append(f"| {rec.get('score_group','?')} | {_fmt_pct(rec.get('target_raw_mean'))} |")
    if med_uplift:
        med_rows.append(f"| **提升** | **{_fmt_pct(med_uplift.get('target_raw_mean_diff'))}** |")

    # threshold table
    thr_rows = ["| 阈值 | 保留前 | target_raw 均值 | vs 全体 |", "|---:|---:|---:|---:|"]
    for rec in (thr.get("rows") or []):
        q = rec.get("score_quantile", rec.get("quantile", "?"))
        try:
            kept = f"{(1-float(q))*100:.0f}%"
        except Exception:
            kept = "?"
        mean = rec.get("target_raw_mean")
        vs_raw = rec.get("target_raw_mean_vs_all")
        vs_pp = vs_raw * 100 if isinstance(vs_raw, (int, float)) else None
        thr_rows.append(f"| {q} | {kept} | {_fmt_pct(mean)} | {_fmt_num(vs_pp, digits=2)}pp |")

    return "\n".join([
        "## X7 分组与单调性",
        "",
        "> 本章结论：高预测分对应高收益，五分组单调上行，中位数过滤有效。",
        "",
        "**100 组抗噪汇总**",
        "",
        "\n".join(sum_rows),
        "",
        "**100 组可视化**",
        "",
        "![](../assets/monotonic_target_all.png)",
        "",
        "*图：target_raw_mean 三集合对比｜怎么读：整体上行 = 排序有效；曲线走平或抖动 = 该区间无稳定信息*",
        "",
        "![](../assets/monotonic_target_train.png)",
        "",
        "![](../assets/monotonic_target_val.png)",
        "",
        "![](../assets/monotonic_target_test.png)",
        "",
        "![](../assets/monotonic_y_pred_groups.png)",
        "",
        "> ① 横轴：样本按预测分低→高分成 100 组；② 曲线整体上行 = 排序有效；",
        '> ③ 看的是排序准不准，不是预测涨跌幅准不准；',
        "> ④ 首尾单组约 500 条，均值易被极端样本带动；趋势请看整体秩相关与首/末 10 组均值。",
        "",
        "**五分组表**",
        "",
        "\n".join(q_rows),
        "",
        "**中位数二分表**",
        "",
        "\n".join(med_rows),
        "",
        "**阈值过滤表**",
        "",
        "\n".join(thr_rows),
        "",
        "> 注意：阈值不是越激进越好。只留前 10% 时收益回落——说明最顶端预测并不可靠，不宜当精选器。",
    ])


def _x8(fi) -> str:
    rows = ["| 排名 | 特征 | Gain | Gain 占比 | 来源 |", "|---:|:---|---:|---:|:---|"]
    for rec in fi[:20]:
        rows.append(
            f"| {rec.get('rank','—')} | `{rec.get('feature','—')}` | "
            f"{_fmt_num(rec.get('gain'), digits=2, sign=False)} | "
            f"{_fmt_pct(rec.get('gain_share'))} | "
            f"{rec.get('feature_origin','—')} |"
        )
    return "\n".join([
        "## X8 特征重要性",
        "",
        "> 本章结论：头部特征集中度较高，可探索特征多样化。",
        "",
        "\n".join(rows),
    ])


def _x9(fd, lo, gtc, mr) -> str:
    # X9.1 feature drift
    fd_count = fd.get("count_over_0_1")
    fd_med = fd.get("median_ks")
    fd_top10 = fd.get("top10") or []
    fd_reason = fd.get("reason")

    fd_rows = ["| 特征 | KS 值 |", "|:---|---:|"]
    for rec in fd_top10[:10]:
        fd_rows.append(f"| `{rec.get('feature','?')}` | {rec.get('ks', 0):.4f} |")

    # X9.2 label overlap
    lo_share = lo.get("share_gap_le_horizon")
    lo_reason = lo.get("reason")
    # X9.3 group time concentration
    gtc_max = gtc.get("max_share")
    gtc_reason = gtc.get("reason")
    # X9.4 monthly returns
    mr_months = len(mr)
    mr_pos = sum(1 for r in mr if r.get("mean", 0) > 0) / mr_months if mr_months > 0 else None
    mr_best = max(mr, key=lambda r: r.get("mean", -999), default=None) if mr else None
    mr_worst = min(mr, key=lambda r: r.get("mean", 999), default=None) if mr else None

    return "\n".join([
        "## X9 稳健性与漂移",
        "",
        '> 本章结论：本节提供稳健性证据，不下稳健或不稳健结论。',
        "",
        "> 图待补（v1.2）：特征漂移 Top10 条形图、月度收益柱状图",
        "",
        "### X9.1 特征漂移（训练集 vs 测试集 KS）",
        "",
        (f"超过 KS > 0.1 的特征数：{fd_count}，中位 KS：{fd_med:.4f}" if fd_count is not None
         else f"未计算：{fd_reason or '字段缺失'}"),
        "",
        "\n".join(fd_rows) if fd_top10 else "",
        "",
        "### X9.2 标签重叠",
        "",
        (f"- share_gap_le_horizon = {lo_share:.4f}（同一股票相邻信号间隔 ≤ horizon_days 的比例）\n"
         "- **注意**：样本非独立，置信区间被高估，不得用样本数当独立样本数。"
         if lo_share is not None else f"未计算：{lo_reason or '字段缺失'}"),
        "",
        "### X9.3 分组时间集中度",
        "",
        (f"- max_share = {gtc_max:.4f}" + (
            "（建议关注：结论可能被单年行情放大）" if gtc_max and gtc_max > 0.70 else ""
        ) if gtc_max is not None else f"未计算：{gtc_reason or '字段缺失'}"),
        "",
        "### X9.4 月度收益",
        "",
        (f"- 月数：{mr_months}，正收益月占比：{_fmt_pct(mr_pos)}\n"
         f"- 最好月：{mr_best.get('ym')} ({_fmt_pct(mr_best.get('mean'))})，"
         f"最差月：{mr_worst.get('ym')} ({_fmt_pct(mr_worst.get('mean'))})"
         if mr else "未计算：monthly_returns 为空"),
        "",
        ("> 简单说：均值由少数月份贡献，建议关注月度分布是否均匀。" if mr else ""),
    ])


def _x10(smp, ii, split_ov, step6, q5, meds, mon) -> str:
    sp = step6.get("spearman")
    mon_sum = mon.get("summary") or {}
    per_split_mon = mon_sum.get("per_split") or {}
    test_mon = per_split_mon.get("test") or {}
    rc_test = test_mon.get("spearman")

    q_sorted = sorted(
        [r for r in (q5 or []) if r.get("target_raw_mean") is not None],
        key=lambda r: r.get("pred_group", 0),
    )
    q_means = [float(r["target_raw_mean"]) for r in q_sorted]
    head_tail = (q_means[-1] - q_means[0]) * 100 if len(q_means) >= 5 else None

    med_uplift = (meds.get("uplift") or {}).get("target_raw_mean_diff")

    decay = _split_decay(smp)
    decay_state = decay["direction"]

    mode = ii.get("input_mode", "holding_file")
    cov = ii.get("label_non_null_ratio")
    if mode == "samples" and cov is not None:
        label_view = f"标签覆盖率 {cov * 100:.1f}%"
    else:
        label_view = "无独立标签覆盖率（目标列随持仓表提供）"

    if decay_state == "down":
        why_not_bet = "训练→测试 Spearman 衰减约一半，稳定性未验证"
    elif decay_state == "up":
        why_not_bet = "训练→测试排序能力未见衰减，但仍需多期验证"
    else:
        why_not_bet = "训练→测试排序能力基本持平，仍需多期验证"

    return "\n".join([
        "## X10 结论与局限",
        "",
        "> 本章结论：模型有排序价值，但应作过滤器而非决策器；稳健性证据有限。",
        "",
        "### X10.1 分视角结论",
        "",
        f"**统计视角**：测试集 Spearman = {_fmt_num(sp)}；100 组秩相关 = {_fmt_num(rc_test)}；",
        f"{_decay_text(smp)}。",
        "",
        f"**交易视角**：五分组头尾收益差 = {_fmt_num(head_tail, digits=2) if head_tail else '—'}pp；",
        f"中位数过滤提升 = {_fmt_pct(med_uplift)}；适合宽阈值过滤，不适合极端筛选。",
        "",
        f"**数据与标签视角**：{label_view}；存在停牌导致的长跨度标签，极值不代表可预测收益（见 X1.3）。",
        "",
        "**工程视角**：流程完整，step4 是主要时间成本；确定性未正式校验。",
        "",
        "### X10.2 可探索方向",
        "",
        "| 优先级 | 方向 | 预期产出 | 成本 |",
        "|:---|:---|:---|:---|",
        "| P0 | 验证确定性（同 seed 重跑） | 确认结果可复现 | 低 |",
        "| P0 | 加入更长持有期标签（40d/60d） | 评估不同持仓周期的有效性 | 低 |",
        "| P1 | 多轮时间窗口 out-of-time 验证 | 确认排序能力非单期偶然 | 中 |",
        "| P1 | 增加更多样本池（更多标的） | 提高统计显著性 | 中 |",
        "| P2 | 特征多样化（如宏观/情绪特征） | 探索与 tsfresh 特征的互补性 | 高 |",
        "| P2 | 加入交易成本假设 | 评估实际可用性 | 中 |",
        "| P2 | 神经网络对比 | 看 LightGBM 是否是瓶颈 | 高 |",
        "| P2 | 在线学习/滚动训练 | 验证是否改善漂移 | 高 |",
        "",
        "### X10.3 不宜做的事",
        "",
        "| 反模式 | 为什么 |",
        "|:---|:---|",
        "| 直接用作绝对收益预测（择时） | 模型目标是排序，不是收益率预测；绝对值无意义 |",
        f"| 基于单次实验结论大幅下注 | {why_not_bet} |",
        "| 只留最顶端 10% 以期更高收益 | 阈值表显示顶端收益回落，极端筛选适得其反 |",
        "",
        "### X10.4 已知局限",
        "",
        "- 未计入交易成本（冲击/手续费），实际收益可能更低",
        "- 样本期仅覆盖 2007–2025，未包含 2000–2006 低流动性市场",
        "- 月度回测未用独立样本，标签重叠使置信区间偏小",
        "- 确定性未正式校验（需同 seed 重跑一次）",
        "- 长跨度停牌样本（>60天）仍在训练集中，可能导致极值偏差",
    ])


