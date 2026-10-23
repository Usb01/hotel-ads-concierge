import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,          -- meta | google | whatsapp | web
    external_id   TEXT,
    campaign      TEXT,
    ad_name       TEXT,
    name          TEXT,
    email         TEXT,
    phone         TEXT,
    status        TEXT NOT NULL DEFAULT 'new',  -- new | replied | needs_staff | booked | lost
    submitted_at  TEXT,                  -- when the guest filled the form (from the ad platform)
    created_at    TEXT NOT NULL,         -- when we received it
    first_reply_at TEXT,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY,
    lead_id    INTEGER NOT NULL REFERENCES leads(id),
    direction  TEXT NOT NULL,             -- in | out
    channel    TEXT NOT NULL,             -- whatsapp | email | form
    body       TEXT NOT NULL,
    intent     TEXT,
    author     TEXT NOT NULL DEFAULT 'guest',  -- guest | bot | staff
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoffs (
    id         INTEGER PRIMARY KEY,
    lead_id    INTEGER NOT NULL REFERENCES leads(id),
    reason     TEXT NOT NULL,
    details    TEXT,
    resolved   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY,
    lead_id    INTEGER NOT NULL,
    channel    TEXT NOT NULL,
    recipient  TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL,             -- dry_run | sent | failed
    error      TEXT,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
