from contextlib import contextmanager
import json
import sqlite3
from .schema import SCHEMA, SCHEMA_VERSION
from .errors import LoomError
from .values import canonical, now


class Database:
    def __init__(self, root):
        self.path = root / "reel_loom.sqlite3"
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def initialize(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            row = db.execute("SELECT version FROM schema_info").fetchone()
            if row is None:
                db.execute("INSERT INTO schema_info VALUES(?)", (SCHEMA_VERSION,))
            elif row["version"] != SCHEMA_VERSION:
                raise RuntimeError("Unsupported database schema")

    @contextmanager
    def write_unit(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise LoomError("integrity_conflict", "The operation conflicts with persisted references", 409) from exc
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def read(self):
        db = self.connect()
        try:
            db.execute("BEGIN")
            yield db
        finally:
            db.rollback()
            db.close()

    def event(self, db, subject, kind, payload):
        db.execute("INSERT INTO events(subject,kind,payload,created) VALUES(?,?,?,?)",
                   (subject, kind, canonical(payload), now()))

    def events(self, subject, after=0, limit=100):
        with self.read() as db:
            rows = db.execute("SELECT * FROM events WHERE subject=? AND sequence>? ORDER BY sequence LIMIT ?",
                              (subject, after, limit)).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def health(self):
        with self.read() as db:
            result = db.execute("PRAGMA quick_check").fetchone()[0]
            states = db.execute("SELECT state,COUNT(*) n FROM tickets GROUP BY state").fetchall()
            return {"database": result, "ticket_states": {r["state"]: r["n"] for r in states}}
