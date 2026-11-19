# v1.1.1 报告层重构 — 工程代理执行提示词

> 来源: [experiment_design.md](./experiment_design.md)（内容权威）+ [implementation_plan.md](./implementation_plan.md)（施工顺序）
> 回归锚点: [baseline_v1.1.0.json](./baseline_v1.1.0.json)
> 目标: 在 `ashare_signal_pipeline` 内核内落地报告层重构（三份报告 + 数据卡 + 参考页 + 产物清单），**只改白名单文件，不改 step 语义，不提前做 v1.2**。
> 定位: 本文件是交给独立实施 agent 的**唯一执行规范**；冲突时按 §0 仲裁。

---

## 0. 文档优先级

1. **本文件**（执行边界、白名单纪律、禁止项、止损规则）
2. `experiment_design.md`（规格正文；本文件未覆盖处一律以它为准）
3. `implementation_plan.md`（文件级施工顺序与验收命令）
4. `baseline_v1.1.0.json`（数值锚点；与规格冲突时以基线为准并停止报告）

`AGENTS.md` 与 `docs/01_pipeline_contract.md` 是**不可被覆盖的硬约束**（`data/raw` 只读、禁止破坏性清理、`reset_index(drop=True)` 禁令、独立 state-file、共享中间目录串行）。当本文件与它们冲突时，以它们为准并立即停止报告——那说明本文件写错了。

---

## 1. 执行边界

### 1.1 工作目录与解释器

| 项 | 值 |
| :--- | :--- |
| 工作目录 | 仓库根（`ashare_signal_pipeline_kernel_v1_1`，下文所有命令都在这里执行） |
| 解释器 | `C:/ProgramData/anaconda3/envs/factor311/python.exe`（下文记 `$py`） |
| 唯一例外 | 若需对照旧 tag，允许在 `git worktree` 中操作；只有那一步可 `Push-Location` / `Pop-Location`，结束必须回到仓库根 |

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
$base = "version_design/design/refactor/v1.1.1_report/baseline_v1.1.0.json"
```

### 1.2 开工前必须确认的三件事

```powershell
git status --porcelain
git log -1 --oneline
git tag --list "v1.1.0"
```

| 检查 | 期望 | 不符时 |
| :--- | :--- | :--- |
| `git status --porcelain` | 空（或仅有 `outputs/**`、`configs/local/**` 等 gitignore 内内容） | **停止**，报告文件名与改动摘要；不得覆盖、不得 stash |
| 当前分支 | `refactor/v1.1.1` | 停止，报告实际分支 |
| `v1.1.0` tag | 存在 | 停止，报告 |

> 本仓库可能被多个会话共用。若发现他人未提交改动，**不得** `git checkout --`、`git stash`、`git reset`；应改用独立 worktree 并在报告中说明。

### 1.3 允许新增文件的范围

只允许新增：

1. `implementation_plan.md` 各里程碑「具体动作」表首列列出的路径；
2. `configs/local/report_check_*.yaml` 临时验证配置（`configs/local/**` 已 gitignore）；
3. 运行期产物（`outputs/**`、`data/reference/**`），由命令生成，**不得手工编辑**；
4. 阻塞时的 `version_design/design/refactor/v1.1.1_report/BLOCKED.md`（只此一个）。

不得新增：设计笔记、草稿、汇总 md、一次性 Python 脚本。临时比对逻辑写进 `$py -c` 或 `tools/verify_reports.py`。

### 1.4 数据与产物保护

| 目录 | 规则 |
| :--- | :--- |
| `data/raw/**` | **只读**，任何情况下不得修改、移动、删除 |
| `data/prepared/**`、`data/labels/**` | **只读**。本迭代复用既有样本集，**不得重建**（重建会改变 `samples_set_hash`，锚点失效） |
| `data/processed/**`、`data/slices/**`、`data/features/**`、`models/**`、`outputs/**` | 不得删除目录做"重置"；用新 `state-file` / 新 `experiment_version` 隔离 |
| `outputs/kernel_v11/check/kernel_v11_20261102_baseline_*/**` | **只读**：v1.1 里程碑证据 |
| `verify_report_M*.json`（仓库根，若存在） | **只读**：历史里程碑报告，禁止覆盖 |
| `version_design/demo/**` | 只读参考 |

---

## 2. 自动执行流程

### 2.1 四个阶段

```text
阶段 A 审计    git 状态 + 解释器 + 里程碑目标文件现状 → 输出审计报告（§2.4）
阶段 B 编码    只改该里程碑白名单内的文件
阶段 C 验证    按 implementation_plan 的验收命令逐条执行，最后跑 verify_reports
阶段 D 报告    提交 → 打 tag → 输出结项报告（§9）
```

### 2.2 默认不等待用户确认

审计完成后：**若无 §2.3 阻塞条件，直接开始编码，不要问「是否继续」**；每个里程碑验收通过后直接进入下一个，不要逐步请示。

只有两种情况停下等人：

1. 命中 §2.3 阻塞条件；
2. 同一里程碑连续失败三次（写 `BLOCKED.md`）。

### 2.3 阻塞条件（命中即停）

| # | 条件 | 判据 | 动作 |
| :---: | :--- | :--- | :--- |
| B1 | 解释器不可用 | `$py -c "import sys;print(sys.version)"` 非 0 | 停止，报告探测结果 |
| B2 | 目标结构与描述不符 | 白名单文件里找不到 `implementation_plan` 引用的函数/键 | 停止，报告「期望位置 / 实际内容」 |
| B3 | 白名单文件有未提交改动 | `git status --porcelain` 命中白名单路径 | 停止，不覆盖 |
| B4 | 既有错误无法切开 | 白名单文件语法/导入已坏，或 `pytest tests` **改动前**即失败 | 停止，报告失败用例 |
| B5 | Gate 连续失败三次 | 同一里程碑第 3 次失败 | 停止，写 `BLOCKED.md` |
| B6 | 需要改设计才能继续 | 规格缺失/自相矛盾/无法实现 | 停止，写 `BLOCKED.md`，**不得**自行放宽 |
| B7 | 发现必须改 step 语义 | 例如"要修 `holding_clean` 覆盖" | 停止。该问题属 PR-001 / v1.2 |
| B8 | 样本集或标签需重建 | `data/prepared/**` 缺失或 hash 不符 | 停止，报告中说明需人工先跑 `build_samples/build_labels` |

**B8 的自检命令**（审计阶段执行一次，结果贴进审计报告）：

```powershell
& $py -c "import json,hashlib,pathlib,numpy as np,pandas as pd;sid='bollinger_breakout_080c9262_294b0504';m=json.load(open(f'data/prepared/{sid}/manifest.json',encoding='utf-8'));s=pd.read_parquet(f'data/prepared/{sid}/samples.parquet');h=hashlib.sha256(np.sort(s['sample_id'].to_numpy().astype('<i8')).tobytes()).hexdigest()[:16];print('hash_ok',h==m['samples_set_hash'],'rows',len(s),m['rows']);print('labels',sorted(p.name for p in pathlib.Path(f'data/labels/{sid}').glob('*.parquet')))"
# 期望 hash_ok True、rows 相等、labels 含 6 个 parquet
```

`BLOCKED.md` 只写四项：现象（命令与回显）、涉及章节（§ 编号）、为什么无法在本计划内解决、建议的两种修法。

### 2.4 审计报告（阶段 A 必须输出）

1. 当前分支 + `git status --porcelain` 摘要；
2. 解释器与关键包版本（`pandas` / `scipy` / `markdown` / `lightgbm`）；
3. 本里程碑白名单文件现状：存在/不存在、关键函数是否在原位；
4. 明确列出「本里程碑不碰的文件」；
5. 是否命中阻塞条件（命中则停止）。

---

## 3. 逐里程碑循环

### 3.1 单步循环（M0 → M6，不可跳）

```text
1. 读 implementation_plan 的第 k 节 + experiment_design 的对应阅读范围（见其「阅读方式」表）
2. 记录基线：git status --porcelain + git diff --stat
3. 只改白名单内的文件
4. 对照基线：git diff --stat 应只多出白名单路径
5. 按 plan 的验收命令逐条执行（小 → 大）
6. 跑 tools/verify_reports.py --milestone M<k>，确认 exit 0 且 items 数 == baseline.expected_item_counts[M<k>]
7. exit 0 → git add <白名单路径，逐条列出>（不用 -A / -a）
8. git commit -m "<type>: <subject>"
9. git tag m<k>-ok → 进入 k+1；失败 → §4 止损
```

**顺序固定：提交 → 打 tag**，不得先 tag 后提交。

### 3.2 每个里程碑只读对应章节

按 `experiment_design.md` 开头的「阅读方式」表。**不要预读下一个里程碑的内容**——那会导致一次性大改，破坏逐里程碑可回滚性。

### 3.3 报告产物的幂等要求

同一 `report_summary.json` 重复渲染，除 `generated_at`（及头部"生成时间"行）外必须逐字节一致。每次改动渲染逻辑后都要跑一次幂等检查（plan 的 `A-6` 命令）。

---

## 4. 白名单纪律与止损

### 4.1 白名单纪律

1. 每里程碑结束前，`git diff --stat` 相对基线**只能**包含该里程碑白名单路径；
2. 出现白名单外改动 → 回退该改动（`git checkout -- <path>` 仅对自己本次的改动），并在报告中说明；
3. **不得**修改 `pipeline/step*.py`、`data_prep/**`、`tools/verify_v11.py`（已归档）；
4. `pipeline/ensemble/bagging.py` **只允许** M5 改 §5.3 列的 6 处，不得顺带重构。

### 4.2 止损规则

| 情形 | 动作 |
| :--- | :--- |
| 单次验收命令失败 | 读错误 → 修 → 重跑该命令；不跳步 |
| 同一里程碑连续 3 次 Gate 失败 | 停止，写 `BLOCKED.md`，交回人工 |
| 发现规格与实现冲突 | 停止，报告冲突点（§ 编号 + 代码行），不自行选择 |
| 发现锚点数值对不上 | **立即停止**。这意味着重构引入了计算副作用 |
| 发现需要改历史报告/证据 | 停止。历史证据只读 |

### 4.3 明确禁止的「修复」方式

| 禁止 | 原因 |
| :--- | :--- |
| 修改 `baseline_v1.1.0.json` 的数值使其"通过" | 锚点是判定基准，改它等于取消验收 |
| 删除/重命名 `verify_report_M*.json` | 里程碑证据 |
| 用 `reset_index(drop=True)` 修改 `sample_id` | AGENTS 铁律 5 |
| 删除 `outputs/`、`data/` 目录"重置环境" | AGENTS 铁律 2 |
| 在渲染器里直接 `pd.read_csv` 绕开事实表 | 违反不变量 1（A-7） |
| 为通过断言而放宽断言范围 | 等同于取消验收 |
| 在报告里硬编码实验数字（非取自事实表） | 违反不变量 4（A-20） |
| 重建 `data/prepared` / `data/labels` | 锚点与 hash 失效 |

---

## 5. 必做

1. 每个里程碑开工前输出审计报告（§2.4）；
2. 每里程碑结束提交 + 打 tag（§3.1 第 7–9 步）；
3. Gate 报告一律写到 `$env:TEMP`；只有 M6 的最终验收可写仓库根 `verify_report_reports.json`；
4. 每次改动渲染逻辑后跑幂等检查；
5. 报告中出现的每个数字都能在 `report_summary.json` 找到出处；
6. 结项报告按 §9 固定栏目逐项输出。

---

## 6. 禁做

| # | 禁做 | 理由 |
| :---: | :--- | :--- |
| 1 | 修改 `data/raw/**` | 只读数据 |
| 2 | 修改 `pipeline/step1_clean.py` … `step6_model.py` | step 语义零变更 |
| 3 | 修改 `data_prep/**` | 采样/标签语义零变更 |
| 4 | 重建 `data/prepared` / `data/labels` | 锚点失效 |
| 5 | 删除 `outputs/`、`data/` 目录 | 破坏性清理 |
| 6 | 用 `reset_index(drop=True)` 改 `sample_id` | 铁律 |
| 7 | 覆盖 `verify_report_M*.json` | 历史证据 |
| 8 | 提交 `configs/local/**` 或 `configs/report/executive_override.yaml` | 含本机路径 |
| 9 | 在报告中内联"与上次运行对比" | 破坏幂等（A-22） |
| 10 | 给领导版加逐组明细表 | A-24 明确禁止 |
| 11 | 修改 `md_report_to_html.py` | 缺陷已由 `60ce3b9` 修复，本迭代只做回归 |
| 12 | 顺手修 `holding_clean` 覆盖问题 | 属 PR-001 / v1.2 |

---

## 7. 禁止发明清单（逐条保留，不得压缩改写含义）

以下内容**必须**来自规格或事实表；规格未定义的，停止并报告，不要自己补：

1. **不得发明新的 `report_summary.json` 顶层键**（只允许 design §3.1/§3.2 列出的）。
2. **不得发明新的报告章节**（E/X/K 编号只在 design §4 内）。
3. **不得发明新的断言编号**（A-1..A-28；里程碑自定义项写在 `verify_reports.py` 内并标 `M<k>-*`）。
4. **不得在代码里写死实验数字**（`324`、`8629`、`73` 等必须来自事实表或 baseline JSON）。
5. **不得在代码里写死标签名 / 样本集名**（必须从 `resolved_params` / `input_identity` 读取）。
6. **不得发明标签定义文案**（一句话映射只在 `report/datacard.py::LABEL_ONE_LINERS`；缺失输出「（未登记定义）」）。
7. **不得发明采样规则描述**（构造逻辑 5 行必须来自 design §4.2.1 的固定内容）。
8. **不得发明 X9 指标**（只允许 KS / 重叠 / 集中度 / 月度收益四项）。
9. **不得发明阈值**（漂移阈值只有 1.0pp / 5.0pp，写进 `drift_hint.thresholds` 常量）。
10. **不得发明图形类型**（K3 只允许 4 张折线 + 1 张柱状；不得加饼图/热力图）。
11. **不得发明降级行为**（缺字段只按 design §3.5 矩阵处理）。
12. **不得改 `experiment_design.md` 的章节编号**（跨文档引用依赖它）。

---

## 8. 验证顺序

每个里程碑按以下顺序执行，小 → 大：

```text
1. 语法/导入             $py -m py_compile <改动的 .py>
2. 单模块冒烟            $py -c "import report.<mod>"
3. 单次渲染              $py tools/build_reports.py --types <本里程碑类型>
4. 结构检查              plan 里该节的 --py -c 检查命令
5. 幂等检查              A-6
6. 全量断言              $py tools/verify_reports.py ... --report $env:TEMP\verify_report_M<k>.json
7. 白名单检查            git diff --stat 相对基线
```

第 6 步失败时**不要**跳到下一里程碑。

---

## 9. 结项报告（固定栏目，逐项输出）

```markdown
## 1. 里程碑执行矩阵
| 里程碑 | 目标 | 是否完成 | 提交 | tag | Gate exit | items |

## 2. 文件清单
| 路径 | 动作（新增/修改） | 行数变化 |

## 3. 断言结果
| 编号 | 断言 | 期望 | 实际 | 通过 |

## 4. 数值锚点比对
| 指标 | baseline | 实测 | 一致 |

## 5. 幂等与 golden
| 检查 | 结果 |

## 6. 白名单越界检查
| 里程碑 | git diff --stat | 是否越界 |

## 7. 未完成项与原因
（逐条；无则写"无"）

## 8. 结论
PASS / 需返修（附理由）
```

---

## 10. 完成定义

全部满足才算完成：

1. M0–M6 各自 Gate exit 0，`items` 数与 `baseline.expected_item_counts` 一致；
2. 双路径 demo 各产出 3 报告 + 1 数据卡 + 1 参考页，且 `verify_reports` 均 exit 0；
3. `baseline_v1.1.0.json` 中 `experiments.*` 数值零变化；
4. `git status --porcelain` 为空；
5. `pipeline/**` 仅 `ensemble/bagging.py` 6 处改动；`data_prep/**` 零改动；
6. 历史 `verify_report_M*.json` 未被覆盖；
7. 已打 annotated tag `v1.1.1`；
8. 结项报告 §9 全部栏目已输出。
