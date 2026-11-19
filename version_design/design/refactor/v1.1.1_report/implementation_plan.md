# v1.1.1 报告层重构 — 实施计划

> 状态: 待执行（M0 开工前置）
> 创建日期: 2026-11-19
> 规范（唯一内容权威）: [experiment_design.md](./experiment_design.md)
> 执行边界（交给实施 agent）: [code_agent_prompt.md](./code_agent_prompt.md)
> 回归锚点: [baseline_v1.1.0.json](./baseline_v1.1.0.json)
> 目标 tag: `v1.1.1`｜分支: `refactor/v1.1.1`｜基线 tag: `v1.1.0`（`fe9a3d6`）

本文件只回答两件事：**按什么顺序改哪些文件**、**用什么命令判定通过**。规格正文一律以 `experiment_design.md` 的 § 编号引用，不在此重复；冲突时以规范文档为准（仲裁顺序见 `code_agent_prompt.md` §0）。

---

## 0. 总览

```text
M0  事实表补字段 + 断言工具就绪            只增字段；渲染输出不变
M1  渲染层抽取（golden 逐字节一致）         纯搬运；报告内容与改造前完全相同
M2  数据卡 + 参考页（T2/T3 载体）           纯新增产物
M3  工程报告（E0–E11）                     新增 reports/engineering.md
M4  实验报告（X0–X10）                     新增 reports/experiment.md
M5  领导版 + 清单 + 接线                    新增 reports/executive.html + manifest + bagging 接线
M6  文档与 demo 收尾                        文档 + 双路径 demo + compare_runs
```

顺序不可交换。每个里程碑单独合入后，仓库必须可跑、旧行为必须可证。

### 0.1 依赖关系表

| 里程碑 | 依赖 | 必须先存在的产物 | 提前做的后果 |
| :---: | :--- | :--- | :--- |
| M0 | 无（可独立开工） | `data/prepared/**`、`data/labels/**`、demo 的 `outputs/<ev>` 全套 | 无事实表可补，A-1 无对象 |
| M1 | M0 | 带 `schema_version` 的 `report_summary.json` | golden 比对无基线 |
| M2 | M0 | `input_identity`、`fingerprints`、prepared manifest | 数据卡缺字段 |
| M3 | M0, M2 | `workload`/`param_effective`/`determinism`/`artifact_sizes`、`report/common.py` | E8/E9/E10 无数可填 |
| M4 | M0, M2, M3 | `target_stats_by_split`/`drift_hint`/X9 四字段、`common.py` | X2/X9 无数据 |
| M5 | M1–M4 | 三份报告 + `report/spec.py` | 清单指向不存在的报告；bagging 回退路径未被验证 |
| M6 | M0–M5 全部 exit 0 | 各里程碑 Gate 报告 | 发布 Gate 无法判定 |

### 0.2 判定权

每个里程碑的通过与否，**只**由以下命令的 exit code 决定：

```powershell
& $py tools/verify_reports.py --milestone M<k> --analysis-dir "outputs/<ev>/analysis" `
    --baseline "version_design/design/refactor/v1.1.1_report/baseline_v1.1.0.json" `
    --report "$env:TEMP\verify_report_M<k>.json"
```

人工书面结论、命令回显、截图只能作为补充证据，**不得**替代 exit code。

> 本迭代**不复用** `tools/verify_v11.py`（已在 v1.1 M5 归档）。历史里程碑报告 `verify_report_M*.json` 只读，不得覆盖。

### 0.3 本计划不覆盖

| 项 | 归属 |
| :--- | :--- |
| `holding_clean.parquet` 覆盖语义修复 | `docs/16_potential_risks.md` PR-001，v1.2 |
| alias 删除与 bagging 回退分支移除 | v1.2 |
| X9 配图（漂移条形图、月度柱状图） | v1.2 |
| `md_report_to_html.py --no-mermaid` 语义修正 | 独立排期 |
| 任何 step1–step6、`data_prep/**` 改动 | **禁止**（见 prompt §6） |

### 0.4 独立实现可行性检查（差距闭合表）

> 本节回答"一个未参与设计的工程 agent 能否只靠本套文档完成实施"。
> 结论：**可以**，条件是 §0.4.2 的 12 项闭合项在 M0 前全部成立。§0.4.1 列出原 `design.md`（单文档版）的缺口，§0.4.2 列出闭合位置。

#### 0.4.1 单文档版的 12 项实施缺口

| # | 缺口 | 后果（若不闭合） |
| :---: | :--- | :--- |
| G1 | 无文件白名单 | agent 会改到 `pipeline/**`，或新增散落脚本 |
| G2 | 无逐文件改动清单 | agent 不知道"在哪个函数里加"，只能猜 |
| G3 | 无函数签名契约 | 模块之间无法对接（`render_all` 入参/返回不一致） |
| G4 | 无 `report_summary.json` 字段路径级定义 | 字段名相同但层级不同（`buffer.train_val_days` vs `step5.buffer_x`） |
| G5 | 无 X9 四项计算定义 | KS/重叠/集中度/月度各写一套，数字不可复现 |
| G6 | 无 CLI 参数与 exit code 契约 | 验收命令无法固定；参数错误被当成断言失败 |
| G7 | 无降级矩阵 | 缺字段时 agent 自行发明占位值或直接崩 |
| G8 | 无 golden 比对方法（M1） | "逐字节一致"无法判定 |
| G9 | 无数值锚点文件 | "数值不变"只能靠人眼 |
| G10 | 无断言 → 代码位置映射 | A-1..A-29 写了但不知在哪实现 |
| G11 | 无回滚点与 tag 命名 | 失败后无法回到已知良好状态 |
| G12 | 无"禁止发明"清单 | agent 会自行补充未定义的字段/图表/结论 |

#### 0.4.2 闭合位置

| # | 闭合项 | 位置 | 验收方式 |
| :---: | :--- | :--- | :--- |
| G1 | 每里程碑「文件白名单」表 | 本文件 §2–§8 各「2.2 具体动作」表首列 | 每里程碑 `git diff --stat` 相对基线只多白名单路径（prompt §3.1 第 4 步） |
| G2 | 逐文件「动作 + 改什么（函数/键）+ 设计依据」三列 | 本文件 §2.2、§3.2 … §8.2 | 审计阶段逐条核对 |
| G3 | 12 个模块的公开符号与签名 | design §2.2 表 | 断言 A-7（渲染器不读文件）+ import 冒烟 |
| G4 | 38 个必填键 + 45 行（含子键）的 JSON 路径与口径 | design §3.1、§3.2 | A-1 + A-11 + A-12 |
| G5 | X9 四项计算定义（含 `idxs` 解码规则） | design §3.4 | A-27 + 与 `baseline.report_anchors.robustness` 比对 |
| G6 | 3 个 CLI 参数表 + exit code 3 档 | design §2.2 表、§8.3 | 参数错误用例（exit 2）纳入 M0 验收 |
| G7 | 降级矩阵（6 行） | design §3.5 | 缺字段用例：构造 `report_summary` 去掉 `smoke_vs_full` → 渲染不崩 |
| G8 | golden 比对：改造前后 `render_markdown_report` 输出 sha256 相等 | 本文件 §3.3 M1-3 | `Get-FileHash` 相等 |
| G9 | `baseline_v1.1.0.json`（含 `report_anchors`） | 同目录 | A-4 逐值比对 |
| G10 | 断言 → 实现位置映射表 | 本文件 §9 | A-9 自身可被 `verify_reports --only A-9` 验证 |
| G11 | 每里程碑回滚点 + tag `m<k>-ok` | 本文件各节「回滚点」 | 打 tag 后 `git checkout m<k>-ok` 可回到该状态 |
| G12 | 「禁止发明」清单（13 条） | code_agent_prompt §7 | 结项报告逐条声明 |

#### 0.4.3 仍需人工确认的 3 项

以下不是文档缺口，而是**评审决策**，开工前需人工拍板（已给默认值，可直接接受）：

| # | 事项 | 默认值 |
| :---: | :--- | :--- |
| D1 | M1 的 golden 基线取哪一次运行 | 取 M0 完成后、M1 改动前的 `experiment_analysis_report.md`（demo_new，full） |
| D2 | `configs/report/` 目录是否随内核分发 | 是；`executive_default.yaml` 入库，`executive_override.yaml` 入 `.gitignore` |
| D3 | `tools/compare_runs.py` 是否本迭代交付 | 是（M6）；不进 `manifest.json` |

---

## 1. 通用约定

### 1.1 解释器与变量

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
$base = "version_design/design/refactor/v1.1.1_report/baseline_v1.1.0.json"
$evOld = "kernel_v11/check/kernel_v11_20261102_demo_old_75443d88"
$evNew = "kernel_v11/check/kernel_v11_20261102_demo_new_6e499192"
```

所有命令在仓库根执行。

### 1.2 每次渲染的统一入口

```powershell
# 事实表（重算，不渲染）
& $py analyze_experiment_results.py --manifest "outputs/$evNew/metadata/experiment_manifest.json" --emit-summary-only

# 渲染（读事实表）
& $py tools/build_reports.py --manifest "outputs/$evNew/metadata/experiment_manifest.json" --types engineering,experiment,executive
```

### 1.3 提交纪律

1. 每里程碑结束：`git add <白名单路径，逐条列出>`（**不用** `-A` / `-a`）；
2. `git commit -m "<type>: <subject>"`；
3. `git tag m<k>-ok`；
4. 顺序固定：**提交 → 打 tag**（不得先 tag 后提交）。

### 1.4 临时验证配置

`configs/local/**` 已被 `.gitignore` 排除。M3–M5 需要独立 `experiment_version` 做多轮渲染验证时，用 `Copy-Item` 生成 `configs/local/report_check_*.yaml`，**只改 `experiment.label`**，其余字段逐字不动。

---

## 2. M0：事实表补字段 + 断言工具就绪

### 2.1 目标

让 `report_summary.json` 成为"三份报告所需信息的完备集合"，并提供唯一 Gate 脚本。**本里程碑不改变任何渲染输出**。

### 2.2 具体动作

| 文件路径 | 动作 | 改什么（函数/键） | 设计依据 |
| :--- | :---: | :--- | :--- |
| `analyze_experiment_results.py` | 修改 | 新增 `_collect_label_span_stats()`（条件必填键 `label_span_stats`，见 design §3.2.1）；新增 `_collect_input_identity()`、`_collect_fingerprints()`、`_collect_buffer()`、`_collect_feature_funnel()`、`_collect_clip_and_target_stats()`、`_collect_artifact_sizes()`、`_collect_runs()`、`_collect_smoke_vs_full()`、`_collect_workload()`、`_collect_param_effective()`、`_collect_determinism()`、`_collect_robustness()`；`render_summary_json()` 输出上述键；新增 CLI `--emit-summary-only` | §3.2 |
| `analyze_experiment_results.py` | 修改 | `SCHEMA_VERSION = "report-summary/1"` 常量 + 写入 `schema_version` | §3.1 |
| `tools/verify_reports.py` | 新增 | `main()` / `run_checks(analysis_dir, baseline, milestone, only)` / `add_item(id, name, expected, actual, passed, detail)` / `write_report()`；实现 A-1..A-4 | §8.1–§8.3 |
| `configs/report/executive_default.yaml` | 新增 | **按 design 附录 A 全文入库**（M5 直接消费，不再改动） | 附录 A |
| `.gitignore` | 修改 | 增 `configs/report/executive_override.yaml` | §4.3.2 |

补充约定：

1. `_collect_*` 一律**只读**既有产物，不跑 pipeline、不写 `data/`。
2. `_collect_runs()` 解析 `metadata/pipeline_state_*.json` 文件名后缀，**不得**按 mtime 猜测 mode。
3. `_collect_workload()` 的"每单位计算量"必须从 `resolved_params` 推导（如 `len(indicators.ma_windows)+...`），**不得**在代码里写 `324` 之类字面量；`324` 只能来自 `step4.details.raw_extracted_feature_columns`。
4. `_collect_robustness()` 用 `scipy.stats.ks_2samp`；若 `scipy` 不可用则四项写 `null` 并记 `reason`，不抛异常。
5. `param_effective` 的判定必须**基于实际消费路径**（如 `input.mode == "samples"` 时 `manual_features.include_signal_date_selection_score` 判 ✘），不得硬编码实验名。

### 2.3 验收命令

```powershell
# M0-1 重算事实表（两个 demo 各一次）
& $py analyze_experiment_results.py --manifest "outputs/$evOld/metadata/experiment_manifest.json" --emit-summary-only
& $py analyze_experiment_results.py --manifest "outputs/$evNew/metadata/experiment_manifest.json" --emit-summary-only
# 期望：exit 0；report_summary.json 含 schema_version 且 38 个必填键齐全

# M0-2 字段抽查（demo_new）
& $py -c "import json;d=json.load(open(r'outputs/$evNew/analysis/report_summary.json',encoding='utf-8'));print({k:d[k] for k in ['schema_version','run_modes']});print(d['input_identity']);print(d['buffer']);print(d['feature_funnel'])"
# 期望：input_mode=samples, coupling_mode=decoupled, dropped_no_target=8629,
#       buffer=73/63/enforced, feature_funnel.raw_extracted=324 / zero_variance_dropped=20 / selected=50 / model_input=51

# M0-3 渲染输出不变（golden）
Copy-Item "outputs/$evNew/analysis/experiment_analysis_report.md" "$env:TEMP\pre_m0.md" -Force
& $py analyze_experiment_results.py --manifest "outputs/$evNew/metadata/experiment_manifest.json"
& $py -c "import hashlib,sys;a=open(sys.argv[1],encoding='utf-8').read().split('生成时间')[0];b=open(sys.argv[2],encoding='utf-8').read().split('生成时间')[0];print('equal:', a==b)" "$env:TEMP\pre_m0.md" "outputs/$evNew/analysis/experiment_analysis_report.md"
# 期望 equal: True（M0 不改变报告内容）

# M0-4 参数错误 exit 2
& $py tools/verify_reports.py --milestone M0 --analysis-dir "nonexistent" --baseline $base --report "$env:TEMP\x.json"
# 期望 exit 2

# M0-5 里程碑 Gate
& $py tools/verify_reports.py --milestone M0 --analysis-dir "outputs/$evNew/analysis" --baseline $base --report "$env:TEMP\verify_report_M0.json"
# 期望 exit 0，items == 13
```

### 2.4 验收断言

| # | 断言 |
| :---: | :--- |
| M0-1 | `schema_version == "report-summary/1"` |
| M0-2 | 38 个必填键齐全；缺一即 fail |
| M0-3 | `input_identity` 六值与 step1 state 逐值相等（demo_new） |
| M0-4 | `buffer` 三值与 step5 state 相等 |
| M0-5 | `feature_funnel` 六值自洽（`candidates == raw - zero_var`；`consistency_ok == true`） |
| M0-6 | `run_modes` 含 `smoke` 与 `full` |
| M0-7 | `param_effective` ≥ 10 项，含 `manual_features.include_signal_date_selection_score` 判 ✘ |
| M0-8 | `label_overlap.share_gap_le_horizon` 在 [0,1]，demo_new 约 0.43 |
| M0-9 | `feature_drift.count_over_0_1` == 15（demo_new） |
| M0-10 | 渲染输出与 M0 前逐字节相同（除"生成时间"行） |
| M0-11 | 参数错误 exit 2 |
| M0-12 | `label_span_stats` 存在，`n_gt_60d == 3634`、`max_days == 2478`、`policy == "uncapped"`（demo_new） |
| M0-13 | `run_env` 六字段齐全（`git_commit` 非空、五个依赖版本非 null） |
| M0-14 | `verify_report_M0.json` 的 `items` 长度 == 13（本行不计入 items） |

### 2.5 回滚点

`git tag m0-ok` → 回退 `git checkout m0-ok~1`（事实表可由旧代码重生成，无数据损失）。

---

## 3. M1：渲染层抽取（golden 逐字节一致）

### 3.1 目标

建立 `report/` 包骨架，把现有渲染逻辑**原样搬运**，并用 golden 证明输出未变。这是后续所有里程碑的地基。

### 3.2 具体动作

| 文件路径 | 动作 | 改什么 | 设计依据 |
| :--- | :---: | :--- | :--- |
| `report/__init__.py` | 新增 | `render_all()` 编排（M1 阶段只调用 experiment） | §2.2 |
| `report/schema.py` | 新增 | `SCHEMA_VERSION`、`REQUIRED_KEYS`、`validate()`、`DEGRADE_MATRIX` | §3.1、§3.5 |
| `report/load.py` | 新增 | `load_summary()`、`get()`、`fmt_*` 系列 | §2.2 |
| `report/common.py` | 新增 | `build_toc`、`render_table`、`details_block`、`figure`、`section`、`explain_simple` | §6 |
| `report/_legacy.py` | 新增 | 完整复制改造前的 `render_markdown_report()` 与 `render_leadership_html()` 函数体（**仅供 golden 比对与回滚，M6 删除**） | §7 回滚点 |
| `report/experiment.py` | 新增 | `render_experiment(summary) -> str`，M1 阶段内容 == `_legacy.render_markdown_report` | §4.2 |
| `analyze_experiment_results.py` | 修改 | `render_markdown_report` 改为 `report.experiment.render_experiment` 的薄包装；删除其内部实现（保留 git 历史） | §2.2、§2.3 |

补充约定：

1. M1 **不改任何文案、表格、章节顺序**；`report/experiment.py` 必须逐字符等价于 `_legacy`。
2. `embed_images` 相关逻辑留在 `md_report_to_html.py`，不在 `report/` 内重复实现。
3. 图片路径在 M1 仍写裸文件名（保持与旧输出一致）；**M4 才改为 `assets/...`**。

### 3.3 验收命令

```powershell
# M1-1 生成 golden（M1 改动前先做一次，存 $env:TEMP）
& $py analyze_experiment_results.py --manifest "outputs/$evNew/metadata/experiment_manifest.json"
Copy-Item "outputs/$evNew/analysis/experiment_analysis_report.md" "$env:TEMP\golden_experiment.md" -Force
Copy-Item "outputs/$evNew/analysis/experiment_summary_for_leadership.html" "$env:TEMP\golden_executive.html" -Force

# M1-2 改动后重新渲染
& $py analyze_experiment_results.py --manifest "outputs/$evNew/metadata/experiment_manifest.json"

# M1-3 逐字节比对（剔除时间行）
& $py -c "import sys;f=lambda p:[l for l in open(p,encoding='utf-8') if not l.startswith('生成时间') and 'generated_at' not in l];print('md equal:', f(sys.argv[1])==f(sys.argv[2]))" "$env:TEMP\golden_experiment.md" "outputs/$evNew/analysis/experiment_analysis_report.md"
# 期望 md equal: True

# M1-4 幂等
& $py tools/build_reports.py --manifest "outputs/$evNew/metadata/experiment_manifest.json" --types experiment
& $py -c "import hashlib;p='outputs/$evNew/analysis/reports/experiment.md';print(hashlib.sha256(open(p,'rb').read()).hexdigest()[:12])"
# 连跑两次，hash 相同

# M1-5 断言 A-7：渲染器不读文件
& $py -c "import pathlib,re;bad=[(p.name,i+1,l) for p in pathlib.Path('report').glob('*.py') if p.name not in ('load.py','schema.py') for i,l in enumerate(p.read_text(encoding='utf-8').splitlines()) if re.search(r'read_csv|read_parquet|json\.load\(',l)];print(bad);assert not bad"

# M1-6 Gate
& $py tools/verify_reports.py --milestone M1 --analysis-dir "outputs/$evNew/analysis" --baseline $base --report "$env:TEMP\verify_report_M1.json"
# 期望 exit 0，items == 6
```

### 3.4 验收断言

| # | 断言 |
| :---: | :--- |
| M1-1 | `report/` 包可 import，无循环依赖 |
| M1-2 | golden md 逐字节相同（除时间行） |
| M1-3 | 幂等（两次渲染 sha256 相同） |
| M1-4 | A-7 通过（`report/` 内除 `load.py`/`schema.py` 外无文件读取） |
| M1-5 | `report/_legacy.py` 存在且可调用（回滚保障） |
| M1-6 | Gate items == 6 |

### 3.5 回滚点

`git tag m1-ok`。回退：`git checkout m1-ok~1`，并把 `analyze_experiment_results.py` 的包装改回 `_legacy` 调用。

---

## 4. M2：数据卡 + 参考页（T2/T3）

### 4.1 目标

把"这批数据是什么""系统怎么算"从报告里移出，落到独立产物，供报告引用。

### 4.2 具体动作

| 文件路径 | 动作 | 改什么 | 设计依据 |
| :--- | :---: | :--- | :--- |
| `report/datacard.py` | 新增 | `render_datacard(summary) -> str`，输出 D0–D5 | §4.5 |
| `report/reference.py` | 新增 | `render_reference(summary) -> str`，输出 R1–R6 | §4.6 |
| `report/build.py` | 新增 | `write_all()`、`resolve_external_paths()`；写 `data/reference/**` | §5.2 |
| `tools/build_reports.py` | 新增 | `--types` 支持 `datacard,reference`；`--dry-run` | §2.2 |
| `data/reference/<sid>/datacard.md` | 运行期产物 | 由命令生成，**不得手工编辑** | §4.5 |
| `data/reference/_kernel/v1.1.0/reference.md` | 运行期产物 | 同上 | §4.6 |

补充约定：

1. 参考页内容来自**规格常量**（`report/reference.py` 内的 `REFERENCE_SECTIONS`），不读代码、不读配置；版本目录名取 `summary.resolved_params.experiment.base_version` 与 `git describe` 的组合，缺失时用 `v1.1.0`。
2. 数据卡 D3 按年分布从 `pipelines_state.step_1` 附近的 prepared manifest 读取；若 manifest 无该字段，从 `samples.parquet` 现算 `idxd // 10000` 的 `value_counts`（只读）。
3. A-13 要求 D4 条数 == `_index.json` 键数；标签定义一句话映射表写在 `report/datacard.py::LABEL_ONE_LINERS`，新增标签时必须补映射（缺失则输出「（未登记定义）」并在报告中标 `[WARN]`）。

### 4.3 验收命令

```powershell
# M2-1 生成（两个 demo 都做；旧路径无 samples_id 时会写 legacy 卡）
& $py tools/build_reports.py --manifest "outputs/$evNew/metadata/experiment_manifest.json" --types datacard,reference
& $py tools/build_reports.py --manifest "outputs/$evOld/metadata/experiment_manifest.json" --types datacard,reference

# M2-2 结构检查
& $py -c "import pathlib,re;p=pathlib.Path('data/reference/bollinger_breakout_080c9262_294b0504/datacard.md');t=p.read_text(encoding='utf-8');print([s for s in ['## D0','## D1','## D2','## D3','## D4','## D5'] if s in t]);print('label rows:', t.count('| target_'))"
# 期望六个 D 节齐全；label rows == 6

# M2-3 参考页
& $py -c "import pathlib;p=pathlib.Path('data/reference/_kernel/v1.1.0/reference.md');t=p.read_text(encoding='utf-8');print([s for s in ['## R1','## R2','## R3','## R4','## R5','## R6'] if s in t])"
# 期望六节齐全

# M2-4 Gate
& $py tools/verify_reports.py --milestone M2 --analysis-dir "outputs/$evNew/analysis" --baseline $base --report "$env:TEMP\verify_report_M2.json"
# 期望 exit 0，items == 8
```

### 4.4 验收断言

| # | 断言 |
| :---: | :--- |
| M2-1 | 数据卡 D0–D5 六节齐全，`samples_id` 与 manifest 一致 |
| M2-2 | D4 标签行数 == `_index.json` 键数（6） |
| M2-3 | D3 按年分布合计 == 样本行数（393,881） |
| M2-4 | 参考页 R1–R6 六节齐全 |
| M2-5 | 参考页不含任何具体实验数字（正则扫 `\d{2,}` 排除版本号/参数值白名单） |
| M2-6 | `datacard_path` / `reference_path` 已写入事实表且文件存在 |
| M2-7 | 旧路径（`holding_file`）的数据卡写「不适用」而非报错 |
| M2-8 | Gate items == 8 |

### 4.5 回滚点

`git tag m2-ok`。回退删除 `report/datacard.py`、`report/reference.py` 与 `data/reference/**` 即可（data 目录不入库）。

---

## 5. M3：工程报告（E0–E11）

### 5.1 目标

产出 `reports/engineering.md`，回答"这次跑得怎么样、慢在哪、产物在哪"。

### 5.2 具体动作

| 文件路径 | 动作 | 改什么 | 设计依据 |
| :--- | :---: | :--- | :--- |
| `report/engineering.py` | 新增 | `render_engineering(summary) -> str`；内部函数 `_e0`…`_e11`、`_artifact_table`、`_processed_section` | §4.1 |
| `report/common.py` | 修改 | 补 `render_throughput_table`、`render_artifact_table` | §6.3 |
| `tools/build_reports.py` | 修改 | `--types` 支持 `engineering` | §2.2 |

补充约定：

1. E6 的 `data/processed` 专节**必须**包含 design §4.1 的警告块（含两次行数 + PR-001 链接）。
2. E8 工作量表必须区分"实际计算特征数"与"入选特征数"，且数字取自事实表 `feature_funnel`。
3. E10 的 `determinism.executed == false` 时输出「未执行」，**不得**写成「已通过」。
4. 每章首句 `> 本章结论：` 由 `common.section()` 自动加，正文传入 `lead_sentence`。

### 5.3 验收命令

```powershell
& $py tools/build_reports.py --manifest "outputs/$evNew/metadata/experiment_manifest.json" --types engineering
& $py -c "import pathlib;t=pathlib.Path('outputs/$evNew/analysis/reports/engineering.md').read_text(encoding='utf-8');print('sections:',[s for s in ['## E0','## E1','## E2','## E3','## E4','## E6','## E7','## E8','## E9','## E10','## E11'] if s in t]);print('has PR-001:', 'PR-001' in t);print('nan_filled:', '4,940,897' in t);print('funnel absent:', 'zero_variance_dropped' not in t)"
# 期望 11 节齐全；has PR-001: True；nan_filled: True；funnel absent: True

& $py tools/verify_reports.py --milestone M3 --analysis-dir "outputs/$evNew/analysis" --baseline $base --report "$env:TEMP\verify_report_M3.json"
# 期望 exit 0，items == 14
```

### 5.4 验收断言

| # | 断言 |
| :---: | :--- |
| M3-1 | E0–E11 章节齐全（E5 已废弃，不要求） |
| M3-2 | E2 六步耗时之和 == `total_seconds`（±1s） |
| M3-3 | E6 七类目录齐全，合计文件数 == 52 |
| M3-4 | E6 含 `holding_clean` 两次行数与 PR-001 链接 |
| M3-5 | E7 含 smoke/full 对照表与 Δ 列 |
| M3-6 | E8 含"324"与"50"且说明二者关系 |
| M3-7 | E9 ≥10 项，含 `include_signal_date_selection_score` ✘ |
| M3-8 | E10 未执行时显示「未执行」 |
| M3-9 | A-19：不含漏斗数值（`zero_variance_dropped` 字样） |
| M3-10 | A-22：不含"上次运行" |
| M3-11 | 每章有 `> 本章结论：` |
| M3-12 | 报告行数 > 100 且未超 `[HINT]` 阈值时不截断 |
| M3-13 | `engineering.md` 不含业务结论（黑名单：`建议买入`/`预期收益`） |
| M3-14 | Gate items == 14 |

### 5.5 回滚点

`git tag m3-ok`；删除 `report/engineering.py` 与 `reports/engineering.md` 即可。

---

## 6. M4：实验报告（X0–X10）

### 6.1 目标

产出 `reports/experiment.md`，回答"数据和标签怎么来、怎么切、模型表现如何、稳不稳"。

### 6.2 具体动作

| 文件路径 | 动作 | 改什么 | 设计依据 |
| :--- | :---: | :--- | :--- |
| `report/experiment.py` | 重写 | 从 M1 的 `_legacy` 等价实现，改为 X0–X10 结构；新增 `_x1_build`、`_x2_split`、`_x9_robustness`、`_x10_conclusions` | §4.2 |
| `report/common.py` | 修改 | 补 `render_five_number_table`、`render_drift_notice`、`render_group_tables` | §6.3 |
| `analyze_experiment_results.py` | 修改 | **修复 P7**：删除硬编码「把原始持仓与行情数据清洗成可建模样本」，改为按 `input.mode` 分支取文案 | §0.2 P7 |
| `assets/` 生成 | 修改 | 新增分布图生成：`dist_target_raw.png`（0.1%/99.9% 裁切显示）、`dist_target.png`（不裁切） | §4.2.2 |
| 图片路径 | 修改 | 报告中图片统一写 `assets/<file>.png` | §6.5 |
| `report/build.py` | 修改 | 写盘时为图片生成正确的相对路径（`../assets/...`） | §5.2 |

补充约定：

1. **步骤1 文案分支**是 A-14 的硬要求：`holding_file` 与 `samples` 必须输出不同句子。
2. 分布图生成放在 `analyze_experiment_results.py` 的绘图段（与现有 `plot_monotonic_*` 同处），入参只用已加载的 `split_frames`，**不得**重新读 CSV。
3. X2 五数表用 `target_stats_by_split`；漂移提示用 `drift_hint`；两者都不得现算。
4. X9 四项用事实表字段；缺字段走降级矩阵，不现算。
5. `> 简单说：` 每个报告最多 5 处，且只在章节末尾。
6. `label_span_stats` 缺失时按 design §3.5 降级（不显示有效持有期行并标 `[WARN]`），**不得**在渲染器内现算跨度（违反不变量 2）。

### 6.3 验收命令

```powershell
& $py analyze_experiment_results.py --manifest "outputs/$evNew/metadata/experiment_manifest.json"
& $py tools/build_reports.py --manifest "outputs/$evNew/metadata/experiment_manifest.json" --types experiment

& $py -c "import pathlib;t=pathlib.Path('outputs/$evNew/analysis/reports/experiment.md').read_text(encoding='utf-8');print([s for s in ['## X0','## X1','## X2','## X3','## X4','## X5','## X6','## X7','## X8','## X9','## X10'] if s in t]);print('imgs:',t.count('](assets/'));print('simple:',t.count('> 简单说：'));print('drift:', '经验约定' in t);print('five:', all(k in t for k in ['P25','P50','P75','IQR']))"
# 期望 11 节齐全；imgs >= 7；simple <= 5；drift True；five True

# A-14：两路径文案不同
& $py tools/build_reports.py --manifest "outputs/$evOld/metadata/experiment_manifest.json" --types experiment
& $py -c "import pathlib;o=pathlib.Path('outputs/$evOld/analysis/reports/experiment.md').read_text(encoding='utf-8');n=pathlib.Path('outputs/$evNew/analysis/reports/experiment.md').read_text(encoding='utf-8');import re;ko=re.search(r'### X1\.1.*?\n\n(.+?)\n',o,re.S).group(1)[:40];kn=re.search(r'### X1\.1.*?\n\n(.+?)\n',n,re.S).group(1)[:40];print('old:',ko);print('new:',kn);print('different:', ko!=kn)"
# 期望 different: True

& $py tools/verify_reports.py --milestone M4 --analysis-dir "outputs/$evNew/analysis" --baseline $base --report "$env:TEMP\verify_report_M4.json"
# 期望 exit 0，items == 17
```

### 6.4 验收断言

| # | 断言 |
| :---: | :--- |
| M4-1 | X0–X10 章节齐全 |
| M4-2 | A-11 六字段与 step1 state 逐值相等 |
| M4-3 | A-12 buffer 73/63/enforced 出现 |
| M4-4 | A-14 两路径 X1.1 文案不同 |
| M4-5 | A-15 含数据卡与参考页引用，文件存在 |
| M4-6 | A-17 五数（min/P50/max/IQR）+ 漂移提示 + "经验约定" |
| M4-7 | A-25 两张分布图存在且不同（sha256 不等） |
| M4-8 | A-23 图片全部可解析 |
| M4-9 | A-27 X9 四项有数字或"未计算" |
| M4-10 | X1.2 构造逻辑为 5 行表格，含"为什么这样定"列 |
| M4-11 | X1.2 含按年分布表与样本数据处理表 |
| M4-12 | X4 漏斗 324→20→100→50→+1=51 自洽 |
| M4-13 | X10 四小节齐全，X10.2 ≥8 行，X10.3 ≥3 行 |
| M4-14 | A-19 不含 `nan_filled` / `inf_replaced` |
| M4-15 | A-21 `> 简单说：` ≤5 且无新数字 |
| M4-16 | A-29 有效持有期行 + 成因警告块（含 PR-009） |
| M4-17 | A-4 九类数值锚点与 `baseline.report_anchors` 逐值一致 |
| M4-18 | Gate items == 17（本行不计入 items） |

### 6.5 回滚点

`git tag m4-ok`；回退到 M1 的 `_legacy` 等价实现（`report/_legacy.py` 保留至 M6）。

---

## 7. M5：领导版 + 清单 + 接线

### 7.1 目标

产出 `reports/executive.html` 与 `reports/manifest.json`，并把 `bagging.py` 从"硬编码文件名"改为"读清单"。

### 7.2 具体动作

| 文件路径 | 动作 | 改什么 | 设计依据 |
| :--- | :---: | :--- | :--- |
| `report/spec.py` | 新增 | `load_spec()`、`resolve_placeholders()`、`DERIVED` 注册表、`SpecError` | §4.3.2 |
| `report/executive.py` | 新增 | `render_executive(summary, spec) -> str`；自包含 HTML（图片 base64） | §4.3.1 |
| `configs/report/executive_default.yaml` | 校验 | 不改内容；仅断言 4 个 `locked` KPI 与 5 图顺序存在 | 附录 A |
| `report/build.py` | 修改 | `write_manifest()`；alias 生成；`layout` 开关（语义见 design §2.2.1） | §2.4、§5.2 |
| `pipeline/ensemble/bagging.py` | 修改 | 新增 `_pick_report()`；替换 1290/1292/2689/2699/2888/2889 六处 | §5.3 |
| `tools/build_reports.py` | 修改 | `--layout`、`--spec` 参数 | §2.2 |

补充约定：

1. `_pick_report` 必须**先读清单、再回退旧名**；两分支都要有验证（清单存在 / 临时改名验证回退）。
2. alias 文件内容必须与 `reports/` 对应文件**逐字节相同**（A-8）。
3. K3 的 5 张图 base64 内嵌，总大小约 0.5 MB；不得外链（A-3）。
4. KPI 的 4 个 `locked: true` 项不可被 override 删除；删除时 `load_spec` 抛 `SpecError`。

### 7.3 验收命令

```powershell
& $py tools/build_reports.py --manifest "outputs/$evNew/metadata/experiment_manifest.json" --types engineering,experiment,executive --layout v2
& $py -c "import json;m=json.load(open('outputs/$evNew/analysis/reports/manifest.json',encoding='utf-8'));print([r['path'] for r in m['reports']]);print('external:',[e['type'] for e in m.get('external',[])])"

# A-24 结构
& $py -c "import pathlib;t=pathlib.Path('outputs/$evNew/analysis/reports/executive.html').read_text(encoding='utf-8');print('imgs:',t.count('<img'));print('data:',t.count('data:image'));print('detail table:', 'group_id' in t);print('three tables:', all(k in t for k in ['五分组','中位数','阈值']))"
# 期望 imgs == 5；data == 5；detail table False；three tables True

# bagging 接线：清单存在
& $py -c "import sys;sys.path.insert(0,'.');from pipeline.ensemble.bagging import _pick_report;print(_pick_report('outputs/$evNew/analysis','experiment','md'));print(_pick_report('outputs/$evNew/analysis','executive','html'))"

# bagging 接线：清单缺失时回退
Rename-Item "outputs/$evNew/analysis/reports/manifest.json" "manifest.json.bak"
& $py -c "import sys;sys.path.insert(0,'.');from pipeline.ensemble.bagging import _pick_report;p=_pick_report('outputs/$evNew/analysis','experiment','md');print(p);assert p.endswith('experiment_analysis_report.md')"
Rename-Item "outputs/$evNew/analysis/reports/manifest.json.bak" "manifest.json"

& $py tools/verify_reports.py --milestone M5 --analysis-dir "outputs/$evNew/analysis" --baseline $base --report "$env:TEMP\verify_report_M5.json"
# 期望 exit 0，items == 18
```

### 7.4 验收断言

| # | 断言 |
| :---: | :--- |
| M5-1 | A-2 清单存在，3 份权威 + 2 个 deprecated + 2 个 external |
| M5-2 | A-3 三份报告非空；`executive.html` 无外链 `<img>` |
| M5-3 | A-5 禁止词全部不出现 |
| M5-4 | A-8 alias 与权威文件 sha256 相同 |
| M5-5 | A-18 KPI 与 experiment 同值 |
| M5-6 | A-20 4 个 locked KPI 全在；占位符全可解析；无字面数字 |
| M5-7 | A-24 5 图顺序为 train→val→test→all→pred；无逐组明细；三表齐全 |
| M5-8 | A-26 experiment 图集合 ⊇ 领导版 5 图 |
| M5-9 | `_pick_report` 清单存在时返回 `reports/` 路径 |
| M5-10 | `_pick_report` 清单缺失时回退旧名 |
| M5-11 | override 删除 locked KPI → `SpecError` |
| M5-12 | 未知占位符 → `SpecError` 且列出可用字段 |
| M5-13 | 页脚显示规格来源与 A-24 说明 |
| M5-14 | 阈值表用「保留前 N%」措辞 |
| M5-15 | 风险提示独立成行且在行动项之前 |
| M5-16 | 分页逻辑：追加 3 个 KPI 后仍能渲染 |
| M5-17 | `bagging.py` 不再含 `experiment_analysis_report.md` 字面量（除回退分支） |
| M5-18 | Gate items == 18 |

### 7.5 回滚点

`git tag m5-ok`；回退 `bagging.py` 的 6 处改动 + 关闭清单即可（alias 可保留）。

---

## 8. M6：文档与 demo 收尾

### 8.1 目标

补齐文档、双路径 demo、独立对比工具，使 v1.1.1 可发布。

### 8.2 具体动作

| 文件路径 | 动作 | 改什么 | 设计依据 |
| :--- | :---: | :--- | :--- |
| `docs/10_architecture.md` | 修改 | 模块表增 `report/` 一行；`tools/` 增 3 个脚本 | §10 |
| `README.md` | 修改 | 增"报告"章节：三份报告用途 + 生成命令 | §10 |
| `docs/16_potential_risks.md` | 修改 | PR-002 标记「v1.1.1 已闭环（清单化）」 | — |
| `tools/compare_runs.py` | 新增 | `--left`/`--right`/`--output`；口径校验 + Δ 表 | §11 |
| `outputs/_compare/` | 运行期 | 对比产物，不入库 | §11.3 |

### 8.3 验收命令

```powershell
# 双路径完整产物
foreach ($ev in @($evOld, $evNew)) {
  & $py tools/build_reports.py --manifest "outputs/$ev/metadata/experiment_manifest.json" --types engineering,experiment,executive,datacard,reference --layout v2
  & $py tools/verify_reports.py --milestone M6 --analysis-dir "outputs/$ev/analysis" --baseline $base --report "$env:TEMP\verify_$($ev.Split('/')[-1]).json"
  if ($LASTEXITCODE -ne 0) { throw "verify failed: $ev" }
}

# 运行间对比（口径不同 → 只出差异说明）
& $py tools/compare_runs.py --left "outputs/$evOld/analysis/report_summary.json" --right "outputs/$evNew/analysis/report_summary.json" --output "outputs/_compare/old__vs__new.md"
& $py -c "import pathlib;t=pathlib.Path('outputs/_compare/old__vs__new.md').read_text(encoding='utf-8');print('口径不一致提示:', '不可直接比较' in t);print('无指标Δ:', 'Δ Spearman' not in t)"
# 期望 True / True

# 文档一致性
& $py -c "import pathlib;t=pathlib.Path('docs/10_architecture.md').read_text(encoding='utf-8');print('report pkg:', 'report/' in t);print('scripts:', all(s in t for s in ['build_reports.py','verify_reports.py','compare_runs.py']))"
```

### 8.4 验收断言

| # | 断言 |
| :---: | :--- |
| M6-1 | 双路径各产出 3 报告 + 1 数据卡 + 1 参考页 |
| M6-2 | 双路径 Gate 均 exit 0 |
| M6-3 | `compare_runs` 口径不同时只输出说明，不输出指标 Δ |
| M6-4 | `compare_runs` 口径相同时输出 6 项 Δ |
| M6-5 | `docs/10_architecture.md` 含 `report/` 与 3 个新脚本 |
| M6-6 | `README.md` 含报告章节与命令 |
| M6-7 | `docs/16_potential_risks.md` PR-002 已标记闭环 |
| M6-8 | `git status --porcelain` 为空 |
| M6-9 | 打 tag `v1.1.1` 后 `git describe --tags` 返回 `v1.1.1` |
| M6-10 | 双路径 A-29 行为正确：demo_new（samples）有「有效持有期」行且含 PR-009；demo_old（holding_file）无该行且 Gate 仍 exit 0 |

### 8.5 回滚点

文档回退；`v1.1.1` tag 未打之前可回到 `m5-ok`。

---

## 9. 断言 → 实现位置映射

`tools/verify_reports.py` 内部结构（固定）：

```python
CHECKS = {
    "A-1":  _a1_schema_keys,
    "A-2":  _a2_manifest,
    "A-3":  _a3_reports_exist_no_external_img,
    "A-4":  _a4_numeric_anchors,
    "A-5":  _a5_forbidden_words,
    "A-6":  _a6_idempotent,
    "A-7":  _a7_single_source,
    "A-8":  _a8_aliases,
    "A-9":  _a9_length_hint,
    "A-10": _a10_chapter_leads,
    "A-11": _a11_v11_fields,
    "A-12": _a12_buffer,
    "A-13": _a13_label_list,
    "A-14": _a14_step1_wording,
    "A-15": _a15_references,
    "A-16": _a16_no_duplicate_definitions,
    "A-17": _a17_extremes_and_drift,
    "A-18": _a18_allowed_redundancy,
    "A-19": _a19_no_cross_leak,
    "A-20": _a20_spec_driven,
    "A-21": _a21_simple_hint,
    "A-22": _a22_no_run_diff,
    "A-23": _a23_images_resolvable,
    "A-24": _a24_monotonic_100,
    "A-25": _a25_distribution_figures,
    "A-26": _a26_experiment_superset,
    "A-27": _a27_robustness,
    "A-28": _a28_param_effective,
    "A-29": _a29_label_span,
}
```

| 里程碑 | 启用的断言 | 期望 items |
| :---: | :--- | ---: |
| M0 | 见 §2.4 枚举行（13 项） | 13 |
| M1 | A-6, A-7, A-9 + 自定义 3 项 | 6 |
| M2 | A-13 + 自定义 7 项 | 8 |
| M3 | A-6, A-9, A-10, A-19, A-22, A-28 + 自定义 8 项 | 14 |
| M4 | A-4, A-11, A-12, A-14, A-15, A-17, A-19, A-21, A-23, A-25, A-27, A-29 + 自定义 5 项 | 17 |
| M5 | A-2, A-3, A-5, A-8, A-18, A-20, A-22, A-24, A-26 + 自定义 9 项 | 18 |
| M6 | 29 条断言（A-1..A-29）+ §8.4 自定义 10 项 | 39 |

> `expected_item_counts` 已写入 baseline JSON；Gate 会校验 `len(items)` 是否匹配，防"漏跑断言"。
> `--milestone` 同时决定**启用哪些断言**与**期望 items 数**；未传 `--milestone` → exit 2。
> **计数规则**：各里程碑「验收断言」表里列为 `M<k>-*` 的行是**必须存在的断言**；`Gate items == N` 行是计数校验，**不计入 items**。若枚举行数超过 N，则把 N 上调并在该里程碑提交说明；M6 的 N 恒等于「29 条断言 + §8.4 枚举行数」。里程碑自定义项（`M0-1`…`M6-9`）写在 `verify_reports.py` 内，编号前缀固定 `M<k>-`。

---

## 10. 两项补充决策

| # | 决策 | 理由 |
| :---: | :--- | :--- |
| E1 | M1 的 golden 基线取「M0 完成后、M1 改动前」的 demo_new 全量报告 | 这是最后一个"改造前"的稳定输出；用 smoke 会掩盖长文本差异 |
| E2 | `--layout` 在 M0–M4 保持 `v1`，M5 起默认 `v2` | 保证 M1 的 golden 可比；同时让 M5 一次性切换目录结构，减少中间态 |

---

## 11. 结项标准

同时满足以下全部条件，视为 v1.1.1 可发布：

1. M0–M6 各自 Gate exit 0，且 `items` 数量与 baseline `expected_item_counts` 匹配；
2. 双路径 demo 各产出 3 报告 + 1 数据卡 + 1 参考页，`verify_reports` 全部 exit 0；
3. `baseline_v1.1.0.json` 中 `experiments` 的数值未发生任何变化（重构无副作用）；
4. `git status --porcelain` 为空；
5. `pipeline/**`（除 `ensemble/bagging.py` 的 6 处接线）与 `data_prep/**` 无改动；
6. 历史 `verify_report_M*.json` 未被覆盖（`git status` 与 `git diff` 均无）；
7. 已打 annotated tag `v1.1.1`，且 tag 指向最后一个 M6 提交。
