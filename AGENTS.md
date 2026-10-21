# AGENTS

## 1. 这是什么

这是一个可复用的 A 股信号实验内核；开一个新实验的标准路径是：克隆仓库、为新的 `base_version` 建入口，然后按 step1~step6 逐步运行。

原始数据不是启动前置条件：默认从 `data/raw/` 读取，路径由配置 `data.holding_file` / `data.datebar_file` 决定（可用相对或绝对路径）。没有数据也能先 scaffold 与提交，只是 step1~step6 无法运行；就绪与否用 `python tools/check_raw_data.py --config <配置>` 判定（0=就绪，2=缺失，3=契约不符）。

它负责的是稳定机制，而不是单次实验结论：

- 负责保存可复用的 step1~step6 执行方式；
- 负责保存输入契约、状态机、输出契约与收尾工具；
- 负责给新样本族提供统一的起步方式；
- 不负责替你决定某个样本族的策略方向或超参答案。

因此，遇到问题时先判断自己在做哪类工作：

- 如果你在开新样本族，优先改 `configs/`、`design/` 与数据入口；
- 如果你在修执行机制，优先改 `pipeline/` 与 `tools/`；
- 如果你在沉淀稳定知识，优先改 `docs/` 与本文件。

## 2. 目录职责

| 路径 | 职责 | 默认操作 |
| :--- | :--- | :--- |
| `pipeline/` | execution kernel：配置、状态机、step1~step6、即时评估与适配器 | 可改 |
| `tools/` | 收尾、脚手架、分析与回放辅助工具 | 可改 |
| `configs/` | 默认参数、实验注册表、bagging 模板与各谱系 YAML | 可改 |
| `docs/` | 跨谱系可复用知识、SOP、架构说明、模板文档 | 可改 |
| `data/raw/` | 样本族输入数据入口（持仓样本与日线原始文件；也可由配置指向别处） | 只读 |
| `outputs/` | 实验输出、报告、manifest 与运行登记 | 可改 |

补充约定：

- `models/` 保存训练好的模型文件与相关导出物。
- `design/` 属于各样本族/各版本的上下文层，不是内核文档主入口。
- `pipeline/branches/` 只放历史命名或非主线扩展；主线逻辑仍以 step1~step6 为准。

边界判断：

- 想确认“代码在哪里改”时，先看这一节。
- 想确认“规则是什么”时，转到 `docs/01_pipeline_contract.md`。
- 想确认“从模板克隆出一个新仓库怎么做”时，转到 `docs/12_clone_and_start.md`。
- 若你是被一段“引导提示词”派来从零开新仓库的，先读 `docs/13_agent_bootstrap_prompt.md`。
- 想确认“在已有仓库内开新谱系/新版本怎么做”时，转到 `docs/08_lineage_startup.md`。

## 3. 必读（开新谱系时）

先读两份：

1. `docs/01_pipeline_contract.md`
2. `docs/08_lineage_startup.md`

若是**从模板新建仓库**，把 `docs/12_clone_and_start.md` 放在这两份之前读。

若你是被一段**引导提示词**派来从零开新仓库的（不需要人工逐步操作），
直接按 `docs/13_agent_bootstrap_prompt.md` 的阶段划分执行。

其余文档按需读取：

- 需要排坑时看 `docs/03_pitfalls.md`
- 需要验收口径时看 `docs/07_step_acceptance_criteria.md`
- 需要收尾时看 `docs/06_closeout_playbook.md`
- 需要理解模块边界时看 `docs/10_architecture.md`

推荐阅读方式：

1. 先读硬约束，再读操作 SOP；
2. 开始动手前确认本次是否涉及共享中间产物；
3. 对拍旧行为时，把 `docs/11_baseline_fixture_guide.md` 一起打开。

## 4. 铁律

以下 6 条是执行边界，不得自行放宽：

1. `data/raw/` 只读，任何情况下不得修改或删除。
2. 严禁破坏性清理（不得删除 `outputs/`、`data/` 目录作为重置手段）。
3. 每条验证路径使用独立 `state-file`；同一次运行的 step1~step6 共用同一个 `state-file`；共享中间产物的验证必须串行。
4. 单步 CLI：`--mode {smoke,full} --step {1..6} --config <path> --state-file <name>`。
5. 严禁使用 `reset_index(drop=True)` 修改 `sample_id`。
6. 内核改动必须回灌 `ashare_signal_pipeline`，不在谱系仓库内直接改。

执行时还应同步遵守下列稳定约束：

- 全量模式必须按时间先后切分，并保留不少于 60 个交易日缓冲带。
- 特征筛选、标签缩尾分位数等依赖标签分布的统计量只能在训练集内部计算。
- 冒烟模式只验证管道畅通，不把其指标当成正式结论。
- 任何与旧行为对拍的验证，都必须走 YAML 加载路径，而不是裸构造参数对象。
- 共享 `data/processed/`、`data/slices/`、`data/features/` 等中间目录的验证，不得并行。

遇到下列情况应立刻停下并先澄清边界：

- 需要修改 `data/raw/`；
- 需要通过删除目录来“重置环境”；
- 需要在谱系仓库里直接改内核而不准备回灌模板；
- 需要让多个验证任务同时争用同一批中间目录。

## 5. 内核版本

模板仓库本身只维护 execution kernel；每个样本族 fork 自哪个内核版本、为何开启该轮迭代、对应的数据来源与目标边界，应记录在该版本目录下的 `design/<base_version>/<version_subdir>/CONTEXT.md`。

建议同时保持：

- `design/<base_version>/README.md`：记录该样本族已有的 `version_subdir`
- `design/<base_version>/<version_subdir>/CONTEXT.md`：记录该版本的 fork 来源、数据来源与目标
- `configs/experiment_index.yaml`：记录每个可执行配置的注册信息

版本记录最少应回答 4 个问题：

1. 这个样本族从哪个模板版本起步；
2. 这一轮迭代为什么存在；
3. 输入数据来自哪里、是否更换了样本；
4. 哪些改动属于谱系局部，哪些应回灌模板。

如果需要修改稳定规则，先更新本文件，再同步更新相关 `docs/` 文档；不要把跨谱系规则散落到单个实验目录中。