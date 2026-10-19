# 知识面 03 · 需规避的问题（实测踩坑清单）

> 创建日期: 2026-09-30
> 定位: 第一轮实验（v0–v5）**实际踩过**的坑。每条都附证据来源，不是泛泛而谈的注意事项。
> 使用方式: 设计与评审阶段逐条核对，确认新设计不会重犯。
> 分级: 🔴 = 已造成错误结论或数据损失；🟡 = 造成返工或误导。

---

## 1. 数据与泄露

### 🔴 P-01 日历特征泄露

| 项 | 内容 |
| :--- | :--- |
| 现象 | 模型 gain 的 **69.8%** 来自日历特征，实际按时间「作弊」 |
| 证据 | v3：`v3_baseline` 的 `calendar_gain_share=0.6980`；去掉后归零，peak_group 57→97 |
| 影响 | 结论完全失真；模型在测试期表现不可信 |
| 规避 | ① 默认关闭 `include_date/year/month`；② 任何实验必须校验 `calendar_gain_share` 接近 0；③ `date` 若启用，必须是**月内日序号（1~31）**而非 8 位整数 |

### 🔴 P-02 缓冲带不足导致特征窗口重叠

| 项 | 内容 |
| :--- | :--- |
| 现象 | 仅「按时间划分」不足以隔离，相邻集合的特征窗口会在时间上重叠 |
| 证据 | [../01_pipeline_contract.md](01_pipeline_contract.md) §1 铁律 4 |
| 规避 | 全量模式相邻集合预留 **≥60 个交易日**；step5 必须执行缓冲带硬校验，不得跳过 |

---

## 2. 实验设计与归因（方法论类，最容易出错）

### 🔴 P-03 「单变量控制」标签未经核实

| 项 | 内容 |
| :--- | :--- |
| 现象 | 报告把 `lookback_days` 60d vs 100d 标注为「单变量控制」，**实际至少 3 个变量同时变化** |
| 证据 | `buffer_trading_days`、`val_start`、`test_start` 全都随之改变（另有 `max_holding_days`）；见 `strategy_explore` 仓库 `design/v4/discussion/v4_1_results/discussion_1_lookback_60_vs_100/analysis.md` §2.1 |
| 影响 | 效应量被错误归因 |
| 规避 | ① 标注「单变量」前**逐字段 diff 两个 config**；② 无法隔离的变量必须显式声明为「混淆」 |

### 🔴 P-04 混淆对比用于归因

| 项 | 内容 |
| :--- | :--- |
| 现象 | 用 A3 vs A1 得出「Manual 路线 corr 为负」，但两者**同时改了 corr 与 Step5** 两个变量 |
| 证据 | `strategy_explore` 仓库 `design/v4/discussion/v4_1_results/discussion_5_corr_mechanism/analysis.md` §2.2 |
| 影响 | 得出反向的错误结论；与五组干净对比（均值 +0.021 全正）矛盾 |
| 规避 | 每个对比对必须**只差一个变量**；已有干净对比矩阵可参考 [02_reference_conclusions.md](02_reference_conclusions.md) |

### 🔴 P-05 术语与机制混用（corr）

| 项 | 内容 |
| :--- | :--- |
| 现象 | 把 `selection_mode=correlation` 说成「按与 target 相关度筛噪声」 |
| 证据 | 代码事实：真正删特征的只有「特征间贪心去重」；`abs(corr(特征,target))` 仅用于**排序**（`top_candidates=None` 时为 tiebreaker） |
| 影响 | 机制解释错误，进而误判筛选价值 |
| 规避 | 描述特征筛选时，必须区分「排序依据」与「删除依据」 |

### 🟡 P-06 用低容量模型 judge 特征好坏

| 项 | 内容 |
| :--- | :--- |
| 现象 | 在 `num_leaves=31` 单一容量下测得的 gain，被用来推断「某些特征族是弱信号」 |
| 证据 | v51 → v52 闭环：full 档放大容量后 test Spearman +19.4%、死特征占比 −24.7pp |
| 影响 | **因果倒置**——把「容量错配」误判为「特征弱」 |
| 规避 | 涉及特征价值判断时，至少跑一个更高容量对照；或明确标注「仅在容量 X 下成立」 |

### 🟡 P-07 混用「排序」与「分层」两类指标

| 项 | 内容 |
| :--- | :--- |
| 现象 | 用 MonoScore / median uplift 推断排序能力，或用 test Spearman 推断分层能力 |
| 证据 | v52：aggressive 下两者系统性背离（MonoScore 前两名 test Spearman 仅 0.082~0.084） |
| 影响 | 结论自相矛盾 |
| 规避 | 因果判定以 **test Spearman** 为主；MonoScore / uplift 仅作**单调性与分层**辅助，不得混用 |

---

## 3. 特征与工具

### 🔴 P-08 量纲未处理导致跨样本不可比

| 项 | 内容 |
| :--- | :--- |
| 现象 | 原始 OHLC + 保留量纲计算器组合，产生大量跨样本不可比特征 |
| 证据 | v4_1：**B 组（11 个保留量纲计算器）在原始 OHLC 上产生 308 个跨样本不可比特征**，占 42.3% 特征预算，仅贡献 8.8% gain |
| 影响 | 维度膨胀、gain 稀释 |
| 规避 | 用 `fc_profile` 或输入归一化控制 B 组规模；值域画像（min/max/分位数/缺失率）作为常规诊断 |

### 🔴 P-09 `fft_coefficient` 越界静默返回 NaN

| 项 | 内容 |
| :--- | :--- |
| 现象 | `coeff >= N/2+1` 时**静默返回 NaN**（不报错），被填充为常量后触发零方差删除 |
| 证据 | v51 closure 硬结论 1（源码级 + 逐周期精确吻合 8656=8656） |
| 影响 | 大量特征被静默丢弃，且不易察觉 |
| 规避 | coeff 上限按 `N/2+1` 动态对齐；登记为待办（见 `strategy_explore` 仓库 `design/v5/design/v51_tsfresh/closure.md` §7 T1） |

### 🟡 P-10 参数化展开是「采样点」而非「独立信息」

| 项 | 内容 |
| :--- | :--- |
| 证据 | v51：`quantile` 家族内相关度高达 0.99 |
| 影响 | 高估特征信息量 |
| 规避 | 评估计算器扩展时，先做家族内共线诊断 |

### 🟡 P-11 默认计算器参数为通用长序列设计，缺金融语义

| 项 | 内容 |
| :--- | :--- |
| 证据 | v51：`quantile` 缺 0.5、`ratio_beyond_r_sigma` 冗余档、`number_peaks` 的 n=50 过度 |
| 规避 | 短序列（约 100 点）场景下不要直接沿用 `EfficientFCParameters()` 默认参数 |

### 🟡 P-12 并行低效但分块控内存有效

| 项 | 内容 |
| :--- | :--- |
| 证据 | v51：CPU 利用率仅 **17~20%**（`tsfresh_n_jobs=8` 基本无效）；分块控内存有效（样本 48× → 内存 4×） |
| 规避 | 不要指望靠加 `n_jobs` 提速；用分块控内存峰值 |

### 🟡 P-13 `corr + smoke` 分支未传 `fc_profile`

| 项 | 内容 |
| :--- | :--- |
| 证据 | `strategy_explore` 仓库 `design/v4/design/v4_2_expand_tsfresh/known_issues/smoke_corr_fc_profile_mismatch.md`：回退到全量 `EfficientFCParameters()`，实证 5,989 特征 |
| 规避 | 该分支须显式传入 profile（v51 已参数化修复）；新谱系中复核同类分支 |

---

## 4. 工程与执行（pipeline 侧）

### 🔴 P-14 step3 双代码路径共享输出文件

| 项 | 内容 |
| :--- | :--- |
| 现象 | `skip_indicators` 分支与旧逻辑写入**同一个** `ts_features_{lookback}d.parquet`，列结构完全不同 |
| 影响 | 同一 state-file 下先跑 pure 再跑非 pure → step3 幂等跳过 → step4 读到错误列结构 |
| 规避 | 不同分支用不同 state-file 隔离；或让输出文件名体现分支 |

### 🔴 P-15 多条验证路径并行争抢中间产物

| 项 | 内容 |
| :--- | :--- |
| 现象 | 两条验证路径输出目录不同，但共享 `data/processed/`、`data/slices/` 等中间目录 |
| 规避 | **必须串行执行**；每条路径用独立 state-file |

### 🟡 P-16 分支返回结构键不一致

| 项 | 内容 |
| :--- | :--- |
| 现象 | `selection_mode=none` 不产生 `selected_features.txt` 等文件，导致 `details`/`output_files` 键缺失 |
| 影响 | 下游 state 记录与后续 step 需按分支特判 |
| 规避 | 同一步骤多分支的 `details`/`checks`/`output_files` 键必须一致 |

### 🟡 P-17 默认值变更 ≠ 零行为变更

| 项 | 内容 |
| :--- | :--- |
| 现象 | 重构改了 dataclass 默认值（如 `lookback_days`），影响 `PipelineParams()` 但不影响 `from_yaml()` |
| 影响 | 用裸构造函数验证旧行为会得出错误结论 |
| 规避 | 验证旧行为必须走 **YAML 加载路径**；新旧行为用不同 state-file 分别验证 |

### 🟡 P-18 GBK / 编码坑

| 项 | 内容 |
| :--- | :--- |
| 现象 | ① PowerShell 内联 Python 中的中文标识符被转成 `??`；② `Set-Content -Encoding UTF8` 写 JSON 带 BOM，Python 读取报 `JSONDecodeError` |
| 规避 | ① 对 GBK 文件按列位置或 Unicode 转义处理，不要内联中文标识符；② JSON 用无 BOM UTF-8 |

---

## 5. 磁盘与生命周期

### 🔴 P-19 磁盘耗尽直接中断正式实验

| 项 | 内容 |
| :--- | :--- |
| 现象 | `v5_20260902_lgb_high_dim_full_3ae3c5aa` 的 step6 因 `No space left on device` 在写 `train_predictions.csv` 时失败 |
| 证据 | `strategy_explore` 仓库 `design/v5/discussion/closeout_map/01_v5_first/05_v5_open_issues.md` §1 |
| 规避 | 跑前评估磁盘；补容量模型 |

### 🔴 P-20 仅改 `lgb_profile` 仍重复落盘 step1~5

| 项 | 内容 |
| :--- | :--- |
| 证据 | `param_hash` 覆盖 step1~6 全部参数；full 档两组实验的 `selected_features.txt` 哈希完全一致、`train.parquet` 字节数完全一致 |
| 影响 | 只改模型参数也复制整套宽表 |
| 规避 | 机制解见 `strategy_explore` 仓库 `design/repo_cleanup/upstream_version_refactor_design.md`（引入 `upstream_version`） |

### 🔴 P-21 prediction CSV 体积远超分析所需

| 项 | 内容 |
| :--- | :--- |
| 现象 | step6 把 base columns + **完整 `feature_cols`** 一起写入 prediction CSV |
| 证据 | 单体 `train_predictions.csv` 达 **24.48GB**；而分析脚本只消费 `sample_id`/`signal_date`/`target`/`target_raw`/`y_pred` |
| 规避 | 改为最小列输出，或允许跳过 train/val 预测落盘 |

### 🔴 P-22 failed / aborted run 缺少生命周期回收

| 项 | 内容 |
| :--- | :--- |
| 现象 | aborted run 只在 `data/` 留大体积派生物、无 outputs；failed run 留残缺文件 |
| 证据 | `v5_20260903_lgb_high_dim_aggressive_full_1d1e9741`（data-only）、`v5_20260902_lgb_high_dim_full_3ae3c5aa`（failed） |
| 规避 | 明确定义 failed / aborted 的最小保留集与回收动作 |

---

## 6. 元教训（跨类别）

1. **「报告里的标签」必须可被代码/配置复核。** P-03、P-04、P-05 都是「描述与事实不符」——共同点是报告先给结论、后补核查。
2. **归因前先 diff 配置。** 任何「单变量」「干净对比」的声称，都应能一键列出两个 config 的字段差异。
3. **指标语义先定义、再使用。** P-07 源于两类指标被当成同一件事。
4. **容量与特征、排序与分层、筛选与去重**——这三组概念在第一轮中都被混淆过，新谱系中应作为固定检查项。
