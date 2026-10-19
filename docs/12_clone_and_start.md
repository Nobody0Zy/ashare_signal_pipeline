# 12_clone_and_start

## 这份文档解决什么问题

你拿到了 `ashare_signal_pipeline` 模板，想从一个空目录开始，克隆出属于某个样本族（例如 `lowvol`、`boll`、`momentum`）的独立仓库，并跑通第一次 smoke。

本文覆盖：获取方式、命名约定、首次提交前必须做的事、最小可运行路径、常见错误。

> 本文面向"从模板新建仓库"的场景。若你已经在仓库内部、只想开新版本，请读 `08_lineage_startup.md`。

---

## 1. 选择获取方式

| 方式 | 怎么做 | 得到什么 | 建议 |
| :--- | :--- | :--- | :--- |
| **GitHub Template** | 仓库页点 `Use this template` → `Create a new repository` | 新仓库，**只有 1 条 `Initial commit`**，无模板历史 | ✅ 推荐 |
| **git clone** | `git clone <template-url> <folder>` | 完整模板历史（3 条 commit）+ `origin` 指向模板 | 需要追溯模板演进时使用 |
| 手动复制文件 | 复制目录后 `git init` | 无历史 | ❌ 不推荐，易漏 `.github/`、`.gitignore` |

两种方式的差异只在 **git 历史与 remote**，工作树内容完全相同（52 个文件）。

---

## 2. 命名约定

**样本族的名字就是仓库文件夹名，也是 `base_version`。**

| 规则 | 说明 |
| :--- | :--- |
| 字符集 | 小写字母、数字、下划线 |
| 不含版本号 | 不要叫 `v6`、`v7`；版本用 `version_subdir` 表达 |
| 语义化 | 反映信号类型或数据来源，如 `lowvol`、`boll`、`momentum` |

```powershell
# 方式一：Use this template 之后
git clone https://github.com/<owner>/<new-repo>.git lowvol
Set-Location lowvol

# 方式二：直接 clone 模板并重命名目录
git clone https://github.com/Nobody0Zy/ashare_signal_pipeline.git lowvol
Set-Location lowvol
```

---

## 3. 重设远端

### 3.1 用 Use this template 建的仓库

`origin` 已经指向你自己的新仓库，无需改动。

### 3.2 直接 clone 模板的仓库

`origin` 指向模板仓库，直接 push 会被拒绝。二选一：

**A. 只保留自己的仓库**

```powershell
git remote remove origin
git remote add origin https://github.com/<you>/<your-repo>.git
```

**B. 保留模板为 upstream，便于日后合入模板改进**

```powershell
git remote rename origin upstream
git remote add origin https://github.com/<you>/<your-repo>.git
```

> 模板改进**不会自动同步**。若要吸收，手动 `git fetch upstream` 后挑选提交合入，不要整体 merge——谱系仓库的配置与模板可能已经分叉。

---

## 4. 首次提交前必须做的三件事

### 4.1 设置本仓库的提交身份

不要依赖全局配置，否则可能继承机器上别人的身份。

```powershell
git config user.name "<your-name>"
git config user.email "<your-email>"
git config --local --get user.name
git config --local --get user.email
```

### 4.2 修改兜底配置的样本族名

`configs/default.yaml` 出厂值为占位符，必须改成你的样本族：

```yaml
experiment:
  base_version: your_family   # ← 改成 lowvol / boll / momentum
```

同时检查以下"示例值"，按新样本实际情况调整：

| 参数 | 出厂值 | 说明 |
| :--- | ---: | :--- |
| `window.lookback_days` | 60 | 特征窗口宽度（非停牌交易日数） |
| `time_split.train_end` / `val_start` / `test_start` | 2020–2023 区间 | 必须按新样本的实际时间跨度重划 |
| `time_split.buffer_trading_days` | 60 | 全量模式必须 ≥ `lookback_days` |

> `configs/default.yaml` 是**兜底继承模板**，正式实验请用 `scaffold` 生成 `configs/<base_version>/<version_subdir>/` 下的配置。

### 4.3 记录 fork 来源

在版本目录里写清楚"这个仓库从哪来、对应模板哪个版本"：

```powershell
New-Item -ItemType Directory -Path design\<base_version>\<version_subdir> -Force
```

`design/<base_version>/<version_subdir>/CONTEXT.md`：

```markdown
# <base_version> / <version_subdir> 上下文

- fork 自: ashare_signal_pipeline v1.0.0
- fork 日期: <yyyy-mm-dd>
- 样本族: <base_version>
- 数据来源: <简述原始数据出处>
- 本轮目标: <一句话>
- 是否修改内核: 否
```

---

## 5. 最小可运行路径

### 5.1 放入原始数据

| 文件 | 位置 | 说明 |
| :--- | :--- | :--- |
| `holding_stats_all.parquet` | `data/raw/` | 已平仓交易样本 |
| `dateBar.pkl` | `data/raw/` | 后复权日线 |

先确认契约：

```powershell
python pipeline/adapters/sample_family.py --show-contract
```

输出会列出 `holding` 需要的 6 个字段与 `datebar` 需要的 9 个字段。缺字段时不要硬跑，先补数据。

### 5.2 生成谱系入口

```powershell
python tools/scaffold_experiment_line.py <base_version> --version-subdir v0 --label baseline
```

生成：

```text
configs/<base_version>/v0/<base_version>_baseline.yaml
design/<base_version>/v0/CONTEXT.md
design/<base_version>/README.md
```

同时向 `configs/experiment_index.yaml` 追加注册条目。

### 5.3 跑 smoke（step1 → step6）

**每一步都必须用独立的 `state-file`**，不要复用：

```powershell
$cfg = "configs/<base_version>/v0/<base_version>_baseline.yaml"

python run_experiment_pipeline.py --mode smoke --smoke 5000 --step 1 --config $cfg --state-file pipeline_state_smoke_<base_version>_v0_baseline_step1.json
python run_experiment_pipeline.py --mode smoke --smoke 5000 --step 2 --config $cfg --state-file pipeline_state_smoke_<base_version>_v0_baseline_step2.json
python run_experiment_pipeline.py --mode smoke --smoke 5000 --step 3 --config $cfg --state-file pipeline_state_smoke_<base_version>_v0_baseline_step3.json
python run_experiment_pipeline.py --mode smoke --smoke 5000 --step 4 --config $cfg --state-file pipeline_state_smoke_<base_version>_v0_baseline_step4.json
python run_experiment_pipeline.py --mode smoke --smoke 5000 --step 5 --config $cfg --state-file pipeline_state_smoke_<base_version>_v0_baseline_step5.json
python run_experiment_pipeline.py --mode smoke --smoke 5000 --step 6 --config $cfg --state-file pipeline_state_smoke_<base_version>_v0_baseline_step6.json
```

每一步成功后都会打印 `checks_passed`，并把状态写入该实验的 `metadata/` 目录。任一 check 失败则该步为 `failed`，后续步骤禁止启动。

冒烟结果**只用于验证管道畅通，不作为有效性参考**。

### 5.4 验证产物

```powershell
# 找到本次 smoke 的输出目录
Get-ChildItem outputs/<base_version> -Directory

# 查看状态
Get-Content outputs/<base_version>/<experiment_version>/metadata/pipeline_state_smoke_<...>.json
```

期望：`outputs/<base_version>/v0/<experiment_version>/` 下存在 `predictions_smoke.csv`、`metadata/experiment_manifest.json`。

### 5.5 提交首次成果

```powershell
git add configs design
git commit -m "Add <base_version> v0 baseline scaffold"
```

`outputs/`、`models/`、`data/` 已在 `.gitignore` 中，不会被提交。

---

## 6. 进入全量前的前置检查

| # | 检查 | 判据 |
| :---: | :--- | :--- |
| 1 | smoke 六步全通 | 每步 `checks_passed = true` |
| 2 | 时间隔离 | 三集合 `signal_date` 无重叠 |
| 3 | 缓冲带 | 相邻集合间隔 ≥ 60 个交易日（`enforce_buffer=true` 时会硬校验） |
| 4 | 防泄漏 | `calendar_gain_share` 接近 0 |
| 5 | 命名 | `version_subdir` 与 `label` 未混用 |

全量必须用独立 `state-file`，且**串行执行**，不要与 smoke 或其他配置并行。

---

## 7. 常见错误

| 现象 | 原因 | 处理 |
| :--- | :--- | :--- |
| `push` 报 `Repository not found` | 直接 clone 模板后未重设 remote | 见 §3.2 |
| 输出跑到 `outputs/your_family/` | 未改 `configs/default.yaml` 的 `base_version` | 见 §4.2 |
| `config 不存在` | 用了 `configs/default.yaml` 而非 scaffold 生成的配置 | 见 §5.2 |
| step2 报 `checks_passed=false` | 样本历史不足 `lookback_days`，或信号日停牌 | 查看该步 `details.checks` |
| 多步并发后数据错乱 | 共享中间目录、并行执行 | 串行 + 每步独立 `state-file` |
| `tsfresh` 报列名冲突或重复列 | 未按契约清洗列名 | 见 `03_pitfalls.md` |

---

## 8. 相关文档

| 我想… | 读 |
| :--- | :--- |
| 了解全部硬约束 | `01_pipeline_contract.md` |
| 在已有仓库内开新版本 | `08_lineage_startup.md` |
| 判断某步是否合格 | `07_step_acceptance_criteria.md` |
| 避免已知坑 | `03_pitfalls.md` |
| 配置 tsfresh / LightGBM | `05_framework_mechanisms.md` |
| 收尾一个谱系 | `06_closeout_playbook.md` |
