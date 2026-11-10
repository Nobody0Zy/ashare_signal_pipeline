# v1.1 设计依据与修订历史（记忆回溯）

> 状态: 归档
> 日期: 2026-11-06
> 定位: **实施时不需要读**。本文记录「为什么这样定」——原始方案的缺陷、五轮核对的过程、每处修正的证据、被否决的备选方向。
> 作用: 未来若有人质疑某条设计，或需要重跑验证，从这里找回依据与复现方法。
> 配套: `experiment_design.md`（实施规范，唯一权威）、`design_raw.md`（原始方案 v2.1，仅留档）

---

## 0. 这份文档解决什么问题

`experiment_design.md` 是**规范**：只写「要建成什么」，供实施 agent 直接执行。

但规范里每个决定背后都有一段排查：某条原方案为什么不可行、实测数据推翻了什么假设、某个看起来更好的方案为什么被否。把这些写进规范会让它变长且难以执行，所以单独放这里。

**读法**：按需查。质疑某条设计时，先在 §2 的修正清单里找对应 ID，再看 §3 的证据或 §4 的决策记录。

---

## 1. 演进时间线

| 阶段 | 事件 | 产出 |
| :---: | :--- | :--- |
| 1 | 用户提供 `design_raw.md`（v2.1，703 行，6 个 Phase） | 原始方案 |
| 2 | 第一轮：代码逐行核对 + 真实数据只读探测 | 发现 10 项阻塞（D2–D20 的一部分） |
| 3 | 第二轮：交叉 grep + 回归路径审计 | 补 D27–D29（D27 会导致 step1 必然失败） |
| 4 | 第三轮：可移植性审计（`check_raw_data`、`bagging`、`docs` 索引） | 补 D30–D33 |
| 5 | 第四轮：特征溯源（反解 `signal_date_selection_score`） | 补 D34–D36，并纠正一次自身误判 |
| 6 | 第五轮：交付结构审计（agent 独立落地的可行性） | 拆为 M0–M5，实验移出 v1.1.0 |
| 7 | 重写 | `experiment_design.md` 转为纯规范；本文承接全部过程记录 |

**累计**：36 项修正，其中 14 项阻塞。

---

## 2. 修正清单（D1–D36）

「级别」含义：**阻塞** = 不修正则方案跑不通或结论无效；高 = 会静默出错；中/低 = 影响可维护性或一致性。

| ID | `design_raw` 的说法 | 冲突 / 遗漏 | 修正 | 级别 | 现设计位置 |
| :--- | :--- | :--- | :--- | :---: | :--- |
| D1 | `pipeline/step1_clean/`、`configs/pipeline.yaml`、`configs/step5.yaml`、`tests/` | 实际是单文件模块 + 单继承配置；仓库无测试目录 | 按单文件改；配置并入 `configs/default.yaml` 新段；新建 `tests/` + `pytest.ini` | 中 | §2.2、§6.3 |
| D2 | `R3`：step1 提取 `hist_*` 序列，step3 直接用，step2 不变 | step2 就是窗口生成器；`hist_*` 会让 24 万×100 天序列进 parquet，且与「step2 不变」互斥 | **删除 R3 与全部 `hist_*`**；历史窗口仍归 step2 | **阻塞** | §2.3、§6.1 |
| D3 | step5 读 `data/labels/{column}` + join | step4 已用 `target` 筛特征与定缩尾，step5 的 `target` 来自特征文件；在 step5 换标签会造成「筛选用 A、训练用 B」 | **标签在 step1 决定**并注入 `holding_clean.target`；step4/5 零改动 | **阻塞** | §3.3、§6.3 |
| D4 | 未提 `sample_id` | 全链路以其为键、禁止 `reset_index(drop=True)`；`idxs` 是字符串 | `sample_id = market*10^14 + code6*10^8 + idxd` | **阻塞** | §3.4 |
| D5 | 契约列 `symbol`/`signal_date:date`/`is_st`/`is_limit_*`/`tradestatus` | 真实列是 `idxs`(str)/`idxd`(int)/`paused`/`marks`；涨跌停可精确判定；ST 无独立列但 5% 限幅已体现在 `marks` | 用真实列名；可交易性 = `paused` + 历史长度 + `marks` | **阻塞** | §1.1、§3.1、§4.4 |
| D6 | 要求记录 `datebar_version` | dateBar 无版本字段，方案未给生成方式 | `datebar_fingerprint = sha256(bytes)[:12] + 元数据后缀` | 中 | §3.4 |
| D7 | `data/labels/{name}.parquet` 单文件 | samples 变化后同名文件被静默复用 | `data/labels/{samples_id}/{label}.parquet` + `_index.json` | **阻塞** | §3.2 |
| D8 | 未提状态幂等 | 除 step4 外完成即跳过；重跑 step1 会自动清空 2–6 | step1 `details` 写签名，`runner` 扩展 step1 校验 | 高 | §3.3、§6.3 |
| D9 | step5 `target.transform: winsorize` | 已是「训练集分位 + 全量裁剪」，再加一层等于三重裁剪 | 标签层**不缩尾** | 中 | §4.6、§3.6 |
| D10 | 只对比 0A vs 0C | 采样与标签同时变，差异不可归因 | 2×2 四组 0A/0B/0C/0D + 4 个控制组 | **阻塞** | `experiment_plan_v11.md` §2 |
| D11 | 未提缓冲带与标签前视 | 只校验 `buffer >= lookback`；`future_20d` 会让训练标签越过验证区间 | `buffer_trading_days >= max(lookback_days, horizon_days)` | 高 | §5.3 V5 |
| D12 | 新增 YAML 顶层段 | `from_dict` 只遍历白名单，未注册的段被静默忽略 | 必须同时改 4 处（字段声明 / `nested` / 校验 / `version_payload`） | 高 | §5.1 |
| D13 | 新增 `docs/20/21/30` | docs 已占 00–13，`00_start_here` 是索引 | 用 `docs/14`、`docs/15`，同步 `00`/`07`/`10`/`AGENTS` | 低 | §2.2、M0/M5 |
| D14 | 0A 与 samples 模式口径一致 | 旧路径有 `max_holding_days`；samples 模式无 `signal_date_selection_score` | 0A 固定 `max_holding_days: null`；缺失特征写进报告 | 中 | `experiment_plan_v11.md` §2.2 |
| D15 | 未定义股票池 | 723 个指数会被当股票采样 | `universe.exclude_prefixes` | 高 | §4.1、§5.1 |
| D16 | 未评估样本量 | 实测外推 ≈6.9×10⁵（2.9 倍） | `max_samples` + `date_range` 闸门 | **阻塞** | §9.3 |
| D17 | 未识别复权口径 | 价格已后复权，再乘 `xs` 双重复权 | 价格即最终口径；`xs` 只进指纹与审计 | **阻塞** | §1.1、§4.2 |
| D18 | 未提 step2 覆写 | full 模式把 `holding_clean.parquet` 改成有效子集 | 声明 `samples.parquet` 为样本权威；step1 不依赖 `holding_clean` | **阻塞** | §9.2 |
| D19 | 未提就绪检查与 adapter | samples 模式会被判 invalid | `check_raw_data` 与 adapter 按 `input.mode` 分支 | **阻塞** | M3 |
| D20 | 未提 bagging | 写死 60 日产物 + 8 件门禁 | v1.1 明确 bagging 仅支持 `holding_file` 模式 | **阻塞** | §0.4 |
| D21 | 未提 check 名称稳定性 | `docs/07` 与基线已固定 6 个 check 名 | samples 模式复用同一组 key，语义用文档映射，**不改名** | 高 | §3.3 规则 3 |
| D22 | 未提版本哈希变化 | 新增配置段必然改变 `experiment_version`；`--smoke` 也参与哈希 | 回归比对数值不比对路径；必须传相同 `--smoke` | 高 | §5.4、M4 |
| D23 | 未提「11 个主线文件」 | `docs/10` 声明主线固定 11 文件 | `data_prep/` 定位为前置层，不入主线；修订 `docs/10` | 中 | M5 |
| D24 | 未提产物是否入库 | `data/*` 全忽略 | samples/labels 是运行时资产，靠「配置 + 确定性 ID + 重建」 | 中 | §3.5 |
| D25 | `tradability.py` 独立模块 | 职责与 `universe`、`marks` 解码重叠 | 合并为 `universe.py` + `labels/base.py` 的 `next_tradable_position()` | 低 | §2.2、§6.1 |
| D26 | `configs/data_prep.yaml` 独立配置 | 会与 pipeline 配置重复 `datebar_file`；采样参数不进 `experiment_version` | 单一 pipeline 配置，新增 `input`/`target`/`data_prep` 三段 | 高 | §5.2 |
| D27 | step1 的 `holding_target_non_null` 要求逐行非空 | 尾部标签必然 NaN → samples 模式**必定判 failed**，整链跑不起来 | samples 模式注入标签后**丢弃空标签行**，并把计数写入 `details` | **阻塞** | §3.3 规则 1、§4.9 |
| D28 | 校验把 `holding_return` 绑死 `holding_file` | 与 0D（`samples` + `holding_return`）直接矛盾 | `holding_return` 允许两种 mode；`label_file` 才要求 `samples` | **阻塞** | §5.3 V2/V3 |
| D29 | 为 `marks` 扩展 `datebar.parquet` | 会打破 `datebar_columns` 的精确列表相等检查，并使旧路径回归失真 | `datebar.parquet` 恒 9 列；`marks` 只在 `data_prep` 从 raw 消费 | **阻塞** | §6.2 不变量 6 |
| D30 | 未定义 samples 输出行序 | 冒烟抽样对行序敏感，不排序则同参数重跑可能抽到不同子集 | samples 与 samples 模式的 `holding_clean` 按 `sample_id` 升序 | 高 | §6.2 不变量 8 |
| D31 | 未定义 `is_complete` 与 `is_entry_blocked` 的关系 | 两处描述冲突，下游歧义 | `is_complete` 只表示窗口完整；可用性统一看 `target.notna()` | 高 | §3.2、§4.6 |
| D32 | `label_params_hash` 不含算法版本与 holding 指纹 | 改标签代码或换 holding 后，旧标签被静默复用 | 增加 `spec_version` 与 `holding_fingerprint` | 高 | §3.4、§4.5 |
| D33 | 「step5 会兜底丢弃无效 target」是错的 | step5 先 `fillna(0)` 再判空，`target_rows_dropped` 恒 0 → 虚假 0 收益样本且无告警 | 不改 step5；改用守恒恒等式验收 | 高 | §4.9 |
| D34 | 曾把 `signal_date_selection_score` 判为「旧策略的路径依赖排序」并据此设计 R2「四组统一关掉」 | ①实测它等于 `close/open − 1`，非路径依赖；②但**位置在架构上不对**：价格衍生量寄存在 raw 交易记录，绕过指标层直达模型特征，且只在 `holding_file` 模式存在 → 正是本重构要消除的「输入模式影响特征集」 | **删除**该特征；R1 保留旧行为做回归，R2 四组统一不含 | 高 | §0.5、§5.3 V11 |
| D35 | `LabelBuildParams` 用固定字段（`future_horizons` / `trend_quality_horizon`） | 新增标签必须改 dataclass + 校验 + YAML 三处，「可插拔」是假的 | 改为按 `params_key` 分桶的字典 `params: {key: {...}}` | 高 | §5.1 |
| D36 | 校验规则 V4 硬编码 `target_future_{h}d` / `target_trend_quality_{h}d` | 任何新标签都会被判非法 | V4 改为向 registry 查询 `horizon`；代码中不出现任何标签名字面量 | 高 | §5.3 V4 |

---

### 2.1 2026-11-10 复核修订（D37–D46）

独立复核发现并已直接改入 `experiment_design.md` 的问题；记录在此仅作追溯。

| ID | 问题 | 修正 | 级别 | 现设计位置 |
| :--- | :--- | :--- | :---: | :--- |
| D37 | 守恒恒等式漏掉 step2 丢弃（full 旧路径约 4,402 行） | 增加 `− dropped_samples(step2)`，并写明 step2 会覆写 `holding_clean` | **阻塞** | §4.9、M4 验收 |
| D38 | `max_samples` 不在 `sampling_params_hash` / manifest 中 | 入哈希；manifest 增 `max_samples` / `rows_before_cap` / `sampling_rate` / `year_quota`；补确定性分年抽样算法 | **阻塞** | §3.1、§3.4、§4.2、§9.3 |
| D39 | `verify_v11.py` 无逐里程碑规格，M0 白名单/§10.1/§7.2 三处归属矛盾 | 补 M0–M5 检查项表；`verify_v11` 划入 M0 白名单与验收；禁止空真与 skip | **阻塞** | §8.1、M0、§10.1 |
| D40 | M3 验收要求 `experiment_version` 不变，与 §5.1/§5.4 及 `version_payload` 实现冲突 | 改为“会变、属预期、只比对 state 数值” | **阻塞** | M3 验收、§5.4 |
| D41 | M1 要求“先改 dataclass”但 `config.py` 不在 M1 白名单；M1 工具又依赖 `DataPrepParams` | config 字段/`nested`/`version_payload` 划入 M1；校验留 M3；samples 模式在 step1 顶部显式拒绝 | **阻塞** | §5.1、§7.1、M1、M3 |
| D42 | `implementation_plan.md` / `code_agent_prompt.md` 为空但被当作配套计划 | 标为 **M0 开工硬前置**，新增 §7.2 规则 0 与附录检查 0 | **阻塞** | 头部、§7.2、附录、§10.1 |
| D43 | “移除 `signal_date_selection_score`”与“step5 零改动”冲突 | 改写为“新路径不提供、守卫自动跳过、旧路径保留”，V11 警告落在加载期 | 高 | §0.5、§5.3 V11 |
| D44 | 0D 需要的 `samples + target.source=holding_return` 未定义标签名解析 | 新增 V13 与 §3.3 规则 5（固定 `target_holding_return`） | 高 | §5.3、§3.3 |
| D45 | 标签文件行契约缺失；`holding_return` 可被实现成“删空行” | 明确“每 sample 一行、缺失写 NaN、不得删行” | 中 | §3.2 |
| D46 | 陈旧数字与重复清单（行数、文件数、“12 个基线字段”、两份阅读表、两处恒等式） | 重算行数；改为“L3 全表”；README 不再复制行数；实验计划引用正文 | 低 | 头部、§10.1、README、`experiment_plan_v11.md` |

### 2.2 2026-11-10 第二轮复核修订（D47–D52）

| ID | 问题 | 修正 | 级别 | 现设计位置 |
| :--- | :--- | :--- | :---: | :--- |
| D47 | 标签名 → 定义解析模型缺失：`LabelDefinition.name`/`horizon` 是类级标量，但 `future_return` 一个类出 4 个名称；V4 要求“查询该标签声明的 horizon”，同时禁止字面量 | 改为 `name_pattern` + `horizon_of(name, params)`；新增 `resolve_label(name) -> (definition, name_params, horizon)` 作为唯一入口；双命中即注册冲突；`target_trend_quality_{h}d` 一并参数化 | **阻塞** | §4.5、§4.6、§4.7、§4.8、§4.10、§6.1、V4 |
| D48 | `on_missing_samples` / `on_missing_label` 只有字段没有行为；`on_missing_label` 默认 `build` 会让 step1 在状态机内写 `data/labels` | v1.1 只允许 `error`（`build` 保留字段但报错并提示先跑 CLI）；`label_builder` 明确只由 `tools/build_labels.py` 调用 | **阻塞** | §5.1、§5.2、V3、V13、§6.1 |
| D49 | `holding_fingerprint` 无算法，却进了 `_index.json` 与 `label_params_hash` | 定义 `sha256(bytes)[:12] + "_{rows}_{min}_{max}"`，与 `datebar_fingerprint` 同构 | 高 | §3.2、§3.4 |
| D50 | baseline 无 `target_stats`，但 L1 要求其一致、M0 要求“§8.3 全表无空值” | §8.3 观测值纳入 `step1.target_stats`；M0 采集、M4 比对 | 高 | §8.3、M0、§8.1 |
| D51 | M5 无文件白名单，且 §7.3 把它写成“只改文档” | 补 M5 白名单（文档 + registry + 删除 `verify_v11.py`）；§7.3 行同步 | 高 | M5、§7.3 |
| D52 | `is_paused` 声明“恒 False”，但 `legacy_holding` 可能落在停牌日；`legacy_holding` 未禁 `max_samples`/`date_range`；`label_hash` 与 `label_params_hash` 同物两名；不变量 7 的 dtype 歧义；阅读表漏 §8.1/§3.1；`tests/conftest.py` 越界 | 逐条修正：`is_paused` 取原值；新增 V14；显式声明两名字等价；改为“按值比较”；补阅读表；补白名单 | 中 | §3.1、§3.3、§3.4、§4.3、§6.2、V14、阅读表、M1 |

### 2.3 2026-11-10 第三轮复核修订（D53–D55，接口级）

| ID | 问题 | 修正 | 级别 | 现设计位置 |
| :--- | :--- | :--- | :---: | :--- |
| D53 | `compute()` 返回形状与“一个定义出多个名称”冲突（单帧 vs 4 个文件）；`ensure()` 语义连带不明 | `compute -> Dict[str, DataFrame]`；`ensure(..) -> List[Path]`，语义 = 该定义全部产出就绪、缺则整组重建；`_index.json` 按实际写出文件逐条登记 | **阻塞** | §4.5、§6.1 |
| D54 | `spec_version` 双来源（定义级 + 全局 YAML），`label_params_hash` 未指明取哪个 | **删除**全局 `LabelBuildParams.spec_version` 与 YAML 对应行；hash 只取 `definition.spec_version`；V12 改为加载期校验定义 | **阻塞** | §3.4、§5.1、§5.2、V12 |
| D55 | `ctx` 从未定义，且采样/标签需要不同类型；这是防泄漏边界 | 新增 `SampleContext`（`history` / `index_of`，无前瞻）与 `LabelContext`（`entry_position` / `forward_window`，无过去窗口），分别由 `build_samples` / `label_builder` 构造 | **阻塞** | §4.1、§4.5、§6.1 |

## 3. 探测证据

### 3.1 数据事实（F1–F14）

| # | 事实 | 方法 |
| :---: | :--- | :--- |
| F1 | dateBar 是 MultiIndex `(idxs, idxd)` 的 DataFrame，`reset_index()` 后 11 列 | `d.index.names` |
| F2 | 15,547,065 行 × 6090 代码 × 4384 交易日，2007-01-04 → 2025-01-14 | `shape` / `idxd.min/max` |
| F3 | `idxs` 为字符串，6090 个全部匹配 `^(sh\|sz\|bj)\d{6}$` | 正则全量匹配 |
| F4 | 指数 723（`sh000*` 311 + `sz399*` 412），股票 5367 | 前缀分组计数 |
| F5 | `(idxs, idxd)` 零重复；无 NaN | `duplicated().sum()` / `isna().sum()` |
| F6 | `paused` 0/1，占 3.19%；停牌行 99.99% 同时 `volume<=0` | 交叉统计 |
| F7 | **价格已后复权**：`close ≈ 原始价 × xs` | 见 §3.2 锚点 |
| F8 | `amount / (close × volume) ≈ 100`，逐年稳定 | 按年中位数 |
| F9 | `marks` 43 种取值，bit ≥ 12 恒 0 | `np.unique` |
| F10 | `marks` 位序：高 nibble = 涨停 开/高/低/收，低 nibble = 跌停 | 见 §3.3 |
| F11 | `high<low` 54 行、`high<max(o,c)` 109 行、`low>min(o,c)` 26 行 | 布尔掩码计数 |
| F12 | holding 240,467 行 / 5328 代码 / 2007-02-01 → 2025-01-13，与 dateBar 代码 100% 匹配 | merge 计数 |
| F13 | `holding_return` 均值 0.77% / σ 17.2% / min −52.8% / max 761% | `describe()` |
| F14 | 布林带 20/2.0：397 只股票 18 年 50,844 次（≈127/只）；外推全市场 ≈6.9×10⁵ | 见 §3.5 |

### 3.2 复权口径的四个锚点

判据：若价格已复权，则 `close / xs` 应等于真实市价。

| 代码 | 日期 | `close` | `xs` | `close / xs` | 真实价（记忆/公开） |
| :--- | :--- | ---: | ---: | ---: | ---: |
| sh600519 | 2025-01-14 | 11411.01 | 7.7494 | ≈1472 | ≈1470 |
| sh600519 | 2007-01-04 | 368.25 | 4.29 | ≈85.8 | ≈86 |
| sh601318 | 2007-03-01 | 46.79 | 1.0 | 46.8 | 46.8 |
| sz000002 | 2007-01-04 | 653.41 | 42.21 | ≈15.5 | ≈15.5 |

四个锚点误差均 < 0.5% → 结论：**价格列已是后复权，`xs` 不得再次乘用**。

补充验证：`xs` 跃迁日（如 sh600000 于 2010-06-10 由 3.859 → 5.059）价格无跳空，证明复权已在价格内。

### 3.3 `marks` 位序的推导

初始只知 `marks` 是整数掩码。用「价格等式」反推每个 bit 的含义：

| nibble 取值 | 观察到的价格关系 | 含义 |
| :---: | :--- | :--- |
| `zt=0b1100` | `open == high` | 开、高涨停 |
| `zt=0b1111` | `open == high == low == close` | 四价全涨停（一字板） |
| `zt=0b0101` | `high == close` | 高、收涨停 |
| `dt=0b1010` | `open == low` | 开、低跌停 |
| `dt=0b0011` | `low == close` | 低、收跌停 |

由此得 nibble 内位序：`8=开盘, 4=最高, 2=最低, 1=收盘`；高 nibble 为涨停（bit 11..8），低 nibble 为跌停（bit 7..4）。

交叉验证：

| 检验 | 结果 |
| :--- | :--- |
| 涨停组中位收益 | +10.0% |
| 跌停组中位收益 | −10.0% |
| `data/raw/marks对照表.txt` | 与上述位序**一致**（2048=开涨停、512=低涨停、256=收涨停） |

> `marks对照表.txt` 把 2048/512/256 等组合标注为「按逻辑不应该出现」——那是**规则意见**，不是标签错误。早期一度误判为「该文件标签有误」，后经该文件自身的 zt/dt 位表核对纠正。

### 3.4 `signal_date_selection_score` 的反解

**起因**：初判它是「旧策略生成的人工打分」，因而设计「R2 四组统一关掉」。但缺乏证据，于是做反解。

**方法**：合并 holding 的 `(idxs, signal_date, score)` 与 dateBar 同日行情，计算逐日截面 Spearman。

**过程**：

| 轮次 | 候选 | 逐日截面 Spearman（均值） |
| :---: | :--- | ---: |
| 1（12 个日期） | `amplitude = (high-low)/prev_close` | 0.837 |
| | `ret1 = close/prev_close-1` | 0.829 |
| 2（1,717 个日期） | `rng_pos = (close-low)/prev_close` | 0.912 |
| | `intraday = close/open-1` | **1.0000** |

**最终验证**（400 只股票、17,616 个样本）：

```text
max | score − (close/open − 1) | = 6e-8   （float32 精度极限）
Pearson = Spearman = 1.0
逐日截面 Spearman 均值 = 1.0000，无一日例外
```

**结论**：`score ≡ close/open − 1`，即「信号日日内涨幅」，是收盘时点完全可知的合法市场量，**不含未来信息**。

**它的强度**（这解释了基线为何看起来弱）：

| 指标 | 值 |
| :--- | :--- |
| Pearson(score, holding_return) | −0.061 |
| Spearman | −0.161 |
| 逐日截面 IC 均值（3,088 日） | **−0.171**（P10 −0.42，P90 +0.09） |
| IC 为负的比例 | 81.2% |
| 十分组单调性 | Q1 +1.80% → Q10 −2.17%，10/10 严格单调 |

与 A 股短期反转效应一致。

**但仍然删除**，理由是架构而非统计：

| 理由 | 说明 |
| :--- | :--- |
| 数据层错位 | 价格衍生量寄存在「交易记录」文件里 |
| 阶段层错位 | 绕过指标层（step3）从 raw 直达 step5 的模型特征 |
| 对称性破坏 | 只有 `holding_file` 模式有 → 输入模式决定特征集，正是本次重构要消除的 |

删除的副产品：`0A(R1) − 0A(R2)` 天然量化该特征贡献，零额外成本。

**若将来要重建**：需要 step3 产出 per-sample 标量表（`sample_scalars.parquet`）并由 step5 合并 —— 这是指标层扩展，属 v1.2 backlog（G4），不混进本次解耦重构。

### 3.5 布林带触发量的外推

**方法**：取 400 只股票（排除指数），在非停牌 `close` 序列上算 `ma20 / sd20( ddof=1) / upper`，统计「首次上穿」，再按股票数等比外推。

| 项 | 值 |
| :--- | :--- |
| 样本股票数 | 397（有效） |
| 触发总数 | 50,844 |
| 每只均值 | 128.07（中位 128） |
| 每只年均 | ≈7 |
| 全市场（5367 只）外推 | **≈687,354** |
| 对比旧样本 240,257 | **2.9 倍** |
| 逐年分布 | 2.4×10⁴ ~ 5.5×10⁴，2025 年仅 242（数据截止） |

这是 `max_samples` 闸门与 §9.3 成本评估的来历。

### 3.6 回滚与复现

若要重跑上述任一验证：

| 验证 | 方法 |
| :--- | :--- |
| F1–F11、§3.2、§3.3 | 读 `data.raw_datebar`，按 §3.2/§3.3 的等式重算 |
| §3.4 | merge holding 与 dateBar，逐日截面 Spearman vs `close/open-1` |
| §3.5 | 取固定股票子集（需固定抽样种子，才能复现 50,844） |

> 这些验证脚本**未入库**。M0 的 `tools/probe_datebar.py` 会覆盖 F1–F11；§3.4/§3.5 属一次性调查，如需重做，按本节方法重写即可。

---

## 4. 决策记录（Q1–Q13）

### 4.1 第一轮（方案取舍）

| # | 问题 | 结论 | 理由 |
| :---: | :--- | :--- | :--- |
| Q1 | `samples` 模式 step1 如何处理空标签 | **丢弃**空标签行，`details` 报 `dropped_no_target` | 满足既有 `holding_target_non_null`，不改 `docs/07`；让不可用样本在最早位置暴露 |
| Q2 | `signal_date_selection_score` 如何处理 | **删除** | 见 §3.4：架构理由，不是统计理由 |
| Q3 | 基线锚点是否升级为自包含 | **是**（内联参数快照 + 两个 raw 文件 sha256），同时把回归配置提前入库 | 原文件只记 `configs/local/...` 路径，而该目录被 gitignore → 换机器即失效 |
| Q4 | `max_samples` 初值 | 写成**决策规则**：compact → `null`；medium → 先 20k 实测再定 cap | 69 万是外推值；未被实测前不拍数字 |
| Q5 | 标签口径版本怎么管 | **手动** `spec_version`，写进 prompt 硬约束 | 自动 hash 源码在 Windows + `core.autocrlf` 下会因换行差异误触发全量重算 |
| Q6 | 三个 0 字节 `*.new.md` | 删除 | 编辑器残留 |

### 4.2 第二轮（可移植性）

| # | 问题 | 结论 | 理由 |
| :---: | :--- | :--- | :--- |
| Q7 | v1.1 是否补「外部标签源（G1）」与「契约校验（G2）」 | **G1 不做，只补 G2** | 见 Q8 |
| Q8 | 是否允许外部标签文件 | **不允许**。标签只有一条路：内核内实现 + 注册 | 一旦允许，内核无法再对防泄漏负责；为便利开一个不可验证的入口，成本收益不成立 |
| Q9 | 与「短平快」定位的关系 | 一次实验若需自定义标签，写 ~60 行模板即可，**不为便利引入复杂机制** | 拒绝 G1/G3/G4 的即时改造；转正靠"反复用到"再沉淀 |

### 4.3 第三轮（交付结构）

| # | 问题 | 结论 | 理由 |
| :---: | :--- | :--- | :--- |
| Q10 | 设计是否拆成多份 | **不拆设计**，改为按**合并点**切里程碑 M0–M5 | 不变量必须单一来源；拆文档会造成契约漂移、且不减少总工作量 |
| Q11 | 实验是否留在 v1.1.0 | **移出**，独立为 `experiment_plan_v11.md` | 重构的完成标准是「数字相等」（可机械判定），实验是「结论可信」（需人工判断）；混在一起会让发布被研究结论绑架 |
| Q12 | 是否新增 `tools/verify_v11.py` | **是** | 把散文式验收变成 exit code，防「声称已对齐」 |
| Q13 | 该工具的长期地位 | **v1.1 专用一次性工具**：不得被 pipeline 引用、只读、M5 归档或删除、删除后项目必须照常运行 | 保持项目稳定，不新增长期依赖 |

### 4.4 被否决的方向

| 方向 | 否决理由 |
| :--- | :--- |
| step1 提取历史 OHLC（`hist_*`） | 与 step2 完全重复；数据量爆炸；与「step3 不变」互斥 |
| 标签在 step5 选择 | step4 已用 `target` 筛特征与定缩尾 → 会造成「筛选用 A 标签、训练用 B 标签」 |
| 标签文件单文件（`labels/{name}.parquet`） | samples 变化后被静默复用 |
| 标签增量补算 | 纯向量化计算重算成本远低于一致性风险 |
| 重建 `intraday_return` 作为新特征 | 架构上应在指标层；属 v1.2（G4） |
| 外部标签文件 | 内核无法验证时间口径（Q8） |
| 允许外部标签但打 `label_provenance` 标记 | 曾被评估为折中方案；最终认为「开了口子就无法负责」，不如不做 |
| 拆成 3 份设计文档 | 不变量被切碎；契约漂移；工作量不减少 |
| 把实验留在发布 Gate 内 | 让 agent 承担研究结论产出，是幻觉风险最高的地方 |

---

## 5. 与 `design_raw.md` 的逐节对应

| `design_raw` | 处置 |
| :--- | :--- |
| §1 背景/目标 | 保留；目标收窄为「只改 step1」 |
| §2 八条决策 | 保留 6 条；「step5 切标签」被 D3 推翻；「step1 提历史 OHLC」被 D2 删除 |
| §3 架构 / §4 契约 | 改写（真实列名、`samples_id`、`sample_id` 派生、单配置） |
| §5 模块职责 | 保留并补 `ids.py`、`universe.py`、CLI、`_index.json`、`_template.py` |
| §6 Phase 总览 | 改为 M0–M5（按合并点，不按主题）；实验另立 |
| §7 Phase 0 | → M0（探测固化 + 基线自包含） |
| §8 Phase 1 | → M1（samples 层）；去掉 `hist_*` |
| §9 Phase 2 | → M2（labels 层）；去掉增量补算 |
| §10 Phase 3 | 拆为 M3（接线，零变化）+ M4（step1，唯一行为变化） |
| §11 Phase 4 | **取消**（标签入口放 step5 的方法错误，见 D3） |
| §12 Phase 5 | → M5（收尾发布）；四组实验移入 `experiment_plan_v11.md` |
| §14–§21 | 保留结构，数值与结论按本文修正 |

---

## 6. 环境与工作区快照（2026-11-05/06）

| 项 | 状态 |
| :--- | :--- |
| 分支 | `refactor/v1.1`，与 `main` / `v1.0.13` 同一提交（`70d4341`） |
| 已跟踪改动 | `.gitignore`：新增忽略 `configs/local/`（机器绝对路径）与 `version_design/demo/`（外部参考模板） |
| 未跟踪 | `outputs/experiment_registry.json`、`version_design/design/refactor/*` |
| 解释器 | `C:/ProgramData/anaconda3/envs/factor311/python.exe`（3.11.13） |
| 原始数据 | 仓库内 `data/raw/` 只有 `.gitkeep` 与 `marks对照表.txt`；实际数据在 `D:/strategy_explore_total/strategy_explore/data/raw/`（由 `configs/local/refactor_check.yaml` 指向） |
| dataBar 大小 | 533.9 MB；holding 14.1 MB |
| 基线记录 | `baseline_v1.0.13.json` 当时只记配置路径与 12 个观测值，**无参数快照**（M0 需补齐） |

---

## 7. 历史教训：agent 常见失败模式

这些不是假想，是本次核对中真实暴露的倾向，`code_agent_prompt.md` 的规则即由此而来。

| 失败模式 | 具体表现 | 现设计中的对策 |
| :--- | :--- | :--- |
| **静默放宽判据** | check 不通过 → 删掉 check 或改成比例阈值 | V-2：不得改既有 check 名称与语义 |
| **伪造对拍** | 报告写「已对齐」但没跑，或手抄数字 | V-1：数字只能来自脚本；基线只能从 JSON 读 |
| **顺手重构** | 改 step2/step3「顺便优化」 | V-3：文件白名单 + `git diff --stat` |
| **契约漂移** | 用 `idx` 代替 `idxs`、把 `signal_date` 写成 datetime | adapter 契约 + 单测 + `check_raw_data` |
| **测试造假** | 只测 mock，不跑端到端 | 每个里程碑要求至少一条真跑 smoke |
| **提前宣布完成** | 说 M1 完成但缺 labels | V-4：verify 未过不得前进；每个里程碑独立 DoD |
| **凭命名推测字段** | 假设 `tradestatus` / `is_st` 存在 | §1 明确「以实测为准，不得凭命名推测」 |
| **隐式重复计算** | dateBar 被加载两次却不说 | 不变量 10：重复计算必须显式写出 |
| **口径混写** | 把 `target_raw` 与 `target` 的数字混用 | 契约明确列名与用途；报告要求标注口径 |

---

## 8. 如何复核本文

| 想确认的事 | 做法 |
| :--- | :--- |
| 某条修正是必需的 | 在 §2 找 ID → 看「冲突/遗漏」列 → 按 §3 的方法重跑证据 |
| 数据事实是否仍成立 | 跑 M0 的 `tools/probe_datebar.py --json` 与附录 A 对照 |
| 某方向为何被否 | §4.4 被否决方向表 |
| 设计是否覆盖了某需求 | 在 `experiment_design.md` 中搜索对应关键词；未覆盖则说明是遗漏 |
| 标题党怀疑「设计是否过于庞大」 | 见 §1 时间线与 §2 的 14 项阻塞：每一项都对应一次真实的「原方案跑不通」 |

---

## 文档结束

**一句话**：`experiment_design.md` 说「怎么建」，本文说「为什么这么建、以及为什么不那么建」。实施时只读前者。
