# ashare_signal_pipeline

`ashare_signal_pipeline` 是一个可复用的 A 股信号实验流水线模板，提供从原始持仓样本到 step1~step6 建模评估的执行内核，以及开新样本族时所需的最小 SOP、知识文档与工具脚手架。

仓库关注的是可迁移机制，而不是某一轮历史实验本身：

- 输入契约固定在 `data/raw/`
- 执行内核集中在 `pipeline/`
- 配置与注册集中在 `configs/`
- 跨谱系知识集中在 `docs/`

## 快速开始

1. 放数据  
   把 `holding_stats_all.parquet` 与 `dateBar.pkl` 放到 `data/raw/`。
2. 建谱系  
   运行 `python tools/scaffold_experiment_line.py <base_version> --version-subdir v0 --label baseline`。
3. 做 smoke  
   按 step1~step6 分步运行 `python run_experiment_pipeline.py --mode smoke --smoke 5000 --step <n> --config <path> --state-file <name>`。

## 目录结构

```text
ashare_signal_pipeline/
├── AGENTS.md
├── README.md
├── configs/
├── data/
├── docs/
├── models/
├── pipeline/
├── tools/
├── run_experiment_pipeline.py
└── run_bagging_experiment.py
```

## 文档导航

- 规则入口：`AGENTS.md`
- 上手导航：`docs/00_start_here.md`
- 硬约束：`docs/01_pipeline_contract.md`
- 开新谱系：`docs/08_lineage_startup.md`
- 架构总览：`docs/10_architecture.md`

## 环境要求

- Python 3.11
- 建议使用独立 conda 环境
- 能读取 `parquet` / `pickle` 数据文件
- 具备运行 LightGBM 与 tsfresh 所需的依赖

## 使用提醒

- `data/raw/` 只读，不要把它当作临时工作区。
- 不要靠删除 `outputs/`、`models/` 或 `data/` 子目录来“重置环境”。
- 多条验证路径若共享中间产物目录，必须串行执行并使用独立 `state-file`。

进一步的操作流程，请从 `AGENTS.md` 与 `docs/00_start_here.md` 开始。