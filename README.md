# ashare_signal_pipeline

`ashare_signal_pipeline` 是一个可复用的 A 股信号实验流水线模板，提供从原始持仓样本到 step1~step6 建模评估的执行内核，以及开新样本族时所需的最小 SOP、知识文档与工具脚手架。

仓库关注的是可迁移机制，而不是某一轮历史实验本身：

- 输入契约固定在 `data/raw/`
- 执行内核集中在 `pipeline/`
- 配置与注册集中在 `configs/`
- 跨谱系知识集中在 `docs/`

## 用 agent 从模板开新实验

如果你想让任意 coding agent（本机或远程设备均可）接手"从克隆到 smoke 跑通"的全过程，
把下面这段交给它即可：

> 按 `docs/13_agent_bootstrap_prompt.md` 执行。
> 参数：TEMPLATE_REF=`v1.0.3`，TARGET_DIR=`<绝对路径>`，
> BASE_VERSION=`<样本族名>`，VERSION_SUBDIR=`v0`，LABEL=`baseline`，
> DATA_DIR=`<原始数据目录>`，SMOKE_N=`500`，PUSH=`no`
>
> 若有未填项，先向我追问，不要自行假设。

完整的占位符说明、环境探测判据、自检门与变体见
**`docs/13_agent_bootstrap_prompt.md`**。

## 快速开始

> 完整的分步说明（含 clone、命名、remote 重设、首次提交）见 **`docs/12_clone_and_start.md`**。
> 本仓库是 GitHub Template：在仓库页点 `Use this template` 即可生成一个干净的新仓库。

1. 克隆并命名  
   `git clone <url> <base_version>`（目录名即样本族名，如 `lowvol`、`boll`）。
2. 放数据  
   把 `holding_stats_all.parquet` 与 `dateBar.pkl` 放到 `data/raw/`。
3. 建谱系  
   运行 `python tools/scaffold_experiment_line.py <base_version> --version-subdir v0 --label baseline`。
4. 做 smoke  
   按 step1~step6 分步运行 `python run_experiment_pipeline.py --mode smoke --smoke 5000 --step <n> --config <path> --state-file <name>`，每步用独立 `state-file`。

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
- 交给 agent 从零开新仓库：`docs/13_agent_bootstrap_prompt.md`
- 从模板克隆出新仓库（人类手册）：`docs/12_clone_and_start.md`
- 硬约束：`docs/01_pipeline_contract.md`
- 开新谱系/新版本：`docs/08_lineage_startup.md`
- 架构总览：`docs/10_architecture.md`

## 环境要求

- Python **3.11.x**（conda 环境、系统 Python、venv、pyenv 均可，不限定 conda）
- 以下 11 项依赖可导入：
  `numpy` `pandas` `pyarrow` `PyYAML` `tsfresh` `lightgbm`
  `scipy` `scikit-learn` `matplotlib` `psutil` `markdown`
- 能读取 `parquet` / `pickle` 数据文件

> 环境准备由使用者完成；agent 只负责探测与执行。解释器发现顺序见
> `docs/13_agent_bootstrap_prompt.md` §4.2。

## 使用提醒

- `data/raw/` 只读，不要把它当作临时工作区。
- 不要靠删除 `outputs/`、`models/` 或 `data/` 子目录来“重置环境”。
- 多条验证路径若共享中间产物目录，必须串行执行并使用独立 `state-file`。

进一步的操作流程，请从 `AGENTS.md` 与 `docs/00_start_here.md` 开始。