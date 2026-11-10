# v1.1 重构实施设计 — 采样层与标签层解耦

> 状态: 设计定稿（代码与数据均已核对；仅剩 4 项人工抽样确认）
> 创建日期: 2026-11-05
> 父文档: `design_raw.md`（原始方案 v2.1，仅作留档）
> 权威约束: `AGENTS.md`、`docs/01_pipeline_contract.md`
> 目标: 在 step1–6 语义不变的前提下，把「样本从哪来」与「学什么」拆开，落地为可版本化、可复现的 samples / labels 资产。
> 适用仓库: 本仓库即 `ashare_signal_pipeline` 模板，无需回灌。

---

## 0. 结论速览

**方向可以通过，但不能按 `design_raw.md` 原样施工。** 五轮核对（代码逐行 + 真实数据只读探测 + 交叉 grep + 二次回归审计 + 可移植性审计）共发现 **36 处**需要修正的地方，其中 **14 处阻塞级**：

| 阻塞项 | 一句话 | 依据 |
| :--- | :--- | :--- |
| D2 | `R3` 让 step1 提取历史 OHLC，与 step2 的窗口切片完全重复，且与「step3 不变」自相矛盾 → 删除 | `step2_slices.py:104-127` |
| D3 | 标签入口放 step5 会与 step4 的筛选/缩尾标签不一致 → 标签改在 step1 注入 | `step4_features.py:679-690`、`:711` |
| D4 | `sample_id` 未定义，而全链路以它为键；且 `idxs` 是字符串，`idxs*1e8` 不成立 | `step2_slices.py:100`、`state.py` |
| D5 | 契约列 `symbol`/`date`/`is_st`/`is_limit_*`/`tradestatus` 在真实数据里全不存在；涨跌停可由 `marks` 精确判定 | F4–F8 |
| D7 | 单文件 `labels/{name}.parquet` 会被不同 samples 静默复用 | — |
| D10 | 只比 0A vs 0C 无法归因（采样与标签同时变） | — |
| D16 | 布林带触发约为旧样本的 2.9 倍，tsfresh 成本不可控 | 实测外推 6.9×10⁵ |
| D17 | 价格已是后复权，再乘 `xs` 会双重复权 | 四个价格锚点实测 |
| D18 | step2 full 模式会**覆写** `holding_clean.parquet`，样本权威事实必须是 `samples.parquet` | `step2_slices.py:197-201` |
| D19 | `tools/check_raw_data.py` 与 adapter 契约**强制要求 holding/holding_return**，samples 模式会直接判 invalid | `check_raw_data.py:60-88`、`sample_family.py:11-19` |
| D20 | bagging 写死 `slices_60d` / `ts_features_60d` 与 8 件产物门禁，samples 模式不可用 | `bagging.py:459-465` |
| D27 | step1 的 `holding_target_non_null = target.notna().all()` 与「标签层不删行」**必然冲突**：尾部样本 `future_20d` 不完整 → target 为 NaN → step1 自检失败、被判 failed | `step1_clean.py:60-66` |
| D28 | V2/V3 把 `holding_return` 绑死在 `holding_file` 模式，与 0D（`samples` + `holding_return`）**直接矛盾**，0D 无法配置 | §6.3 自相矛盾 |
| D29 | 若为 `marks` 而扩展 `datebar.parquet`，会打破 step1 的 `datebar_columns` **精确列表相等**检查，并使旧路径回归失真 | `step1_clean.py:39-45,65` |

**修正后的形态**：新增 `data_prep/`（不占 step 编号、不进入状态机）→ 产出 samples 与 labels；**只改 step1**（双模式 + 标签注入）、`config.py`、`runner.py`、`check_raw_data.py`、adapter 契约；step2–step6 零逻辑改动。原方案 Phase 4（改 step5 读标签）取消。`signal_date_selection_score` 在新路径中**删除**（D34）。

**交付结构**（§8）：拆成 **M0–M5** 六个里程碑，按"合并后仓库是否仍绿"划分；**M0–M3 零行为变化**，风险集中在 M4 一个 2–3 天窗口。阶段 0 对照实验**移出 v1.1.0**，独立为 `experiment_plan_v11.md`，发布 Gate 只依赖「旧路径逐值一致 + 新路径跑通」两条二值判定。

---

## 1. 核对事实

### 1.1 代码事实

| # | 事实 | 依据 |
| :---: | :--- | :--- |
| C1 | `step1_clean` / `step5_split` 是**单文件模块**；不存在 `configs/pipeline.yaml`、`configs/step5.yaml` | 目录结构 |
| C2 | step1 输出 `holding_clean.parquet`（含 `target`）与 `datebar.parquet`（规范化行情） | `step1_clean.py:52-56` |
| C3 | step1 把 `holding_return` 直接赋给 `target` | `step1_clean.py:24` |
| C4 | `holding_return` 全仓库**只出现在** `step1_clean.py` 与 `sample_family.py` → 把标签注入点收在 step1 是零污染的最小改动 | 全仓 grep 仅 5 处命中 |
| C5 | `holding_clean` 的消费方只有 step2/step4/step5/bagging，且都取固定列 | `step2:176,197`、`step4:709-711`、`step5:46`、`bagging:459` |
| C6 | step2 **已**按 `window.lookback_days` 生成「信号日及之前 N 个非停牌交易日」切片，窗口含信号日 | `step2_slices.py:104-127` |
| C7 | step3 只读 step2 的切片，从不读 dateBar；输出 `ts_features_{lookback}d{_smoke}.parquet` | `step3_indicators.py:compute_step3` |
| C8 | step4 读 `holding_clean[_smoke]` 的 `[sample_id, signal_date, target]`，用 `target` 做相关性筛选并算训练集 P1/P99 | `step4_features.py:709-711,679-690` |
| C9 | step5 从特征文件读 `target`，`target_raw = target`，再用 `clip_percentiles.json` 裁剪 | `step5_split.py:49,73,76` |
| C10 | step5 full 读 `holding_clean.parquet` 取 `signal_date_selection_score`，**有列才取**（已守卫） | `step5_split.py:52-57` |
| C11 | **step2 full 会覆写 `holding_clean.parquet` 为有效子集**；smoke 另写 `holding_clean_smoke.parquet` | `step2_slices.py:197-201` |
| C12 | 状态机「已完成即跳过」，只有 step4 校验输入签名 | `runner.py:46-63,90` |
| C13 | `update_step_state` 会删除所有编号更大的步骤状态 → 重跑 step1 自动作废 2–6 | `state.py:110-118` |
| C14 | `PipelineParams.from_dict` 只遍历固定 `nested` 白名单，未注册的顶层段被**静默忽略** | `config.py:415-446` |
| C15 | `version_payload()` 排除顶层 `data`，其余（含 `smoke_size`）参与哈希 → `--smoke` 取值会改变 `experiment_version` | `config.py:502-514`、`run_experiment_pipeline.py:21` |
| C16 | `_validate_params` 目前只要求 `buffer_trading_days >= lookback_days` | `config.py:521-525` |
| C17 | state 文件默认落在 `outputs/{version}/metadata/`，且与 `experiment_version` 绑定（不一致直接抛错） | `config.py:45-52`、`state.py:57-63` |
| C18 | `check_raw_data` 无条件要求 holding 文件存在，`HOLDING_REQUIRED_COLUMNS` 含 `holding_return` | `check_raw_data.py:60-88`、`sample_family.py:11-19` |
| C19 | bagging 的产物门禁写死 `slices_60d.parquet`、`ts_features_60d.parquet`、`features_selected_50.parquet` 等 8 件 | `bagging.py:459-465` |
| C20 | `docs/10_architecture.md` 声明「主线模块固定为 11 个文件」 | `10_architecture.md:7` |
| C21 | `docs/07_step_acceptance_criteria.md` 的 Step 1 已固定 6 个 check 名称 | `07:Step 1` |
| C22 | 仓库无 `tests/`、无 `requirements.txt` / `pyproject.toml`；`utils.load_holding_clean()` 无调用方 | 目录 + grep |
| C23 | 解释器：`C:/ProgramData/anaconda3/envs/factor311/python.exe`（3.11.13）；pandas 2.2.2 / numpy 1.26.4 / pyarrow 21 / lightgbm 4.6.0 / tsfresh 0.21.2 / **pytest 9.0.3** | 实测 |
| C24 | `data/*` 被 `.gitignore` 忽略（仅保留 `data/raw/.gitkeep`）→ samples/labels 属**运行时资产**，不入库 | `.gitignore:13-16` |
| C25 | **step1 的冒烟模式不抽样**：`compute_step1` 首行 `del mode`，永远写全量；`--smoke 500` 只影响 step2 的抽样 | `step1_clean.py:49`、`step2_slices.py:176-179` |
| C26 | step1 的 `datebar_columns` 是**精确列表相等**（9 列），不是子集判断 | `step1_clean.py:39-45,65` |
| C27 | step2 冒烟抽样 `random.sample(holding["sample_id"].tolist(), n)` 对**输入行顺序敏感** → 上游必须稳定排序才能复现 | `step2_slices.py:14-24` |
| C28 | step1 的 `holding_target_non_null` 要求 `target` **逐行非空**；`target_stats` 用 `describe()`（自动跳过 NaN）→ 该 check 是 samples 模式的硬门槛 | `step1_clean.py:59-66` |
| C29 | `save_resolved_params` 在 `run_step` **之前**执行 → 失败的运行也会写入 manifest 与 registry | `run_experiment_pipeline.py:20-23` |
| C30 | `check_raw_data` 用 `REPO_ROOT`（脚本位置），`run_experiment_pipeline` 用 `Path.cwd()` → 两者对「根目录」的定义不一致 | `check_raw_data.py:36`、`run_experiment_pipeline.py:19` |
| C31 | step5 先 `replace_inf_and_fill`（`fillna=True`）再判 `target` 是否为空 → **NaN 标签会被填成 0**，`target_rows_dropped` 实际恒为 0 | `step5_split.py:82-87`、`utils.py:24-38` |
| C32 | `signal_date_selection_score` **恒等于** `close/open − 1`（17,616 个样本上 `max|diff| = 6e-8`，Pearson=Spearman=1.0）；同日 `signal_date_selection_rank` 恒 1、`signal_date_selection_for_target` 恒 True | 实测（400 只股票） |

### 1.2 数据事实（`data/raw/` 实指外部目录，只读探测）

| # | 事实 |
| :---: | :--- |
| F1 | dateBar 是 **MultiIndex DataFrame**（`idxs, idxd`）；`reset_index()` 后 11 列：`idxs, idxd, open, high, low, close, volume, amount, xs, paused, marks` |
| F2 | 15,547,065 行 × 6090 代码 × 4384 交易日，2007-01-04 → 2025-01-14 |
| F3 | `idxs` 为**字符串**，6090 个全部匹配 `^(sh\|sz\|bj)\d{6}$`；其中指数 723（`sh000*` 311 + `sz399*` 412），可交易股票 **5367** |
| F4 | `idxd` 为 int（`YYYYMMDD`）；`paused` int16 0/1（3.19%）；价格列 `float32`；无 NaN；`(idxs, idxd)` **零重复** |
| F5 | **价格已后复权**：`close ≈ 原始价 × xs`。锚点：茅台 2025-01-14 `11411.01 / 7.7494 ≈ 1472`、2007-01-04 `368.25 / 4.29 ≈ 85.8`；平安 `46.79 / 1.0 ≈ 46.8`；万科、浦发同样吻合 → **不得再乘 `xs`** |
| F6 | `amount / (close × volume) ≈ 100`（volume 以手计，且与价格互逆复权） |
| F7 | `marks` 是涨跌停位掩码，43 种取值，bit ≥ 12 恒 0；**位序经价格等式实测确认**：涨停高 nibble = bit11..8、跌停低 nibble = bit7..4，nibble 内 `8=开盘, 4=最高, 2=最低, 1=收盘`。即 `开盘涨停 = marks & (1<<11)`、`收盘跌停 = marks & (1<<4)` |
| F8 | `data/raw/marks对照表.txt` 的位序与本设计**一致**（2048=开涨停、512=低涨停、256=收涨停，`收/低/开` 对应 nibble 值 1/2/8）；文件仅把这些组合标注为「按逻辑不应该出现」，属**规则意见**，不是标签错误 → 引用它作为交叉验证，不作为实现依据 |
| F9 | 价格完整性：`high<low` 54 行（0.00035%），`high<max(open,close)` 109 行，`low>min(open,close)` 26 行 → 存在极少量脏 bar，本版不修（step2 现状照旧），仅记录 |
| F10 | 停牌行 99.99% 同时 `volume<=0`；停牌占比随年份从 8.2%(2007) 降到 0.2%(2023+) |
| F11 | holding：240,467 行、22 列、5328 代码、2007-02-01 → 2025-01-13，与 dateBar 代码 **100% 匹配**；过滤后 240,257 行；`holding_trade_days` 中位 13 / P90 30 / P99 63；`holding_return` 均值 0.77% / σ 17.2% / 最大 761% |
| F12 | 布林带 20/2.0 首次上穿：397 只股票 50,844 次（≈127 次/只/18 年），外推全市场 **≈6.9×10⁵**，是旧样本的 **2.9 倍** |
| F13 | **`signal_date_selection_score` 不是人工打分，而是 `close/open − 1`（信号日日内涨幅）**：400 只股票 17,616 个样本上 `max|score − (close/open−1)| = 6e-8`（float32 精度），Pearson/Spearman 均为 1.0 → 它是**收盘即可知的合法市场量**，不是路径依赖产物 |
| F14 | 上市时间：`first_idxd` 中位 2013-11-16，5% 分位 2007-01-04 → 若 `min_history_days=60`，大量次新股样本会被剔除 |

---

## 2. 对 `design_raw.md` 的修正清单

| ID | `design_raw` 的说法 | 冲突 / 遗漏 | 修正（本文采用） | 级别 |
| :--- | :--- | :--- | :--- | :---: |
| D1 | `pipeline/step1_clean/`、`configs/pipeline.yaml`、`configs/step5.yaml`、`tests/` | 实际单文件模块 + 单继承配置；无测试目录 | 按单文件改；配置并入 `configs/default.yaml` 的新段；新建 `tests/` + `pytest.ini` | 中 |
| D2 | `R3`：step1 提取 `hist_*` 序列，step3 直接用，step2 不变 | C6/C7：step2 就是窗口生成器；`hist_*` 会让 24 万×100 天序列进 parquet，且与「step2 不变」互斥 | **删除 R3 与全部 `hist_*`**；历史窗口仍归 step2 | **阻塞** |
| D3 | step5 读 `data/labels/{column}` + join | C8/C9：step4 已用 `target` 筛特征与定缩尾，step5 的 `target` 来自特征文件 | **标签在 step1 决定**（`input.mode` + `target.*`），step1 注入 `holding_clean.target`；step4/5 零改动 | **阻塞** |
| D4 | 未提 `sample_id` | 全链路以其为键、禁止 `reset_index(drop=True)`；`idxs` 是字符串 | `sample_id = market*10^14 + code6*10^8 + idxd`（`sh=1,sz=2,bj=3`），int64，与 `(idxs, idxd)` 双射 | **阻塞** |
| D5 | 契约列 `symbol`/`signal_date:date`/`is_st`/`is_limit_*`/`tradestatus` | 真实列是 `idxs`(str)/`idxd`(int)/`paused`/`marks`；涨跌停可精确判定；ST 无独立列但 5% 限幅已体现在 `marks` | 用真实列名；可交易性 = `paused` + 历史长度 + `marks`（开盘涨停不可买）；不再用 ±10% 代理 | **阻塞** |
| D6 | 要求记录 `datebar_version` | dateBar 无版本字段，方案未给生成方式 | `datebar_fingerprint = sha256(bytes)[:12] + "_{rows}_{min}_{max}_{nsym}"` | 中 |
| D7 | `data/labels/{name}.parquet` 单文件 | samples 变化后同名文件被静默复用 | `data/labels/{samples_id}/{label}.parquet` + `_index.json` | **阻塞** |
| D8 | 未提状态幂等 | C12/C13：除 step4 外完成即跳过；但重跑 step1 会自动清空 2–6 | step1 `details` 写 `samples_id`/`fingerprint`/`label_hash`，`runner` 扩展 step1 签名校验 | 高 |
| D9 | step5 `target.transform: winsorize` | C8+C9 已是训练集分位 + 全量裁剪 → 再加一层等于三重裁剪 | 标签层**不缩尾**；沿用 `clip_percentiles.json`，报告写明缩尾发生在 step4/step5 | 中 |
| D10 | 只对比 0A vs 0C | 采样与标签同时变，差异不可归因 | 改为 2×2 四组 0A/0B/0C/0D + 4 个控制组 | **阻塞** |
| D11 | 未提缓冲带与标签前视 | C16 只校验 `buffer >= lookback`；`future_20d` 会让训练标签越过 `train_end` 之后的验证区间 | 校验 `buffer_trading_days >= max(lookback_days, horizon_days)` | 高 |
| D12 | 新增 YAML 顶层段 | C14 未注册的段被静默忽略 → 配置写了不生效且不报错 | 新段必须加 dataclass + 注册 `nested` + 进 `version_payload`；漏注册视为 bug | 高 |
| D13 | 新增 `docs/20/21/30` | docs 已占 00–13，`00_start_here` 是索引；AGENTS 目录表无 `data_prep/` | 用 `docs/14_datebar_probe.md`、`docs/15_data_contract.md`，并同步 `00`/`10`/`07`/`AGENTS` | 低 |
| D14 | 0A 与 samples 模式口径一致 | 旧路径有 `max_holding_days` 过滤；samples 模式无 `signal_date_selection_score` | 0A 固定 `max_holding_days: null`；缺失的手工特征写进报告，不静默补齐 | 中 |
| D15 | 未定义股票池 | 723 个指数会被当股票采样 | `universe.exclude_prefixes`，只保留 6 位股票代码 | 高 |
| D16 | 未评估样本量 | 实测外推 ≈6.9×10⁵（2.9 倍） | `max_samples` + `date_range` 闸门；M1 用真实计数替换外推 | **阻塞** |
| D17 | 未识别复权口径 | 价格已后复权，再乘 `xs` 双重复权 | 价格即最终口径；`xs` 只进指纹与审计 | **阻塞** |
| D18 | 未提 step2 覆写 | C11：full 模式把 `holding_clean.parquet` 改成有效子集 | 声明 `samples.parquet` 为样本权威事实；step1 必须可重跑且不依赖 `holding_clean` 现状 | **阻塞** |
| D19 | 未提就绪检查与 adapter | C18：samples 模式会被判 invalid | `check_raw_data` 与 adapter 契约改为按 `input.mode` 分支 | **阻塞** |
| D20 | 未提 bagging | C19：写死 60 日产物 + 8 件门禁 | v1.1.0 明确 bagging **仅支持 `holding_file` 模式**，samples 模式不承诺 | **阻塞** |
| D21 | 未提 check 名称稳定性 | C21：`docs/07` 与基线 fixture 已固定 6 个 check 名 | samples 模式复用同一组 key，语义用文档映射说明，不改名 | 高 |
| D22 | 未提版本哈希变化 | C15：新增配置段必然改变 `experiment_version`；`--smoke` 取值也参与哈希 | 回归**比对数值，不比对路径**；回归必须传相同 `--smoke 500` | 高 |
| D23 | 未提「11 个主线文件」 | C20 | `data_prep/` 定位为**前置层**，不加入主线 11 文件；同步修订 `docs/10` | 中 |
| D24 | 未提产物是否入库 | C24：`data/*` 全忽略 | samples/labels 是运行时资产；可复现性靠「配置 + 确定性 ID + 重建」 | 中 |
| D25 | `tradability.py` 独立模块 | 职责与 `universe`、`marks` 解码重叠，模块越多口径越易漂 | 合并为 `universe.py`（前缀/历史长度/停牌）+ `labels/base.py` 的 `next_tradable_position()` | 低 |
| D26 | `configs/data_prep.yaml` 独立配置 | 会与 pipeline 配置重复 `datebar_file`，产生漂移；采样参数不进 `experiment_version` | **改为单一 pipeline 配置**：新增 `data_prep:` / `input:` / `target:` 三段 | 高 |
| D27 | step1 的 `holding_target_non_null` 要求逐行非空 | C28：尾部标签必然 NaN，samples 模式**必定判 failed** | **step1 samples 模式在注入标签后丢弃 `target` 为空的行**，并把 `samples_rows / sample_count / dropped_no_target / target_coverage` 写入 `details`；`holding_target_non_null` 恒真 | **阻塞** |
| D28 | V2/V3 把 `holding_return` 绑死 `holding_file` | 与 0D 矛盾 | 校验改为：`target.source=holding_return` 允许两种 mode；`label_file` 才要求 `samples` | **阻塞** |
| D29 | 为 `marks` 扩展 `datebar.parquet` | C26 精确列相等检查会失败；两套规范化必然漂移 | **`datebar.parquet` 保持 9 列不动**；`marks` 只在 `data_prep` 从 raw dateBar 读取；`samples.parquet` 里的 `marks` 仅审计、逻辑禁止消费 | **阻塞** |
| D30 | 未定义 samples 输出行序 | C27：冒烟抽样依赖行序，不排序则同参数重跑可能抽到不同子集 | `samples.parquet` 与 step1 输出的 `holding_clean` **必须按 `sample_id` 升序落盘**（旧路径不改，保持原序） | 高 |
| D31 | 未定义 `is_complete` 与 `is_entry_blocked` 的关系 | §4.2 说 `is_complete` 含「可建仓」，§5.1 又单独置 NaN → 下游歧义 | `is_complete` 只表示**窗口在数据范围内完整**；`is_entry_blocked` 独立；`target` 是否可用统一由 `target.notna()` 判定 | 高 |
| D32 | `label_params_hash` 不含算法版本与 holding 指纹 | 改标签代码或换 holding 文件后，旧标签文件被静默复用 | 增加 `labels.label_spec_version`（改算法手动 +1）进哈希；`_index.json` 记录 `holding_fingerprint` 并在 0D 校验 | 高 |
| D33 | 「step5 会兜底丢弃无效 target」是错的 | C31：`fillna` 在前，NaN 标签会被填成 0 并通过检查 → 虚假 0 收益样本，且无任何告警 | 不改 step5（避免动 frozen 行为），改为**用恒等式验收**：`samples_rows − dropped_no_target − invalid_targets_dropped == 特征表行数`；不平即失败 | 高 |
| D34 | 曾把 `signal_date_selection_score` 判为「旧策略的路径依赖排序」并据此设计 R2「四组统一关掉」 | ①实测它等于 `close/open − 1`（F13），非路径依赖；②但**它的位置在架构上是错的**：价格衍生量寄存在 raw 交易记录里，绕过指标层直接穿透到 step5 的模型特征，且只存在于 `holding_file` 模式 → 正是本次重构要消除的「输入模式影响特征集」 | **删除**（v1.1 不提供该特征）：R2 四组统一 `include_signal_date_selection_score=false`；R1 原样保留以做回归；`0A(R1) − 0A(R2)` 天然给出「该特征的贡献」，零额外成本。重建该特征属**指标层扩展**，写入 v1.2 backlog，不在本次范围 | **高** |
| D35 | `LabelBuildParams` 用固定字段（`future_horizons`/`trend_quality_horizon`）描述标签参数 | 与「标签可插拔」直接矛盾：新增一个标签必须改 dataclass + 校验 + 前端配置三处 | 改为**按标签名分桶的参数字典** `labels.params: {label: {...}}`（与 `SampleBuildParams.params` 同构），校验改为**注册表驱动**：由标签声明 `default_params` 与 `horizon`，未知键报错 | **高** |
| D36 | `_validate_params` 的 V4 硬编码 `target_future_{h}d` / `target_trend_quality_{h}d` 名称 | 任何新标签都会被判非法，「可插拔」形同虚设 | V4 改为向 registry 查询 `horizon`：声明了就必须与配置一致，未声明则要求 `horizon_days=0`；不再出现任何标签名字面量 | **高** |

---

## 3. 目标架构

### 3.1 分层

```
┌──────────────────────────────────────────────────────────────┐
│ data_prep/（新增前置层，不占 step 编号，不进入状态机）        │
│  datebar.py        加载 + 规范化 + 指纹 + 逐 symbol 数组       │
│  universe.py       前缀过滤 / min_history / paused 掩码        │
│  samples/          base.py · registry.py · events/bollinger    │
│                    events/legacy_holding                       │
│  labels/           base.py · registry.py · future_return       │
│                    trend_quality · holding_return · cache      │
│  ids.py            sample_id 编解码 + 指纹 + 哈希              │
│  build_samples.py  编排 → data/prepared/{samples_id}/          │
│  label_builder.py  按需构造 → data/labels/{samples_id}/        │
└──────────────────────────────────────────────────────────────┘
        ↓ samples.parquet              ↓ {label}.parquet
┌──────────────────────────────────────────────────────────────┐
│ 执行主线（语义不变）                                          │
│  step1_clean（双模式）→ step2 → step3 → step4 → step5 → step6  │
└──────────────────────────────────────────────────────────────┘
```

**依赖方向（硬约束）**：`data_prep` **不得** import `pipeline`；`pipeline` 允许在函数内**惰性** import `data_prep`。`data_prep` 的公共函数只接受路径、primitive 与 dict，不依赖 `PipelineParams` 类型。

### 3.2 文件布局

```
├── data_prep/
│   ├── __init__.py
│   ├── ids.py
│   ├── datebar.py
│   ├── universe.py
│   ├── samples/{__init__.py, base.py, registry.py,
│   │            events/{__init__.py, bollinger.py, legacy_holding.py}}
│   ├── labels/{__init__.py, base.py, registry.py, future_return.py,
│   │            trend_quality.py, holding_return.py, cache.py}
│   ├── build_samples.py
│   └── label_builder.py
├── data/prepared/{samples_id}/{samples.parquet, manifest.json}
├── data/labels/{samples_id}/{_index.json, target_*.parquet}
├── pipeline/step1_clean.py          # 唯一改逻辑的 step
├── pipeline/config.py               # 新增三段配置 + 校验 + Paths 两字段
├── pipeline/runner.py               # 扩展 step1 签名校验
├── pipeline/adapters/sample_family.py  # 增加 samples 契约
├── tools/build_samples.py           # 新增 CLI
├── tools/build_labels.py            # 新增 CLI（可选预构建）
├── tools/probe_datebar.py           # 新增 CLI（M0 固化）
├── tools/check_raw_data.py          # 按 input.mode 分支
├── tests/ + pytest.ini              # 新增
├── configs/default.yaml             # 新增 input / target / data_prep 三段
└── docs/14_datebar_probe.md, docs/15_data_contract.md（+ 00/01/07/10/AGENTS 更新）
```

### 3.3 数据流

```
data/raw/dateBar.pkl ──datebar.py──▶ 规范化 + 指纹 + 逐 symbol 数组
        │
        ├─ samples/events/bollinger.py（只用 ≤ signal_date 的数据）
        │        └──▶ data/prepared/{samples_id}/samples.parquet（无标签）
        │
        └─ labels/*（只用 > signal_date 的数据）
                 └──▶ data/labels/{samples_id}/target_*.parquet
                                    │
step1（input.mode=samples）─────────┘  按 sample_id join 出 target
        └──▶ holding_clean.parquet（sample_id, idxs, signal_date, target）
                    │
step2 ─▶ step3 ─▶ step4 ─▶ step5 ─▶ step6（全部不变）
```

---

## 4. 契约

### 4.1 `samples.parquet`（骨架，无标签）

| 列 | 类型 | 来源 | 说明 |
| :--- | :--- | :--- | :--- |
| `sample_id` | int64 | `ids.py` | 唯一键，见 §4.4 |
| `idxs` | str | dateBar | 股票代码，已排除指数 |
| `idxd` | int32 | dateBar | 信号日 `YYYYMMDD` |
| `open/high/low/close/volume/amount` | float32 | dateBar | 信号日行情（审计用；step2 自行取窗口） |
| `is_paused` | bool | dateBar | 恒 False，显式化语义 |
| `marks` | int16 | dateBar | 信号日涨跌停掩码，**仅审计**；任何逻辑都不得从 samples 消费它（标签层从 raw dateBar 取，见 §5.4 / D29） |
| `sampling_method` | str | build | 如 `bollinger_breakout` |
| `sampling_params_hash` | str | build | 8 位 |
| `datebar_fingerprint` | str | build | 见 §4.4 |
| `created_at` | str | build | ISO |

硬约束：不含任何 `target*` / `label*` 列；不含历史窗口序列；**按 `sample_id` 升序落盘**（C27 / D30）。

`manifest.json` 记录：`samples_id`、`sampling_method`、`sampling_params`、`params_hash`、`datebar_fingerprint`、`universe`、`date_range`、`min_history_days`、`rows`、`n_symbols`、`created_at`、`code_version`（`git rev-parse --short HEAD`，取不到写 `""`）。

### 4.2 标签文件 `data/labels/{samples_id}/{label}.parquet`

| 列 | 类型 | 说明 |
| :--- | :--- | :--- |
| `sample_id` | int64 | 主键，与 samples 一致 |
| `idxs` / `signal_date` | str / int32 | 冗余，便于审计与集合校验 |
| `target` | float64 | 标签值 |
| `entry_idxd` / `exit_idxd` | int32 | 建仓日 / 结束日，不可得写 `-1` |
| `is_complete` | bool | **仅**表示未来窗口在数据范围内完整（不含建仓可行性） |
| `is_entry_blocked` | bool | 建仓日开盘涨停（`marks & (1<<11)`），独立于 `is_complete` |
| `label_name` / `label_params_hash` / `samples_id` / `datebar_fingerprint` / `created_at` | str | 元数据 |

`_index.json`：`{label: {params_hash, rows, non_null_rows, coverage, created_at}}`，用于不读 parquet 的缓存判定。

### 4.3 step1 输出契约（两模式同构）

| 列 | `holding_file` | `samples` |
| :--- | :--- | :--- |
| `sample_id` | 原 parquet 索引（不变） | `ids.derive` |
| `idxs` | 原样 | 原样 |
| `signal_date` | 原样 int | 原样 int |
| `target` | `holding_return` | `target.label` 的值 |
| 其他原列 | 保留 | 不保留 |

- 文件名保持 `holding_clean.parquet`（**不改名**），以免触碰 step2/4/5 与 bagging；
- **samples 模式必须丢弃 `target` 为空的行**（D27）：否则 `holding_target_non_null` 必然失败。`details` 用两组计数区分口径：`samples_rows`（samples.parquet 全集）、`sample_count`（注入标签后入管道行数）、`dropped_no_target`、`target_coverage`；
- 两模式输出均**按 `sample_id` 升序**（samples 模式强制；holding 模式保持原序以对齐基线回归，D30）；
- `checks` 的 6 个 key **两模式完全一致**（`holding_exists`/`holding_columns`/`holding_target_non_null`/`datebar_exists`/`datebar_columns`/`datebar_sorted`），语义映射写入 `docs/07`：samples 模式下 `holding_exists` 指「干净样本表已落盘」，`holding_target_non_null` 指「入管道样本的 target 已全部非空」；
- `details` 两模式同键：`input_mode`、`coupling_mode`、`samples_rows`、`sample_count`、`dropped_no_target`、`target_coverage`、`signal_date_min/max`、`target_stats`、`datebar_rows`、`datebar_fingerprint`、`samples_id`、`samples_set_hash`、`label_name`、`label_hash`、`label_non_null_ratio`；holding 模式下 `samples_*`/`label_*` 一律为 `""`（**不要用 `null`**，避免 JSON 类型漂移影响签名比较）。

### 4.4 标识与哈希

| 标识 | 计算 | 用途 |
| :--- | :--- | :--- |
| `sample_id` | `market*10^14 + code6*10^8 + idxd`，`sh=1,sz=2,bj=3` | 唯一键；与 `(idxs, idxd)` 双射；`sh000001` 与 `sz000001` 不冲突 |
| `datebar_fingerprint` | `sha256(file bytes)[:12] + "_{rows}_{min_idxd}_{max_idxd}_{nsym}"` | 行情变更即失效 |
| `sampling_params_hash` | `sha256(canonical_json({method, params, universe, date_range, min_history_days}))[:8]` | 采样参数变更即失效 |
| `samples_id` | `{method}_{params_hash}_{fingerprint[:8]}` | 目录名；`target.samples_id: auto` 时由 step1 推导 |
| `label_params_hash` | `sha256(canonical_json({label, label_spec_version, params, samples_id, fingerprint, holding_fingerprint_if_used}))[:8]` | 标签变更即失效（D32） |
| `samples_set_hash` | `sha256(np.sort(sample_id).astype("<i8").tobytes())[:16]` | 缓存命中判定；step1 也用它校验 samples 未被替换 |

**缓存策略**：`label_params_hash` 与 `samples_set_hash` 均一致 → 直接复用；任一不一致 → **整表重建**。不做增量补算（标签为纯向量化计算，重算成本远低于一致性风险）。

**step1 的双重校验**（D32 补充）：samples 模式下 step1 除比对 manifest 的 `datebar_fingerprint` 外，还必须重算 `samples_set_hash` 并与 manifest 比对；不一致直接报错（说明 samples.parquet 被替换或损坏），不得静默继续。

**`label_spec_version`**：`LabelBuildParams` 的整数字段，默认 `1`；任何改变标签数值口径的代码修改都必须 +1，否则旧标签文件会被复用。

**`samples_id: auto` 的实现约束**：step1 必须用与 `build_samples` **同一个函数**推导，不得各写一份；显式给出时，若与推导值不一致直接报错（提示配置与产物不匹配）。

### 4.5 路径与入库边界

| 路径 | 是否入库 | 说明 |
| :--- | :---: | :--- |
| `configs/**`（除 `configs/local/`） | 是 | 采样/标签配置的唯一来源 |
| `data/raw/**` | 否（仅 `.gitkeep`） | 只读输入 |
| `data/prepared/**`、`data/labels/**` | 否（`.gitignore: data/*`） | 运行时资产；靠配置 + 确定性 ID 重建 |
| `outputs/experiment_registry.json` | 是 | 台账；v1.1.0 收尾时用可跟踪配置重跑一次再提交 |

### 4.6 防泄漏与边界

| 约束 | 落点 |
| :--- | :--- |
| 采样只用 `signal_date` 当日及以前（布林带仅用截至当日收盘） | `events/bollinger.py` |
| 标签只用 `signal_date` 之后（建仓日 `> signal_date`） | `labels/*` |
| 训练标签不得侵入验证区间 | `buffer_trading_days >= max(lookback_days, horizon_days)` |
| 统计量只在训练集内算 | 沿用 step4 `clip_percentiles.json`，不新增 |
| 冒烟指标不作结论 | 不变 |

### 4.7 标签只有一条路：内核内实现 + 注册

**不做 `external_file`**（Q8=B）。理由：外部标签一旦允许，内核就无法再对"防泄漏"负责，而这条红线是本仓库存在的前提；为了短平快的便利去换一条不可验证的入口，成本收益不成立。

因此标签的形态是唯一的：

| 项 | 规定 |
| :--- | :--- |
| 唯一来源 | `data_prep/labels/` 下注册的标签；`target.source ∈ {holding_return, label_file}` |
| 新增方式 | 复制 `_template.py` → `@register_label` → 配置引用（§7.1），**不改任何 step** |
| 内置清单 | `docs/15_data_contract.md` 维护一张表：`name` / 定义 / `horizon` / 覆盖率口径 / 适用场景；新增标签必须同时更新该表 |
| 沉淀节奏 | 实验里反复用到的标签再"转正"进内核并补文档；一次性需求用 `_template.py` 在自己分支里写完即可，不必上库 |
| 防泄漏 | 由 `LabelDefinition` 接口强制（只暴露 `entry_position` / `forward_window`，不交原始 `bars`） |

这样"任意样本"的兑现方式是：**样本随便换（samples.parquet + datebar），标签要么用内置、要么写 60 行模板**。比开一个不可验证的口子更符合本项目定位。

---

## 5. 标签口径

记 `s = signal_date`；`pos(s)` = 该 symbol 在非停牌序列中的位置；`e = pos(s)+1`（`signal_date` 之后第一个非停牌交易日）。

### 5.1 `target_future_{h}d`（h ∈ {5, 10, 20, 40}）

```text
entry = open(e)                    # 与「收盘出信号、次日开盘执行」一致
exit  = close(e + h)               # 相对 e 的第 h 个非停牌交易日
target = exit / entry - 1
```

- `e` 不存在或 `e+h` 越界 → `target = NaN`、`is_complete = False`（`is_complete` 只反映窗口完整性）；
- `marks` 显示 `e` 日**开盘涨停**（`marks & (1<<11)`）→ `is_entry_blocked = True`；`drop_blocked_entries: true` 时置 `target = NaN`，但**不修改 `is_complete`**；下游一律以 `target.notna()` 判断可用性；
- **`e` 取的是 `signal_date` 之后第一个非停牌日，不是「次日」**：若 `s+1` 停牌，则顺延，与 step2 的「连续拼接」口径一致；
- 出场日收盘跌停不顺延（本版限制，写入报告）；
- 停牌日不参与计数；
- 不缩尾、不截断、不分类。

### 5.2 `target_trend_quality_20d`

```text
r  = target_future_20d
c  = close(e .. e+20)
md = max_i(1 - c_i / max(c_e..c_i))
target = r / md      if md > 1e-6 else NaN
```

单调上涨（`md≈0`）给 NaN 而非极大值，避免污染训练。

### 5.3 `target_holding_return`（诊断标签）

从 holding 按 `(idxs, signal_date)` 对齐 `holding_return`，前置过滤与 step1 一致（`is_closed & entry_execution_price>0 & holding_return.notna`）。

三条限定必须写进报告：

1. 覆盖不完整且**非随机**（只有「突破后跌破中轨」的完整交易才有记录）；
2. 只能回答「旧标签在新采样集上是否还成立」，**不能**用于比较采样优劣；
3. 覆盖率 `non_null_rows / samples_rows` 是一等指标。

### 5.4 `marks` 解码（实测位序）

```python
open_limit_up   = bool(marks & (1 << 11))   # 开盘涨停
close_limit_up  = bool(marks & (1 << 8))    # 收盘涨停
open_limit_down = bool(marks & (1 << 7))    # 开盘跌停
close_limit_down= bool(marks & (1 << 4))    # 收盘跌停
```

验证依据：`zt=0b1100 → open==high`、`zt=0b0101 → high==close`、`zt=0b1111 → 四价全等`、`dt=0b1010 → open==low`、`dt=0b0011 → low==close`，且中位收益分别为 +10.0% / −10.0%。`marks对照表.txt` 与本解码**完全一致**（2048=开涨停、512=低涨停、256=收涨停）；该文件把部分组合标注为「按逻辑不应该出现」只是规则意见，不影响位序。位序最终以 C4 的抽样互验为准。

### 5.5 NaN 与丢弃

- 标签层只标记 `is_complete=False`，**不删行**；
- **真正的丢弃发生两处**：step1（samples 模式下丢弃 `target` 为空的行，D27）与 step4（`invalid_targets_dropped`）；
- **step5 不是兜底**（D33/C31）：它先 `replace_inf_and_fill(..., fillna=True)` 把 NaN 填成 0，再判空，因此 `target_rows_dropped` 恒为 0。任何漏到 step5 的 NaN 标签都会变成 **0 收益样本**且不报警；
- 因此必须用恒等式验收（M4 与实验每组必查）：

  ```text
  samples_rows(samples.parquet)
    − dropped_no_target(step1)
    − invalid_targets_dropped(step4)
    == 特征表行数(step4 output)
  ```

  不平即说明有 NaN 标签被填成 0，结论作废；
- 每次运行把 `label_non_null_ratio`、`dropped_no_target`、`invalid_targets_dropped` 写入 state，作为可观测指标。

---

## 6. 配置契约

### 6.1 新增/修改的 dataclass（`pipeline/config.py`）

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
    on_missing_samples: str = "error"    # error | build
    on_missing_label: str = "build"      # build | error

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
    spec_version: int = 1                       # 全局口径版本（兜底）
    drop_blocked_entries: bool = True
    # 按标签名分桶，与 SampleBuildParams 同构；新增标签无需改 dataclass
    params: Dict[str, Dict[str, object]] = field(
        default_factory=lambda: {
            "future_return": {"horizons": [5, 10, 20, 40]},
            "trend_quality": {"horizon": 20},
        }
    )

@dataclass
class DataPrepParams:
    date_range_start: Optional[int] = None
    date_range_end: Optional[int] = None
    universe: UniverseParams = field(default_factory=UniverseParams)
    samples: SampleBuildParams = field(default_factory=SampleBuildParams)
    labels: LabelBuildParams = field(default_factory=LabelBuildParams)
```

`ManualFeatureParams` **不新增字段**（D34 改为删除该特征，见 §2）。`manual_features` 现有五项里，`date/year/month` 由 `signal_date` 派生、`non_trading_ratio` 由 step2 从 datebar 计算——都由内核产出、两模式对称；唯独 `include_signal_date_selection_score` 是从 raw 交易记录**透传**进来的，是本次唯一移除的例外。

`RawDataParams` 增加 `prepared_dir: str = "data/prepared"`、`labels_dir: str = "data/labels"`；`Paths` 增加 `prepared_root_dir`、`labels_root_dir`（由 `build_paths` 解析，`ensure_directories` 创建根目录，不创建 `samples_id` 子目录）。

必须同时改 4 处（漏一处即为 bug，正是 C14 的静默失败）：

1. `PipelineParams` 字段声明；
2. `from_dict` 的 `nested` 白名单加 `input` / `target` / `data_prep`；
3. `_validate_params` 加 §6.3 规则；
4. `version_payload()` —— 保持默认包含（不要像 `data` 那样排除）。

### 6.2 YAML

```yaml
data:
  holding_file: data/raw/holding_stats_all.parquet
  datebar_file: data/raw/dateBar.pkl
  prepared_dir: data/prepared
  labels_dir: data/labels

# 旧路径（默认；行为与 v1.0.13 完全一致）
input:
  mode: holding_file
target:
  source: holding_return

# 新路径（内核内标签）
#input:
#  mode: samples
#target:
#  source: label_file
#  label: target_future_20d
#  horizon_days: 20
#  samples_id: auto
#  on_missing_samples: error
#  on_missing_label: build

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
    spec_version: 1
    drop_blocked_entries: true
    params:
      future_return: {horizons: [5, 10, 20, 40]}
      trend_quality: {horizon: 20}
      # 新增标签只需在这里加一个键，无需改 dataclass
```

### 6.3 校验规则（`_validate_params`）

| # | 规则 | 级别 |
| :---: | :--- | :--- |
| V1 | `input.mode ∈ {holding_file, samples}`；`target.source ∈ {holding_return, label_file}` | error |
| V2 | `holding_file` 模式：`target.source=holding_return` 时 `label` 必须为空；若填了 `label_file` 则拒绝（旧路径不读标签文件） | error |
| V3 | `target.source=label_file` 时**必须** `input.mode=samples`（反向不成立）；`label` 非空、`on_missing_* ∈ {build,error}` | error |
| V4 | `target.source=label_file` 时**向 registry 查询**该 label 的 `horizon` 声明：声明了则 `horizon_days` 必须相等；未声明（如 `target_holding_return`）则必须为 0；label 不在 registry → 报错并列出可用名 | error |
| V5 | `enforce_buffer=true` 时 `buffer_trading_days >= max(lookback_days, horizon_days)` | error |
| V6 | `data_prep.universe.min_history_days >= window.lookback_days` | error（否则样本会在 step2 被静默丢弃） |
| V7 | `samples.method ∈ registry`，`params` 值均为正数 | error |
| V8 | `samples_id != auto` 时需匹配 `^[a-z0-9_]+$`，且必须等于推导值 | error |
| V9 | 现有配置（无新段）必须零改动可加载，且行为与 v1.0.13 一致 | 回归 |
| V10 | `samples.method=legacy_holding` 时 `sample_filter.max_holding_days` 必须为 `null`（否则 0A/0B 样本集无法保证相同） | error |
| V11 | `samples` 模式下设置 `manual_features.include_signal_date_selection_score=true` → 允许但必须打印「该特征不可得，将跳过」警告；对照实验要求显式 `false`（见 §9.1） | warn |
| V12 | `label_spec_version >= 1` 且为整数 | error |

### 6.4 版本哈希

| 变更 | 影响 `experiment_version` | 说明 |
| :--- | :---: | :--- |
| `input.mode` / `target.label` / `target.horizon_days` | 是 | 不同标签必须隔离输出 |
| `data_prep.*` | 是 | 采样参数变化即新样本集 |
| `data.*` 路径 | 否 | 沿用现有排除规则 |
| `--smoke` 取值 | 是 | `smoke_size` 在 payload 内（C15）→ 回归必须传相同值 |
| 新增配置段本身 | 是 | 一次性；回归比对数值而非路径 |

---

## 7. 模块职责与不变量

| 模块 | 职责 | 接口 |
| :--- | :--- | :--- |
| `data_prep/ids.py` | `sample_id` 编解码、三类哈希、指纹 | `derive_sample_id(idxs, idxd)`、`parse_sample_id()`、`derive_datebar_fingerprint(path)`、`derive_samples_id(...)` |
| `data_prep/datebar.py` | 加载、规范化（小写列、int32/float32、排序）、逐 symbol 数组 | `load_datebar(path) -> (df, fingerprint)`、`build_symbol_arrays(df)` |
| `data_prep/universe.py` | 前缀排除、`min_history_days`、`paused` 掩码 | `filter_universe(df, params) -> df`、`valid_positions(sym_arrays)` |
| `data_prep/samples/base.py` | 采样基类 | `generate(sym_arrays, params) -> ndarray[(idxs, idxd)]` |
| `data_prep/samples/events/bollinger.py` | 首次上穿布林带上轨 | 见 §8 M1 |
| `data_prep/samples/events/legacy_holding.py` | 旧 holding 的 `(idxs, signal_date)` 重贴标 | 同上 |
| `data_prep/labels/base.py` | 标签基类 + `next_tradable_position()` | `compute(samples, sym_arrays, params) -> DataFrame` |
| `data_prep/labels/future_return.py` | 多 horizon 一次遍历、分别落盘 | — |
| `data_prep/labels/trend_quality.py` / `holding_return.py` | §5.2 / §5.3 | — |
| `data_prep/labels/cache.py` | 索引、校验、读写、重建 | `is_valid / load / save / rebuild` |
| `data_prep/build_samples.py` / `label_builder.py` | 编排 | `build(config_dict, ...) -> samples_id`、`ensure(...)` |
| `tools/build_samples.py` / `build_labels.py` / `probe_datebar.py` | CLI | `--config`、`--dry-run`、`--json` |
| `pipeline/step1_clean.py` | 双模式 + 标签注入 | `compute_step1` |
| ~~`pipeline/step5_split.py`~~ | **不改**（D34 改为删除特征，无需开关） | — |
| `data_prep/labels/_template.py` | 新增标签的复制模板（含契约注释与最小实现） | — |

### 7.1 扩展点：内核内新增一个标签

标签与采样都是"注册表 + 基类"，二者结构对称。新增标签**不需要改 step1/step5/step4**，只需三步：

**① 新建实现**（复制 `data_prep/labels/_template.py`）：

```python
@register_label
class VolatilityLabel(LabelDefinition):
    name = "target_vol_20d"
    spec_version = 1                    # 改数值口径时必须 +1
    horizon = 20                        # 用于 buffer 校验；无前视则 None
    default_params = {"window": 20}

    def compute(self, samples, bars, params, ctx) -> pd.DataFrame:
        # 只允许通过 ctx.forward_window(pos, n) / ctx.entry_position(pos) 取未来数据
        ...
        return frame  # 必须含 §4.2 的全部列
```

基类 `LabelDefinition` 强制：
- `compute` 返回列契约（§4.2）由 `validate_label_frame()` 检查，缺列即报错；
- 未来数据只能通过 `ctx.forward_window()` 取，**不允许直接访问 `bars` 原始数组**（在类型/接口层把"用到过去"变成写不出来的事）；
- `horizon` 参与 `buffer_trading_days >= max(lookback_days, horizon)` 校验。

**② 配置引用**（无需改 dataclass，D35）：

```yaml
target:
  source: label_file
  label: target_vol_20d
  horizon_days: 20
data_prep:
  labels:
    params:
      vol: {window: 20}       # 键名 = 该标签在 register_label 里声明的 params_key
```

> `params` 的键是标签**声明的 `params_key`**（不是文件名、也不是完整 label 名）；内置标签的 `params_key` 分别为 `future_return` / `trend_quality` / `holding_return`。未声明的键在做 §6.3 校验时报错并列可用键名。

**③ 缓存自动生效**：`label_params_hash` 已包含 `label + spec_version + 合并后的 params + samples_id + datebar_fingerprint`，配置一变即重建，无需手工清缓存。

`_validate_params` 的 V4 因此改为**向 registry 查询**（D36），代码里不再出现任何标签名。

同样的流程适用于新增采样：`data_prep/samples/events/<name>.py` + `@register_sample` + `data_prep.samples.method: <name>`，step1–6 全部不用改。

**不变量**

1. `idxs` 全链路为字符串；`signal_date`/`idxd` 全链路为 int。
2. `sample_id` 单调可排序、与 `(idxs, idxd)` 双射；禁止 `reset_index(drop=True)`。
3. `data_prep` 不 import `pipeline`；`pipeline` 只在函数内惰性 import `data_prep`。
4. `holding_clean.parquet` 的文件名与必需列不变。
5. step1 的 `checks` 键集合两模式一致；`details` 值类型一致（缺省用 `""`，不用 `null`）。
6. 任何路径重复计算（`datebar` 两次加载）都必须显式说明，不得隐式。
7. **`processed/datebar.parquet` 恒为 9 列**（`idxs/idxd/OHLCV/amount/paused`），两模式都由 `_clean_datebar` 生成；`marks`/`xs` 只在 `data_prep` 内从 raw dateBar 消费（D29）。
8. `data_prep/datebar.py` 与 `step1._clean_datebar` 是**两套独立实现**，必须产出数值逐值相同的 9 列；M4 用一次性比对脚本证明，之后不再重复比对。
9. `samples.parquet`、step1 的 `holding_clean` 必须按 `sample_id` 升序落盘（D30）。
10. `data_prep` 的 CLI 必须先 `load_params`，再把 `asdict(params)` 的子集以 **dict** 传入 `data_prep`；禁止 `data_prep` 直接解析 YAML。

---

## 8. 交付切分（M0–M5）

> 执行环境：`C:/ProgramData/anaconda3/envs/factor311/python.exe`。所有命令在工作区根目录执行。
> 文件级顺序与验收命令见 `implementation_plan.md`；执行边界见 `code_agent_prompt.md`。

### 8.1 切分原则

按**合并点**切，不按主题切：每一个里程碑单独合入后，仓库必须是绿的、旧行为必须是可证的。

| 里程碑 | 内容 | 天 | 行为变化 | 可独立合入 |
| :---: | :--- | ---: | :---: | :---: |
| **M0** | 基线自包含 + 探测固化 | 0.5–1 | 无 | 是 |
| **M1** | `data_prep/` samples 层（纯新增） | 3–4 | 无 | 是 |
| **M2** | `data_prep/` labels 层（纯新增） | 3–4 | 无 | 是 |
| **M3** | 接线准备：config + adapters + check + step2 计数键 | 1.5–2 | **无**（新字段默认不生效） | 是 |
| **M4** | step1 双模式 + runner 签名 + R1 回归 | 2–3 | **有（唯一一处）** | 是 |
| **M5** | 收尾与发布 | 1 | 无 | 是 |
| **E** | 阶段 0 对照实验 | 2–3 | 无（只出报告） | **不在 v1.1.0 内**，见 `experiment_plan_v11.md` |

**关键性质**：M0–M3 全部"零行为变化"，可以放心交给 agent 独立跑；风险集中在 M4 一个 2–3 天的窗口。

### 8.2 每个里程碑的唯一验收

用 `tools/verify_v11.py` 把验收变成 exit code，**替代散文式验收**：

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
& $py tools/verify_v11.py --milestone M3 --baseline version_design/design/refactor/baseline_v1.0.13.json
if ($LASTEXITCODE -ne 0) { throw "M3 验收未通过" }
```

规则：

| # | 规则 |
| :---: | :--- |
| V-1 | 报告/PR 里的数字**只能来自脚本输出**，禁止手写；基线数字只能从 JSON 读取，禁止在代码里硬编码字面量 |
| V-2 | **不得修改任何既有 check 的名称或判定语义**（`holding_target_non_null` 等）。check 不通过时修数据，不得改断言 |
| V-3 | 每个里程碑有**文件白名单**；`git diff --stat` 越界即停 |
| V-4 | 里程碑未通过 `verify_v11.py` 时，**不得开始下一个** |

### 8.3 `tools/verify_v11.py` 的定位（重要）

它是**v1.1 专用的一次性工具**，不是内核能力：

| 约束 | 说明 |
| :--- | :--- |
| 不得被 pipeline 引用 | `pipeline/**` 与 `data_prep/**` **禁止** import 它；它只 import 它们 |
| 只读 | 只读 state / manifest / parquet / baseline JSON，不写业务产物；仅写 `verify_report.json` |
| 可删除 | M5 收尾时二选一：① 移入 `version_design/design/refactor/` 归档；② 直接删除。删除后 `pytest` 与 `run_experiment_pipeline.py` 必须不受任何影响 |
| 不做长期承诺 | 不写进 `docs/` 的稳定机制章节，不加入 `tools/frozen_experiments.yaml` |

M5 验收必须包含一条**删除演练**：临时移走 `verify_v11.py` 后，`pytest tests -q` 与一次 smoke step1–6 仍全部通过。

---

### M0：基线自包含 + 探测固化（只读 + 文档）

**已完成**（本次只读探测，结论见 §1.2 与附录 A）。剩余工作：

1. 新增 `tools/probe_datebar.py`，能复现附录 A 的全部指标（输出 `--json`）；
2. 写出 `docs/14_datebar_probe.md`；
3. 完成 4 项人工确认：

| # | 确认项 | 通过标准 |
| :---: | :--- | :--- |
| C1 | 复权口径 | 抽 1 只已知除权股（如 sh600000 2010-06-10），与行情软件的前/后复权价对照，确认「价格已后复权、无需再乘 `xs`」 |
| C2 | `idxs` 格式稳定性 | 确认未来新增代码仍为 `sh/sz/bj + 6 位`；若不满足，`ids.py` 需记录 `unmapped` 并以错误退出 |
| C3 | 尾部缺口 | 记录 `is_complete=False` 的样本比例（预期集中在 2024-10 之后） |
| C4 | `marks` 位序 | 抽 10 个涨/跌停 bar，与 `pct_change` 及板块限幅（10%/20%/5%）互验 §5.4 的位序 |

**验收**：`probe_datebar.py --json` 输出与附录 A 一致（±1%）；C1–C4 均有书面结论；`baseline_v1.0.13.json` 已内联全部参数快照与两个 raw 文件 sha256（见 §14 Q3）。

**文件白名单**：`tools/probe_datebar.py`（新）、`docs/14_datebar_probe.md`（新）、`version_design/design/refactor/baseline_v1.0.13.json`（改）、`configs/kernel_v11/check/refactor_check.yaml`（新，相对路径版）。

**Commit**：`feat(tools): add datebar probe script`、`docs: add datebar probe report`、`chore: inline baseline params and data fingerprints`

### M1：samples 骨架（纯新增）

**做什么**

1. `ids.py`：`sample_id` 编解码 + 指纹 + `samples_id` 推导；单测覆盖 `sh000001` vs `sz000001` 不冲突、`派生→解析` 往返一致、int64 不溢出；
2. `datebar.py`：**必须走 `reset_index()`**（`idxs`/`idxd` 在索引里）；列名小写；`idxd` int32；按 `(idxs, idxd)` mergesort 排序；
3. `universe.py`：前缀排除（指数）→ `min_history_days`（默认 60，与 `window.lookback_days` 对齐）；
4. `events/bollinger.py`：
   - 逐 symbol、仅非停牌 `close`；
   - `ma = rolling(ma_period).mean()`、`sd = rolling(ma_period).std(ddof=1)`、`upper = ma + std_mult*sd`；
   - **价格直接使用，不乘 `xs`**；
   - 事件 = `close_t > upper_t` 且 `close_{t-1} <= upper_{t-1}`，要求前值非 NaN（即至少 `ma_period+1` 个观测）；
   - `max_samples` 超限时按 `(idxs, idxd)` 排序后分年等距抽样并打印抽样率；
5. `events/legacy_holding.py`：读旧 holding，**复制 step1 的过滤条件**（`is_closed & entry_execution_price>0 & holding_return.notna`）后取 `(idxs, signal_date)`；
6. `build_samples.py` + `tools/build_samples.py`：写 `samples.parquet` + `manifest.json`；**按 `sample_id` 升序落盘**，`manifest` 必须含 `samples_set_hash`、`rows`、`n_symbols`、`params_hash`、`datebar_fingerprint`、`code_version`；
7. 配置：`configs/default.yaml` 加 `data_prep` / `input` / `target` 三段（先改 dataclass 再改 YAML）。

**不做什么**：不碰 `pipeline/`；不写标签；不生成 `hist_*`。

**验收**

1. 列契约符合 §4.1，无 `target*`；
2. `sample_id` 唯一、非空、可逆；
3. 同配置重跑两次，`samples.parquet` **逐行逐值相等**（含行序；忽略 `created_at`），`samples_set_hash` 相同；
4. **规模闸门**：真实全量计数替代外推（预期 6–7×10⁵）；若 > 4×10⁵ 且 `max_samples` 仍为 null，必须先定抽样/区间方案；
5. 输出中不含指数前缀；
6. `legacy_holding` 的 `(idxs, signal_date)` 集合与旧 `holding_clean` **完全相同**（用集合比较，不用 `sample_id`），并已验证 `max_holding_days` 只能是 `null`（V10）；
7. `samples.parquet` 严格升序（`is_monotonic_increasing`）；
8. `pytest tests/test_data_prep -q` 全绿；
9. `pipeline/step1–6` 零改动，旧路径仍可跑。

**Commit**：`feat(data_prep): add ids and datebar loader`、`feat(data_prep): add universe filter`、`feat(data_prep): add bollinger breakout sampler`、`feat(data_prep): add legacy holding sampler`、`feat(tools): add build_samples cli`、`test(data_prep): add sampler tests`

### M2：labels 与缓存（纯新增）

**做什么**

1. `labels/base.py`（含 `next_tradable_position`）、`registry.py`；
2. `future_return.py`：一次遍历算全部 horizon，分别落盘；`marks` 判定建仓可行性；
3. `trend_quality.py`、`holding_return.py`；
4. `cache.py`：`{samples_id}` 目录 + `_index.json`；命中 = `label_params_hash` ∧ `samples_set_hash`；
5. `label_builder.py` + `tools/build_labels.py`。

**不做什么**：不碰 pipeline；不缩尾；不做增量补算。

**验收**

1. 4 个 `future_{h}d` 行数 = samples 行数；`is_complete=False` 比例与 C3 预估同量级；
2. 手工抽 10 个样本，用 dateBar 原值复算 `entry/exit/target` 一致（浮点 1e-9）；
3. `trend_quality` 在 `md<=1e-6` 时为 NaN，无 inf；
4. `holding_return` 覆盖率写入 `_index.json`，且与旧路径同子集数值完全一致；
5. 二次调用不重算（mtime + 日志验证）；
6. 篡改 `label_params_hash` 或 `datebar_fingerprint` → 触发重建；
7. `pytest tests/test_data_prep -q` 全绿。

**Commit**：`feat(data_prep): add label registry and future return labels`、`feat(data_prep): add trend quality and holding return labels`、`feat(data_prep): add label cache`、`test(data_prep): add label tests`

**文件白名单**：`data_prep/labels/**`、`data_prep/label_builder.py`、`tools/build_labels.py`、`tests/test_data_prep/test_labels*.py`、`tests/test_data_prep/test_cache*.py`。**禁止**触碰 `pipeline/**`。

### M3：接线准备（零行为变化）

**目标**：把配置、契约、就绪检查全部就位，但**不改变任何现有行为**。这一步做完了，pipeline 仍是旧行为。

**做什么**

1. `pipeline/config.py`：三段配置（`input`/`target`/`data_prep`）、`RawDataParams` 两字段、`Paths` 两字段、`nested` 注册、§6.3 校验（V1–V12）；
2. `pipeline/adapters/sample_family.py`：新增 `SAMPLES_REQUIRED_COLUMNS`、`LABEL_REQUIRED_COLUMNS`，`--show-contract` 打印样本/标签契约；
3. `tools/check_raw_data.py`（G2）：
   - 按 `input.mode` 分支：samples 模式下 holding 可选；
   - 新增 `prepared` 段：`samples.parquet` 存在/列契约/`sample_id` 唯一且 int64/`signal_date` 可 int 化/`datebar_fingerprint` 与 `samples_set_hash` 匹配；
   - 新增 `idxs` 交集率检查（< 99% 判 invalid，给前 5 个不匹配样例）；
   - 退出码保持 0/2/3；
4. `pipeline/step2_slices.py`：`invalid_reasons` 的 `missing_signal_date` 拆成 `unknown_symbol` 与 `date_not_in_symbol_range`（只改计数键名与打印文案，逻辑不变）；
5. `docs/15_data_contract.md`（新）：samples / labels 契约 + 内置标签清单 + 新增标签步骤。

**不做什么**：不改 `step1_clean.py`；不改 step3/4/5/6；不产生任何新产物。

**验收（全部为"零变化"证明）**

1. `input.mode=holding_file`（默认配置）跑 smoke step1–6，与基线逐值一致（`verify_v11.py --milestone M3`）；
2. 旧 YAML 不改一行仍能加载，且 `experiment_version` 不变（新字段取默认值不应影响哈希）；
3. `input.mode=samples` 的配置能被解析（但 step1 尚未实现，预期在 step1 报"未实现"而不是配置报错）；
4. `check_raw_data` 旧配置返回 0；samples 配置缺 samples 文件返回 2；
5. `pytest tests -q` 全绿；
6. `git diff --stat` 只含白名单文件。

**文件白名单**：`pipeline/config.py`、`pipeline/adapters/sample_family.py`、`pipeline/step2_slices.py`、`tools/check_raw_data.py`、`docs/15_data_contract.md`、`configs/default.yaml`、对应测试。

**Commit**：`feat(config): add input/target/data_prep params`、`feat(adapters): expose samples and label contracts`、`feat(tools): check prepared samples in raw data guard`、`refactor(step2): split invalid_reasons keys`、`docs: add samples and labels data contract`

### M4：step1 双模式 + runner 签名（唯一的行为变化）

**做什么**

1. `pipeline/step1_clean.py`：
   - `_clean_holding`：现有逻辑原样（含 `max_holding_days`）；
   - `_clean_samples`：`samples_id` 推导 → 校验 `manifest` 的 `datebar_fingerprint` 与**重算的 `samples_set_hash`** → 按 `target.source` 取标签（`label_file` 走 `label_builder.ensure`）→ 按 `sample_id` join → **丢弃 `target` 为空的行**（D27）→ 按 `sample_id` 升序输出 `holding_clean.parquet`；
   - 两模式返回同构 `checks/details`；
2. `pipeline/runner.py`：`_completed_step_matches_current_inputs` 扩展到 step1（比对 `input_mode`/`samples_id`/`label_hash`/`datebar_fingerprint`）；
3. `docs/07`：Step 1 六个 check 的**语义映射**（名称与判定不变，只补 samples 模式下的解释）。

**M3 已完成、此处不重复**：`config.py`、`adapters`、`check_raw_data`、`step2` 计数键、`docs/15`、`labels/_template.py`、`baseline_v1.0.13.json` 的内联快照。

**不做什么**：不改 step2–step6 任何逻辑；不引入 `hist_*`；不在 step5 选标签。

**回归（最关键）**

用 `configs/local/refactor_check.yaml`（不改一行）跑 smoke，`--smoke 500`，独立 state 文件：

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
foreach ($s in 1..6) {
  & $py run_experiment_pipeline.py --mode smoke --smoke 500 --step $s `
    --config configs/local/refactor_check.yaml `
    --state-file pipeline_state_smoke_v11_regression.json
  if ($LASTEXITCODE -ne 0) { throw "step $s failed" }
}
```

逐项比对（容差：计数精确相等；浮点 1e-12）：

| 字段 | 基线值 |
| :--- | :--- |
| `step1.sample_count` | 240257 |
| `step1.datebar_rows` | 15547065 |
| `step2.input_samples` | 500 |
| `step2.valid_samples` | 494 |
| `step2.slice_rows` | 29640 |
| `step3.rows` | 29640 |
| `step4` 列数 / 入模特征 | 53 / 50 |
| `step5` train/val/test | 345 / 74 / 75 |
| `step6.best_iteration` | 215 |
| `step6.mae` | 0.09924436719491053 |
| `step6.spearman` | -0.0676529160739687 |
| `step6.pearson` | -0.06172137091798144 |

注意（四点，全部为实测约束）：

1. 新增配置段会改变 `experiment_version`，因此**不能**沿用旧输出目录；只比对 state 里的数值；
2. **step1 在冒烟模式下不抽样**（C25）：`--smoke 500` 只让 step2 从 240,257 条里抽 500，所以 `step1.sample_count` 仍是 240257；
3. **基线配置当前不可复现**：`baseline_v1.0.13.json` 只记了 `configs/local/refactor_check.yaml` 这个路径，而 `configs/local/` 已被 `.gitignore` 排除。本轮必须把「影响输出的全部参数快照」**内联进 `baseline_v1.0.13.json`**（至少：`lookback_days=60`、`time_split.*`、`buffer_trading_days=60`、`feature_selection.*`、`manual_features.*`、`tsfresh.fc_profile=compact`、`lgb.*`、`seed=42`、`smoke_size=500`、`run_date=20261102`、`label=baseline`、两个 raw 文件指纹），否则锚点会随人/机器消失；
4. `step4` 冒烟模式用**全体冒烟样本**筛特征（`step4_features.py:645-676`），与 full 模式的「仅训练集」不同 → smoke 500 的回归只覆盖「同一代码路径」而非 full 语义。因此对拍分三层：

   | 层 | 覆盖 | 做法 | 成本 |
   | :--- | :--- | :--- | :--- |
   | L1 | step1 full | 跑一次 full step1，比对 `sample_count=240257`、`datebar_rows`、`target_stats` | 低（只读+落盘） |
   | L2 | step2/step3 逻辑 | `--smoke 20000` 跑 step1–3，与 `v1.0.13` worktree 的同参数产物逐值比对（同 seed、同输入顺序 → 子集相同） | 中 |
   | L3 | step4–6 | smoke 500 比对上表指标（tsfresh 成本最低） | 低 |

   L2 必须用 `git worktree add` 检出 `v1.0.13` 作为对照，否则无法得到「重构前」产物。

**验收**

1. 上表全部一致；
2. `input.mode=samples` 能跑通 step1–6（smoke），且 `samples_id` 变更会触发 step1 重跑；
3. samples 模式下 step1 的 `dropped_no_target > 0` 且 `checks.holding_target_non_null == True`（D27 生效）；
4. `data_prep/datebar.py` 与 `step1._clean_datebar` 的 9 列输出**逐值相等**（一次性比对脚本，结论写入 `docs/14`）；
5. `check_raw_data` 两种模式分别返回 0；缺 samples 时返回 2、指纹/hash 不符时返回 3；
6. 两条路径使用不同 `state-file` 且**串行**执行；
7. `pytest tests -q` 全绿。

**Commit**：`refactor(step1): support dual input modes`、`fix(state): validate step1 input signature`、`docs: document step1 dual-mode check semantics`

**文件白名单**：`pipeline/step1_clean.py`、`pipeline/runner.py`、`docs/07_step_acceptance_criteria.md`、`tests/test_pipeline/test_step1_dual_mode.py`。**禁止**触碰 `config.py`（已在 M3 完成）、step2–6。

### M5：收尾与发布

1. `docs/00`（新命令）、`docs/01`（step1 双模式、`samples_id` 与 state 规则）、`docs/10`（新增前置层，修订「11 个主线文件」表述）、`AGENTS.md`（目录职责 + 铁律补充）、`docs/11`（基线位置）；
2. 用 `configs/kernel_v11/check/refactor_check.yaml` 对跑一次 smoke，提交 `outputs/experiment_registry.json`（按 state 的 `status` 过滤，剔除失败/探索条目）；
3. **`verify_v11.py` 处置**：移入 `version_design/design/refactor/` 归档，或直接删除；
4. **删除演练**（强制）：移走 `verify_v11.py` 后，`pytest tests -q` 与一次 smoke step1–6 仍全部通过；
5. 合入 `main` 后打 annotated tag **`v1.1.0`**。

**v1.1.0 发布 Gate（只两条）**

| # | 条件 | 判定方式 |
| :---: | :--- | :--- |
| 1 | 旧路径（`holding_file`）的 12 个基线字段与 v1.0.13 **逐值相同** | `verify_v11.py --milestone M4` exit 0 |
| 2 | 新路径（`samples`）冒烟能跑到 step6 | 同上，M4 包含该项 |

**阶段 0 对照实验不属于发布 Gate**，见 `experiment_plan_v11.md`。

---

## 9. 阶段 0 实验（已移出本文）

实验设计（0A/0B/0C/0D、R2、指标口径、控制组、报告要求）已独立为 [experiment_plan_v11.md](./experiment_plan_v11.md)。

**为什么移出**：重构的完成标准是"数字相等"（可机械判定），实验的完成标准是"结论可信"（需人工判断）。混在一起会让 v1.1.0 的发布被研究结论绑架。

**与本文的唯一关联**：

| 本文 | 实验计划 |
| :--- | :--- |
| M0 固化 `baseline_v1.0.13.json` | 被两侧共用：M4 的 R1 与实验的 R1 是同一份 |
| M3/M4 定义 `input.mode` / `target.source` 契约 | 实验的 0A–0D 直接使用 |
| §5.5 标签守恒恒等式 | 实验每组必查 |

**§9 原文（四组设计、指标口径、控制组、报告要求）已全部移入 [experiment_plan_v11.md](./experiment_plan_v11.md)，本文不再重复**，避免两处各写一份导致口径漂移。

唯一需要在此保留的接口约定：

| 项 | 约定 |
| :--- | :--- |
| R1 配置 | 用 M0 固化的旧参数原值，**不因实验需要而修改** |
| `signal_date_selection_score` | v1.1 删除（§2 D34）。侧证：它恒等于 `close/open − 1`（F13） |
| `target.source=holding_return` + `input.mode=samples` | 合法（0D 用）；标签从 raw `holding_file` 读，**不读** `processed/holding_clean.parquet` |

---

## 10. 风险与明确不支持项

| 项 | 影响 | 应对 |
| :--- | :--- | :--- |
| 样本量 2.9 倍 | tsfresh 机时/内存不可控 | `max_samples` + `date_range` 闸门；M1 用真实计数复核 |
| 0A vs 0C 样本量与标签分布不同 | 直接比 spread 会得出错误结论 | 规模匹配 + `spread_norm` + rank IC + 分年报告 |
| `min_history_days` < `lookback_days` | 样本在 step2 被静默丢弃，样本量莫名变小 | V6 硬校验 |
| step2 覆写 `holding_clean` | 误把过滤后文件当样本全集 | 文档明确 `samples.parquet` 为权威；step1 不读 `holding_clean` |
| 尾部标签缺失 | test 尾部样本被丢弃 | 报告 `is_complete=False` 比例（C3） |
| 脏 bar（`high<low` 54 行） | 指标轻微失真 | 本版不修；记录数量；修数据属独立议题 |
| 停牌/涨跌停 | 标签高估可实现收益 | 只保证「建仓日非停牌且非开盘涨停」；出场跌停不顺延，写入报告 |
| **bagging 不支持 samples 模式** | 不能对新采样做 bagging | v1.1.0 明确不承诺；`bagging.py` 写死 60 日产物与 8 件门禁，改造属后续版本 |
| **per-sample 标量特征通道缺失** | `close/open−1` 这类"信号日单点值"无法进入模型 | v1.1 不做。若要重建，需在 step3 增出 `sample_scalars.parquet` 契约并由 step5 合并 —— 这是**指标层扩展**，写入 v1.2 backlog，不混进本次解耦重构 |
| 无数据/无解释器的机器 | 无法验证 | 执行环境固定为 factor311 + 外部 `data/raw` |
| 冒烟与全量的标签口径 | 结论混淆 | 报告标注模式；冒烟指标不作结论 |
| **NaN 标签被静默填成 0**（C31/D33） | 虚假 0 收益样本混入训练，指标全部失真且无告警 | 用 §5.5 的守恒恒等式验收；不平即停 |
| **失败/探索运行会污染台账**（C29） | `registry` 出现指向被忽略配置或失败实验的条目 | 收尾时按 state 的 `status` 过滤后重写 `outputs/experiment_registry.json`；不允许直接把中间台账提交 |
| **两套 datebar 规范化漂移** | `data_prep` 与 step1 对同一文件产出不同数值 | 不变量 8 的一次性逐值比对；之后任何改动都要重跑该比对 |
| **`data_prep` 与 `run_experiment_pipeline` 的根目录不一致**（C30） | 在子目录执行时找不到 samples/truth | 所有命令强制在仓库根执行；`tools/build_samples.py` 用 `Path.cwd()` 与 pipeline 对齐，文档写明 |

---

## 11. 工作量

| 里程碑 | 内容 | 估计 | 行为变化 |
| :---: | :--- | ---: | :---: |
| M0 | 探测脚本 + 文档 + C1–C4 + 基线内联 | 0.5–1 天 | 无 |
| M1 | samples 层 + legacy + CLI + 测试 | 3–4 天 | 无 |
| M2 | labels 层 + 缓存 + 测试 | 3–4 天 | 无 |
| M3 | config + adapters + check_raw_data + step2 计数键 + docs/15 | 1.5–2 天 | 无 |
| M4 | step1 双模式 + runner 签名 + R1 三层回归 | 2–3 天 | **有** |
| M5 | 文档 + 基线 + 删除演练 + 发布 | 1 天 | 无 |
| **v1.1.0 合计** | | **11–15 人天** | |
| E（另立） | 四组实验 + 控制组 + 报告 | 2–3 天 | 不在 v1.1.0 内 |

相比原「6 Phase / 13.5–18 天」，总量下降，且**风险从 18 天摊薄压缩到 M4 一个 2–3 天窗口**。

---

## 12. 交付物

| 类别 | 内容 | 归属 |
| :--- | :--- | :---: |
| 代码 | `data_prep/**`（9 文件，含 `labels/_template.py`）、`tools/build_samples.py`、`tools/build_labels.py`、`tools/probe_datebar.py` | M1/M2 |
| 代码（改） | `pipeline/step1_clean.py`、`runner.py` | M4 |
| 代码（改） | `pipeline/config.py`、`adapters/sample_family.py`、`tools/check_raw_data.py`、`step2_slices.py`（仅计数键） | M3 |
| 验收工具 | `tools/verify_v11.py`（**v1.1 专用，M5 归档或删除**） | M0 |
| 测试 | `tests/conftest.py`、`tests/test_data_prep/*`、`tests/test_pipeline/test_step1_dual_mode.py`、`pytest.ini` | M1–M4 |
| 数据（运行时，不入库） | `data/prepared/{samples_id}/`、`data/labels/{samples_id}/` | 运行时 |
| 文档 | `docs/14_datebar_probe.md`、`docs/15_data_contract.md`（含**内置标签清单** + 新增标签/采样步骤）；更新 `00/01/07/10/11`、`AGENTS.md` | M0/M3/M4/M5 |
| 基线 | `baseline_v1.0.13.json` 升级为「含完整参数快照 + 指纹」；`configs/kernel_v11/check/refactor_check.yaml` | M0 |
| 报告 | 阶段 0 对照报告 | **E（另立）** |

---

## 13. 发布

| 项 | 决定 |
| :--- | :--- |
| 分支 | 沿用 `refactor/v1.1` |
| tag | **`v1.1.0`**（annotated；现有 v1.0.0–v1.0.13，结构性重构必须进 minor 号） |
| 合入 | M0–M5 全部验收后合入 `main` 再打 tag，不打中间 tag |
| `base_version` | 与内核版本独立（样本族仍用 `boll` 等） |
| 阶段 0 实验 | **不阻塞发布**，见 `experiment_plan_v11.md` |

**打 tag 前的最后三道检查**

| # | 检查 | 命令 |
| :---: | :--- | :--- |
| 1 | R1 逐值一致 + 新路径跑通 | `verify_v11.py --milestone M4` → exit 0 |
| 2 | `verify_v11.py` 删除演练 | 临时移走后 `pytest tests -q` 与 smoke step1–6 全绿 |
| 3 | 无越界改动 | `git diff --stat v1.0.13..HEAD` 与 §12 交付物逐项对应 |

---

## 14. 已确认决策与仍待确认项

### 14.1 已确认（2026-11-05）

| # | 决定 | 影响 |
| :---: | :--- | :--- |
| Q1 | samples 模式 step1 **丢弃无标签样本**，`details` 报 `dropped_no_target` | 满足 `holding_target_non_null`，不改 `docs/07` 语义 |
| Q2 | **删除** `signal_date_selection_score`；R1 保留旧行为做回归，R2 四组统一不含 | 方案回到"只改 step1"；`0A(R1) − 0A(R2)` 作为免费附注 |
| Q3 | **A+C**：内联参数快照 + 两个 raw 文件 sha256（`baseline_v1.0.13.json`），并把回归配置提前入库 | 基线可跨机器复现 |
| Q4 | **决策规则**：`fc_profile=compact` → `max_samples: null`；要上 `medium` → 先 20k 实测再定 cap，并按年等距抽样、记录抽样率 | 不在无实测时拍数字 |
| Q5 | **手动 `label_spec_version`**，写进 `code_agent_prompt.md` 硬约束 | 改标签口径必须 +1 |
| Q6 | 三个 0 字节 `*.new.md` 已删除 | — |
| Q7 | **v1.1 只补 G2（契约校验）**；G1 外部标签**不做**（Q8=B）；G3/G4/G5 归 v1.2 | "任意样本"可用，标签扩展走内核模块 |

### 14.2 已确认（第二轮）

| # | 决定 | 影响 |
| :---: | :--- | :--- |
| Q8 | **不做外部标签**。标签只有一条路：内核内实现 + 注册；常用标签整理成内置清单写进 `docs/15`，反复用到再转正 | 去掉 `external_file`；新增 `_template.py` 与内置清单章节；防泄漏红线不开口子 |
| Q9 | 保持"短平快"定位：一次实验若需自定义标签，写 60 行模板即可，**不为便利性引入复杂机制** | 拒绝 G1/G3/G4 的即时改造 |

### 14.3 已确认（第三轮：交付结构）

| # | 决定 | 影响 |
| :---: | :--- | :--- |
| Q10 | **按合并点切分 M0–M5**，不按主题拆设计文档：每个里程碑单独合入后仓库必须是绿的、旧行为必须可证 | M0–M3 零行为变化，可放心交给 agent；风险集中在 M4 |
| Q11 | **实验移出 v1.1.0**，独立为 `experiment_plan_v11.md`；发布 Gate 只依赖 R1 回归 + 新路径跑通 | 重构完成标准变成二值判定，不再被研究结论绑架 |
| Q12 | 新增 `tools/verify_v11.py` 把验收变成 exit code | 替代散文式验收，防"声称已对齐" |
| Q13 | `verify_v11.py` 是 **v1.1 专用一次性工具**：不得被 pipeline 引用、只读、M5 归档或删除、删除后项目必须照常运行 | 保持项目稳定，不新增长期依赖 |

### 14.4 仍待确认

无。设计已冻结，可进入 plan / prompt 编写。

---

## 15. v1.2 backlog（本次明确不做）

| 项 | 内容 | 触发条件 |
| :--- | :--- | :--- |
| G3 | `holding_clean.parquet` 改名（如 `samples_clean.parquet`），同步 `bagging.py` 与全部路径引用 | 决定做 v2 架构整理时 |
| G4 | per-sample 标量特征通道（`sample_scalars.parquet`）：让 `close/open−1`、行业、市值等单点值能进模型 | 需要横截面/单点特征时 |
| G5 | `bagging.py` 解耦：去掉写死的 `slices_60d` / `ts_features_60d` / `features_selected_50` 与 8 件产物门禁 | 要在新采样上做 bagging 时 |
| — | 标签/采样模块的 `tools/scaffold_*.py` 脚手架（当前用 `_template.py` 手工复制） | 新增频率高到需要时 |
| — | 全量 tsfresh 在 6.9×10⁵ 样本、`medium` 档下的内存/机时基准 | 真要上 medium 时 |

---

## 附录 A：探测快照（2026-11-05，只读）

| 项 | 实测值 |
| :--- | :--- |
| dateBar 形状 | 15,547,065 × 11（`reset_index` 后） |
| 列 / dtype | `idxs`object、`idxd`int64、OHLCV+`amount`+`xs` float32、`paused`/`marks` int16 |
| 代码 | 6090，全部 `^(sh\|sz\|bj)\d{6}$`；指数 723（`sh000*` 311 + `sz399*` 412）；股票 5367 |
| 时间 | 4384 交易日，2007-01-04 → 2025-01-14 |
| 唯一性 | `(idxs, idxd)` 重复 0 行 |
| 停牌 | 3.19%；停牌行 99.99% 同时 `volume<=0` |
| 涨/跌停标记 | 任一价格触及：2.32% / 1.57% |
| `marks` | 43 种；bit≥12 恒 0；位序见 §5.4 |
| 复权 | 已后复权（4 个锚点误差 < 0.5%） |
| 量纲 | `amount/(close×volume) ≈ 100` |
| 脏 bar | `high<low` 54、`high<max(o,c)` 109、`low>min(o,c)` 26 |
| holding | 240,467 行 / 5328 代码 / 2007-02-01 → 2025-01-13；过滤后 240,257 |
| 持有期 | 中位 13 / P90 30 / P99 63 |
| `holding_return` | mean 0.77%、σ 17.2%、min −52.8%、max 761% |
| 布林带触发 | 397 股票 50,844 次（≈127/只/18 年），外推 ≈6.9×10⁵ |
| 上市时间 | `first_idxd` 中位 2013-11-16 |

## 附录 B：与 `design_raw` 的对应

| `design_raw` | 本文处置 |
| :--- | :--- |
| §1 背景/目标 | 保留；目标收窄为「只改 step1」 |
| §2 八条决策 | 保留 6 条；「step5 切标签」被 D3 推翻；「step1 提历史 OHLC」被 D2 删除 |
| §3 架构 / §4 契约 | 改写（真实列名、`samples_id`、`sample_id` 派生、单配置） |
| §5 模块职责 | 保留并补 `ids.py`、`universe.py`、CLI、`_index.json` |
| §6 Phase 总览 | 改为 **M0–M5 交付切分**（按合并点，不按主题）；实验另立 |
| §7 Phase 0 | → M0（探测固化 + 基线自包含） |
| §8 Phase 1 | → M1（samples 层）；去掉 `hist_*` |
| §9 Phase 2 | → M2（labels 层）；去掉增量补算 |
| §10 Phase 3 | 拆为 M3（接线，零变化）+ M4（step1，唯一行为变化） |
| §11 Phase 4 | **取消**（Tags 入口放 step5 的方法是错的，见 D3） |
| §12 Phase 5 | → M5（收尾发布）；四组实验移入 `experiment_plan_v11.md` |
| §14–§21 | 保留结构，数值与结论按本文修正 |

## 附录 C：工作区与环境

| 项 | 状态 |
| :--- | :--- |
| 分支 | `refactor/v1.1`，与 `main`/`v1.0.13` 同一提交 |
| 已跟踪改动 | `.gitignore`（忽略 `configs/local/`、`version_design/demo/`） |
| 未跟踪 | `outputs/experiment_registry.json`（约定 v1.1.0 收尾时提交）、`version_design/design/refactor/*` |
| 解释器 | `C:/ProgramData/anaconda3/envs/factor311/python.exe` |
| 原始数据 | 仓库内 `data/raw/` 为空；实指 `D:/strategy_explore_total/strategy_explore/data/raw/`（`configs/local/refactor_check.yaml`） |
| 计划/提示词 | `implementation_plan.md`、`code_agent_prompt.md` 已按要求清空为 0 字节，待本设计冻结后重写 |
| 相关文档 | `experiment_plan_v11.md`（阶段 0 对照实验，不阻塞 v1.1.0）、`design_raw.md`（原始方案，仅留档） |

## 文档结束

**给工程 agent 的提示**

1. 先读 §1（事实）与 §2（修正清单），再读 §4–§6（契约）；**不要按 `design_raw` 施工**。
2. 按 **M0 → M5** 顺序执行，每个里程碑通过 `verify_v11.py` 后才能开始下一个（§8.2 V-4）。
3. 唯一允许改逻辑的 pipeline 文件是 `step1_clean.py`（M4）；`config.py`/`adapters`/`check_raw_data.py`/`step2` 的改动仅限 M3 白名单。
4. 不得修改任何既有 check 的名称或判定语义（§8.2 V-2）；check 不通过时修数据，不改断言。
5. `verify_v11.py` 是 v1.1 专用工具，禁止被 `pipeline/**` 或 `data_prep/**` import。
