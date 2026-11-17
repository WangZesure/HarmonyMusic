## claude:
手下已经做了一部分：`07_load_mysql.py` 和 `data_pipeline/sql/` 下的两个 SQL 文件都有了，但跟我昨天定的表结构对不上。下面的任务说明按“实际情况和目标的差距”来写，可以直接复制给他。

---

**任务：数据库 v1 落地（阶段 1 最后一步，07）**

**背景**
- 表结构以 `db/schema_v1.sql` 为准（我已在临时库验证过，14 张表都能建成功）。
- 契约以 `DocsForMusicAgent/contracts_v1.md` 的 C1、C2、C5 为准，C2 开头已写明本次改动。
- 你现在的 `data_pipeline/sql/schema_v1.sql` 只是在旧库上追加三张表，作废。

**表结构相对旧库的改动**（不用你改，但写入数据时要遵守）
- `songs.id`、`singers.id` 不再自增，直接用 Jamendo 的 ID；
- `songs.name`、`singers.name` 放宽到 varchar(128)，`songs.lyric` 改为 mediumtext；
- `song_lyric_lines.text` 是 varchar(512)；
- `collects` 有 `(user_id, song_id)` 唯一键，`list_songs` 有 `(song_list_id, song_id)` 唯一键；
- `user_plays` 正式进库，新增 `source` 字段（`real` / `synthetic`）；
- `song_meta`、`song_tags`、`song_lyric_lines` 对 `songs` 有外键，删歌时一并删除；
- 每张表开头都是 `DROP TABLE`，导入就是重建整个库。

**要做的事**
1. **统一文件位置。** 删除 `data_pipeline/sql/schema_v1.sql`；把 `config.SQL_DIR` 改为指向仓库根目录的 `db/`；种子文件输出到 `db/seed_v1.sql`。
2. **改 `07_load_mysql.py`：**
   - 删掉 `[:100]`、`[:255]` 这类截断。字段超长时直接报错并退出，不要静默截断。
   - 歌名和艺人名先用 `html.unescape` 还原。现在的 `seed_v1.sql` 里有 33 处 `&amp;`。
   - 因为表结构会重建所有表，种子文件里要重新插入 `music.sql` 中的 2 个管理员和 2 个测试用户（ID 保持不变），还有轮播图。
   - 旧歌曲、歌手、歌单、收藏、评论一律不导入。
   - 按标签生成歌单：每个情绪、场景标签如果有不少于 8 首歌就建一个歌单，标题用标签中文名，封面用其中第一首歌的封面，同时写入 `list_songs`。
   - `user_plays` 本轮不导入数据。旧的 5000 条假数据引用的是旧歌 1–10 号，已经失效；合成数据放到阶段 4 重新生成。
   - `--apply` 现在一定会失败：pymysql 默认不支持一次执行多条语句。改为调用 `mysql` 命令行导入，或者连接时加 `client_flag=CLIENT.MULTI_STATEMENTS`。
3. **新增 `db/check_v1.sql`**，每条查询期望返回 0 或指定值：
   - `songs` 500 行；所有 `singer_id` 都能在 `singers` 中找到；
   - `songs.url` 全部等于 `/stream/{id}`；全库搜不到 `client_id`；
   - `pic` 全部以 `https://` 开头；名称里没有 `&amp;`；
   - 每首歌的 `song_meta` 恰好 1 行；
   - 纯音乐的 `language` 是 `zxx`，`lyric` 为空，且没有歌词行；
   - `song_tags.tag` 都在 `vocab/tags_zh.yaml` 里（脚本生成白名单）；
   - 有歌词的歌，`song_lyric_lines` 的行数等于 LRC 解析出的行数，且 `start_ms` 单调递增；
   - `list_songs` 没有孤立行；
   - 全文检索能用：拿一句已知歌词，`MATCH ... AGAINST` 能找回对应的歌。
4. **先自测，不要碰正式库。** 把 `mymusic` 替换成临时库名，导入 `schema_v1.sql` 和 `seed_v1.sql`，跑 `check_v1.sql`，然后删掉临时库。确认无误后再导入正式库，导入后启动 Koa，确认 `sync()` 没有改动表结构。

**禁止**
- 种子文件和仓库里不能出现 `client_id`、DeepSeek key、服务器密码；
- 不改 `db/schema_v1.sql`，需要改结构先找我；
- 不手改 `seed_v1.sql`，只能由 07 生成。

**交付物**
- `db/seed_v1.sql`、`db/check_v1.sql`，以及改好的 07；
- 一份执行记录，包含：
  - `check_v1.sql` 的完整输出；
  - 各表行数；
  - 歌单列表，含每个歌单的歌曲数；
  - Koa 启动后，`curl` 调用首页歌单接口和单首歌详情接口的返回片段。

---

几点补充：
- **数据库会整个重建**：`db/schema_v1.sql` 每张表都先 `DROP`，导入会清空 `mymusic` 里的所有东西。所以我要求他先在临时库自测，并在种子文件里补回管理员和测试用户。
- **推荐功能会暂时没有数据**：`user_plays` 这一轮是空表，推荐脚本 `simple_recommend.py` 可能会报错或返回空。如果要不阻塞首页，可以加一条“推荐接口在空表时返回热门歌曲”，或者让他直接把合成播放数据也做了。
- **不在本轮范围**：Koa 的 `user_plays` 模型和客户端的 `resolveUrl()`，属于阶段 5 的改动。

## mysql情况：
能调通。我（Cursor 里的 agent）用的是本机的 `mysql` 命令行客户端，直接连 `root@localhost:3306`，账号密码和 `data_pipeline/config.py` 里的一样，不需要通过 Workbench。Workbench 只是图形界面，开不开都没关系，只要 Windows 的 MySQL80 服务在运行就行；它现在是开机自动启动的。

**`mymusic` 库现在的状态**：手下已经按他自己那份只追加三张表的结构，把数据导进去了。

| 项目 | 情况 |
|---|---|
| 新歌曲数据 | 正常：`songs` 500 首，`song_meta` 500 行，`song_tags` 5510 行，`song_lyric_lines` 约 1.1 万行 |
| 旧数据残留 | `user_plays` 5000 条里 5000 条指向的歌已不存在；`collects` 有 7 条、`list_songs` 有 4 条、`comments` 有 14 条也是这种情况 |
| 表结构 | 还是旧版：`singers.name` 仍是 varchar(50)，`user_plays` 没有 `source` 字段，也没有新加的唯一键 |
| 名称转义 | 有 7 个歌手名还带着 `&amp;` |

这正好印证了上一条任务说明要改的几点。等他按说明改完，用 `db/schema_v1.sql` 和新的种子文件重新导入，这些问题会一起消失。

我的所有操作都是只读的，没有改动这个库。如果你想自己先在 Workbench 里看看，用 `root` / `3306` 连接，展开 `mymusic` 就能看到这些表。