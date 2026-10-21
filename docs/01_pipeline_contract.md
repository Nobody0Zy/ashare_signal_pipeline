# 知识面 01 · Pipeline 契约（跨谱系硬约束）

> 创建日期: 2026-09-30
> 定位: 任何谱系、任何新样本数据都必须遵守的**硬约束**。这些不是建议，是已被事故验证的边界。
> 来源: 历史实验总设计中的防泄漏铁律、工作区稳定规则，以及 `strategy_explore` 仓库内对相关机制的复盘分析。
> 使用方式: 开新实验前逐条核对；这些约束在新谱系中**默认继承**，除非有明确理由并记录。

---

## 1. 防泄漏铁律（最高优先级）

| # | 铁律 | 说明 |
| :---: | :--- | :--- |
| 1 | **绝不使用未来信息** | 所有特征只能基于 `signal_date` 当日及之前的数据；信号日完整行情视为已知 |
| 2 | **与策略信号逻辑一致** | 策略对停牌日是「直接剔除、连续拼接实际交易日」，特征构造必须同样处理，否则出现虚假波动率收窄 |
| 3 | **策略执行假设要显式声明** | 收盘价生成信号 + 次日开盘执行 → 信号日行情可用；若改为盘中实时信号，step2 筛选条件须改为 `idxd < signal_date` |
| 4 | **严格时间隔离** | 训练/验证/测试按时间先后严格分离；全量模式下相邻集合预留 **≥60 个交易日**缓冲带；**全量严禁任何随机分割** |
| 5 | **依赖标签的统计量只能在训练集内算** | 特征筛选、标签缩尾分位数等，绝对禁止验证/测试集参与 |
| 6 | **标签缩尾阈值也只在训练集内算** | 训练集 P1/P99 用于全局 `target` 的 clip，兼顾一致性与防泄漏 |
| 7 | **先冒烟后全量** | 冒烟仅验证管道畅通，**其指标不作为有效性参考**；全量严格按时序隔离 |
| 8 | **文件隔离** | 冒烟与全量必须使用不同输出文件名，避免冒烟覆盖全量 |

> **铁律 4 的判据**：仅「时间先后划分」不够，还要有缓冲带。缓冲区不足会让相邻集合的特征窗口在时间上重叠。

---

## 2. 可复现性约束

| # | 约束 | 说明 |
| :---: | :--- | :--- |
| 1 | 任何脚本开头设置 `random.seed(42)` 与 `np.random.seed(42)` | 全局固定 |
| 2 | LightGBM 固定 `seed=42`、`num_threads=1` | 禁用 `deterministic` 与 `n_estimators` |
| 3 | step2 / step4 分块**先按 `sample_id` 升序**再用 `numpy.array_split` 等分 | 保证分块确定性 |
| 4 | 多条验证路径必须使用**不同 state-file** | 共享中间产物的验证必须串行执行，不得并行 |

---

## 3. 版本化与命名约定

**`experiment_version` 结构**：`base_version` + `run_date` + `label` + `param_hash`，例如 `v0_20260715_baseline_a1b2c3d4`。

| 规则 | 说明 |
| :--- | :--- |
| `base_version` 用途 | **只用于定义实验谱系或重大思路迭代**；小开关、超参、手工特征调整**不得**改动它 |
| `label` | 描述实验目的或配置差异 |
| `param_hash` | 保证参数变动生成唯一输出目录 |
| 输出路径 | `outputs/{base_version}/{experiment_version}/`、`models/{base_version}/{experiment_version}/` |
| `build_experiment_version()` 返回值 | **已包含 `base_version/` 前缀**（如 `v5/v5_...`）；拼接时不要再手工补一层 |
| 预测文件 | 统一放版本目录根部：`predictions.csv` / `train_predictions.csv` / `val_predictions.csv`（冒烟加 `_smoke`） |
| 元数据 | 统一放 `metadata/`：`pipeline_state_{mode}.json`、`experiment_manifest.json`、`resolved_params.yaml`、`source_config.yaml`、`model_feature_schema.json` |
| 新实验注册 | 必须在 `configs/{base_version}/` 新增 YAML，并在 `configs/experiment_index.yaml` 注册 `name`/`config`/`description`/`expected_base_version`/`label` |
| 实验专属行为 | 必须通过**配置字段开关**控制，**严禁** `startswith("vX")` 之类版本字符串判断；开关先在 config 数据类声明，再在 YAML 赋值 |

---

## 4. Step 契约（每个 step 必须满足）

1. 执行前**打印**步骤编号、模式（冒烟/全量）与主要操作。
2. 执行中**捕获并报告异常**，不得静默跳过。
3. 执行后按既定验收标准**自检并逐项打印通过/失败**。
4. 必须更新 `metadata/` 下的 state 文件，记录 `status` / `output_files` / `checks_passed` / `timestamp` / `duration_hms`。
5. 前置步骤状态不是 `completed` 或 `checks_passed != true` 时，**后续步骤禁止启动**；已完成步骤可幂等跳过。
6. **同一步骤的不同分支实现，返回结构必须一致**——至少保持 `details` / `checks` / `output_files` 的键一致，避免下游按分支特判。

---

## 5. 实现硬约束（易踩坑的技术红线）

| # | 约束 | 原因 |
| :---: | :--- | :--- |
| 1 | **严禁** `reset_index(drop=True)` 修改或丢弃 `sample_id` | 唯一允许的转换是 `rename_axis("sample_id").reset_index()` |
| 2 | **严禁**在 step4 中直接按行数 `iloc` 切分 DataFrame | 必须先取 `unique(sample_id)`，再按 ID 组提取 |
| 3 | **严禁**在全量模式调用 `train_test_split` 或任何随机分割 | 破坏时间隔离 |
| 4 | `tsfresh` 调用必须**显式传入 `column_value`** | 确保 `time_step` 不被当作 value 列 |
| 5 | 结构性 NaN 必须在**长表阶段删除** `value` 为 NaN 的行 | 不得把 NaN 直接传给 `column_value` |
| 6 | 所有 `inf` / `-inf` 先替换为 `NaN` 并**打印替换数量** | `fillna=False` 只表示跳过补 0，**不表示**跳过 inf→NaN |
| 7 | `volume_ratio` 滚动均值为 0 的单元格先替换为 `np.nan` | 避免除零 |
| 8 | `non_trading_ratio` 必须校验 `calendar_span > 0` | 否则抛异常 |
| 9 | `tsfresh` 输出列名清洗为只含字母/数字/下划线；重名用计数后缀 | 保证列名唯一可解析 |
| 10 | 同一步骤多分支的返回结构键保持一致 | 见 Step 契约第 6 条 |

---

## 6. 数据保护边界

| 目录 | 规则 |
| :--- | :--- |
| `data/raw/` | **只读**，任何情况下不得修改或删除 |
| `outputs/` / `models/` / `data/processed` / `data/slices` 等 | **不要通过删除目录来「重置环境」**；优先用新 `state-file`、新 config 或新 `experiment_version` 隔离运行 |
| `outputs/experiment_registry.json` | 跟踪文件，运行会追加登记，应保留并按实验流程提交 |

原始文件的**具体路径由配置 `data.holding_file` / `data.datebar_file` 决定**（默认指向 `data/raw/`，
也可写绝对路径或指向外部目录）。只读约束跟随实际指向的文件，不因换路径而放宽。
就绪判定：`python tools/check_raw_data.py --config <配置>`（0=就绪，2=缺失，3=契约不符）。

---

## 7. 模式语义

| 模式 | 用法 | 语义 |
| :--- | :--- | :--- |
| 冒烟 | `--mode smoke --smoke 5000`；样本抽取用 `random.sample` 且种子固定 42 | 仅验证管道；step4 特征筛选可基于全部冒烟样本；step5 可按 70/15/15 随机分割并跳过缓冲带校验 |
| 全量 | `--mode full`，使用全部数据 | 严格遵守时间隔离；step4 筛选只能用训练集标签；step5 必须执行缓冲带硬校验 |

> 抽样规模应遵循 `configs/default.yaml` 中的 `smoke_size`，不要临时发明另一套目录或命名。

---

## 8. 与新谱系的关系

1. 以上约束在新谱系中**默认继承**。若新样本数据使其某条不再成立（例如缺少 `paused` 字段导致停牌语义变化），必须**显式记录变更理由**，而不是静默放宽。
2. `experiment_version` 的构成规则属于「稳定机制」，一般不改；真正会变的是样本数据、谱系入口与保留策略。
