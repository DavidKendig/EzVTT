-- EzVTT initial schema.
--
-- Applied once by the runner in ezvtt/db.py. Migrations are append-only: to
-- change the schema, add 002_*.sql -- never edit this file after it has shipped,
-- or existing databases and fresh ones will diverge.

-- ---------------------------------------------------------------------------
-- Accounts
-- ---------------------------------------------------------------------------

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT    NOT NULL,
    email         TEXT,
    -- scrypt digest and its per-user salt, both hex. Cost parameters are stored
    -- alongside so they can be raised later without invalidating existing
    -- passwords -- verification reads the cost the hash was made with.
    pw_hash       TEXT    NOT NULL,
    pw_salt       TEXT    NOT NULL,
    pw_n          INTEGER NOT NULL,
    pw_r          INTEGER NOT NULL,
    pw_p          INTEGER NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('admin', 'gm', 'player')),
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    ip         TEXT,
    user_agent TEXT
);

CREATE INDEX idx_sessions_user    ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);

-- ---------------------------------------------------------------------------
-- Server settings
-- ---------------------------------------------------------------------------
-- Key/value rather than columns: these are read rarely, written by hand from the
-- admin screen, and the set of them will keep growing.

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO settings (key, value) VALUES
    ('schema_created_at',  datetime('now')),
    ('first_run_complete', '0'),
    ('beta_bypass',        '1'),
    ('vault_path',         ''),
    ('vault_allowed_dirs', ''),
    ('campaign_name',      'A New Campaign');

-- ---------------------------------------------------------------------------
-- Maps
-- ---------------------------------------------------------------------------

CREATE TABLE maps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    filename     TEXT    NOT NULL UNIQUE,
    width_px     INTEGER NOT NULL,
    height_px    INTEGER NOT NULL,
    -- Grid geometry in source-image pixels, so it stays correct under any zoom.
    -- grid_px is what the GM drags the slider to match against the artwork.
    grid_px      REAL    NOT NULL DEFAULT 70.0,
    offset_x     REAL    NOT NULL DEFAULT 0.0,
    offset_y     REAL    NOT NULL DEFAULT 0.0,
    grid_color   TEXT    NOT NULL DEFAULT '#000000',
    grid_opacity REAL    NOT NULL DEFAULT 0.25,
    grid_visible INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Scenes
-- ---------------------------------------------------------------------------
-- A scene is a map plus its token layout and fog. Prepping several and switching
-- between them is what makes "get a map on the table fast" work mid-session.

CREATE TABLE scenes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id     INTEGER REFERENCES maps(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_scenes_map ON scenes(map_id);

-- At most one active scene. A partial unique index enforces this in the
-- database rather than trusting every write path to remember.
CREATE UNIQUE INDEX idx_scenes_single_active ON scenes(is_active) WHERE is_active = 1;

-- ---------------------------------------------------------------------------
-- Asset library
-- ---------------------------------------------------------------------------

CREATE TABLE assets (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT    NOT NULL,
    filename TEXT    NOT NULL,
    -- 'bundled' rows live in assets/bundled/ and are READ-ONLY on disk: Tom's
    -- Open Map License does not permit editing them. grid_w/grid_h are display
    -- metadata applied as a render-time transform over the untouched original.
    -- See ADR-005. 'user' rows live in data/assets/ and may be transformed.
    source   TEXT    NOT NULL CHECK (source IN ('bundled', 'user')),
    -- Footprint in grid squares. Parsed from the "_2x1" filename suffix on
    -- import where present, defaulting to 1x1, and adjustable by the GM.
    grid_w   REAL    NOT NULL DEFAULT 1.0,
    grid_h   REAL    NOT NULL DEFAULT 1.0,
    category TEXT,
    tags     TEXT,
    created_at TEXT  NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, filename)
);

CREATE INDEX idx_assets_category ON assets(category);
CREATE INDEX idx_assets_name     ON assets(name);

-- ---------------------------------------------------------------------------
-- Tokens
-- ---------------------------------------------------------------------------

CREATE TABLE tokens (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    -- Position in grid units, not pixels, so changing grid_px on the map does
    -- not scatter everything already placed.
    x        REAL    NOT NULL DEFAULT 0.0,
    y        REAL    NOT NULL DEFAULT 0.0,
    grid_w   REAL    NOT NULL DEFAULT 1.0,
    grid_h   REAL    NOT NULL DEFAULT 1.0,
    rotation REAL    NOT NULL DEFAULT 0.0,
    z        INTEGER NOT NULL DEFAULT 0,
    layer    TEXT    NOT NULL DEFAULT 'token'
             CHECK (layer IN ('map', 'object', 'token')),
    label    TEXT,
    -- Set to let a player move this token; NULL means GM-only.
    owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    is_hidden INTEGER NOT NULL DEFAULT 0,
    is_locked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT   NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_tokens_scene ON tokens(scene_id, layer, z);
CREATE INDEX idx_tokens_owner ON tokens(owner_user_id);

-- ---------------------------------------------------------------------------
-- Fog of war
-- ---------------------------------------------------------------------------
-- One row per scene. revealed_rle is a run-length-encoded bitmask over grid
-- cells, row-major across cols x rows. Compact enough to rewrite wholesale on
-- each brush stroke, which keeps the write path trivial.
--
-- The server never sends unrevealed map data to players -- fog is an
-- access-control boundary, not a visual overlay. See ADR-004.

CREATE TABLE fog (
    scene_id    INTEGER PRIMARY KEY REFERENCES scenes(id) ON DELETE CASCADE,
    cols        INTEGER NOT NULL,
    rows        INTEGER NOT NULL,
    revealed_rle TEXT   NOT NULL DEFAULT '',
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Notes
-- ---------------------------------------------------------------------------

CREATE TABLE notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL DEFAULT 'Untitled',
    body       TEXT    NOT NULL DEFAULT '',
    -- 'private' author only; 'public' whole table; 'gm' author and GMs.
    visibility TEXT    NOT NULL DEFAULT 'private'
               CHECK (visibility IN ('private', 'public', 'gm')),
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_notes_user       ON notes(user_id);
CREATE INDEX idx_notes_visibility ON notes(visibility);

-- ---------------------------------------------------------------------------
-- Chat and dice
-- ---------------------------------------------------------------------------

CREATE TABLE chat_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    -- Denormalised so history survives account deletion with attribution intact.
    author    TEXT    NOT NULL,
    kind      TEXT    NOT NULL CHECK (kind IN ('chat', 'roll', 'whisper', 'system')),
    body      TEXT    NOT NULL,
    -- Roll detail as JSON: notation, per-die results, modifier, total. Rolls are
    -- evaluated server-side; a modified client cannot fabricate one. See ADR-004.
    roll_json TEXT,
    -- NULL for public. Set for a whisper or a private roll, which is delivered
    -- only to this user and to GMs.
    target_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    is_private INTEGER NOT NULL DEFAULT 0,
    created_at TEXT   NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_chat_created ON chat_log(created_at);
CREATE INDEX idx_chat_target  ON chat_log(target_user_id);

-- ---------------------------------------------------------------------------
-- Initiative
-- ---------------------------------------------------------------------------

CREATE TABLE initiative (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id   INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    token_id   INTEGER REFERENCES tokens(id) ON DELETE CASCADE,
    label      TEXT    NOT NULL,
    value      REAL    NOT NULL DEFAULT 0.0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_current INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_initiative_scene ON initiative(scene_id, sort_order);
