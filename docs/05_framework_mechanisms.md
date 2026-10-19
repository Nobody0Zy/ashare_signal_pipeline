# 知识面 05 · 可复用框架机制与参数档位

> 创建日期: 2026-10-09
> 定位: v51 / v52 沉淀下来的**工程机制与参数档位**，供后续实验**直接参考实现**。
> 原则: **引用优先**——机制设计引用原始 design 文档，**参数取值以 `pipeline/` 代码为准**（代码是唯一事实源，YAML 只是赋值）。
> 何时读: 设计新实验的 step4 特征提取、step6 模型训练与资源规划时。

---

## 1. 母库（master library）机制

### 1.1 它解决什么

多档 `fc_profile`（compact/medium/broad/full）如果各自重新提取，会重复跑最贵的 full 档 tsfresh。母库机制把 full 档提取**只做一次**，其余档按 calculator 名做**列投影读盘**精确重建。

### 1.2 三种角色（`tsfresh.master_library_role`）

| 角色 | 含义 | 说明 |
| :--- | :--- | :--- |
| `off` | 不使用母库 | 默认值；各档独立提取 |
| `build` | 构建母库 | **仅允许与 `fc_profile=full` 搭配**（代码强制校验）；标准终点是 step4 |
| `project` | 从母库投影 | 读母库 `schema.json`，按本档 calculator 集合筛列重建 |

### 1.3 关键实现（`pipeline/config.py`）

| 函数/字段 | 作用 |
| :--- | :--- |
| `_master_version_hash(params)` | 母库版本哈希；payload 只含 `fc_profile="full"` + `lookback_days` + `lookback_periods` + `lookback_columns` → 取 sha256 前 8 位 |
| `resolve_master_library_version(params)` | 优先用显式 `master_library_version`，否则回退哈希 |
| `Paths.resolved_master_library_version` | 解析后的母库版本 |
| `Paths.tsfresh_master_root_dir` | 母库根目录，即 `data/tsfresh_master/` |

**目录形态**：`data/tsfresh_master/{master_version}/` 下含三类文件——`chunks/`（分块特征）、`schema.json`（列名 → kind/calculator/params 映射）、`zero_variance_columns.json`（零方差列清单）。

> **可复用的判据**：`master_version` 只由「full + lookback 配置」决定，因此**只要这些参数不变，母库可跨实验复用**；参数一变，hash 变，自动生成新母库目录。

### 1.4 新谱系如何用

1. 先跑一次 `master_library_role=build` + `fc_profile=full` 的配置（用新样本数据）。
2. 之后各档实验设 `master_library_role=project`，无需重跑 full 提取。
3. 若新谱系只做单档实验且不需复用，可直接 `off`。

**原始设计文档（详读）**：`strategy_explore` 仓库 `design/v5/design/v51_tsfresh/v511_tsfresh_performance/design.md`（§3 核心方案 / §4 build 步骤 / §5 project 步骤 / §6 缓存与复用语义）。

---

## 2. tsfresh `fc_profile` 四档

### 2.1 档位定义（`pipeline/step4_features.py::_build_full_tsfresh_fc_parameters`）

以 `EfficientFCParameters()` 全量为基准，按成本集合做减法：

| 档位 | 定义 | 计算器数（实测） |
| :--- | :--- | ---: |
| `compact` | 硬编码 `COMPACT_KEYS`（26 个基础计算器） | 26 |
| `medium` | `Efficient − HIGH_COST − MID_COST` | 56 |
| `broad` | `Efficient − HIGH_COST` | 64 |
| `full` | `Efficient` 全量 | 73 |

- `HIGH_COST`（9 个）：`ar_coefficient`、`cwt_coefficients`、`fft_aggregated`、`fft_coefficient`、`max_langevin_fixed_point`、`number_cwt_peaks`、`partial_autocorrelation`、`query_similarity_count`、`spkt_welch_density`
- `MID_COST`（8 个）：`agg_autocorrelation`、`augmented_dickey_fuller`、`autocorrelation`、`binned_entropy`、`fourier_entropy`、`friedrich_coefficients`、`lempel_ziv_complexity`、`permutation_entropy`

> 计算器数 ≠ 输出列数：多值计算器（`quantile`、`fft_coefficient` 等）会按参数展开成多列；full 档实测理论列数为 782/kinds。

### 2.2 四档的质量与资源结论（v51）

| fc_profile | test Spearman（default LGB） | peak_group |
| :--- | ---: | ---: |
| compact | 0.080 | 19 |
| **medium** | **0.097** | 42 |
| broad | 0.082 | 39 |
| full | 0.039 | 13 |

**结论**：medium 是拐点档；full 档反而最差（在 default 容量下被压制，见 §5 与 [02_reference_conclusions.md](02_reference_conclusions.md)）。量纲问题只在 compact 显著（不可比占比 42.3% → 14.7% → 12.5% → 7.4%）。

> 详读：`strategy_explore` 仓库 `design/v5/design/v51_tsfresh/closure.md`（7 条硬结论）。

---

## 3. LightGBM `lgb_profile` 三档

### 3.1 档位定义（`pipeline/config.py::LightGBMParams.resolve_profile`）

| 参数 | `default` | `high_dim` | `high_dim_aggressive` |
| :--- | :---: | ---: | ---: |
| `num_leaves` | 不覆写（31） | 63 | 127 |
| `max_depth` | 不覆写（7） | 8 | 10 |
| `min_child_samples` | 不覆写（100） | 200 | 300 |
| `colsample_bytree` | 不覆写（0.5） | 0.6 | 0.5 |
| `subsample` | 不覆写（0.6） | 0.7 | 0.8 |
| `learning_rate` | 不覆写（0.05） | 0.03 | 0.03 |
| `num_boost_round` | 不覆写（3000） | 5000 | 5000 |
| `reg_alpha` | 不覆写（1.0） | 2.0 | 3.0 |
| `reg_lambda` | 不覆写（5.0） | 2.0 | 3.0 |

- `default` → `resolve_profile()` 返回**空 dict**，完全使用 `LightGBMParams` 的显式字段值。
- **`num_boost_round` 是独立形参**，不在 `to_lgb_params()` 返回值里；集成时必须用 `profile_overrides.pop("num_boost_round", ...)` 单独取出，否则 5000 轮不生效。
- **冒烟降级**：`--mode smoke` 且 `lgb_profile != default` 时，强制降级为 `default`（$p \gg n$ 风险）。

### 3.2 三档效果结论（v52）

见 [02_reference_conclusions.md](02_reference_conclusions.md) 与 v52 closure。要点：**容量放大不是普适收益**——medium 档放大反而变差，只有 full 档有正向回升，且只缓解「排序」不改变「分层反向」。

---

## 4. 其它可复用工程机制

| 机制 | 取值/做法 | 来源 |
| :--- | :--- | :--- |
| step4 分块大小 | `chunk_size_tsfresh=18000`（v51 正式值；已证伪 40000） | `strategy_explore` 仓库 `design/v5/design/v51_tsfresh/closure.md`、[02_reference_conclusions.md](02_reference_conclusions.md) |
| tsfresh 并行度 | `tsfresh_n_jobs=8` | 同上（注意：CPU 利用率仅 17~20%，加 n_jobs 基本无效） |
| 分块控内存 | 有效（样本 48× → 内存 4×） | v51 closure 硬结论 4 |
| 上游内容寻址 | `upstream_version`（只由 step1–5 参数决定） | `strategy_explore` 仓库 `design/repo_cleanup/upstream_version_refactor_design.md` |
| 母库版本哈希 | 只含 `fc_profile=full` + lookback 配置 | `strategy_explore` 仓库 `design/v5/design/v51_tsfresh/v511_tsfresh_performance/design.md` §6.3.1 |

> `upstream_version` 目前是**设计草案**，尚未落地到 `pipeline/`；引用它时应明确标注「待实施」。

---

## 5. 参数选择结论（可直接参考）

| 场景 | 建议 |
| :--- | :--- |
| 特征档位（fc_profile） | 从 **medium** 起步（v51 拐点档）；不要默认上 full |
| 模型容量（lgb_profile） | 高维特征先试 `high_dim`；**不要假设容量越大越好**——medium 类中等维度放大会变差 |
| 母库 | 多档实验**务必先 build 再 project**，避免重复跑 full 提取 |
| 评估口径 | 因果判定以 test Spearman 为主；MonoScore / median uplift 只作分层辅助 |
| 特征价值判断 | **必须用足够容量**，否则会把「容量错配」误判为「特征弱信号」 |

**v52 的 11 个已落盘组合中，test Spearman 最高的是 `medium × default`（0.0969）**——即「中等特征量 + 默认容量」优于任何高容量放大。详见 `strategy_explore` 仓库 `design/v5/design/v52_lgb/closure.md` §2。

---

## 6. 与其它知识面的关系

| 知识面 | 关系 |
| :--- | :--- |
| [01_pipeline_contract.md](01_pipeline_contract.md) | 本层的参数取值必须满足该层的硬约束（如 LGB 固定 `seed=42`/`num_threads=1`） |
| [02_reference_conclusions.md](02_reference_conclusions.md) | 本层给「怎么配」，该层给「为什么这么配」的实验结论 |
| [03_pitfalls.md](03_pitfalls.md) | 本层参数涉及的风险（如低容量 judge 特征）在该层有踩坑记录 |
| [06_closeout_playbook.md](06_closeout_playbook.md) | 本层机制是「收尾后框架抽象」的输入之一 |
