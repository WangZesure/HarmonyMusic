# agent/ — 阶段 2 工具层

8 个只读工具，统一 `{ok, data:{items}, error}`，供阶段 3 的 Agent 循环调用（契约 C7）。
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
