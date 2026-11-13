# 重构设计目录索引

> 最后更新: 2026-11-10
> 这份文件只做导航，不含设计内容。

---

## 该读哪个

| 你的角色 | 读这些 | 不必读 |
| :--- | :--- | :--- |
| **实施 agent（单个里程碑）** | [experiment_design.md](./experiment_design.md) 的对应章节（见下表）+ [code_agent_prompt.md](./code_agent_prompt.md)（**待写；M0 开工前置**） | 其余全部 |
| 实施 agent（总览） | [experiment_design.md](./experiment_design.md) §0、§7.1、§7.2 | — |
| 复核人 | 全部 | — |
| 想查「为什么这样定」 | [design_memory.md](./design_memory.md) | — |
| 要做阶段 0 对照实验 | [experiment_plan_v11.md](./experiment_plan_v11.md) | — |

---

## 各里程碑的阅读范围

**不要通读全文。** 每个里程碑的必读章节与行数，以 [experiment_design.md](./experiment_design.md) 开头的「阅读方式」表为**唯一权威**（本文件不再重复，避免两份数字漂移）。

单次最多约 378 行（M1），其中约 61 行是可直接复制的 dataclass。

---

## 文件说明

| 文件 | 内容 | 状态 |
| :--- | :--- | :--- |
| [experiment_design.md](./experiment_design.md) | **实施规范（唯一权威）**。目标、事实基准、架构、契约、采样/标签规范、配置、里程碑、验收、限制 | 定稿（2026-11-10 复核修订） |
| [design_memory.md](./design_memory.md) | 设计依据与修订历史。D1–D36 修正清单、2026-11-10 复核记录（D37–D46）、探测证据、Q1–Q13 决策、被否决方向、失败模式 | 归档，实施时不需要 |
| [experiment_plan_v11.md](./experiment_plan_v11.md) | 阶段 0 对照实验（0A–0D）。**不阻塞 v1.1.0 发布** | 待 v1.1.0 发布后启动 |
| [implementation_plan.md](./implementation_plan.md) | 文件级施工顺序与验收命令 | **待写（M0 开工前置）** |
| [code_agent_prompt.md](./code_agent_prompt.md) | 交给实施 agent 的执行边界与硬约束 | **待写（M0 开工前置）** |
| [baseline_v1.0.13.json](./baseline_v1.0.13.json) | 回归锚点。当前只记配置路径与观测值，M0 需补参数快照与数据指纹 | 待 M0 补齐 |
| `_archive/` | 历史留档，**不要读** | 归档 |

---

## `_archive/` 说明

| 文件 | 来历 |
| :--- | :--- |
| `design_raw.md` | 最初的用户方案（v2.1，703 行）。其缺陷已在 `design_memory.md` §2 逐条记录，结论已全部吸收进 `experiment_design.md` |
| `experiment_design_v1_draft.md` | 第一版实施设计（1077 行），包含大量过程性论证。已重写为纯规范，过程记录移入 `design_memory.md` |

两者仅供追溯，实施时不读。

---

## 版本与分支

| 项 | 值 |
| :--- | :--- |
| 目标 tag | `v1.1.0`（annotated） |
| 分支 | `refactor/v1.1` |
| 基线 tag | `v1.0.13`（`70d4341`） |
| 发布 Gate | 旧路径 12 个字段逐值一致 + 新路径 smoke 跑通 step6 |

> 设计文档路径约定：`version_design/design/<lineage>/`（版本目录：`version_design/design/<lineage>/<version_subdir>/`）

---

## v1.1.1（报告重构）

| 文件 | 内容 | 状态 |
| :--- | :--- | :--- |
| [v1.1.1_report/design.md](./v1.1.1_report/design.md) | 报告层重构规范：三份报告（engineering / experiment / executive）、事实表契约、产物清单、可读性规范、里程碑与验收 | 设计稿，待评审 |

> 该迭代在 `v1.1.0` 发布后开分支 `refactor/v1.1.1`，不修改 step1–step6 任何行为。
