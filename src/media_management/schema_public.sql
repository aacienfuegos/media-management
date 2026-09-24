CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    link_id INTEGER NOT NULL,
    link_file_id INTEGER NOT NULL,
    grant_id INTEGER,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS tickets_link ON tickets (link_id);

CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY,
    ticket_id TEXT NOT NULL,
    link_id INTEGER NOT NULL,
    link_file_id INTEGER NOT NULL,
    grant_id INTEGER,
    ts TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT,
    range_header TEXT
);
CREATE INDEX IF NOT EXISTS downloads_link ON downloads (link_id);

CREATE TABLE IF NOT EXISTS auth_attempts (
    id INTEGER PRIMARY KEY,
    link_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ip TEXT,
    ts TEXT NOT NULL,
    ok INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_attempts_link ON auth_attempts (link_id, ts);
CREATE INDEX IF NOT EXISTS auth_attempts_ip ON auth_attempts (ip, ts);

CREATE TABLE IF NOT EXISTS access_requests (
    id INTEGER PRIMARY KEY,
    link_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    note TEXT NOT NULL,
    code_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    resolved_at TEXT,
    resolved_by TEXT
);
CREATE INDEX IF NOT EXISTS access_requests_link ON access_requests (link_id, status);
CREATE INDEX IF NOT EXISTS access_requests_ip ON access_requests (ip, created_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    link_id INTEGER,
    ref TEXT,
    ip TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
