# 07_step_acceptance_criteria

## 总则

每一步执行后都必须立刻自检并打印 `checks_passed`。任一 check 失败，则该步状态记为 `failed`，后续步骤禁止启动；只有当前步已完成且 `checks_passed=true`，后续步骤才允许继续。

本文只定义检查项名称与语义，不写死样本数、特征数、lookback 天数。具体数值一律以当前 `config` 与对应实验的 `metadata/pipeline_state_<mode>_*.json` 为准。

## Step 1

| Check | 语义 |
| :--- | :--- |
| `holding_exists` | 清洗后的持仓表已落盘，且路径可读 |
| `holding_columns` | 持仓表包含最小必需字段，如 `sample_id`、`signal_date`、`target` |
| `holding_target_non_null` | 监督目标列非空，不能把空标签带入后续步骤 |
| `datebar_exists` | 标准化后的日线表已落盘，且路径可读 |
| `datebar_columns` | 日线表列集合符合约定，列名已统一 |
| `datebar_sorted` | 日线表按 `(idxs, idxd)` 正确排序 |

## Step 2

| Check | 语义 |
| :--- | :--- |
| `slices_exists` | 切片结果已生成并可读 |
| `non_trading_exists` | `non_trading_ratio` 衍生结果已生成并可读 |
| `holding_exists` | 过滤后的持仓表已同步更新 |
| `slice_columns` | 切片列结构完整，含 `sample_id`、`time_step` 与行情列 |
| `exactly_60_rows_per_sample` | 每个样本的切片行数与配置要求一致 |
| `slice_no_missing` | 切片内关键数值列不存在结构性缺失 |
| `non_trading_range` | `non_trading_ratio` 位于合法区间 |
| `holding_matches_valid_samples` | 保留下来的持仓样本与有效切片样本严格一致 |

> `exactly_60_rows_per_sample` 是历史命名遗留：其真实语义是「每个样本恰为 `lookback_days` 行」，
> 不等于固定 60 行。v5 实验使用 `lookback_days=100`，该 check 依然为 true。

## Step 3

| Check | 语义 |
| :--- | :--- |
| `features_exists` | 手工指标结果已落盘并可读 |
| `features_columns` | 结果列集合符合当前 profile 的预期 |
| `no_inf` | 手工指标结果已完成 `inf -> NaN` 处理，不残留无穷值 |

## Step 4

| Check | 语义 |
| :--- | :--- |
| `feature_file_exists` | 提取后的宽表特征文件已生成 |
| `selected_exists` | 被筛选的特征清单已生成 |
| `row_count_matches` | 特征矩阵行数与有效样本数一致 |
| `unique_columns` | 特征列名唯一，无冲突 |
| `clean_column_names` | 特征列名已清洗为只含字母、数字、下划线 |
| `no_nan_inf` | 进入 step5 前的特征矩阵不残留 `NaN` / `inf` |
| `selected_count_valid` | 被保留的特征数满足当前模式与配置的约束 |

Step 4 的检查重点不是“特征越多越好”，而是：

1. 样本对齐是否正确；
2. 标签相关统计是否仅使用训练集；
3. 宽表是否已经满足后续建模要求。

## Step 5

| Check | 语义 |
| :--- | :--- |
| `split_files_exist` | train / val / test 三个切分结果都已生成 |
| `columns_aligned` | 三个数据集的列集合与列顺序完全一致 |
| `has_target_raw` | 输出同时保留 `target` 与 `target_raw` |
| `no_nan_inf` | 三个切分文件都不残留 `NaN` / `inf` |
| `smoke_ratio_reasonable` | 冒烟模式下的切分比例与预期近似一致 |
| `buffer_skipped` | 冒烟模式允许跳过缓冲带，全量模式则必须显式校验 |

Step 5 还应额外确认：

- 全量模式下，时间切分严格单向，不允许重叠。
- 缓冲带样本已被正确排除。
- 标签缩尾阈值来自训练集，而不是全量样本。

## Step 6

| Check | 语义 |
| :--- | :--- |
| `model_exists` | 模型文件已成功生成 |
| `train_prediction_exists` | 训练集预测结果已生成 |
| `val_prediction_exists` | 验证集预测结果已生成 |
| `test_prediction_exists` | 测试集预测结果已生成 |
| `test_prediction_rows_match` | 测试集预测行数与测试集样本行数一致 |
| `train_prediction_rows_match` | 训练集预测行数与训练集样本行数一致 |
| `val_prediction_rows_match` | 验证集预测行数与验证集样本行数一致 |
| `feature_schema_exists` | 模型特征清单已写入 metadata |
| `feature_count_positive` | 实际入模特征数大于 0 |

Step 6 的结果检查既要看工件是否存在，也要看工件是否可用于后续分析：

- 预测文件至少应保留 `sample_id`、`signal_date`、`target`、`target_raw`、`y_pred`
- 模型文件与特征清单要能支持复现
- state 文件中的评估摘要要与实际工件一致

## 通用判定原则

1. check 名称必须稳定，避免不同分支使用不同键名表达同一语义。
2. 同一步骤的不同实现分支，`details`、`checks`、`output_files` 的返回结构必须一致。
3. 失败时必须显式记录失败项与原因，不能静默跳过。
4. 若某项 check 需要具体阈值，应由当前配置或 state 元数据提供，不要在代码或文档中硬编码历史数字。

## 如何读取 state 中的 check

1. 先看当前 step 的 `status`，确认是否已经完成。
2. 再看 `checks_passed`，判断是否允许进入下一步。
3. 然后逐项展开 `checks`，定位是哪一个约束没有通过。
4. 最后结合 `details` 与 `output_files`，确认失败是数据问题、路径问题还是逻辑问题。

这种读取顺序能避免只看到“失败”而不知道失败发生在哪个层次。

## 常见失败处置

| 场景 | 处理方式 |
| :--- | :--- |
| 输入文件缺失 | 回到 step1 前补齐原始数据，不要继续后续步骤 |
| 样本行数不一致 | 回查切片逻辑与 `sample_id` 对齐过程 |
| 特征列名不干净 | 回查 step4 列名清洗与重名去重逻辑 |
| train / val / test 列不一致 | 回查 step5 的列排序与拼接顺序 |
| 预测行数不匹配 | 回查 step6 的数据对齐与输出写回逻辑 |

> 本文不写死样本数、特征数、lookback 天数。所有阈值以当前 `config` 实际值为准；
> 需要具体数字时，读取该实验的 `metadata/pipeline_state_<mode>_*.json`。