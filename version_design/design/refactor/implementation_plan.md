# v1.1 重构 — 实施计划

> 状态: 待执行（M0 开工前置）
> 创建日期: 2026-11-10
> 规范（唯一内容权威）: [experiment_design.md](./experiment_design.md)
> 执行边界（交给实施 agent）: [code_agent_prompt.md](./code_agent_prompt.md)
> 回归锚点: [baseline_v1.0.13.json](./baseline_v1.0.13.json)
> 目标 tag: `v1.1.0`（annotated）｜分支: `refactor/v1.1`｜基线 tag: `v1.0.13`（`70d4341`）

本文件只回答两件事：**按什么顺序改哪些文件**、**用什么命令判定通过**。规格正文一律以 `experiment_design.md` 的 § 编号引用，不在此重复；任何与本文件冲突之处，以规范文档为准（仲裁顺序见 `code_agent_prompt.md` §0）。

---

## 0. 总览

```text
M0  基线自包含 + 探测固化 + 验收工具就绪        只新增文件；旧 pipeline 零改动
M1  samples 层                                  data_prep/ 新增 + config 纯增量 + step1 顶部守卫
M2  labels 层与缓存                             纯新增
M3  接线准备（校验 / 契约 / 就绪检查）            零行为变化
M4  step1 双模式 + runner 签名 + R1 回归         唯一行为变化
M5  收尾与发布                                  文档 + registry + 归档 verify_v11
```

顺序不可交换。每个里程碑（含 M0）单独合入后，仓库必须可跑、旧行为必须可证（§7.1、§7.3）。

### 0.1 依赖关系表

| 里程碑 | 依赖 | 必须先存在的产物 | 提前做的后果 |
| :---: | :--- | :--- | :--- |
| M0 | 无（可独立开工） | — | — |
| M1 | M0 | `baseline_v1.0.13.json` 快照（含 `legacy_holding_samples_set_hash`）、`tools/verify_v11.py`、`configs/kernel_v11/check/refactor_check.yaml` | 无法判定「旧样本集未漂移」，M1 验收 #6 无锚点 |
| M2 | M1 | `data/prepared/{samples_id}/samples.parquet` + `manifest.json` | 标签无处对齐 `sample_id`，行契约不可验 |
| M3 | M1, M2 | `data_prep` 两层可导入、`data/labels/**` 已生成 | `check_raw_data` 的 `prepared` 段与 V4/V7/V13/V14 无可校验对象 |
| M4 | M1, M2, M3 | 标签产物、V1–V14 校验、`SAMPLES_REQUIRED_COLUMNS` / `LABEL_REQUIRED_COLUMNS` | 双模式会绕过校验，行为变化无兜底（§7.3） |
| M5 | M0–M4 全部 exit 0 | 每步 `verify_report.json` 与本地 tag | 发布 Gate 无法判定（§7 M5） |

### 0.2 判定权

每个里程碑的通过与否，**只**由 `tools/verify_v11.py --milestone M<k>` 的 exit code 决定（§8.1、§8.2 V-4）。人工书面结论、命令回显、截图都只能作为补充证据，**不得**替代该 exit code。该脚本由 M0 创建，当前不存在。

### 0.3 本计划不覆盖

| 项 | 归属 |
| :--- | :--- |
| 阶段 0 对照实验（0A–0D） | `experiment_plan_v11.md`，**不阻塞** v1.1.0 |
| v1.2 backlog（`holding_clean` 改名、per-sample 标量通道、bagging 支持新路径） | §0.4 |
| 运行环境的安装与依赖升级 | 不在本计划内，出现依赖缺失即视为阻塞（见 prompt §2） |

---

## 1. 通用约定

### 1.1 解释器与命令形态

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
$base = "version_design/design/refactor/baseline_v1.0.13.json"
```

所有命令在**仓库根**执行。本计划只用两类真实命令（`code_agent_prompt.md` 禁止发明清单第 16 条）：`$py -c "<one-liner>"` 与 `$py run_experiment_pipeline.py --mode {smoke|full} --step {1..6} --config <path> --state-file <name>`。

### 1.2 `state-file` 与 `output-dir` 解析

| 传入形态 | 落点 | 依据 |
| :--- | :--- | :--- |
| 裸文件名（无目录分隔符） | `outputs/<experiment_version>/metadata/<name>` | `pipeline/config.py::_resolve_state_file_path` |
| 带目录的相对/绝对路径 | 原样 | 同上 |

`experiment_version` 由 `PipelineParams.build_experiment_version()` 得出，含参数哈希，**不能**手写。需要它时用一行命令算：

```powershell
$ver = & $py -c "from pipeline.config import load_params; print(load_params('configs/local/refactor_check.yaml').build_experiment_version())"
```

`tools/verify_v11.py` 的 `--state-file` / `--output-dir` **可重复传入**；脚本按 state 顶层的 `experiment_version` 反推输出目录，不按文件名猜（详见 §2.3）。

### 1.3 回滚 tag 与提交纪律

每个里程碑验收通过后按**固定顺序**收尾，三步缺一不可：`git add <本里程碑白名单路径>` → `git commit -m "<type>: <subject>"` → `git tag m<k>-ok`。失败时回退到 `m<k-1>-ok`（§7.3 止损规则）。

| 规则 | 说明 |
| :--- | :--- |
| 只暂存白名单 | `git add` 必须逐条列出该里程碑白名单路径；**不得** `git add -A`、**不得** `git commit -a` |
| 先提交再打 tag | tag 必须指向已包含该里程碑改动的提交；在提交之前打 tag 会指到旧树 |
| 两种 tag 不是一回事 | `m<k>-ok` 是本地轻量回滚 tag；`v1.1.0` 是发布用 annotated tag |
| 为什么必须提交 | A3-01（`git diff --name-only HEAD`）与 A5-01（`git ls-files`）都以 HEAD 为基准：不提交时 M3 的 diff 会带出 M0/M1/M2 的文件（判越界）；M5 删除 `verify_v11.py` 后未提交，`git ls-files` 仍返回 0（判未删除） |
| 删除文件怎么暂存 | 用 `git add <已删除路径>` 暂存删除（等价 `git rm --cached` + 文件已删），仍然不要用 `git add -A` |
| 台账要跟着提交 | `outputs/experiment_registry.json` 是**已跟踪**文件，而每次跑 pipeline 都会重写它。凡本里程碑运行过 pipeline 的，收尾时把它一并 `git add`；它不计入白名单越界，但必须随该里程碑提交，否则 `git status` 会留下白名单外的改动（违反结项标准第 6 条） |

未跟踪的运行期产物（`data/**`、`outputs/**`、`models/**`、`configs/local/**`）不进入任何里程碑提交；`version_design/design/refactor/**` 中列在该里程碑白名单里的设计产物（如 `baseline_v1.0.13.json`、`datebar_probe.json`）随该里程碑一起提交。

### 1.4 临时验证配置

`configs/local/**` 已被 `.gitignore` 排除。本计划允许在其中新建 `refactor_check_*.yaml` 作为验证配置（`refactor_check_legacy.yaml`、`refactor_check_samples.yaml`），**不入库**、不计入白名单越界。除此之外不得在 `configs/` 下新增文件。

> 本机 `data/raw/` 为空，raw 数据由 `configs/local/refactor_check.yaml` 的绝对路径提供。因此：

| 配置文件 | 用途 | 路径形态 |
| :--- | :--- | :--- |
| `configs/kernel_v11/check/refactor_check.yaml` | **入库**的回归配置，供 M5 registry 与外部复现 | 相对仓库根（`data/raw/...`） |
| `configs/local/refactor_check.yaml` | 本机实际跑数据的事实来源 | 绝对路径 |

两者除 `data.*` 路径外必须**逐值相同**（同 `seed`、同 `smoke_size`、同 `experiment.*`）。两条特别约定：

- 入库配置的 `smoke_size` 必须显式写成 `baseline.smoke.n`（回归要用同一个值），本地配置同步改成同值；不得用 `--smoke` 临时覆盖来「凑」版本。
- 当 `data/raw/` 为空时，对入库配置跑 `check_raw_data` 返回 2 是**预期**，不是失败；此时所有需要真实数据的命令改用 `configs/local/refactor_check.yaml`。

---

## 2. M0：基线自包含 + 探测固化 + 验收工具就绪

### 2.1 目标

把「基线是什么」「行情事实是什么」「怎么判定通过」三件事固化成可执行的东西，让后续每个里程碑都有机器 Gate。

### 2.2 具体动作

| 文件路径 | 动作 | 改什么（函数/类/键） | 设计依据 |
| :--- | :---: | :--- | :--- |
| `tools/verify_v11.py` | 新增 | 断言引擎：`main()`、`run_milestone(milestone, ...)`、`assert_true(name, expected, actual)`、`write_report()` | §8.1 / §8.2 / §8.3 |
| `tools/probe_datebar.py` | 新增 | `probe(path) -> dict`：列、dtype、行数、`n_symbols`、`idxd` 边界、停牌占比、`marks` 取值数、`xs` 口径；`--config`、`--json` | §1.1 / §7 M0 |
| `docs/14_datebar_probe.md` | 新增 | 探测结论 + C1–C4 四节书面结论（`### C1` … `### C4`） | §7 M0 |
| `version_design/design/refactor/datebar_probe.json` | 新增 | `probe_datebar.py --json` 的输出落盘（入库，供 verify 读取） | §8.1 M0④ |
| `version_design/design/refactor/baseline_v1.0.13.json` | 修改 | 补 §8.3 全表字段；`raw` 指纹块；`legacy_holding_samples_set_hash`；`step1.target_stats`；`labels.target_holding_return.coverage`；`data_prep` 参数快照；`labels.builtin_names` 期望清单 | §8.3 |
| `configs/kernel_v11/check/refactor_check.yaml` | 新增 | 相对路径版回归配置；`smoke_size` 取值 = `baseline.smoke.n`；`experiment.*` 与本地配置逐值相同 | §7 M0 |
| `configs/experiment_index.yaml` | 修改 | 注册一条 `name`/`config`/`description`/`expected_base_version`/`label` | `docs/08` |

补充约定：

1. `baseline_v1.0.13.json` 的 `data_prep` 快照用于 **M1/M2 的期望值**（`universe.exclude_prefixes`、`universe.min_history_days`、`samples.method`、`samples.params`、`labels.params`），不得写在 verify 代码里。
2. `labels.builtin_names` 是**标签名期望清单**（由 §4.6–§4.8 得到：`future_return` 的四个 horizon、`trend_quality` 的默认 h、`holding_return` 单例），必须存在 JSON 中，**不得**出现在 verify 代码中——否则违反「代码里不得出现标签名字面量」（禁止发明清单第 1 条）。
3. `labels.target_holding_return.coverage` 在 M0 由「raw holding 套用 step1 过滤后 `holding_return.notna()` 占比」实测得到（由构造应恰为 `1.0`）。M2 用它检出新层过滤口径漂移。
4. raw 密码学指纹只对 `data.holding_file` / `data.datebar_file` 实际指向的文件计算，路径本身不入版本哈希。
5. `git rev-parse --short HEAD` 取不到时 `code_version` 写 `""`（§3.1），不要抛异常。

### 2.3 `tools/verify_v11.py` 专节（M0 必做）

该脚本**当前不存在**，由 M0 创建；M0–M5 的每一个 Gate 都由它给出 exit code。它不是长期机制（M5 归档或删除），不得被 `pipeline/**` 或 `data_prep/**` import。

#### 2.3.1 参数契约

| 参数 | 必填 | 语义 | 备注 |
| :--- | :---: | :--- | :--- |
| `--milestone` | 是 | `M0`…`M5`（大小写敏感） | 非法值 → exit 2 |
| `--baseline` | 是 | baseline JSON 路径 | 不可读 / JSON 解析失败 → exit 2 |
| `--baseline-git-ref` | M0 是 | 该 baseline 在 M0 修改**之前**的 git 提交 | A0-15 用它取旧 `smoke` 段做防篡改比对；读不到该 ref → exit 1（不得 skip） |
| `--config` | 否 | 本次验收的 YAML，默认 `configs/kernel_v11/check/refactor_check.yaml` | M0⑤、M3②③④ 使用；本机可传 `configs/local/refactor_check.yaml` |
| `--state-file` | M3–M5 必填 | state JSON 路径或裸名；**可重复** | M0–M2 忽略 |
| `--output-dir` | M3–M5 必填 | `outputs/<experiment_version>`；**可重复** | 必须与对应 state 内的 `experiment_version` 一致，不一致判 fail |
| `--report` | 否 | 报告路径，默认 `verify_report.json`（仓库根） | 只写这一个文件 |

`--state-file` / `--output-dir` 必须是**可重复**参数：M4 需要同时验收旧路径（L1/L3）与新路径（samples smoke）两份 state，二者 `experiment_version` 不同。未显式给出 `--output-dir` 时按 state 的 `experiment_version` 反推，但该反推只是容错路径，M3–M5 的验收命令必须显式传。

#### 2.3.2 输入（只读，不跑 pipeline、不改任何产物）

| 输入 | 用法 |
| :--- | :--- |
| baseline JSON | 所有 `expected` 值的**唯一**来源 |
| `datebar_probe.json` | M0④ |
| `state` JSON | `experiment_version`、`steps.step_*` 的 `status` / `checks_passed` / `details` |
| `data/prepared/*/manifest.json` + `samples.parquet` | M1 列契约/哈希/排序；M2 行数；M4 守恒恒等式 |
| `data/labels/*/_index.json` + 标签 parquet | M2 |
| `data/processed/<ver>/*.parquet`、`data/features/<ver>/*` | M4 |
| `docs/14_datebar_probe.md` | M0⑥、A4-16（两套规范化比对结论） |
| `git` 只读命令 | M3①（`git diff --name-only HEAD`）、M5①（`git ls-files`）、M5④（`git cat-file -t`） |
| 子进程 `sys.executable -c` | M0⑤、M3②③④ 的加载/就绪检查（不写产物） |

依赖限制：只 import 标准库 + `pandas` / `pyarrow`。**不得** import `pipeline` / `data_prep`。

#### 2.3.3 输出

`verify_report.json`：

```json
{
  "milestone": "M4",
  "generated_at": "ISO8601",
  "git_commit": "short-sha",
  "exit_code": 0,
  "items": [
    {"id": "A4-09", "name": "conservation_identity", "expected": {}, "actual": {}, "pass": true, "detail": ""}
  ],
  "failed": []
}
```

每条断言必须写入 `items`，`expected` / `actual` 原样落盘（不做四舍五入），失败项同时进 `failed`。

#### 2.3.4 exit code 规则

| 情形 | exit |
| :--- | :---: |
| 全部 `pass == true` | `0` |
| 任一 `pass == false` | `1` |
| 产物缺失（文件/目录/键/state 缺步骤） | `1` |
| 参数错误（`--milestone` 非法、`--baseline` 不可读） | `2` |

硬性纪律：

1. **缺产物一律 exit 1**，不得 `skip`、不得 `continue`、不得「未就绪视为通过」。
2. 禁止 `all([]) == True` 式空真：任何断言集合在为空时必须显式 fail；`items` 为空也必须 fail。
3. 禁止 `try/except` 吞异常：捕获后必须转成一条 `pass=false` 的 item。
4. `expected` 只能来自 baseline / 契约常量；**代码中不得出现观测值数字字面量**。容差允许写成命名常量（`REL_TOL_PROBE = 0.01`、`ABS_TOL_FLOAT = 1e-12`），它们是规则而不是观测值。
5. 比较规则：计数用精确相等；浮点用 `abs(a-b) <= ABS_TOL_FLOAT * max(1, abs(b))`；比例/规模用相对误差 `<= REL_TOL_PROBE`。
6. 只读：禁止写除 `verify_report.json` 以外的任何文件。

#### 2.3.5 §8.1「逐里程碑检查项」逐条映射（六张表，项数不得减少）

**M0**

| §8.1 原文项 | 断言 ID | 判定方式 |
| :--- | :---: | :--- |
| ① baseline 含 §8.3 全表字段且无空值 | A0-01 / A0-02 / A0-03 / A0-04 / A0-05 | 身份、参数快照、数据身份、`target_stats`、coverage 五组键逐组检查；`None`/`""`/`{}`/`[]` 均判空 |
| ② 两个 raw 文件 `sha256` 与 baseline 一致 | A0-06 | 现场重算 sha256，与 baseline 字符串精确相等 |
| ③ `legacy_holding_samples_set_hash` 非空 | A0-07 | 匹配 `^[0-9a-f]{16}$` |
| ④ probe json 的 rows / n_symbols / min_idxd / max_idxd 与 baseline 一致（规模 ±1%） | A0-08 / A0-09 / A0-10 | rows、n_symbols 相对误差 ≤ `REL_TOL_PROBE`；min/max `idxd` 精确相等 |
| ⑤ `refactor_check.yaml` 能被 `load_params` 加载 | A0-11 | 子进程加载成功（exit 0）且 `input.mode == "holding_file"` |
| ⑥ `docs/14` 含 C1–C4 四个结论 | A0-12 | 正则命中 `^### C[1-4]` 各一次 |
| （补充）基线未被篡改 | A0-15 | 用 `--baseline-git-ref` 取旧文件，断言 `smoke` 段逐值相同；只允许新增其它字段 |
| （补充）两套回归配置等价 | A0-16 | 子进程分别加载入库配置与本地配置，`build_experiment_version()` 必须相等 |

**M1**

| §8.1 原文项 | 断言 ID | 判定方式 |
| :--- | :---: | :--- |
| ① samples 列集 == §3.1 且无 `target*` / `label*` 列 | A1-01 / A1-02 | 列集合相等（顺序无关）；列名正则 `^(target\|label)` 命中数必须为 0 |
| ② `sample_id` 为 int64、唯一、严格升序 | A1-03 / A1-04 / A1-05 | dtype 相等；`nunique == len`；`diff() > 0` 全真 |
| ③ manifest 键完整 | A1-06 | §3.1 列出的 17 个键全部存在 |
| ④ `params_hash` / `datebar_fingerprint` / `samples_set_hash` 重算相等 | A1-07 / A1-08 / A1-09 | 按 §3.4 算法重算（verify 内部独立实现，不复用 `data_prep`） |
| ⑤ `max_samples` 生效时 `rows == max_samples` 且 `sampling_rate == rows / rows_before_cap` | A1-10 / A1-11 | 从 manifest 读 `max_samples`；未生效（`null`/0）时该项判「预期不适用」但必须记 item（`expected = null`），不得省略 |
| ⑥ 无 `sh000` / `sz399` / `sh950` 前缀 | A1-12 | 前缀列表来自 `baseline.data_prep.universe.exclude_prefixes` |
| ⑦ `legacy_holding` 的 `(idxs, idxd)` 集合指纹 == baseline | A1-13 / A1-14 | 对每个发现的样本集重算 §8.3 指纹，要求**至少一个**等于 baseline；同时校验 `sample_id` 与 `(idxs, idxd)` 双射 |

**M2**

| §8.1 原文项 | 断言 ID | 判定方式 |
| :--- | :---: | :--- |
| ① 每个内置标签文件存在且列集 == §3.2 | A2-01 / A2-02 | 期望名取自 `baseline.labels.builtin_names`；列集合逐文件相等 |
| ② `rows == samples.rows`（全部标签，含 NaN 行） | A2-03 | 每个标签 parquet 行数 == 对应 `samples.parquet` 行数 |
| ③ `target` 无 ±inf | A2-04 | `np.isinf` 计数为 0；NaN 允许 |
| ④ `_index.json` 每项 `params_hash` 重算相等、`coverage == non_null_rows / rows` | A2-05 / A2-06 | 按 §3.4 重算；比例精确到 `ABS_TOL_FLOAT` |
| ⑤ `target_holding_return` 的 coverage 与 baseline 一致 | A2-07 | 差 ≤ `ABS_TOL_FLOAT` |
| （补充）文件与索引双向一致 | A2-08 / A2-09 / A2-10 | 无孤儿文件/孤儿条目；`label_name` == 文件名；`samples_id` / `datebar_fingerprint` == manifest |

**M3**

| §8.1 原文项 | 断言 ID | 判定方式 |
| :--- | :---: | :--- |
| ① `git diff --stat` 只含 M3 白名单 | A3-01 | 以本里程碑开工前记录的 `git status --porcelain` 为基线，结束时 `git diff --name-only HEAD`（已跟踪文件）与 `git status --porcelain`（未跟踪新增）相对基线**只多出** M3 白名单路径；开工前已存在的改动（如 `.gitignore`）与 `version_design/**`、`outputs/**`、`data/**`、`configs/local/**` 不计入 |
| ② 旧配置 `load_params` 成功且 `input.mode=holding_file` | A3-02 | 子进程加载成功并打印值 |
| ③ samples 配置 `load_params` 成功，且 `pytest` 覆盖 step1「未实现」拒绝用例 | A3-03 / A3-04 | 子进程加载成功；测试文件中存在同时引用 `input.mode`/`samples` 与 `NotImplementedError` 的用例（A3-04 为代理断言，见 §5.4 括注） |
| ④ `check_raw_data --config` → exit 0 | A3-05 | 子进程退出码 == 0 |
| （补充）契约常量与依赖方向 | A3-06 / A3-07 / A3-08 / A3-09 / A3-10 | adapters 暴露两套常量；step2 计数键拆分；`docs/15` 存在；`data_prep` 无 `import pipeline`；CLI 不自行 `yaml.safe_load` |

**M4**

| §8.1 原文项 | 断言 ID | 判定方式 |
| :--- | :---: | :--- |
| ① state 中 L3 全部字段与 baseline 相等（计数精确、浮点 1e-12），且 step1 的 `sample_count` / `datebar_rows` / `target_stats` 相等 | A4-01 / A4-02 / A4-03 / A4-04 / A4-05 | 遍历 `baseline.smoke.*` 的每个键（含 `step_1..step_6.checks` 布尔）；不写死字段名清单之外的值 |
| ② samples 模式 step1 六个 `checks` 全 True、`details` 键完整、`dropped_no_target > 0` | A4-06 / A4-07 / A4-08 | checks 键集合与 baseline 同名且全 True；`details` 16 键齐全；计数 > 0 |
| ③ 守恒恒等式成立（§4.9，含 step2 `dropped_samples`） | A4-09 | 四项等式的起算口径见 §9；口径由 state 顶层 `mode` 决定，见 §9「口径选择规则」 |
| ④ state 中 step1 的 `details.input_mode` / `samples_id` / `label_hash` 与当前配置推导值一致 | A4-10 | 子进程按当前 YAML 推导 `samples_id`；`label_hash` 由标签 `_index.json` 对账 |
| （补充）同构性与契约 | A4-11 … A4-18 | `holding_clean` 文件名/必需列、dtype、`datebar.parquet` 恒 9 列、升序、`invalid_reasons` 三键、新路径到 step6、两套规范化比对结论 |

**M5**

| §8.1 原文项 | 断言 ID | 判定方式 |
| :--- | :---: | :--- |
| ① `tools/verify_v11.py` 已删除或已归档（`git ls-files` 无此路径） | A5-01 | `git ls-files --error-unmatch tools/verify_v11.py` 非 0 |
| ② `outputs/experiment_registry.json` 只含 `status=completed` 的条目 | A5-02 / A5-03 | 每条按其 `experiment_version` 在其 `metadata/` 下找 `pipeline_state_*.json`，要求存在且 `step_1..step_6` 全 `completed`、`checks_passed` 全 True；条目数为 0 判 fail（禁止空真） |
| ③ `pytest tests -q` 与一次 smoke 的通过证据存在 | A5-04 / A5-05 | smoke 证据：由 `--state-file` 指定的 state 六步全绿；pytest 证据：`tests/**` 至少一个文件、且 `pytest.ini` 存在（该命令的退出码由人工在结项报告中记录，verify 不代跑） |
| ④ `git cat-file -t v1.1.0` 返回 `tag` | A5-06 | 子进程输出 `tag` |
| （补充）文档收敛 | A5-07 / A5-08 | `README.md` 记一行目录约定；`AGENTS.md` §5 与 `docs/08` 不再并存两套路径表述 |

> 「六张表」指上表六个小节的 **全部原文项**；新增的 A*-补充项只能追加，不能替换或减少原文项。

### 2.4 验收命令

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
$base = "version_design/design/refactor/baseline_v1.0.13.json"

# M0-1 行情探测（stdout → 文件；用 Set-Content 避免 PowerShell 默认 UTF-16）
& $py tools/probe_datebar.py --config configs/local/refactor_check.yaml --json |
    Set-Content -Encoding utf8 version_design/design/refactor/datebar_probe.json
if ($LASTEXITCODE -ne 0) { throw "probe_datebar 失败" }

# M0-2 旧路径 full step1（既有代码，M0 不改它），取得旧 holding_clean
& $py run_experiment_pipeline.py --mode full --step 1 `
    --config configs/local/refactor_check.yaml --state-file m0_legacy_step1.json
if ($LASTEXITCODE -ne 0) { throw "旧路径 step1 失败" }

# M0-3 计算旧样本集指纹（结果写入 baseline 的 legacy_holding_samples_set_hash）
#   先用当前配置推导 $ver，并断言与 baseline.experiment_version 一致；不一致在这里明确报错，
#   而不是在 read_parquet 处崩溃
$ver = & $py -c "from pipeline.config import load_params; print(load_params('configs/local/refactor_check.yaml').build_experiment_version())"
$ver_base = & $py -c "import json; print(json.load(open('version_design/design/refactor/baseline_v1.0.13.json',encoding='utf-8'))['experiment_version'])"
if ($ver -ne $ver_base) { throw "配置推导的 experiment_version ($ver) 与 baseline ($ver_base) 不一致；最常见原因是 smoke_size 未与 baseline.smoke.n 对齐（见 §1.4），或配置在基线之后被改过" }
& $py -c "import hashlib,pandas as pd,sys; d=pd.read_parquet(sys.argv[1],columns=['idxs','signal_date']); s='\n'.join(sorted(f'{a},{int(b)}' for a,b in zip(d['idxs'],d['signal_date']))); print(hashlib.sha256(s.encode()).hexdigest()[:16])" "data/processed/$ver/holding_clean.parquet"
if ($LASTEXITCODE -ne 0) { throw "旧样本集指纹计算失败" }

# M0-4 基线就绪自检（a170d67 = baseline 纳入版本控制时的提交，供 A0-15 防篡改比对）
& $py tools/verify_v11.py --milestone M0 --baseline $base --baseline-git-ref a170d67
if ($LASTEXITCODE -ne 0) { throw "M0 验收未通过" }

# M0-5 入库配置的就绪检查（data/raw 为空 → 2 属预期；已就绪 → 0）
& $py tools/check_raw_data.py --config configs/kernel_v11/check/refactor_check.yaml
```

期望 exit code：M0-1 `0`、M0-2 `0`、M0-3 `0`、M0-4 `0`、M0-5 `0` 或 `2`（见 §1.4）。

### 2.5 验收断言

| ID | 断言 | 阈值 / 容差 |
| :---: | :--- | :--- |
| A0-01 | baseline 身份键 `git_tag` / `git_commit` / `run_date` / `label` / `base_version` / `version_subdir` 全部非空 | 精确 |
| A0-02 | 参数快照段 `window` / `time_split` / `feature_selection` / `manual_features` / tsfresh 段 / `lgb` / `seed` / `smoke_size` 全部存在且非空 | 精确 |
| A0-03 | 两个 raw 文件的 `sha256` / `rows` / `min` / `max` 齐全非空 | 精确 |
| A0-04 | `step1.target_stats` 含 `count/mean/std/min/25%/50%/75%/max` 且无空值 | 键集合精确 |
| A0-05 | `labels.target_holding_return.coverage` ∈ [0, 1] | `ABS_TOL_FLOAT` |
| A0-06 | 两个 raw 文件现场 sha256 == baseline | 精确相等 |
| A0-07 | `legacy_holding_samples_set_hash` 匹配 `^[0-9a-f]{16}$` | 正则 |
| A0-08 | `datebar_probe.json.rows` 与 baseline 相对误差 | ≤ 1% |
| A0-09 | `datebar_probe.json.n_symbols` 相对误差 | ≤ 1% |
| A0-10 | `datebar_probe.json.min_idxd` / `max_idxd` 与 baseline 相等 | 精确 |
| A0-11 | `--config` 可被 `load_params` 加载且 `input.mode == "holding_file"` | 精确 |
| A0-12 | `docs/14_datebar_probe.md` 有 `### C1` / `C2` / `C3` / `C4` 四节 | 各 1 次命中 |
| A0-13 | baseline 的 L3 观测块可解析为「字段名 → 数值」，与 state 的 `steps` 结构同名 | 结构可解析 |
| A0-14 | `baseline.labels.builtin_names` 为非空字符串数组，且至少含 1 个命中 `^target_future_\d+d$` 的项、1 个命中 `^target_holding_return$` 的项 | 非空 + 各 pattern 命中 ≥ 1 |
| A0-15 | **防篡改**：`git show <--baseline-git-ref>:<baseline 路径>` 的 `smoke` 段与当前 baseline 的 `smoke` 段逐值相同（键、值、嵌套 `steps.*.checks` 全部相等）；基线观测值只允许被"新增字段包围"，不允许被改写 | 深度相等；ref 读不到 → fail |
| A0-16 | 入库配置与本地配置的 `build_experiment_version()` 相等（两者除 `data.*` 外逐值相同，而 `data.*` 不入哈希） | 字符串精确相等 |

### 2.6 回滚点

验收通过后按固定顺序三步收尾（只暂存白名单，禁用 `git add -A` / `git commit -a`）：

```powershell
git add tools/probe_datebar.py tools/verify_v11.py docs/14_datebar_probe.md `
    version_design/design/refactor/baseline_v1.0.13.json version_design/design/refactor/datebar_probe.json `
    configs/kernel_v11/check/refactor_check.yaml configs/experiment_index.yaml `
    outputs/experiment_registry.json
git commit -m "feat(tools): add v1.1 verification and datebar probe with self-contained baseline"
git tag m0-ok
```

三步顺序不可换：先 `add` 白名单 → 再 `commit` → 最后打 tag（tag 必须指向含本次改动的提交）。

---

## 3. M1：samples 层

### 3.1 目标

在不触碰 step2–step6 的前提下，把「样本从哪来」建成独立可重建的一层，并让 `input.mode=samples` 在 step1 顶部**显式失败**而不是静默走旧路径。

### 3.2 具体动作

| 文件路径 | 动作 | 改什么（函数/类/键） | 设计依据 |
| :--- | :---: | :--- | :--- |
| `data_prep/__init__.py` | 新增 | 包声明；不 import `pipeline` | §2.2 / §6.2 |
| `data_prep/ids.py` | 新增 | `derive_sample_id` / `parse_sample_id` / `derive_datebar_fingerprint` / `derive_samples_id` / `canonical_json` | §3.4 |
| `data_prep/datebar.py` | 新增 | `load_datebar(path) -> (df, fingerprint)`、`build_symbol_arrays(df)`；`reset_index`、小写列、`idxd` int32、`mergesort` | §6.1 / §6.2-7 |
| `data_prep/universe.py` | 新增 | `filter_universe(df, params)`、`valid_positions(sym_arrays)` | §6.1 |
| `data_prep/samples/__init__.py` | 新增 | 包声明 | §2.2 |
| `data_prep/samples/base.py` | 新增 | `SampleDefinition`、`SampleContext`（`history` / `index_of`，**无前瞻方法**） | §4.1 |
| `data_prep/samples/registry.py` | 新增 | `register_sample` / `get_sample` | §4.10 |
| `data_prep/samples/events/__init__.py` | 新增 | 导入内置实现以触发注册 | §2.2 |
| `data_prep/samples/events/bollinger.py` | 新增 | 布林带突破 + 分年等距抽样 | §4.2 |
| `data_prep/samples/events/legacy_holding.py` | 新增 | 旧 holding 过滤后取 `(idxs, signal_date) → (idxs, idxd)` | §4.3 |
| `data_prep/build_samples.py` | 新增 | `build(config_dict) -> samples_id`；写 `samples.parquet` + `manifest.json` | §3.1 |
| `tools/build_samples.py` | 新增 | CLI：`--config`、`--dry-run`、`--json`；`--json` 输出单行 JSON 含 `samples_id` | §6.1 |
| `tests/conftest.py` | 新增 | 临时 raw / 夹具 | §7 M1 |
| `tests/test_data_prep/test_ids.py` | 新增 | 往返、`sh000001` vs `sz000001`、int64 不溢出 | §7 M1 |
| `tests/test_data_prep/test_samples_contract.py` | 新增 | 列契约、升序、前缀排除、确定性 | §7 M1 |
| `pytest.ini` | 新增 | `testpaths = tests`、`python_files = test_*.py` | §7 M1 |
| `pipeline/config.py` | 修改 | 新增 `InputParams` / `TargetParams` / `UniverseParams` / `SampleBuildParams` / `LabelBuildParams` / `DataPrepParams`；`PipelineParams` 加 `input` / `target` / `data_prep`；`from_dict` 的 `nested` 加三项；`version_payload` **保持**包含它们；`RawDataParams` 加 `prepared_dir` / `labels_dir`；`Paths` 加 `prepared_root_dir` / `labels_root_dir` | §5.1（1/2/4） |
| `pipeline/step1_clean.py` | 修改 | **仅**在 `compute_step1` 顶部加 samples 模式显式拒绝；`_clean_holding` / `_clean_datebar` 一字不改 | §5.1 注 / §6.3 |
| `configs/default.yaml` | 修改 | 追加 `input` / `target` / `data_prep` 三段（取值即默认值） | §5.2 |

**不做什么**：不碰 `step2`–`step6`；不写标签；不生成历史窗口列；`config.py` 只做纯增量；`step1` 只多那几行拒绝。

### 3.3 验收命令

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
$base = "version_design/design/refactor/baseline_v1.0.13.json"

# 前置：把 default.yaml 的三段追加到本地配置（临时、不入库）
#       并用 Copy-Item 生成 legacy 变体：method=legacy_holding、
#       max_samples=null、date_range_start/end=null（V10/V14 强制）
Copy-Item configs/local/refactor_check.yaml configs/local/refactor_check_legacy.yaml

# M1-1 生成 bollinger 样本集
$o1 = & $py tools/build_samples.py --config configs/local/refactor_check.yaml --json | ConvertFrom-Json
$sid_bol = $o1.samples_id
if ($LASTEXITCODE -ne 0) { throw "build_samples(bollinger) 失败" }

# M1-2 生成 legacy 样本集（样本集必须与旧路径完全相同）
$o2 = & $py tools/build_samples.py --config configs/local/refactor_check_legacy.yaml --json | ConvertFrom-Json
$sid_leg = $o2.samples_id
if ($LASTEXITCODE -ne 0) { throw "build_samples(legacy) 失败" }

# M1-3 dry-run 不落盘
& $py tools/build_samples.py --config configs/local/refactor_check.yaml --dry-run
if ($LASTEXITCODE -ne 0) { throw "dry-run 失败" }

# M1-4 确定性：同配置重跑，忽略 created_at 逐行逐值相等
$h1 = & $py -c "import hashlib,pandas as pd; d=pd.read_parquet('data/prepared/$sid_bol/samples.parquet').drop(columns=['created_at']); print(hashlib.sha256(d.to_csv(index=False).encode()).hexdigest())"
& $py tools/build_samples.py --config configs/local/refactor_check.yaml --json | Out-Null
$h2 = & $py -c "import hashlib,pandas as pd; d=pd.read_parquet('data/prepared/$sid_bol/samples.parquet').drop(columns=['created_at']); print(hashlib.sha256(d.to_csv(index=False).encode()).hexdigest())"
if ($h1 -ne $h2) { throw "samples 非确定性：$h1 != $h2" }

# M1-5 单测
& $py -m pytest tests/test_data_prep -q
if ($LASTEXITCODE -ne 0) { throw "单测失败" }

# M1-6 旧路径仍可跑（smoke step1）
& $py run_experiment_pipeline.py --mode smoke --step 1 `
    --config configs/local/refactor_check.yaml --state-file m1_old_step1.json
if ($LASTEXITCODE -ne 0) { throw "旧路径被破坏" }

# M1-7 samples 模式必须显式报错（M1 守卫；M4 才放开）
& $py run_experiment_pipeline.py --mode smoke --step 1 `
    --config configs/local/refactor_check_samples.yaml --state-file m1_samples_step1.json
if ($LASTEXITCODE -ne 1) { throw "M1 守卫未生效：期望 exit 1" }

# M1-8 里程碑 Gate
& $py tools/verify_v11.py --milestone M1 --baseline $base
if ($LASTEXITCODE -ne 0) { throw "M1 验收未通过" }
```

期望 exit code：M1-1/2/3/4/5/6/8 为 `0`；**M1-7 必须为 `1`**（且 stdout 含 `NotImplementedError` 与 `samples`）。

### 3.4 验收断言

| ID | 断言 | 阈值 / 容差 |
| :---: | :--- | :--- |
| A1-01 | `samples.parquet` 列集合 == §3.1 | 集合相等 |
| A1-02 | 列名 `^(target\|label)` 命中数为 0 | 0 |
| A1-03 | `sample_id` dtype == int64 | 精确 |
| A1-04 | `sample_id` 唯一 | `nunique == len` |
| A1-05 | `sample_id` 严格升序 | `diff() > 0` 全真 |
| A1-06 | `manifest.json` 含 §3.1 的 17 个键 | 集合包含 |
| A1-07 | `params_hash` 重算相等 | 精确（8 位） |
| A1-08 | `datebar_fingerprint` 重算相等 | 精确 |
| A1-09 | `samples_set_hash` 重算相等 | 精确（16 位） |
| A1-10 | `max_samples` 生效时 `rows == max_samples` | 精确；未生效时记录不适用 |
| A1-11 | `sampling_rate == rows / rows_before_cap` | `ABS_TOL_FLOAT` |
| A1-12 | 无 `exclude_prefixes` 前缀 | 计数 0（前缀取自 baseline） |
| A1-13 | 某个样本集的 `(idxs, idxd)` 集合指纹 == `baseline.legacy_holding_samples_set_hash` | 精确 |
| A1-14 | `sample_id` 与 `(idxs, idxd)` 双射（解码后逐行相等） | 精确 |

### 3.5 回滚点

```powershell
git add data_prep/ids.py data_prep/datebar.py data_prep/universe.py data_prep/__init__.py `
    data_prep/samples data_prep/build_samples.py tools/build_samples.py `
    tests/conftest.py tests/test_data_prep pytest.ini `
    pipeline/config.py pipeline/step1_clean.py configs/default.yaml
git commit -m "feat(data_prep): add samples layer with config fields and step1 guard"
git tag m1-ok
```

---

## 4. M2：labels 层与缓存

### 4.1 目标

把「学什么」做成可插拔的一层：标签按名称解析、按定义整组重建、按参数哈希缓存；新样本集可以贴任意内置标签。

### 4.2 具体动作

| 文件路径 | 动作 | 改什么（函数/类/键） | 设计依据 |
| :--- | :---: | :--- | :--- |
| `data_prep/labels/__init__.py` | 新增 | 包声明 | §2.2 |
| `data_prep/labels/base.py` | 新增 | `LabelDefinition`（`name_pattern` / `params_key` / `spec_version` / `default_params` / `horizon_of` / `compute`）、`LabelContext`（`entry_position` / `forward_window`，**无过去窗口方法**）、`validate_label_frame` | §4.5 / §3.6 |
| `data_prep/labels/registry.py` | 新增 | `register_label`、`resolve_label(name) -> (definition, name_params, horizon)`；双命中即报错；未命中列出全部 pattern | §4.5 / §6.1 |
| `data_prep/labels/future_return.py` | 新增 | `^target_future_(\d+)d$`；一次遍历算全部 horizon；`marks` 判建仓可行性 | §4.6 |
| `data_prep/labels/trend_quality.py` | 新增 | `^target_trend_quality_(\d+)d$`；`md <= 1e-6` 给 NaN | §4.7 |
| `data_prep/labels/holding_return.py` | 新增 | `^target_holding_return$`；`horizon_of` 恒 `None`；从 raw holding 对齐 | §4.8 |
| `data_prep/labels/cache.py` | 新增 | `is_valid` / `load` / `save` / `rebuild`；`_index.json` | §3.2 / §3.4 |
| `data_prep/labels/_template.py` | 新增 | 新增标签的复制模板（含契约注释与 `@register_label` 用法） | §4.10 |
| `data_prep/label_builder.py` | 新增 | `ensure(config_dict, samples_id, label) -> List[Path]`（定义级整组就绪）；内部 `_materialize(definition, params, ctx)` 供 CLI 复用 | §6.1 |
| `tools/build_labels.py` | 新增 | CLI：`--config`、`--dry-run`、`--json`；遍历 registry 的定义，对每个定义用其 `params_key` 取参数（缺省回落到 `default_params`），一次 `compute` 落盘全部返回名 | §6.1 / §4.5 |
| `tests/test_data_prep/test_labels_contract.py` | 新增 | 行契约、列契约、`is_complete` / `is_entry_blocked` 语义 | §3.2 |
| `tests/test_data_prep/test_cache_and_registry.py` | 新增 | 缓存命中/失效、`resolve_label` 三类异常 | §7 M2 |

**不做什么**：不碰 `pipeline/`；不缩尾、不截断、不分类；不做增量补算（§3.4 缓存策略）。

### 4.3 验收命令

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
$base = "version_design/design/refactor/baseline_v1.0.13.json"

# M2-1 构建全部内置标签（两套样本集各一份）
& $py tools/build_labels.py --config configs/local/refactor_check.yaml --json
if ($LASTEXITCODE -ne 0) { throw "build_labels(bollinger) 失败" }
& $py tools/build_labels.py --config configs/local/refactor_check_legacy.yaml --json
if ($LASTEXITCODE -ne 0) { throw "build_labels(legacy) 失败" }

# M2-2 缓存命中：二次调用不得重写任何标签文件
$before = Get-ChildItem data\labels -Recurse -File | Select-Object FullName,LastWriteTime
& $py tools/build_labels.py --config configs/local/refactor_check.yaml --json
$after = Get-ChildItem data\labels -Recurse -File | Select-Object FullName,LastWriteTime
if (Compare-Object $before $after -Property FullName,LastWriteTime) { throw "缓存未命中，出现重算" }

# M2-3 单测
& $py -m pytest tests/test_data_prep -q
if ($LASTEXITCODE -ne 0) { throw "单测失败" }

# M2-4 里程碑 Gate
& $py tools/verify_v11.py --milestone M2 --baseline $base
if ($LASTEXITCODE -ne 0) { throw "M2 验收未通过" }
```

期望 exit code：全部 `0`；M2-2 的 `Compare-Object` 必须无差异。

### 4.4 验收断言

| ID | 断言 | 阈值 / 容差 |
| :---: | :--- | :--- |
| A2-01 | `baseline.labels.builtin_names` 的每个标签文件都存在 | 精确 |
| A2-02 | 每个标签文件列集合 == §3.2 | 集合相等 |
| A2-03 | 每个标签 `rows == samples.parquet` 行数 | 精确 |
| A2-04 | `target` 无 ±inf | `np.isinf` 计数 0 |
| A2-05 | `_index.json` 每项 `params_hash` 重算相等 | 精确（8 位） |
| A2-06 | `coverage == non_null_rows / rows` | `ABS_TOL_FLOAT` |
| A2-07 | `target_holding_return` coverage == `baseline.labels.target_holding_return.coverage` | `ABS_TOL_FLOAT` |
| A2-08 | 目录内无孤儿文件、索引内无孤儿条目 | 双向一致 |
| A2-09 | `label_name` 列 == 文件名 | 精确 |
| A2-10 | `samples_id` / `datebar_fingerprint` 列 == manifest 对应值 | 精确 |

### 4.5 回滚点

```powershell
git add data_prep/labels data_prep/label_builder.py tools/build_labels.py `
    tests/test_data_prep
git commit -m "feat(data_prep): add labels layer with registry and cache"
git tag m2-ok
```

---

## 5. M3：接线准备（零行为变化）

### 5.1 目标

让配置校验、数据契约、就绪检查全部就位，但 `pipeline` 的行为仍然只有旧路径一种。

### 5.2 具体动作

| 文件路径 | 动作 | 改什么（函数/类/键） | 设计依据 |
| :--- | :---: | :--- | :--- |
| `pipeline/config.py` | 修改 | `_validate_params` 增加 V1–V14；**拦截仍在 step1 顶部**，不加到这里 | §5.3 |
| `pipeline/adapters/sample_family.py` | 修改 | 新增 `SAMPLES_REQUIRED_COLUMNS`、`LABEL_REQUIRED_COLUMNS`；`--show-contract` 一并打印 | §7 M3 |
| `pipeline/step2_slices.py` | 修改 | `invalid_reasons` 的 `missing_signal_date` 拆为 `unknown_symbol` 与 `date_not_in_symbol_range`（仅键名与文案） | §6.3 |
| `tools/check_raw_data.py` | 修改 | 按 `input.mode` 分支；新增 `prepared` 段（存在性、列契约、`sample_id` 唯一且 int64、`signal_date` 可 int 化、指纹与集合哈希匹配）；`idxs` 交集率 < 99% 判 invalid | §7 M3 |
| `docs/15_data_contract.md` | 新增 | samples / labels 契约、内置采样与标签清单（含 `name_pattern` 列）、扩展步骤 | §4.10 / §7 M3 |
| `tests/test_pipeline/test_config_v11.py` | 修改/新增 | V1–V14 逐条正反用例 | §5.3 |
| `tests/test_pipeline/test_step1_guard.py` | 修改/新增 | samples 模式在 step1 顶部被拒绝 | §7 M3③ |

### 5.3 验收命令

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
$base = "version_design/design/refactor/baseline_v1.0.13.json"

# M3-1 旧配置可加载、模式仍是旧路径
& $py -c "from pipeline.config import load_params; p=load_params('configs/local/refactor_check.yaml'); print(p.input.mode, p.target.source)"
if ($LASTEXITCODE -ne 0) { throw "旧配置加载失败" }

# M3-2 samples 配置可加载（校验通过；运行期才拒绝）
& $py -c "from pipeline.config import load_params; p=load_params('configs/local/refactor_check_samples.yaml'); print(p.input.mode, p.target.label, p.target.horizon_days)"
if ($LASTEXITCODE -ne 0) { throw "samples 配置被校验误拒" }

# M3-3 就绪检查（本机 raw 走绝对路径配置）
& $py tools/check_raw_data.py --config configs/local/refactor_check.yaml
if ($LASTEXITCODE -ne 0) { throw "旧配置就绪检查失败" }
& $py tools/check_raw_data.py --config configs/local/refactor_check_samples.yaml
if ($LASTEXITCODE -ne 0) { throw "samples 配置就绪检查失败" }

# M3-4 契约展示
& $py -m pipeline.adapters.sample_family --show-contract
if ($LASTEXITCODE -ne 0) { throw "契约展示失败" }

# M3-5 全量单测
& $py -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "单测失败" }

# M3-6 旧路径 smoke step1（行为未变）后跑 Gate
& $py run_experiment_pipeline.py --mode smoke --step 1 `
    --config configs/local/refactor_check.yaml --state-file m3_old_step1.json
$ver = & $py -c "from pipeline.config import load_params; print(load_params('configs/local/refactor_check.yaml').build_experiment_version())"
& $py tools/verify_v11.py --milestone M3 --baseline $base `
    --config configs/local/refactor_check.yaml `
    --state-file "outputs/$ver/metadata/m3_old_step1.json" --output-dir "outputs/$ver"
if ($LASTEXITCODE -ne 0) { throw "M3 验收未通过" }
```

期望 exit code：全部 `0`（M3-2 的加载必须成功，samples 模式要到运行时才被 step1 拒绝）。

### 5.4 验收断言

| ID | 断言 | 阈值 / 容差 |
| :---: | :--- | :--- |
| A3-01 | 相对开工前基线，`git diff --name-only HEAD`（已跟踪）+ `git status --porcelain`（未跟踪）只多出 M3 白名单路径（开工前已有的 `.gitignore` 改动视为基线，不算越界） | 越界即 fail |
| A3-02 | 旧配置 `input.mode == "holding_file"` | 精确 |
| A3-03 | samples 配置 `load_params` 成功 | 退出码 0 |
| A3-04 | 测试文件同时引用 `input.mode`/`samples` 与 `NotImplementedError`（代理断言：文本命中，不替代实际 pytest 执行；pytest 结果由结项报告记录） | 命中 ≥ 1 |
| A3-05 | `check_raw_data --config <本次配置>` 返回 0 | 精确 |
| A3-06 | `SAMPLES_REQUIRED_COLUMNS` / `LABEL_REQUIRED_COLUMNS` 存在且非空 | 集合非空 |
| A3-07 | step2 `invalid_reasons` 含 `unknown_symbol` / `date_not_in_symbol_range` / `paused_signal_date` / `insufficient_history` | 集合相等 |
| A3-08 | `docs/15_data_contract.md` 存在且含内置采样与标签清单两节 | 各存在 |
| A3-09 | `data_prep/**` 中无 `import pipeline` / `from pipeline` | 命中 0 |
| A3-10 | `tools/build_samples.py`、`tools/build_labels.py` 中无 `yaml.safe_load` | 命中 0 |

### 5.5 回滚点

```powershell
git add pipeline/config.py pipeline/adapters/sample_family.py pipeline/step2_slices.py `
    tools/check_raw_data.py docs/15_data_contract.md tests
git commit -m "feat(config): add v1.1 validation, contracts and readiness checks"
git tag m3-ok
```

---

## 6. M4：step1 双模式（唯一行为变化）

### 6.1 目标

让 `input.mode=samples` 真正跑通，同时用 L1/L2/L3 三层回归证明旧路径逐值未变；这是 v1.1 唯一允许改变行为的里程碑。

### 6.2 具体动作

| 文件路径 | 动作 | 改什么（函数/类/键） | 设计依据 |
| :--- | :---: | :--- | :--- |
| `pipeline/step1_clean.py` | 修改 | 删除 M1 顶部拒绝；拆 `_clean_holding` / `_clean_samples`；`compute_step1` 两模式同构返回 | §3.3 / §6.3 |
| `pipeline/runner.py` | 修改 | `_completed_step_matches_current_inputs` 扩展到 step1：比对 `input_mode` / `samples_id` / `label_hash` / `datebar_fingerprint`；旧 state 缺键 → 视为签名变化 → 重跑 | §6.3 / §7 M4 |
| `docs/07_step_acceptance_criteria.md` | 修改 | 补 Step 1 六个 check 在 samples 模式下的语义映射（名称与判定不变） | §7 M4 |
| `tests/test_pipeline/test_step1_dual_mode.py` | 新增 | 两模式同构、丢空标签、升序、缺标签报错、签名触发重跑 | §7 M4 |

**不做什么**：不改 step2–step6；不引入历史窗口列；不在 step5 选标签；不在 `_clean_samples` 里构建标签。

### 6.3 验收命令

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
$base = "version_design/design/refactor/baseline_v1.0.13.json"

$ver_old = & $py -c "from pipeline.config import load_params; print(load_params('configs/local/refactor_check.yaml').build_experiment_version())"
$ver_new = & $py -c "from pipeline.config import load_params; print(load_params('configs/local/refactor_check_samples.yaml').build_experiment_version())"

# M4-1 L1：旧路径 full step1
& $py run_experiment_pipeline.py --mode full --step 1 `
    --config configs/local/refactor_check.yaml --state-file m4_l1_full.json
if ($LASTEXITCODE -ne 0) { throw "L1 失败" }

# M4-2 L3：旧路径 smoke 全链（固定用 baseline.smoke.n 对应的 smoke_size）
1..6 | ForEach-Object {
    & $py run_experiment_pipeline.py --mode smoke --step $_ `
        --config configs/local/refactor_check.yaml --state-file m4_l3_smoke.json
    if ($LASTEXITCODE -ne 0) { throw "L3 step $_ 失败" }
}

# M4-3 新路径 smoke 全链（samples 模式）
1..6 | ForEach-Object {
    & $py run_experiment_pipeline.py --mode smoke --step $_ `
        --config configs/local/refactor_check_samples.yaml --state-file m4_new_smoke.json
    if ($LASTEXITCODE -ne 0) { throw "新路径 step $_ 失败" }
}

# M4-4a 守恒恒等式 — smoke 口径（用 M4-3 的 state；口径选择规则见 §9，两种口径不得混用）
& $py -c "import json; s=json.load(open(r'outputs/$ver_new/metadata/m4_new_smoke.json',encoding='utf-8')); d2=s['steps']['step_2']['details']; d4=s['steps']['step_4']['details']; lhs=d2['input_samples']-d2['dropped_samples']-d4['invalid_targets_dropped']; print('step2_input',d2['input_samples'],'lhs',lhs,'rhs',d4['samples']); assert lhs==d4['samples'], 'smoke 口径守恒恒等式不成立'"
if ($LASTEXITCODE -ne 0) { throw "smoke 口径守恒恒等式不成立" }

# M4-4b 守恒恒等式 — full 口径（**M4 不要求**；本分支供阶段 0 实验后复核使用）
$full_state = "outputs/$ver_new/metadata/m4_full_samples.json"
if (Test-Path $full_state) {
    & $py -c "import json,pandas as pd; s=json.load(open(r'$full_state',encoding='utf-8')); d1=s['steps']['step_1']['details']; d2=s['steps']['step_2']['details']; d4=s['steps']['step_4']['details']; rows=len(pd.read_parquet('data/prepared/'+d1['samples_id']+'/samples.parquet')); assert d2['input_samples']==rows-d1['dropped_no_target'], 'full 口径起算项不符：step2.input_samples != samples_rows - dropped_no_target'; lhs=d2['input_samples']-d2['dropped_samples']-d4['invalid_targets_dropped']; print('samples_rows',rows,'step2_input',d2['input_samples'],'lhs',lhs,'rhs',d4['samples']); assert lhs==d4['samples'], 'full 口径守恒恒等式不成立'"
    if ($LASTEXITCODE -ne 0) { throw "full 口径守恒恒等式不成立" }
} else {
    Write-Output "M4 只验 smoke 口径（口径 B）；full 口径（口径 A）由阶段 0 实验承担，见 experiment_plan_v11.md"
}

# M4-5 L2：v1.0.13 worktree 逐值对比（design §7 M4 强制；唯一允许离开仓库根的一步）
git worktree add ..\_wt_v1013 v1.0.13
Push-Location ..\_wt_v1013
1..3 | ForEach-Object {
    & $py run_experiment_pipeline.py --mode smoke --step $_ --smoke 20000 `
        --config <指向主仓库 raw 的绝对路径配置> --state-file m4_l2_old.json
    if ($LASTEXITCODE -ne 0) { throw "L2 旧分支 step $_ 失败" }
}
Pop-Location
# 主仓库同参数再跑一次（state-file 用 m4_l2_new.json），然后逐值比对：
#   比对清单取两边 state 的 output_files（切片、non_trading、特征文件），逐个执行
#   $py -c "import pandas as pd,sys; a=pd.read_parquet(sys.argv[1]); b=pd.read_parquet(sys.argv[2]); print(a.shape,b.shape); assert a.equals(b)" <主仓库文件> <worktree 文件>
#   期望：shape 相同且 assert 不抛异常（exit 0）

# M4-6 里程碑 Gate
& $py tools/verify_v11.py --milestone M4 --baseline $base `
    --config configs/local/refactor_check.yaml `
    --state-file "outputs/$ver_old/metadata/m4_l1_full.json" `
    --state-file "outputs/$ver_old/metadata/m4_l3_smoke.json" `
    --state-file "outputs/$ver_new/metadata/m4_new_smoke.json" `
    --output-dir "outputs/$ver_old" --output-dir "outputs/$ver_new"
if ($LASTEXITCODE -ne 0) { throw "M4 验收未通过" }
```

期望 exit code：全部 `0`。L2 的 worktree 必须用**指向主仓库的绝对路径配置**（或 junction），**不得**把 raw 数据复制进 worktree；所用路径写进验收记录。

M4 的守恒恒等式验收**只要求 smoke 口径**；`$full_state` 分支默认不存在，跳过它**不构成失败**，也不得据此在报告里声称「full 口径已验证」。

### 6.4 验收断言

| ID | 断言 | 阈值 / 容差 |
| :---: | :--- | :--- |
| A4-01 | L3 计数类字段与 `baseline.smoke.*` 逐值相等 | 精确 |
| A4-02 | L3 浮点类字段与 baseline 逐值相等 | 1e-12（相对） |
| A4-03 | `step1.sample_count` == baseline | 精确 |
| A4-04 | `step1.datebar_rows` == baseline | 精确 |
| A4-05 | `step1.target_stats` 8 项 == baseline | 1e-12 |
| A4-06 | samples 模式 6 个 `checks` 全 True，键名与 baseline 同名 | 精确 + 集合相等 |
| A4-07 | samples 模式 `details` 16 键齐全 | 集合包含 |
| A4-08 | `dropped_no_target > 0` | 严格大于 0 |
| A4-09 | 守恒恒等式成立（按 `state.mode` 选口径，规则见 §9；smoke 用口径 B、full 用口径 A） | 精确；full 口径不在 M4 范围内，需先过前置断言 |
| A4-10 | `details.input_mode` / `samples_id` / `label_hash` == 当前配置推导值 | 精确 |
| A4-11 | 两模式 `holding_clean.parquet` 文件名不变、必需列 `sample_id`/`idxs`/`signal_date`/`target` 齐全 | 集合包含 |
| A4-12 | 新路径 state 的 `step_1`…`step_6` 全 `completed` 且 `checks_passed` 全 True | 精确 |
| A4-13 | step2 `details` 含 `dropped_samples`，`invalid_reasons` 含四个计数键 | 集合相等 |
| A4-14 | 两模式 `holding_clean` 的 `idxs` 为 str、`signal_date` 为整型、`sample_id` 为 int64 | dtype 规则 |
| A4-15 | `processed/datebar.parquet` 恒 9 列（两模式） | 列集合相等 |
| A4-16 | `docs/14` 记录了 `data_prep/datebar.py` 与 `_clean_datebar` 的按值比对结论 | 命中 ≥ 1 |
| A4-17 | samples 模式 `holding_clean` 按 `sample_id` 升序 | `diff() > 0` 全真 |
| A4-18 | `data_prep/datebar.py` 与 `_clean_datebar` 的 9 列按值逐值相等 | 数值精确（dtype 允许 int32/int64 差异） |

### 6.5 回滚点

```powershell
git add pipeline/step1_clean.py pipeline/runner.py docs/07_step_acceptance_criteria.md `
    tests/test_pipeline/test_step1_dual_mode.py
git commit -m "feat(step1): support dual input modes and extend runner signature"
git tag m4-ok
```

---

## 7. M5：收尾与发布

### 7.1 目标

收敛文档口径、重写可跟踪台账、归档验收脚本，并打出 `v1.1.0`；内核零改动。

### 7.2 具体动作

| 文件路径 | 动作 | 改什么（函数/类/键） | 设计依据 |
| :--- | :--- | :--- | :--- |
| `docs/00_start_here.md` | 修改 | 新命令（builder CLI、双模式运行方式） | §7 M5 |
| `docs/01_pipeline_contract.md` | 修改 | step1 双模式、`samples_id` 与 state 规则 | §7 M5 |
| `docs/08_lineage_startup.md` | 修改 | 目录约定统一为 `version_design/design/<lineage>/` | §7 M5 |
| `docs/10_architecture.md` | 修改 | 新增前置层，修订「11 个主线文件」表述 | §7 M5 |
| `docs/11_baseline_fixture_guide.md` | 修改 | 基线位置与读取方式 | §7 M5 |
| `AGENTS.md` | 修改 | 目录职责 + 铁律补充 + §5 路径表述 | §7 M5 |
| `version_design/design/refactor/README.md` | 修改 | 记一行目录约定，避免两套路径并存 | §7 M5 |
| `outputs/experiment_registry.json` | 修改 | 只保留可复现（有全绿 state）的条目 | §7 M5 / §3.5 |
| `tools/verify_v11.py` | 删除（或归档到 `version_design/design/refactor/verify_v11.py`） | — | §8.1 稳定性约束 |

> 归档与删除两种做法都必须让 `git ls-files tools/verify_v11.py` 不再命中（A5-01）；
> 选择归档时，归档文件本身要在 M5-5 的 `git add` 里，否则 `git status` 会留下未跟踪文件。

### 7.3 验收命令

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
$base = "version_design/design/refactor/baseline_v1.0.13.json"

# M5-1 全量单测
& $py -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "单测失败" }

# M5-2 用入库配置跑一次 smoke 全链，产生可跟踪台账
# 注意 1：state 文件名**必须以 pipeline_state_ 开头**，M5-3 与 A5-02 用
#         glob("pipeline_state_*.json") 找台账依据；改名会导致 keep=[] 而 A5-02 失败。
# 注意 2：data/raw 为空时（本机默认）必须改用本地绝对路径配置；两个配置除 data.* 外
#         逐值相同，`build_experiment_version()` 不含 data.*，故 $ver_rel 不变。
$cfg_release = if (Test-Path "data/raw/holding_stats_all.parquet") {
    "configs/kernel_v11/check/refactor_check.yaml"
} else {
    "configs/local/refactor_check.yaml"
}
Write-Output "M5 使用的配置: $cfg_release"
$ver_rel = & $py -c "from pipeline.config import load_params; import sys; print(load_params(sys.argv[1]).build_experiment_version())" $cfg_release
1..6 | ForEach-Object {
    & $py run_experiment_pipeline.py --mode smoke --step $_ `
        --config $cfg_release --state-file pipeline_state_m5_release_smoke.json
    if ($LASTEXITCODE -ne 0) { throw "发布 smoke step $_ 失败" }
}

# M5-3 台账收敛：只留「metadata 下存在六步全绿 state」的条目
# 该 glob 只认 pipeline_state_*.json；历史遗留的其它 state 名（如 *_smoke.json）
# 对应的条目会被正常淘汰——这是预期行为，不是缺陷。
& $py -c 'import json, pathlib; p=pathlib.Path("outputs/experiment_registry.json"); E=json.loads(p.read_text(encoding="utf-8")); ok=lambda e: any((lambda s: len(s)==6 and all(v.get("status")=="completed" and v.get("checks_passed") is True for v in s.values()))(json.loads(x.read_text(encoding="utf-8")).get("steps",{})) for x in sorted((pathlib.Path("outputs")/e["experiment_version"]/"metadata").glob("pipeline_state_*.json"))); keep=[e for e in E if ok(e)]; p.write_text(json.dumps(keep,ensure_ascii=False,indent=2),encoding="utf-8"); print(len(keep))'
if ($LASTEXITCODE -ne 0) { throw "台账收敛失败" }

# M5-4 归档并做删除演练
# 归档目标目录已被 git 跟踪，因此归档后的新文件必须在 M5-5 一并 git add，
# 否则 git status 会出现白名单外的未跟踪文件，违反结项标准第 6 条。
Move-Item tools/verify_v11.py version_design/design/refactor/verify_v11.py
& $py -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "删除演练：pytest 失败" }
1..6 | ForEach-Object {
    & $py run_experiment_pipeline.py --mode smoke --step $_ `
        --config $cfg_release --state-file pipeline_state_m5_drill_smoke.json
    if ($LASTEXITCODE -ne 0) { throw "删除演练：smoke step $_ 失败" }
}

# M5-5 提交白名单并打发布 tag（必须先提交：A5-01/A5-06 的 Gate 以 HEAD 与 tag 为准）
git add docs/00_start_here.md docs/01_pipeline_contract.md docs/08_lineage_startup.md `
    docs/10_architecture.md docs/11_baseline_fixture_guide.md AGENTS.md `
    version_design/design/refactor/README.md outputs/experiment_registry.json `
    tools/verify_v11.py version_design/design/refactor/verify_v11.py
git commit -m "docs: close out v1.1 refactor and archive verify script"
if ($LASTEXITCODE -ne 0) { throw "M5 白名单提交失败" }
git tag -a v1.1.0 -m "v1.1.0: decouple samples from labels"

# M5-6 从归档路径跑 Gate
& $py version_design/design/refactor/verify_v11.py --milestone M5 --baseline $base `
    --config $cfg_release `
    --state-file "outputs/$ver_rel/metadata/pipeline_state_m5_release_smoke.json" --output-dir "outputs/$ver_rel"
if ($LASTEXITCODE -ne 0) { throw "M5 验收未通过" }
```

期望 exit code：全部 `0`。顺序固定：先归档 → 再演练 → 再收敛台账 → **提交白名单** → 再打 tag → 最后跑 Gate（A5-01 依赖已提交的删除，A5-06 依赖已打 tag，二者都必须在 Gate 之前完成）。

> `$cfg_release` 由 M5-2 开头按 `data/raw` 是否就绪自动选择：就绪用入库配置，否则用 `configs/local/refactor_check.yaml`（两者除 `data.*` 外逐值相同，`build_experiment_version()` 不含 `data.*`，故 `$ver_rel` 不变）。若走了本地配置，须在结项报告 §8 写明：registry 条目的 `source_config` 指向本地路径，属环境事实。

### 7.4 验收断言

| ID | 断言 | 阈值 / 容差 |
| :---: | :--- | :--- |
| A5-01 | `git ls-files --error-unmatch tools/verify_v11.py` 非 0 | 非 0 |
| A5-02 | registry 条目数 ≥ 1，且每条目的 `metadata/` 下存在 `pipeline_state_*.json` | 条目数 ≥ 1；文件存在 |
| A5-03 | registry 每个条目六步 `completed` 且 `checks_passed` 全 True | 精确 |
| A5-04 | `--state-file` 指定的发布 smoke 六步全绿 | 精确 |
| A5-05 | `pytest.ini` 存在且 `tests/**` 非空 | 精确 |
| A5-06 | `git cat-file -t v1.1.0` == `tag` | 精确 |
| A5-07 | `version_design/design/refactor/README.md` 含目录约定一行 | 命中 ≥ 1 |
| A5-08 | `AGENTS.md` 与 `docs/08` 不再出现两套目录写法并存（只保留 `version_design/design/<lineage>/`） | 命中 0（旧写法） |

### 7.5 回滚点

M5 的提交在 §7.3 M5-5 完成（与 `v1.1.0` tag 同一个提交）。此处三步的语义是「确认已提交 → 打回滚 tag」：

```powershell
git add docs/00_start_here.md docs/01_pipeline_contract.md docs/08_lineage_startup.md `
    docs/10_architecture.md docs/11_baseline_fixture_guide.md AGENTS.md `
    version_design/design/refactor/README.md outputs/experiment_registry.json `
    tools/verify_v11.py version_design/design/refactor/verify_v11.py
    # 与 §7.3 M5-5 同一组路径；已提交时为空操作
git commit -m "docs: close out v1.1 refactor and archive verify script"
git tag m5-ok
```

若上一条 `git commit` 返回「nothing to commit」，说明 M5-5 已完成提交，属正常；此时只需继续 `git tag m5-ok`，**不要**为了「有东西可提交」而改动任何文件。

最后一个路径 `tools/verify_v11.py` 是**已删除文件**：`git add <已删除路径>` 用于暂存删除，`git ls-files` 随后才不再返回它（A5-01 依赖这一点）；仍然不得用 `git add -A` 代替。

---

## 8. 不变量对照表（§6.2 十条 → 里程碑与断言）

| # | 不变量（§6.2） | 守住它的里程碑 | 断言 ID | 补充手段 |
| :---: | :--- | :---: | :--- | :--- |
| 1 | `idxs` 全链 str；`signal_date` / `idxd` 全链 int | M1 / M2 / M4 | A1-01、A1-14、A2-02、A4-14 | `docs/15` 写明列类型 |
| 2 | `sample_id` 单调、与 `(idxs, idxd)` 双射；禁止 `reset_index(drop=True)` 重建 | M1 / M4 | A1-04、A1-05、A1-14、A4-17 | 全仓 grep `reset_index(drop=True)`：只允许用于非 `sample_id` 的索引重建（datebar 排序、切片内部），不得作用于 `holding_clean` / `samples` |
| 3 | `data_prep` 不 import `pipeline`；`pipeline` 只函数内惰性 import `data_prep` | M3 | A3-09 | 静态扫描 `from pipeline` / `import pipeline` |
| 4 | `holding_clean.parquet` 文件名与必需列不变 | M4 | A4-11 | 两模式共用同一 `paths.processed_dir` |
| 5 | 两模式 `checks` 键集合与布尔语义一致；`details` 缺省值类型一致（`""` / `0`，不用 `null`） | M4 | A4-06、A4-07 | `docs/07` 语义映射表 |
| 6 | `processed/datebar.parquet` 恒 9 列 | M4 | A4-15 | 两模式都走 `_clean_datebar` |
| 7 | `data_prep/datebar.py` 与 `_clean_datebar` 按值逐值相同 | M4 | A4-16、A4-18 | 一次性比对脚本，结论写 `docs/14` |
| 8 | `samples.parquet` 与 samples 模式 `holding_clean` 按 `sample_id` 升序 | M1 / M2 / M4 | A1-05、A2-03、A4-17 | 标签文件与 samples 行序一一对应 |
| 9 | CLI 先 `load_params` 再以 dict 传入 `data_prep`；`data_prep` 不解析 YAML | M3 | A3-10 | `tools/build_*.py` 静态扫描 |
| 10 | 任何重复计算（dateBar 两次加载）显式写出 | M4 | A4-10 | 代码评审 + `details.datebar_fingerprint` 单一来源 |

---

## 9. 守恒恒等式

四项等式（§4.9）的**概念形式**（full 口径；可直接执行的两种写法见下方「起算口径」），M4 与任何使用新路径的实验必查：

```text
samples_rows
  − dropped_no_target(step1)
  − dropped_samples(step2)            # unknown_symbol / date_not_in_symbol_range / paused_signal_date / insufficient_history 合计
  − invalid_targets_dropped(step4)
  == step4 输出特征表行数
```

| 项 | 数据来源 | 读取方式 |
| :--- | :--- | :--- |
| `samples_rows` | `data/prepared/{samples_id}/samples.parquet` 行数 | `samples_id` 取 `state.steps.step_1.details.samples_id` |
| `dropped_no_target` | `state.steps.step_1.details.dropped_no_target` | state JSON |
| `dropped_samples` | `state.steps.step_2.details.dropped_samples` | state JSON |
| `invalid_targets_dropped` | `state.steps.step_4.details.invalid_targets_dropped` | state JSON |
| 等号右边 | `state.steps.step_4.details.samples`（唯一 `sample_id` 数） | 与 `details.output_feature_file` 指向的 parquet 行数交叉核对 |

**起算口径（必须显式写进报告）**：

`step2.details.input_samples` 已经是「step1 之后剩余的全部样本」再经 step2 冒烟抽样后的行数（step1 在冒烟模式不抽样，`--smoke N` 只影响 step2）。因此两种口径的起算项**不同**：

```text
# 口径 A：full（step2 收到 step1 的全部输出）
前置断言：d2['input_samples'] == samples_rows - d1['dropped_no_target']
lhs      = d2['input_samples'] - d2['dropped_samples'] - d4['invalid_targets_dropped']
断言      lhs == d4['samples']

# 口径 B：smoke（step2 先从 d2['input_samples'] 抽 smoke_size 条）
lhs      = d2['input_samples'] - d2['dropped_samples'] - d4['invalid_targets_dropped']
断言      lhs == d4['samples']
```

**两种口径不得混用起算项**：口径 A 里 `dropped_no_target` 只出现在前置断言中（用来校验 step2 收到的是全集），**不得**再减一次；口径 B 里 `input_samples` 已经不含空标签样本，**不得**再减 `dropped_no_target`（否则双重扣减，M4 必然失败）。口径 B 只证明代码路径（§7 M4 约束 3），结论以口径 A 为准。

**口径选择规则（确定性）**：`verify_v11` 逐个读取 `--state-file` 指向的 state 顶层 `mode` 字段选择口径——`mode == "full"` 用口径 A（含前置断言 `d2['input_samples'] == samples_rows - d1['dropped_no_target']`）；`mode == "smoke"` 用口径 B。字段缺失时**不得默认**，判 fail 并提示该 state 不完整。同一份报告里出现两种口径时，必须分别标注来源 state 文件名。

M4 的 Gate 只要求 smoke 口径（口径 B）；口径 A 由阶段 0 实验承担（`experiment_plan_v11.md`），不在 M4 范围内。

另有一条独立禁令：**不得**用被覆写后的 `processed/holding_clean.parquet` 当样本全集（step2 full 会覆写它，§4.9）。

不平即说明有 NaN 标签被静默填成 0（或样本被误丢），结论作废，**停下**（设计依据：`experiment_design.md` §9.2 风险表）。

---

## 10. 两项补充决策（设计已定，此处显式写成规则）

### 10.1 采样侧 `params_key` 是退化的

采样参数就是 `data_prep.samples.params` 这**一个扁平 dict**（如 `{ma_period: 20, std_mult: 2.0}`）。

| 规则 | 说明 |
| :--- | :--- |
| 取值方式 | `params = config["data_prep"]["samples"]["params"]`，直接传给 `SampleDefinition.generate` |
| 禁止 | **不得**实现成 `params["params"]`，也不得按 `params_key` 再做一层索引 |
| 字段保留 | `SampleDefinition.params_key` 可以保留（默认 `"params"`），但**采样侧忽略它**；它只在未来多采样注册共存的场景有意义 |
| 校验 | `_validate_params` 的 V7 只校验「`method` ∈ registry、`params` 值均为正数」，不校验 `params_key` |

### 10.2 配置缺 `params` 键时用 `default_params` 补齐

| 情形 | 行为 |
| :--- | :--- |
| `data_prep.labels.params` 中没有该定义的 `params_key` | 用 `definition.default_params` 补齐，**不报错** |
| 配置里出现**未知键**（不属于任何已注册定义的 `params_key`） | 报错，并列出全部可用键 |
| 采样侧 | 同理：`data_prep.samples.params` 缺项时用 `SampleDefinition.default_params` 补齐 |

为什么必须这样：`target_holding_return` 是单例、无参数、无前视（§4.8），配置里通常不会为它写 `holding_return:` 段。若把「缺键」当错误，选择该标签（0D 必用）会直接 `KeyError`，`on_missing_label=error` 也就永远走不到。

---

## 11. 结项标准

只有以下六条**同时**成立，v1.1.0 才算完成（这是唯一的完成定义；不设「部分完成」）：

1. **每个里程碑的 Gate 都留下 `exit 0` 记录**：M0–M5 各自的 `verify_report.json` 存在，且 `exit_code == 0`（M5 用归档路径运行）。
2. **旧路径逐值未变**：L1（full step1）的 `sample_count` / `datebar_rows` / `target_stats`，L2（worktree 逐值）的切片与特征，L3（smoke 全链）的全部字段，均与 `baseline_v1.0.13.json` 一致（计数精确、浮点 1e-12）。
3. **新路径可用**：`input.mode=samples` 的 smoke 能跑到 step6，六个 `checks` 全 True，`dropped_no_target > 0`，且守恒恒等式成立（含 step2 `dropped_samples`）。
4. **就绪检查语义正确**：`check_raw_data` 对旧路径返回 0，缺 samples 返回 2，指纹/哈希不符返回 3。
5. **可删除性成立**：`tools/verify_v11.py` 归档（或删除）后，`pytest tests -q` 与一次 smoke step1–6 仍全部通过。
6. **发布形态正确**：`outputs/experiment_registry.json` 只含可复现条目；`v1.1.0` 为 annotated tag；`git status --porcelain` 中没有白名单之外的改动。

阶段 0 对照实验**不属于**结项标准（§7 M5）。