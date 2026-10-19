# 08_lineage_startup

## 命名层级

命名是三层包含关系，不是并列：

```text
样本族 base_version      boll / lowvol / momentum —— 决定 data/raw 与顶层目录
  └── 版本 version_subdir  v0 / v1 / v2 —— 同一份样本数据上的第 N 轮迭代
        └── 配置 label      baseline / expand_tsfresh —— 同版本内的并行对照
```

换样本数据 → 新 `base_version`；样本不变但方法迭代 → 递增 `version_subdir`；小开关 / 超参 / 手动特征调整 → 只改 `label`。

## 8 步 SOP

1. **确认数据契约**  
   先运行 `python pipeline/adapters/sample_family.py --show-contract`，确认 holding 与 datebar 的最小字段集。

2. **放置原始数据**  
   把 `data/raw/holding_stats_all.parquet` 与 `data/raw/dateBar.pkl` 放到 `data/raw/`。

3. **确定实验坐标**  
   明确样本族名 `base_version`（如 `boll`）与版本目录 `version_subdir`（如 `v0`）。

4. **建入口**  

   ```powershell
   python tools/scaffold_experiment_line.py <base_version> --version-subdir <version_subdir> --label baseline
   ```

   这会生成 `configs/<base_version>/<version_subdir>/`、`design/<base_version>/<version_subdir>/`，并在 `design/<base_version>/README.md` 追加本版本记录。

5. **写版本上下文**  
   在 `design/<base_version>/<version_subdir>/` 下补充 `CONTEXT.md`，记录 fork 版本、样本族、数据来源、版本目标。

6. **补配置并注册**  
   在配置中设置 `experiment.version_subdir: <version_subdir>`，并把该配置注册到 `configs/experiment_index.yaml`。

7. **先做 smoke**  
   使用 `--mode smoke --smoke 5000 --step 1..6` 的逐步执行方式，每步独立 state-file。

8. **full 前复核硬边界**  
   确认相邻集合的缓冲带 ≥60 个交易日，同时检查 `calendar_gain_share` 接近 0。

## state-file 命名

| 场景 | 命名 |
| :--- | :--- |
| smoke | `pipeline_state_smoke_<base_version>_<version_subdir>_<label>.json` |
| full | `pipeline_state_full_<base_version>_<version_subdir>_<label>.json` |
| 诊断重跑 | `pipeline_state_full_<base_version>_<version_subdir>_<label>_<purpose>.json` |

`version_subdir` 必须进入 state-file；否则同一样本族的多个版本会互相覆盖。

## 目录隔离

| 类型 | 路径 |
| :--- | :--- |
| 输出 | `outputs/<base_version>/[<version_subdir>/]<run_id>/` |
| 模型 | `models/<base_version>/[<version_subdir>/]<run_id>/` |
| 数据 | `data/{processed,slices,ts_features,features,splits}/<base_version>/[<version_subdir>/]<run_id>/` |

其中 `<run_id>` = `<base_version>_<run_date>_<label>_<param_hash>`。

> `build_experiment_version()` 的返回值**已包含** `base_version/`（以及存在时的 `version_subdir/`）前缀，
> 例如 `boll/v0/boll_20261020_baseline_a1b2c3d4`。
> 因此产物路径有两种等价写法，**不要混用、也不要重复补前缀**：
> - 用返回值：`outputs/<返回值>/` → `outputs/boll/v0/boll_20261020_baseline_a1b2c3d4/`
> - 用分段：`outputs/<base_version>/<version_subdir>/<run_id>/` → 同上

## 设计文档落位

| 文件 | 落位 |
| :--- | :--- |
| 版本上下文 | `design/<base_version>/<version_subdir>/CONTEXT.md` |
| 实验设计 | `design/<base_version>/<version_subdir>/experiment_design.md` |
| 实施计划 | `design/<base_version>/<version_subdir>/implementation_plan.md` |
| 收尾记录 | `design/<base_version>/<version_subdir>/closeout.md` |
| 主库基线 | `design/<base_version>/<version_subdir>/baseline_fixture.md` |
| 样本族总览 | `design/<base_version>/README.md`（跨版本，记录本族已有哪些 `version_subdir`） |

> `design/` 按 `base_version/version_subdir/` 两级组织，**不要扁平化**。
> 原因：每一轮迭代都会产生同名的 `experiment_design.md` / `implementation_plan.md`；
> 扁平存放会造成同名冲突。样本族层面的公共信息（如本族已有哪些版本）写入
> `design/<base_version>/README.md`，不重复写到各版本目录。

## 最小复核清单

1. 数据文件已放到 `data/raw/`
2. scaffold 已生成 config 与 design 入口
3. `experiment_index.yaml` 已注册
4. smoke 的 step1~step6 计划使用独立 state-file
5. full 启动前已确认缓冲带与泄露诊断