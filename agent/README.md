# agent/ — 搜歌 Agent

## v6 Run loop（当前入口）

`POST /agent/chat`（非流式）和 `POST /agent/chat/stream`（SSE）都走
`agent/core/agent_loop.py` 的单 agent Run：

```
Input → Model → Tools → tool results → … → finish / ask_user / text
```

- **工具面**（`agent/tools/agent_tools.py`）：`search_songs`、`refine_candidates`、
  `song_detail`、`user_profile`、`remember_preference`、`ask_user`、`finish`。
  标签只接受 C3 词表/别名（`data_pipeline/vocab/tags_zh.yaml`），表外标签返回
  `INVALID_ARGUMENT` 并点名，不再静默丢弃；`languages` 统一归一到 ISO 639-1。
- **会话**：MySQL `agent_sessions`（`agent/session_store.py`；建表脚本
  `db/migrations/001_agent_sessions.sql`）。按 `{uid}:{session_id}` 存一份
  `state_json`，含 `constraints / seen / last_shown / last_search / pending_question / last_turn_id`。
  `last_search.turn_id` 标记候选来自哪一轮：`refine_candidates` 只在本轮候选上做减法，
  上一轮的候选会被拒绝（要重搜请用 `search_songs(exclude_tags=…)`）。
- **长期偏好**：MySQL `user_preference`（`remember_preference` 写入，`search_songs` 内部生效）。
- **Instructions**：`agent/prompts/system.md`。
- **输出**：`type = songs / clarify / refusal / text`，可带 `degraded{reasons,at_step,partial}`。
- **上下文**：`recent` 只保留最近 8 条；`state` 是结构化笔记。Compaction v1 是确定性的
  （不额外调 LLM）；单次工具结果超过 8k 字符会截断（tool result clearing）。
- **限制**：5 步、2 次 `search_songs`、45s、20k tokens；单个工具 20s，单次 LLM 30s。
- **鉴权**：设置 `AGENT_KEY` 时，Koa 会带 `X-Agent-Key`；不设置则假定 loopback。
  未登录 / token 过期时，Koa 返回 SSE `event: error`（“请先登录”），不会静默断开。

### LangGraph 原型（可选引擎）

`agent/core/graph_loop.py` 是同一套 Run 的 StateGraph 版本：复用
`agent_tools.py`、`system.md`、`session_store.py`，只替换循环调度。

```
START → guard ──(budget ok)──→ llm ──(tool_calls)──→ tools ──→ guard
            └──(cancel/limit)──→ finalize   └──(no calls)──→ finalize
tools ──(finish / ask_user / degrade)──→ finalize → END
```

- 节点：`guard`（取消/deadline/token/步数）、`llm`（调模型）、`tools`（共享
  `handle_tool_calls`）、`finalize`（degraded、写回会话）。
- 条件边：`guard → llm | finalize`，`llm → tools | finalize`，`tools → guard | finalize`。
- Fast Path、`turn_id` 幂等、会话锁仍在图外（确定性例外 + 存储层）。
- 领域状态（allowed_ids、candidates、filters、pending）放在 `RunContext`；
  工具派发、预算检查、落库与手写版共用 `agent/core/run_support.py`。

跑同一套 golden：

```bash
D:\apps\tools\Conda\python.exe -m agent.eval.run_agent_eval --engine graph --min-pass 0.9
```

HTTP 演示切换：`AGENT_ENGINE=graph ... uvicorn agent.app:app --port 8000`（默认 `hand`）。
完整的节点/边映射与对比见 `agent/GRAPH_PROTOTYPE.md`。

首轮结果：30/30，P95 6.29s，degraded 0（与手写引擎一致）。
Parity 单测在 `agent/tests/test_graph_loop.py`；图可用
`graph_loop._graph().get_graph().draw_mermaid()` 导出。

运行（单 worker）：

```bash
D:\apps\tools\Conda\python.exe -m uvicorn agent.app:app --port 8000
```

首次启动后端口立即可用；bge-m3 + bge-reranker 在后台预热，首个请求最多等待约 50s
（Koa 的超时是 150s、App 读超时 180s，覆盖 50s 预热 + 45s Run）。

测试与评测：

```bash
D:\apps\tools\Conda\python.exe -m pytest agent/tests -q
D:\apps\tools\Conda\python.exe -m agent.eval.build_agent_golden
D:\apps\tools\Conda\python.exe -m agent.eval.run_agent_eval --min-pass 0.9
D:\apps\tools\Conda\python.exe -m agent.eval.run_agent_eval --kind select --limit 2 --baseline
# 真实链路烟雾测试（需先起 Agent :8000 和 Koa :3000；每次用新会话）
D:\apps\tools\Conda\python.exe -m agent.e2e_smoke
```

---

## 阶段 2 工具层

8 个只读工具，统一 `{ok, data:{items}, error}`，供 Agent 循环调用（契约 C7）。
工具参数用 Pydantic 定义，自动生成 OpenAI/DeepSeek function-calling schema。

## 运行环境（重要）

embedding 工具需要 `sentence-transformers` + `bge-m3`，且工具要读本机 MySQL。
本机默认 `python` 是 3.14（无 torch）。**用装了 torch 的解释器运行**，例如：

```
D:\apps\tools\Conda\python.exe -m agent.cli list
D:\apps\tools\Conda\python.exe -m uvicorn agent.app:app --port 8000
```
（该解释器已装 torch / sentence-transformers / fastapi / pydantic / pymysql；
首次跑 embedding 工具会从 hf-mirror 下 bge-m3 ~2.3GB 到 `~/.cache/huggingface`。）

## 工具

| 工具 | 依赖 |
|---|---|
| `search_by_keyword` | MySQL FULLTEXT(ngram) + LIKE 兜底 |
| `filter_by_tags` | MySQL，Filter 取值受 C3 词表约束 |
| `get_song_detail` | MySQL（只返回摘要，不含歌词全文） |
| `get_user_profile` | MySQL（收藏+播放聚合；user_id 由运行时注入） |
| `recommend_for_user` | 标签偏好推荐 / 候选重排（双塔重训待做） |
| `semantic_search` | bge-m3 + `index/songs_v1` |
| `similar_songs` | bge-m3 + `index/songs_v1`（排除同歌手） |
| `search_by_lyrics` | FULLTEXT + `index/lyric_lines_v1`，RRF 融合 |

## 调试

```bash
python -m agent.cli list
python -m agent.cli schemas
python -m agent.cli filter_by_tags '{"filters":{"moods":["伤感"]},"limit":3}'
python -m agent.cli semantic_search '{"query":"适合深夜的伤感歌","limit":5}'
python -m agent.cli get_user_profile '{}' --user 1
python -m pytest agent/tests -q
python -m agent.eval.eval_tools --n-lyrics 100 --n-tags 50
```

## 评测（2.3）

- `eval/eval_tools.py`：歌词搜歌三法对比（全文/向量/混合）、标签过滤一致性、语义 nDCG@5。
- 语义 nDCG 需人工标注 `eval/semantic_labels.jsonl`（每行 `{query, relevant_song_ids:[...]}`）。
