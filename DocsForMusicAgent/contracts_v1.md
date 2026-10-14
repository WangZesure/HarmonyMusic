# 搜歌 Agent 契约 v1（阶段 1、2 交付时冻结）

> 冻结 = 后续阶段直接依赖，改动必须经负责人批准、版本号 +1，并在本文末尾"变更记录"写明影响了哪些阶段。
> 本文只冻结"跨阶段共享的东西"。Agent 的 Prompt、会话状态内部结构、SSE 事件格式**现在不冻结**，分别在阶段 3、4、5 开工时再定。

| 编号 | 契约 | 主要消费方 |
|---|---|---|
| C1 | 歌曲 / 歌手 ID | 全部阶段 |
| C2 | 数据库表结构 | 阶段 2 工具、阶段 4 偏好、阶段 5 客户端 |
| C3 | 标签词表 | 阶段 2 工具 enum、阶段 3 意图解析、阶段 6 评测 |
| C4 | LRC 歌词格式 | 阶段 2 歌词搜歌、阶段 5 滚动歌词 |
| C5 | 音频 / 图片地址 | 阶段 5 客户端播放 |
| C6 | 向量索引 | 阶段 2 检索、阶段 3 运行时查询编码 |
| C7 | 工具接口 | 阶段 3 Agent 循环、阶段 4 多轮、阶段 6 评测 |
| C8 | 评测集与合成数据 | 阶段 2 / 6 评测、双塔重训 |

---

## C1. ID

- `songs.id` **直接等于 Jamendo track id**（当前范围 341–2343127，INT 足够），不用自增。
- `singers.id` **直接等于 Jamendo artist_id**。
- 重跑任何一步流水线，ID 都不能变。评测集、收藏、合成播放记录、双塔映射（`song_{id}`）、向量索引都按这个 ID 引用。
- 旧的 10 首种子歌曲和 `recommend/sql_scripts/mymusic/` 里的旧数据整体废弃，不与新 ID 混用。
- 合成用户的 ID 从 `100000` 开始，和真实注册用户隔开。

## C2. 数据库表

完整建表语句见 `db/schema_v1.sql`，它取代 `music.sql` / `database_init.sql`。导入顺序：`schema_v1.sql` → `seed_v1.sql`（由 07 生成）→ 启动 Koa。

除下面四张表外，现有表的变动：`singers.name` 放宽到 128（Jamendo 艺人名最长 64）；`songs.name` 放宽到 128，`songs.lyric` 改为 mediumtext；`collects` 加 `(user_id, song_id)` 唯一键；`user_plays` 正式纳入，新增 `source`（`real` / `synthetic`）字段；`songs`、`singers` 的歌名和艺人名加 ngram 全文索引。另外，Jamendo 的名称里有 HTML 实体（例如 `&amp;`），07 入库前要先反转义。

### `songs`（沿用现有表结构，字段含义更新）

| 字段 | 含义 |
|---|---|
| `id` | 见 C1 |
| `singer_id` | 见 C1 |
| `name` | Jamendo 原名，不翻译 |
| `introduction` | 阶段 1.5 生成的中文一句话摘要，≤ 255 字符 |
| `pic` | 封面的 https 绝对地址（见 C5） |
| `lyric` | 标准化 LRC 全文（见 C4）；纯音乐为空串 |
| `url` | `/stream/{id}`（见 C5） |

### `song_meta`（新增，与 `songs` 一对一）

单值的客观事实放这里，**不放进标签表**。

| 字段 | 类型 | 取值 |
|---|---|---|
| `song_id` | INT PK | 见 C1 |
| `duration_s` | INT | 秒 |
| `vocal` | ENUM | `vocal` / `instrumental`（来自拉取时的查询条件，不用响应里的字段） |
| `language` | VARCHAR(8) | ISO 639-1 两字母代码（`en`、`fr`……）；纯音乐填 `zxx`；未知填 `und` |
| `speed` | ENUM NULL | `verylow` / `low` / `medium` / `high` / `veryhigh` |
| `release_date` | DATE NULL | |
| `license_url` | VARCHAR | CC 协议链接，客户端展示署名时要用 |
| `share_url` | VARCHAR | Jamendo 歌曲页 |
| `lrc_source` | ENUM | `lrclib` / `aligned` / `asr` / `none` |
| `lrc_quality` | FLOAT NULL | 0–1，由 05 质检给出 |
| `lyric_searchable` | BOOL | 这首歌的歌词是否进入歌词检索，规则见 C4 |
| `pipeline_version` | VARCHAR | 生成这条数据的契约版本，如 `v1` |

### `song_tags`（新增，多值的主观标签）

| 字段 | 说明 |
|---|---|
| `song_id` | |
| `tag_type` | ENUM `genre` / `mood` / `scene` |
| `tag` | 中文标签原文，必须在 C3 词表内 |
| `source` | ENUM `jamendo`（规则映射）/ `llm`（DeepSeek） |

主键 `(song_id, tag_type, tag, source)`，索引 `(tag_type, tag)`。两种来源都保留，工具默认取并集，评测时可以分开做消融。

### `song_lyric_lines`（新增，歌词逐行）

| 字段 | 说明 |
|---|---|
| `song_id`, `line_no` | 主键；`line_no` 从 0 开始 |
| `start_ms` | 本行开始时间，毫秒 |
| `end_ms` | 下一行的 `start_ms`；最后一行取歌曲时长 |
| `text` | 本行歌词 |

建 `FULLTEXT(text) WITH PARSER ngram`。**`songs.lyric` 和本表必须由同一个 LRC 解析函数生成**（放在 `common.py`），保证两边一致。

时间单位约定：数据库和工具输出里一律用**整数毫秒**；只有 LRC 文本里用 `[mm:ss.xx]`；时长字段用秒，字段名带 `_s` 后缀。

## C3. 标签词表 v3

在 `vocab/tags_zh.yaml` 的 v2 基础上修改：

1. **删除 `voice` 类**。"有人声 / 纯音乐"放到 `song_meta.vocal`，语种放到 `song_meta.language`。原因是它们是单值事实，混在多值标签里会让过滤语义含混。
2. **只保留 `genre` / `mood` / `scene` 三类**。标签值就是中文字符串，数据库、工具 enum、Prompt 里用的都是同一个字面值，不用英文 key。
3. **覆盖门槛**：06 打完标签后，每个标签至少要有 **5 首歌**。不够的删掉，或者降级成别名指向近义的标签。按现有的 Jamendo 先验，`孤独`、`失落`、`甜美`、`紧张` 目前是 0 首，`愤怒` 只有 3 首，要在 06 之后重新统计再决定去留。
4. **新增 `aliases`**：把用户的口语说法映射到标准标签，比如 `emo → 伤感`、`丧 → 忧郁`、`嗨 → 燃`、`chill → 慵懒`、`助眠 → 睡前`。阶段 3 意图解析直接使用。
5. **新增 `language_display`**：`en → 英文`、`fr → 法文`……、`zxx → 纯音乐`、`und → 未知`，供展示和意图解析用。
6. `jamendo_*_map` 两张映射表保留，用来生成 `source=jamendo` 的标签。

## C4. LRC 格式

- UTF-8 编码，LF 换行。
- 每行严格是 `[mm:ss.xx]歌词`，**一行只有一个时间戳**，按时间升序排列。
- 不允许元信息行（`[ar:]`、`[ti:]` 等），不允许只有时间戳没有文字的行（现有 9 首 LRCLIB 歌词里有 15 行这种，要删掉）。
- 第一个时间戳 ≥ 0，最后一个时间戳小于歌曲时长。
- **`aligned` 来源的歌：LRC 的行必须和清洗后的歌词逐行一一对应。** 当前 04 的冒烟测试里，1537329 这首原歌词 41 行，对齐后只剩 27 行，说明 stable-ts 自己重新切分了句子。需要在 `model.align()` 里打开保留原始分行的选项（stable-ts 的 `original_split=True`），并在 05 中校验行数一致。
- `lyric_searchable` 规则：
  - `lrclib` 和 `aligned`，且 `lrc_quality` ≥ 阈值：设为 true；
  - `asr`（Whisper 转写，文字本身可能有错）：默认设为 false，进入歌词检索之前需要单独评估；客户端（阶段 5）显示"歌词由 AI 识别"。

## C5. 音频与图片地址

- `songs.url` 存成 `/stream/{id}`。Koa 新增 `GET /stream/:id` 接口，302 跳转到 Jamendo 的 `tracks/file/?client_id=…&id=…&action=stream`，`client_id` 从 `.env` 读取。
  - 这样 `client_id` 不会写进数据库，也不会写进提交到仓库的种子 SQL（目前 00 生成的地址里就带着它）；
  - 以后换 key 或换音频源，不用改任何数据。
- `songs.pic` 存 https 绝对地址。
- 客户端统一用 `resolveUrl()` 处理地址：
  - 以 `http(s)://` 开头：直接使用；
  - 以 `/` 开头：拼上 `Config.SERVER`；
  - 其他情况：按旧逻辑拼 `Config.SERVER_URL`，兼容老数据。
- 待验证：鸿蒙 AVPlayer 能不能跟随 302 跳转，需要真机测试一次。不行的话，`/stream/:id` 改成由 Koa 中转音频流，接口地址不变。

## C6. 向量索引

| 项 | 约定 |
|---|---|
| 模型 | `BAAI/bge-m3`，dense 向量 1024 维，L2 归一化，余弦相似度 |
| 歌曲级集合 | `songs_v1`，文档 id = `str(song_id)` |
| 歌词行级集合 | `lyric_lines_v1`，文档 id = `"{song_id}:{line_no}"`；每条由第 `line_no` 行和下一行拼成窗口；metadata 包含 `song_id`、`line_no`、`start_ms` |
| 歌曲文档模板 | `"{name} / {artist} / 风格:{genres} / 情绪:{moods} / 场景:{scenes} / {introduction} / {前 4 行歌词}"`，版本 `tpl_v1` |
| 入库范围 | 歌曲级：全部歌曲；歌词行级：只收 `lyric_searchable = true` 的歌 |
| 版本文件 | `index_meta.json`：模型名、模板版本、契约版本、构建时间、各集合条数。Agent 服务启动时校验，不一致就拒绝启动 |

运行时，用户查询必须用同一个模型编码。换模型或改模板都要重建索引，并升级集合名（`_v2`）。

## C7. 工具接口 v1

**工具名固定**：`search_by_keyword`、`search_by_lyrics`、`filter_by_tags`、`semantic_search`、`similar_songs`、`get_user_profile`、`recommend_for_user`、`get_song_detail`。

**统一返回格式**

```json
{ "ok": true, "data": { "items": [ /* SongHit */ ] }, "error": null }
{ "ok": false, "data": null, "error": { "code": "INVALID_ARGUMENT", "message": "..." } }
```

- 错误码：`INVALID_ARGUMENT` / `NOT_FOUND` / `TIMEOUT` / `UNAVAILABLE` / `INTERNAL`。
- **没搜到结果算成功**：返回 `ok: true` 和空的 `items`，不算错误。阶段 3 的"放宽条件"逻辑需要区分"没结果"和"出错了"。

**通用参数**

- `limit`：默认 10，最大 20。
- `exclude_ids: int[]`：阶段 4 的"换一首"靠它实现。
- `filters`：`filter_by_tags` 和 `semantic_search` 共用**同一个** Filter 结构。阶段 3 放宽条件时修改的是这个对象，阶段 4 会话状态里存的也是这个对象。

```json
{
  "genres": ["摇滚"], "moods": ["燃"], "scenes": ["跑步"],
  "vocal": "vocal", "languages": ["en"],
  "duration_s": { "min": 120, "max": 300 }
}
```

`genres` / `moods` / `scenes` 的取值用 C3 词表生成 enum，`languages` 用 ISO 代码。

- **`user_id` 不是 LLM 可以填的参数**：它由运行时从 JWT 注入，防止模型替别人查数据。

**返回条目 `SongHit`（给 LLM 看的精简版）**

```json
{
  "song_id": 1537329, "name": "...", "artist": "...", "duration_s": 226,
  "vocal": "vocal", "language": "en",
  "genres": ["流行"], "moods": ["浪漫"], "scenes": ["恋爱"],
  "rank": 1, "score": 0.83,
  "match": { "type": "lyric", "line_no": 12, "line_text": "...", "start_ms": 61230 }
}
```

- `match.type` 取值：`keyword` / `lyric` / `tag` / `semantic` / `similar` / `cf`（双塔）。
- `rank` 必填；`score` 统一成 0–1、越大越好，但不同工具之间的分数不可直接比较，阶段 3 合并多路结果时按 `rank` 做 RRF 融合。
- 不返回歌词全文、`url`、`pic`。10 条结果的总体积 ≤ 2KB。
- 阶段 5 给客户端的数据另行组装：根据 `song_id` 回查数据库，拿到 Koa 现有的 songs 行，再加上 `singerName`、推荐理由和 `start_ms`。所以只要有 `song_id`，客户端要的东西就都能查到。

## C8. 评测集与合成数据

- 评测集统一用 jsonl，每行：
  `{qid, query, type, relevant_song_ids[], split: "dev"|"test", origin: "auto"|"manual", contract_version}`。
  阈值只能在 `dev` 上调，报告里的数字只用 `test`。
- 自动生成的歌词搜歌题目，只从 `lrc_source` 为 `lrclib` 或 `aligned` 的歌里抽取。
- 合成用户的 ID ≥ 100000（见 C1）。合成播放记录写入 `user_plays` 时要能和真实数据区分开。双塔模型的映射 key 继续用 `song_{id}` / `user_{id}`。
- 所有随机过程的种子都写在 `config.py` 里。

---

## 要满足 v1，现有代码需要改的地方

| 位置 | 改动 |
|---|---|
| `04_align.py` | 打开保留原始分行的选项；输出每行的置信度，供 05 计算 `lrc_quality` |
| `common.py` | 新增唯一的 LRC 标准化和解析函数（删元信息行和空文本行、拆分多时间戳行、排序），03、04、05、07 都调用它 |
| `vocab/tags_zh.yaml` | 升级到 v3：删除 `voice` 类，新增 `aliases` 和 `language_display`；06 之后按覆盖门槛清理 |
| `06_llm_tagging.py` | 顺便让 LLM 判断歌词语种，填补 211 首 `lang` 为空的歌（有人声的歌填判断结果，纯音乐填 `zxx`） |
| `07_load_mysql.py` | 按 C1 写 ID，`url` 写 `/stream/{id}`，种子 SQL 里不能出现 `client_id`；生成 `song_lyric_lines` |
| `openKoa` | 新增 `GET /stream/:id` |
| `myMusic` | 新增 `resolveUrl()`，替换 `AvPlayerUtils.ets` 里写死的 IP 和各页面的 `Config.SERVER_URL + pic` |

## 变更记录

| 版本 | 日期 | 改动 | 影响阶段 |
|---|---|---|---|
| v1 | 2026-10-11 | 初版 | — |
