# 11_baseline_fixture_guide

## 为什么需要基线
重构、迁移、模板抽取或性能修补之后，最危险的问题不是“代码不能跑”，而是“代码能跑，但行为悄悄变了”。基线 fixture 的作用，就是把关键事实固定下来，后续任何改动都能逐步对拍，而不是凭感觉判断“应该没变”。

## 什么时候建立基线
在同一份样本数据上，首次完成一轮可信的 full 实验后，就应该立刻建立基线。不要等到多轮改动之后再回头补，因为那时很难还原当时的真实口径。

## 建立基线时要记录什么

### Step 1

- `sample_count`
- `signal_date_min`
- `signal_date_max`
- `datebar_rows`

### Step 2

- `valid_samples`
- `dropped_samples`

### Step 4

- `columns`
- `selected_features`

### Step 5

- `train` / `val` / `test` 行数

### Step 6

- `best_iteration`
- `mae`
- `spearman`

## 基线文档放哪里

主库基线文档统一落位在：
`design/<base_version>/baseline_fixture.md`

如果某个版本迭代需要额外说明对拍背景，可以在对应的 `design/<base_version>/<version_subdir>/CONTEXT.md` 中补充引用关系，但主库基线仍保持在样本族级别，避免重复。

## 如何做对拍

1. 新建一次性的验证 `state-file`，不要复用既有运行状态。
2. 按 step1~step6 逐步执行。
3. 每一步都读取新的 `metadata/pipeline_state_<mode>_*.json`。
4. 把关键字段与基线 fixture 逐项比对。
5. 任一不一致，都必须给出原因，而不是直接接受偏差。

推荐对拍顺序：

- 先比 step1 / step2 的样本与时间范围
- 再比 step4 的列规模与入选特征
- 最后比 step5 / step6 的切分与评估摘要

## 解释差异时要问什么

| 问题 | 目的 |
| :--- | :--- |
| 样本数据是否完全相同 | 排除输入差异 |
| 配置是否通过 YAML 加载 | 排除默认值漂移 |
| state-file 是否全新 | 排除旧中间产物污染 |
| 是否共享了中间目录 | 排除并行验证争抢 |
| 指标语义是否一致 | 排除“同名不同义” |

## 适用范围
基线只在**同一份样本数据**内有效；如果样本变了，就必须重新建立基线。样本族升级到新的原始数据版本时，不应要求它复现旧样本的具体数值。

## 不应记录什么
以下内容不应作为可迁移基线硬编码到模板里：

- 某一轮历史实验的具体收益水平
- 只对单一策略成立的阈值
- 不能从 state 或工件直接复核的口头结论

## 最小模板

建立基线时，至少应让 `baseline_fixture.md` 包含：

1. 数据版本说明
2. canonical config 路径
3. 对应 run_id
4. step1~step6 的关键字段表
5. 后续对拍时的命令模板与 state-file 命名方式

这样，未来无论是代码重构还是仓库迁移，都能有一个稳定、可复核、可重复执行的参照面。