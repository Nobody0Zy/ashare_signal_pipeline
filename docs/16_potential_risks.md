# 16 潜在风险登记（Potential Risks）

> 最后更新: 2026-11-13
> 适用范围: 整个内核模板（跨谱系、跨版本）
> 读者: 内核维护者 / 新谱系实施者 / 复核人
> 性质: **登记与提醒**，不是缺陷清单的替代品。已修复项不在此列（见 git log）。

本文件记录**已知但尚未修复**的工程与方法论风险。每条包含：现象、证据、影响、建议、目标版本。
新发现的跨谱系风险请追加到本文件，并在 `03_pitfalls.md` 中补一条一线排障视角的条目（两者受众不同）。

---

## 风险总表

| 编号 | 风险 | 严重度 | 影响面 | 建议修复版本 |
| :--- | :--- | :---: | :--- | :--- |
| PR-001 | `holding_clean.parquet` 被 step2 原地覆盖 | **高** | 可复现性、对照实验 | v1.2 |
| PR-002 | 报告文件名即契约（下游硬编码 4 处） | 中 | 报告演进、ensemble | v1.1.1 |
| PR-003 | samples 模式静默忽略 `include_signal_date_selection_score` | 中 | 实验可比性 | v1.2 |
| PR-004 | 标签窗口重叠未在显著性检验中校正 | 中 | 结论可信度 | 研究侧（不属内核） |
| PR-005 | 报告只呈现单一 `run_mode` | 低 | 排障效率 | v1.1.1 |
| PR-006 | `data/raw` 默认为空 + `configs/local` 不入库 | 低 | 新环境上手 | 文档已覆盖，保持 |

---

## PR-001 `holding_clean.parquet` 被 step2 原地覆盖

**严重度**：高（可复现性与可观测性）

### 现象

同一路径下的文件在不同步骤后代表不同语义：

| 时刻 | demo_new 行数 | 语义 |
| :--- | ---: | :--- |
| step1 完成后 | 385,252 | 全部有标签样本 |
| step2 完成后 | 378,088 | 通过 60 日历史校验的样本 |

### 证据

`pipeline/step2_slices.py`：

```python
holding = pd.read_parquet(paths.processed_dir / "holding_clean.parquet")   # L176 读
...
valid_holding = holding[~holding["sample_id"].isin(invalid_sample_ids)]   # L191 过滤
valid_holding.to_parquet(holding_path, index=False)                       # L201 写回同一路径
```

全量模式下 `holding_path` 即 `holding_clean.parquet`；仅冒烟模式写 `holding_clean_smoke.parquet`。

### 影响场景

| 场景 | 后果 | 严重度 |
| :--- | :--- | :---: |
| 同参数重跑 step2 | 幂等，无影响 | 无害 |
| 提高 `window.lookback_days`（60→100） | 已丢样本本就不足 60 天，仍会被丢 | 无害 |
| **降低 `lookback_days`（60→40）** | 60 天被丢的样本中部分 40 天够用，但文件里已不存在 → **无法复现** | **高** |
| 不跑 step1 直接跑 step2（对照实验） | 输入已被上次 step2 改过，前提不成立 | **高** |
| step1 完成、step2 中断 | 文件仍是全量，state 已记录 step2 开始 → 文件与状态不一致 | 中 |
| 并发执行两次 step2 | 互相覆盖（AGENTS 已要求串行，但根因在此） | 中 |
| 人工核对行数 | 易误判为"step1 就丢了 7,164" | 中 |

### 定性

**不是正确性 bug**（正常顺序执行结果正确），而是 **in-place mutation of an input artifact**：把输入当输出原地修改，导致"文件代表什么"依赖执行历史。

### 建议

| 方案 | 做法 | 改动面 | 评价 |
| :--- | :--- | :--- | :--- |
| **A（推荐）** | step2 写 `holding_valid.parquet`；step3/4/5 改读它；`holding_clean.parquet` 永久保持 step1 语义 | step2 + 3/4/5 读取路径 + state 字段 | 语义最清晰，改动可控 |
| B | 先双写两文件，下游切到新名，v1.3 移除旧写 | A + 兼容逻辑 | 迁移安全，过渡期有双份数据 |
| C | 不落盘有效集，下游按 slice 的 `sample_id` 集合在内存 join | 3/4/5 各加一次 join | 无冗余文件，每步多一次开销 |

实施时需同步更新：`docs/01_pipeline_contract.md` 的产物表、`docs/07_step_acceptance_criteria.md` 的 step2 验收、`tools/verify_v11.py` 的对应断言。

### 过渡期对策（当前版本）

1. 报告/文档中提到"样本数"必须注明取自哪一步（step1 `sample_count` vs step2 `valid_samples`）；
2. engineering 报告 E6 同时列出两次行数并标注覆盖；
3. 需要"step1 语义"时，重新执行 step1（成本约 12–16 秒）而不是复用文件。

---

## PR-002 报告文件名即契约

**严重度**：中

**现象**：`pipeline/ensemble/bagging.py` 有 4 处硬编码报告文件名（写 2 处、读 2 处），导致报告结构无法演进——任何拆分都会打断 ensemble。

**影响**：报告只能保持"一份大杂烩"的形状，工程细节与实验结论被迫混写。

**建议**：改为读取 `analysis/reports/manifest.json`（产物清单），按 `audience` 选取；旧名保留一个版本的弃用 alias。详见 `version_design/design/refactor/v1.1.1_report/design.md` §5。

**目标版本**：v1.1.1。

---

## PR-003 samples 模式静默忽略手工特征开关

**严重度**：中（影响实验可比性）

**现象**：配置 `manual_features.include_signal_date_selection_score: true` 在 `input.mode=samples` 下不可用。当前实现只打印一行提示，配置值仍为 `true`，容易让人误以为该特征已进入模型。

**证据**：demo_new 的 step5 `manual_features` 仅 `["non_trading_ratio"]`；模型特征数 51（50 tsfresh + 1 手工），比旧路径少 1 个。

**影响**：新旧路径的模型输入不一致，指标不可直接横向比较；报告若不写明，读者会误判为"同一特征集下新路径更差"。

**建议**：
1. 短期：报告中"参数生效清单"显式标注（v1.1.1 已纳入 engineering E10）；
2. 长期：配置校验阶段对"模式不适用"的开关直接报错或自动改写为 `false`，而不是仅告警。

**目标版本**：v1.2。

---

## PR-004 标签窗口重叠未在显著性检验中校正

**严重度**：中（方法论，非代码缺陷）

**现象**：20 日固定持有 + 事件采样 → 同一股票相邻信号的标签窗口大量重叠。demo_new 测试集中 **43.25%** 的样本与前一条同股票信号间隔 ≤ 20 个交易日。

**影响**：Spearman/Pearson 等指标的置信区间被显著高估；直接按 5 万样本做检验会得到过窄的区间和过小的 p 值。

**建议**：
1. 报告必须写出重叠比例（v1.1.1 已纳入 experiment X9.2）；
2. 做显著性检验时使用按股票或时间块的 block bootstrap；
3. 或采用去重采样（同股票 20 日内只取首条）做敏感性分析。

**目标版本**：研究侧规范；报告呈现属 v1.1.1。

---

## PR-005 报告只呈现单一 `run_mode`

**严重度**：低（排障效率）

**现象**：同一 `experiment_version` 目录下可同时存在 `pipeline_state_*_smoke.json` 与 `*_full.json`，但当前报告只按 manifest 的 `latest_run_mode` 渲染，smoke 运行被忽略。

**影响**：无法判断"小样本结论是否可信"，也无法定位 smoke 与 full 的偏差来源。

**建议**：事实表增加 `runs: {smoke, full}` 与 `smoke_vs_full` 对照，engineering 报告输出 Δ 表。

**目标版本**：v1.1.1。

---

## PR-006 `data/raw` 默认为空、`configs/local` 不入库

**严重度**：低（属预期设计）

**现象**：仓库不含真实行情文件；本地配置带机器绝对路径，被 gitignore。

**影响**：新环境首次运行必然失败在 step1，容易被误判为代码问题。

**现状**：`AGENTS.md`、`docs/12_clone_and_start.md` 已明确"无数据也能先 scaffold"；`tools/check_raw_data.py` 提供 0/2/3 三态判定。

**建议**：保持现状；新增谱系时先跑 `check_raw_data.py`。

---

## 维护约定

1. 新增风险 → 追加到"风险总表"并在下方补详细条目；
2. 风险修复后 → 从本文件删除，并在 commit message 中注明 `closes PR-xxx`；
3. 每条风险必须带**证据**（文件:行号 或实测数字），不接受"感觉有问题"；
4. 跨谱系通用的教训同时补入 `03_pitfalls.md`（一线排障）与 `04_engineering_lessons.md`（设计教训）。
