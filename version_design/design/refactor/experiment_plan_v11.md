# v1.1 实验计划（阶段 0 对照）

> 状态: 待 v1.1.0 重构完成后启动
> 创建日期: 2026-11-05
> 父文档: `experiment_design.md`（重构设计，本文不重复其契约与不变量）
> 定位: **研究文档，不是重构交付物**。不阻塞 v1.1.0 发布。
> 执行者: 人工主导 + agent 辅助（与重构的"agent 独立落地"不同）

---

## 0. 为什么单独成文

重构（R）与实验（E）是两类不同的工作：

| | 重构 R | 实验 E |
| :--- | :--- | :--- |
| 交付物 | 代码能跑、旧路径不变 | 结论（哪组更好） |
| 完成标准 | **可机械判定**（数字相等） | **需人工判断**（效应是否可信） |
| 失败模式 | 报错、回归失败 | 结论错误、归因混淆 |
| 前置 | 无 | v1.1.0 已发布 |

混在一起会让"重构完成"这个里程碑被研究结论绑架。因此本文独立，**v1.1.0 的发布 Gate 只依赖 `experiment_design.md` 的 R1 回归**，与本文件无关。

---

## 1. 一组容易混淆的锚点

| 锚点 | 归属 | 目的 | 何时做 |
| :--- | :--- | :--- | :--- |
| **R1 行为回归** | **重构**（`experiment_design.md` §7 M4） | 证明重构没有改变旧路径 | v1.1.0 发布前，必须通过 |
| **R2 归因对照** | **实验**（本文） | 保证四组差异只来自采样与标签 | v1.1.0 发布后 |

R1 用**旧配置原值**（`include_signal_date_selection_score=true` 等）；R2 四组统一 `include_signal_date_selection_score=false`。两者数值本来就不同，**不要拿 R1 的数字去和四组比**。

---

## 2. 2×2 因子设计

| 组 | 采样（X） | 标签（Y） | 配置 | 回答什么 |
| :---: | :--- | :--- | :--- | :--- |
| **0A** | 旧 holding 样本 | `holding_return` | `input.mode=holding_file` | 旧结论参照 |
| **0B** | 旧 holding 样本（`legacy_holding`） | `target_future_20d` | `samples` + `label_file` | 标签外生化的效应（采样固定） |
| **0C** | 布林带突破 | `target_future_20d` | `samples` + `label_file` | 采样换事件驱动的效应（标签固定） |
| **0D** | 布林带突破 | `holding_return` | `samples` + `holding_return` | 旧标签在新样本上是否成立（**诊断，不作主结论**） |

**0B 的样本集必须与 0A 完全相同**（用 `(idxs, signal_date)` 集合比较，不用 `sample_id`——后者在两模式下本来就不同）。

**为什么必须四组**：只比 0A vs 0C 时，采样与标签同时变，任何差异都无法归因。0B 是唯一能把两者拆开的组。

### 2.1 关于 `signal_date_selection_score`

该特征恒等于 `close/open − 1`（证据见 `design_memory.md` §3.4），但在 v1.1 中被删除（架构理由，见 `experiment_design.md` §0.5）。

后果：R2 四组统一不含它，因此四组特征集一致、可比。**副产品**：R1 保留它、R2 不含，`0A(R1) − 0A(R2)` 恰好量化「这个特征值多少」——两者本来都要跑，零额外成本。报告里作为附注一行，不作为主结论。

### 2.2 0D 的两个实现约束

1. `target.source=holding_return` 在 `samples` 模式下合法；其标签名固定为 `target_holding_return`（规范见 `experiment_design.md` §5.3 V13），由 `labels/holding_return.py` 从 **`data.holding_file`（raw）** 读取，**不得**读 `processed/holding_clean.parquet`（该文件在 samples 模式不存在，且在 full 模式会被 step2 覆写）。
2. `_index.json` 必须记录 `holding_fingerprint`，step1 校对其与当前 raw holding 一致，否则重建。
3. v1.1 **不支持隐式构建**（`on_missing_*=error`）：开跑 0B/0D 前必须先执行 `tools/build_labels.py` 生成 `target_future_{h}d` / `target_holding_return`；输出对不上时先修 CLI 调用，不要在实验里补丁。

---

## 3. 规模问题（前置检查）

| 事实 | 数值 |
| :--- | :--- |
| 0A 样本量 | 240,257 |
| 0C/0D 预期样本量 | ≈6.9×10⁵（外推，2.9 倍） |
| 标签分布 | `holding_return` 中位 −3.0%；`future_20d` 未知 |

因此 **0A vs 0C 的原始 spread 与 MAE 不可比**，必须用 §4 的 `spread_norm` / rank IC + 规模匹配。

**开工前必须先定 `max_samples`**（决策规则见 `experiment_design.md` §9.3，抽样算法见 §4.2；本文不重复定义）：

1. `fc_profile=compact` → `max_samples: null`，全量；
2. 要上 `medium` → 先在 20k 子样本上实测 step4 耗时与峰值内存，外推后再定 cap；
3. 若设了 cap，必须按年等距抽样、在 manifest 记录抽样率，并在报告中声明。

---

## 4. 指标口径

| 指标 | 定义 | 用途 |
| :--- | :--- | :--- |
| rank IC | test 集 `spearman(pred, target)` | 主指标（跨标签可比） |
| `spread_norm` | `(mean(Q5 target) − mean(Q1 target)) / std(target_test)` | 主指标（消除量纲） |
| `spread_raw` | `mean(Q5) − mean(Q1)` | 辅助，**禁止跨标签比较** |
| 单调性 | 5 组均值序列的 spearman | 辅助 |
| MAE | 同标签内可比 | 辅助 |
| 覆盖率 / 重叠率 | 标签非空比例；`(idxs, signal_date)` Jaccard | 必报 |

**禁止混用**：`holding_return`（中位 −3.0%）与 `future_20d` 的 `spread_raw`、MAE 不可直接比较。

---

## 5. 控制组

| 控制 | 做法 | 通过标准 |
| :--- | :--- | :--- |
| L0 随机排序 | test 集预测打乱 100 次，同分组口径算 `spread_norm` | 观测 \|t\| > 2 且效应量 ≥ 0.05σ |
| 打乱标签 | 训练集内打乱 `target` 重训 | test spread 落入 L0 的 95% 区间 |
| 规模匹配 | 按 `signal_date` 分层下采样到同量级，或按月分块 bootstrap 95% CI | 匹配后结论方向不变 |
| 时间稳定性 | 分年度 rank IC | 不出现单年驱动 |

---

## 6. 标签守恒恒等式（每组必查）

```text
samples_rows(samples.parquet)
  − dropped_no_target(step1)
  − dropped_samples(step2)          # missing_signal_date / paused_signal_date / insufficient_history
  − invalid_targets_dropped(step4)
  == 特征表行数(step4 output)
```

不平即说明有 NaN 标签被 step5 的 `fillna` 填成 0（`experiment_design.md` §4.9），该组结论作废。

> 本式的权威定义在 `experiment_design.md` §4.9；此处若与正文不一致，以正文为准。

---

## 7. 报告必须包含

1. 四组样本量 / 时间分布 / 重叠率 / 标签覆盖率；
2. 每组 rank IC / `spread_norm` / 单调性 / 分年 IC；
3. 0B−0A（标签效应）与 0C−0B（采样效应）的差分与置信区间；
4. 特征集差异（若有）；
5. 本轮尝试过的标签 / 参数清单（多重比较登记）；
6. `0A(R1) − 0A(R2)` 附注一行。

## 8. 不可比项（写在报告首页）

- 冒烟结果的任何指标；
- 不同标签的原始 `spread_raw` / MAE；
- 0D 的覆盖率缺失部分（选择偏差，非随机缺失）；
- `max_samples` 抽样后的总体外推（只能在报告内讨论抽样分布）。

---

## 9. 完成标准

| 项 | 标准 |
| :--- | :--- |
| 四组均跑通 | step1–6 完整，无 failed |
| 守恒恒等式 | 四组全部成立 |
| 控制组 | L0 与打乱标签结果齐全 |
| 规模匹配 | 已做，或已说明为何不需要 |
| 报告 | §7 的六项齐全 |

任一不满足 → 不出结论。

---

## 文档结束

**执行提示**

1. 本文的前提是 v1.1.0 已发布、`experiment_design.md` 的 M0–M5 全部验收通过。
2. 本文的工作**不影响**内核代码；若发现必须改代码才能实验，说明重构设计有遗漏，回到 `experiment_design.md` 修订，而不是在实验里打补丁。
3. 结论写入 `outputs/<experiment_version>/`，不要写回设计文档。
