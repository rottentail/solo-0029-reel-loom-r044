SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS blobs(
 hash TEXT PRIMARY KEY, size INTEGER NOT NULL CHECK(size>=0), created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assets(
 id TEXT PRIMARY KEY, label TEXT NOT NULL, blob TEXT NOT NULL REFERENCES blobs(hash),
 metadata TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('ready','retired')), created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS assets_blob ON assets(blob);
CREATE TABLE IF NOT EXISTS reels(
 id TEXT PRIMARY KEY, title TEXT NOT NULL, head INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('open','sealed')), created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions(
 reel TEXT NOT NULL REFERENCES reels(id), number INTEGER NOT NULL,
 document TEXT NOT NULL, hash TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(reel,number)
);
CREATE TABLE IF NOT EXISTS looks(
 id TEXT NOT NULL, version INTEGER NOT NULL, spec TEXT NOT NULL,
 hash TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(id,version)
);
CREATE TABLE IF NOT EXISTS cue_tracks(
 id TEXT PRIMARY KEY, asset TEXT NOT NULL REFERENCES assets(id),
 language TEXT NOT NULL, cues TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets(
 id TEXT PRIMARY KEY, reel TEXT NOT NULL REFERENCES reels(id), revision INTEGER NOT NULL,
 look_id TEXT NOT NULL, look_version INTEGER NOT NULL,
 snapshot TEXT NOT NULL, fingerprint TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
 attempt INTEGER NOT NULL, progress REAL NOT NULL DEFAULT 0,
 cancel INTEGER NOT NULL DEFAULT 0, error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 FOREIGN KEY(reel,revision) REFERENCES revisions(reel,number),
 FOREIGN KEY(look_id,look_version) REFERENCES looks(id,version)
);
CREATE INDEX IF NOT EXISTS ticket_queue ON tickets(state,created,id);
CREATE TABLE IF NOT EXISTS artifacts(
 id TEXT PRIMARY KEY, ticket TEXT REFERENCES tickets(id), asset TEXT REFERENCES assets(id),
 kind TEXT NOT NULL, blob TEXT NOT NULL REFERENCES blobs(hash),
 metadata TEXT NOT NULL, created REAL NOT NULL,
 CHECK(ticket IS NOT NULL OR asset IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS artifact_ticket ON artifacts(ticket);
CREATE TABLE IF NOT EXISTS analyses(
 id TEXT PRIMARY KEY, asset TEXT NOT NULL REFERENCES assets(id),
 kind TEXT NOT NULL, settings TEXT NOT NULL, result TEXT NOT NULL,
 fingerprint TEXT NOT NULL UNIQUE, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS imprints(
 id TEXT PRIMARY KEY, ticket TEXT NOT NULL REFERENCES tickets(id),
 manifest TEXT NOT NULL, blob TEXT NOT NULL REFERENCES blobs(hash),
 fingerprint TEXT NOT NULL UNIQUE, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS event_subject ON events(subject,sequence);
CREATE TABLE IF NOT EXISTS request_keys(
 scope TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 response TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(scope,key)
);
"""
