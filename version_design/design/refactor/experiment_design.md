# v1.1 重构设计（实施规范）

> 版本: v1.1.0 目标设计
> 日期: 2026-11-06（2026-11-10 复核修订）
> 适用仓库: `ashare_signal_pipeline`（本仓库即内核模板，无需回灌）
> 读者: 工程实施 agent / 复核人
> 权威约束: `AGENTS.md`、`docs/01_pipeline_contract.md`
> 配套文档: `design_memory.md`（设计依据、修正历史、决策记录 —— **实施时不需要读**）
> 配套计划: `implementation_plan.md`（文件级顺序）、`code_agent_prompt.md`（执行边界）
> **开工前置**: 上述两份配套计划当前为空文件；M0 开工前必须由设计方补齐并保持非空（见 §7.2 规则 0）

---

## 阅读方式（先看这一节）

**不要通读全文。** 本文是参考手册，按里程碑查阅。

| 里程碑 | 只读这些章节 | 实际行数 |
| :---: | :--- | ---: |
| M0 | §1.1–1.3 · §7.2 · §7 M0 · §8.1 · §8.3 | ~143 |
| M1 | §1 · §2.1–2.2 · §3.1 · §3.4 · §4.1–4.4 · §5.1 · §6.2 · §7 M1 | ~378（含约 61 行可直接复制的 dataclass） |
| M2 | §1.1 · §3.1 · §3.2 · §3.4 · §4.4–4.10 · §6.2 · §7 M2 | ~334 |
| M3 | §3.3 · §3.5 · §5（全部） · §6.2 · §6.3 · §7 M3 · §8.1–8.2 | ~300 |
| M4 | §3.3 · §3.4 · §4.9 · §5.3 · §6.2 · §6.3 · §7 M4 · §8（全部） | ~278 |
| M5 | §0.4 · §6.3 · §7 M5 · §8.1 · §10.1 | ~111 |

全文 1289 行，但单个里程碑最多读 378 行，其中约 61 行是照抄的代码块、约 55 行是可跳过的表格列。**实际需要理解的散文式内容每个里程碑不到 200 行。**

（本表是唯一的阅读范围权威；`README.md` 不再重复行数。）

跨章节的约束只有两条，任何时候都适用，只读这两个小节即可：

- **§6.2 不变量** —— 违反即视为 bug，不论在哪个里程碑
- **§7.2 执行规则 / §7.3 止损规则** —— 含"越界即停"与"失败三次交回人工"

其余章节（§2.3 数据流、§9 限制与风险、§10 交付物、附录）只在需要时查阅。

---

## 0. 目标与范围

### 0.1 一句话目标

在 step1–step6 语义不变的前提下，把「**样本从哪来**」与「**学什么**」拆开，使二者可独立配置、独立版本化、独立重建。

### 0.2 现状问题

当前样本集与标签共用同一个生成规则：`holding_file` 是布林带卖出规则的输出，标签 `holding_return` 也是该规则的输出。因此模型学到的是「旧卖出规则在何种形态下更赚」，而不是「市场在何种形态下有正期望」。此外，样本只能来自 `holding_file`，无法接入任意事件采样。

### 0.3 本次范围

| 编号 | 交付 | 说明 |
| :--- | :--- | :--- |
| S1 | 新增 `data_prep/` 前置层 | 生成 `samples.parquet`（样本骨架）与 `labels/*.parquet`（标签），不占 step 编号、不进入状态机 |
| S2 | step1 双模式 | `input.mode: holding_file \| samples`，两模式输出同构 |
| S3 | 标签可插拔 | 注册表 + 基类；新增标签不改任何 step |
| S4 | 样本可插拔 | 注册表 + 基类；新增采样方法不改任何 step |
| S5 | 契约与校验 | `docs/15_data_contract.md`、`check_raw_data` 扩展、配置校验 |
| S6 | 可复现基线 | `baseline_v1.0.13.json` 自包含（参数快照 + 数据指纹） |

### 0.4 明确不做（v1.1）

| 不做 | 归属 |
| :--- | :--- |
| 改 step2/step3/step4/step6 的任何逻辑 | 永久 |
| 改 step5 的任何逻辑 | 永久（v1.1 起） |
| 组合回测、持仓管理、资金曲线 | 非目标 |
| `holding_clean.parquet` 改名 | v1.2 backlog（G3） |
| per-sample 标量特征通道 | v1.2 backlog（G4） |
| `bagging.py` 支持 samples 模式 | v1.2 backlog（G5） |
| 外部标签文件接入（`external_file`） | 不做，且不列入 backlog |
| 阶段 0 对照实验 | 独立文档 `experiment_plan_v11.md`，**不阻塞 v1.1.0 发布** |

### 0.5 与旧版本的关系

| 项 | 结论 |
| :--- | :--- |
| 旧路径（`holding_file`） | **行为必须与 v1.0.13 逐值一致**，这是发布的硬 Gate |
| 中间产物文件名 | `holding_clean.parquet` / `datebar.parquet` 等**一律不改名**，避免触碰下游与 bagging |
| 既有 check 名称与语义 | **不改**（`holding_target_non_null` 等 6 项） |
| 特征集 | **新路径不提供** `signal_date_selection_score`：samples 模式的 `holding_clean` 没有该列，step5 的 `if col in df.columns` 守卫会自动跳过，**不改 step5**；旧路径保留该列，R1 用它做回归。证据与权衡见 `design_memory.md` §3.4 |

---

## 1. 输入事实基准

以下为**实测确认**的输入形状。实现时必须以此为准，不得凭命名推测。

### 1.1 dateBar（`data.datebar_file`）

| 项 | 事实 |
| :--- | :--- |
| 类型 | `pd.DataFrame`，**MultiIndex** `(idxs, idxd)`，需 `reset_index()` 才能取列 |
| `reset_index` 后列 | `idxs, idxd, open, high, low, close, volume, amount, xs, paused, marks` |
| dtype | `idxs`: object(str)；`idxd`: int64；OHLCV+amount+xs: float32；`paused`/`marks`: int16 |
| `idxs` 格式 | `sh` / `sz` / `bj` + 6 位数字，如 `sh600000`；6090 个代码全部符合 |
| 覆盖 | 4384 个交易日，2007-01-04 至 2025-01-14；`(idxs, idxd)` 无重复 |
| 指数 | 723 个（`sh000*` 311 + `sz399*` 412），**不是可交易股票**，采样前必须排除 |
| `xs` | **后复权因子，且价格列已经复权**（`close ≈ 原始价 × xs`）→ **绝不能再乘 `xs`** |
| `paused` | 1 = 停牌；占 3.19%；停牌行 99.99% 同时 `volume <= 0` |
| `marks` | 涨跌停位掩码，43 种取值，bit ≥ 12 恒 0；位序见 §4.4 |
| 脏数据 | `high<low` 54 行、`high<max(open,close)` 109 行、`low>min(open,close)` 26 行；本版不修、不阻断 |
| 量纲 | `amount / (close × volume) ≈ 100`（volume 单位为手） |

### 1.2 holding（`data.holding_file`，旧路径专用）

| 项 | 事实 |
| :--- | :--- |
| 必需列 | `idxs, signal_date, holding_return, holding_trade_days, is_closed, entry_execution_price` |
| 规模 | 240,467 行、5328 个代码、2007-02-01 至 2025-01-13 |
| 与 dateBar | 代码 100% 匹配 |
| 过滤后 | 240,257 行（`is_closed & entry_execution_price>0 & holding_return.notna`） |
| `holding_trade_days` | 中位 13、P90 30、P99 63 |
| `holding_return` | 均值 0.77%、σ 17.2%、min −52.8%、max 761% |
| 死列 | `signal_date_selection_rank` 恒 1.0、`signal_date_selection_for_target` 恒 True、`position_side` 恒 `long` |

### 1.3 运行环境

| 项 | 值 |
| :--- | :--- |
| Python | `C:/ProgramData/anaconda3/envs/factor311/python.exe`（3.11.13） |
| 关键包 | pandas 2.2.2 / numpy 1.26.4 / pyarrow 21.0.0 / lightgbm 4.6.0 / tsfresh 0.21.2 / pytest 9.0.3 |
| 工作目录 | 所有命令在**仓库根**执行（`check_raw_data` 用脚本位置、pipeline 用 `cwd`，两者不一致） |

---

## 2. 架构

### 2.1 分层

```
┌──────────────────────────────────────────────────────────────┐
│ data_prep/（新增前置层；不占 step 编号，不进入状态机）        │
│  ids.py            sample_id 编解码 + 三类哈希 + 指纹          │
│  datebar.py        加载 + 规范化 + 指纹 + 逐 symbol 数组       │
│  universe.py       前缀过滤 / min_history / paused 掩码        │
│  samples/          base.py · registry.py                       │
│                    events/{bollinger.py, legacy_holding.py}    │
│  labels/           base.py · registry.py · cache.py            │
│                    future_return.py · trend_quality.py         │
│                    holding_return.py · _template.py            │
│  build_samples.py  编排 → data/prepared/{samples_id}/          │
│  label_builder.py  按需构造 → data/labels/{samples_id}/        │
└──────────────────────────────────────────────────────────────┘
              ↓ samples.parquet          ↓ {label}.parquet
┌──────────────────────────────────────────────────────────────┐
│ 执行主线（step1–6 语义不变）                                  │
│  step1_clean（双模式，唯一改逻辑的 step）                      │
│    → step2 → step3 → step4 → step5 → step6                   │
└──────────────────────────────────────────────────────────────┘
```

**依赖方向（硬约束）**

| 规则 | 说明 |
| :--- | :--- |
| `data_prep` **不得** import `pipeline` | 反向依赖会造成循环 |
| `pipeline` 允许**函数内惰性** import `data_prep` | 不在模块顶层 import |
| `data_prep` 公共函数只接受路径、primitive、dict | 不依赖 `PipelineParams` 类型 |
| `tools/verify_v11.py` **不得**被 `pipeline/**` 或 `data_prep/**` import | 它是 v1.1 专用一次性工具 |

### 2.2 文件布局

```
data_prep/
├── __init__.py
├── ids.py
├── datebar.py
├── universe.py
├── samples/{__init__.py, base.py, registry.py,
│            events/{__init__.py, bollinger.py, legacy_holding.py}}
├── labels/{__init__.py, base.py, registry.py, cache.py,
│           future_return.py, trend_quality.py, holding_return.py,
│           _template.py}
├── build_samples.py
└── label_builder.py

data/prepared/{samples_id}/{samples.parquet, manifest.json}
data/labels/{samples_id}/{_index.json, <label>.parquet}

tools/{build_samples.py, build_labels.py, probe_datebar.py, verify_v11.py}
tests/{conftest.py, test_data_prep/*, test_pipeline/test_step1_dual_mode.py}
pytest.ini
docs/{14_datebar_probe.md, 15_data_contract.md}
```

> 本图只表示**最终布局**；新增/修改文件以各里程碑的白名单为准（§7 M0–M5）。

### 2.3 数据流

```
data.raw_datebar
   │
   ├─ datebar.py → 规范化 9 列 + 指纹 + 逐 symbol 数组
   │
   ├─ samples/events/*（只用 ≤ signal_date 的数据）
   │     → data/prepared/{samples_id}/samples.parquet（骨架，无标签）
   │
   └─ labels/*（只用 > signal_date 的数据）
         → data/labels/{samples_id}/<label>.parquet

step1（input.mode=samples）
   读 samples → 校验指纹与集合哈希 → 取标签 → join → 丢空标签
   → holding_clean.parquet（sample_id, idxs, signal_date, target）→ 按 sample_id 升序

step2 → step3 → step4 → step5 → step6   （全部不变）
```

---

## 3. 数据契约

### 3.1 `samples.parquet`

路径：`data/prepared/{samples_id}/samples.parquet`

| 列 | 类型 | 来源 | 说明 |
| :--- | :--- | :--- | :--- |
| `sample_id` | int64 | `ids.derive_sample_id` | 唯一键，见 §3.4 |
| `idxs` | str | dateBar | 股票代码，已排除指数 |
| `idxd` | int32 | dateBar | 信号日 `YYYYMMDD` |
| `open/high/low/close/volume/amount` | float32 | dateBar | 信号日行情，**仅审计**；step2 自行取窗口 |
| `is_paused` | bool | dateBar | 该 `signal_date` 的 `paused` 原值。`bollinger_breakout` 构造上恒 `False`；`legacy_holding` **可能为 `True`**（旧路径也在 step2 丢弃，故保持原样才能与旧样本集相等）。**仅审计，step2 仍以自己的判定为准** |
| `marks` | int16 | dateBar | 信号日涨跌停掩码，**仅审计**；任何逻辑不得从 samples 消费它 |
| `sampling_method` | str | 构建 | 如 `bollinger_breakout` |
| `sampling_params_hash` | str | 构建 | 8 位 |
| `datebar_fingerprint` | str | 构建 | 见 §3.4 |
| `created_at` | str | 构建 | ISO 时间 |

**硬约束**

1. 不含任何 `target*` / `label*` 列；
2. 不含历史窗口序列（窗口由 step2 生成）；
3. **按 `sample_id` 升序落盘**（step2 冒烟抽样对行序敏感）。

**`manifest.json` 必需字段**

```text
samples_id, sampling_method, sampling_params, params_hash,
datebar_fingerprint, samples_set_hash, universe, date_range,
min_history_days, max_samples, rows_before_cap, sampling_rate,
year_quota, rows, n_symbols, created_at, code_version
```

`code_version` = `git rev-parse --short HEAD`，取不到写 `""`。

`rows_before_cap` / `sampling_rate` / `year_quota`：`max_samples` 未生效时写 `0` / `1.0` / `{}`；生效时写实际触发总数、`rows / rows_before_cap` 与各年配额（§4.2）。

### 3.2 标签文件

路径：`data/labels/{samples_id}/<label_name>.parquet`

| 列 | 类型 | 说明 |
| :--- | :--- | :--- |
| `sample_id` | int64 | 主键，与 samples 一致 |
| `idxs` | str | 冗余，便于审计与集合校验 |
| `signal_date` | int32 | 冗余 |
| `target` | float64 | 标签值 |
| `entry_idxd` | int32 | 建仓日；不可得写 `-1` |
| `exit_idxd` | int32 | 结束日；不可得写 `-1` |
| `is_complete` | bool | **仅**表示未来窗口在数据范围内完整 |
| `is_entry_blocked` | bool | 建仓日开盘涨停；**独立于** `is_complete` |
| `label_name` | str | 与文件名一致 |
| `label_params_hash` | str | 8 位 |
| `samples_id` | str | 所属样本集 |
| `datebar_fingerprint` | str | 行情指纹 |
| `created_at` | str | ISO |

**下游一律以 `target.notna()` 判断可用性**，不要用 `is_complete` 推断。

**行契约**：标签文件必须**每个 sample 一行**（与 `samples.parquet` 一一对应，按 `sample_id` 升序）；不可得的样本写 `target = NaN`，**不得删行**（否则覆盖率与守恒恒等式都失去意义）。

**`_index.json`**

```json
{
  "<label_name>": {
    "params_hash": "...", "rows": 0, "non_null_rows": 0,
    "coverage": 0.0, "created_at": "...", "holding_fingerprint": "..."
  }
}
```

`holding_fingerprint` 仅 `target_holding_return` 需要，其余写 `""`。算法：`sha256(holding 原始文件字节)[:12] + "_" + f"{rows}_{min_signal_date}_{max_signal_date}"`（与 `datebar_fingerprint` 同构，**不因 `processed/holding_clean` 被 step2 覆写而变化**）。

### 3.3 step1 输出（两模式同构）

文件：`data/processed/{experiment_version}/holding_clean.parquet`（**不改名**）

| 列 | `holding_file` 模式 | `samples` 模式 |
| :--- | :--- | :--- |
| `sample_id` | 原 parquet 索引（不变） | `ids.derive_sample_id` |
| `idxs` | 原样 | 原样 |
| `signal_date` | 原样 int | 原样 int |
| `target` | `holding_return` | `target.label` 对应标签值 |
| 其他原列 | 保留 | 不保留 |

**规则**

| # | 规则 |
| :---: | :--- |
| 1 | `samples` 模式**必须丢弃 `target` 为空的行**（否则 `holding_target_non_null` 必然失败） |
| 2 | `samples` 模式输出**按 `sample_id` 升序**；`holding_file` 模式保持原序（对齐基线） |
| 3 | `checks` 的 6 个 key 两模式完全一致：`holding_exists` / `holding_columns` / `holding_target_non_null` / `datebar_exists` / `datebar_columns` / `datebar_sorted`；**名称与布尔语义不得修改** |
| 4 | `details` 两模式同键；缺省值一律用 `""`（字符串）或 `0`，**不要用 `null`**（避免 JSON 类型漂移影响签名比较） |
| 5 | 标签名解析：`target.source=label_file` → `target.label`；`target.source=holding_return`（仅 `samples` 模式合法）→ 固定 `target_holding_return`，且 `target.label` 必须为空。两者都经 `resolve_label` 解析，不得在 step1 内按名称分支 |

**`details` 必需键**

```text
input_mode, coupling_mode, samples_rows, sample_count, dropped_no_target,
target_coverage, signal_date_min, signal_date_max, target_stats,
datebar_rows, datebar_fingerprint, samples_id, samples_set_hash,
label_name, label_hash, label_non_null_ratio
```

`label_hash` = 标签的 `label_params_hash`（§3.4）。

| 键 | `holding_file` 模式取值 |
| :--- | :--- |
| `input_mode` | `"holding_file"` |
| `coupling_mode` | `"path_dependent"` |
| `samples_rows` / `dropped_no_target` / `target_coverage` | `sample_count` / `0` / `1.0` |
| `samples_id` / `samples_set_hash` / `label_name` / `label_hash` / `label_non_null_ratio` | `""` |

| 键 | `samples` 模式取值 |
| :--- | :--- |
| `input_mode` | `"samples"` |
| `coupling_mode` | `"decoupled"` |
| `samples_rows` | `samples.parquet` 行数 |
| `sample_count` | 丢弃空标签后的行数 |
| `dropped_no_target` | `samples_rows − sample_count` |
| `target_coverage` | `sample_count / samples_rows` |
| `label_non_null_ratio` | 标签文件中 `target.notna()` 比例 |

### 3.4 标识与哈希

| 标识 | 算法 | 用途 |
| :--- | :--- | :--- |
| `sample_id` | `market*10^14 + code6*10^8 + idxd`；`sh=1, sz=2, bj=3`，`code6` 为 `idxs` 后 6 位整数 | 全链路唯一键；与 `(idxs, idxd)` 双射；`sh000001`(指数) 与 `sz000001`(平安) 不冲突 |
| `datebar_fingerprint` | `sha256(raw 文件字节)[:12] + "_" + f"{rows}_{min_idxd}_{max_idxd}_{n_symbols}"` | 行情变更即失效 |
| `holding_fingerprint` | `sha256(raw holding 文件字节)[:12] + "_" + f"{rows}_{min_signal_date}_{max_signal_date}"` | 仅 `target_holding_return`；旧标签变更即失效 |
| `sampling_params_hash` | `sha256(canonical_json({method, params, universe, date_range, min_history_days, max_samples}))[:8]` | 采样参数变更即失效（`max_samples` 必须在内，否则换 cap 会复用同一 `samples_id`） |
| `samples_id` | `f"{method}_{sampling_params_hash}_{datebar_fingerprint[:8]}"` | 目录名 |
| `label_params_hash` | `sha256(canonical_json({label, spec_version, params, samples_id, datebar_fingerprint, holding_fingerprint}))[:8]` | 标签变更即失效 |
| `samples_set_hash` | `sha256(np.sort(sample_id).astype("<i8").tobytes())[:16]` | 缓存命中判定 + samples 完整性校验 |

`canonical_json` = `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`。

`label_params_hash` 中的 `spec_version` **只取** `resolve_label(name)` 返回的 `definition.spec_version`（YAML 中没有该字段，见 §5.1）。`holding_fingerprint` 在非 `target_holding_return` 时取 `""`；step1 `details` 与 runner 签名里的 **`label_hash` 就是该 `label_params_hash`**（同一物，两处命名）。

**缓存策略**：`label_params_hash` 与 `samples_set_hash` 同时一致 → 复用；任一不一致 → **整表重建**。不做增量补算。

**`samples_id: auto` 规则**

1. step1 必须调用与 `build_samples` **同一个函数**推导，不得各写一份；
2. 显式给出 `samples_id` 时，若与推导值不一致 → 报错（提示配置与产物不匹配）；
3. 显式值必须匹配 `^[a-z0-9_]+$`。

**step1 的双重校验**（`samples` 模式）

| 校验 | 不一致时 |
| :--- | :--- |
| `manifest.datebar_fingerprint` == 当前重算值 | 报错，提示重建 samples |
| `manifest.samples_set_hash` == 当前重算值 | 报错，提示 samples 被替换或损坏 |

### 3.5 路径与入库边界

| 路径 | 入库 | 说明 |
| :--- | :---: | :--- |
| `configs/**`（除 `configs/local/`） | 是 | 采样/标签配置的唯一来源 |
| `data/raw/**` | 否（仅 `.gitkeep`） | 只读输入 |
| `data/prepared/**`、`data/labels/**` | 否 | 运行时资产；由配置 + 确定性 ID 重建 |
| `outputs/experiment_registry.json` | 是 | 台账；发布前用可跟踪配置重跑一次再提交 |

### 3.6 防泄漏边界

| 约束 | 落点 |
| :--- | :--- |
| 采样只用 `signal_date` 当日及以前（布林带仅用截至当日收盘） | `samples/events/*`；基类不交原始数组 |
| 标签只用 `signal_date` 之后（建仓日严格 `> signal_date`） | `labels/*`；由 `LabelDefinition` 接口强制 |
| 训练标签不得侵入验证区间 | 配置校验 V5：`buffer_trading_days >= max(lookback_days, horizon_days)` |
| 统计量只在训练集内算 | 沿用 step4 的 `clip_percentiles.json`，不新增 |
| 冒烟指标不作结论 | 不变 |

---

## 4. 采样与标签规范

### 4.1 采样基类

```python
class SampleDefinition:
    name: str                      # 唯一标识
    params_key: str                # 配置中 data_prep.samples.params 的键（内置固定为 "params"）
    default_params: dict

    def generate(self, bars, params, ctx: "SampleContext") -> pd.DataFrame:
        """返回 (idxs, idxd) 两列；不得返回标签，不得返回历史窗口。"""
```

| 约束 | 说明 |
| :--- | :--- |
| 只读过去 | `ctx` 只提供「截至当日」的序列访问；不提供未来窗口 |
| 确定性 | 同输入同参数必须产出逐值相同的结果 |
| 输出 | 仅 `(idxs, idxd)`；`sample_id` 与元数据由 `build_samples` 统一附加 |

**`SampleContext`（由 `build_samples` 构造；采样的唯一数据入口）**

| 成员 | 语义 |
| :--- | :--- |
| `history(symbol, pos, n)` | 该 symbol 在 `pos`（含）之前 n 个非停牌 bar 的行情数组 |
| `index_of(symbol, idxd)` | `idxd` 在该 symbol 非停牌序列中的位置；不存在返回 `-1` |

**不得**暴露原始 `bars` 数组、任何 `pos` 之后的序列或前瞻方法——「采样看不到未来」由接口保证，不靠自觉。

### 4.2 内置采样：`bollinger_breakout`

参数：`ma_period`（默认 20）、`std_mult`（默认 2.0）。

```text
对每个 symbol 的非停牌 close 序列：
  ma    = close.rolling(ma_period).mean()
  sd    = close.rolling(ma_period).std(ddof=1)
  upper = ma + std_mult * sd
  事件  = (close_t > upper_t) and (close_{t-1} <= upper_{t-1})
  要求  t-1 处 upper 非 NaN（即至少 ma_period+1 个观测）
```

| 规则 | 值 |
| :--- | :--- |
| 价格 | **直接使用 close，不乘 `xs`**（已后复权） |
| 股票池 | 排除 `universe.exclude_prefixes`（默认 `sh000` / `sz399` / `sh950`） |
| 历史长度 | 至少 `universe.min_history_days` 个非停牌观测 |
| 超限处理 | 触发数 > `max_samples` 时按下方「分年等距抽样」执行；`max_samples=null` 时全量保留 |
| 输出规模参考 | 397 只股票 18 年约 50,844 次（≈127 次/只）；全市场外推 ≈6.9×10⁵ |

**分年等距抽样（`max_samples` 的确定性口径）**

```text
year(y) = idxd // 10000
n[y]    = 该年触发行数；R = 总触发数；M = max_samples

1) 年度配额（最大余数法，保证 Σquota = M）：
   quota[y] = floor(M * n[y] / R)
   余数 r[y] = M * n[y] - quota[y] * R
   按 (r[y] 降序, y 升序) 逐个 +1，直到 Σquota == M
2) 组内等距：该年按 (idxs, idxd) 升序取下标
   idx_i = floor(i * n[y] / quota[y])，i = 0..quota[y]-1（去重后若少 1 行，取最后一个未用下标）
3) 落盘：仍按 sample_id 升序；manifest 写 rows_before_cap、sampling_rate = M/R、year_quota
```

同一输入 + 同一 `max_samples` 必须得到逐行相同的结果；抽样率必须打印并写入 manifest。

### 4.3 内置采样：`legacy_holding`

用途：把旧样本集重贴新标签（对照实验 0B），保证样本集与旧路径完全相同。

```text
读 data.holding_file，套用与 step1 相同的过滤：
  is_closed == True
  and entry_execution_price > 0
  and holding_return.notna()
取 (idxs, signal_date) → 转 (idxs, idxd)
```

强制约束（配置校验 V10 / V14）：`sample_filter.max_holding_days = null`；`max_samples` 与 `date_range_*` 必须为 `null`。任一不满足，样本集就会与旧路径不一致。

### 4.4 `marks` 位解码

```python
open_limit_up    = bool(marks & (1 << 11))
close_limit_up   = bool(marks & (1 << 8))
open_limit_down  = bool(marks & (1 << 7))
close_limit_down = bool(marks & (1 << 4))
```

即：高 nibble（bit 11..8）= 涨停的 开/高/低/收；低 nibble（bit 7..4）= 跌停的 开/高/低/收；nibble 内 `8=开盘, 4=最高, 2=最低, 1=收盘`。

### 4.5 标签基类

```python
class LabelDefinition:
    name_pattern: str              # 必须命中的标签名正则，如 r"^target_future_(\d+)d$"
    params_key: str                # 配置中 data_prep.labels.params 的键
    spec_version: int              # 改数值口径必须 +1
    default_params: dict

    def horizon_of(self, name: str, params: dict) -> int | None:
        """由名称 + 参数解析前视跨度；无前视返回 None。参与 V5 的 buffer 校验。"""

    def compute(self, samples, ctx: "LabelContext", params) -> Dict[str, pd.DataFrame]:
        """返回 {标签名: 完整帧}；键必须命中本定义的 name_pattern。"""
```

**名称是"解析出来的"，不是类字段**：一个定义可以产出多个标签名（`future_return` 一次算 4 个 horizon）。因此 `target.label` 一律经 `registry.resolve_label(name)` 解析为 `(definition, name_params, horizon)`，V4 / step1 / M2 全部走这同一个入口，**任何地方都不得按名称字符串做分支**。

**返回形状**：`compute` 返回 **dict**，键是标签名（如 `{"target_future_20d": frame}` 或 4 个 horizon 各一个键）；每个 frame 必须满足 §3.2。一个 `params` 组合下能产出哪些键由定义自身决定。`_index.json` 按**实际写出的文件名**逐条登记。

**接口强制**

| 机制 | 作用 |
| :--- | :--- |
| `resolve_label(name)` | 名称 → `(definition, name_params, horizon)`；未命中任何 pattern 时报错并列出全部可用 pattern |
| `LabelContext.entry_position(pos)` | 返回 `signal_date` 之后第一个非停牌位置 |
| `LabelContext.forward_window(pos, n)` | 返回从建仓点起 n 个 bar；**唯一获取未来数据的入口** |
| 不传原始 `bars` 数组 | 让「误用过去数据」在接口层写不出来 |
| `validate_label_frame(frame)` | 校验 §3.2 列契约，缺列即报错 |

**`LabelContext`（由 `label_builder` 构造）** 只暴露上述两个前瞻方法，**不提供过去窗口方法**；与 `SampleContext` 是两个不可互换的类型（§4.1）。

### 4.6 内置标签：`target_future_{h}d`（h ∈ {5, 10, 20, 40}）

**名称与产出**

| 项 | 值 |
| :--- | :--- |
| `name_pattern` | `^target_future_(\d+)d$` |
| `params_key` | `future_return` |
| `horizon_of(name, params)` | 取正则捕获组；**且必须出现在 `params["horizons"]` 中**，否则报错 |
| 一次构建的产出 | 对 `params["horizons"]` 的每个 h 各写一个 `<label>.parquet` |

记 `s = signal_date`，`pos(s)` = 该 symbol 在非停牌序列中的位置，`e = pos(s) + 1`（`s` 之后第一个非停牌交易日）。

```text
entry  = open(e)
exit   = close(e + h)
target = exit / entry - 1
```

| 情形 | 处理 |
| :--- | :--- |
| `e` 不存在 | `target = NaN`，`is_complete = False` |
| `e + h` 越界 | `target = NaN`，`is_complete = False` |
| `e` 日开盘涨停（`marks & (1<<11)`） | `is_entry_blocked = True`；`drop_blocked_entries=true` 时 `target = NaN`，**不改 `is_complete`** |
| 出场日收盘跌停 | **不顺延**（本版限制，写入文档） |
| 停牌日 | 不参与计数（`e` 是「之后第一个非停牌日」，不是「次日」） |
| 缩尾 / 截断 / 分类 | **不做**（由 step4/step5 在训练集内处理） |

### 4.7 内置标签：`target_trend_quality_{h}d`（内置默认 h = 20）

| 项 | 值 |
| :--- | :--- |
| `name_pattern` | `^target_trend_quality_(\d+)d$` |
| `params_key` | `trend_quality` |
| `horizon_of(name, params)` | 取正则捕获组；**且必须等于 `params["horizon"]`**，否则报错 |

```text
r  = target_future_{h}d（同 h）
c  = close(e .. e+h)
md = max_i(1 - c_i / max(c_e .. c_i))
target = r / md     if md > 1e-6 else NaN
```

单调上涨时 `md ≈ 0` → 给 `NaN` 而非极大值。

### 4.8 内置标签：`target_holding_return`（诊断专用）

| 项 | 值 |
| :--- | :--- |
| `name_pattern` | `^target_holding_return$`（单例） |
| `params_key` | `holding_return` |
| `horizon_of(...)` | 恒 `None`（无前视，不用 `forward_window`） |

从 `data.holding_file`（**raw**，不是 `processed/holding_clean.parquet`）按 `(idxs, signal_date)` 对齐 `holding_return`，前置过滤与 step1 一致。

三条限定必须写入任何使用它的报告：

1. 覆盖不完整且**非随机**（只有「突破后最终跌破中轨」的完整交易才有记录）；
2. 只能回答「旧标签在新样本集上是否还成立」，**不能**用于比较采样优劣；
3. `coverage` 是一等指标，必须报告。

### 4.9 NaN 与丢弃口径

| 层 | 行为 |
| :--- | :--- |
| 标签生成 | 只标记 `is_complete=False`，**不删行** |
| step1（`samples` 模式） | 丢弃 `target` 为空的行，计入 `dropped_no_target` |
| step4 | 丢弃 `target` 为 NaN/inf 的样本，计入 `invalid_targets_dropped` |
| step5 | **不是兜底**：它先 `replace_inf_and_fill(fillna=True)` 把 NaN 填 0，再判空，因此 `target_rows_dropped` 恒为 0 |

**守恒恒等式**（M4 与任何使用新路径的实验必查）

```text
samples_rows
  − dropped_no_target(step1)
  − dropped_samples(step2)            # missing_signal_date / paused_signal_date / insufficient_history
  − invalid_targets_dropped(step4)
  == step4 输出特征表行数
```

`dropped_samples` 取 step2 `details.dropped_samples`。**step2 full 会用有效样本覆写 `holding_clean.parquet`**（`step2_slices.py`），因此任何计算都必须以 `samples.parquet` 为样本全集，不能读覆写后的 `holding_clean`。

不平即说明有 NaN 标签被静默填成 0（或样本被误丢），结论作废。

### 4.10 扩展方式

**新增标签**（不改任何 step）

| 步骤 | 内容 |
| :---: | :--- |
| 1 | 复制 `data_prep/labels/_template.py`，实现 `LabelDefinition` 子类：声明 `name_pattern` / `params_key` / `spec_version`，实现 `horizon_of()` 与 `compute()`，并加 `@register_label` |
| 2 | 在 YAML 的 `target.label` 引用，并在 `data_prep.labels.params.<params_key>` 提供参数 |
| 3 | 更新 `docs/15_data_contract.md` 的内置标签清单表（含 `name_pattern` 一列） |

```yaml
target:
  source: label_file
  label: target_vol_20d          # 必须命中该定义的 name_pattern
  horizon_days: 20               # 必须等于 horizon_of() 的解析值（V4）
data_prep:
  labels:
    params:
      vol: {window: 20}          # 键 = 该标签声明的 params_key
```

对应定义：`name_pattern = r"^target_vol_(\d+)d$"`、`params_key = "vol"`、`horizon_of` 取捕获组（无需再写 `horizon` 参数）。

缓存自动生效（`label_params_hash` 已涵盖 label + spec_version + params + samples_id + 指纹）。

**新增采样**（不改任何 step）

| 步骤 | 内容 |
| :---: | :--- |
| 1 | 新建 `data_prep/samples/events/<name>.py`，实现 `SampleDefinition` 并加 `@register_sample` |
| 2 | 配置 `data_prep.samples.method: <name>` |
| 3 | 更新 `docs/15` 的内置采样清单 |

**`params_key` 与名称规则**：配置字典的键是标签/采样**自己声明的 `params_key`**，不是文件名。内置值：`future_return` / `trend_quality` / `holding_return`（标签），`params`（采样）。未声明的键在校验时报错并列出可用键。

**标签名必须命中某个定义的 `name_pattern`**；新增标签时 `name_pattern` 与 `params_key` 必须成对声明，`resolve_label` 是唯一入口（见 §4.5）。若两个定义都能命中同一名称 → 判定为注册冲突，加载期报错。

---

## 5. 配置规范

### 5.1 新增 dataclass（`pipeline/config.py`）

```python
@dataclass
class InputParams:
    mode: str = "holding_file"           # holding_file | samples

@dataclass
class TargetParams:
    source: str = "holding_return"       # holding_return | label_file
    label: str = ""
    samples_id: str = "auto"
    horizon_days: int = 0
    on_missing_samples: str = "error"    # v1.1 仅支持 error；build 保留字段但报错（见 §5.3 V3）
    on_missing_label: str = "error"      # v1.1 仅支持 error；build 保留字段但报错（见 §5.3 V3）

@dataclass
class UniverseParams:
    exclude_prefixes: List[str] = field(default_factory=lambda: ["sh000", "sz399", "sh950"])
    min_history_days: int = 60

@dataclass
class SampleBuildParams:
    method: str = "bollinger_breakout"
    params: Dict[str, float] = field(default_factory=lambda: {"ma_period": 20.0, "std_mult": 2.0})
    max_samples: Optional[int] = None

@dataclass
class LabelBuildParams:
    drop_blocked_entries: bool = True
    params: Dict[str, Dict[str, object]] = field(default_factory=lambda: {
        "future_return": {"horizons": [5, 10, 20, 40]},
        "trend_quality": {"horizon": 20},
    })

@dataclass
class DataPrepParams:
    date_range_start: Optional[int] = None
    date_range_end: Optional[int] = None
    universe: UniverseParams = field(default_factory=UniverseParams)
    samples: SampleBuildParams = field(default_factory=SampleBuildParams)
    labels: LabelBuildParams = field(default_factory=LabelBuildParams)
```

**修改既有类**

| 类 | 变更 |
| :--- | :--- |
| `RawDataParams` | 增加 `prepared_dir: str = "data/prepared"`、`labels_dir: str = "data/labels"` |
| `Paths` | 增加 `prepared_root_dir`、`labels_root_dir`（由 `build_paths` 解析；`ensure_directories` 只创建根目录，不创建 `samples_id` 子目录） |
| `ManualFeatureParams` | **不新增字段** |

**必须同时改 4 处**（漏一处即静默失效）；其中 1 / 2 / 4 在 **M1** 完成，3 在 **M3** 完成（见 §7）：

| # | 位置 | 里程碑 |
| :---: | :--- | :---: |
| 1 | `PipelineParams` 字段声明 | M1 |
| 2 | `from_dict` 的 `nested` 白名单：加 `input` / `target` / `data_prep` | M1 |
| 3 | `_validate_params`：加 §5.3 规则 | M3 |
| 4 | `version_payload()`：保持默认包含（**不要**像 `data` 那样排除） | M1 |

> M1 完成 1/2/4 后，`input.mode=samples` 必须**在 `step1.compute_step1` 顶部**显式 `raise NotImplementedError`，直到 M4 实现双模式为止；否则 samples 配置会静默走旧路径。
>
> 拦截**不放在** `_validate_params`：builder 工具（`tools/build_samples.py` / `build_labels.py`）要用同一份 YAML 的 `data_prep` 段，配置层若直接拒绝，M1 的产物就无法生成。§5.3 的完整规则集仍在 M3。

### 5.2 YAML 形态

```yaml
data:
  holding_file: data/raw/holding_stats_all.parquet
  datebar_file: data/raw/dateBar.pkl
  prepared_dir: data/prepared
  labels_dir: data/labels

# 旧路径（默认，行为与 v1.0.13 完全一致）
input:
  mode: holding_file
target:
  source: holding_return

# 新路径
#input:
#  mode: samples
#target:
#  source: label_file
#  label: target_future_20d
#  horizon_days: 20
#  samples_id: auto
#  on_missing_samples: error
#  on_missing_label: error

data_prep:
  date_range_start: null
  date_range_end: null
  universe:
    exclude_prefixes: ["sh000", "sz399", "sh950"]
    min_history_days: 60
  samples:
    method: bollinger_breakout
    params: {ma_period: 20, std_mult: 2.0}
    max_samples: null
  labels:
    drop_blocked_entries: true
    params:
      future_return: {horizons: [5, 10, 20, 40]}
      trend_quality: {horizon: 20}
```

### 5.3 校验规则（`_validate_params`）

| # | 规则 | 级别 |
| :---: | :--- | :--- |
| V1 | `input.mode ∈ {holding_file, samples}`；`target.source ∈ {holding_return, label_file}` | error |
| V2 | `holding_file` 模式：`target.source=holding_return` 时 `label` 必须为空；`target.source=label_file` 一律拒绝 | error |
| V3 | `target.source=label_file` 时**必须** `input.mode=samples`（反向不成立）；`label` 非空；**v1.1 中 `on_missing_samples` 与 `on_missing_label` 只允许 `error`**：取值为 `build` 时报错，并提示先运行 `tools/build_samples.py` / `tools/build_labels.py` | error |
| V4 | `target.label` 经 `resolve_label(name)` 解析得 `(definition, name_params, horizon)`：解析失败 → 报错并列出全部可用 `name_pattern`；`horizon` 非 `None` 时 `horizon_days` 必须相等；`horizon` 为 `None` 时 `horizon_days` 必须为 0 | error |
| V5 | `enforce_buffer=true` 时 `buffer_trading_days >= max(lookback_days, horizon_days)` | error |
| V6 | `data_prep.universe.min_history_days >= window.lookback_days` | error |
| V7 | `samples.method ∈ registry`；`params` 值均为正数 | error |
| V8 | `samples_id != auto` 时匹配 `^[a-z0-9_]+$` 且等于推导值 | error |
| V9 | 现有配置（无新段）零改动可加载，行为与 v1.0.13 一致 | 回归 |
| V10 | `samples.method=legacy_holding` 时 `sample_filter.max_holding_days` 必须为 `null` | error |
| V11 | `samples` 模式下 `manual_features.include_signal_date_selection_score=true` → 允许，但 `_validate_params` 必须在**加载期**打印「该特征不可得，将跳过」（**不改 step5**） | warn |
| V12 | registry 中**每个标签定义**的 `spec_version` 必须为 `>= 1` 的整数（加载期校验，不在 YAML 里配置） | error |
| V13 | `input.mode=samples` 且 `target.source=holding_return`：合法；`label` 必须为空；step1 取内置标签 `target_holding_return`（§4.8，从 raw holding 读取）；`on_missing_label` 同样生效（v1.1 同 V3，只允许 `error`） | error |
| V14 | `samples.method=legacy_holding` 时 `samples.max_samples` 与 `data_prep.date_range_start/end` 必须全为 `null`（否则样本集与旧路径不再相等，M1 验收 #6 必然失败） | error |

> V4 与 V7 都必须走 registry（标签侧一律用 `resolve_label`），**代码里不得出现任何标签名或采样名的字面量**。
>
> 两段式落地：V1–V14 在 M3 写入；但 samples 模式在 M4 之前仍被 M1 的显式拒绝拦截，故 V4 / V7 / V13 / V14 的 samples 分支在 M4 前不可达——这是预期行为，不是遗漏。

### 5.4 版本哈希

| 变更 | 影响 `experiment_version` | 说明 |
| :--- | :---: | :--- |
| `input.mode` / `target.label` / `target.horizon_days` | 是 | 不同标签必须隔离输出 |
| `data_prep.*` | 是 | 采样参数变化即新样本集 |
| `data.*` 路径 | 否 | 沿用现有排除规则 |
| `--smoke` 取值 | 是 | `smoke_size` 在 payload 内；回归必须传相同值 |
| 新增配置段本身 | 是 | M1 起即生效；此后不得沿用 v1.0.13 的输出目录，回归只比对 state 数值 |

---

## 6. 模块规范与不变量

### 6.1 模块职责

| 模块 | 职责 | 关键接口 |
| :--- | :--- | :--- |
| `data_prep/ids.py` | `sample_id` 编解码、三类哈希、指纹 | `derive_sample_id(idxs, idxd)`、`parse_sample_id()`、`derive_datebar_fingerprint(path)`、`derive_samples_id(...)` |
| `data_prep/datebar.py` | 加载、规范化（小写列、int32/float32、按 `(idxs, idxd)` 排序）、逐 symbol 数组 | `load_datebar(path) -> (df, fingerprint)`、`build_symbol_arrays(df)` |
| `data_prep/universe.py` | 前缀排除、`min_history_days`、`paused` 掩码 | `filter_universe(df, params)`、`valid_positions(sym_arrays)` |
| `data_prep/samples/base.py` | 采样基类 | `generate(bars, params, ctx: SampleContext)` |
| `data_prep/samples/registry.py` | 注册与查找 | `register_sample` / `get_sample` |
| `data_prep/samples/events/bollinger.py` | §4.2 | — |
| `data_prep/samples/events/legacy_holding.py` | §4.3 | — |
| `data_prep/labels/base.py` | 标签基类 + `LabelContext` + `horizon_of()` + `validate_label_frame()` | `compute(samples, ctx: LabelContext, params) -> Dict[str, DataFrame]`、`horizon_of(name, params)` |
| `data_prep/labels/registry.py` | 注册与**名称解析** | `register_label` / `resolve_label(name) -> (definition, name_params, horizon)` |
| `data_prep/labels/future_return.py` | §4.6，一次遍历算全部 horizon，分别落盘 | — |
| `data_prep/labels/trend_quality.py` / `holding_return.py` | §4.7 / §4.8 | — |
| `data_prep/labels/cache.py` | 索引、校验、读写、重建 | `is_valid` / `load` / `save` / `rebuild` |
| `data_prep/labels/_template.py` | 新增标签的复制模板 | — |
| `data_prep/build_samples.py` | 编排 samples 生成 | `build(config_dict) -> samples_id` |
| `data_prep/label_builder.py` | 构造标签（**只由 `tools/build_labels.py` 调用**，step1 不调用） | `ensure(config_dict, samples_id, label) -> List[Path]`：先 `resolve_label(label)`，再保证该定义的**全部**产出就绪（缺任一则整组重建），返回本次写出的文件列表；step1 缺标签只抛错，不隐式构建 |
| `tools/build_samples.py` / `build_labels.py` / `probe_datebar.py` | CLI | `--config`、`--dry-run`、`--json` |
| `pipeline/step1_clean.py` | 双模式 + 标签注入 | `compute_step1` |

### 6.2 不变量

| # | 不变量 |
| :---: | :--- |
| 1 | `idxs` 全链路为字符串；`signal_date` / `idxd` 全链路为 int |
| 2 | `sample_id` 单调可排序、与 `(idxs, idxd)` 双射；**禁止** `reset_index(drop=True)` 重建 |
| 3 | `data_prep` 不 import `pipeline`；`pipeline` 只在函数内惰性 import `data_prep` |
| 4 | `holding_clean.parquet` 的文件名与必需列不变 |
| 5 | step1 的 `checks` 键集合与布尔语义两模式一致；`details` 缺省值类型一致（`""` / `0`，不用 `null`） |
| 6 | `processed/datebar.parquet` **恒为 9 列**（`idxs, idxd, open, high, low, close, volume, amount, paused`），两模式都由 `_clean_datebar` 生成；`marks` / `xs` 只在 `data_prep` 内从 raw dateBar 消费 |
| 7 | `data_prep/datebar.py` 与 `step1._clean_datebar` 是两套独立实现，必须产出**数值逐值相同**的 9 列（比较按值，不按 dtype：前者允许 `int32`，后者实际为 `int64`）；M4 用一次性比对脚本证明 |
| 8 | `samples.parquet` 与 `samples` 模式的 `holding_clean` 必须按 `sample_id` 升序落盘 |
| 9 | CLI 必须先用 `load_params` 解析 YAML，再把 `asdict(params)` 的子集以 **dict** 传入 `data_prep`；`data_prep` 不得自行解析 YAML |
| 10 | 任何重复计算（如 dateBar 两次加载）都必须显式写出，不得隐式发生 |

### 6.3 唯一的行为变化点

| 文件 | 允许的改动 |
| :--- | :--- |
| `pipeline/step1_clean.py` | M1：`compute_step1` 顶部新增 samples 模式显式拒绝（M4 删除）；M4：拆 `_clean_holding` / `_clean_samples`，两模式同构返回 |
| `pipeline/runner.py` | `_completed_step_matches_current_inputs` 扩展到 step1 |

其余 `pipeline/**` 在 v1.1 中**只有两处改动**：`config.py`（M1 新增字段、M3 新增校验；默认值保持旧行为）与 `step2_slices.py`（仅 `invalid_reasons` 的计数键名）。step3/4/5/6 零改动。M1 的 samples 拒绝只影响 M1 新引入、此前不存在的配置组合，不改变任何既有配置的行为。

---

## 7. 交付里程碑

### 7.1 总览

按**合并点**切分：每个里程碑单独合入后，仓库必须是绿的、旧行为必须是可证的。

| 里程碑 | 内容 | 行为变化 |
| :---: | :--- | :---: |
| M0 | 基线自包含 + 探测固化 + 验收工具就绪 | 无 |
| M1 | samples 层（含 config 字段与 step1 守卫） | 无（仅 `experiment_version` 变化；samples 模式显式报错） |
| M2 | labels 层（纯新增） | 无 |
| M3 | 接线准备（校验/契约/就绪检查） | 无 |
| M4 | step1 双模式 + runner 签名 + R1 回归 | **有（唯一）** |
| M5 | 收尾与发布 | 无 |
| E | 阶段 0 对照实验 | 不在 v1.1.0 内 |

### 7.2 执行规则

| # | 规则 |
| :---: | :--- |
| 0 | **开工前置**：`implementation_plan.md` 与 `code_agent_prompt.md` 必须非空且经设计方确认；实施 agent 不得自行生成施工顺序或执行边界 |
| 1 | 按 M0 → M5 顺序执行；**每个里程碑（含 M0）未通过 `verify_v11.py` 不得开始下一个** |
| 2 | 每个里程碑只改**文件白名单**内的文件；`git diff --stat` 越界即停 |
| 3 | 报告/PR 中的数字只能来自脚本输出；基线数字只能从 JSON 读取，**禁止硬编码字面量** |
| 4 | **不得修改任何既有 check 的名称或判定语义**；check 不通过时修数据，不改断言 |
| 5 | 遇到本设计未覆盖的情形 → 停下并记录，不自行扩展设计 |

### 7.3 为什么不可能「改坏到从零开始」

这是本设计最重要的安全性质，**先看结论再看理由**：

| 里程碑 | 能改什么 | 最坏情况 | 旧 pipeline 是否还能跑 |
| :---: | :--- | :--- | :---: |
| M0 | 只新增文件（脚本/文档/JSON/配置） | 脚本有 bug | ✅ **能**，零改动 |
| M1 | 只新增 `data_prep/` + `config.py` 加字段 + `step1` 顶部拒绝 samples 模式 | 采样逻辑错 / 版本哈希变 | ✅ **能**，旧配置解析与旧路径行为不变 |
| M2 | 只新增 `data_prep/labels/` | 标签算错 | ✅ **能**，同上 |
| M3 | `config.py` 加校验、`adapters` 加常量、`check_raw_data` 加分支、`step2` 改计数键名 | 旧配置加载失败 | ⚠️ **可能受影响**，靠回归拦截 |
| M4 | `step1_clean.py`、`runner.py` | 新路径算错 | ⚠️ **可能受影响**，靠 L1/L2/L3 拦截 |
| M5 | 只改文档 + `outputs/experiment_registry.json` + 删除 `verify_v11.py` | 文档写错 | ✅ **能**，内核零改动 |

**M0–M2 的安全性**：M0/M2 只新增文件，且没有既有模块引用它们；M1 新增的 `data_prep/` 同样无人引用，`config.py` 只增加带默认值的字段，旧 YAML 的解析结果与全部数值不变（`experiment_version` 变化属预期，见 §5.4）。这是刻意的排序——先用最安全的一段范围把新层建好并验证，再动唯一需要动的那一个文件。

**M3/M4 的兜底机制**

| 机制 | 作用 |
| :--- | :--- |
| Git 回滚点 | 每个里程碑验收通过后打一个本地 tag（`m0-ok` … `m4-ok`），失败时 `git reset --hard m<k>-ok` |
| 双配置并存 | 新旧配置同时可用；旧配置永远是默认值 |
| 单点改动 | 行为变化集中在 `step1_clean.py` 一个文件，其他都是加字段/加分支 |
| 分层回归 | L1（full step1）/ L2（worktree 逐值）/ L3（smoke 指标）三层，任一层失败即停 |

**止损规则（必须遵守）**

| 情形 | 动作 |
| :--- | :--- |
| 某里程碑回归失败 **第 1 次** | 定位并修复，**不得**修改 check 或降低断言 |
| 同一里程碑回归失败 **第 2 次** | `git reset --hard m<k-1>-ok`，回到上一个绿点，重新读该里程碑对应章节后再做 |
| 失败 **第 3 次** | **停止实施**，把失败现象、已尝试方案、`verify_report.json` 写入 `version_design/design/refactor/BLOCKED.md`，交回人工裁决 |

**明确禁止的"修复"方式**（出现任一即视为实施失败）

| 禁止 | 为什么 |
| :--- | :--- |
| 删除或重命名既有 check | 掩盖问题，且破坏 `docs/07` 契约 |
| 把基线数字改成实际跑出来的值 | 等于取消回归 |
| 在 `step2`–`step6` 里加 `if input_mode == "samples"` 之类的分支 | 违反"step2–6 零改动"，且会把耦合扩散到全链 |
| 为了让新路径跑通而放宽 `_validate_params` | 校验是防泄漏的一部分 |
| 跳过某个里程碑直接做后面的 | 依赖关系会断（M4 依赖 M1/M2/M3 的产物） |
| 删除 `data/` 或 `outputs/` 目录「重置环境」 | `AGENTS.md` 铁律 2 |

---

### M0：基线自包含 + 探测固化

**做什么**

1. `tools/verify_v11.py`：按 §8.1 的里程碑检查表实现，并先用它自证 M0；
2. `tools/probe_datebar.py`：复现 §1.1 的全部事实（列、dtype、规模、停牌占比、marks 取值数、复权口径），支持 `--json`；把输出保存为 `version_design/design/refactor/datebar_probe.json`（入库，供 `verify_v11` 读取）；
3. `docs/14_datebar_probe.md`：探测结论 + 缺失字段应对；
4. `baseline_v1.0.13.json` 升级为**自包含**：内联影响输出的全部参数值 + 两个 raw 文件的 sha256 与行数 + step1 的 `target_stats`，并记录旧样本集指纹 `legacy_holding_samples_set_hash`（供 M1/M4 比对）；
5. `configs/kernel_v11/check/refactor_check.yaml`：相对路径版回归配置（可入库）；
6. 把该配置注册进 `configs/experiment_index.yaml`（`docs/08` 约定）。

**4 项人工确认**

| # | 确认项 | 通过标准 |
| :---: | :--- | :--- |
| C1 | 复权口径 | 抽 1 只已知除权股与行情软件的前/后复权价对照，确认「价格已后复权」 |
| C2 | `idxs` 格式 | 确认新增代码仍为 `sh/sz/bj + 6 位`；不满足则 `ids.py` 记 `unmapped` 并报错退出 |
| C3 | 尾部缺口 | 记录 `is_complete=False` 的样本比例（预期集中在 2024-10 之后） |
| C4 | `marks` 位序 | 抽 10 个涨/跌停 bar，与 `pct_change` 及板块限幅（10%/20%/5%）互验 §4.4 |

**验收**

| # | 条件 |
| :---: | :--- |
| 1 | `probe_datebar.py --json` 输出与 §1.1 一致（规模 ±1%） |
| 2 | C1–C4 均有书面结论 |
| 3 | `baseline_v1.0.13.json` 含全部参数与指纹，不依赖 `configs/local/` |
| 4 | `verify_v11.py --milestone M0` → exit 0（检查项见 §8.1） |

**文件白名单**：`tools/probe_datebar.py`、`tools/verify_v11.py`、`docs/14_datebar_probe.md`、`version_design/design/refactor/baseline_v1.0.13.json`、`version_design/design/refactor/datebar_probe.json`、`configs/kernel_v11/check/refactor_check.yaml`、`configs/experiment_index.yaml`。

**Commit**：`feat(tools): add datebar probe script` / `docs: add datebar probe report` / `chore: inline baseline params and data fingerprints`

---

### M1：samples 层

**做什么**

| # | 文件 | 要点 |
| :---: | :--- | :--- |
| 1 | `ids.py` | 编解码 + 指纹 + `samples_id` 推导；单测覆盖 `sh000001` vs `sz000001` 不冲突、往返一致、int64 不溢出 |
| 2 | `datebar.py` | **必须 `reset_index()`**；列名小写；`idxd` int32；按 `(idxs, idxd)` mergesort 排序 |
| 3 | `universe.py` | 前缀排除 → `min_history_days` |
| 4 | `samples/base.py` + `registry.py` | 基类与注册 |
| 5 | `events/bollinger.py` | §4.2 |
| 6 | `events/legacy_holding.py` | §4.3 |
| 7 | `build_samples.py` + `tools/build_samples.py` | 写 `samples.parquet` + `manifest.json`，**升序**，manifest 含 §3.1 全部字段（含 `max_samples` / `rows_before_cap` / `sampling_rate` / `year_quota`） |
| 8 | `pipeline/config.py` | 新增 §5.1 的 6 个 dataclass、`RawDataParams` 两字段、`Paths` 两字段、`nested` 注册、`version_payload` 保持包含 |
| 9 | `pipeline/step1_clean.py` | **仅**在 `compute_step1` 顶部新增 samples 模式显式拒绝（`raise NotImplementedError`，M4 删除）；`_clean_holding` / `_clean_datebar` 一字不改 |
| 10 | `configs/default.yaml` | 加 `data_prep` / `input` / `target` 三段（取值即默认值） |

**不做什么**：不碰 `step2`–`step6`；`pipeline/config.py` 只允许上述纯增量字段；`step1` 只允许多那 3 行拒绝；不写标签；不生成历史窗口列。

**验收**

| # | 条件 |
| :---: | :--- |
| 1 | 列契约符合 §3.1，无 `target*` 列 |
| 2 | `sample_id` 唯一、非空、可逆 |
| 3 | 同配置重跑两次，`samples.parquet` 逐行逐值相等（含行序，忽略 `created_at`），`samples_set_hash` 相同 |
| 4 | **规模闸门**：真实全量计数替代外推；若 > 4×10⁵ 且 `max_samples` 为 `null`，先定抽样/区间方案再继续（见 §9.2） |
| 5 | 输出不含指数前缀 |
| 6 | `legacy_holding` 的 `(idxs, signal_date)` 集合与旧 `holding_clean` **完全相同**（集合比较，不用 `sample_id`） |
| 7 | `samples.parquet` 严格升序 |
| 8 | `pytest tests/test_data_prep -q` 全绿 |
| 9 | `step1`–`step6` 逻辑不变（`step1` 仅多 samples 模式显式拒绝）；旧路径仍可跑 |
| 10 | 改 `max_samples` 后 `samples_id` 必须变化；manifest 的四项抽样字段与 §4.2 算法一致 |
| 11 | `verify_v11.py --milestone M1` → exit 0 |

**文件白名单**：`data_prep/{ids.py, datebar.py, universe.py, __init__.py}`, `data_prep/samples/**`, `data_prep/build_samples.py`, `tools/build_samples.py`, `tests/conftest.py`, `tests/test_data_prep/**`, `pytest.ini`, `pipeline/config.py`（§5.1 纯增量）, `pipeline/step1_clean.py`（仅 samples 模式显式拒绝）, `configs/default.yaml`（仅三段配置）。

---

### M2：labels 层与缓存

**做什么**

| # | 文件 | 要点 |
| :---: | :--- | :--- |
| 1 | `labels/base.py` | 基类 + `next_tradable_position()` + `validate_label_frame()` |
| 2 | `labels/registry.py` | 注册与**名称解析**：`resolve_label(name) -> (definition, name_params, horizon)`；双命中即注册冲突报错 |
| 3 | `labels/future_return.py` | 一次遍历算全部 horizon，分别落盘；`marks` 判定建仓可行性 |
| 4 | `labels/trend_quality.py` / `holding_return.py` | §4.7 / §4.8 |
| 5 | `labels/cache.py` | `{samples_id}` 目录 + `_index.json`；命中 = `label_params_hash` ∧ `samples_set_hash` |
| 6 | `labels/_template.py` | 新增标签模板（含契约注释） |
| 7 | `label_builder.py` + `tools/build_labels.py` | 编排 |

**不做什么**：不碰 `pipeline/`；不缩尾；不做增量补算。

**验收**

| # | 条件 |
| :---: | :--- |
| 1 | 4 个 `future_{h}d` 行数 = samples 行数；`is_complete=False` 比例与 C3 预估同量级 |
| 2 | 手工抽 10 个样本，用 dateBar 原值复算 `entry / exit / target` 一致（1e-9） |
| 3 | `trend_quality` 在 `md <= 1e-6` 时为 NaN，无 inf |
| 4 | `holding_return` 覆盖率写入 `_index.json`，数值与旧路径同子集完全一致 |
| 5 | 二次调用不重算（mtime + 日志） |
| 6 | 篡改 `label_params_hash` 或 `datebar_fingerprint` → 触发重建 |
| 7 | `pytest tests/test_data_prep -q` 全绿 |
| 8 | `resolve_label` 三类异常均有单测：未命中任何 pattern、`target_future_30d`（h ∉ `horizons`）、两个定义同时命中同一名称 |

**文件白名单**：`data_prep/labels/**`, `data_prep/label_builder.py`, `tools/build_labels.py`, `tests/test_data_prep/test_labels*.py`, `tests/test_data_prep/test_cache*.py`。

---

### M3：接线准备（零行为变化）

**目标**：配置、契约、就绪检查全部就位，但 pipeline 行为仍是旧的。

**做什么**

| # | 文件 | 要点 |
| :---: | :--- | :--- |
| 1 | `pipeline/config.py` | §5.3 全部校验（V1–V13）；samples 模式由 step1 顶部拦截（M4 删除），配置层**不**拒绝 |
| 2 | `pipeline/adapters/sample_family.py` | 新增 `SAMPLES_REQUIRED_COLUMNS`、`LABEL_REQUIRED_COLUMNS`；`--show-contract` 打印样本/标签契约 |
| 3 | `tools/check_raw_data.py` | 按 `input.mode` 分支；新增 `prepared` 段（存在性/列契约/`sample_id` 唯一且 int64/`signal_date` 可 int 化/指纹与集合哈希匹配）；新增 `idxs` 交集率检查（< 99% 判 invalid，给前 5 个不匹配样例）；退出码保持 0/2/3 |
| 4 | `pipeline/step2_slices.py` | `invalid_reasons` 的 `missing_signal_date` 拆为 `unknown_symbol` 与 `date_not_in_symbol_range`（仅键名与文案） |
| 5 | `docs/15_data_contract.md` | samples / labels 契约 + 内置标签与采样清单 + 扩展步骤 |

**不做什么**：不改 `step1_clean.py`；不改 step3/4/5/6；不产生新产物。

**验收**

| # | 条件 |
| :---: | :--- |
| 1 | 默认配置（`holding_file`）跑 smoke step1–6，与基线逐值一致（`verify_v11.py --milestone M3`） |
| 2 | 旧 YAML 不改一行仍能加载，数值与 v1.0.13 一致；因新增配置段进入 payload，`experiment_version` **会变化**（§5.4）——这是预期行为，回归只比对 state 数值 |
| 3 | `input.mode=samples` 配置能被 `load_params` 解析；运行 step1 时在开头显式报「未实现」（M4 放开），不会静默走旧路径 |
| 4 | `check_raw_data` 旧配置返回 0；samples 配置缺文件返回 2 |
| 5 | `pytest tests -q` 全绿 |
| 6 | `git diff --stat` 只含白名单 |

**文件白名单**：`pipeline/config.py`, `pipeline/adapters/sample_family.py`, `pipeline/step2_slices.py`, `tools/check_raw_data.py`, `docs/15_data_contract.md`, `tests/**` 中对应文件。

---

### M4：step1 双模式（唯一行为变化）

**做什么**

| # | 文件 | 要点 |
| :---: | :--- | :--- |
| 1 | `pipeline/step1_clean.py` | **删除** M1 在 `compute_step1` 顶部的 samples 模式显式拒绝；`_clean_holding` 保持原逻辑（含 `max_holding_days`）；新增 `_clean_samples`：`resolve_label` → 推导 `samples_id` → 校验指纹与集合哈希 → 读取标签文件（**缺失即报错**，不隐式构建）→ join → 丢空标签 → 升序输出；两模式同构返回 |
| 2 | `pipeline/runner.py` | `_completed_step_matches_current_inputs` 扩展到 step1：比对 `input_mode` / `samples_id` / `label_hash` / `datebar_fingerprint`；旧 state 缺这些键时视为**签名变化 → 重跑 step1**（不得跳过） |
| 3 | `docs/07_step_acceptance_criteria.md` | 补 Step 1 六个 check 在 samples 模式下的语义映射（名称与判定不变） |

**不做什么**：不改 step2–step6；不引入历史窗口列；不在 step5 选标签。

**R1 行为回归（三层）**

| 层 | 覆盖 | 做法 | 通过标准 |
| :---: | :--- | :--- | :--- |
| L1 | step1 full | 跑一次 full step1 | `sample_count = 240257`、`datebar_rows`、`target_stats` 一致 |
| L2 | step2/step3 | `--smoke 20000` 跑 step1–3，与 `v1.0.13` worktree 同参数产物逐值比对（同 seed、同输入顺序） | 切片与特征逐值一致 |
| L3 | step4–6 | smoke 500 跑全链 | 下表字段逐值一致 |

L2 的对照必须用 `git worktree add` 检出 `v1.0.13`，否则没有「重构前」产物可比。`data/raw` 未入库，新 worktree 里没有数据：必须用指向主仓库的**绝对路径**配置（或 junction/软链），并在验收记录里写明所用路径；**不得**把 raw 数据复制进 worktree。

**L3 基线字段**（容差：计数精确相等；浮点 1e-12）

| 字段 | 基线值 |
| :--- | :--- |
| `step1.sample_count` | 240257 |
| `step1.datebar_rows` | 15547065 |
| `step2.input_samples` | 500 |
| `step2.valid_samples` | 494 |
| `step2.slice_rows` | 29640 |
| `step3.rows` | 29640 |
| `step3.nan_filled` | 6443 |
| `step4.columns` / `step4.selected_features` | 53 / 50 |
| `step5.train_rows` / `val_rows` / `test_rows` | 345 / 74 / 75 |
| `step6.best_iteration` | 215 |
| `step6.mae` | 0.09924436719491053 |
| `step6.spearman` | -0.0676529160739687 |
| `step6.pearson` | -0.06172137091798144 |

**三条必须注意的实测约束**

| # | 约束 |
| :---: | :--- |
| 1 | 新增配置段会改变 `experiment_version` → **不能**沿用旧输出目录；只比对 state 中的数值 |
| 2 | step1 **在冒烟模式下不抽样**（`del mode`）：`--smoke 500` 只让 step2 从 240,257 条中抽 500，故 `step1.sample_count` 仍是 240257 |
| 3 | step4 冒烟模式用**全体冒烟样本**筛特征，与 full 的「仅训练集」不同 → smoke 只证明代码路径一致，full 语义由 L1/L2 覆盖 |

**验收**

| # | 条件 |
| :---: | :--- |
| 1 | L1/L2/L3 全部通过 |
| 2 | `input.mode=samples` smoke 跑通 step1–6；`samples_id` 变更触发 step1 重跑 |
| 3 | samples 模式 `dropped_no_target > 0` 且 `checks.holding_target_non_null == True` |
| 4 | `data_prep/datebar.py` 与 `_clean_datebar` 的 9 列**按值**逐值相等（dtype 允许 int32/int64 差异；一次性比对脚本，结论写入 `docs/14`） |
| 5 | `check_raw_data` 两模式分别返回 0；缺 samples 返回 2；指纹/哈希不符返回 3 |
| 6 | 两条路径用不同 `state-file`，且**串行**执行 |
| 7 | `pytest tests -q` 全绿 |
| 8 | samples 模式守恒恒等式成立（§4.9，**含 step2 `dropped_samples`**） |
| 9 | `verify_v11.py --milestone M4` → exit 0 |

**文件白名单**：`pipeline/step1_clean.py`, `pipeline/runner.py`, `docs/07_step_acceptance_criteria.md`, `tests/test_pipeline/test_step1_dual_mode.py`。

---

### M5：收尾与发布

**做什么**

| # | 内容 |
| :---: | :--- |
| 1 | 文档：`docs/00`（新命令）、`docs/01`（step1 双模式、`samples_id` 与 state 规则）、`docs/10`（新增前置层，修订「11 个主线文件」表述）、`docs/11`（基线位置）、`AGENTS.md`（目录职责 + 铁律补充） |
| 2 | **目录约定收敛**：本项目正式约定为 `version_design/design/<lineage>/`；M5 同步修改 `AGENTS.md` §5 与 `docs/08` 的 `design/<base_version>/` 表述，并在本目录 `README.md` 记一行，避免两套路径并存 |
| 3 | 用 `configs/kernel_v11/check/refactor_check.yaml` 对跑一次 smoke，按 state 的 `status` 过滤后提交 `outputs/experiment_registry.json` |
| 4 | `verify_v11.py` 处置：移入 `version_design/design/refactor/` 归档，或直接删除 |
| 5 | **删除演练**：移走 `verify_v11.py` 后，`pytest tests -q` 与一次 smoke step1–6 仍全部通过 |
| 6 | 合入 `main` 后打 annotated tag `v1.1.0` |

**文件白名单**：`docs/00_start_here.md`、`docs/01_pipeline_contract.md`、`docs/08_lineage_startup.md`、`docs/10_architecture.md`、`docs/11_baseline_fixture_guide.md`、`AGENTS.md`、`version_design/design/refactor/README.md`、`outputs/experiment_registry.json`；**删除** `tools/verify_v11.py`（或移入本目录归档）。白名单之外的文件一律不得改；tag 不是文件改动，但必须打 annotated tag。

**v1.1.0 发布 Gate（只有两条）**

| # | 条件 | 判定 |
| :---: | :--- | :--- |
| 1 | 旧路径的 **L3 全表字段**（基线 JSON 同名键）与 v1.0.13 **逐值相同** | `verify_v11.py --milestone M4` → exit 0 |
| 2 | 新路径 smoke 能跑到 step6 | 同上 |

阶段 0 对照实验**不属于**发布 Gate。

---

## 8. 验收机制

### 8.1 `tools/verify_v11.py`

**定位**：v1.1 专用的一次性验收工具，把散文式验收变成 exit code。

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
& $py tools/verify_v11.py --milestone M4 --baseline version_design/design/refactor/baseline_v1.0.13.json
if ($LASTEXITCODE -ne 0) { throw "M4 验收未通过" }
```

**行为**

| 项 | 规定 |
| :--- | :--- |
| 输入 | `--milestone`、`--baseline`（JSON）、`--state-file` / `--output-dir`（M3–M5 必填；M0–M2 可缺省） |
| 读取 | 只读 state / manifest / 标签索引 / parquet 元数据 / baseline JSON（**不跑 pipeline、不改任何产物**） |
| 输出 | `verify_report.json`（含每项 `expected` / `actual` / `pass`）；不一致时打印差异并 `exit 1` |
| 基线来源 | 所有期望值从 `--baseline` 读取，**代码中不得出现数字字面量** |
| 依赖 | 只 import 标准库 + pandas/pyarrow；**不得被 `pipeline/**` 或 `data_prep/**` import** |
| 缺产物 | 一律 `exit 1`（**不得 skip**；`all([]) == True` 这类空真视为实现错误） |

**逐里程碑检查项（必须逐条实现，项数不得减少）**

| 里程碑 | verify_v11 必查项 |
| :---: | :--- |
| **M0** | ① `baseline_v1.0.13.json` 含 §8.3 全表字段且无空值；② 两个 raw 文件的 `sha256` 与 baseline 一致；③ `legacy_holding_samples_set_hash` 非空；④ `version_design/design/refactor/datebar_probe.json` 的 rows / n_symbols / min_idxd / max_idxd 与 baseline 一致（规模 ±1%）；⑤ `refactor_check.yaml` 能被 `load_params` 加载；⑥ `docs/14` 含 C1–C4 四个结论 |
| **M1** | ① samples 列集 == §3.1 且无 `target*` / `label*` 列；② `sample_id` 为 int64、唯一、严格升序；③ manifest 键完整；④ `params_hash` / `datebar_fingerprint` / `samples_set_hash` 重算相等；⑤ `max_samples` 生效时 `rows == max_samples` 且 `sampling_rate == rows / rows_before_cap`；⑥ 无 `sh000` / `sz399` / `sh950` 前缀；⑦ `legacy_holding` 的 `(idxs, idxd)` 集合指纹（§8.3 定义）== `baseline.legacy_holding_samples_set_hash` |
| **M2** | ① 每个内置标签文件存在且列集 == §3.2；② `rows == samples.rows`（全部标签，含 NaN 行）；③ `target` 无 ±inf；④ `_index.json` 每项 `params_hash` 重算相等、`coverage == non_null_rows / rows`；⑤ `target_holding_return` 的 coverage 与 baseline 记录一致（1e-12） |
| **M3** | ① `git diff --stat` 只含 M3 白名单；② 旧配置 `load_params` 成功且 `input.mode=holding_file`；③ samples 配置 `load_params` 成功（不误报校验错），且 `pytest` 覆盖 step1 的「未实现」拒绝用例；④ `check_raw_data --config refactor_check` → exit 0 |
| **M4** | ① state 中 L3 全部字段与 baseline 相等（计数精确、浮点 1e-12），且 step1 的 `sample_count` / `datebar_rows` / `target_stats` 与 baseline 相等；② samples 模式 step1 六个 `checks` 全 True、`details` 键完整、`dropped_no_target > 0`；③ 守恒恒等式成立（§4.9，含 step2 `dropped_samples`）；④ state 中 step1 的 `details.input_mode` / `samples_id` / `label_hash` 与当前配置推导值一致（不一致即说明未重跑） |
| **M5** | ① `tools/verify_v11.py` 已删除或已归档（`git ls-files` 无此路径）；② `outputs/experiment_registry.json` 只含 `status=completed` 的条目；③ `pytest tests -q` 与一次 smoke 的通过证据存在；④ `git cat-file -t v1.1.0` 返回 `tag` |

**稳定性约束**

| 约束 | 说明 |
| :--- | :--- |
| 不进入内核 | 不改 `pipeline/**`、`data_prep/**` 的任何行为 |
| 不做长期承诺 | 不写入 `docs/` 的稳定机制章节；不加入 `tools/frozen_experiments.yaml` |
| 可删除 | M5 归档或删除；删除后项目必须照常运行（M5 强制演练） |
| 只写一个文件 | 仅写 `verify_report.json` |

### 8.2 四条硬规则

| # | 规则 | 防的失败模式 |
| :---: | :--- | :--- |
| V-1 | 数字只能来自脚本输出；基线只能从 JSON 读 | 伪造对拍、手写"已对齐" |
| V-2 | 不得修改既有 check 的名称或判定语义 | check 挂了就删 check / 放宽断言 |
| V-3 | 每个里程碑有文件白名单，越界即停 | 顺手重构、契约漂移 |
| V-4 | 未通过 `verify_v11.py` 不得开始下一个里程碑 | 提前宣布完成 |

### 8.3 基线自包含要求

`baseline_v1.0.13.json` 必须内联（不接受「只记配置路径」）：

| 类别 | 字段 |
| :--- | :--- |
| 身份 | `git_tag`、`git_commit`、`run_date`、`label`、`base_version`、`version_subdir` |
| 窗口 | `window.lookback_days`、`window.chunk_size_slices` |
| 切分 | `time_split.*`（含 `buffer_trading_days`、`enforce_buffer`） |
| 筛选 | `feature_selection.*` |
| 手工特征 | `manual_features.*` |
| tsfresh | `tsfresh.fc_profile`、`chunk_size_tsfresh`、`tsfresh_n_jobs` |
| 模型 | `lgb.*`、`seed`、`smoke_size` |
| 数据身份 | 两个 raw 文件的 `sha256`、`rows`、`min/max` 键；`legacy_holding_samples_set_hash`（M0 由旧 `holding_clean` 的 `(idxs, idxd)` 集合计算：`sha256("\n".join(sorted(f"{idxs},{idxd}")))[:16]`，**不依赖** `sample_id` 编码） |
| 观测值 | L3 表的全部字段（M4 逐值比对）；`step1.target_stats`（mean/std/min/25%/50%/75%/max，供 L1 逐值比对）；`target_holding_return.coverage`（M0 记录，M2 比对） |

---

## 9. 已知限制与风险

### 9.1 明确不支持

| 项 | 影响 | 处置 |
| :--- | :--- | :--- |
| `bagging.py` 不支持 samples 模式 | 不能对新采样做 bagging | v1.1 明确不承诺；写死 60 日产物与 8 件门禁，改造属 v1.2 |
| 出场日跌停不顺延 | 高估可实现收益 | 写入文档与报告 |
| 停牌期间无法建仓 | 已在 §4.6 处理（`e` 顺延至第一个非停牌日） | — |
| 无 per-sample 标量特征通道 | `close/open−1` 这类单点值无法进模型 | v1.2 backlog |
| 脏 bar（54 行 `high<low`） | 指标轻微失真 | 本版不修、不阻断，记录数量 |

### 9.2 风险与应对

| 风险 | 影响 | 应对 |
| :--- | :--- | :--- |
| 样本量约为旧集 2.9 倍 | tsfresh 机时与内存不可控 | `max_samples` + `date_range` 闸门；M1 用真实计数替换外推 |
| `min_history_days < lookback_days` | 样本在 step2 被静默丢弃 | 校验 V6 |
| step2 full 会**覆写** `holding_clean.parquet` | 误把过滤后文件当样本全集 | `samples.parquet` 是唯一权威；step1 不读 `holding_clean` |
| 两套 datebar 规范化漂移 | `data_prep` 与 step1 产出不同数值 | 不变量 7；M4 一次性逐值比对 |
| NaN 标签被静默填 0 | 虚假 0 收益样本 | §4.9 守恒恒等式；不平即停 |
| 失败/探索运行污染台账 | registry 出现不可复现条目 | 收尾时按 state 的 `status` 过滤后重写 |
| 工作目录不一致 | 子目录执行时找不到文件 | 所有命令在仓库根执行，文档写明 |

### 9.3 `max_samples` 决策规则

| 条件 | 取值 |
| :--- | :--- |
| 最终 `fc_profile = compact` | `max_samples: null`（全量） |
| 需要使用 `medium` | **先在 20k 子样本上实测 step4 耗时与峰值内存**，外推后再定 cap |
| 设置了 cap | 必须按年等距抽样（算法见 §4.2）、在 manifest 记录 `rows_before_cap` / `sampling_rate` / `year_quota`，并在报告中声明 |

> 本节与 §4.2 是 `max_samples` 的唯一权威；`experiment_plan_v11.md` §3 只做引用，不重复定义。

参考量级（`5 kind × 样本数`）：

| 组合 | 相对基线 step4 成本 | 候选宽表内存峰值 |
| :--- | ---: | ---: |
| compact @ 240k（基线） | 1.0× | ≈1.5 GB |
| compact @ 690k | 2.9× | ≈4.3 GB |
| medium @ 690k | ≈6.2× | ≈14 GB |

---

## 10. 交付物

### 10.1 交付物清单

| 类别 | 内容 | 里程碑 |
| :--- | :--- | :---: |
| 新增代码 | `data_prep/**`（20 个文件，含 `_template.py` 与 4 个 `__init__.py`） | M1/M2 |
| 新增工具 | `tools/build_samples.py`、`tools/build_labels.py`、`tools/probe_datebar.py` | M0/M1/M2 |
| 新增验收工具 | `tools/verify_v11.py`（v1.1 专用，M5 归档或删除） | M0 |
| 修改代码 | `pipeline/config.py`（M1 增量字段 + M3 校验）、`adapters/sample_family.py`、`step2_slices.py`（仅计数键） | M1/M3 |
| 修改代码 | `pipeline/step1_clean.py`（M1 守卫 + M4 双模式）、`runner.py` | M1/M4 |
| 测试 | `pytest.ini`、`tests/conftest.py`、`tests/test_data_prep/**`、`tests/test_pipeline/test_step1_dual_mode.py` | M1–M4 |
| 文档 | `docs/14_datebar_probe.md`、`docs/15_data_contract.md`（含内置清单与扩展步骤） | M0/M3 |
| 文档更新 | `docs/00`、`01`、`07`、`10`、`11`、`AGENTS.md` | M4/M5 |
| 基线 | `baseline_v1.0.13.json`（自包含）、`configs/kernel_v11/check/refactor_check.yaml` | M0 |
| 运行时资产 | `data/prepared/{samples_id}/`、`data/labels/{samples_id}/`（不入库） | — |
| **开工前置** | `implementation_plan.md`、`code_agent_prompt.md`（非空；由设计方提供，实施 agent 不得自行生成） | M0 前 |

> 文件归属以**各里程碑的文件白名单**为准；§2.2 只描述最终布局，不构成许可。

---

## 附录：开工前检查

| # | 检查 | 命令 / 判据 |
| :---: | :--- | :--- |
| 0 | 交付包完整（**硬前置**） | `implementation_plan.md`、`code_agent_prompt.md` 均非空且已确认 |
| 1 | 分支正确 | `git status -sb` → `refactor/v1.1` |
| 2 | 工作区干净（除 `.gitignore`） | `git status --porcelain` |
| 3 | 解释器可用 | `& $py -c "import pandas, pyarrow, lightgbm, tsfresh, pytest"` |
| 4 | 数据就绪 | `& $py tools/check_raw_data.py --config configs/kernel_v11/check/refactor_check.yaml --check-columns` → 0 |
| 5 | 基线可读 | `baseline_v1.0.13.json` 含参数与指纹，不是只有路径 |
| 6 | 目录为空则先建 | `tests/`、`pytest.ini` 不存在时由 M1 创建 |

---

## 文档结束

**给实施 agent 的四句话**

1. 只读你当前里程碑的章节（见开头的阅读表），不要通读全文。
2. 按 **M0 → M5** 顺序做，每个里程碑用 `verify_v11.py` 判定通过，未通过不得前进。
3. 只改文件白名单内的文件；不改既有 check 的名称与语义；数字只能来自脚本输出。
4. 同一里程碑失败两次就回滚到上一个绿点（§7.3）；失败三次停下来交回人工，不要硬凑。
