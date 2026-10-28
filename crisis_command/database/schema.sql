CREATE TABLE resources (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    incident_id TEXT
);

CREATE TABLE world_metadata (
    id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    resource_id TEXT,
    incident_id TEXT,
    world_version INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);