# 重构设计方案（最终版 v2.1）
## ashare_signal_pipeline → 事件驱动信号实验内核


## 阅读指南

| 部分 | 内容 | 读者目的 |
|---|---|---|
| **第一部分：全局设计视角** | 要建成什么——架构、决策、数据契约、模块职责 | 先理解整体形态 |
| **第二部分：落地路径** | 从哪开始——六个 Phase，每个可独立验收 | 按 Phase 顺序施工 |
| **第三部分：执行指南与附录** | 执行流程、约束、风险、工作量、交付物 | 施工参考手册 |

**核心原则**：第一部分是蓝图，第二部分是施工顺序。

---

# 第一部分：全局设计视角（要建成什么）

## 1. 背景与目标

### 1.1 背景

当前 `ashare_signal_pipeline` 是从**原始持仓样本**到**模型评估**的执行内核，主线固定为 step1–step6。它存在一个**架构级缺陷**：

> **样本集和标签共用同一个生成规则（布林带买入 + 跌破中轨卖出）。**  
> `holding_file` 是卖出规则的输出，标签 `holding_return` 也是卖出规则的输出。  
> LGBM 学的是“布林带卖出规则在什么形态下更赚”，不是“市场在什么形态下有正期望”。

### 1.2 目标

在不破坏 step1–6 固定语义的前提下，新增**数据准备层（data_prep）**，实现：

1. **采样层与标签层解耦**：采样只定义“哪些 (signal_date, symbol) 进入样本”，标签独立计算、独立存储；
2. **物理分离**：samples.parquet 只放骨架，标签独立文件；
3. **标签按需构造 + 缓存**：每次实验只构造需要的标签；
4. **step1 支持双输入模式**：兼容旧 `holding_file` 路径，新增 `samples.parquet` 路径；
5. **step5 改为“读标签文件 + join + 变换”**：不再内嵌标签计算逻辑。

### 1.3 非目标

- 不重构 step2–step4、step6 的核心逻辑；
- 不引入组合回测、持仓管理、资金曲线；
- 不改变防泄漏铁律；
- **不删除旧 `holding_file` 路径**，保留为对照模式；
- **本次重构只实现布林带突破上轨**，其他采样方法属于后续实验分支。

### 1.4 关键约束

| 约束 | 说明 |
|---|---|
| **OHLC 唯一来源是 dateBar** | `holding_file` 只提供交易记录，不提供价格 |
| **旧路径必须保留** | 阶段 0 的 0A（耦合基线）依赖旧路径 |
| **dateBar 字段必须先探测** | samples 列契约以真实字段为准 |
| **多周期标签批量算、独立落盘** | 一次读未来价格算多个 horizon，每个 horizon 一个文件 |
| **step1 负责提取历史 OHLC** | clean_samples 包含历史 OHLC 序列，供 step3 使用（R3） |

---

## 2. 核心设计决策（8 条）

| 决策 | 选择 | 理由 |
|---|---|---|
| data_prep 不叫 step0 | 叫 data_prep（不占编号） | step1–6 是执行主线，data_prep 生成样本，发生在主线之前 |
| samples/ 与 labels/ 并列 | 不嵌套 | 采样决定“哪些样本进入”，标签决定“学什么”，两者正交 |
| samples/ 下加 events/ 子目录 | events/ 是 samples/ 的子目录 | 采样是更广抽象 |
| samples.parquet 只放骨架 | 不含任何标签列 | X-Y 物理分离 |
| 标签独立文件 + 缓存 | `data/labels/{name}.parquet`，按需构造 | 版本独立、扩展方便、避免重复计算 |
| 保留旧 holding_file 路径 | 标记 `coupling_mode: path_dependent` | 阶段 0 的 0A 对照需要 |
| 本次只实现布林带突破上轨 | 其他采样方法是后续分支 | 范围可控 |
| dateBar 字段探测前置 | 先探测，再写代码 | 避免字段假设错误 |

---

## 3. 目标架构

### 3.1 分层结构

```
┌───────────────────────────────────────────────────────────────┐
│  数据准备层（data_prep/）—— 新增，不占 step 编号                │
│  ┌──────────────────────┐    ┌──────────────────────┐         │
│  │ samples/             │    │ labels/              │         │
│  │  - base.py           │    │  - base.py           │         │
│  │  - registry.py       │    │  - registry.py       │         │
│  │  - events/bollinger  │    │  - cache.py          │         │
│  │    （本次唯一）       │    │  - future_return     │         │
│  │                      │    │  - trend_quality     │         │
│  │                      │    │  - holding_return    │         │
│  └──────────────────────┘    └──────────────────────┘         │
│           ↓                            ↓                      │
│  ┌──────────────────────┐    ┌──────────────────────┐         │
│  │ build_samples.py     │    │ label_builder.py     │         │
│  │ 只生成骨架            │    │ 按需 + 缓存          │         │
│  └──────────────────────┘    └──────────────────────┘         │
└───────────────────────────────────────────────────────────────┘
           ↓                            ↓
   data/prepared/               data/labels/
   samples.parquet              target_future_5d.parquet
   （骨架，无标签）              target_future_20d.parquet
                                target_holding_return.parquet
           ↓                            ↓
           └────────────┬───────────────┘
                        ↓
┌───────────────────────────────────────────────────────────────┐
│  执行主线（pipeline/）—— step1–6 语义固定                      │
│  step1_clean（双模式：holding_file / samples）                 │
│       ↓                                                       │
│  step2_slices → step3_indicators → step4_features              │
│       ↓                                                       │
│  step5_split（读标签文件 + join + 变换）                        │
│       ↓                                                       │
│  step6_model                                                  │
└───────────────────────────────────────────────────────────────┘
```

### 3.2 数据流

```
data/raw/dateBar.pkl（OHLC + 交易日历，唯一价格源）
data/raw/holding_stats_all.parquet（可选，仅对照用）
        ↓
data_prep/samples/events/bollinger.py → 读取 dateBar 历史 OHLC → 生成采样触发点
        ↓
data_prep/tradability.py → 可交易性过滤 + 标记
        ↓
data_prep/build_samples.py → data/prepared/samples.parquet（骨架，无标签）
        ↓
step1_clean（input_mode="samples"）
    → 从 dateBar 提取每个触发点的历史 OHLC 序列（过去 N 天）
    → datebar_version 一致性检查
    → 输出 clean_samples.parquet（含历史 OHLC 序列）
        ↓
step2_slices → step3_indicators（使用 clean_samples 中的历史 OHLC）→ step4_features
        ↓
step5_split
    ├── 读取 data/labels/{target_column}.parquet（samples 模式）
    │   或 从 clean_samples 读取 holding_return（holding_file 模式）
    ├── 不存在或版本不匹配 → 触发 label_builder → 落盘缓存
    ├── 按 (signal_date, symbol) join 到 clean_samples
    ├── 变换（缩尾、分类、样本权重）
    └── 数据集划分
        ↓
step6_model
```

### 3.3 文件布局

```
ashare_signal_pipeline/
├── data/
│   ├── raw/
│   │   ├── dateBar.pkl
│   │   └── holding_stats_all.parquet
│   ├── prepared/                          # 新增
│   │   └── samples.parquet                # 骨架（无标签）
│   └── labels/                            # 新增
│       ├── target_future_5d.parquet
│       ├── target_future_10d.parquet
│       ├── target_future_20d.parquet
│       ├── target_future_40d.parquet
│       ├── target_trend_quality_20d.parquet
│       └── target_holding_return.parquet  # 对照，覆盖范围可能少于 samples
├── data_prep/                             # 新增
│   ├── samples/
│   │   ├── base.py
│   │   ├── registry.py
│   │   └── events/
│   │       └── bollinger.py               # 本次唯一采样方法
│   ├── labels/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── cache.py
│   │   ├── future_return.py
│   │   ├── trend_quality.py
│   │   └── holding_return.py
│   ├── tradability.py
│   ├── datebar_loader.py
│   ├── build_samples.py
│   └── label_builder.py
├── pipeline/
│   ├── step1_clean/                       # 改造：双模式 + 历史 OHLC 提取
│   ├── step2_slices/                      # 不变
│   ├── step3_indicators/                  # 不变（从 clean_samples 读历史 OHLC）
│   ├── step4_features/                    # 不变
│   ├── step5_split/                       # 改造：读标签文件
│   └── step6_model/                       # 不变
├── configs/
│   ├── data_prep.yaml                     # 新增
│   ├── pipeline.yaml                      # 修改：input_mode
│   └── step5.yaml                         # 修改：target.source + target.column
└── docs/
    ├── 01_pipeline_contract.md            # 更新
    ├── 10_architecture.md                 # 更新
    ├── 20_data_contract.md                # 新增
    ├── 21_datebar_probe.md                # 新增
    └── 30_refactor_design.md              # 本设计归档
```

---

## 4. 数据契约

### 4.1 samples.parquet 契约（骨架，无标签）

| 列类别 | 列名 | 类型 | 来源 |
|---|---|---|---|
| 标识 | `signal_date` | date | samples |
| 标识 | `symbol` | str | samples |
| 采样元信息 | `sampling_method` | str | build_samples |
| 采样元信息 | `sampling_params_hash` | str | build_samples |
| 可交易标记 | `is_tradable` | bool | tradability |
| 可交易标记 | `is_suspended` | bool | tradability |
| 可交易标记 | `is_limit_up` | bool | tradability |
| 可交易标记 | `is_limit_down` | bool | tradability |
| 可交易标记 | `is_st` | bool | tradability |
| OHLC（触发日） | `open` / `high` / `low` / `close` | float | dateBar（列名以探测结果为准） |
| 成交量（触发日） | `volume` / `amount`（若 dateBar 有） | float | dateBar |
| 元数据 | `datebar_version` | str | build_samples |
| 元数据 | `created_at` | datetime | build_samples |

**明确不含任何 `target_*` 列。不含历史 OHLC 序列（历史序列由 step1 生成）。**

### 4.1.1 clean_samples.parquet 契约（step1 输出）

**这是本次设计新增的契约，解决 step3 历史 OHLC 来源问题（R2/R3）。**

| 列类别 | 列名 | 类型 | 来源 | 说明 |
|---|---|---|---|---|
| 标识 | `signal_date` | date | samples | 触发日 |
| 标识 | `symbol` | str | samples | 股票代码 |
| 采样元信息 | `sampling_method` | str | samples | 采样方法名 |
| 采样元信息 | `sampling_params_hash` | str | samples | 采样参数哈希 |
| 可交易标记 | `is_tradable` / `is_suspended` / `is_limit_up` / `is_limit_down` / `is_st` | bool | samples | 透传 |
| OHLC（触发日） | `open` / `high` / `low` / `close` | float | samples | 透传 |
| 成交量（触发日） | `volume` / `amount` | float | samples | 透传 |
| **历史 OHLC 序列** | `hist_open` / `hist_high` / `hist_low` / `hist_close` / `hist_volume` | array[float] 或序列化格式 | **step1 从 dateBar 提取** | **过去 N 个交易日（N=100，可配置）的序列，供 step3 生成指标** |
| 耦合模式 | `coupling_mode` | str | step1 | `decoupled`（samples 模式）或 `path_dependent`（holding_file 模式） |
| 模式特有列 | `holding_return`（仅 holding_file 模式） | float | holding_file | 旧路径的标签列，透传给 step5 |
| 元数据 | `datebar_version` | str | samples 或 step1 | dateBar 版本 |
| 元数据 | `created_at` | datetime | step1 | 生成时间 |

**关键点**：
- **历史 OHLC 序列在 step1 提取**，step3 直接从 clean_samples 使用，不再读 dateBar；
- 历史序列长度 N=100（覆盖 step3 的最大指标窗口），可配置；
- 存储格式建议用 parquet 的 list 类型，或序列化为定长数组列（如 `hist_close_1` 到 `hist_close_100`），按 parquet 性能选择；
- **不含标签列**（holding_file 模式的 `holding_return` 例外，因为它是旧路径透传的，不是本设计新增的标签）。

### 4.2 标签文件契约

**路径**：`data/labels/{label_name}.parquet`（文件名即标签名，每个 horizon 一个文件）

| 列 | 类型 | 说明 |
|---|---|---|
| `signal_date` | date | 采样触发日 |
| `symbol` | str | 股票代码 |
| `target` | float | 标签值 |
| `label_name` | str | 标签名（冗余，方便校验） |
| `label_params_hash` | str | 标签参数哈希 |
| `datebar_version` | str | 对应 dateBar 版本 |
| `created_at` | datetime | 生成时间 |

**本次重构实现的标签文件**：

| 文件 | 定义 | 覆盖范围 |
|---|---|---|
| `target_future_5d.parquet` | P(T+6)/P(T+1) − 1 | samples.parquet 全量 |
| `target_future_10d.parquet` | P(T+11)/P(T+1) − 1 | samples.parquet 全量 |
| `target_future_20d.parquet` | P(T+21)/P(T+1) − 1 | samples.parquet 全量 |
| `target_future_40d.parquet` | P(T+41)/P(T+1) − 1 | samples.parquet 全量 |
| `target_trend_quality_20d.parquet` | 20 日收益 / 最大回撤 | samples.parquet 全量 |
| `target_holding_return.parquet` | 路径依赖收益（从 holding_file 读） | **仅覆盖能匹配到 holding_file 的样本**（R5） |

**R5 补充说明**：`holding_return` 标签的行数可能少于 samples.parquet，因为：
- samples.parquet 包含所有布林带突破触发点；
- holding_file 只包含“突破后最终跌破中轨”的完整交易；
- 突破后从未跌破中轨的样本（长趋势样本）在 holding_file 里可能不存在或被截断。

**这个样本数减少本身是可观测指标**，用于量化路径依赖筛选掉了多少样本。step5 使用 holding_return 时，join 后样本数减少，需在实验记录中注明。

### 4.3 防泄漏契约（R1 修正）

| 约束 | 适用层 |
|---|---|
| 采样只能用 `signal_date` 当日及以前的数据 | samples |
| 标签只能用 `signal_date` 之后的数据 | labels |
| 特征只能用 `signal_date` 当日及以前的数据 | step3/step4 |
| 训练/验证/测试严格时序隔离 | step5/step6 |
| 统计量只能在训练集内算 | step4/step5 |
| **`datebar_version` 必须记录，变化时 samples 和 labels 必须重建** | 全局 |
| **`sampling_params_hash` 必须记录，采样参数变化时 samples.parquet 重建（R1）** | samples |
| **`label_params_hash` 必须记录，参数变化时该标签文件重建** | labels |

### 4.4 缓存命中逻辑（R6 补充）

```
需要 target_future_20d
        ↓
检查 data/labels/target_future_20d.parquet
        ├── 存在 且 datebar_version 匹配 且 label_params_hash 匹配
        │       且 samples.parquet 的 (signal_date, symbol) 集合是标签文件的子集
        │       → 直接读
        └── 不存在 或 版本不匹配 或 参数哈希不匹配
               或 samples.parquet 有标签文件里没有的 (signal_date, symbol)
                → 触发计算（只算缺失部分）→ 落盘 → 读
```

**R6 补充说明**：标签文件依赖 samples.parquet 的 (signal_date, symbol) 集合。如果 samples.parquet 变了（比如换了采样方法、或加了新的交易日），标签文件需要：
- 如果新 samples 是旧 samples 的子集 → 标签文件可复用；
- 如果新 samples 有旧标签文件里没有的样本 → 增量计算缺失部分；
- 如果 datebar_version 变了 → 全部重建。

**简化实现（推荐）**：标签文件的元数据里记录 `samples_params_hash`（samples.parquet 的哈希）。如果当前 samples.parquet 的哈希与标签文件记录的不一致，标记标签文件为"可能不完整"，触发增量计算或全量重建。

---

## 5. 模块职责总表

| 模块 | 职责 | 输入 | 输出 | 接口契约 |
|---|---|---|---|---|
| `samples/base.py` | 定义采样基类 | — | — | `generate(datebar, config) -> DataFrame` |
| `samples/events/bollinger.py` | 布林带突破上轨采样 | datebar | 触发点表（signal_date, symbol） | 实现基类 |
| `labels/base.py` | 定义标签基类 | — | — | `compute(samples, datebar, config) -> Series` |
| `labels/future_return.py` | 未来 N 日收益 | samples, datebar | Series | 支持多 horizon 批量计算 |
| `labels/trend_quality.py` | 趋势质量 | samples, datebar | Series | — |
| `labels/holding_return.py` | 路径依赖收益 | samples, holding_file | Series（覆盖范围可能少于 samples） | 从 holding_file 读，仅对照 |
| `labels/cache.py` | 标签缓存管理 | — | — | `is_cached / load / save / invalidate` |
| `tradability.py` | 可交易性过滤 | datebar, config | 可交易标记 | — |
| `datebar_loader.py` | dateBar 加载与字段适配 | config | datebar DataFrame | 按探测结果映射字段 |
| `build_samples.py` | 生成骨架 | config | samples.parquet | 只做编排 |
| `label_builder.py` | 按需生成标签 | config, label_name | 标签文件 | 缓存命中检查 + 计算 + 落盘 |
| `step1_clean` | 双模式入口 + 历史 OHLC 提取 | holding_file 或 samples + datebar | clean_samples.parquet（含历史 OHLC） | `input_mode` 切换 |
| `step5_split` | 读标签 + join + 变换 | clean_samples + labels 或 clean_samples.holding_return | 划分后的数据集 | 配置 `target.source` + `target.column` |

---

# 第二部分：落地路径（从哪开始）

## 6. 六个 Phase 总览

| Phase | 目标 | 改 pipeline？ | 可独立验收？ | 依赖 | 关键风险 |
|---|---|---|---|---|---|
| **Phase 0** | dateBar 字段探测 | 否 | 是 | 无 | 字段缺失/格式不符 |
| **Phase 1** | samples 骨架生成 | 否（纯新增） | 是 | Phase 0 | 采样逻辑正确性 |
| **Phase 2** | labels 独立模块 + 缓存 | 否（纯新增） | 是 | Phase 1 | 标签计算正确性、缓存一致性 |
| **Phase 3** | step1 双模式 + 历史 OHLC | 是（只改 step1） | 是 | Phase 1 | 旧路径回归、历史 OHLC 提取正确性 |
| **Phase 4** | step5 改造 | 是（只改 step5） | 是 | Phase 2、Phase 3 | 旧路径回归、target.source 切换 |
| **Phase 5** | 端到端验证 | 否（只跑实验） | 是 | Phase 4 | 0A vs 0C 可解释性 |

---

## 7. Phase 0：dateBar 字段探测

### 7.1 目标
搞清楚 `data/raw/dateBar.pkl` 的真实结构，产出 `docs/21_datebar_probe.md`。

### 7.2 做什么

确认以下 10 项：

| 探测项 | 需要确认的内容 |
|---|---|
| 数据结构 | DataFrame / dict / 其他？索引结构？ |
| 价格列 | open/high/low/close 的列名？有无复权因子？ |
| 成交量列 | volume / amount 是否存在？列名？ |
| 交易状态列 | tradestatus / is_suspended / is_st 是否存在？ |
| 交易日历 | 如何存储？是 dateBar 字段还是独立文件？ |
| 股票代码列 | symbol / code 的列名和格式？ |
| 日期列 | date / trade_date 的列名和格式？ |
| 数据类型 | 价格是 float 还是 int？缺失值如何表示？ |
| 时间范围 | 最早/最晚日期？覆盖多少只股票？ |
| 复权方式 | 是否已复权？前复权还是后复权？ |

### 7.3 探测结果的缺失字段应对方案（R8 补充）

| 缺失字段 | 影响 | 应对方案 |
|---|---|---|
| 缺 volume / amount | 低流动性过滤无法实现 | 要求外部提供，或用价格波动代理，或跳过该项过滤（记录在探测文档） |
| 缺 tradestatus | 停牌判断无法直接做 | 用“当日无 OHLC”或“volume=0”（若 volume 有）替代 |
| 缺 is_st | ST 过滤无法直接做 | 要求外部提供 ST 列表（按日期区间），或在探测文档中记录缺失 |
| 缺 is_limit_up / is_limit_down | 涨跌停无法直接判断 | 用 pct_chg 接近 ±10% 判断（需确认涨跌停规则，如科创板 ±20%） |
| 缺复权因子 | 事件触发失真 | 要求外部提供复权因子，或在 data_prep 中做复权 |

### 7.4 不做什么
- 不写任何 data_prep 代码；
- 不改 step1–6；
- 不生成 samples.parquet 或标签文件。

### 7.5 交付物
- `docs/21_datebar_probe.md`（完整探测结果 + 缺失字段应对方案）。

### 7.6 验收标准
1. 探测文档完成，10 项全部有结论；
2. 缺失字段有明确应对方案；
3. 抽样 10 只股票 × 10 个交易日，人工核对 OHLC 与行情软件一致。

### 7.7 对现有系统的影响
**零**。只读数据、写文档。

### 7.8 检查点
**可以在这里停下。**

### 7.9 Commit 拆分
- `docs: 添加 datebar 字段探测文档`

---

## 8. Phase 1：samples 骨架生成

### 8.1 目标
新增 `data_prep/samples/` 和 `build_samples.py`，生成 `data/prepared/samples.parquet`（骨架，无标签，无历史 OHLC）。

### 8.2 做什么

| 模块 | 内容 |
|---|---|
| `data_prep/samples/base.py` | SampleDefinition 基类 |
| `data_prep/samples/registry.py` | 采样方法注册表 |
| `data_prep/samples/events/bollinger.py` | 布林带突破上轨（本次唯一） |
| `data_prep/tradability.py` | 可交易性过滤（按 Phase 0 探测结果实现） |
| `data_prep/datebar_loader.py` | dateBar 加载与字段适配 |
| `data_prep/build_samples.py` | 编排，只生成骨架 |
| `configs/data_prep.yaml` | 采样方法 + 可交易性配置 |

**布林带突破上轨参数**：`ma_period`（默认 20）、`std_mult`（默认 2.0）。只输出触发点。

### 8.3 不做什么
- **不碰 pipeline/step1–6**；
- 不实现标签模块；
- 不实现 label_builder；
- 不实现其他采样方法；
- **不在 samples.parquet 里生成历史 OHLC**（历史 OHLC 由 step1 在 Phase 3 生成）。

### 8.4 交付物
- `data/prepared/samples.parquet`（契约见 4.1）；
- 单元测试：`tests/test_samples/`、`tests/test_tradability/`、`tests/test_build_samples/`。

### 8.5 验收标准
1. samples.parquet 生成成功，列契约符合，**不含 target 列，不含历史 OHLC 列**；
2. 手工抽查 10 个触发点，与布林带突破逻辑一致；
3. 可交易性过滤生效；
4. 单元测试全部通过；
5. **pipeline/step1–6 完全没改，旧路径能跑通**。

### 8.6 对现有系统的影响
**零**。

### 8.7 检查点
**可以在这里停下。**

### 8.8 Commit 拆分
- `feat(data_prep): 添加 samples 基类和注册表`
- `feat(data_prep): 添加布林带突破采样`
- `feat(data_prep): 添加可交易性过滤`
- `feat(data_prep): 添加 build_samples（骨架生成）`
- `test(data_prep): 添加 samples 单元测试`

---

## 9. Phase 2：labels 独立模块 + 按需构造

### 9.1 目标
新增 `data_prep/labels/` 和 `label_builder.py`，按需生成 `data/labels/{name}.parquet`，缓存生效。

### 9.2 做什么

| 模块 | 内容 |
|---|---|
| `data_prep/labels/base.py` | LabelDefinition 基类 |
| `data_prep/labels/registry.py` | 标签注册表 |
| `data_prep/labels/cache.py` | 缓存管理（含 samples_params_hash 校验，R6） |
| `data_prep/labels/future_return.py` | 多周期：5d/10d/20d/40d，批量计算 |
| `data_prep/labels/trend_quality.py` | 趋势质量（20d） |
| `data_prep/labels/holding_return.py` | 路径依赖，从 holding_file 读，仅对照 |
| `data_prep/label_builder.py` | 按需生成 + 缓存 |

**批量计算优化**：一次请求多个 horizon 时，`future_return` 只读一次未来价格序列，一次算出所有 horizon，但**落盘为独立文件**。

### 9.3 不做什么
- **不碰 pipeline/step1–6**；
- 不改 samples.parquet；
- 不在 samples.parquet 里加任何标签列。

### 9.4 交付物
- `data/labels/` 下 6 个标签文件（契约见 4.2）；
- 单元测试：`tests/test_labels/`、`tests/test_label_builder/`。

### 9.5 验收标准
1. 5 个外生标签文件生成成功，行数 = samples.parquet 行数；
2. `target_holding_return.parquet` 生成成功，**行数可能少于 samples.parquet**（R5），验收时记录覆盖率；
3. 手工抽查 10 个样本的 5d/20d 标签，与手工计算一致；
4. **缓存命中**：第二次调用同一标签，不重新计算；
5. **缓存失效**：修改 `datebar_version` 或 `label_params_hash` 或 `samples_params_hash`，标签文件重建或增量更新；
6. 单元测试全部通过；
7. **pipeline/step1–6 完全没改，旧路径能跑通**。

### 9.6 对现有系统的影响
**零**。

### 9.7 检查点
**可以在这里停下。**

### 9.8 Commit 拆分
- `feat(data_prep): 添加 labels 基类和注册表`
- `feat(data_prep): 添加多周期 future_return`
- `feat(data_prep): 添加 trend_quality 和 holding_return`
- `feat(data_prep): 添加标签缓存管理`
- `feat(data_prep): 添加 label_builder（按需生成）`
- `test(data_prep): 添加 labels 和缓存单元测试`

---

## 10. Phase 3：step1 双模式 + 历史 OHLC 提取

### 10.1 目标
改造 `pipeline/step1_clean/`，支持 `input_mode: "samples"`，同时保留 `input_mode: "holding_file"` 旧路径不变。**新增历史 OHLC 提取逻辑**（R3）。

### 10.2 做什么

- step1 增加配置项 `input_mode`（`samples` / `holding_file`）；
- **step1 的 samples 模式**：
  - 读 samples.parquet；
  - **从 dateBar 提取每个触发点的历史 OHLC 序列（过去 N=100 天，可配置）**；
  - `datebar_version` 一致性检查（R7 修正：检查 samples.parquet 的 datebar_version 与当前 dateBar 是否一致，而非 signal_date 与 OHLC 日期）；
  - 可交易性二次校验；
  - 输出 clean_samples.parquet（含历史 OHLC 序列，契约见 4.1.1）；
  - 标记 `coupling_mode: decoupled`。
- **step1 的 holding_file 模式**（保持原逻辑 + 适配）：
  - 读 holding_file（交易记录）；
  - 从 dateBar 读触发日 OHLC + 历史 OHLC；
  - 清洗、标准化、落盘；
  - 输出 clean_samples.parquet（含 holding_return，透传给 step5）；
  - 标记 `coupling_mode: path_dependent`。
- 更新 `configs/pipeline.yaml`。

**R7 补充说明**：时间对齐检查的准确定义是：
- samples.parquet 里的 `datebar_version` 与当前 dateBar 的版本是否一致；
- 如果不一致，说明 samples.parquet 可能基于旧 dateBar 生成，需要重建；
- 这一步不是检查 "signal_date 与 OHLC 的日期是否一致"（因为它们本来就一致，OHLC 就是从 dateBar 的 signal_date 提取的）。

### 10.3 不做什么
- **不改 step2–step4、step6**；
- 不改 step5（Phase 4）；
- 不修改 samples.parquet 或标签文件。

### 10.4 交付物
- 改造后的 `pipeline/step1_clean/`（含历史 OHLC 提取）；
- 集成测试：`tests/test_step1_dual_mode/`。

### 10.5 验收标准
1. `input_mode: "holding_file"` 时，旧路径跑通，结果与改造前一致（分组差、样本数不变）；
2. `input_mode: "samples"` 时，新路径跑通，输出 clean_samples.parquet，**包含历史 OHLC 序列**（契约见 4.1.1）；
3. 手工抽查 5 个触发点，验证历史 OHLC 序列与 dateBar 一致；
4. `datebar_version` 不一致时，报错或提示重建；
5. 两种模式输出的 clean_samples.parquet 都包含 step2/step3/step4 所需的列；
6. 集成测试全部通过。

### 10.6 对现有系统的影响
**低**。只改 step1，旧路径保留。

### 10.7 检查点
**可以在这里停下。** 用 `holding_file` 模式跑旧实验确认结果一致——关键回归测试点。

### 10.8 Commit 拆分
- `refactor(step1): 支持双模式输入 + 历史 OHLC 提取`
- `test(step1): 添加双模式集成测试`

---

## 11. Phase 4：step5 改造

### 11.1 目标
改造 `pipeline/step5_split/`，支持从 `data/labels/{column}.parquet` 读标签 + join，同时保留 `holding_file` 模式的旧逻辑。

### 11.2 做什么（R4 补充）

- step5 增加配置项 `target.source`（`label_file` / `clean_samples`）和 `target.column`；
- **step5 的 samples 模式**（`target.source: label_file`）：
  - 读 `data/labels/{column}.parquet`；
  - 不存在或版本不匹配 → 触发 label_builder → 落盘缓存；
  - 按 `(signal_date, symbol)` join 到 clean_samples；
  - 变换（缩尾、分类、样本权重）；
  - 切分。
- **step5 的 holding_file 模式**（`target.source: clean_samples`）：
  - 从 clean_samples 读取 `holding_return` 作为 target（**不重新计算**，因为 step1 已经透传了，R4 修正）；
  - 变换 + 切分。
- 更新 `configs/step5.yaml`。

**配置示例**：

```yaml
# configs/step5.yaml
# samples 模式
target:
  source: "label_file"
  column: "target_future_20d"
  transform: "winsorize"
  winsorize_pct: [0.01, 0.99]
  classification: false
  sample_weight: "none"

# holding_file 模式
# target:
#   source: "clean_samples"
#   column: "holding_return"
#   transform: "winsorize"
#   winsorize_pct: [0.01, 0.99]
```

### 11.3 不做什么
- 不改 step2–step4、step6；
- 不再修改 step1；
- 不在 step5 内部重新实现标签计算。

### 11.4 交付物
- 改造后的 `pipeline/step5_split/`；
- 集成测试：`tests/test_step5_label_selection/`。

### 11.5 验收标准
1. `input_mode: "samples"` + `target.source: "label_file"` 时，step5 读标签文件，join 成功；
2. 切换 `target.column` 到 `target_future_40d`，跑通；
3. 标签文件不存在时，step5 自动触发 label_builder 生成；
4. `input_mode: "holding_file"` + `target.source: "clean_samples"` 时，从 clean_samples 读 holding_return，旧实验跑通；
5. 集成测试全部通过。

### 11.6 对现有系统的影响
**低**。

### 11.7 检查点
**可以在这里停下。**

### 11.8 Commit 拆分
- `refactor(step5): 支持 target.source 切换（label_file / clean_samples）`
- `test(step5): 添加标签选择集成测试`

---

## 12. Phase 5：端到端验证

### 12.1 目标
跑通阶段 0 的 0A vs 0C 对照，验证新架构能支持实验设计。

### 12.2 做什么

- 跑 0A（holding_file 模式 + target.source=clean_samples + holding_return）；
- 跑 0C（samples 模式 + target.source=label_file + target_future_20d）；
- 对比分组差、单调性、赔率；
- 跑 L0 随机排序 100 次；
- 跑打乱标签测试；
- 更新文档。

**Phase 5 只跑 0A vs 0C 作为架构验证，0D/0E 在后续实验分支跑。**

### 12.3 不做什么
- 不写新代码；
- 不实现新采样方法；
- 不实现 L1—L4 的区分函数对比。

### 12.4 交付物
- 阶段 0 对照报告；
- L0 随机排序结果；
- 打乱标签测试结果；
- 更新 `docs/20_data_contract.md`、`docs/01_pipeline_contract.md`、`docs/10_architecture.md`。

### 12.5 验收标准
1. 0A 和 0C 都能跑通；
2. 0A vs 0C 的分组差差异可解释；
3. L0 随机排序分组差均值 ≈0（|均值| < 0.01%）；
4. 打乱标签后分组差归零（<0.02%）；
5. 所有文档更新完成。

### 12.6 对现有系统的影响
**零**。

### 12.7 检查点
**可以在这里停下。**

### 12.8 Commit 拆分
- `docs: 更新 pipeline contract 和 architecture`
- `docs: 添加 samples 和 labels 数据契约`
- `docs: 添加阶段 0 对照报告`

---

## 13. Phase 依赖图

```
Phase 0（dateBar 探测）
    ↓
Phase 1（samples 骨架）—— 纯新增，不碰 pipeline
    ↓
Phase 2（labels 独立模块）—— 纯新增，不碰 pipeline
    ↓
Phase 3（step1 双模式 + 历史 OHLC）—— 第一次改 pipeline，保留旧路径
    ↓
Phase 4（step5 改造）—— 第二次改 pipeline，保留旧逻辑
    ↓
Phase 5（端到端验证）—— 只跑实验，不改代码
```

**注意**：Phase 3 依赖 Phase 1，Phase 4 依赖 Phase 2 和 Phase 3。Phase 1 和 Phase 2 可以并行，但建议按顺序做。

---

# 第三部分：执行指南与附录

## 14. 本地 agent 执行流程

```
1. clone 仓库
   git clone https://github.com/Nobody0Zy/ashare_signal_pipeline.git
   cd ashare_signal_pipeline

2. 按 Phase 顺序执行
   Phase 0 → 验收 → Phase 1 → 验收 → Phase 2 → 验收
   → Phase 3 → 验收 → Phase 4 → 验收 → Phase 5 → 验收

3. 每个 Phase 内：
   - 读本设计对应章节
   - 拆分成可执行的子任务
   - 每个子任务一个 commit
   - 单元测试 + 集成测试
   - 验收通过后进入下一 Phase

4. 遇到字段冲突：
   - 以 Phase 0 探测结果为准
   - 更新 docs/20_data_contract.md 和 docs/21_datebar_probe.md
   - 再继续

5. 上传：
   - 每个 Phase 完成后可独立 push
   - PR 描述包含：改动范围、验收结果、文档更新
```

---

## 15. 关键约束提醒（给本地 agent）

| 约束 | 说明 |
|---|---|
| **先探测再重构** | Phase 0 不做完，不写任何代码 |
| **不改 step2/step3/step4/step6** | 只改 step1 和 step5 |
| **保留旧路径** | holding_file 模式必须能跑通，标记 path_dependent |
| **samples.parquet 不含任何标签列** | 骨架只有 signal_date、symbol、OHLC、可交易标记、元数据 |
| **samples.parquet 不含历史 OHLC** | 历史 OHLC 由 step1 生成到 clean_samples |
| **标签独立文件 + 缓存** | `data/labels/{name}.parquet`，按需构造 |
| **多周期批量算、独立落盘** | 一次读未来价格，算多个 horizon，落盘为独立文件 |
| **samples/ 与 labels/ 并列** | 不嵌套 |
| **data_prep 不占 step 编号** | 不叫 step0 |
| **本次只实现布林带突破上轨** | 其他采样方法是后续分支 |
| **每步测试做裁判** | 单元测试通过才进下一步 |
| **防泄漏是红线** | 采样只能用 T 日及以前，标签只能用 T+1 之后 |
| **每个 Phase 完成即可停下** | 验收不通过可停，不影响已完成部分 |

---

## 16. 风险与待确认项

| 风险 | 影响 | 应对 |
|---|---|---|
| dateBar 缺少必要字段（volume、tradestatus） | 可交易性过滤无法完整实现 | Phase 0 探测确认，应对方案见 7.3 |
| dateBar 的 OHLC 列名与预期不符 | samples.parquet 列契约需调整 | Phase 0 探测确认，映射关系写入文档 |
| dateBar 未复权 | 采样触发和标签计算失真 | Phase 0 探测确认，必要时在 data_prep 中复权 |
| holding_file 与 dateBar 的股票代码格式不一致 | join 失败 | Phase 0 探测确认，统一格式 |
| 标签缓存版本管理复杂 | 缓存失效、重建时机 | cache.py 统一管理，datebar_version + label_params_hash + samples_params_hash 三重校验 |
| holding_return 覆盖范围可能少于 samples | step5 使用 holding_return 时样本数减少 | 在实验记录中注明覆盖率，这是可观测指标 |
| step1 历史 OHLC 提取性能 | 24 万样本 × 100 天序列，I/O 量大 | 用 parquet 的 list 类型，或序列化为定长数组列，按性能测试选择 |
| clean_samples 的存储大小 | 历史 OHLC 序列让 clean_samples 膨胀 | 按需压缩，或存为 float32 |
| 旧路径和新路径的 clean_samples 格式不一致 | step2 需兼容两套格式 | 统一 4.1.1 的契约，step1 负责格式转换 |
| 多重比较追踪缺失 | 过拟合风险 | sampling_params_hash 和 label_params_hash 记录所有试验 |

---

## 17. 工作量估计（按 Phase）

| Phase | 工作量 | 主要任务 |
|---|---|---|
| Phase 0 | 0.5 天 | 读数据、写探测文档（含缺失字段应对） |
| Phase 1 | 4 天 | samples 基类 + bollinger + tradability + build_samples + 测试 |
| Phase 2 | 5 天 | labels 基类 + 3 个标签器 + cache + label_builder + 测试 |
| Phase 3 | 3 天 | step1 双模式 + 历史 OHLC 提取 + 集成测试 |
| Phase 4 | 2 天 | step5 改造（target.source 切换）+ 集成测试 |
| Phase 5 | 2 天 | 0A vs 0C 对照 + L0 + 打乱标签 + 文档 |
| **总计** | **约 16.5 人天** | 不含后续新采样方法扩展 |

---

## 18. 交付物清单

### 18.1 代码（按 Phase）

| Phase | 新增/修改 |
|---|---|
| Phase 0 | `docs/21_datebar_probe.md` |
| Phase 1 | `data_prep/samples/`、`data_prep/tradability.py`、`data_prep/datebar_loader.py`、`data_prep/build_samples.py`、`configs/data_prep.yaml` |
| Phase 2 | `data_prep/labels/`、`data_prep/label_builder.py` |
| Phase 3 | `pipeline/step1_clean/`（双模式 + 历史 OHLC）、`configs/pipeline.yaml` |
| Phase 4 | `pipeline/step5_split/`、`configs/step5.yaml` |
| Phase 5 | `docs/01_pipeline_contract.md`、`docs/10_architecture.md`、`docs/20_data_contract.md` |

### 18.2 数据

| 文件 | 生成于 |
|---|---|
| `data/prepared/samples.parquet` | Phase 1 |
| `data/labels/target_future_5d.parquet` 等 6 个 | Phase 2 |
| `data/prepared/clean_samples.parquet` | Phase 3（step1 运行时生成） |

### 18.3 测试

| 测试 | 生成于 |
|---|---|
| `tests/test_samples/`、`tests/test_tradability/`、`tests/test_build_samples/` | Phase 1 |
| `tests/test_labels/`、`tests/test_label_builder/` | Phase 2 |
| `tests/test_step1_dual_mode/` | Phase 3 |
| `tests/test_step5_label_selection/` | Phase 4 |

---

## 19. 术语表

| 术语 | 含义 |
|---|---|
| **采样（sampling）** | 决定“哪些 (signal_date, symbol) 进入样本” |
| **事件驱动采样** | 由可观测市场状态触发的采样（如布林带突破） |
| **非事件采样** | 日历、随机、分层等（本次不实现） |
| **骨架（skeleton）** | samples.parquet 中不含标签、不含历史 OHLC 的部分 |
| **历史 OHLC 序列** | 触发日前 N 个交易日的 OHLC，供 step3 生成指标用 |
| **标签缓存** | data/labels/ 下的文件，避免重复计算 |
| **耦合模式** | 采样和标签共用同一生成规则（旧路径） |
| **解耦模式** | 采样和标签独立（新路径） |
| **X-Y-f 框架** | 样本（X）、标签（Y）、区分函数（f）三层分离 |

---

## 20. 决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| data_prep 是否叫 step0 | 否 | 不破坏 step1–6 固定语义 |
| samples/ 与 labels/ 是否嵌套 | 并列 | X-Y 边界清晰 |
| samples/ 下是否加 events/ | 是 | 采样是更广抽象 |
| 本次实现哪些采样方法 | 只有布林带突破上轨 | 范围可控 |
| 标签是否预计算多周期 | 按需构造 + 缓存 | 避免 samples 膨胀 |
| 标签文件粒度 | 每个标签一个文件 | 版本独立 |
| 旧 holding_file 路径是否保留 | 是 | 阶段 0 对照需要 |
| dateBar 字段探测是否前置 | 是 | 避免假设错误 |
| 历史 OHLC 由谁提取 | step1 | 统一入口，step3 不再读 dateBar（R3） |
| step5 target 来源 | `target.source` 配置切换 | samples 模式读标签文件，holding_file 模式读 clean_samples（R4） |

---

## 21. 与实验设计的映射

| 实验设计阶段 | 本重构的支持 | 落地 Phase |
|---|---|---|
| 阶段 -1：基础设施 | step1 双模式、防泄漏测试 | Phase 3 |
| 阶段 0：耦合诊断 | 旧路径（0A）+ 新路径（0C）对照 | Phase 5 |
| 阶段 1：标签外生化 | labels/ 多周期独立文件 + 缓存 | Phase 2 |
| 阶段 2：事件族扩展 | samples/events/ 后续新增（本次只布林带） | 后续分支 |
| 阶段 3：区分函数对比 | L0 随机排序（后续独立脚本）、L1—L4（后续扩展） | 后续分支 |
| 阶段 4：可交易性 | tradability.py 已在 data_prep 实现 | Phase 1 |
| 阶段 5：实盘验证 | 延后 | 后续 |

---

## 文档结束

**给本地工程 agent 的执行提示**：

1. **先读第一部分，理解整体架构和数据契约**；
2. **按第二部分 Phase 0—5 顺序施工**，每个 Phase 验收通过再进下一步；
3. **Phase 0—2 完全不碰 pipeline**，可以放心做；
4. **Phase 3—4 渐进式改 pipeline**，每个 Phase 完成后切回 `holding_file` 模式跑旧实验，确认无回归；
5. **Phase 5 只跑实验写文档**，不改代码；
6. **每个 Phase 完成即可独立 push**；
7. **遇到与 dateBar 字段冲突，以 Phase 0 探测结果为准**，更新数据契约文档后再继续；
8. **特别注意 R2/R3（历史 OHLC 由 step1 提取）和 R4（step5 target.source 切换）**，这是本版相对 v2.0 的关键修正。

**本版核对结论**：v2.1 修正了 v2.0 的 8 处关键遗漏（见文档开头的版本核对说明），架构、Phase 依赖、数据流、契约自洽，可交给工程 agent 具体化为 `experiment_design.md`、`implement_plan.md` 和 `code_agent_prompt`。