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
| `{{PYTHON}}` | 已准备好的 Python 3.11 解释器路径 | `C:\Python311\python.exe` | agent 无法执行后续命令 |
| `{{TARGET_DIR}}` | 新仓库落地目录（绝对路径） | `D:\lab\lowvol` | 写已有目录会覆盖 |
| `{{BASE_VERSION}}` | 样本族名 | `lowvol` | 输出目录与注册表全错 |
| `{{VERSION_SUBDIR}}` | 族内版本目录 | `v0` | 与既有版本混叠 |
| `{{LABEL}}` | 配置差异标签 | `baseline` | 多配置互相覆盖 |
| `{{DATA_DIR}}` | 原始数据所在目录；**可留空** | `D:\raw\lowvol` 或留空 | 留空=无数据模式（跳过 smoke） |
| `{{SMOKE_N}}` | 冒烟样本量 | `500` | 过小无意义、过大耗时长 |
| `{{PUSH}}` | 是否推送远端 | `no` / `yes` | 误推到模板仓库 |
| `{{REMOTE_URL}}` | 新远端地址（`PUSH=yes` 时必填） | `https://github.com/<you>/<repo>.git` | 推错位置 |
| `{{MODE}}` | 运行模式 | `first-run` / `regression` | `regression` 才会执行 capsule 检查 |
| `{{ENV_SETUP}}` | 缺依赖时是否允许自行安装 | `auto` / `forbidden` | `auto` 会建 venv 装钉版依赖 |

### 2.1 MODE 与 ENV_SETUP 的选择

| 参数 | 取值 | 行为 |
| :--- | :--- | :--- |
| `MODE` | `first-run` | 阶段 0–7；不检查 capsule。适合日常开新谱系。 |
| | `regression` | 阶段 0–7 + **阶段 6.5 capsule 检查**。适合验证模板、升级后回归。 |
| `ENV_SETUP` | `auto` | 缺依赖时自动建 venv 并安装**钉版依赖集**；无 3.11 时可用 winget 安装。 |
| | `forbidden` | 缺依赖就停下报告，由用户补齐。 |

**默认建议**：`MODE=regression` + `ENV_SETUP=auto`（首次在新设备验证）；
之后日常开谱系用 `MODE=first-run`。

> `ENV_SETUP=auto` 的约束：只装钉版版本、只装进 TARGET_DIR 之外的新 venv、
> 不改动系统 Python 与已有 conda 环境。这样才不会让特征列因版本漂移而静默变化。

### 2.2 验证 vs 开谱系：先选对获取方式

| 场景 | 获取方式 | `PUSH` | 说明 |
| :--- | :--- | :--- | :--- |
| **验证模板 / 回归** | 直连 clone 模板（可 pin `TEMPLATE_REF`） | `no` | `commit` 只落本地，不碰模板 |
| **开新谱系并长期维护** | ① `Use this template` 生成独立仓库后 clone<br>② 或直连 clone + 改 `origin`（docs/12 §3.2） | `yes` | 生成式仓库无 tag，但 `origin` 天然正确 |

关键区别：

- **直连 clone 后 `origin` 指向模板仓库**。此时 `push` 会把内容推到模板上，必须先改地址。
- **`Use this template` 生成的仓库没有 tag**，所以不能用 `--branch TEMPLATE_REF`，但也因此不需要改 `origin`。

> 只做验证就用第一种；要开长期谱系用第二种。混用会踩 `origin` 指向的坑。

### 2.3 数据前提（可选启动条件，但跑 smoke 必需）

**模板不包含任何样本数据。** 仓库里 `data/raw/` 只有一个 `.gitkeep`，
真实数据被 `.gitignore` 排除（`data/raw/*`），因此 `git clone` 拿不到。

数据**不是建谱系的前置条件**：没有数据也能完成 clone → scaffold → 配置快照 → 提交，
只是不能跑 step1~step6。要不要在本轮跑 smoke，由 `{{DATA_DIR}}` 是否填写决定。

**文件名与落点不写死在代码里**，由配置的 `data` 段决定：

```yaml
data:
  holding_file: data/raw/holding_stats_all.parquet   # 相对仓库根，或绝对路径
  datebar_file: data/raw/dateBar.pkl
```

新谱系的数据命名不同时，只改这两行；不要改内核代码。

| 项 | 默认约定（可改） |
| :--- | :--- |
| holding 文件 | `data/raw/holding_stats_all.parquet` |
| datebar 文件 | `data/raw/dateBar.pkl` |
| 参考体积 | 约 549 MB（parquet 14 MB + pkl 534 MB） |
| 来源 | 由使用者提供（U 盘、网络共享、内部数据平台等） |
| 是否随模板分发 | **否** |

**就绪检查**（在阶段 3 使用）：

```powershell
python tools/check_raw_data.py --config <配置路径> --data-dir <外部目录> --check-columns
```

| 退出码 | 含义 | 后续动作 |
| :---: | :--- | :--- |
| `0` | 就绪 | 可以跑 step1~step6 |
| `2` | 数据缺失 | **无数据模式**：继续 scaffold 与提交，跳过阶段 5/6/6.5 |
| `3` | 文件存在但不可读或缺列，或 `--data-dir` 指向不存在的目录 | 停止报告；先修数据或改 `data.*`，不要伪造 |

三种数据供给方式：

| 方式 | 覆盖 | 说明 |
| :--- | :--- | :--- |
| 拷入 `data/raw/`（保持默认命名） | 100% | 推荐；549 MB 可用 U 盘或共享目录 |
| 外部目录 + `--data-dir` | 100% | 不复制文件；校验时只取配置里的文件名 |
| 本轮无数据 | ~55% | 只验克隆、scaffold、配置快照、日历开关；提交时注明「无数据模式」 |

> 数据命名与默认不同时，把 `data.*` 改指向真实文件，再用新配置重跑一次
> `check_raw_data.py --config <新配置>`，确认 `status=ready` 后再跑 smoke。

命名规则（`BASE_VERSION`）：

- 只用小写字母、数字、下划线；
- 不放版本号（不要 `v6`、`v7`）；
- 目录名建议与 `BASE_VERSION` 一致。

---

## 3. 引导提示词（复制整段）

```text
【任务】
从模板仓库克隆出一个新的样本族仓库。
- DATA_DIR 已填写：跑通 step1~step6 的 smoke，并完成防泄漏校验。
- DATA_DIR 为空：进入**无数据模式**，完成克隆、scaffold、配置快照与提交，
  跳过阶段 5 / 6 / 6.5，并在结论中标注「无数据模式，未验证管道可运行」。
本任务只做 smoke，不做 full。

【已确认参数】
TEMPLATE_URL   = {{TEMPLATE_URL}}
TEMPLATE_REF   = {{TEMPLATE_REF}}
TEMPLATE_COMMIT= {{TEMPLATE_COMMIT}}
PYTHON         = {{PYTHON}}
TARGET_DIR     = {{TARGET_DIR}}
BASE_VERSION   = {{BASE_VERSION}}
VERSION_SUBDIR = {{VERSION_SUBDIR}}
LABEL          = {{LABEL}}
DATA_DIR       = {{DATA_DIR}}          # 可留空：留空 = 无数据模式
SMOKE_N        = {{SMOKE_N}}
PUSH           = {{PUSH}}
REMOTE_URL     = {{REMOTE_URL}}
MODE           = {{MODE}}          # first-run | regression
ENV_SETUP      = {{ENV_SETUP}}     # auto | forbidden

若上面有未填项，先向我追问，不要自行假设。

【阶段 0 · 环境探测】结果填入证据表，任一不满足按 ENV_SETUP 规则处理
  1. 找到可用的 Python 解释器：版本需为 3.11.x
     （发现顺序与脚本见下方"解释器发现脚本"，直接执行，不要凭猜）
  2. 该解释器能 import 以下 11 项：
       numpy pandas pyarrow yaml tsfresh lightgbm
       scipy sklearn matplotlib psutil markdown
  3. 到 TEMPLATE_URL 的网络可达（git ls-remote 成功）
  4. TARGET_DIR 不存在，或其父目录可写
  5. 磁盘剩余空间 >= 5 GB

  ── 解释器发现脚本（PowerShell，逐条执行）──
  $cands = @()
  if (Get-Command conda -ErrorAction SilentlyContinue) {
    (& conda env list) -split "`n" | Where-Object { $_ -match '^\S' -and $_ -notmatch '^#' } |
      ForEach-Object { $p = ($_ -split '\s+')[-1].Trim(); if ($p) { $cands += "$p\python.exe" } }
  }
  foreach ($root in @('C:\ProgramData\anaconda3','C:\ProgramData\miniconda3',
                      "$env:USERPROFILE\anaconda3","$env:USERPROFILE\miniconda3")) {
    if (Test-Path "$root\python.exe") { $cands += "$root\python.exe" }
    if (Test-Path "$root\envs") {
      Get-ChildItem "$root\envs" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { $cands += "$($_.FullName)\python.exe" }
    }
  }
  if (Get-Command py -ErrorAction SilentlyContinue) {
    $p = ((& py -3.11 -c "import sys;print(sys.executable)" 2>$null) -join '').Trim()
    if ($p) { $cands += $p }
  }
  $cands += @("$env:LOCALAPPDATA\Programs\Python\Python311\python.exe","C:\Python311\python.exe")
  $cands += (Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })

  foreach ($c in ($cands | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) {
    if ($c -match '\\Microsoft\\WindowsApps\\') { "{0,-72} SKIP (store alias)" -f $c; continue }
    $v = ((& $c --version 2>&1) -join '')
    if ($v -notmatch '3\.11') { "{0,-72} SKIP ({1})" -f $c, $v.Trim(); continue }
    $ok = ((& $c -c "import numpy,pandas,pyarrow,yaml,tsfresh,lightgbm,scipy,sklearn,matplotlib,psutil,markdown;print('OK')" 2>&1) -join '').Trim()
    if ($ok -eq 'OK') { "{0,-72} USABLE" -f $c }
    else { "{0,-72} MISSING: {1}" -f $c, ($ok -split "`n" | Select-Object -Last 1) }
  }
  ── 脚本结束 ──

  ENV_SETUP 规则：
    forbidden（默认）
      - 只能使用探测到的 USABLE 解释器。
      - 缺依赖 / 没有 3.11 → 停止报告，由用户补齐。
    auto
      - 若找到 3.11 但缺依赖：允许建独立 venv 并安装下方"钉版依赖集"。
      - 若完全没有 3.11：允许用 winget 安装 Python 3.11，再建 venv 装依赖。
      - 安装位置必须在 TARGET_DIR 之外（例：$env:USERPROFILE\.venv-asp）。
      - 严禁改动系统 Python、conda base、或其他已有环境。
      - 必须使用"钉版依赖集"中的精确版本，不得升级/降级。
      - 安装后重新执行探测，确认 11 项全绿，并把实际版本写入证据表。

  ── 钉版依赖集（与模板验证环境一致，必须用这些版本）──
  numpy==1.26.4
  pandas==2.2.2
  pyarrow==21.0.0
  PyYAML==6.0.3
  tsfresh==0.21.2
  lightgbm==4.6.0
  scipy==1.17.1
  scikit-learn==1.7.2
  matplotlib==3.9.2
  psutil==7.1.1
  markdown==3.10.3
  （Python 3.11.x；参考环境为 3.11.13）

  auto 模式的安装命令模板：
    py -3.11 -m venv $env:USERPROFILE\.venv-asp
    & "$env:USERPROFILE\.venv-asp\Scripts\python.exe" -m pip install --upgrade pip
    & "$env:USERPROFILE\.venv-asp\Scripts\python.exe" -m pip install `
        numpy==1.26.4 pandas==2.2.2 pyarrow==21.0.0 PyYAML==6.0.3 `
        tsfresh==0.21.2 lightgbm==4.6.0 scipy==1.17.1 `
        scikit-learn==1.7.2 matplotlib==3.9.2 psutil==7.1.1 markdown==3.10.3
    # 之后 PYTHON 改用该 venv 的 python.exe

  为什么必须钉版：tsfresh 的计算器集合随版本变化，会导致特征列静默改变；
  不钉版就无法与模板的历史结果对比。

【阶段 1 · 克隆】
  git clone --branch TEMPLATE_REF --depth 1 TEMPLATE_URL TARGET_DIR
  cd TARGET_DIR

  # 按 tag 克隆会得到 detached HEAD，必须先挂回主线分支，否则后续 commit 不属于任何分支
  git checkout -B main

  校验：
    - git rev-parse HEAD 等于 TEMPLATE_COMMIT
    - git status --short 为空
    - 工作树文件数与模板一致（当前版本为 54）
    - git remote get-url origin 等于 TEMPLATE_URL（记下这个事实，阶段 7 要用）

  本提示词固定使用【直连 clone 模板】的方式，目的是 pin 到 TEMPLATE_REF 从而可复现。
  若你要长期维护一个 GitHub 谱系仓库（而不是本地验证），推荐改用
  「Use this template 生成独立仓库后再 clone」——那种仓库没有 tag，
  不能用 --branch TEMPLATE_REF，但 origin 天然指向你自己的仓库，无需改地址。
  两种方式的取舍见 docs/12 §1 与 §3。
  
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

【阶段 3 · 数据契约（可跳过）】
  数据是可选启动条件。先做一次就绪检查，不要用空文件或伪造数据绕过：

  # DATA_DIR 非空时检查外部目录；为空时去掉 --data-dir 参数（检查 data/raw/）
  <PYTHON> tools/check_raw_data.py --data-dir DATA_DIR --check-columns
  # 若该族用了非默认文件名，阶段 4 生成配置后再补跑：
  #   <PYTHON> tools/check_raw_data.py --config <生成的配置> --check-columns

  按退出码处理：
    0 = 就绪                       → 复制 DATA_DIR 下两个文件到 data/raw/（若用了外部目录），继续阶段 4
    2 = 数据缺失                    → 进入无数据模式：继续阶段 4 与阶段 7，跳过阶段 5/6/6.5，
                                      提交说明与本轮结论都必须写明「无数据模式」
    3 = 存在但不可读/缺列，或 DATA_DIR 指向不存在的目录 → 停止报告，不要自行补造数据

  约定：本阶段不因「没有数据」而中止；只有「数据存在但契约不符」才是阻塞。
  需要列名清单时：<PYTHON> pipeline/adapters/sample_family.py --show-contract

【阶段 4 · 生成谱系入口】
  python tools/scaffold_experiment_line.py BASE_VERSION \
      --version-subdir VERSION_SUBDIR --label LABEL
  
  必须校验（任一不符则停止报告）：
    a. 生成的 YAML 是完整配置快照（含 manual_features 等段），不是几行 stub
    b. include_date / include_year / include_month 三项均为 false
    c. configs/experiment_index.yaml 仍是合法 YAML，且包含本条注册
    d. YAML 含 data 段（holding_file / datebar_file）；有数据时指向本族真实文件，无数据模式保留默认约定即可

【阶段 5 · 跑 smoke】
  前置：阶段 3 的检查结果为 0（就绪）。若为 2（数据缺失），跳过阶段 5/6/6.5，
  直接到阶段 7，并在结论中写明「无数据模式」。

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

【阶段 6.5 · capsule 可移植性检查】（仅 MODE=regression 执行；first-run 跳过本阶段）
  目的：smoke 只走 step1~6，不经过 capsule 工具；本阶段补上这个盲区。
  
  1. 备份：Copy-Item tools\frozen_experiments.yaml tools\frozen_experiments.yaml.bak
  2. 用本次 run_id 填充 tools/frozen_experiments.yaml：
       base_version: "BASE_VERSION"
       master_library_version: ""
       master_library_docs: []
       protected_paths: {}
       experiments:
         - experiment_version: <run_id>
           status: canonical
           config_path: configs/BASE_VERSION/VERSION_SUBDIR/BASE_VERSION_LABEL.yaml
           max_step: 6
           summary_lines:
             - "MODE=regression 验证"
  3. 导出：python tools\export_replay_capsule.py <run_id> --output-dir design\_cap
  4. 断言 A：产物中存在 capsule md 文件
  5. 断言 B：capsule 内不含本机硬编码
       Select-String -Path design\_cap\*.md -Pattern 'anaconda|factor311|C:/ProgramData|C:\\ProgramData'
       期望 0 命中
  6. 断言 C：复跑命令以 python 开头
       Select-String -Path design\_cap\*.md -Pattern '^python run_experiment_pipeline'
       期望 >= 1 命中
  7. 还原：Move-Item tools\frozen_experiments.yaml.bak tools\frozen_experiments.yaml -Force
  8. 清理：Remove-Item design\_cap -Recurse -Force
  
  任一断言失败 → 停止报告，并注明"这是模板缺陷，不是环境问题"。
  注意：本阶段会临时修改 frozen_experiments.yaml，必须确保还原成功后再继续。

【阶段 7 · 提交】
  git add configs design
  # 无数据模式没有输出登记文件，缺失时跳过而不是报错
  if (Test-Path outputs/experiment_registry.json) { git add outputs/experiment_registry.json }
  git commit -m "start BASE_VERSION/VERSION_SUBDIR LABEL (smoke|no-data)"
  
  校验：
    - 提交作者为你设置的身份
    - git ls-files 中不含 data/raw 下除 .gitkeep 之外的文件
    - git status --short 为空
  
  `data/raw/` 的原始数据属于本地资产，已被 .gitignore 排除，不会也不应被提交。
  若发现它出现在待提交列表里，说明 .gitignore 被改动，停止报告。

  若 PUSH=no：不执行任何 push，报告里写明"未推送"。

  ⚠️ push 前的安全守卫（PUSH=yes 时必做）：
       git remote get-url origin
     若输出等于 TEMPLATE_URL → 立即停止报告：
       "origin 仍指向模板仓库，禁止 push，否则会污染模板"
     这是直连 clone 的默认状态；必须在 push 之前先改地址。

  若 PUSH=yes：
      # 直连 clone 后 origin 已存在且指向模板，必须改地址而不是新增
      # （新增会报 "remote origin already exists"；对不存在的 remote 用 set-url 会报
      #   "No such remote 'origin'"）
      git remote get-url origin
      # 若上面报错（origin 不存在）→ git remote add origin REMOTE_URL
      # 若上面成功（origin 指向模板） → git remote set-url origin REMOTE_URL
      git remote get-url origin                 # 必须等于 REMOTE_URL
      git ls-remote --heads origin    # 可写时继续
      git push -u origin main
      禁止 --force。

  若需要保留模板为 upstream（日后吸收模板改进），在阶段 2 追加：
      git remote rename origin upstream
      然后在本阶段用 git remote add origin REMOTE_URL

【禁止事项】
1. 不修改 pipeline/ 或 tools/ 下任何代码。需要改才能通过 → 停止报告。
2. 不修改 data/raw/ 内容（只读）。
3. 不用删除 outputs/ models/ data/ 的方式重置环境。
4. 不使用 --mode full。
5. 不编造任何指标；所有数值必须来自实际文件。
6. 依赖只能按 ENV_SETUP 规则处理：
   - ENV_SETUP=forbidden：不得安装或升级任何依赖。
   - ENV_SETUP=auto：只能安装"钉版依赖集"中的精确版本，且只能装进 TARGET_DIR 之外的新 venv；
     不得升级、不得降级、不得改动系统 Python 或已有 conda 环境。
7. 不得使用 git push --force。

【阻塞处理】
出现以下任一情况，立即停止并报告（阶段编号 / 完整命令 / 完整 stderr / 已尝试动作 / 建议）：
- 阶段 0 任一探测项不满足（ENV_SETUP=forbidden 时）
- ENV_SETUP=auto 时安装失败，或装完后 11 项仍不全
- check_raw_data.py 退出码 3（数据存在但不可读或缺列）
- DATA_DIR 非空但目录不存在（空值属正常，走无数据模式）
- 克隆失败或 HEAD 与 TEMPLATE_COMMIT 不符
- 生成配置中日历开关不为 false
- 任一 smoke 步骤失败
- calendar_gain_share != 0
- MODE=regression 时 6.5 的任一断言失败
- PUSH=yes 时 origin 仍等于 TEMPLATE_URL（未经改地址就试图 push）
- 需要改内核代码才能继续

【交付格式】
一、环境证据表
   解释器路径 / Python 版本 / 11 项依赖实际版本 / 网络 / 磁盘
   若执行了安装：说明原因、venv 路径、安装命令
二、克隆结果（目录、HEAD、tags、文件数）
三、本地化（身份、base_version、CONTEXT 摘要）
四、数据状态（check_raw_data.py 退出码与结论：ready / missing / invalid）
    missing 时写明「无数据模式，本轮不验证管道可运行」
五、scaffold（生成文件、配置行数、三项日历开关原文、data 段、index 条目）
六、smoke（逐步 exit code、耗时、checks 摘要；无数据模式写"已跳过"）
七、产物与防泄漏（输出目录、文件清单、六步状态、calendar_gain_share；无数据模式写"已跳过"）
八、capsule 检查（仅 MODE=regression：断言 A/B/C 结果；first-run 或无数据模式写"已跳过"）
九、提交（commit、作者、status、是否推送）
十、结论（逐条打勾）
   [ ] 环境 11 项依赖齐全（钉版）
   [ ] 未改内核即跑通（或已标注无数据模式、未验证）
   [ ] 配置为完整快照、含 data 段且日历开关全 false
   [ ] 六步 smoke 全通过（无数据模式不勾选）
   [ ] calendar_gain_share = 0.0（无数据模式不勾选）
   [ ] 未改 data/raw、未用 full
   [ ] MODE=regression 时 capsule 检查通过（无数据模式不勾选）
十一、未完成项
```

---

## 4. 环境探测的判据说明

阶段 0 的每一项都不是形式检查，都对应一类真实故障。

| 项 | 判据 | 不满足的后果 | 为什么不让 agent 自行修复 |
| :--- | :--- | :--- | :--- |
| Python 版本 | `3.11.x` | 语法/依赖不兼容 | 换版本会改变依赖解析结果 |
| 11 项依赖可导入 | 能 import、不报错 | 中途失败，浪费前面步骤 | 装新版会改变 tsfresh 计算器集合 |
| 网络可达 | `git ls-remote` 成功 | 无法克隆 | 猜代理只会浪费轮次 |
| 目标目录可用 | 不存在或父目录可写 | 覆盖已有工作 | — |
| 磁盘余量 | `>= 5 GB` | smoke 尚可，full 会爆盘 | — |

> **为什么依赖版本如此关键**：模板冻结了 `seed=42`、`num_threads=1`、`chunk_size` 等确定性参数，但 tsfresh 的**计算器集合**随版本变化。升级后特征列会静默改变，`calendar_gain_share` 之外的指标也随之变化，结论不可比。

### 4.1 依赖完整清单

阶段 0 要求 11 项全部可导入。缺任意一项都可能让流程走到中途才失败：

| # | 包 | 首次用到的位置 | 缺失时的表现 |
| :---: | :--- | :--- | :--- |
| 1 | `numpy` | 全链路 | step1 即失败 |
| 2 | `pandas` | 全链路 | step1 即失败 |
| 3 | `pyarrow` | step1 写 parquet | step1 即失败 |
| 4 | `PyYAML` | `config.py` | 配置加载失败 |
| 5 | `tsfresh` | step4 | step4 失败 |
| 6 | `lightgbm` | step6 | step6 失败 |
| 7 | `scipy` | `step6_model.py` 的 `scipy.stats` | **step6 失败** |
| 8 | `scikit-learn` | `step6_model.py` 的 `sklearn.metrics` | **step6 失败** |
| 9 | `matplotlib` | `analyze_experiment_results.py`、`bagging.py` | **分析/集成阶段失败** |
| 10 | `psutil` | `monitoring.py` | 开启 monitoring 时失败 |
| 11 | `markdown` | `md_report_to_html.py` | 报告转 HTML 失败 |

> 前 6 项是"早期就知道缺"，后 5 项是"跑很久才缺"。阶段 0 一次查全，避免跑到 step6 才发现。

### 4.2 解释器发现顺序

不同机器上 Python 的来源差异很大，**不要假定存在 conda，也不要假定 `python` 在 PATH 上**。
按下面顺序找，找到即停：

| 序 | 来源 | 探测命令 | 说明 |
| :---: | :--- | :--- | :--- |
| 1 | conda 命令 | `conda env list` | 有 conda 且在 PATH 时最快；环境名任意，不要求 `factor311` |
| 2 | **conda 安装目录**（不依赖命令） | 枚举 `<conda_root>\envs\*\python.exe` | **`conda` 不在 PATH 但环境存在时唯一可行路径**（实测踩过） |
| 3 | py launcher | `py -0p` / `py -3.11 -c "import sys;print(sys.executable)"` | Windows 装了 Python 就有，**不需要 PATH** |
| 4 | 注册表与常见路径 | `HKLM:\SOFTWARE\Python\PythonCore`、`%LOCALAPPDATA%\Programs\Python\Python311`、`C:\Python311` | 覆盖安装时未勾 PATH 的情况 |
| 5 | PATH | `where.exe python` / `Get-Command python -All` | 可能有多个，需逐个验证版本 |
| 6 | 都没有 | ENV_SETUP=auto → 安装（§4.3）；forbidden → 停下报告 | agent 只在 auto 下自行安装 |

对每个候选，都必须同时满足：`--version` 为 3.11.x **且** 11 项依赖可导入。
只有版本对但缺依赖的，记录下来；ENV_SETUP=auto 时可建 venv 补齐，否则报给用户。

> 上面的脚本已内联到 §3 提示词的"阶段 0"中，粘贴提示词即可执行，无需再翻本节。

**必须跳过 Windows 应用商店别名**：`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` 是一个
指向商店的 stub，执行时输出 "Python was not found but can be installed from the Microsoft Store"。
它不是真正的解释器，必须识别并跳过，否则会误判为"找到了 Python"。

> PowerShell 探测脚本示例（可直接粘贴）：
>
> ```powershell
> $cands = @()
>
> # 1) conda 命令（可能不存在）
> if (Get-Command conda -ErrorAction SilentlyContinue) {
>   (& conda env list) -split "`n" | Where-Object { $_ -match '^\S' -and $_ -notmatch '^#' } |
>     ForEach-Object { $p = ($_ -split '\s+')[-1].Trim(); if ($p) { $cands += "$p\python.exe" } }
> }
>
> # 2) conda 安装目录（conda 不在 PATH 时的补救）
> foreach ($root in @('C:\ProgramData\anaconda3','C:\ProgramData\miniconda3',
>                     "$env:USERPROFILE\anaconda3","$env:USERPROFILE\miniconda3")) {
>   if (Test-Path "$root\python.exe") { $cands += "$root\python.exe" }
>   if (Test-Path "$root\envs") {
>     Get-ChildItem "$root\envs" -Directory -ErrorAction SilentlyContinue |
>       ForEach-Object { $cands += "$($_.FullName)\python.exe" }
>   }
> }
>
> # 3) py launcher
> if (Get-Command py -ErrorAction SilentlyContinue) {
>   $p = ((& py -3.11 -c "import sys;print(sys.executable)" 2>$null) -join '').Trim()
>   if ($p) { $cands += $p }
> }
>
> # 4) 常见安装路径
> $cands += @("$env:LOCALAPPDATA\Programs\Python\Python311\python.exe", "C:\Python311\python.exe")
>
> # 5) PATH
> $cands += (Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
>
> foreach ($c in ($cands | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) {
>   # 跳过 Windows 应用商店别名
>   if ($c -match '\\Microsoft\\WindowsApps\\') { "{0,-72} SKIP (store alias)" -f $c; continue }
>   $v = ((& $c --version 2>&1) -join '')
>   if ($v -notmatch '3\.11') { "{0,-72} SKIP ({1})" -f $c, $v.Trim(); continue }
>   # 必须 -join 后再比较：stderr 混入时 & 返回的是数组而非字符串
>   $ok = ((& $c -c "import numpy,pandas,pyarrow,yaml,tsfresh,lightgbm,scipy,sklearn,matplotlib,psutil,markdown;print('OK')" 2>&1) -join '').Trim()
>   if ($ok -eq 'OK') { "{0,-72} USABLE" -f $c }
>   else { "{0,-72} MISSING: {1}" -f $c, ($ok -split "`n" | Select-Object -Last 1) }
> }
> ```

> **三个实测踩过的陷阱**：
> 1. `& $exe ...` 在 stderr 有输出时返回**数组**而不是字符串。比较前必须 `-join ''`。
> 2. 不要把多行脚本存成字符串用 `&` 执行；存成 `.ps1` 文件再跑。写文件时用 **UTF-8 无 BOM**，
>    否则中文会被破坏、引号闭合失败。
> 3. `conda` 可能不在 PATH 上，但 `factor311` 之类的环境仍然存在——
>    此时只能靠第 2 步直接枚举 `envs` 目录。

### 4.3 没有可用解释器时

分两种情况，取决于 `ENV_SETUP`：

| ENV_SETUP | 行为 |
| :--- | :--- |
| `auto` | **agent 自行安装**：winget 装 Python 3.11 → 建 venv → 装钉版依赖 → 重新探测 |
| `forbidden` | 停止报告，由用户补齐后重新执行 |

**auto 模式的完整步骤**（agent 执行）：

```powershell
# 1) 安装 Python 3.11（若完全没有）
winget install -e --id Python.Python.3.11
# 离线/老系统改用 python.org 官方安装包，勾选 "py launcher"；不要用 embeddable zip

# 2) 建独立 venv（必须在 TARGET_DIR 之外）
py -3.11 -m venv $env:USERPROFILE\.venv-asp
& "$env:USERPROFILE\.venv-asp\Scripts\python.exe" -m pip install --upgrade pip

# 3) 安装钉版依赖（版本见 §3 阶段 0 的"钉版依赖集"，不得改动）
& "$env:USERPROFILE\.venv-asp\Scripts\python.exe" -m pip install `
    numpy==1.26.4 pandas==2.2.2 pyarrow==21.0.0 PyYAML==6.0.3 `
    tsfresh==0.21.2 lightgbm==4.6.0 scipy==1.17.1 `
    scikit-learn==1.7.2 matplotlib==3.9.2 psutil==7.1.1 markdown==3.10.3

# 4) 重新探测
& "$env:USERPROFILE\.venv-asp\Scripts\python.exe" -c "import numpy,pandas,pyarrow,yaml,tsfresh,lightgbm,scipy,sklearn,matplotlib,psutil,markdown;print('OK')"
```

装完把该 venv 的 `python.exe` 路径填入 `{{PYTHON}}`，继续阶段 1。

**三条硬约束**（auto 模式下同样适用）：

1. 只装钉版版本，**不得升级或降级**（tsfresh 版本影响计算器集合）。
2. 只装进 TARGET_DIR 之外**新建的 venv**，不得污染系统 Python、conda base 或已有环境。
3. 把实际安装的版本写进证据表，便于追溯。

> **用什么解释器都行**：conda 环境、系统 Python、venv、pyenv 均可。
> 唯一硬性要求是 Python 3.11.x + 11 项依赖**且版本与钉版集一致**。

### 4.4 平台支持

| 项 | 状态 |
| :--- | :--- |
| Windows + PowerShell | **已验证**（同一 OS 内跨目录、跨解释器路径） |
| 另一台 Windows 设备 | 结构上支持；步骤相同，仅解释器路径与数据路径不同 |
| Linux / macOS + bash | **未验证**；代码用 `pathlib`，结构上支持，但需把第 3 节示例换成等价 bash |
| 命令形式 | capsule 中的复跑命令以 `python ` 开头，不绑定解释器绝对路径 |

---

## 5. 自检门

在每个阶段末尾设置门，不通过就停，不允许"先跑通再说"。

| 门 | 位置 | 通过条件 | 不通过 |
| :---: | :--- | :--- | :--- |
| **G1** | 阶段 0 末 | 五项探测全绿（11 项依赖齐全） | ENV_SETUP=auto 时先尝试安装后复测；仍不绿则停止 |
| **G2** | 阶段 4 末 | 配置为完整快照、三项日历开关全 false、index 合法 | 停止报告（这是模板缺陷，需回灌） |
| **G3** | 阶段 5 末 | 六步 `exit=0` 且 `checks_passed=true` | 停止，附失败步骤的 checks |
| **G4** | 阶段 6 末 | `calendar_gain_share == 0.0` | 停止，说明防泄漏被破坏 |
| **G5** | 阶段 6.5 末（仅 regression） | capsule 存在、无本机硬编码、命令以 `python` 开头 | 停止，说明工具仍有硬编码 |

G2 / G4 / G5 是**模板级回归门**：它们失败说明模板本身有问题，而不是使用者操作错误。
G1 属于环境问题，`ENV_SETUP=auto` 时允许 agent 自行修复一次。

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
| `git status` 显示 `data/raw/*.parquet` 待提交 | `.gitignore` 未覆盖该层 | 停止；原始数据不应提交 |
| `git status` 显示 `Not currently on any branch` | 按 tag 克隆得到 detached HEAD | 执行 `git checkout -B main` |
| `git rev-parse <tag>^{commit}` 报 `unknown revision` | `--depth 1` 浅克隆未取到 tag ref | 改用 `{{TEMPLATE_COMMIT}}` 比对 HEAD |
| `push` 报 `Repository not found` | 远端为空但 URL/账号不符 | 核对 URL 与凭据，勿 force |
| `push` 报 `fetch first` | 远端已有提交 | 让用户重建空仓库 |
| 特征列数与预期不符 | 依赖版本与模板记录不一致 | 按阶段 0 判据核对版本；ENV_SETUP=auto 时重装钉版 |
| `data/raw` 缺字段 | 数据不是该契约的产物 | 停止；看 `01_pipeline_contract.md` |
| `check_raw_data.py` 退出码 2 | 数据还没放到位 | 无数据模式可继续 scaffold；跑 smoke 前补数据 |
| `check_raw_data.py` 退出码 3 | 文件不可读、缺列，或 `--data-dir` 不存在 | 修数据或改 `data.*`；勿硬跑 |
| `pip install` 报找不到版本 | 钉版在 PyPI 上不存在或 Python 版本不匹配 | 停止报告，附完整报错；勿改成不带版本的安装 |
| `winget` 不可用 | Win10 < 1809 或离线 | 停止，让用户用 python.org 安装包 |

---

## 8. 用作模板回归

改了模板（`pipeline/`、`tools/`、`configs/default.yaml`、文档生成逻辑）之后，用本文跑一遍即可确认没有回归。

**回归 = 同一套阶段 0–7 + `MODE=regression` + 一个一次性谱系。** 不需要另写一套流程。

### 8.1 一次性谱系的约定

| 项 | 取值 |
| :--- | :--- |
| `MODE` | `regression`（触发阶段 6.5 的 capsule 检查） |
| `BASE_VERSION` | `smokecheck`（或 `regression_<日期>`） |
| `TARGET_DIR` | 模板仓库**之外**的新目录 |
| `PUSH` | `no` |
| `SMOKE_N` | `500`（足以触发全部分支，又足够快） |

跑完即删除 `TARGET_DIR`，不要把它当真实谱系保留。

### 8.2 各阶段已内置的检查

设 `MODE=regression` 后，提示词会自动执行下列检查，**不需要你额外操作**：

| # | 检查 | 内置位置 | 期望 |
| :---: | :--- | :--- | :--- |
| R1 | 生成配置是**完整快照** | 阶段 4 / G2 | > 100 行，且含 `manual_features` 段 |
| R2 | 日历开关显式关闭 | 阶段 4 / G2 | 三项均为 `false` |
| R3 | **capsule 不含硬编码** | 阶段 6.5 / G5 | 无 `anaconda` / `factor311` / `C:/ProgramData`；命令以 `python ` 开头 |

> R3 为什么必要：`smoke` 只走 step1–6，不经过 capsule / prune 工具。
> 这两个工具曾经写死过本机解释器路径，导致 capsule 在别的机器上无法回放。
> R3 同时验证 metadata 查找覆盖 `<base_version>/<version_subdir>/<run_id>` 三段式路径。

### 8.3 一次通过的产物长这样

结构预期，不要用指标判断有效性（smoke 指标本身没有意义）：

| 位置 | 应存在 | 说明 |
| :--- | :---: | :--- |
| `outputs/<bv>/<vs>/<run_id>/predictions_smoke.csv` | 是 | 测试集预测 |
| `.../train_predictions_smoke.csv` | 是 | 训练集预测 |
| `.../val_predictions_smoke.csv` | 是 | 验证集预测 |
| `.../metadata/pipeline_state_smoke_*.json` | 是 | step_1..step_6 均为 `completed` |
| `.../metadata/experiment_manifest.json` | 是 | 版本与来源登记 |
| `.../metadata/model_feature_schema_smoke.json` | 是 | 特征清单 |
| `.../analysis/eval_summary.json` | 是 | `calendar_gain_share` 必须为 `0` |

### 8.4 回归失败的归类

| 失败项 | 性质 | 处理 |
| :--- | :--- | :--- |
| 阶段 0–3 失败 | 环境或数据问题 | 修环境，不算模板回归 |
| G2（R1/R2）失败 | **模板缺陷** | 修复模板后重跑 |
| G3（六步）失败 | **模板缺陷** | 修复模板后重跑 |
| G4（防泄漏）失败 | **模板缺陷** | 修复模板后重跑 |
| R3 失败 | **模板缺陷** | 修复工具后重跑 |

> 只有后四类才需要在模板仓库提交修复；前三类属于使用环境。

---

## 9. 更新记录

| 版本 | 变更 |
| :--- | :--- |
| v1.0.3 | 新建本文；提取自实际 smoke 验证中暴露的两个缺陷（日历开关、state-file 语义） |
| v1.0.4 | 修正阶段 1：按 tag 克隆会得到 detached HEAD，补 `git checkout -B main`；新增 `{{TEMPLATE_COMMIT}}` 占位符，因浅克隆下 tag ref 不可用 |
| v1.0.5 | 修正 `.gitignore`：`data/raw/*` 原先未被忽略，`git add -A` 会提交数百 MB 原始数据；阶段 7 改为显式提交 `outputs/experiment_registry.json` |
| v1.0.6 | 新增 §8 用作模板回归（含 capsule 检查 R3，覆盖 smoke 盲区）；`_closeout_common` 去掉硬编码解释器与 PowerShell 围栏 |
| v1.0.7 | 修正 `metadata_dir()`：原先只认 `outputs/<base_version>/<run_id>/` 两段式，未覆盖 `version_subdir` 引入的 `outputs/<base_version>/<version_subdir>/<run_id>/` 三段式，导致 R3 在首次回归时直接失败 |
| v1.0.8 | 阶段 0 依赖清单 6 项 → 11 项（补 scipy / sklearn / matplotlib / psutil / markdown）；新增 §4.2 解释器发现顺序、§4.3 安装指引；§4.4 平台声明改为准确表述 |
| v1.0.9 | 提示词自包含：探测脚本内联进阶段 0，不再要求 agent 另查 §4.2；新增 `{{MODE}}`（first-run / regression）与**阶段 6.5 capsule 检查**，使 R3 随提示词自动执行；新增 `{{ENV_SETUP}}`（auto / forbidden），auto 时允许建 venv 装**钉版依赖集**；修正交付格式中的"六项依赖"→ 11 项；自检门扩为 G1–G5 |
| v1.0.10 | 修正 `PUSH=yes` 的 remote 处理：直连 clone 后 `origin` 已指向模板，`remote add` 会报错且存在把谱系推到模板的风险；改为 `set-url` 并新增 **push 前安全守卫**（`origin == TEMPLATE_URL` 时禁止 push）。新增 §2.2「验证 vs 开谱系：先选对获取方式」，并在阶段 1 标注本提示词使用直连 clone 的理由 |
| v1.0.11 | 新增 §2.3「数据前提」：明确模板不含样本数据、`{{DATA_DIR}}` 不能留空、参考体积与两种降级方案；阶段 3 增加前置检查（缺文件即停止），阻塞清单同步；README 与 docs/12 §5.1 补充「数据不随模板分发」 |
| v1.0.12 | 数据契约改为**可选启动条件**：`{{DATA_DIR}}` 可留空 → 无数据模式（跳过阶段 5/6/6.5，仍完成 scaffold 与提交）；新增 `data.holding_file` / `data.datebar_file` 配置项，不再把文件名写死在代码里；新增 `tools/check_raw_data.py`，用退出码 0/2/3 区分就绪、缺失、契约不符；阶段 3/4/5、阻塞清单、交付格式与结论清单同步；`data` 段不参与版本哈希 |

> 本文与 `12_clone_and_start.md` 描述同一条路径，但读者不同：前者面向 agent（指令式），后者面向人（解释式）。两者内容若冲突，以本文的阶段划分与判据为准。
