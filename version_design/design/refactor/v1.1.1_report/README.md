# v1.1.1 报告层重构 — 文档索引

> 最后更新: 2026-11-19
> 目标 tag: `v1.1.1`（annotated）｜分支: `refactor/v1.1.1`｜基线 tag: `v1.1.0`（`fe9a3d6`）
> 本文件只做导航，不含设计内容。

---

## 该读哪个

| 你的角色 | 读这些 | 不必读 |
| :--- | :--- | :--- |
| **实施 agent（单个里程碑）** | [code_agent_prompt.md](./code_agent_prompt.md)（执行边界）+ [implementation_plan.md](./implementation_plan.md) 对应章节 + [experiment_design.md](./experiment_design.md) 对应章节（见其「阅读方式」表） | 其余全部 |
| 实施 agent（总览） | [experiment_design.md](./experiment_design.md) §0 · §2.3 · [implementation_plan.md](./implementation_plan.md) §0 | — |
| 复核人 | 全部 | — |
| 想查「为什么这样定」 | [design_memory.md](./design_memory.md) | — |
| 只要数值锚点 | [baseline_v1.1.0.json](./baseline_v1.1.0.json) | — |

**不要通读全文。** 每个里程碑的必读章节与行数，以 [experiment_design.md](./experiment_design.md) 开头的「阅读方式」表为唯一权威。

---

## 文件说明

| 文件 | 内容 | 状态 |
| :--- | :--- | :--- |
| [experiment_design.md](./experiment_design.md) | **规格正文（唯一内容权威）**：目标、事实基准、架构、契约、三份报告规范、可读性规范、里程碑、验收、限制 | 定稿（2026-11-19） |
| [implementation_plan.md](./implementation_plan.md) | **施工顺序**：每里程碑的文件白名单、逐文件改什么、验收命令、回滚点；§0.4 为「独立实现可行性检查」 | 定稿 |
| [code_agent_prompt.md](./code_agent_prompt.md) | **执行边界**：文档优先级、阻塞条件、止损规则、禁止发明清单、结项报告格式 | 定稿 |
| [design_memory.md](./design_memory.md) | 设计依据、决策记录（Q1–Q12）、被否决方案、修订历史、失败模式。**实施时不需要读** | 归档 |
| [baseline_v1.1.0.json](./baseline_v1.1.0.json) | 回归锚点：v1.1.0 demo 实测值（demo_old / demo_new）与报告必须呈现的字段清单 | 定稿 |
| `verify_report_M*.json` | 各里程碑 Gate 报告（运行期产物，不入库） | 运行期 |

---

## 与 v1.1 目录的关系

| 项 | v1.1 目录（`..`） | 本目录 |
| :--- | :--- | :--- |
| 范围 | `data_prep` 前置层 + step1 双模式 | **只改报告层**，不动 step 语义 |
| 规格 | `../experiment_design.md` | `./experiment_design.md` |
| 计划 | `../implementation_plan.md` | `./implementation_plan.md` |
| 执行提示 | `../code_agent_prompt.md` | `./code_agent_prompt.md` |
| 验证脚本 | `../tools/verify_v11.py`（M5 已归档） | `tools/verify_reports.py`（本迭代新增） |

**不在本迭代范围**：任何 step1–step6 语义变更、标签/采样规则变更、`holding_clean` 覆盖问题修复（见 `docs/16_potential_risks.md` PR-001）。

---

## 版本与分支

| 项 | 值 |
| :--- | :--- |
| 目标 tag | `v1.1.1` |
| 分支 | `refactor/v1.1.1` |
| 基线 tag | `v1.1.0` → `fe9a3d6` |
| 开工前 HEAD 参考 | `120d635`（本套文档提交后为其后续提交） |
| 发布 Gate | `tools/verify_reports.py` 全部 exit 0 + M0–M5 各自里程碑 Gate exit 0 |

> 设计文档路径约定：`version_design/design/<lineage>/<version_subdir>/`。
