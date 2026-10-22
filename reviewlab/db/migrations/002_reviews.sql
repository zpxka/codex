CREATE TABLE review_projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, data_kind TEXT NOT NULL,
 rating_min REAL NOT NULL, rating_max REAL NOT NULL, description TEXT NOT NULL,
 created_at TEXT NOT NULL, CHECK(rating_max > rating_min)
);
CREATE TABLE review_imports (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES review_projects(id),
 file_sha256 TEXT NOT NULL, encoding TEXT NOT NULL, mapping_json TEXT NOT NULL,
 source_note TEXT NOT NULL, total_rows INTEGER NOT NULL, imported_rows INTEGER NOT NULL,
 skipped_rows INTEGER NOT NULL, ignored_columns INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE reviews (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES review_projects(id),
 import_id TEXT NOT NULL REFERENCES review_imports(id), source_row INTEGER NOT NULL,
 identity_key TEXT NOT NULL, review_key TEXT, platform TEXT, industry TEXT, rating REAL,
 review_date TEXT, content TEXT, event_mentioned TEXT NOT NULL, ad_disclosed TEXT NOT NULL,
 collected_on TEXT, original_url TEXT, raw_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(project_id, identity_key)
);
CREATE TABLE review_notes (
 id TEXT PRIMARY KEY, review_id TEXT NOT NULL REFERENCES reviews(id),
 memo TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE review_checks (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 review_id TEXT NOT NULL REFERENCES reviews(id), status TEXT NOT NULL,
 reviewer TEXT NOT NULL, checked_on TEXT NOT NULL, evidence TEXT NOT NULL,
 memo TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE review_source_links (
 project_id TEXT NOT NULL REFERENCES review_projects(id),
 referenced_id TEXT NOT NULL, source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
 title TEXT NOT NULL, original_url TEXT NOT NULL,
 PRIMARY KEY(project_id, referenced_id)
);
CREATE TABLE review_analysis_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES review_projects(id),
 filters_json TEXT NOT NULL, parameters_json TEXT NOT NULL, input_ids_json TEXT NOT NULL,
 results_json TEXT NOT NULL, created_at TEXT NOT NULL, tool_version TEXT NOT NULL
);
CREATE INDEX review_project_index ON reviews(project_id);
CREATE INDEX review_check_index ON review_checks(review_id, sequence);
CREATE INDEX review_notes_index ON review_notes(review_id, recorded_at);
