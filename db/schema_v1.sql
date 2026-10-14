-- HarmonyMusic 数据库结构 v1（对应 DocsForMusicAgent/contracts_v1.md）
-- 取代 music.sql / database_init.sql。只含表结构，数据由 data_pipeline/07_load_mysql.py 生成 db/seed_v1.sql。
--
-- 导入顺序：先本文件，再 seed_v1.sql，最后才启动 Koa。
-- Koa 启动时的 sequelize.sync() 只会补建缺失的表；如果先启动 Koa，它会按 Sequelize 默认类型建表，和这里不一致。

CREATE DATABASE IF NOT EXISTS `mymusic` DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE `mymusic`;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- =============================================================================
-- 一、沿用的表：结构基本不变，只加索引或放宽长度
-- =============================================================================

DROP TABLE IF EXISTS `admins`;
CREATE TABLE `admins` (
  `id` int NOT NULL AUTO_INCREMENT,
  `username` varchar(50) NOT NULL COMMENT '用户名',
  `password` varchar(50) NOT NULL COMMENT '密码(md5)',
  `createdAt` datetime DEFAULT NULL,
  `updatedAt` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 真实用户 id 自增从 1 开始；合成用户 id >= 100000（契约 C1），username 以 synth_ 开头
DROP TABLE IF EXISTS `consumers`;
CREATE TABLE `consumers` (
  `id` int NOT NULL AUTO_INCREMENT,
  `username` varchar(255) NOT NULL,
  `password` varchar(255) NOT NULL,
  `sex` varchar(255) NOT NULL,
  `phone_num` varchar(255) NOT NULL,
  `email` varchar(255) NOT NULL,
  `birth` varchar(255) NOT NULL,
  `introduction` varchar(255) NOT NULL,
  `location` varchar(255) NOT NULL,
  `avatar` varchar(255) NOT NULL,
  `create_time` varchar(255) NOT NULL,
  `update_time` varchar(255) NOT NULL,
  `createdAt` datetime NOT NULL,
  `updatedAt` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- id = Jamendo artist_id（契约 C1）。name 由 50 放宽到 128：现有数据最长 64
DROP TABLE IF EXISTS `singers`;
CREATE TABLE `singers` (
  `id` int NOT NULL COMMENT 'Jamendo artist_id',
  `name` varchar(128) NOT NULL,
  `sex` varchar(10) NOT NULL DEFAULT '',
  `pic` varchar(255) NOT NULL DEFAULT '' COMMENT 'https 绝对地址',
  `birth` varchar(50) NOT NULL DEFAULT '',
  `location` varchar(50) NOT NULL DEFAULT '',
  `introduction` varchar(255) NOT NULL DEFAULT '',
  `createdAt` datetime DEFAULT NULL,
  `updatedAt` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  FULLTEXT KEY `ft_name` (`name`) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- id = Jamendo track id（契约 C1）；字段含义见契约 C2/C4/C5
DROP TABLE IF EXISTS `songs`;
CREATE TABLE `songs` (
  `id` int NOT NULL COMMENT 'Jamendo track id',
  `singer_id` int NOT NULL,
  `name` varchar(128) NOT NULL,
  `introduction` varchar(255) NOT NULL DEFAULT '' COMMENT 'LLM 中文一句话摘要',
  `pic` varchar(255) NOT NULL DEFAULT '' COMMENT 'https 绝对地址',
  `lyric` mediumtext NOT NULL COMMENT '标准化 LRC；纯音乐为空串',
  `url` varchar(255) NOT NULL COMMENT '/stream/{id}',
  `createdAt` datetime DEFAULT NULL,
  `updatedAt` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_singer` (`singer_id`),
  FULLTEXT KEY `ft_name` (`name`) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 按情绪/场景自动生成的歌单，style = 对应的中文标签
DROP TABLE IF EXISTS `song_lists`;
CREATE TABLE `song_lists` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(255) NOT NULL DEFAULT '',
  `pic` varchar(255) NOT NULL DEFAULT '',
  `introduction` varchar(255) NOT NULL DEFAULT '',
  `style` varchar(255) DEFAULT NULL,
  `createdAt` datetime DEFAULT NULL,
  `updatedAt` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `list_songs`;
CREATE TABLE `list_songs` (
  `id` int NOT NULL AUTO_INCREMENT,
  `song_id` int NOT NULL,
  `song_list_id` int NOT NULL,
  `createdAt` datetime DEFAULT NULL,
  `updatedAt` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_list_song` (`song_list_id`, `song_id`),
  KEY `idx_song` (`song_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 新增唯一键：同一用户不能重复收藏同一首歌（阶段 4 偏好统计依赖这一点）
DROP TABLE IF EXISTS `collects`;
CREATE TABLE `collects` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `song_id` int NOT NULL,
  `song_list_id` int DEFAULT NULL,
  `createdAt` datetime DEFAULT NULL,
  `updatedAt` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_song` (`user_id`, `song_id`),
  KEY `idx_song` (`song_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `comments`;
CREATE TABLE `comments` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `type` int NOT NULL,
  `song_id` int DEFAULT NULL,
  `song_list_id` int DEFAULT NULL,
  `content` varchar(255) NOT NULL,
  `up` int NOT NULL DEFAULT 0,
  `createdAt` datetime NOT NULL,
  `updatedAt` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_song` (`song_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `ranks`;
CREATE TABLE `ranks` (
  `id` int NOT NULL AUTO_INCREMENT,
  `song_list_id` int NOT NULL,
  `consumer_id` int NOT NULL,
  `score` int NOT NULL,
  `createdAt` datetime DEFAULT NULL,
  `updatedAt` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `swipers`;
CREATE TABLE `swipers` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(50) NOT NULL COMMENT '名称',
  `url` varchar(100) NOT NULL COMMENT '连接',
  `imgurl` varchar(300) NOT NULL COMMENT '轮播图像',
  `state` int NOT NULL DEFAULT '0' COMMENT '状态',
  `order` int NOT NULL DEFAULT '0' COMMENT '排序',
  `createdAt` datetime NOT NULL,
  `updatedAt` datetime NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- =============================================================================
-- 二、正式纳入的表：原来只在 recommend/sql_scripts 里，Koa 从不写入
-- =============================================================================

-- source 区分真实播放和合成数据（契约 C8）；阶段 5 由 Koa 写入真实播放记录
DROP TABLE IF EXISTS `user_plays`;
CREATE TABLE `user_plays` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL COMMENT '用户ID',
  `song_id` int NOT NULL COMMENT '歌曲ID',
  `play_duration` int NOT NULL COMMENT '播放时长(秒)',
  `full_duration` int NOT NULL COMMENT '歌曲总时长(秒)',
  `is_liked` tinyint(1) DEFAULT 0 COMMENT '是否喜欢(0=否,1=是)',
  `play_time` datetime NOT NULL COMMENT '播放时间',
  `device_type` varchar(20) DEFAULT 'mobile' COMMENT '设备类型',
  `source` enum('real','synthetic') NOT NULL DEFAULT 'real',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_user_time` (`user_id`, `play_time`),
  KEY `idx_song` (`song_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户播放记录表';

-- =============================================================================
-- 三、新增的表（契约 C2）
-- =============================================================================

DROP TABLE IF EXISTS `song_meta`;
CREATE TABLE `song_meta` (
  `song_id` int NOT NULL,
  `duration_s` int NOT NULL,
  `vocal` enum('vocal','instrumental') NOT NULL,
  `language` varchar(8) NOT NULL DEFAULT 'und' COMMENT 'ISO 639-1；纯音乐 zxx；未知 und',
  `speed` enum('verylow','low','medium','high','veryhigh') DEFAULT NULL,
  `release_date` date DEFAULT NULL,
  `license_url` varchar(255) NOT NULL DEFAULT '',
  `share_url` varchar(255) NOT NULL DEFAULT '',
  `lrc_source` enum('lrclib','aligned','asr','none') NOT NULL DEFAULT 'none',
  `lrc_quality` float DEFAULT NULL COMMENT '0-1，由 05 质检给出',
  `lyric_searchable` tinyint(1) NOT NULL DEFAULT 0,
  `pipeline_version` varchar(16) NOT NULL DEFAULT 'v1',
  PRIMARY KEY (`song_id`),
  KEY `idx_filter` (`vocal`, `language`),
  CONSTRAINT `fk_meta_song` FOREIGN KEY (`song_id`) REFERENCES `songs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `song_tags`;
CREATE TABLE `song_tags` (
  `song_id` int NOT NULL,
  `tag_type` enum('genre','mood','scene') NOT NULL,
  `tag` varchar(16) NOT NULL COMMENT '必须在 vocab/tags_zh.yaml v3 内',
  `source` enum('jamendo','llm') NOT NULL,
  PRIMARY KEY (`song_id`, `tag_type`, `tag`, `source`),
  KEY `idx_tag` (`tag_type`, `tag`),
  CONSTRAINT `fk_tag_song` FOREIGN KEY (`song_id`) REFERENCES `songs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `song_lyric_lines`;
CREATE TABLE `song_lyric_lines` (
  `song_id` int NOT NULL,
  `line_no` int NOT NULL COMMENT '从 0 开始',
  `start_ms` int NOT NULL,
  `end_ms` int NOT NULL,
  `text` varchar(512) NOT NULL,
  PRIMARY KEY (`song_id`, `line_no`),
  FULLTEXT KEY `ft_text` (`text`) WITH PARSER ngram,
  CONSTRAINT `fk_line_song` FOREIGN KEY (`song_id`) REFERENCES `songs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SET FOREIGN_KEY_CHECKS = 1;
