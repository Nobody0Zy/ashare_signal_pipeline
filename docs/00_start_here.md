# 00_start_here

## 这份文档解决什么问题

如果你刚把仓库克隆下来，不知道先放什么数据、先读什么文档、先跑什么命令，就从这里开始。

## 5 分钟上手

按顺序完成这 4 个命令：

```powershell
python pipeline/adapters/sample_family.py --show-contract
python tools/scaffold_experiment_line.py <base_version> --version-subdir v0 --label baseline
python run_experiment_pipeline.py --mode smoke --smoke 5000 --step 1 --config configs/<base_version>/v0/<base_version>_baseline.yaml --state-file pipeline_state_smoke_<base_version>_v0_baseline_step1.json
python run_experiment_pipeline.py --mode smoke --smoke 5000 --step 2 --config configs/<base_version>/v0/<base_version>_baseline.yaml --state-file pipeline_state_smoke_<base_version>_v0_baseline_step2.json
```

说明：

- 第 1 条先确认输入契约。
- 第 2 条生成 `design/` 与 `configs/` 的入口骨架。
- 第 3、4 条只是 smoke 起步；后续 step3~step6 继续沿同样模式逐步执行。
- 每一步都使用独立 `state-file`，不要复用。

## 阅读顺序

建议阅读顺序固定为：

1. `01_pipeline_contract.md`
2. `08_lineage_startup.md`
3. `03_pitfalls.md`
4. `05_framework_mechanisms.md`
5. `04_engineering_lessons.md`

先掌握硬边界，再看 SOP，再看已知坑与机制解释。

## 我想做什么

| 我想… | 读 |
| :--- | :--- |
| 开新谱系 | `08_lineage_startup.md` |
| 搞清数据契约 | `01_pipeline_contract.md` |
| 判断某步是否合格 | `07_step_acceptance_criteria.md` |
| 避免已知坑 | `03_pitfalls.md` |
| 配置 tsfresh / LightGBM | `05_framework_mechanisms.md` |
| 收尾一个谱系 | `06_closeout_playbook.md` |

## 最小执行提醒

1. `data/raw/` 只读。
2. 不要靠删除目录重置环境。
3. 冒烟与全量都必须逐步执行，不要跳过前置检查。
4. 对拍旧行为时，始终通过 YAML 加载参数。

## 下一步

如果你已经确认输入契约无误，请转到 `08_lineage_startup.md`，按样本族 → 版本迭代 → 配置差异的层级创建第一条实验线。