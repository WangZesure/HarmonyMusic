-- Agent v6 persistence tables. Safe to run repeatedly.
-- Apply to the existing `mymusic` DB; does not touch the 14 corpus tables.

CREATE TABLE IF NOT EXISTS agent_sessions (
  session_key  VARCHAR(128) PRIMARY KEY,        -- {uid}:{session_id}
  state_json   JSON NOT NULL,                   -- constraints/seen/last_shown/pending/last_turn_id
  version      INT NOT NULL DEFAULT 0,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
               ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_agent_sessions_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_preference (
  user_id      INT NOT NULL,
  tag          VARCHAR(64) NOT NULL,
  polarity     TINYINT NOT NULL DEFAULT 1,      -- 1 = like, -1 = dislike
  weight       FLOAT NOT NULL DEFAULT 0.1,      -- 0..0.3
  source_turn  VARCHAR(128) NULL,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
               ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, tag),
  INDEX idx_user_preference_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
