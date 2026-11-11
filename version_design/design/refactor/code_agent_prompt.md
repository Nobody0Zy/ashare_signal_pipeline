# v1.1 重构 — 工程代理执行提示词

> 来源: [experiment_design.md](./experiment_design.md)（内容权威）+ [implementation_plan.md](./implementation_plan.md)（施工顺序）
> 回归锚点: [baseline_v1.0.13.json](./baseline_v1.0.13.json)
> 目标: 在 `ashare_signal_pipeline` 内核内落地 v1.1（`data_prep` 前置层 + step1 双模式），只改白名单文件，不提前做 v1.2。
> 定位: 本文件是交给独立实施 agent 的**唯一执行规范**；冲突时按 §0 仲裁。

---

## 0. 文档优先级

1. **本文件**（执行边界、白名单、禁止项、止损规则）
2. `experiment_design.md`（规格正文；本文件未覆盖处一律以它为准）
3. `implementation_plan.md`（文件级施工顺序与验收命令）

`AGENTS.md` 与 `docs/01_pipeline_contract.md` 是**不可被覆盖的硬约束**（只读数据、禁止破坏性清理、`reset_index(drop=True)` 禁令、独立 state-file、串行执行）。当本文件与它们冲突时，以它们为准并立即停止报告——那说明本文件写错了。

---

## 1. 执行边界

### 1.1 工作目录与解释器

| 项 | 值 |
| :--- | :--- |
| 工作目录 | 本仓库根目录（`ashare_signal_pipeline_kernel_v1_1`，即 git 仓库根；下文所有命令都在这里执行） |
| 解释器 | `C:/ProgramData/anaconda3/envs/factor311/python.exe`（下文统一记为 `$py`） |
| 唯一例外 | M4 的 L2 回归需要 `git worktree` 检出 `v1.0.13`，只有那一步允许 `Push-Location`/`Pop-Location` 进入 worktree，并在命令结束后回到仓库根 |

```powershell
$py = "C:/ProgramData/anaconda3/envs/factor311/python.exe"
$base = "version_design/design/refactor/baseline_v1.0.13.json"
```

### 1.2 目标文件已有未提交改动 → 先停下报告

开工前先看：

```powershell
git status --porcelain
git diff --stat
```

已知的**预期**状态（不算冲突）：`.gitignore` 有未提交修改、`version_design/` 与 `outputs/` 未被跟踪。除此之外，若**白名单内**的任何文件存在未提交改动，**先停止并报告**（列文件名 + 改动摘要），不要覆盖、不要 `git checkout`、不要 stash。

### 1.3 允许新增文件的范围

只允许新增：

1. `implementation_plan.md` 每个里程碑「文件白名单」列出的路径；
2. `configs/local/refactor_check_*.yaml` 临时验证配置（`configs/local/**` 已被 `.gitignore` 排除，不入库）；
3. 运行期产物（`data/prepared/**`、`data/labels/**`、`outputs/**`、`models/**`），由命令生成，**不得手工编辑**；
4. 阻塞时的 `version_design/design/refactor/BLOCKED.md`（只此一个）。

不得新增：笔记、草稿、汇总 md、临时 Python 脚本（一次性的比对/探测逻辑必须写进 `$py -c` 或 `tools/` 白名单脚本）。

### 1.4 数据与产物保护

| 目录 | 规则 |
| :--- | :--- |
| `data/raw/**` | **只读**，任何情况下不得修改、移动、删除 |
| `data/processed` / `data/slices` / `data/features` / `outputs` / `models` | 不得通过删除目录「重置环境」；用新 `state-file`、新配置或新 `experiment_version` 隔离 |
| 共享中间目录（`data/processed/`、`data/slices/`、`data/features/`） | 同一时刻只允许一个验证任务使用；新路径与旧路径的验证必须**串行**、各用独立 `state-file` |
| `version_design/demo/**` | 只读参考，不得修改或删除 |

---

## 2. 自动执行流程

### 2.1 四个阶段

```text
阶段 A 审计     git 状态 + 解释器可用性 + 当前里程碑目标文件现状 → 输出审计报告
阶段 B 编码     只改该里程碑白名单内的文件
阶段 C 验证     按 implementation_plan 的验收命令逐条执行，最后跑 verify_v11
阶段 D 报告     打 tag，输出结项报告（本文件 §12）
```

### 2.2 默认不等待用户确认

审计完成后：**若无 §2.3 的阻塞条件，直接开始编码，不要停下来问「是否继续」**；每个里程碑验收通过后直接进入下一个里程碑，不要逐步请示。

只有下面两种情况需要停下来等人：

1. 命中 §2.3 的阻塞条件（停止并报告）；
2. 同一里程碑连续失败三次（写 `BLOCKED.md` 并交回人工）。

### 2.3 阻塞条件（逐条枚举，命中即停）

| # | 阻塞条件 | 判据 | 动作 |
| :---: | :--- | :--- | :--- |
| B1 | 解释器不可用 | `$py -c "import sys; print(sys.version)"` 退出码非 0 | 停止，报告版本探测结果 |
| B2 | 目标文件结构与描述不符 | 白名单文件里找不到 §7 引用的函数/类/键（例如 `compute_step1` 不存在） | 停止，报告「期望位置 / 实际内容」 |
| B3 | 白名单内文件有未提交改动 | `git status --porcelain` 命中白名单路径 | 停止，不覆盖 |
| B4 | 既有错误无法与本任务切开 | 白名单文件本身语法/导入已坏，或 `pytest tests` 在**未改动前**就失败 | 停止，报告失败用例 |
| B5 | `verify_v11` 连续失败三次 | 同一里程碑第 3 次 Gate 失败 | 停止，写 `BLOCKED.md` |
| B6 | 需要改动设计才能继续 | 发现规格缺失/自相矛盾/无法实现 | 停止，写 `BLOCKED.md`，**不得**自行放宽 |

`BLOCKED.md` 只写四项：现象（含命令与回显）、涉及章节（§ 编号）、为什么无法在本计划内解决、建议的两种修法。

### 2.4 审计报告（阶段 A 必须输出）

1. 当前分支、`git status --porcelain` 摘要；
2. 解释器版本与关键包版本；
3. 本里程碑要改的白名单文件现状（存在/不存在、关键函数是否仍在原位）；
4. 明确列出「本里程碑不碰的文件」；
5. 是否命中阻塞条件（命中则停止）。

---

## 3. 逐里程碑循环

### 3.1 单步循环（M0 → M5，不可跳）

```text
1. 读 implementation_plan 的第 k 节 + experiment_design 该里程碑的阅读范围（见其开头「阅读方式」表）
2. 记录基线：git status --porcelain + git diff --stat（A3-01 的比对基线）
3. 只改白名单内的文件
4. 对照白名单：git diff --stat（相对第 2 步基线应只多出白名单路径）
5. 按 plan 的验收命令逐条执行（小 → 大，见 §9）
6. 跑 $py tools/verify_v11.py --milestone M<k> --baseline $base ...
7. exit 0 → git add <本里程碑白名单路径>（逐条列出，不用 -A / -a）
8. git commit -m "<type>: <subject>"
9. git tag m<k>-ok → 进入 k+1；exit 非 0 → 见 §4 止损
```

第 7–9 步不可省：`verify_v11` 的 A3-01（`git diff --name-only HEAD`）与 A5-01（`git ls-files`）都以 HEAD 为基准，不提交时前者会把上个里程碑的文件算成本次越界、后者会认为已删除的脚本仍在库里。顺序固定为 **提交 → 打 tag**，不得先打 tag 再提交。

M0 例外：`verify_v11.py` 由 M0 自己创建，因此 M0 的 Gate 是「先写脚本，再用它自证 M0」。

### 3.2 每个里程碑只读对应章节

严格按 `experiment_design.md` 开头「阅读方式」表的行号范围读，**不要通读全文**。跨章节只有两处始终适用：

- §6.2 不变量（违反即 bug，不论哪个里程碑）
- §7.2 执行规则 / §7.3 止损规则

### 3.3 不要预读下一个里程碑的实现

读完当前里程碑范围内的章节即开工；下一里程碑的章节在进入它时再读。提前实现后续里程碑内容 = 越界。

---

## 4. 文件白名单纪律与止损

### 4.1 白名单纪律

| 时点 | 命令 | 判定 |
| :--- | :--- | :--- |
| 开工前 | `git diff --stat` | 记录基线；只允许 `.gitignore` 与未跟踪目录 |
| 编码后 | `git diff --stat` | 新增条目必须全部落在本里程碑白名单内 |
| 验证前 | `git status --porcelain` | 多出的路径只能是运行期产物（`data/**`、`outputs/**`、`models/**`）与 `configs/local/**` |

越界即停：不要「顺手」重构、格式化、改名。

### 4.2 止损规则

| 失败次数 | 动作 |
| :---: | :--- |
| 第 1 次 | 定位并修复；**不得**修改 check、**不得**降低断言、**不得**改 baseline 数字 |
| 第 2 次 | `git reset --hard m<k-1>-ok`，回到上一个绿点，重读该里程碑章节后再做 |
| 第 3 次 | 停止实施；写 `BLOCKED.md`（现象、已尝试方案、`verify_report.json` 全文），交回人工裁决 |

### 4.3 明确禁止的「修复」方式

出现任一即视为实施失败：

1. 删除、重命名或弱化任何既有 check；
2. 把 `baseline_v1.0.13.json` 里的数字改成实际跑出来的值；
3. 在 `step2`–`step6` 里加 `if input_mode == "samples"` 之类的分支；
4. 为了让新路径跑通而放宽 `_validate_params`（校验是防泄漏的一部分）；
5. 跳过某个里程碑直接做后面的；
6. 删除 `data/` 或 `outputs/` 目录「重置环境」；
7. 用 `--smoke` 值的变化去「凑」`experiment_version`（回归必须同参数）。

---

## 5. 必做

1. 每个里程碑开工前输出 §2.4 的审计报告；结束后输出 §12 的结项片段。
2. 每个里程碑都以 `tools/verify_v11.py` 的 exit code 判定（M0 先创建它）。
3. 每个里程碑验收通过后打 `m<k>-ok` tag。
4. 所有数字来自命令回显；基线数字只从 `baseline_v1.0.13.json` 读。
5. 两套路径使用**独立** `state-file`，且**串行**执行（共享 `data/processed/` 等中间目录）。
6. 改动 `data_prep` 时保持「只接受路径 / primitive / dict」的公共接口约定。
7. 交付时输出 `git status --porcelain`，证明没有白名单之外的改动。

## 6. 禁做

1. 不改 `step2`–`step6` 的逻辑（`step2` 只允许改 `invalid_reasons` 的计数键名）。
2. 不在 `step1` 里隐式构建标签；缺标签只报错，提示先跑 `tools/build_labels.py`。
3. 不把 `SampleContext` 与 `LabelContext` 合并或复用；不向采样侧暴露任何前瞻方法。
4. 不用 `reset_index(drop=True)` 重建 `sample_id`。
5. 不改 `holding_clean.parquet` 的文件名与必需列。
6. 不让 `data_prep` import `pipeline`；`pipeline` 只在函数内惰性 import `data_prep`。
7. 不修改 `data/raw/**`、不修改 `version_design/demo/**`、不修改 `experiment_design.md` / `design_memory.md` / `README.md` / `baseline_v1.0.13.json` / `experiment_plan_v11.md` 的**设计内容**（baseline 只由 M0 按 §8.3 补齐字段）。
8. 不发明 `run_experiment_pipeline.py` 的参数（见 §10）。
9. 不把 `tools/verify_v11.py` 接进 `pipeline/**` 或 `data_prep/**`。
10. 不在代码里出现标签名/采样名的字符串字面量，也不按名称字符串做分支。

---

## 7. 逐里程碑精确改动清单（现有形态 → 目标形态 → 最小验收命令）

| 里程碑 | 只看并只改这些小节 | 说明 |
| :---: | :--- | :--- |
| M0 | 7.9 | 只新增 `tools/verify_v11.py` 等脚本与文档，不动内核 |
| M1 | 7.1（**只做 1–5 项**）、7.2（只加守卫）、7.4 | 纯增量：`data_prep/` + config 字段 + step1 顶部拒绝 |
| M2 | 7.5 | 纯新增 `data_prep/labels/**` 与 `label_builder` |
| M3 | 7.1（第 6 项 V1–V14）、7.6、7.7、7.8 | 零行为变化：校验 / 契约 / 就绪检查 |
| M4 | 7.2（删除守卫 + 双模式）、7.3 | 唯一行为变化 |
| M5 | 无代码小节 | 只改文档、registry，归档 `verify_v11.py` |

未列出的文件一律不碰；每个小节的三段式都要按顺序读完再动。

### 7.1 `pipeline/config.py`（M1 增量 / M3 校验）

**现有形态**（`PipelineParams`，`pipeline/config.py:398`）：

```python
@dataclass
class PipelineParams:
    seed: int = 42
    smoke_size: int = 5000
    data: RawDataParams = field(default_factory=RawDataParams)
    window: WindowParams = field(default_factory=WindowParams)
    ...
    experiment: ExperimentParams = field(default_factory=ExperimentParams)
```

`from_dict` 的嵌套白名单（`pipeline/config.py:416`）：

```python
        nested: Dict[str, Type[object]] = {
            "data": RawDataParams,
            "window": WindowParams,
            ...
            "experiment": ExperimentParams,
        }
```

`version_payload`（`pipeline/config.py:472`）当前只排除 `experiment` 与 `data`。

**目标形态**：

1. 新增 6 个 dataclass（`InputParams` / `TargetParams` / `UniverseParams` / `SampleBuildParams` / `LabelBuildParams` / `DataPrepParams`），字段与默认值**逐字**照 `experiment_design.md` §5.1 的代码块，不要自创字段名。
2. `PipelineParams` 增加三个字段：`input: InputParams`、`target: TargetParams`、`data_prep: DataPrepParams`。
3. `nested` 白名单加 `"input"` / `"target"` / `"data_prep"`。
4. `version_payload()` **保持默认包含**这三段（不要像 `data` 那样排除）。
5. `RawDataParams` 加 `prepared_dir: str = "data/prepared"`、`labels_dir: str = "data/labels"`；`Paths` 加 `prepared_root_dir` / `labels_root_dir`，由 `build_paths` 解析；`ensure_directories` **只创建根目录**，不创建 `samples_id` 子目录。
6. M3 才加 `_validate_params` 的 V1–V14；**M1 不加**任何新校验，也不把 samples 模式的拦截放这里。

**最小验收命令**：

```powershell
& $py -c "from pipeline.config import load_params; p=load_params('configs/default.yaml'); print(p.input.mode, p.target.source, p.data_prep.samples.method, p.data_prep.universe.min_history_days)"
```

期望：exit 0，输出 `holding_file holding_return bollinger_breakout 60`。

---

### 7.2 `pipeline/step1_clean.py`（M1 守卫 / M4 双模式）

**现有形态**（`pipeline/step1_clean.py:49`）：

```python
def compute_step1(paths: Paths, mode: str, params: PipelineParams) -> Dict[str, object]:
    del mode
    holding = _clean_holding(paths, params)
    holding_path = paths.processed_dir / "holding_clean.parquet"
    holding.to_parquet(holding_path, index=False)
    ...
```

**目标形态（M1，只加这几行）**：

```python
def compute_step1(paths: Paths, mode: str, params: PipelineParams) -> Dict[str, object]:
    del mode
    if getattr(params, "input", None) is not None and params.input.mode == "samples":
        raise NotImplementedError(
            "input.mode=samples 尚未实现（计划在 M4 放开）；当前只支持 holding_file 模式"
        )
    holding = _clean_holding(paths, params)
    ...
```

`_clean_holding` / `_clean_datebar` 在 M1 **一个字都不改**。拦截必须放在 `compute_step1` 顶部，**不得**放进 `_validate_params`（否则 builder CLI 用同一份 YAML 会被挡住）。

**目标形态（M4）**：删除上面的守卫，拆成 `_clean_holding` / `_clean_samples`，`compute_step1` 按 `params.input.mode` 分派，两条路径返回**同构**的 `output_files` / `checks` / `details`（§3.3）。`_clean_samples` 的顺序固定：

```text
resolve_label(target.label) → 推导 samples_id（与 build_samples 同一函数）
→ 校验 manifest 的 datebar_fingerprint / samples_set_hash
→ 读取标签文件（缺失即报错，不构建）
→ join（键 sample_id）→ 丢 target 为空的行（计 dropped_no_target）
→ 按 sample_id 升序落盘 holding_clean.parquet
```

**最小验收命令**：

```powershell
# M1：必须显式失败
& $py run_experiment_pipeline.py --mode smoke --step 1 --config configs/local/refactor_check_samples.yaml --state-file m1_guard.json
# 期望 exit 1，stdout 含 NotImplementedError 与 samples
```

```powershell
# M4：必须通过且六个 check 全 True
& $py run_experiment_pipeline.py --mode smoke --step 1 --config configs/local/refactor_check_samples.yaml --state-file m4_new_smoke.json
# 期望 exit 0，stdout 六个 check 均为「通过」
```

---

### 7.3 `pipeline/runner.py`（M4）

**现有形态**（`pipeline/runner.py:43`）：

```python
def _completed_step_matches_current_inputs(
    paths: Paths,
    params: PipelineParams,
    step: int,
    current: Dict[str, object],
) -> bool:
    if step != 4:
        return True
    details = current.get("details", {}) if isinstance(current, dict) else {}
    ...
```

**目标形态**：`step == 1` 时比对 `details.input_mode` / `samples_id` / `label_hash` / `datebar_fingerprint` 与当前配置推导值；旧 state 缺这些键 → 返回 `False`（视为签名变化 → 重跑），**不得**当成「已完成」跳过。`step == 4` 的既有逻辑保持不动。

**最小验收命令**：

```powershell
& $py -c "import json; s=json.load(open(r'outputs/<ver_new>/metadata/m4_new_smoke.json',encoding='utf-8')); print(sorted(s['steps']['step_1']['details']))"
# 期望包含 input_mode / samples_id / label_hash / datebar_fingerprint 四个键
```

---

### 7.4 `data_prep/samples/**`（M1，纯新增）

**现有形态**：不存在（`data_prep/` 目录不存在）。

**目标形态**：

```python
@dataclass
class SampleDefinition:
    name: str
    params_key: str = "params"      # 保留字段；采样侧忽略它（见下）
    default_params: dict = field(default_factory=dict)

    def generate(self, bars, params: dict, ctx: "SampleContext") -> pd.DataFrame:
        """返回 (idxs, idxd) 两列；不得返回标签，不得返回历史窗口。"""
```

```python
class SampleContext:
    def history(self, symbol: str, pos: int, n: int): ...      # pos 及之前 n 个非停牌 bar
    def index_of(self, symbol: str, idxd: int) -> int: ...     # 不存在返回 -1
    # 不得提供任何前瞻方法，不得暴露原始 bars 数组
```

**采样侧 `params_key` 是退化的（必须按此实现）**：采样参数就是 `data_prep.samples.params` 这**一个扁平 dict**，直接传给 `generate`。

```python
# 正确
params = config["data_prep"]["samples"]["params"]

# 错误（会 KeyError）
params = config["data_prep"]["samples"]["params"]["params"]
```

`SampleDefinition.params_key` 可以保留（默认 `"params"`），但**采样侧忽略它**；`_validate_params` 的 V7 也不校验它。

配置缺项时用 `definition.default_params` 补齐，只有**未知键**才报错（与 §7.5 标签侧同一规则）。

**最小验收命令**：

```powershell
& $py tools/build_samples.py --config configs/local/refactor_check.yaml --json
# 期望 exit 0，单行 JSON 含 samples_id / rows / params_hash / datebar_fingerprint
```

---

### 7.5 `data_prep/labels/registry.py` + `label_builder.py`（M2，纯新增）

**现有形态**：不存在。

**目标形态**：

```python
def resolve_label(name: str) -> Tuple[LabelDefinition, dict, Optional[int]]:
    """名称 → (definition, name_params, horizon)。唯一入口。"""
```

规则：

1. 名称只经 `name_pattern` 匹配，**不得**按字符串分支；
2. 未命中 → 报错并列出**全部**可用 `name_pattern`；
3. 两个定义同时命中 → 注册冲突，加载期报错；
4. `horizon` 语义见 §4.6 / §4.7 / §4.8（`target_holding_return` 恒 `None`）。

```python
def ensure(config_dict: dict, samples_id: str, label: str) -> List[Path]:
    """该定义的『全部』产出就绪；缺任一则整组重建；返回本次写出的文件。"""
```

**配置缺 `params` 键时用 `default_params` 补齐（必须按此实现）**：

```python
group = config_dict["data_prep"]["labels"]["params"]
params = group.get(definition.params_key, definition.default_params)   # 缺键 → 默认值
unknown = set(group) - {d.params_key for d in registry.all()}
if unknown:
    raise ValueError(f"未知的标签参数键: {sorted(unknown)}；可用: {sorted(...)}")
```

若把「缺键」当错误，选择 `target_holding_return`（0D 必用、无参数）会直接 `KeyError`，`on_missing_label=error` 永远走不到。

`tools/build_labels.py` 的遍历方式：对 registry 中每个定义取 `group.get(params_key, default_params)`，调用一次 `compute` 并落盘其返回的**全部**键（与 `ensure` 共用同一实现），不按名称硬编码。

**最小验收命令**：

```powershell
& $py tools/build_labels.py --config configs/local/refactor_check.yaml --json
# 期望 exit 0，JSON 的 written 列表含 baseline.labels.builtin_names 的全部名称
```

---

### 7.6 `pipeline/adapters/sample_family.py`（M3）

**现有形态**（`pipeline/adapters/sample_family.py:10`）：

```python
HOLDING_REQUIRED_COLUMNS = (
    "idxs", "signal_date", "holding_return",
    "holding_trade_days", "is_closed", "entry_execution_price",
)

DATEBAR_REQUIRED_COLUMNS = (
    "idxs", "idxd", "open", "high", "low", "close", "volume", "amount", "paused",
)
```

**目标形态**：新增两个常量（不改上面两个）：

```python
SAMPLES_REQUIRED_COLUMNS = (...)   # 照 experiment_design §3.1 的列，逐字
LABEL_REQUIRED_COLUMNS = (...)     # 照 experiment_design §3.2 的列，逐字
```

`--show-contract` 同时打印四组列与 `RawArtifactContract`。

**最小验收命令**：

```powershell
& $py -m pipeline.adapters.sample_family --show-contract
# 期望 exit 0，JSON 含 samples_required_columns / label_required_columns
```

---

### 7.7 `pipeline/step2_slices.py`（M3，仅计数键）

**现有形态**（两处，`pipeline/step2_slices.py:78` 与 `:152`）：

```python
invalid_reasons = {"missing_signal_date": 0, "paused_signal_date": 0, "insufficient_history": 0}
```

以及两处 `invalid_reasons["missing_signal_date"] += 1`（`symbol` 不存在 / `signal_date` 不在该 symbol 的日期范围内）。

**目标形态**：

```python
invalid_reasons = {"unknown_symbol": 0, "date_not_in_symbol_range": 0,
                   "paused_signal_date": 0, "insufficient_history": 0}
```

第一处 `+= 1` 归 `unknown_symbol`，第二处归 `date_not_in_symbol_range`。**只改键名与文案**，不改判定逻辑、不改 `dropped_samples` 的合计口径。

**最小验收命令**：

```powershell
& $py -c "import inspect,pipeline.step2_slices as m; s=inspect.getsource(m); print('unknown_symbol' in s, 'date_not_in_symbol_range' in s, 'missing_signal_date' in s)"
# 期望 True True False
```

---

### 7.8 `tools/check_raw_data.py`（M3）

**现有形态**：`EXIT_READY = 0` / `EXIT_MISSING = 2` / `EXIT_INVALID = 3`；只检查 holding / datebar 两个文件。

**目标形态**：

1. 先 `load_params` 读配置，按 `params.input.mode` 分支；
2. `samples` 模式新增 `prepared` 段：目录存在性、`samples.parquet` 列契约、`sample_id` 唯一且 int64、`signal_date` 可 int 化、`datebar_fingerprint` 与 `samples_set_hash` 重算匹配、`idxs` 与 raw datebar 的交集率（< 99% 判 invalid，打印前 5 个不匹配样例）；
3. 退出码语义不变：0 = 就绪、2 = 缺失、3 = 契约不符；
4. 不得 import `pipeline` 之外的业务层；不得写任何文件。

**最小验收命令**：

```powershell
& $py tools/check_raw_data.py --config configs/local/refactor_check.yaml          # 期望 0
& $py tools/check_raw_data.py --config configs/local/refactor_check_samples.yaml  # 期望 0
```

---

### 7.9 `tools/verify_v11.py`（M0 创建，M5 归档/删除）

**现有形态**：不存在。

**目标形态**：M0 只实现 M0 表（`_run_m0_checks`）；M<k> 在自己的里程碑内实现 M<k> 表，见 §2.3 与 §1.3「只增不改」。整体结构严格按 `implementation_plan.md` §2.3 实现。

| 项 | 规定 |
| :--- | :--- |
| 参数 | `--milestone`、`--baseline`、`--baseline-git-ref`（M0 必填）、`--config`（可选）、`--state-file`（可重复）、`--samples-id`（M1 必填，可重复）、`--output-dir`（可重复）、`--report` |
| exit | 全通过 `0`；任一失败或缺产物 `1`；参数错误 `2` |
| 输出 | 只写 `--report` 指定的文件（Gate 必须显式传 `--report verify_report_M<k>.json`） |
| 依赖 | 只 `import` 标准库 + `pandas` / `pyarrow` |
| 数字来源 | 全部 `expected` 从 baseline 读；代码中不得出现观测值字面量 |
| 禁止 | 缺产物不得 skip；`all([])` 不得视为通过；不得 `try/except` 吞异常 |
| 禁止 | 不得被 `pipeline/**` 或 `data_prep/**` import |

**最小验收命令**：

```powershell
& $py tools/verify_v11.py --milestone M0 --baseline $base --baseline-git-ref a170d67 `
    --state-file "outputs/$ver/metadata/m0_legacy_step1.json" `
    --report verify_report_M0.json
# 期望 exit 0；M0 未完成时 exit 1，且 verify_report_M0.json 的 failed 非空
# A0-15 用 --baseline-git-ref 取 M0 修改前的 baseline，断言旧版全部键未被改写（仅允许新增键）
# Gate 必须 --report verify_report_M<k>.json；探索性运行必须写到 $env:TEMP
```

> M1 起 verify 必须由 `--samples-id` 显式指定样本集；禁止实现目录扫描或「无参数则跳过」（见 A1-16）。
>
> M5 注意：`_run_m5_checks` 必须在归档（`Move-Item`）**之前**加进 `tools/verify_v11.py`；归档后该文件不再是工作副本，无法再修改（见 §7.3 M5-4 注释与 A5-09）。

---

## 8. 禁止发明清单（逐条保留，不得压缩改写含义）

以下每条都已在设计文档中定死，实施 agent 不得自行变更。若发现必须变更：停手 → 写 `BLOCKED.md`，不得在 plan 里放宽。

1. 标签名解析只有唯一入口 `resolve_label(name) -> (definition, name_params, horizon)`；任何地方不得按标签名字符串分支，代码里不得出现标签名/采样名字面量。
2. `LabelDefinition.compute(...) -> Dict[str, pd.DataFrame]`，键=标签名，每个 frame 满足 §3.2。
3. `label_builder.ensure(config_dict, samples_id, label) -> List[Path]`：语义 = 该定义的**全部**产出就绪，缺任一则整组重建。
4. `spec_version` **只**来自 `definition.spec_version`；YAML 里没有该字段；`label_params_hash` 只取定义级值。
5. `on_missing_samples` / `on_missing_label` 在 v1.1 **只允许 `error`**；`build` 保留字段但报错并提示先跑 CLI。step1 不得隐式构建标签。
6. 采样与标签的上下文是两个不可互换的类型：`SampleContext`（`history(symbol,pos,n)` / `index_of(symbol,idxd)`，无前瞻方法）与 `LabelContext`（`entry_position(pos)` / `forward_window(pos,n)`，无过去窗口方法），分别由 build_samples / label_builder 构造。这是防泄漏边界的唯一落点。
7. `samples.parquet` 的 `is_paused` 取 datebar 原值（`bollinger` 恒 False，`legacy_holding` 可为 True），仅审计，step2 仍用自己的判定。
8. 守恒恒等式是四项（见 §3 的 plan 要求），漏掉 step2 的 `dropped_samples` 即判不成立。
9. `holding_clean.parquet` 文件名与必需列不变；`processed/datebar.parquet` 恒为 9 列；`idxs` 全链 str，`signal_date`/`idxd` 全链 int；禁止 `reset_index(drop=True)` 重建 `sample_id`。
10. `verify_v11.py` 的所有期望值**只能**从 `baseline_v1.0.13.json` 读取，代码中不得出现数字字面量；缺产物 exit 1；不得被 pipeline/** 或 data_prep/** import；M5 必须做「删除该脚本后 pytest 与 smoke 仍通过」的演练。
11. `data_prep` 不得 import `pipeline`；`pipeline` 只能函数内惰性 import `data_prep`。
12. M1 阶段 `input.mode=samples` 必须在 `compute_step1` 顶部显式抛错（M4 删除）；拦截**不放在** `_validate_params`（否则 builder CLI 用同一份 YAML 会被挡住）。
13. M3 之后旧配置的 `experiment_version` **会变化**（新增配置段进入 payload），这是预期行为；回归只比对 state 中的数值，不得沿用 v1.0.13 的输出目录。
14. L2 回归需要 `git worktree add` 检出 v1.0.13，但 data/raw 未入库：必须用指向主仓库的绝对路径配置或 junction，**不得**把 raw 数据复制进 worktree。
15. 冒烟模式只证明代码路径一致，不能作为结论；step1 在冒烟模式不抽样（`--smoke 500` 只影响 step2），故 step1.sample_count 仍是全量。
16. 只允许两类真实命令：`$py -c "<one-liner>"` 与 `$py run_experiment_pipeline.py --mode {smoke|full} --step {1..6} --config <path> --state-file <name>`。不要发明 `--steps` / `--all` / `--resume` / `--profile` 之类的参数。

> 第 8 条所说的「§3 的 plan 要求」，在本仓库对应 `implementation_plan.md` §9「守恒恒等式」（四项 + 数据来源表）。

**两项补充规则（设计已定，此处显式化，与上表同等效力）**

| # | 规则 |
| :---: | :--- |
| S1 | 采样侧 `params_key` 是退化的：采样参数就是 `data_prep.samples.params` 这个扁平 dict，**不要**实现成 `params["params"]`；`SampleDefinition.params_key` 可保留字段但采样侧忽略它。 |
| S2 | 配置缺 `params` 键时用 `definition.default_params` 补齐；只有**未知键**才报错。否则选 `target_holding_return`（0D 必用）会直接 `KeyError`。 |

---

## 9. 验证顺序

先小后大，**任何一步失败先修这一层，不要跳到下一层**：

| 层 | 内容 | 典型命令 |
| :---: | :--- | :--- |
| L0 | 静态/加载：配置可 `load_params`、CLI `--dry-run`、模块可 `import` | `$py -c "from pipeline.config import load_params; ..."` |
| L1 | 单元：`pytest tests/test_data_prep -q`（M3 起 `pytest tests -q`） | `$py -m pytest ...` |
| L2 | 产物自检：`build_samples` / `build_labels`，哈希与行契约 | `$py tools/build_samples.py ...` |
| L3 | 旧路径回归：step1 smoke → full step1 → smoke 全链 → worktree 逐值 | `$py run_experiment_pipeline.py ...` |
| L4 | 新路径：samples 模式 smoke 全链 + 守恒恒等式（smoke 口径 B；按 state 顶层 `mode` 选口径，规则见 `implementation_plan.md` §9） | 同上 + 一行 `$py -c` 断言 |
| L5 | 里程碑 Gate：`verify_v11.py --milestone M<k>` | `$py tools/verify_v11.py ...` |

补充约束：

1. 共享 `data/processed/`、`data/slices/`、`data/features/` 的验证**不得并行**；旧路径与新路径各用独立 `state-file`。
2. 冒烟只证明代码路径（禁止发明清单第 15 条）；任何「指标变好」都不作为通过依据。
3. 回归只比对 state 中的数值，不比对输出目录名（`experiment_version` 会变，第 13 条）。

---

## 10. 真实命令约束

允许的命令只有下面几类（全部在仓库根执行）：

```text
$py -c "<one-liner validation>"
$py run_experiment_pipeline.py --mode {smoke|full} --step {1..6} --config <path> --state-file <name>
$py tools/probe_datebar.py --config <path> --json
$py tools/build_samples.py --config <path> [--dry-run] [--json]
$py tools/build_labels.py --config <path> [--dry-run] [--json]
$py tools/check_raw_data.py --config <path> [--json]
$py tools/verify_v11.py --milestone M<k> --baseline <path> [--config <path>] [--state-file <path>]... [--output-dir <path>]...
$py -m pytest <selector> -q
git add <path>...                      # 只允许逐条列出本里程碑白名单路径
git commit -m "<type>: <subject>"
```

`git add` / `git commit` 的纪律：

1. `git add` 必须逐条列出白名单路径；**禁止** `git add -A`、`git add .`、`git commit -a`；
2. 删除文件用 `git add <已删除路径>` 暂存（M5 的 `tools/verify_v11.py` 就靠这一步让 `git ls-files` 不再返回它）；
3. 除 `git reset --hard m<k-1>-ok`（§4.2 止损）外，**禁止**任何历史改写命令：`git commit --amend`、`git rebase`、`git push --force`、`git filter-branch`。
4. `tools/verify_v11.py` 若在本里程碑被扩展，必须一并 add；它不占白名单名额，但受「只增不改」约束。

不要发明：

- `--steps`
- `--all`
- `--resume`
- `--profile`

也不要用额外脚本或 `cmd /c` 包装这些命令、不要写临时 `.py` 文件、不要在 `run_experiment_pipeline.py` 上加参数。

---

## 11. 完成定义

只有以下条件**同时**满足，才可宣告 v1.1.0 完成（与 `implementation_plan.md` §11 的结项标准一一对应）：

1. M0–M5 的 `verify_v11.py` 全部 `exit 0`，`verify_report.json` 留档（M5 用归档路径运行）。
2. 旧路径 L1/L2/L3 与 `baseline_v1.0.13.json` 逐值一致（计数精确、浮点 1e-12）。
3. 新路径 samples 模式 smoke 跑到 step6，六个 `checks` 全 True，`dropped_no_target > 0`，守恒恒等式成立。
4. `check_raw_data` 三种退出码语义正确（0 就绪 / 2 缺失 / 3 契约不符）。
5. 归档（或删除）`verify_v11.py` 后 `pytest` 与一次 smoke 仍全部通过。
6. `outputs/experiment_registry.json` 只含可复现条目，`v1.1.0` 为 annotated tag，`git status --porcelain` 无白名单之外改动。

---

## 12. 结项报告（固定栏目，逐项输出）

完成后一次性输出，缺栏目视为未完成：

| # | 栏目 | 内容要求 |
| :---: | :--- | :--- |
| 1 | 审计结论 | 分支、`git status --porcelain` 摘要、解释器版本、是否命中阻塞条件 |
| 2 | 改动文件清单 | 逐文件：路径 + 动作（新增/修改/删除）+ 归属里程碑；按白名单分组 |
| 3 | 每步验收结果 | 每个里程碑的每条验收命令 + 实际 exit code；失败的给出失败原因与修复动作 |
| 4 | `verify_report.json` 关键行 | 每个里程碑的 `milestone` / `exit_code` / `items` 条数 / `failed` 列表（为空写 `[]`） |
| 5 | 与基线不一致项 | 逐项列出 `expected` vs `actual`；**应为空**，非空必须解释 |
| 6 | 守恒恒等式 | 四项数值 + 等式右边 + 是否成立（按 `state.mode` 选口径并写明 A/B；规则见 `implementation_plan.md` §9） |
| 7 | L2 worktree 证据 | worktree 路径、所用 raw 配置路径（绝对路径/junction 方式）、比对文件清单与结论 |
| 8 | 发布形态 | `git cat-file -t v1.1.0` 输出、registry 条目数、`git status --porcelain` 输出 |
| 9 | BLOCKED 条目 | 无则写「无」；有则给出 `BLOCKED.md` 路径与四条内容摘要 |
| 10 | 每里程碑 commit | 逐里程碑给出 commit short-sha + 对应 `m<k>-ok` tag；用 `git log --oneline -1 m<k>-ok` 证明 tag 指向该提交（A3-01/A5-01 都以 HEAD 为基准，缺这一段就无法证明「提交先于 tag」） |

报告中的每个数字都必须能在上面的命令回显里找到出处；基线数字标注为「读自 `baseline_v1.0.13.json` 的 `<键路径>`」。