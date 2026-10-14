# 阶段 3 报告：单轮搜歌 Agent

> 2026-10-13。代码：`agent/core/`（意图/召回/排序/校验/回复/流水线）+ `agent/tools/bm25.py` + `POST /agent/search`。
> 契约：`contracts_v1.md`(C3 词表/C7 工具)。过程记录见 `phase3loop.md`；数据底座见 `phase12_report.md`。
> 运行用解释器：`D:\apps\tools\Conda\python.exe`（含 torch）；模型缓存在 `resources/hf_cache/`。

## 1. 结论

| 指标（k=5） | dev（109） | test（74，第三次使用） | 验收线 |
|---|---|---|---|
| title+lyric+describe 合并 Hit@5 | **1.000** | **1.000** | ≥0.80 且 >B1 ✅ |
| 曲库没有·拒答率 | 1.000 | 1.000 | ≥0.9 ✅ |
| 编造率 | 0 | 0 | 0 ✅ |

**全部达标。** 且三类合并 Hit@5 都高于最强基线（test: B1=0.61、B2=0.78）。

> **F1 口径更正（2026-10-14）**：后续新增的「曲库外歌词拒答」一项，原定双目标「拒答 ≥0.9 **且** lyric Hit@5 ≥0.96」**结构性不可兼得**——曲库外名句与库内真实歌词行在第 [0.7,0.9] 分数带重叠。经架构定调改为**拒答优先**的折中线，**阈值冻结 `LYRIC_MIN_CONF=0.75`**；新验收线：曲库外歌词拒答 ≥0.85、dev lyric Hit@5 ≥0.90、三类合并 Hit@5 ≥0.90、test 不回退。评分-阈值曲线保留在 `phase3loop.md`，**请勿再调阈值赌分**；灰区（`[0.65,0.75)`）返回 ≤2 首 + “不太确定”，单独统计、不计入上述两项。

## 2. 评测集

`agent/eval/golden_{dev,test}.jsonl`。dev 109 / test 74。构造：脚本从歌曲卡片让 DeepSeek 写查询，gold 自带；人工复核删过宽泛条目。

| 类型 | dev | test | 指标 |
|---|---|---|---|
| title 指定歌曲（含拼错） | 15 | 15 | Hit@1/@5 |
| lyric 歌词片段（原文/记错/中文转述） | 25 | 24 | Hit@1/@5/MRR |
| describe 描述找歌 | 20 | 20 | Hit@5 |
| describe_fuzzy 模糊描述（禁抄摘要） | 10 | 0 | Hit@5 |
| absent 曲库没有（同名异歌手/歌手不在库/歌名不在库） | 17 | 15 | 拒答率 |
| constraint 硬约束组合（SQL 算满足集） | 10 | 0 | 满足率@5 |
| mood 氛围（0/1/2 分级） | 12 | 0 | nDCG@5 |

主表只用 **title/lyric/describe**；其余另列。另存 `intent_labels.jsonl`（50 条解析标注）。

## 3. 数据流（固定流水线）

① 意图解析（DeepSeek JSON + 快速通道；标签按 C3 词表校验、**语言归一成 ISO 代码**）
→ ② 多路召回（BM25 歌名+拼错 fuzzy；歌词中英双语 BM25+向量；semantic；similar）
→ ③ 融合/过滤/重排（**歌词类直接用歌词召回顺序**；其余 RRF + 硬约束过滤 + bge-reranker-v2-m3 重排 + 命中歌名置顶）
→ ④ 校验/放宽/拒答（固定放宽顺序，人声/语言/时长不放宽；点名查不到或歌手不匹配则拒答）
→ ⑤ 回复（DeepSeek 只写理由；song_id 越界即丢）。

## 4. 成绩表（k=5）

### dev（109 条）

| 类型/指标 | **A Agent** | B0 猜 | B1 整库 | B2 单向量 | B3 LIKE |
|---|---|---|---|---|---|
| title Hit@5 | 1.000 | 0.867 | 1.000 | 1.000 | 0.000 |
| describe Hit@5 | 0.950 | 0.050 | 0.900 | 0.900 | 0.000 |
| lyric Hit@5 | 1.000 | 0.000 | 0.240 | 0.440 | 0.000 |
| describe_fuzzy Hit@5 | 1.000 | — | 1.000 | 0.900 | — |
| absent 拒答率 | **1.000** | 1.000 | 0.000 | 0.000 | 1.000 |
| constraint 满足率@5 | **1.000** | — | 0.820 | 0.840 | — |
| mood nDCG@5 | **0.974** | — | 0.356 | 0.895 | — |
| **三类合并 Hit@5** | **1.000** | — | 0.513 | 0.733 | 0.000 |

### test（74 条，**第三次使用**；见下方更正）

| 类型/指标 | **A Agent** | B0 | B1 | B2 | B3 |
|---|---|---|---|---|---|
| title Hit@5 | 1.000 | 0.733 | 1.000 | 0.933 | 0.000 |
| describe Hit@5 | 1.000 | 0.000 | 0.900 | 0.850 | 0.000 |
| lyric Hit@5 | 1.000 | 0.042 | 0.125 | 0.625 | 0.000 |
| absent 拒答率 | **1.000** | 1.000 | 0.067 | 0.000 | 1.000 |
| **三类合并 Hit@5** | **1.000** | — | 0.610 | 0.780 | 0.000 |

> **更正（test 使用次数）**：并非"只跑一次"。实际：v1（64 条）跑过一次；代码修改后追加 10 条（74）又跑过一次；`phase3loop` F-loop 修复（F1–F6）后**第三次**跑（上表即第三次；A 三类仍 1.0）。test 的多次使用是事实，如实记下。

## 5. 消融（dev 分层子集 36 条，一次性关一个模块）

| 关掉的模块 | title | lyric | describe | absent | constraint | mood |
|---|---|---|---|---|---|---|
| 无（baseline） | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| **意图解析**（原句直连 B2） | 1.000 | **0.250** | 1.000 | **0.000** | 0.867 | 0.756 |
| **歌词不绕融合/重排**（复现旧 bug） | 1.000 | **0.625** | 1.000 | 1.000 | 1.000 | 1.000 |
| **点名+歌手校验** | 1.000 | 1.000 | 1.000 | **0.333** | 1.000 | 1.000 |
| **拼错歌名 fuzzy** | **0.667** | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| 语言归一 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

**结论**：贡献最大的是**意图解析**（拿掉后 lyric/absent 崩），其次是**歌词类不走卡片重排**、**歌手校验**、**fuzzy 歌名**。语言归一在本子集上无差异（子集无语言条件查询）。

## 6. 已知局限

- **ASR 歌词不可检索**：170 首 ASR 歌词（C4）不进歌词检索，用户搜这些歌的歌词会搜不到。
- **评测集偏小**：dev 109 / test 74；constraint、mood、describe_fuzzy 仅 dev。
- **mood 标注不中立**：0/1/2 在 `semantic ∪ agent` 候选池上打的，两个被评方法都贡献了候选；完全中立需人工标签（`semantic_labels.jsonl` 仍空）。describe 类查询是照卡片摘要生成的，与向量卡片同源。
- **本机 CPU 推理**：单次查询延迟受模型加载/编码影响（未做 P95 延迟与 token 统计的正式测量）。
- **Lyric Hit@1** 0.83–0.88（Hit@5=1.0）：正确歌几乎总在前 5，但排不到第 1 的情况仍有约 1/6。
- 消融在 36 条子集上做（全量太慢），是相对量级。

## 7. 交付物

- 代码：`agent/core/{intent,recall,rank,verify,respond,pipeline,llm,ablate}.py`、`agent/tools/bm25.py`、`agent/app.py`（`POST /agent/search`、`/tool/{name}`）、`agent/cli.py`
- 评测：`agent/eval/{build_golden,build_golden_p1,run_eval,baselines,diagnose_stages,run_ablation,export_traces,eval_tools}.py`
- 评测集/标注：`golden_dev.jsonl`、`golden_test.jsonl`、`intent_labels.jsonl`、`semantic_labels.jsonl`
- 结果：本报告、`ablation.json`、`traces.json`、`DocsForMusicAgent/phase3_traces.md`、`data_pipeline/data/reports/p3_*.log`
- 单测：`pytest agent/tests`（21 passed）

复现：`python -m agent.eval.run_eval --file golden_test.jsonl --methods agent,b0,b1,b2,b3`。
