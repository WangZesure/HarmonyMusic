# 通用 Agent 是怎么做的：一个典型项目拆解 + 对本项目的映射

> 面向 `agent/` 现在的实现（固定流水线 intent → recall → rank → verify → respond，8 个只读工具，DeepSeek 驱动意图解析）。
> 目的：说清"一般 agent 由什么组成、需要做哪些设计"，并给出一个可以照着读的样板项目。

---

## 0. 一句话结论

Agent = **LLM + 工具 + 循环 + 状态**。真正难的不是"调大模型"，而是四件事：

1. **什么时候停**（没有终止判据的 agent 就是无限循环）
2. **上下文怎么压**（工具返回值就是你的花钱大户）
3. **错了怎么办**（工具超时、参数非法、召回了不存在的歌）
4. **怎么证明它变好了**（没有评测的 agent 项目，改动全靠感觉）

你现在的 `agent/` 在 1、2、3 上都做得不错，第 4 项（golden set + 消融 + baseline）反而**强于大多数开源 agent 项目**。

---

## 1. 先分清三档：workflow / 单 agent / 多 agent

上表是**我在 Anthropic 二分法（workflow / agent）基础上补了一行"多 agent"的归纳**，不是原文分类。Anthropic 原文只有 workflow 与 agent 两类，也没有出现 "multi-agent" 这个词；多 agent 在它的框架里是 workflow 的 `orchestrator-workers` 模式。

| 形态 | 谁决定下一步 | 优点 | 代价 | 典型场景 |
|---|---|---|---|---|
| **Workflow** | 代码写死 | 可预测、可调试、便宜、延迟稳定 | 不灵活 | 步骤能事先枚举 |
| **单 Agent（ReAct）** | 模型每轮决定调哪个工具 | 灵活，能处理长尾 | 步数不可控、成本不可控 | 任务路径无法预先枚举 |
| **多 Agent** | 上层 agent 派活给下层 | 能分解 + 并行 | 状态同步、错误传播、成本翻倍 | 任务可自然切分成独立子任务 |

**判据：任务的路径能不能事先枚举。能枚举就别上 loop。**

这句是我对原句的压缩，原文是："Agents can be used for open-ended problems where it's difficult or impossible to predict the required number of steps, and where you can't hardcode a fixed path." 对应的 workflow 一句是："workflows offer predictability and consistency for well-defined tasks"。

一个容易读歪的地方：判定对象是**步数与路径**，不是"子任务内容是否固定"。原文的 `orchestrator-workers` 就属于 workflow，但它的子任务是运行时由 LLM 决定的（"subtasks aren't pre-defined, but determined by the orchestrator"）。所以"步骤编排固定、步骤内的判断可以交给模型"是合法组合 —— 你的 `recall → rank → verify` 正是这种。

你的搜歌 agent 是 **(1) workflow**：意图解析是唯一交给 LLM 自由判断的节点，后面召回/排序/校验/应答都是确定性代码。这个选择是对的 —— 搜歌的路径本来就能枚举，让 LLM 自由决定调哪个工具只会引入不确定性。

---

## 2. 一个 agent 的六个部件

### 2.1 工具层 —— 契约比实现重要

工具不是"函数"，是**给模型看的 API 文档**。每个工具必须有四样东西：

```
名字 + 一句话描述（模型靠这个选工具）
参数 schema（模型靠这个填参数，用 Pydantic/JSON Schema 定，不要手写 dict）
返回结构（统一信封，不要每个工具一个样）
错误码（超时/参数非法/不可用/内部错误，分开）
```

标准形态（你已经在 `agent/registry.py` 里做完了）：

```python
@tool(name="filter_by_tags", args_model=FilterArgs, description="按情绪/场景/曲风过滤曲库")
def filter_by_tags(args, ctx) -> ToolResult: ...
```

三个容易被忽略的约定：

- **只读优先**。写操作（下单、改状态）必须有人类确认或幂等键，否则 agent 一次幻觉就改坏数据。你的 8 个工具全是只读，这是最省心的设计。
- **超时 + 降级**。`registry.run()` 里的 `fut.result(timeout=...)` 就是正确做法 —— 工具卡住时返回 `TIMEOUT` 给模型，而不是让整个请求挂死。
- **返回值是一等公民**。`get_song_detail` 只返回摘要、不含歌词全文，这个决定比它看起来重要：工具返回的每一个 token 都会进上下文，并在此后每一轮里重复付费。

### 2.2 循环控制器 —— 四个闸门一个都不能少

最朴素的 agent loop 就这么长：

```python
messages = [system_prompt] + history
for step in range(MAX_STEPS):                      # 闸门 1：硬迭代上限
    resp = llm.bind_tools(tools).invoke(messages)
    messages.append(resp)
    if not resp.tool_calls:                        # 闸门 2：模型自己收手
        break
    for call in resp.tool_calls:
        result = run_tool_safely(call)             # 闸门 3：异常不外抛
        messages.append(ToolMessage(result, tool_call_id=call.id))
    if any(c.name == "finish" for c in resp.tool_calls):  # 闸门 4：显式终止工具
        break
```

- **闸门 1（计数）**：计数要放在 state 里，靠 prompt 说"别调太多次"是无效的。
- **闸门 2**：模型不调工具就是自然结束 —— 但模型有时会"假装结束"，所以还需要闸门 4。
- **闸门 3**：工具抛异常必须转成 `ToolMessage` 喂回去，让模型自己决定换路，而不是 500。
- **闸门 4**：让模型显式声明"够了"。这是所有严肃 agent 项目的标配（见下节 `ResearchComplete`）。

### 2.3 状态 —— 用状态机，不要用对话历史

低级写法：把所有东西都塞进 `messages`，靠模型记。
工程写法：`messages` 只装"模型需要看见的"，其余是结构化字段。

样板项目里长这样（`open_deep_research/src/open_deep_research/state.py`）：

```python
class AgentState(MessagesState):
    supervisor_messages: Annotated[list, override_reducer]
    research_brief: Optional[str]
    research_iterations: int = 0
    notes: Annotated[list[str], override_reducer] = []
    raw_notes: Annotated[list[str], override_reducer] = []
    final_report: str
```

两个细节值得抄：

- **`Annotated[..., reducer]`**：LangGraph 的 reducer 决定"新值如何合并进旧值"（追加 / 覆盖）。没有 reducer 的并行写入会互相覆盖。
- **计数器独立成字段**（`research_iterations`），而不是数 messages 里有多少条 assistant。

你的 `agent/core/session.py` 是同一个思路的轻量版：

```python
@dataclass
class Session:
    intent: Intent | None     # 当前约束（"更伤感一点"就是改这个对象）
    seen: list[int]           # 已展示的歌曲（"换一首"就是排除这些）
    turns: int
```

"把约束存成结构化对象、把多轮操作实现为对对象的编辑" —— 这个设计比"把对话历史全喂给 LLM 让它重解析"稳定得多，也正是它让 `换一首 / 更X一点 / 不要X` 变成正则能搞定的确定性代码。

### 2.4 上下文管理 —— 三层压缩

| 层 | 手段 | 样板项目 | 你的对应 |
|---|---|---|---|
| 源头 | 工具只返回摘要 | `search` 返回片段不返回全文 | `get_song_detail` 不返歌词 |
| 子任务 | 子 agent 输出压缩摘要再回传 | `compress_research()` | —— |
| 全局 | 硬预算 + 裁剪策略 | gpt-researcher 的 25k 词上限 + 逆序保留最新 | —— |

样板项目里最值得学的一招是**上下文隔离**：子研究员的原始 messages **不出子图**，只把 `compressed_research` 作为一条 `ToolMessage` 交回给上层。上层永远看不到中间过程，上下文就不会随探索轮数线性膨胀。

### 2.5 规划 —— 静态优先，动态兜底

- **静态规划**：步骤写死（你的 pipeline）。
- **动态规划**：planner 产出任务列表 / supervisor 派子任务（样板项目）。
- **混合**：大部分流程写死，只有少数"歧义大"的节点交给模型 —— 比如你的 `verify.LYRIC_SOURCE`：先让模型判断"这句歌词出不出自某首已知歌"，再决定走反查分支还是走置信度阈值分支。**一个显式分支，比让模型自由规划更稳，也比纯阈值更准。**

### 2.6 记忆 —— 三种时间尺度

| 尺度 | 存什么 | 你的实现 | 通用做法 |
|---|---|---|---|
| 单轮 | 中间结果 | 函数内 | state |
| 会话内 | 当前约束、已展示 | `SessionStore`（30 min TTL，内存） | Redis / checkpoint |
| 跨会话 | 用户偏好 | `get_user_profile`（收藏 + 播放聚合） | 画像表 / 向量库 |

会话状态**必须能持久化**。你现在是单进程 dict，重启即失忆；接口对了，换 Redis 只是改一个类。

### 2.7 护栏与评测 —— 决定它算不算工程

- **护栏**：输入校验、工具参数校验、输出 schema 校验（`with_structured_output` / JSON mode）、成本与轮数上限、`verify` 阶段的拒答（"曲库里没有《X》这首歌"就是一条护栏）。
- **可观测**：每个阶段留 trace（你的 `pipeline._run` 里 `trace.append({"step": "intent", ..., "ms": ...})` 正是最实用的形式：**阶段名 + 输入 + 输出规模 + 耗时**）。
- **评测**：golden set + baseline + 消融。这一点上样板项目反而不如你 —— `open_deep_research` 只有 `tests/evaluators.py` 和一份 LangSmith 导出脚本；你有 `golden_dev/test/holdout`、B0–B3 baseline、`run_ablation`、`diagnose_stages`。

---

## 3. 推荐精读的典型项目

### 3.1 主例：`langchain-ai/open_deep_research`（⭐12.7k，Python/LangGraph）

**为什么是它**：结构小到一个下午能读完，却把 agent 的所有关键设计都覆盖了 —— 澄清、规划、双层循环、并发、上下文隔离、终止判据、最终综合。

目录只有四个文件：

```
src/open_deep_research/
├── state.py            # 状态 schema + reducer + 结构化输出模型
├── deep_researcher.py  # 全部节点 + 图装配（718 行）
├── configuration.py    # 所有上限都是可配置字段
└── prompts.py          # 每个节点的提示词
```

**图结构**（`deep_researcher.py:708-716`）：

```
START → clarify_with_user ─┬─→ write_research_brief → research_supervisor → final_report_generation → END
                           └─→ END（用户需要澄清时直接返回问题）
```

`research_supervisor` 是个**子图**，内部是双层循环：

```
           ┌──────────────── 最多 6 轮（max_researcher_iterations）
           ↓
      supervisor ──→ supervisor_tools ──┬─→ 结束（ResearchComplete / 无 tool_call / 超轮数）
   （LLM 决策）    （执行派活）          │
                                        └─→ 并发展开 N 个 researcher 子图
                                              ↓
                     researcher ──→ researcher_tools ──┬─→ researcher（最多 10 次工具调用）
                        （LLM+工具）                    └─→ compress_research → 压缩摘要回传
```

**七个可以直接抄的设计点**：

1. **`think_tool`**（定义在 `utils.py:220`，在 `deep_researcher.py:270` 被处理）。一个不产生任何副作用、只回 "Reflection recorded" 的假工具。模型调用它 = 被迫先写一段计划再行动，对长任务效果显著。**零成本，高收益。**
2. **`ResearchComplete`**（`state.py`）。显式终止工具，等价于你的 pipeline 里"rank 结果足够好就提前退出"。没有它，模型不知道"够了"是个可表达的动作。
3. **两个计数器分两层**：`research_iterations`（派活轮数，默认 6）和 `tool_call_iterations`（ReAct 步数，默认 10）。**外层管广度，内层管深度**，而不是一个大循环数到底。
4. **`max_concurrent_research_units=5`**（`deep_researcher.py:295`）：超出的调用不执行，而是回一条 `ToolMessage` 告诉模型"你超并发了，请分批"。**超限也要给模型可读的反馈**，而不是静默丢弃。
5. **`Command(goto=..., update=...)`**（`deep_researcher.py:178`）：节点自己决定下一个节点，并把 state 增量一起返回。比"节点返回值 + 外部条件边"更紧凑，也更容易做"提前结束"。
6. **压缩节点兜底**（`compress_research`，`deep_researcher.py:511`）：子图结束时强制把 `raw_notes` 压成结构化摘要，并且带重试（`while synthesis_attempts < max_attempts`）。**压缩失败不能让整条链路失败。**
7. **全参数化**（`configuration.py`）：所有上限、模型、API 都是 `Field(default=...)`，可以在运行时按请求覆盖 —— 这让消融实验变成改一个配置项，而不是改代码。

**它没做而你做了的**：golden set、baseline 对比、阶段级诊断、置信度阈值、拒答。它靠人工看报告质量。

### 3.2 对照：`assafelovic/gpt-researcher`（⭐30.7k，Python）

**为什么看它**：它代表另一条主流路线 —— **"多 agent" 其实是"流水线 + 几个 LLM 角色"**，跟你现在的结构更接近。

```
GPTResearcher (agent.py)
├── skills/researcher.py       # ResearchConductor：生成子话题并发检索
├── skills/browser.py          # BrowserManager：抓网页、去重、并行
├── skills/context_manager.py  # ContextManager：压上下文、向量检索
├── skills/curator.py          # SourceCurator：来源筛选排序
├── skills/writer.py           # ReportGenerator：写最终报告
└── skills/deep_research.py    # 递归增强：breadth=4, depth=2, concurrency=2
```

可借鉴的两点：

- **`breadth / depth / concurrency` 三个旋钮**（`skills/deep_research.py:60`）：用参数控制 agent 的"努力程度"，而不是靠 prompt 求它。对应你的场景可以是 `recall_breadth`（并行几条召回路线）与 `relax_depth`（最多放宽几轮）。
- **明确的上限常量**：`MAX_CONTEXT_WORDS = 25000` + 逆序裁剪（保留最新的）。粗暴但有效。

它也暴露了这类项目的通病：**没有下线指标**。哪个 skill 起了正面作用、哪个是累赘，只能靠感觉。

### 3.3 想看"设计原则"而不是代码

- **`humanlayer/12-factor-agents`**（⭐27k）：12 条原则，第一条最反直觉 —— *"Natural language to tool calls"*，最后一条 *"Compact errors into a few tokens"*。整篇的核心主张：**不要造"自主 agent"，造"拥有良好工具的确定性软件，只在必须判断的节点问 LLM"**。你的 pipeline 正是这个主张的实现。
- **Anthropic《Building effective agents》**：workflow vs agent 的三分法出处，最该先读的一篇。
- **`openai/openai-agents-python`**（⭐30.6k）：如果你想要框架级参考（handoff、guardrail、tracing 的 API 长什么样）。

---

## 4. 对照你的 `agent/`：差距在哪

| 维度 | 通用项目 | 你的实现 | 判断 |
|---|---|---|---|
| 控制流 | LLM 自由循环 | 固定 5 段流水线 + LLM 只在 intent | ✅ 适合场景 |
| 工具契约 | Pydantic + 统一信封 | 同（`registry.py`） | ✅ 已达标 |
| 终止判据 | 显式 finish 工具 + 轮数上限 | 流水线天然终止 + `RELAX_MAX` | ✅ 够用 |
| 上下文 | 三层压缩 | 只有"源头摘要"这一层 | ⚠️ 可补 |
| 会话状态 | 持久化 / checkpoint | 内存 dict，重启失忆 | ⚠️ 可补（换 Redis） |
| 规划 | supervisor 动态派活 | 静态 + 少量显式分支 | ✅ 恰当 |
| 可观测 | LangSmith / trace | 自建 trace（阶段/规模/耗时） | ✅ 足够 |
| 评测 | 多数项目缺失 | golden set + 消融 + baseline | ✅ 领先 |

**如果只做三件事**（按性价比）：

1. **把 `Session` 换到 Redis**，接口不变。现在重启一次多轮上下文就没了，这是唯一一个"实现明显拖后腿"的点。
2. **给 `rank` 阶段加一个显式"够了"判据**，复用 `ResearchComplete` 的思路：让 `verify` 输出 `should_stop` 而不是只用阈值间接表达，放宽序列（`relax_sequence`）就有明确的终止理由可记录。
3. **trace 里补 token 数与工具耗时**，让 `diagnose_stages` 能回答"钱花在哪个阶段"，而不只是"哪个阶段慢"。

---

## 5. 参考

- langchain-ai/open_deep_research — https://github.com/langchain-ai/open_deep_research
- assafelovic/gpt-researcher — https://github.com/assafelovic/gpt-researcher
- humanlayer/12-factor-agents — https://github.com/humanlayer/12-factor-agents
- openai/openai-agents-python — https://github.com/openai/openai-agents-python
- Anthropic, *Building effective agents* — https://www.anthropic.com/engineering/building-effective-agents

> 本机访问 GitHub 需走代理：`export HTTPS_PROXY=http://127.0.0.1:7890`（clash 7890）。
