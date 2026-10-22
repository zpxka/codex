CREATE TABLE project_metadata (id INTEGER PRIMARY KEY CHECK(id=1), data_kind TEXT NOT NULL CHECK(data_kind IN ('actual','example')));
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE sources (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT NOT NULL,
    publication_date TEXT NOT NULL, source_type TEXT NOT NULL,
    original_url TEXT NOT NULL, notes TEXT NOT NULL, purpose TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE source_tags (
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags(id), PRIMARY KEY(source_id, tag_id)
);
CREATE TABLE verification_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    status TEXT NOT NULL, checked_on TEXT NOT NULL, checked_by TEXT NOT NULL,
    evidence TEXT NOT NULL, memo TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE INDEX events_source ON verification_events(source_id, sequence);
CREATE INDEX sources_type ON sources(source_type);
