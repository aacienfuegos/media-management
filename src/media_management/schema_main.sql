CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    root TEXT NOT NULL,
    relpath TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    captured_at_utc TEXT,
    captured_at_local TEXT,
    captured_at_source TEXT,
    date_utc TEXT,
    date_source TEXT,
    duration_s REAL,
    width INTEGER,
    height INTEGER,
    meta_error TEXT,
    jellyfin_item_id TEXT,
    present INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE (root, relpath)
);
CREATE INDEX IF NOT EXISTS files_root_present ON files (root, present);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('open', 'password', 'request')),
    password_hash TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS link_files (
    id INTEGER PRIMARY KEY,
    link_id INTEGER NOT NULL REFERENCES links (id),
    file_id INTEGER NOT NULL REFERENCES files (id),
    position INTEGER NOT NULL,
    thumb_jpeg BLOB,
    UNIQUE (link_id, file_id)
);

CREATE TABLE IF NOT EXISTS grants (
    id INTEGER PRIMARY KEY,
    link_id INTEGER NOT NULL REFERENCES links (id),
    request_id INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS trash_entries (
    id INTEGER PRIMARY KEY,
    file_id INTEGER REFERENCES files (id),
    root TEXT NOT NULL,
    relpath TEXT NOT NULL,
    trash_relpath TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    trashed_at TEXT NOT NULL,
    trashed_by TEXT NOT NULL,
    restored_at TEXT,
    purged_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    result TEXT NOT NULL,
    ip TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit (ts);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
