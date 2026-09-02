"""
Structured fact store.

Design decision: SQLite instead of MongoDB.
The assignment leaves the storage choice open ("structured facts, embeddings,
hybrid — your choice, with reasoning"). SQLite gives the same "canonical
versioned document" behavior we'd get from Mongo (one row = one fact, easy
to query/update/supersede) with zero external server dependency — it's a
single file on disk, which matters for a "runnable from a README" CLI
deliverable that has to survive a process restart with no setup steps.
If this needed to be multi-user or scaled, Mongo would be the right call —
but that's explicitly out of scope here.

Facts are never hard-deleted on update. An update sets `superseded_by` on
the old row and inserts a new one. This keeps an audit trail and is what
makes contradiction-logging possible for the eval side later.
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
"""


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
    with get_conn(path) as conn:
        conn.execute(
            """INSERT INTO facts
               (id, subject, predicate, object, text, category, time_bound,
                expires_at, created_turn, created_at, superseded_by, superseded_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
            (fact_id, subject, predicate, obj, text, category, int(time_bound),
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
               AND (expires_at IS NULL OR expires_at > ?)""",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_fact(fact_id: str, path: str = DB_PATH):
    with get_conn(path) as conn:
        row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    return dict(row) if row else None
