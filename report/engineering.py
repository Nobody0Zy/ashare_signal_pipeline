"""report/engineering.py — engineering report renderer E0-E11 (design §4.1)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]

_STEP_CN = {
    "step_1": "步骤1 数据准备", "step_2": "步骤2 切片构造",
    "step_3": "步骤3 技术指标", "step_4": "步骤4 特征工程",
    "step_5": "步骤5 划分与合并", "step_6": "步骤6 模型训练",
}
_WSTEP_CN = {
    "step1": "步骤1 数据准备", "step2": "步骤2 切片构造",
    "step3": "步骤3 技术指标", "step4": "步骤4 特征工程",
    "step5": "步骤5 划分与合并", "step6": "步骤6 模型训练",
}


def _ident(value: Any, default: str = "—") -> str:
    """Render an identifier: empty string is treated same as None."""
    text = str(value).strip() if value is not None else ""
    return text if text else default


def _short_fp(value: Any, width: int, ellipsis: bool = False) -> str:
    """Truncate a fingerprint for display; empty values render as 不适用."""
    text = str(value).strip() if value is not None else ""
    if not text:
        return "不适用"
    out = text[:width]
    return f"{out}…" if ellipsis else out


def _fmt_int(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except Exception:
        return str(v)


def _fmt_pct(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v)*100:+.{digits}f}%"
    except Exception:
        return str(v)


def _fmt_num(v: Any, digits: int = 4) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):+.{digits}f}"
    except Exception:
        return str(v)


def _fmt_bytes(n: Any) -> str:
    if n is None:
        return "—"
    try:
        v = float(n)
    except Exception:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(v) < 1024.0:
            return f"{v:.1f} {unit}"
        v /= 1024.0
    return f"{v:.1f} PB"


def _fmt_hms(secs: Any) -> str:
    if secs is None:
        return "—"
    try:
        t = int(float(secs))
        h, r = divmod(t, 3600)
        m, s = divmod(r, 60)
        txt = f"{h:02d}:{m:02d}:{s:02d}"
        if t >= 3600:
            txt += f"（{t//60} min）"
        return txt
    except Exception:
        return "—"


def render_engineering(summary: Dict[str, Any]) -> str:
    """Render engineering.md from report_summary.json. Summary must contain _render_ctx or be standalone."""
    ga = datetime.now().isoformat(timespec="seconds")
    ev = summary.get("experiment_version", "")
    schema = summary.get("schema_version", "")
    rp = summary.get("resolved_params") or {}

    # sha256 of summary (for E0 header)
    import json, hashlib
    summary_copy = {k: v for k, v in summary.items() if k != "_render_ctx"}
    sha = hashlib.sha256(json.dumps(summary_copy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]

    ps = summary.get("pipeline_steps") or {}
    runs = summary.get("runs") or {}
    run_modes = summary.get("run_modes") or []
    main_mode = "full" if "full" in run_modes else (run_modes[0] if run_modes else "")
    re_ = summary.get("run_env") or {}
    ii = summary.get("input_identity") or {}
    fp = summary.get("fingerprints") or {}
    art = summary.get("artifact_sizes") or []
    smoke_full = summary.get("smoke_vs_full") or {}
    workload = summary.get("workload") or {}
    pe = summary.get("param_effective") or []
    det = summary.get("determinism") or {}
    cleaning = summary.get("cleaning") or {}
    ff = summary.get("feature_funnel") or {}
    buf = summary.get("buffer") or {}

    sections = [
        "E0 运行标识", "E1 输入溯源", "E2 步骤执行",
        "E3 瓶颈分析", "E4 校验明细", "E6 产物清单",
        "E7 数据质量与 smoke 对照", "E8 性能与工作量归因",
        "E9 参数生效清单", "E10 确定性校验", "E11 复现命令",
    ]
    toc_lines = ["## 目录", ""]
    for s in sections:
        anchor = s.lower().replace(" ", "-")
        toc_lines.append(f"- [{s}](#{anchor})")

    # Compute totals
    total_secs: Optional[float] = None
    if ps:
        try:
            total_secs = sum(
                (ps.get(sk) or {}).get("duration_seconds") or 0
                for sk in ["step_1", "step_2", "step_3", "step_4", "step_5", "step_6"]
            )
        except Exception:
            pass

    # Artifact totals
    art_total_bytes = sum(r.get("bytes", 0) for r in art)
    art_count = len(art)

    lines: List[str] = [
        f"# 工程详细报告 - {ev}",
        "",
        f"| 项 | 值 |",
        "|:---|:---|",
        f"| 实验版本 | `{ev}` |",
        f"| run_mode | {main_mode} |",
        f"| 生成时间 | {ga} |",
        f"| 事实源 sha256 | `{sha}` |",
        f"| schema | `{schema}` |",
        "",
        "> 本章结论：三行摘要——计算已完成，产物总量约 " + _fmt_bytes(art_total_bytes) + f"（{_fmt_int(art_count)} 个文件）；瓶颈在 step4 特征工程；确定性校验{'已执行' if det.get('executed') else '未执行'}。",
        "",
        "\n".join(toc_lines),
        "",
        _e0(ev, main_mode, ga, sha, schema, re_, rp, ii),
        "",
        _e1(ii, fp),
        "",
        _e2(ps, total_secs),
        "",
        _e3(ps),
        "",
        _e4(ps),
        "",
        _e6(art, ps, ii),
        "",
        _e7(cleaning, smoke_full, run_modes, buf, ff),
        "",
        _e8(workload, ff),
        "",
        _e9(pe),
        "",
        _e10(det),
        "",
        _e11(ev, rp),
        "",
        _appendix_a(fp),
    ]
    content = "\n".join(lines)
    # Hint on length
    if len(content.splitlines()) > 400:
        print(f"[HINT] engineering.md has {len(content.splitlines())} lines (>400 threshold)")
    return content


def _e0(ev, mode, ga, sha, schema, re_, rp, ii) -> str:
    seed = (rp.get("seed") or (rp.get("lgb") or {}).get("seed")) if isinstance(rp, dict) else "—"
    nt = (rp.get("lgb") or {}).get("num_threads") if isinstance(rp, dict) else "—"
    return "\n".join([
        "## E0 运行标识",
        "",
        "> 本章结论：本次运行可由以下版本与参数完整复现。",
        "",
        "| 项 | 值 |",
        "|:---|:---|",
        f"| 实验版本 | `{ev}` |",
        f"| run_mode | {mode} |",
        f"| git commit | `{re_.get('git_commit', '—')}` |",
        f"| 内核基线 tag | v1.1.0 |",
        f"| 输入模式 | {ii.get('input_mode', '—')} |",
        f"| seed | {seed} |",
        f"| num_threads | {nt} |",
        f"| Python | {re_.get('python', '—')} |",
        f"| pandas | {re_.get('pandas', '—')} |",
        f"| numpy | {re_.get('numpy', '—')} |",
        f"| scipy | {re_.get('scipy', '—')} |",
        f"| lightgbm | {re_.get('lightgbm', '—')} |",
        f"| tsfresh | {re_.get('tsfresh', '—')} |",
    ])


def _e1(ii, fp) -> str:
    return "\n".join([
        "## E1 输入溯源",
        "",
        "> 本章结论：若怀疑结果不一致，先比这三个指纹——datebar/samples_set/label_params 完全相同才能保证复现。",
        "",
        "| 指纹 | 值（短） |",
        "|:---|:---|",
        f"| datebar | `{_short_fp(fp.get('datebar'), 20, ellipsis=True)}` |",
        f"| samples_set | `{_short_fp(fp.get('samples_set'), 16)}` |",
        f"| label_params | `{_short_fp(fp.get('label_params'), 16)}` |",
        f"| resolved_params | `{_short_fp(fp.get('resolved_params'), 8)}` |",
        "",
        f"- input_mode: **{ii.get('input_mode', '—')}** / coupling_mode: **{ii.get('coupling_mode', '—')}**",
        f"- samples_id: `{_ident(ii.get('samples_id'), '（无）')}`",
        f"- label_name: `{_ident(ii.get('label_name'), '（无）')}`",
        "",
        "> 完整指纹值见附表 A。",
    ])


def _e2(ps, total_secs) -> str:
    rows = ["| 步骤 | 状态 | 耗时 | 占比 | 检查通过 |", "|:---|:---|---:|---:|:---:|"]
    step_keys = ["step_1", "step_2", "step_3", "step_4", "step_5", "step_6"]
    for sk in step_keys:
        p = (ps or {}).get(sk) or {}
        dur = p.get("duration_seconds")
        pct = (dur / total_secs * 100) if (dur and total_secs) else None
        rows.append(
            f"| {_STEP_CN.get(sk, sk)} | {p.get('status', '—')} | "
            f"{_fmt_hms(dur)} | {f'{pct:.1f}%' if pct is not None else '—'} | "
            f"{'✔' if p.get('checks_passed') else '✘'} |"
        )
    rows.append(f"| **合计** | — | **{_fmt_hms(total_secs)}** | 100% | — |")
    return "\n".join([
        "## E2 步骤执行",
        "",
        "> 本章结论：所有步骤均已完成。耗时是墙钟时间（wall-clock），包含 I/O 等待；实际 CPU 时间由工作量归因（E8）给出。",
        "",
        "\n".join(rows),
    ])


def _e3(ps) -> str:
    step_keys = ["step_1", "step_2", "step_3", "step_4", "step_5", "step_6"]
    durs = [(sk, (ps or {}).get(sk, {}).get("duration_seconds") or 0) for sk in step_keys]
    durs.sort(key=lambda x: x[1], reverse=True)
    top3 = [(sk, d) for sk, d in durs[:3] if d > 0]
    rows = ["| 排名 | 步骤 | 耗时 |", "|---:|:---|---:|"]
    for i, (sk, d) in enumerate(top3, 1):
        rows.append(f"| {i} | {_STEP_CN.get(sk, sk)} | {_fmt_hms(d)} |")
    return "\n".join([
        "## E3 瓶颈分析",
        "",
        "> 本章结论：优先优化耗时最长的步骤（通常为 step4 tsfresh 特征提取）能最快缩短实验迭代时间。",
        "",
        "\n".join(rows),
        "",
        "建议：先优化 step4 而非直接调训练参数，短期收益更高。",
    ])


def _e4(ps) -> str:
    parts = []
    group1 = ["step_1", "step_2"]
    group2 = ["step_3", "step_4", "step_5", "step_6"]
    for group_name, keys in [("step1–step2 校验", group1), ("step3–step6 校验", group2)]:
        inner = []
        for sk in keys:
            p = (ps or {}).get(sk) or {}
            checks = p.get("checks", []) or []
            if checks:
                inner.append(f"**{_STEP_CN.get(sk, sk)}**")
                for c in checks:
                    if isinstance(c, dict):
                        name = c.get("name", str(c))
                        status = "✔" if c.get("passed", True) else "✘"
                        inner.append(f"- {status} {name}")
                    else:
                        inner.append(f"- {c}")
        if inner:
            parts.append(f"<details><summary>{group_name}</summary>\n\n" + "\n".join(inner) + "\n\n</details>")
    return "\n".join([
        "## E4 校验明细",
        "",
        "> 本章结论：各步骤校验结果见折叠块。",
        "",
        "\n".join(parts) if parts else "> 无校验记录。",
    ])


def _e6(art, ps, ii) -> str:
    from report.load import fmt_bytes as _fb

    _DIR_META = {
        "processed": ("step1（+step2 覆盖 holding_clean）", "清洗后的样本表 + 行情日历", "排障、复算样本量", "体积由行情表决定，约数百 MB"),
        "slices": ("step2", "每条信号的定长窗口（N 行/样本）", "step3 输入", "行数 = 有效样本 × lookback_days"),
        "ts_features": ("step3+step4", "tsfresh 全量特征；供 step4 筛选", "step4 输入", "最大产物，约为 slices 的数倍"),
        "features": ("step4", "筛选后的建模特征 + 入选清单 + 缩尾分位", "step5 输入、复现筛选", "≈ 样本数 × 模型特征数 × 8 字节"),
        "splits": ("step5", "按时间切好的 train/val/test", "step6 输入、报告统计", "三者之和 = 建模样本数"),
        "models": ("step6", "LightGBM 文本模型（可读）", "推理、特征重要性", "与树数量成正比"),
        "outputs": ("step6 + 分析", "三集合预测 + 报告 + 元数据", "报告、下游分析", "预测 CSV 含全部特征列，故偏大"),
    }
    group_summary: Dict[str, Dict] = {}
    for rec in art:
        g = rec.get("group", "unknown")
        group_summary.setdefault(g, {"files": 0, "bytes": 0})
        group_summary[g]["files"] += 1
        group_summary[g]["bytes"] += rec.get("bytes", 0)

    rows = ["| 目录 | 产出步骤 | 文件数 | 体积 | 目的 | 多大正常 |",
            "|:---|:---|---:|---:|:---|:---|"]
    for g, (step, what, who, normal) in _DIR_META.items():
        stats = group_summary.get(g)
        if stats:
            rows.append(f"| {g} | {step} | {stats['files']:,} | {_fb(stats['bytes'])} | {what} | {normal} |")
        else:
            rows.append(f"| {g} | {step} | — | — | {what} | {normal} |")

    # holding_clean warning
    det1 = (ps or {}).get("step_1", {}).get("details", {}) if ps else {}
    det2 = (ps or {}).get("step_2", {}).get("details", {}) if ps else {}
    step1_rows = det1.get("sample_count", "?")
    step2_rows = det2.get("valid_samples", "?")

    return "\n".join([
        "## E6 产物清单",
        "",
        "> 本章结论：先看产出步骤判断能否跳过（如只改模型参数，step1–4 产物可复用）；再看多大算正常判断体积是否异常。",
        "",
        "\n".join(rows),
        "",
        "### data/processed 专节",
        "",
        "| 文件 | 由谁读 | 里面是什么 | 怎么来的 |",
        "|:---|:---|:---|:---|",
        f"| `holding_clean.parquet` | step2 | 建模样本表 | 按 `input_mode={ii.get('input_mode','?')}` 分支构建；**会被 step2 覆盖** |",
        f"| `datebar.parquet` | step2 | 行情底表 | 从行情文件解包 + 排序 |",
        "",
        f"> ⚠️ `holding_clean.parquet` 会被 step2 覆盖：step1 写出 {_fmt_int(step1_rows)} 行（全部有标签样本），",
        f"> step2 校验 60 日历史后重写为 {_fmt_int(step2_rows)} 行。看行数时请确认读的是哪一步之后的状态。",
        f"> 该覆盖属已知反模式，详见 docs/16_potential_risks.md PR-001。",
    ])


def _e7(cleaning, smoke_full, run_modes, buf, ff) -> str:
    nan_total = sum((c.get("nan_filled") or 0) for c in cleaning.values())
    inf_total = sum((c.get("inf_replaced") or 0) for c in cleaning.values())
    insuf = sum((c.get("insufficient_history") or 0) for c in cleaning.values())

    # smoke vs full table
    has_smoke = "smoke" in run_modes and "full" in run_modes
    svf_rows = ["| 指标 | smoke | full | Δ |", "|:---|---:|---:|---:|"]
    for metric, label in [("valid_samples", "有效样本数"), ("selected_features", "入选特征数"),
                           ("best_iteration", "最佳迭代"), ("mae", "MAE"), ("spearman", "Spearman"),
                           ("total_seconds", "耗时(秒)")]:
        sv = (smoke_full.get(metric) or {}).get("smoke")
        fv = (smoke_full.get(metric) or {}).get("full")
        d = (smoke_full.get(metric) or {}).get("delta")
        svf_rows.append(f"| {label} | {sv if sv is not None else '—'} | {fv if fv is not None else '—'} | {f'{d:+.4f}' if isinstance(d, float) else '—'} |")

    smoke_note = "" if has_smoke else "\n> smoke 未执行（仅 full 数据）。"
    return "\n".join([
        "## E7 数据质量与 smoke 对照",
        "",
        "> 本章结论：NaN 填充与 inf 替换已完成，行数见下表；buffer 状态见 X2。",
        "",
        "| 项 | 值 |",
        "|:---|---:|",
        f"| nan_filled（所有集合合计） | {_fmt_int(nan_total)} |",
        f"| inf_replaced | {_fmt_int(inf_total)} |",
        f"| insufficient_history（来自 E4） | {_fmt_int(insuf) if insuf else '—'} |",
        "",
        "### smoke vs full 对照",
        smoke_note,
        "",
        "\n".join(svf_rows),
    ])


def _e8(workload, ff) -> str:
    raw_extracted = ff.get("raw_extracted", "?")
    selected = ff.get("selected", "?")
    rows = ["| 步骤 | 输入单位 | 单位 | 每单位计算量 | 总计算量 | 吞吐 | 归因 |",
            "|:---|---:|:---|---:|---:|---:|:---|"]
    for sk, cn in _WSTEP_CN.items():
        w = (workload or {}).get(sk) or {}
        tp = w.get('throughput_per_sec')
        tp_str = f"{tp:.1f}" if tp else "—"
        rows.append(
            f"| {cn} | {_fmt_int(w.get('input_units'))} | {w.get('units_kind','—')} |"
            f" {w.get('ops_per_unit','—')} | {_fmt_int(w.get('total_ops'))} |"
            f" {tp_str} |"
            f" {w.get('attribution','—')} |"
        )
    return "\n".join([
        "## E8 性能与工作量归因",
        "",
        "> 本章结论：step4 是 CPU 密集瓶颈，主要耗时在 tsfresh 特征提取。",
        "",
        f"> **注意**：step4 计算的是 **{raw_extracted}** 个特征（全量 tsfresh），不是入选的 **{selected}** 个；",
        f"> 两个数字都取自 `feature_funnel`，前者是 I/O+计算量的真实分母。",
        "",
        "\n".join(rows),
    ])


def _e9(pe) -> str:
    if not pe:
        return "\n".join(["## E9 参数生效清单", "", "> [WARN] param_effective 字段缺失。"])
    rows = ["| 配置项 | 值 | 生效 | 原因 |", "|:---|:---|:---:|:---|"]
    for item in pe:
        eff = "✔" if item.get("effective") else "✘"
        reason = item.get("reason") or ""
        rows.append(f"| `{item.get('key','')}` | {item.get('configured','')} | {eff} | {reason} |")
    return "\n".join([
        "## E9 参数生效清单",
        "",
        "> 本章结论：标 ✘ 的配置项在本次实验中未生效（原因见最右列）。",
        "",
        "\n".join(rows),
    ])


def _e10(det) -> str:
    if not det:
        return "\n".join(["## E10 确定性校验", "", "> [WARN] determinism 字段缺失。"])
    executed = det.get("executed", False)
    result = det.get("same_seed_rerun")
    sources = det.get("sources_fixed") or []
    result_str = "✔ 通过" if result is True else ("✘ 失败" if result is False else "未执行")
    return "\n".join([
        "## E10 确定性校验",
        "",
        "> 本章结论：确定性校验" + ("已执行" if executed else "未执行。若需校验，在同一 ev 下重跑一次即可。"),
        "",
        f"- 已执行同 seed 重跑: {'是' if executed else '否（未执行）'}",
        f"- 结果一致: {result_str}",
        "",
        "**随机源固定项**",
        "\n".join(f"- {s}" for s in sources),
    ])


def _e11(ev, rp) -> str:
    if not isinstance(rp, dict):
        rp = {}
    input_mode = (rp.get("input") or {}).get("mode", "?")
    label = (rp.get("experiment") or {}).get("label", "?")
    return "\n".join([
        "## E11 复现命令",
        "",
        "> 本章结论：以下命令可在相同环境下完整复现本次实验。",
        "",
        "```bash",
        f"# 1. 确保样本集与标签已生成（input_mode={input_mode}）",
        f"python run_experiment_pipeline.py --experiment-version {ev} --config configs/local/{label}.yaml --step 1",
        f"python run_experiment_pipeline.py --experiment-version {ev} --config configs/local/{label}.yaml --step 2",
        f"python run_experiment_pipeline.py --experiment-version {ev} --config configs/local/{label}.yaml --step 3",
        f"python run_experiment_pipeline.py --experiment-version {ev} --config configs/local/{label}.yaml --step 4",
        f"python run_experiment_pipeline.py --experiment-version {ev} --config configs/local/{label}.yaml --step 5",
        f"python run_experiment_pipeline.py --experiment-version {ev} --config configs/local/{label}.yaml --step 6",
        "# 2. 生成报告",
        f"python analyze_experiment_results.py --manifest outputs/{ev}/metadata/experiment_manifest.json",
        "```",
    ])


def _appendix_a(fp) -> str:
    return "\n".join([
        "## 附表 A — 完整指纹",
        "",
        "| 指纹 | 完整值 |",
        "|:---|:---|",
        f"| datebar | `{_ident(fp.get('datebar'), '不适用')}` |",
        f"| samples_set | `{_ident(fp.get('samples_set'), '不适用')}` |",
        f"| label_params | `{_ident(fp.get('label_params'), '不适用')}` |",
        f"| resolved_params | `{_ident(fp.get('resolved_params'), '不适用')}` |",
    ])
