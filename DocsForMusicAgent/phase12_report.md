# 阶段 1 & 2 实现与测试报告

> 生成日期 2026-10-12 · 对应 `phase12.md`（阶段1、2）与 `contracts_v1.md`(C1–C8)、`between12.md`(数据库)。
> 运行环境：本机 MySQL 8.0.19(`mymusic`) + `D:\apps\tools\Conda\python.exe`(3.13，含 torch/sentence-transformers)；
> 数据流水线在 `D:\1Projects\HarmonyMusic\data_pipeline/`，Agent 工具层在 `agent/`。

## 0. 结论

| 阶段 | 状态 | 说明 |
|---|---|---|
| **阶段 1 数据底座** | ✅ 完成 | 500 首歌全部入库，链接可播放，歌词逐句对齐，向量库建成 |
| **阶段 2 工具层** | 🟡 基本完成 | 8 个工具全部实现、单测通过、歌词混合检索 Top-5 **0.94**、标签一致性 **100%**；**遗留 2 项**：`recommend_for_user` 双塔未在新曲库重训（现退化为标签偏好）；语义 nDCG 需人工标注 |

阶段 3（Agent 核心）、4（多轮）、5（接入 App）、6（评测迭代）**未开始**。

---

## 1. 交付物

### 1.1 阶段 1（`data_pipeline/`）
| 脚本 | 作用 | 产物 |
|---|---|---|
| `00_check_stream.py` | 1.0 可行性：Jamendo 地址能否播放 | `data/reports/00_stream_check.json` |
| `01_fetch_jamendo.py` | 1.1 拉候选池（vocal/instrumental × 标签） | `data/candidates.json` |
| `02_select.py` | 1.2 选 500 首 + 标签分层 | `data/selected.json` |
| `03_lrclib.py` | 1.3a 查 LRCLIB 现成同步歌词 | `data/lyrics.json` |
| `04_align.py` | 1.3b GPU：Demucs 分离人声 + whisper 强制对齐/ASR | `data/lrc/*.lrc` |
| `05_qc_lrc.py` | 1.4 LRC 质检 + 标准化（C4） | `data/lyrics_final.json` |
| `06_llm_tagging.py` | 1.5 DeepSeek 打标签 + 中文摘要 + 语种 | `data/tags.json` |
| `07_load_mysql.py` | 1.6 生成种子 SQL + 自检 SQL | `db/seed_v1.sql`、`db/check_v1.sql` |
| `08_build_index.py` | 1.7 bge-m3 向量索引 | `data/index/` |
| `tools/gpu.py` | 本地 SSH/SFTP 驱动 GPU 服务器（无 sshpass） | — |
| `tools/sql_selftest.py` | 临时库自测导入 + 跑 check_v1 + 删库 | — |

**阶段 1 三项交付物**：
- **能播的链接**：`songs.url = /stream/{id}`，Koa 新增 `GET /stream/:id` → 302 跳 Jamendo `tracks/file`。
- **对得上的歌词**：201 首可检索（9 首 LRCLIB 同步 + 192 首对齐），LRC 行数与源歌词 1:1、时间戳严格递增。
- **向量数据库**：`index/songs_v1`(500×1024) + `index/lyric_lines_v1`(6823×1024)，bge-m3、L2 归一化、余弦。

### 1.2 数据库（`db/`，14 表）
- `db/schema_v1.sql`（权威，每表先 DROP）；`db/seed_v1.sql`（07 生成，勿手改）；`db/check_v1.sql`（自检，期望值写在别名里）。
- 正式库 `mymusic` 实测行数：
  `songs=500, singers=328, song_meta=500, song_tags=5510, song_lyric_lines=11148, song_lists=37, list_songs=3155, admins=2, consumers=2, swipers=3, user_plays=0`
  歌词来源：`aligned=201, lrclib=9, asr=170, none=120`。
- 执行记录另见 `db/DELIVERY_v1.md`。

### 1.3 阶段 2（`agent/`）
```
agent/
├── config.py     models.py(C7 Filter/SongHit/ToolResult)   db.py(只读+线程内复用连接)
├── index.py(numpy + 惰性 bge-m3)   registry.py(schema生成/超时/日志)
├── tools/{basic,vector,personal}.py   ↑8 个工具
├── cli.py         app.py(FastAPI)         README.md
├── tests/test_tools.py                     eval/{eval_tools.py, make_labels.py}
```
8 个工具（契约 C7 固定名）：`search_by_keyword`、`search_by_lyrics`、`filter_by_tags`、`semantic_search`、`similar_songs`、`get_user_profile`、`recommend_for_user`、`get_song_detail`。

---

## 2. 测试方法

### 2.1 阶段 1
- **可行性**：`python data_pipeline/00_check_stream.py` — 直链与 `/tracks/file` 跳转链各拉 64KB，验证返回音频字节。
- **数据库**：`python data_pipeline/tools/sql_selftest.py` 在**临时库** `mymusic_selftest` 导入 schema+seed，跑 `check_v1.sql` 全部 16 条（每条约定的 0/期望值），再删库；通过后 `07_load_mysql.py --apply` 导入正式库。
- **Koa 不改表**：Koa 启动前后对 `songs/singers/song_meta/song_tags/song_lyric_lines` 做 `SHOW CREATE TABLE` 的 MD5 比对。
- **接口冒烟**：`curl` 首页歌单 / 单曲详情 / `/stream/:id`。

### 2.2 阶段 2
- **单元测试**：`python -m pytest agent/tests -q`。
- **工具评测** `python -m agent.eval.eval_tools --n-lyrics 100 --n-tags 50`：
  - A 歌词搜歌：从**索引覆盖的 201 首**里随机抽 100 句歌词，做一次扰动（改 1–2 字 / 删几个字 / 只留半句），看原歌是否进 Top-1/Top-5；三种方式对比：纯 LIKE、纯向量、混合(RRF)。
  - B 标签过滤：随机 50 组标签条件，工具的 SQL 结果与**另一种独立写法**(group-by HAVING)严格比对，要求完全一致。
  - C 语义检索：`eval/semantic_labels.jsonl` 人工标注后算 nDCG@5（**待人工标注**）。
  - 用 `python -m agent.eval.make_labels` 先打印每条查询的 top-10 候选，供人工勾选。
- **随机种子**固定（歌词 7 / 标签 11），结果可复现。

---

## 3. 测试结果

### 3.1 阶段 1
- `00_check_stream.py`：直链与跳转链均 `200 / audio/mpeg`，可拿到音频字节 → **方案成立**。
- `sql_selftest.py`：**16/16 全过**（正式库同样全过）。
- Koa `sync()` 前后 DDL 哈希一致 → **未改表结构**。
- `/stream/7359` → `302 Location: https://api.jamendo.com/v3.0/tracks/file/?client_id=…&id=7359&action=stream`；跟随后 `206 audio/mpeg`。
- `/getAllSongList` → 返回 37 个自动歌单（如「深夜」）；`/getSongDetail?id=7359`、`/getSingerDetail`、`/getSwiper` 均 200。

### 3.2 阶段 2
`pytest`：**9 passed**。

`eval_tools`（seed 固定）：

| 歌词搜歌方法 | Top-1 | Top-5 |
|---|---|---|
| 纯 LIKE（全文） | 0.26 | 0.30 |
| 纯向量 | 0.74 | 0.88 |
| **混合 RRF** | **0.85** | **0.94** |

- 标签过滤一致性：**50 组 0 不一致（100%）**。
- 语义 nDCG@5：**未评定**（缺人工标注文件）。

**阶段 2 验收对照**：所有工具单测 ✅ · 歌词混合 Top-5 ≥90% → 0.94 ✅ · 命令行可手动调用每个工具 ✅。

---

## 4. 过程中修的关键问题

1. **MySQL 8.0.19 崩溃（重要）**：`song_lyric_lines.text` 上的 ngram `MATCH ... AGAINST` 会让 `mysqld` abort（`init_search@Item_func_match`，崩溃日志 `WZS.err` 已确认）。服务一崩就表现为「连接被拒」。**修复**：全仓库**弃用 `MATCH`，改用 `LIKE`**（`agent/db.py`、歌词工具、评测、`check_v1.sql`）；未改表结构，索引可后续按需删除。
2. **连接风暴**：工具原每次查询新建连接，评测爆发式调用时耗尽端口 → 改为**每线程复用一条连接**。
3. **抽样 bug**：评测最初从全部 11148 行取样，而索引只覆盖 201 首（6823 行）→ 改为只从索引覆盖的歌取样；扰动也从「同时截断+删 15%」改为**每次只用一种**（更贴近真实）。
4. **数据清洗**：名称/歌词 `html.unescape`（`&amp;` 20→0）；字段超长从静默截断改为**硬报错**（1 首 772 字超长行 → QC 剔除其歌词）。
5. **C4 对齐契约**：`04_align.py` 打开 `original_split=True` 并输出每行置信度；`common.normalize_lrc` 保证行 1:1、时间戳严格递增（含去重 +0.01s）。
6. **Koa 旧 bug**：三个分页接口把 query 字符串直接当 `LIMIT` → 改为 `parseInt`。
7. **依赖坑（GPU 服务器）**：torchaudio 版本需与 torch 对齐；`huggingface.co` 被墙 → Demucs/whisper/ST 全程设 `HF_ENDPOINT=https://hf-mirror.com`（否则每首卡 177s）；torch<2.6 无法加载 bge-m3 的 `.bin`。

---

## 5. 遗留 / 未完成

1. **`recommend_for_user` 双塔重训**（phase12 §2.2）：计划用 `mymusic_data_generator.py` 在新曲库上造合成播放（`user_plays`, `source='synthetic'`）再重训；当前实现退化为「按用户标签偏好推荐 + 候选重排」。**待做**。
2. **语义 nDCG@5 人工标注**（§2.3）：需人工勾选 `agent/eval/semantic_labels.jsonl`（`make_labels.py` 可先出候选）。**待你操作**。
3. **可选**：删除 3 个 ngram FULLTEXT 索引（需改 `db/schema_v1.sql`，按规矩需先批准）；或升级 MySQL 到不复现该 bug 的版本。
4. 阶段 3 起（Agent 核心 / 多轮 / 接入 App / 评测迭代）尚未开始。

---

## 6. 复现命令

```bash
# 阶段1（本地；已跑完，产物已在 data/ 与 db/）
python data_pipeline/03_lrclib.py
python data_pipeline/05_qc_lrc.py
python data_pipeline/06_llm_tagging.py
python data_pipeline/07_load_mysql.py --apply
python data_pipeline/tools/sql_selftest.py

# 阶段2（用带 torch 的解释器）
D:\apps\tools\Conda\python.exe -m pytest agent/tests -q
D:\apps\tools\Conda\python.exe -m agent.cli list
D:\apps\tools\Conda\python.exe -m agent.cli semantic_search '{"query":"适合深夜的伤感歌","limit":5}'
D:\apps\tools\Conda\python.exe -m agent.eval.eval_tools --n-lyrics 100 --n-tags 50
D:\apps\tools\Conda\python.exe -m uvicorn agent.app:app --port 8000

# 服务端步骤（GPU 服务器）
python data_pipeline/tools/gpu.py setup
python data_pipeline/tools/gpu.py run "…"   # 见 data_pipeline/README.md
```
