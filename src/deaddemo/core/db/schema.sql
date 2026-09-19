-- DeadDemo catalog schema (schema_version 1).
-- SQLite holds the catalog and per-match summaries; bulk per-tick data lives in Parquet.

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- One row per .dem (or .dem.partial) file seen on disk.
CREATE TABLE IF NOT EXISTS demos (
  id             INTEGER PRIMARY KEY,
  path           TEXT NOT NULL UNIQUE,
  source         TEXT NOT NULL,            -- 'game' | 'addons' | 'download' | 'manual'
  size_bytes     INTEGER NOT NULL,
  mtime          REAL NOT NULL,
  match_id       INTEGER,
  build          INTEGER,
  map_name       TEXT,
  tick_rate      INTEGER,
  total_ticks    INTEGER,
  status         TEXT NOT NULL,            -- 'found' | 'partial' | 'parsed' | 'error' | 'missing'
  parser_version TEXT,
  parsed_at      TEXT,
  error          TEXT,
  first_seen     TEXT NOT NULL,
  last_seen      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS demos_match ON demos(match_id);

-- One row per parsed match.
CREATE TABLE IF NOT EXISTS matches (
  match_id           INTEGER PRIMARY KEY,
  demo_id            INTEGER REFERENCES demos(id) ON DELETE SET NULL,
  map_name           TEXT,
  build              INTEGER,
  game_mode          INTEGER,
  tick_rate          INTEGER,
  total_ticks        INTEGER,
  game_start_tick    INTEGER,
  game_over_tick     INTEGER,
  regulation_seconds REAL,
  pregame_seconds    REAL,
  winning_team       INTEGER,
  parser_version     TEXT,
  boon_version       TEXT,
  parsed_at          TEXT,
  parquet_dir        TEXT,
  has_ticks          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS match_players (
  match_id           INTEGER NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
  hero_id            INTEGER NOT NULL,
  steam_id           INTEGER,
  player_name        TEXT,
  team_num           INTEGER,
  start_lane         INTEGER,
  rank               INTEGER,
  kills              INTEGER,
  deaths             INTEGER,
  assists            INTEGER,
  last_hits          INTEGER,
  denies             INTEGER,
  souls              INTEGER,
  hero_damage        INTEGER,
  level              INTEGER,
  kill_participation REAL,
  time_dead_s        REAL,
  won                INTEGER,
  PRIMARY KEY (match_id, hero_id)
);
CREATE INDEX IF NOT EXISTS mp_steam ON match_players(steam_id);

CREATE TABLE IF NOT EXISTS kills (
  match_id          INTEGER NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
  tick              INTEGER NOT NULL,
  match_seconds     REAL,
  victim_hero_id    INTEGER,
  attacker_hero_id  INTEGER,
  assister_hero_ids TEXT                    -- JSON array
);
CREATE INDEX IF NOT EXISTS kills_match ON kills(match_id);

CREATE TABLE IF NOT EXISTS item_purchases (
  match_id      INTEGER NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
  tick          INTEGER NOT NULL,
  match_seconds REAL,
  hero_id       INTEGER,
  ability_id    INTEGER,
  change        TEXT
);
CREATE INDEX IF NOT EXISTS items_match ON item_purchases(match_id);

CREATE TABLE IF NOT EXISTS objective_events (
  match_id       INTEGER NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
  tick           INTEGER NOT NULL,
  match_seconds  REAL,
  objective_type TEXT,
  team_num       INTEGER,
  lane           INTEGER
);
CREATE INDEX IF NOT EXISTS obj_match ON objective_events(match_id);

-- Match history as reported by deadlock-api (kept separate from parsed data).
CREATE TABLE IF NOT EXISTS api_match_history (
  account_id           INTEGER NOT NULL,
  match_id             INTEGER NOT NULL,
  hero_id              INTEGER,
  start_time           INTEGER,
  match_duration_s     INTEGER,
  game_mode            INTEGER,
  match_mode           INTEGER,
  player_team          INTEGER,
  match_result         INTEGER,
  player_match_outcome INTEGER,
  player_kills         INTEGER,
  player_deaths        INTEGER,
  player_assists       INTEGER,
  denies               INTEGER,
  last_hits            INTEGER,
  net_worth            INTEGER,
  hero_level           INTEGER,
  ranked_display_badge INTEGER,
  ranked_delta         INTEGER,
  team_abandoned       INTEGER,
  fetched_at           TEXT NOT NULL,
  PRIMARY KEY (account_id, match_id)
);

CREATE TABLE IF NOT EXISTS api_match_salts (
  match_id      INTEGER PRIMARY KEY,
  cluster_id    INTEGER,
  metadata_salt INTEGER,
  replay_salt   INTEGER,
  demo_url      TEXT,
  metadata_url  TEXT,
  fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS downloads (
  id          INTEGER PRIMARY KEY,
  match_id    INTEGER NOT NULL,
  url         TEXT NOT NULL,
  dest_path   TEXT NOT NULL,
  status      TEXT NOT NULL,               -- 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  bytes_total INTEGER,
  bytes_done  INTEGER NOT NULL DEFAULT 0,
  started_at  TEXT,
  finished_at TEXT,
  error       TEXT
);

CREATE TABLE IF NOT EXISTS tags (
  id    INTEGER PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE,
  color TEXT
);

CREATE TABLE IF NOT EXISTS match_tags (
  match_id INTEGER NOT NULL,
  tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (match_id, tag_id)
);

CREATE TABLE IF NOT EXISTS annotations (
  match_id   INTEGER PRIMARY KEY,
  comment    TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS calibrations (
  map_name       TEXT PRIMARY KEY,
  image_path     TEXT,
  matrix_json    TEXT NOT NULL,
  landmarks_json TEXT,
  updated_at     TEXT NOT NULL
);
