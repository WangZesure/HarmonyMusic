# 搜歌 Agent · 文档索引

这个目录是**阶段 1–6 的设计、实现与评测记录**。先看这一页，再按需要跳转。

> 代码怎么跑，看各模块的 README（`agent/`、`data_pipeline/`、`recommend/`、`myMusic/`、`openKoa/`）。
> 本目录只放**设计与证据**，不放运行说明。

---

## 一、按目的选阅读路径

### A. 5 分钟了解这个项目
1. [`contracts_v1.md`](contracts_v1.md) — 冻结契约 C1–C8（ID / 表结构 / 标签词表 / LRC / 地址 / 向量索引 / 工具接口 / 评测集）
2. [`architect_report.md`](architect_report.md) §1 — 系统全景、数据流、每个模块的输入输出
3. [`phase3_report.md`](phase3_report.md) §4 — 成绩表与基线对比

### B. 面试准备（讲设计取舍与踩坑）
0. [`agent.html`](agent.html) — **一条真实查询走完五步**，每步的数据结构都摊开
1. [`design_and_gaps.md`](design_and_gaps.md) — **取舍与短板总清单**，包含所有妥协、捷径、未验证项
2. [`architect_report.md`](architect_report.md) §3 — 过程中遇到的问题与解决（12 条，按阶段）
3. [`archive/phase3loop.md`](archive/phase3loop.md) — 迭代史：两次阈值决策、F1 的失败与回退（讲"怎么定位问题"的素材）
4. [`phase4_report.md`](phase4_report.md) §2.1 — F7 案例：评测集全绿但多轮主流程是坏的

### C. 完整评审（逐阶段验收）
按 [`acceptance.md`](acceptance.md) 走：每个阶段列了交付物、验证命令、证据文档。

---

## 二、当前有效文档

| 文档 | 内容 | 状态 |
|---|---|---|
| [`contracts_v1.md`](contracts_v1.md) | 冻结契约 C1–C8，跨阶段共享的东西 | 权威，改动需版本 +1 |
| [`architect_report.md`](architect_report.md) | 架构师复核：系统全景、成绩、F1–F7 问题与修复、端到端复测 | 终版 |
| [`design_and_gaps.md`](design_and_gaps.md) | 设计取舍 / 已知短板 / 未验证项，逐条注明是否已记于别处 | 持续维护 |
| [`acceptance.md`](acceptance.md) | 六阶段验收清单：交付物在哪、怎么验证 | 持续维护 |
| [`phase12_report.md`](phase12_report.md) | 阶段 1–2：数据流水线、数据库、工具层 | 完成 |
| [`phase3_report.md`](phase3_report.md) | 阶段 3：单轮 Agent 的成绩、消融、局限 | 完成（含 F1 终结记录） |
| [`phase3_traces.md`](phase3_traces.md) | 10 条典型 trace（成功 5 / 边界 5） | 附录 |
| [`phase4_report.md`](phase4_report.md) | 阶段 4：多轮对话与记忆（含 F7 修复） | 完成 |
| [`phase5_report.md`](phase5_report.md) | 阶段 5：Koa SSE + 鸿蒙「AI 搜歌」页 | 完成 |
| [`architecture.html`](architecture.html) | 架构图与 Agent 流水线的可视化说明 | 演示用 |
| [`agent.html`](agent.html) | **搜歌 Agent 的数据结构与流水线**，含一条真实 trace 逐阶段讲解 | 演示 / 面试 |

## 三、归档（历史，仅追溯用）

这些文档是当时的**计划、任务书或过程日志**，结论已被上面的报告吸收。保留是为了能追溯"当时为什么这么定"。

| 文档 | 是什么 | 为什么归档 |
|---|---|---|
| [`archive/featureToDo.md`](archive/featureToDo.md) | 六阶段总计划 | 已全部执行完 |
| [`archive/phase12.md`](archive/phase12.md) | 阶段 1–2 计划 | 结论见 `phase12_report.md` |
| [`archive/between12.md`](archive/between12.md) | 数据库落地任务书 | 结论见 `phase12_report.md` |
| [`archive/phase3.md`](archive/phase3.md) | 阶段 3 计划 | 结论见 `phase3_report.md` |
| [`archive/phase3_status.md`](archive/phase3_status.md) | 阶段 3 中途进度（2026-10-12） | **被 `phase3_report.md` 完全取代**，且其"三类合并 Hit@5 ≥0.98"是合并口径 |
| [`archive/phase3loop.md`](archive/phase3loop.md) | 迭代记录（655 行） | 含两轮 F1 决策、作废小节、架构师终审；结论已进 `phase3_report.md` / `design_and_gaps.md` |

> ⚠️ 归档文档里的数字**不要直接引用**——`archive/phase3_status.md` 的合并口径已被拆分更正，`archive/phase3loop.md` 里前半部分的小节也已标"作废"。以 `phase3_report.md` 为准。

---

## 四、一句话结论

- **主指标达标**：test 集上 title / lyric / describe 三类 Hit@5 = 1.000，曲库外拒答 = 1.000，编造率 0。
- **唯一未达标**：曲库外歌词拒答按子类拆开后，知名 0.70（目标 0.90）、冷门 0.633。**已终结，不再迭代**，原因与证据见 `phase3_report.md` §1 与 `archive/phase3loop.md` 末节。
- **回归**：`pytest agent/tests` 22 passed；`python -m agent.e2e_smoke` 12/12 passed。
