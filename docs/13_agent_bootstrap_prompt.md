# 13_agent_bootstrap_prompt

## 1. 这份文档解决什么问题

你要在**一台新设备**上，让**任意 coding agent**（不限具体产品）从本模板开出一个新的样本族实验。

本文给出的是一段**可直接复制使用的引导提示词**，以及它依赖的占位符、环境探测方法、自检门与变体。

它解决的是「克隆之前」的问题：`AGENTS.md` 只有在你**已经进入仓库**后才生效，而"把仓库拉下来"发生在它生效之前。所以引导指令必须有一个"克隆前就能拿到"的载体——就是本文。

> **与其它文档的分工**
>
> | 文档 | 读者 | 用途 |
> | :--- | :--- | :--- |
> | 本文 `13` | agent | 从零到 smoke 跑通的引导提示词 |
> | `12_clone_and_start.md` | 人 | 同一条路径的人类操作手册（含原理说明） |
> | `AGENTS.md` | agent | 进入仓库后的环境规则 |
> | `01_pipeline_contract.md` | agent | 硬约束细节 |

---

## 2. 占位符表

把提示词交给 agent 前，先填好下表。**空着的项 agent 会向你追问，不会自行编造。**

| 占位符 | 含义 | 示例 | 填错会怎样 |
| :--- | :--- | :--- | :--- |
| `{{TEMPLATE_URL}}` | 模板仓库地址 | `https://github.com/<owner>/ashare_signal_pipeline.git` | 克隆到错误仓库 |
| `{{TEMPLATE_REF}}` | 固定版本（tag 或 commit） | `v1.0.3` | 不 pin 会拿到漂移的内核 |
| `{{TEMPLATE_COMMIT}}` | 该 ref 对应的完整 commit hash | `129eb2cfb16c...` | 无法在浅克隆下校验版本 |
| `{{TARGET_DIR}}` | 新仓库落地目录（绝对路径） | `D:\lab\lowvol` | 写已有目录会覆盖 |
| `{{BASE_VERSION}}` | 样本族名 | `lowvol` | 输出目录与注册表全错 |
| `{{VERSION_SUBDIR}}` | 族内版本目录 | `v0` | 与既有版本混叠 |
| `{{LABEL}}` | 配置差异标签 | `baseline` | 多配置互相覆盖 |
| `{{DATA_DIR}}` | 原始数据所在目录 | `D:\raw\lowvol` | 数据契约校验失败 |
| `{{SMOKE_N}}` | 冒烟样本量 | `500` | 过小无意义、过大耗时长 |
| `{{PUSH}}` | 是否推送远端 | `no` / `yes` | 误推到模板仓库 |
| `{{REMOTE_URL}}` | 新远端地址（`PUSH=yes` 时必填） | `https://github.com/<you>/<repo>.git` | 推错位置 |

命名规则（`BASE_VERSION`）：

- 只用小写字母、数字、下划线；
- 不放版本号（不要 `v6`、`v7`）；
- 目录名建议与 `BASE_VERSION` 一致。

---

## 3. 引导提示词（复制整段）

```text
【任务】
从模板仓库克隆出一个新的样本族仓库，跑通 step1~step6 的 smoke，并完成防泄漏校验。
本任务只做 smoke，不做 full。

【已确认参数】
TEMPLATE_URL   = {{TEMPLATE_URL}}
TEMPLATE_REF   = {{TEMPLATE_REF}}
TEMPLATE_COMMIT= {{TEMPLATE_COMMIT}}
TARGET_DIR     = {{TARGET_DIR}}
BASE_VERSION   = {{BASE_VERSION}}
VERSION_SUBDIR = {{VERSION_SUBDIR}}
LABEL          = {{LABEL}}
DATA_DIR       = {{DATA_DIR}}
SMOKE_N        = {{SMOKE_N}}
PUSH           = {{PUSH}}
REMOTE_URL     = {{REMOTE_URL}}

若上面有未填项，先向我追问，不要自行假设。

【阶段 0 · 环境探测】结果填入证据表，任一不满足则停止报告
  1. 找到可用的 Python 解释器：版本需为 3.11.x
  2. 该解释器能 import：numpy pandas lightgbm tsfresh pyarrow yaml
  3. 到 TEMPLATE_URL 的网络可达（git ls-remote 成功）
  4. TARGET_DIR 不存在，或其父目录可写
  5. 磁盘剩余空间 >= 5 GB

  禁止：为通过探测而自行 pip install / conda install / 更换解释器。
  依赖版本会改变特征集，导致结果不可比。不满足就停下来问我。

【阶段 1 · 克隆】
  git clone --branch TEMPLATE_REF --depth 1 TEMPLATE_URL TARGET_DIR
  cd TARGET_DIR

  # 按 tag 克隆会得到 detached HEAD，必须先挂回主线分支，否则后续 commit 不属于任何分支
  git checkout -B main

  校验：
    - git rev-parse HEAD 等于 TEMPLATE_COMMIT
    - git status --short 为空
    - 工作树文件数与模板一致（当前版本为 54）
  
  若 clone 失败：
    按顺序尝试：① 读取系统代理后带代理重试 ② 改用 HTTP/1.1 重试
    最多 3 次；仍失败则停止报告完整 stderr，不要猜测代理地址。

【阶段 2 · 本地化】
  1. 设置本仓库提交身份（不用 --global）：
       git config user.name "<你的名字>"
       git config user.email "<你的邮箱>"
  2. 把 configs/default.yaml 的 base_version 改为 BASE_VERSION
  3. 建立 design/BASE_VERSION/VERSION_SUBDIR/CONTEXT.md，记录：
       fork 自、fork 日期、样本族、数据来源、本轮目标、是否修改内核

【阶段 3 · 数据契约】
  python pipeline/adapters/sample_family.py --show-contract
  把 DATA_DIR 下的 holding_stats_all.parquet 与 dateBar.pkl 放入 data/raw/
  校验两文件都存在，且字段覆盖契约要求的列。
  缺字段时停止报告，不要自行补造数据。

【阶段 4 · 生成谱系入口】
  python tools/scaffold_experiment_line.py BASE_VERSION \
      --version-subdir VERSION_SUBDIR --label LABEL
  
  必须校验（任一不符则停止报告）：
    a. 生成的 YAML 是完整配置快照（含 manual_features 等段），不是几行 stub
    b. include_date / include_year / include_month 三项均为 false
    c. configs/experiment_index.yaml 仍是合法 YAML，且包含本条注册

【阶段 5 · 跑 smoke】
  同一配置、同一模式，step1~step6 必须共用同一个 state-file。
  
  $cfg = "configs/BASE_VERSION/VERSION_SUBDIR/BASE_VERSION_LABEL.yaml"
  $st  = "pipeline_state_smoke_BASE_VERSION_VERSION_SUBDIR_LABEL.json"
  foreach step in 1..6:
      python run_experiment_pipeline.py --mode smoke --smoke SMOKE_N \
          --step step --config $cfg --state-file $st
      若 exit != 0 则停止报告
  
  严禁每步换一个新 state-file：状态机会报"前置步骤未完成"。

【阶段 6 · 产物与防泄漏校验】
  1. 输出目录位于 outputs/BASE_VERSION/VERSION_SUBDIR/<run_id>/
  2. state 中 step_1..step_6 全部 completed 且 checks_passed = true
  3. predictions_smoke.csv 与 metadata/experiment_manifest.json 存在
  4. analysis/eval_summary.json 的 calendar_gain_share 必须等于 0.0
  
  第 4 条是硬门：非 0 说明日历特征被启用，必须停止并报告。

【阶段 7 · 提交】
  git add configs design
  git commit
  校验作者为你设置的身份；git status --short 为空。
  若 PUSH=no：不执行任何 push，报告里写明"未推送"。
  若 PUSH=yes：
      git remote add origin REMOTE_URL
      git ls-remote --heads origin    # 可写时继续
      git push -u origin main
      禁止 --force。

【禁止事项】
1. 不修改 pipeline/ 或 tools/ 下任何代码。需要改才能通过 → 停止报告。
2. 不修改 data/raw/ 内容（只读）。
3. 不用删除 outputs/ models/ data/ 的方式重置环境。
4. 不使用 --mode full。
5. 不编造任何指标；所有数值必须来自实际文件。
6. 不自行安装或升级依赖。

【阻塞处理】
出现以下任一情况，立即停止并报告（阶段编号 / 完整命令 / 完整 stderr / 已尝试动作 / 建议）：
- 阶段 0 任一探测项不满足
- 克隆失败或 HEAD 与 TEMPLATE_REF 不符
- 数据契约缺字段
- 生成配置中日历开关不为 false
- 任一 smoke 步骤失败
- calendar_gain_share != 0
- 需要改内核代码才能继续

【交付格式】
一、环境证据表（解释器路径/版本、六项依赖、网络、磁盘）
二、克隆结果（目录、HEAD、tags、文件数）
三、本地化（身份、base_version、CONTEXT 摘要）
四、数据契约（文件清单、契约摘要）
五、scaffold（生成文件、配置行数、三项日历开关原文、index 条目）
六、smoke（逐步 exit code、耗时、checks 摘要）
七、产物与防泄漏（输出目录、文件清单、六步状态、calendar_gain_share）
八、提交（commit、作者、status）
九、结论（逐条打勾）
   [ ] 未改内核即跑通
   [ ] 配置为完整快照且日历开关全 false
   [ ] 六步 smoke 全通过
   [ ] calendar_gain_share = 0.0
   [ ] 未改 data/raw、未用 full、未装依赖
十、未完成项
```

---

## 4. 环境探测的判据说明

阶段 0 的每一项都不是形式检查，都对应一类真实故障。

| 项 | 判据 | 不满足的后果 | 为什么不让 agent 自行修复 |
| :--- | :--- | :--- | :--- |
| Python 版本 | `3.11.x` | 语法/依赖不兼容 | 换版本会改变依赖解析结果 |
| 六项依赖可导入 | 能 import、不报错 | 中途失败，浪费前面步骤 | 装新版会改变 tsfresh 计算器集合 |
| 网络可达 | `git ls-remote` 成功 | 无法克隆 | 猜代理只会浪费轮次 |
| 目标目录可用 | 不存在或父目录可写 | 覆盖已有工作 | — |
| 磁盘余量 | `>= 5 GB` | smoke 尚可，full 会爆盘 | — |

> **为什么依赖版本如此关键**：模板冻结了 `seed=42`、`num_threads=1`、`chunk_size` 等确定性参数，但 tsfresh 的**计算器集合**随版本变化。升级后特征列会静默改变，`calendar_gain_share` 之外的指标也随之变化，结论不可比。

---

## 5. 自检门

在每个阶段末尾设置门，不通过就停，不允许"先跑通再说"。

| 门 | 位置 | 通过条件 | 不通过 |
| :---: | :--- | :--- | :--- |
| **G1** | 阶段 0 末 | 五项探测全绿 | 停止，不进入克隆 |
| **G2** | 阶段 4 末 | 配置为完整快照、三项日历开关全 false、index 合法 | 停止报告（这是模板缺陷，需回灌） |
| **G3** | 阶段 5 末 | 六步 `exit=0` 且 `checks_passed=true` | 停止，附失败步骤的 checks |
| **G4** | 阶段 6 末 | `calendar_gain_share == 0.0` | 停止，说明防泄漏被破坏 |

G2 与 G4 是**模板级回归门**：它们失败说明模板本身有问题，而不是使用者操作错误。

---

## 6. 四个变体

### 6.1 变体 A · 不推送（默认）

`PUSH = no`。用途：验证模板可用性、本地实验、离线环境。

### 6.2 变体 B · 推到新的远端

`PUSH = yes`，并填 `REMOTE_URL`。用于开一个长期维护的谱系仓库。

前提：远端仓库**必须已存在且为空**。若建库时勾了 README/license，push 会被拒；此时让用户重建，**不要用 `--force`**。

### 6.3 变体 C · 保留模板为 upstream

在阶段 2 追加：

```text
git remote rename origin upstream
git remote add origin REMOTE_URL
```

用于日后想吸收模板改进的谱系。注意：模板改进**不会自动同步**，需手动 `git fetch upstream` 后挑选提交，不要整体 merge。

### 6.4 变体 D · 离线 / 无网络

把克隆换成从离线包解压：

```text
# 前提：用户已提供模板的 zip/tar 包，并已知其对应的 TEMPLATE_REF
解压到 TARGET_DIR
cd TARGET_DIR
git init -b main
git add -A
git commit -m "import template TEMPLATE_REF"
```

其余阶段不变。注意两点：

1. **没有模板 git 历史**，`git rev-parse HEAD` 无法与 `TEMPLATE_COMMIT` 比对。
   改为校验包内是否存在 `docs/13_agent_bootstrap_prompt.md`，且其「更新记录」末行版本与 `TEMPLATE_REF` 一致。
2. 首次 commit 是本仓库的**第 1 条提交**，`v0` 基线从这里开始。

---

## 7. 常见失败

| 现象 | 原因 | 处理 |
| :--- | :--- | :--- |
| `step2` 报"前置步骤 1 未完成" | 每步换了 state-file | 六步共用一个文件 |
| `calendar_gain_share != 0` | 配置中 `include_date/year/month` 为 true | 停止；用完整配置快照，勿手写 YAML |
| `experiment_index.yaml` 解析失败 | `description` 含冒号未加引号 | 停止；这是模板缺陷，回灌修复 |
| `git status` 显示 `Not currently on any branch` | 按 tag 克隆得到 detached HEAD | 执行 `git checkout -B main` |
| `git rev-parse <tag>^{commit}` 报 `unknown revision` | `--depth 1` 浅克隆未取到 tag ref | 改用 `{{TEMPLATE_COMMIT}}` 比对 HEAD |
| `push` 报 `Repository not found` | 远端为空但 URL/账号不符 | 核对 URL 与凭据，勿 force |
| `push` 报 `fetch first` | 远端已有提交 | 让用户重建空仓库 |
| 特征列数与预期不符 | 依赖版本与模板记录不一致 | 按阶段 0 的判据排查，勿降级 |
| `data/raw` 缺字段 | 数据不是该契约的产物 | 停止；看 `01_pipeline_contract.md` |

---

## 8. 更新记录

| 版本 | 变更 |
| :--- | :--- |
| v1.0.3 | 新建本文；提取自实际 smoke 验证中暴露的两个缺陷（日历开关、state-file 语义） |
| v1.0.4 | 修正阶段 1：按 tag 克隆会得到 detached HEAD，补 `git checkout -B main`；新增 `{{TEMPLATE_COMMIT}}` 占位符，因浅克隆下 tag ref 不可用 |

> 本文与 `12_clone_and_start.md` 描述同一条路径，但读者不同：前者面向 agent（指令式），后者面向人（解释式）。两者内容若冲突，以本文的阶段划分与判据为准。
