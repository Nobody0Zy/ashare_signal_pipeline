# 16 潜在风险登记（Potential Risks）

> 最后更新: 2026-11-19
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
| PR-007 | 样本可构造性契约缺失：`min_history_days`（symbol 级/全历史）与 `lookback_days`（样本级/trailing）语义错位；`samples_id` 未绑定 `lookback_days` | 中 | 数据契约、实验口径、可复现性 | v1.2（**待讨论后定稿**） |

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

## PR-007 样本可构造性契约缺失（**待讨论，尚未定稿**）

**严重度**：中（不影响正确性与防泄漏，影响数据契约、实验口径与可复现性）
**登记状态**：仅作**事实记录 + 待讨论方向**，本条目**不对修复方案下结论**。后续重构前需先完成一次专项讨论。

### 现象

`data/prepared/<samples_id>/samples.parquet` 的**生成侧**用 symbol 级的全历史累计天数做准入，
**消费侧**（step2）用样本级的 trailing 天数做准入。两层各自正确，但没有任何一层对
「这条样本最终能否构造出 `lookback_days` 根特征窗口」负责，于是样本会"合法"地流到特征层才被静默剔除。

同一个常量 `60` 在三处出现，语义各不相同：

| 位置 | 参数 | 语义 | 粒度 |
| :--- | :--- | :--- | :--- |
| `data_prep/universe.py`（`sym_counts = non_paused.groupby("idxs").size()`） | `universe.min_history_days` | 该 symbol 在**整段 datebar** 内的非停牌天数总和 | symbol 级 / 全局 |
| `pipeline/step2_slices.py`（`valid_cut = searchsorted(valid_positions, ...)`） | `window.lookback_days` | 信号日**往回**（含当日）的非停牌 bar 数 | 样本级 / 时点 |
| `data_prep/samples/events/bollinger.py`（`n < ma_period + 1`） | 隐含 21 | 该 symbol 总 bar 数够不够算一次布林带 | symbol 级 / 全局 |

### 证据（demo_new，2026-11-12 全量运行）

1. **规模**：step1 `sample_count=385,252` → step2 `valid_samples=378,088`，
   `dropped_samples=7,164`，且 `invalid_reasons` 中 `insufficient_history=7,164`，
   其余三类（`unknown_symbol` / `date_not_in_symbol_range` / `paused_signal_date`）全为 0。
   数据源：`outputs/kernel_v11/check/kernel_v11_20261102_demo_new_6e499192/metadata/pipeline_state_demo_new_full.json`。
2. **构成拆分**（按 step2 同一逻辑复算，结果精确等于 7,164）：
   - A 类 **6,904** 条：`bars_upto < 60`（实测 21–59，中位数 41）——上市初期或数据左边界内的信号；
   - B 类 **260** 条：`bars_upto >= 60` 但非停牌不足 60（如 `sh600003` 在 20070405 有 61 根 bar、仅 39 根非停牌）。
3. **时间分布极不均匀**：2007 年 13,870 条中丢 3,316（**23.9%**），2008–2024 各年 0.1%–6.4%。
   2007 年占比高是数据左边界效应（datebar 自 20070104 起）。
4. **现有 V6 校验是必要非充分条件**：`pipeline/config.py` 要求
   `data_prep.universe.min_history_days >= window.lookback_days`，但该校验仍是**全局累计**语义。
   实测把该参数提到 100，只多排除 9 只 symbol / 14 条样本，重跑后 step2 **仍会丢弃约 15,342 条**。
5. **`samples_id` 未绑定 `lookback_days`**：哈希输入只含
   `method / params / universe{exclude_prefixes, min_history_days} / date_range / min_history_days / max_samples`
   （`data_prep/build_samples.py`），`window.lookback_days` 不在其中。
   实测：`min_history_days=60 → 080c9262 → bollinger_breakout_080c9262_294b0504`；
   `min_history_days=100 → 7951cc13 → bollinger_breakout_7951cc13_294b0504`。
   即：样本产物的身份只承诺"信号长什么样"，不承诺"特征窗口能否拼出来"。

### 与 PR-001 的关系

PR-001 记录的是**症状**（`holding_clean.parquet` 被原地覆写，文件语义随执行步骤漂移），
本条记录的是**病因**（准入契约错位 + 产物身份未绑定窗口参数）。
PR-001 中「`lookback_days` 60→40 无法复现」的场景，其根因即本条第 5 点。两者需一并考虑，不应重复登记。

### 影响面（按严重度）

| 影响 | 说明 | 严重度 |
| :--- | :--- | :---: |
| 样本损失在时间上不均匀 | 不是"随机缺 2%"，而是最早一段系统性缺约 1/4 | 中 |
| 三个分母并存 | 393,881（samples）/ 385,252（step1 有标签）/ 378,088（step2 有效），报告口径易冲突 | 中 |
| 可复现性 | 中间态被覆写抹掉（见 PR-001） | 高（工程） |
| 结论合法性 | **不影响**。step2 只用信号日及之前的数据计数，是纯事后剔除，不引入未来信息 | 无 |
| 模型指标 | 影响很小，不足以解释任何指标差异 | 低 |

### 待讨论的开放问题（**重构前需逐条定夺**）

1. 「可构造性」应当由谁保证：`build_samples` 生成时前置剔除 / step1 负责剔除并记录 / 保持现状仅靠 step2 事后剔除？
2. `samples_id` 是否应纳入 `lookback_days`（即窗口宽度是否属于样本身份的一部分）？
3. V6 校验是否应改为 per-sample trailing 语义，还是彻底移除、改由第 1 项保证？
4. 若样本集随之变化，既有实验的可比性与谱系划分如何处理？
5. 改变 `lookback_days` 时，切分日期（`val_start` / `test_start`）与缓冲带排除区的连带影响如何显式告知？

### 附注：`filter_universe` 的点时间性（低优先级）

`filter_universe` 用 2007–2025 的**全历史累计**判断 symbol 是否入池，严格讲使用了未来信息。
实测影响可忽略：6,090 只中仅排除 33 只（10 只长期停牌/退市代码，非停牌 ≤ 4 根；23 只 2024 年之后上市的新股）。
性质上与 `docs/01_pipeline_contract.md` 铁律 1 的字面要求不一致，但优先级低于本条第 1–5 项。

---

## 维护约定

1. 新增风险 → 追加到"风险总表"并在下方补详细条目；
2. 风险修复后 → 从本文件删除，并在 commit message 中注明 `closes PR-xxx`；
3. 每条风险必须带**证据**（文件:行号 或实测数字），不接受"感觉有问题"；
4. 跨谱系通用的教训同时补入 `03_pitfalls.md`（一线排障）与 `04_engineering_lessons.md`（设计教训）。
