-- db/check_v1.sql - every SELECT should return the value named in its alias.
-- Run after db/seed_v1.sql.  mysql -u root -p < db/check_v1.sql
USE `mymusic`;

SELECT COUNT(*) AS `songs.expect_500` FROM songs;
SELECT COUNT(*) AS `orphan_singer.expect_0`
  FROM songs s LEFT JOIN singers g ON s.singer_id = g.id WHERE g.id IS NULL;
SELECT COUNT(*) AS `bad_url.expect_0` FROM songs WHERE url <> CONCAT('/stream/', id);
SELECT COUNT(*) AS `client_id_leak.expect_0`
  FROM songs WHERE url LIKE '%client_id%' OR lyric LIKE '%client_id%';
SELECT COUNT(*) AS `bad_pic.expect_0` FROM songs WHERE pic NOT LIKE 'https://%';
SELECT COUNT(*) AS `amp_in_song.expect_0`
  FROM songs WHERE name LIKE '%&amp;%' OR introduction LIKE '%&amp;%';
SELECT COUNT(*) AS `amp_in_singer.expect_0` FROM singers WHERE name LIKE '%&amp;%';
SELECT COUNT(*) AS `meta_mismatch.expect_0` FROM (
  SELECT s.id FROM songs s LEFT JOIN song_meta m ON s.id = m.song_id
  GROUP BY s.id HAVING COUNT(m.song_id) <> 1) t;
SELECT COUNT(*) AS `instrumental_meta.expect_0`
  FROM song_meta m JOIN songs s ON s.id = m.song_id
  WHERE m.vocal = 'instrumental' AND (m.language <> 'zxx' OR s.lyric <> '');
SELECT COUNT(*) AS `instrumental_lines.expect_0`
  FROM song_meta m JOIN song_lyric_lines l ON l.song_id = m.song_id WHERE m.vocal = 'instrumental';
SELECT COUNT(*) AS `tag_out_of_vocab.expect_0` FROM song_tags WHERE tag NOT IN ('R&B', '世界音乐', '中国风', '乡村', '伤感', '健身', '儿童', '励志', '原声配乐', '古典', '史诗', '咖啡馆', '失恋', '失落', '婚礼', '孤独', '学习', '工作', '希望', '平静', '开心', '开车', '忧郁', '怀旧', '恋爱', '愤怒', '慵懒', '拉丁', '摇滚', '放克', '散步', '旅行', '朋克', '梦幻', '民谣', '氛围', '治愈', '派对', '流行', '浪漫', '深夜', '清晨', '温暖', '灵魂乐', '燃', '爵士', '独立', '甜美', '电子', '睡前', '神秘', '紧张', '舞曲', '节日', '蓝调', '说唱', '跑步', '通勤', '金属', '雨天', '雷鬼');
SELECT COUNT(*) AS `searchable_no_lines.expect_0` FROM song_meta m
  WHERE m.lyric_searchable = 1
    AND NOT EXISTS (SELECT 1 FROM song_lyric_lines l WHERE l.song_id = m.song_id);
-- songs.lyric 与 song_lyric_lines 行数必须一致（normalize_lrc 保证每行以 \n 结尾）
SELECT COUNT(*) AS `lyric_line_count_mismatch.expect_0` FROM songs s
  LEFT JOIN (SELECT song_id, COUNT(*) c FROM song_lyric_lines GROUP BY song_id) l ON l.song_id = s.id
  WHERE (CHAR_LENGTH(s.lyric) - CHAR_LENGTH(REPLACE(s.lyric, '\n', ''))) <> COALESCE(l.c, 0);
SELECT COUNT(*) AS `nonmonotonic_time.expect_0` FROM song_lyric_lines a
  JOIN song_lyric_lines b ON a.song_id = b.song_id AND b.line_no = a.line_no + 1
  WHERE b.start_ms <= a.start_ms;
SELECT COUNT(*) AS `orphan_list_song.expect_0` FROM list_songs ls
  LEFT JOIN songs s ON ls.song_id = s.id
  LEFT JOIN song_lists sl ON ls.song_list_id = sl.id
  WHERE s.id IS NULL OR sl.id IS NULL;
-- 歌词可检索（用 LIKE；ngram FULLTEXT 的 MATCH 会让 mysqld 8.0.19 崩溃，不用）
SELECT COUNT(*) AS `lyric_like_hit.expect_ge1` FROM song_lyric_lines
  WHERE text LIKE '%love%';
