"""report/reference.py — T3 reference page renderer (design §4.6).

Renders per-kernel-version reference page into data/reference/_kernel/<ver>/reference.md.
Content comes from fixed REFERENCE_SECTIONS constants (design §4.6 R1-R6).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]

# Design §4.6: reference sections are STATIC CONSTANTS (not from code or config).
REFERENCE_SECTIONS: Dict[str, str] = {
    "R1": """\
## R1 流水线语义

流水线由 step1–step6 六个步骤组成，每步读取上一步的产物，写入自己的输出目录。

| 步骤 | 输入 | 处理 | 输出 |
|:---|:---|:---|:---|
| step1 | 持仓文件 或 样本集+标签 | 对齐样本与标签，丢弃无标签行，记录覆盖率 | `data/processed/<ev>/holding_clean.parquet`，`datebar.parquet` |
| step2 | holding_clean.parquet, datebar.parquet | 对每条信号取最近 `lookback_days` 个非停牌交易日切片 | `data/slices/<ev>/` |
| step3 | slices/ | 对每条信号的切片计算 12 个技术指标（滚动窗口） | `data/ts_features/<ev>/` |
| step4 | ts_features/ | tsfresh 提取全量特征，零方差剔除，相关性去重，取前 N | `data/features/<ev>/selected_features*.txt`, `clip_percentiles.json` |
| step5 | features/, splits 参数 | 时间切分（全量按日期严格划分，冒烟随机），手动特征注入 | `data/splits/<ev>/train.parquet`, `val.parquet`, `test.parquet` |
| step6 | splits/ | LightGBM 回归训练，早停 | `models/<ev>/lgb_model.txt`, `outputs/<ev>/predictions.csv` |

> ⚠️ `holding_clean.parquet` 会被 step2 覆盖：step1 写出全部有标签样本（含历史不足的），step2 校验 60 日历史后重写为有效样本。看行数时请确认读的是哪一步之后的状态。详见 `docs/16_potential_risks.md` PR-001。
""",
    "R2": """\
## R2 指标定义

以下 12 个基础技术指标由 step3 计算，`profile=base` 时全部启用。

| 指标 | 公式（简化） | 参数（来自 resolved_params.indicators） |
|:---|:---|:---|
| ma_20 | `close` 的 20 日简单移动平均 | `ma_windows` |
| volatility_20 | `close` 的 20 日样本标准差 | `std_windows` |
| rsi_14 | Wilder RSI，`(avg_gain / (avg_gain + avg_loss))`，周期 14 | `rsi_period` |
| boll_upper | MA(20) + 2×STD(20)，后复权收盘 | `boll_period`, `boll_mult` |
| boll_std | STD(20)，单独存储供特征提取 | — |
| atr_14 | True Range 的 14 日 EMA（ATR） | `atr_period` |
| momentum_5 | `close[t] / close[t-5] − 1` | `momentum_period` |
| return_1d | `close[t] / close[t-1] − 1` | — |
| volume_ratio | `volume / MA(volume, 20)` | `volume_ma_period` |
| non_trading_ratio | 窗口内停牌天数占比（step5 手动特征，非 step3 指标） | — |
| (v3_enriched: +4) | 增加 RSI_7、return_3d、return_5d、MACD signal | 仅 `profile=v3_enriched` |

> 实际使用的参数值见 `resolved_params.yaml` 中 `indicators` 字段。
""",
    "R3": """\
## R3 特征工程规则

step4 执行以下流程：

1. **tsfresh 全量提取**：对每条信号的切片（`lookback_days` 行），按 `fc_profile`（compact/efficient）提取全量 tsfresh 特征，共约 324 个（取决于配置）。
2. **零方差剔除**：训练集内方差为 0 的特征全部丢弃（约 20 个）。
3. **相关性去重**：训练集中与其他特征 Spearman 相关系数 ≥ `corr_threshold`（默认 0.95）的特征，保留 gain 最大的一个，其余丢弃，直到候选数 ≤ `top_candidates`（默认 100）。
4. **前 N 截断**：按 LightGBM gain 降序取前 `max_features`（默认 50）个。
5. **一致性校验**：`selected + manual_added == model_input`，不等则报 `[WARN]`。

特征命名规则：`{indicator_name}__{tsfresh_kind}`，如 `atr_14__skewness`。
""",
    "R4": """\
## R4 切分规则

**全量模式（`run_mode=full`）**

- 按 `signal_date` 严格时间顺序切分，不允许时间交叉。
- 切分比例来自 `resolved_params.time_split`（默认约 60/16/16）。
- **buffer 规则**：train_end 与 val_start 之间必须有 ≥ `buffer_trading_days`（默认 60）个交易日间隔，同理 val_end 与 test_start；若不满足则 `buffer_check_status=violated` 并 exit 1。
- buffer 存在的原因：标签持有期（如 20 日）使训练集尾部的标签与验证集的信号发生时间重叠，buffer 隔离这部分泄漏。

**冒烟模式（`run_mode=smoke`）**

- 随机 70/15/15 切分（无时间隔离）。
- 只验证管道畅通性，不输出有效统计结论。
- 冒烟的 buffer 状态标记为 `smoke_not_applicable`。
""",
    "R5": """\
## R5 标签公式

以下公式基于 `data_prep/labels/` 中各标签的实现，非完整源码，仅供快速参考。

| 标签 | 入场 | 出场 | 公式 | 注意事项 |
|:---|:---|:---|:---|:---|
| `target_future_Nd` | `signal_date` 之后首个非停牌日 `open` | 第 N 个非停牌日 `close` | `close / open − 1` | 实际跨 N+1 个交易时段（N 个隔夜 + N+1 个日内）；入场为开盘、出场为收盘，价格类型不对称 |
| `target_holding_return` | 持仓记录原始 `entry_date` | 持仓记录原始 `exit_date` | 持仓记录的实际收益 | 与旧卖出规则耦合，覆盖率低，仅诊断用 |
| `target_trend_quality_20d` | 同 `target_future_20d` | 同 `target_future_20d` | `future_20d_return / max_drawdown`；回撤 ≤ 1e-6 时为空 | 量纲不是收益率；不同于 Sharpe |

> ⚠️ **口径说明**：`target_future_Nd` 的入场取「次一非停牌日 open」、出场取「第 N 个非停牌日 close」，
> 存在端点价格类型不对称（集合竞价 vs 收盘）的待议项（`docs/16_potential_risks.md` PR-011）；
> 本参考页只陈述当前口径，不宣称其为最优或标准做法。
""",
    "R6": """\
## R6 术语表

| 术语 | 定义 | 为什么重要 |
|:---|:---|:---|
| `target` | 经缩尾处理（clip P1/P99）的标签值，直接送入模型训练 | 模型学习的直接目标；极值被裁减，分布更稳定 |
| `target_raw` | 未经任何处理的原始标签值，仅用于报告统计 | 代表真实收益水平，不受缩尾影响，更适合评估实际表现 |
| 缩尾（clip） | 把低于 P1 的值截断到 P1、高于 P99 的值截断到 P99（训练集内计算） | 防止极端收益扭曲损失函数，导致模型过拟合尾部噪声 |
| 五分位分组 | 按 `y_pred` 从低到高平均分成 5 组（20% 每组），统计各组 `target_raw` 均值 | 验证"高预测值 → 高真实收益"的排序有效性，是核心验收指标 |
| 单调性（100 组） | 把样本按预测值分成 100 组后，观察各组均值曲线是否单调上行 | 比五分组更细粒度，揭示头尾噪声与中间段排序质量 |
| buffer 交易日 | 训练集与验证集之间的时间间隔（以交易日计），用于消除标签时间重叠 | 若 buffer < horizon_days，训练集末尾标签与验证集信号时间重叠，构成泄漏 |
| 覆盖率 | `non_null_rows / rows`，即非空标签行数占总样本行数比例 | 低覆盖率说明大量样本无法使用；`target_holding_return` 覆盖率约 55%，不宜作主标签 |
| 特征漏斗 | 从"原始提取"到"模型输入"的特征数变化路径 | 帮助理解哪个环节丢弃了最多特征，定位特征工程瓶颈 |
""",
}


def render_reference(summary: Dict[str, Any]) -> str:
    """Render reference.md from report_summary.json.

    Returns the full Markdown string.
    Also writes to data/reference/_kernel/<base_version>/reference.md.
    """
    rp = summary.get("resolved_params") or {}
    base_version = (rp.get("experiment") or {}).get("base_version", "") if isinstance(rp, dict) else ""
    if not base_version:
        base_version = "kernel_v11"

    ga = datetime.now().isoformat(timespec="seconds")
    content_lines = [
        f"# 参考页 — 内核版本 {base_version}",
        "",
        f"生成时间: {ga}",
        "",
        "> 本文件是 T3 内核级参考文档，只在内核代码变更时更新。",
        "> 内容来自规格常量（`report/reference.py::REFERENCE_SECTIONS`），不来自某次具体实验。",
        "",
    ]
    for _, section_body in sorted(REFERENCE_SECTIONS.items()):
        content_lines.append(section_body)
        content_lines.append("")

    content = "\n".join(content_lines)
    out_path = ROOT / f"data/reference/_kernel/{base_version}/reference.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return content
