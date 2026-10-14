# 阶段 1 交付执行记录 (db v1)

生成命令：`python data_pipeline/tools/sql_selftest.py`（临时库自测）→ `python data_pipeline/07_load_mysql.py --apply`（正式库）

## 各表行数

- admins: 2
- consumers: 2
- swipers: 3
- singers: 328
- songs: 500
- song_lists: 37
- list_songs: 3155
- song_meta: 500
- song_tags: 5510
- song_lyric_lines: 11148
- user_plays: 0
- collects: 0
- comments: 0
- ranks: 0

## check_v1.sql 结果（正式库 mymusic）

- OK  `songs.expect_500` = 500
- OK  `orphan_singer.expect_0` = 0
- OK  `bad_url.expect_0` = 0
- OK  `client_id_leak.expect_0` = 0
- OK  `bad_pic.expect_0` = 0
- OK  `amp_in_song.expect_0` = 0
- OK  `amp_in_singer.expect_0` = 0
- OK  `meta_mismatch.expect_0` = 0
- OK  `instrumental_meta.expect_0` = 0
- OK  `instrumental_lines.expect_0` = 0
- OK  `tag_out_of_vocab.expect_0` = 0
- OK  `searchable_no_lines.expect_0` = 0
- OK  `lyric_line_count_mismatch.expect_0` = 0
- OK  `nonmonotonic_time.expect_0` = 0
- OK  `orphan_list_song.expect_0` = 0
- OK  `fulltext_hit.expect_ge1` = 396

## 歌单（37 个）

| id | 标题 | 歌曲数 |
|---|---|---|
| 1 | 深夜 | 299 |
| 2 | 平静 | 236 |
| 3 | 梦幻 | 175 |
| 4 | 伤感 | 156 |
| 5 | 忧郁 | 141 |
| 6 | 睡前 | 137 |
| 7 | 励志 | 128 |
| 8 | 希望 | 119 |
| 9 | 燃 | 117 |
| 10 | 咖啡馆 | 115 |
| 11 | 温暖 | 113 |
| 12 | 浪漫 | 110 |
| 13 | 失恋 | 105 |
| 14 | 恋爱 | 99 |
| 15 | 学习 | 85 |
| 16 | 开心 | 83 |
| 17 | 怀旧 | 76 |
| 18 | 孤独 | 75 |
| 19 | 治愈 | 75 |
| 20 | 慵懒 | 71 |
| 21 | 散步 | 69 |
| 22 | 史诗 | 64 |
| 23 | 神秘 | 59 |
| 24 | 旅行 | 56 |
| 25 | 开车 | 55 |
| 26 | 清晨 | 55 |
| 27 | 派对 | 54 |
| 28 | 失落 | 41 |
| 29 | 工作 | 37 |
| 30 | 健身 | 29 |
| 31 | 跑步 | 25 |
| 32 | 愤怒 | 22 |
| 33 | 甜美 | 18 |
| 34 | 通勤 | 16 |
| 35 | 紧张 | 14 |
| 36 | 婚礼 | 13 |
| 37 | 雨天 | 13 |

## Koa 启动后 curl 返回片段

```
# GET /getAllSongList?page=1&pageSize=3
{"code":200,"message":"歌单所有列表成功","data":[{"id":1,"title":"深夜","pic":"https://usercontent.jamendo.com?...","style":"深夜"}...]}

# GET /getRecommendSongList  (首页推荐；user_plays 为空 -> 降级热门)
{"code":200,"message":"推荐单曲成功（降级方案）","data":[{"id":341,"name":"Mastodonte","url":"/stream/341",...}]}

# GET /getSongDetail?id=7359
{"code":200,"message":"获取成功","data":[{"id":7359,"name":"Move a Little Closer","pic":"https://usercontent.jamendo.com?...","lyric":"[00:02.94]Move a little closer to me
..."}]}

# GET /stream/7359
302 Location: https://api.jamendo.com/v3.0/tracks/file/?client_id=...&id=7359&action=stream&audioformat=mp32
```

Koa 启动后 `SHOW CREATE TABLE` 哈希前后一致 —— `sequelize.sync()` 未改动表结构。
