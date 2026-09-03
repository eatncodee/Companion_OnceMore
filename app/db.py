"""
Canonical persistent fact store.

SQLite is the source of truth for structured memory. Facts are versioned:
an update/contradiction inserts the new value and marks the old row as
superseded instead of deleting it. Chroma uses the SQLite fact id as its join
key for semantic retrieval.
"""

import sqlite3
import uuid
import time
from contextlib import contextmanager

DB_PATH = "data/memory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    subject TEXT,
    predicate TEXT,
    object TEXT,
    text TEXT NOT NULL,
    category TEXT,
    time_bound INTEGER DEFAULT 0,
    expires_at REAL,
    created_turn INTEGER,
    created_at REAL,
    superseded_by TEXT,
    superseded_reason TEXT
);

CREATE TABLE IF NOT EXISTS contradiction_log (
    id TEXT PRIMARY KEY,
    old_fact_id TEXT,
    new_fact_text TEXT,
    resolution TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def normalize_slot(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


@contextmanager
def get_conn(path: str = DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: str = DB_PATH):
    with get_conn(path) as conn:
        conn.executescript(SCHEMA)


def save_fact(subject, predicate, obj, text, category, created_turn,
              time_bound=False, expires_at=None, path: str = DB_PATH) -> str:
    fact_id = str(uuid.uuid4())
    subject = normalize_slot(subject)
    predicate = normalize_slot(predicate)
    obj = (obj or "").strip()
    category = normalize_slot(category)
    with get_conn(path) as conn:
        conn.execute(
            """INSERT INTO facts
               (id, subject, predicate, object, text, category, time_bound,
                expires_at, created_turn, created_at, superseded_by, superseded_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
            (fact_id, subject, predicate, obj, text.strip(), category, int(time_bound),
             expires_at, created_turn, time.time()),
        )
    return fact_id


def supersede_fact(old_fact_id: str, new_fact_id: str, reason: str, path: str = DB_PATH):
    with get_conn(path) as conn:
        conn.execute(
            "UPDATE facts SET superseded_by = ?, superseded_reason = ? WHERE id = ?",
            (new_fact_id, reason, old_fact_id),
        )


def log_contradiction(old_fact_id: str, new_fact_text: str, resolution: str, path: str = DB_PATH):
    with get_conn(path) as conn:
        conn.execute(
            """INSERT INTO contradiction_log (id, old_fact_id, new_fact_text, resolution, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), old_fact_id, new_fact_text, resolution, time.time()),
        )


def get_active_facts(path: str = DB_PATH):
    """All facts not superseded and not expired."""
    now = time.time()
    with get_conn(path) as conn:
        rows = conn.execute(
            """SELECT * FROM facts
               WHERE superseded_by IS NULL
               AND (expires_at IS NULL OR expires_at > ?)
               ORDER BY created_at ASC""",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_active_facts_by_slot(subject: str, predicate: str, path: str = DB_PATH):
    """Return active facts sharing the exact canonical (subject, predicate) slot."""
    now = time.time()
    subject = normalize_slot(subject)
    predicate = normalize_slot(predicate)
    with get_conn(path) as conn:
        rows = conn.execute(
            """SELECT * FROM facts
               WHERE subject = ?
               AND predicate = ?
               AND superseded_by IS NULL
               AND (expires_at IS NULL OR expires_at > ?)
               ORDER BY created_at DESC""",
            (subject, predicate, now),
        ).fetchall()
    return [dict(r) for r in rows]


def get_fact(fact_id: str, path: str = DB_PATH):
    with get_conn(path) as conn:
        row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    return dict(row) if row else None


def next_turn(path: str = DB_PATH) -> int:
    """Allocate a persistent conversation turn number across process restarts."""
    with get_conn(path) as conn:
        row = conn.execute("SELECT value FROM state WHERE key = 'turn_no'").fetchone()
        if row is None:
            max_turn = conn.execute(
                "SELECT COALESCE(MAX(created_turn), 0) AS max_turn FROM facts"
            ).fetchone()["max_turn"]
            turn = int(max_turn) + 1
        else:
            turn = int(row["value"])

        conn.execute(
            """INSERT INTO state(key, value) VALUES('turn_no', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (str(turn + 1),),
        )
    return turn
