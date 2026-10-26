"""SQLite storage for jobs, approvals and applications."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,           -- "<ats>:<board>:<job id>"
    company_key   TEXT NOT NULL,
    ats           TEXT NOT NULL,
    board         TEXT NOT NULL,
    ats_job_id    TEXT NOT NULL,
    title         TEXT NOT NULL,
    location      TEXT,
    country       TEXT,                       -- "US", other ISO-ish name, or NULL if unknown
    remote        INTEGER,
    url           TEXT NOT NULL,
    apply_url     TEXT,
    posted_at     TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'new',
    status_reason TEXT,
    keyword_score REAL,
    judge         TEXT,                       -- JSON from the LLM judge
    final_score   REAL,
    resume_key    TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company_key);

CREATE TABLE IF NOT EXISTS answers (
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    field       TEXT NOT NULL,                -- ATS field name
    label       TEXT NOT NULL,
    kind        TEXT NOT NULL,                -- text | textarea | select | multiselect | file | boolean
    required    INTEGER NOT NULL,
    options     TEXT,                         -- JSON list of {label, value}
    value       TEXT,                         -- JSON value chosen
    source      TEXT,                         -- profile | answers | llm | user | none
    position    INTEGER NOT NULL,
    section     TEXT,                         -- standard | custom | eeo
    PRIMARY KEY (job_id, field)
);

CREATE TABLE IF NOT EXISTS approvals (
    job_id       TEXT PRIMARY KEY REFERENCES jobs(id),
    approved_at  TEXT NOT NULL,
    resume_key   TEXT NOT NULL,
    resume_sha   TEXT NOT NULL,
    answers_sha  TEXT NOT NULL,
    snapshot     TEXT NOT NULL                -- JSON of the exact answers approved
);

CREATE TABLE IF NOT EXISTS applications (
    job_id        TEXT PRIMARY KEY REFERENCES jobs(id),
    status        TEXT NOT NULL,              -- submitted | needs_you | failed | filled
    started_at    TEXT,
    finished_at   TEXT,
    detail        TEXT,
    screenshot_before TEXT,
    screenshot_after  TEXT,
    submitted_values  TEXT                    -- JSON
);

CREATE TABLE IF NOT EXISTS codes (
    job_id  TEXT NOT NULL,
    code    TEXT NOT NULL,
    at      TEXT NOT NULL,
    used    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    job_id  TEXT,
    detail  TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was created."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(answers)")}
    if "section" not in columns:
        conn.execute("ALTER TABLE answers ADD COLUMN section TEXT")


def log_event(conn: sqlite3.Connection, kind: str, job_id: str | None = None, detail=None) -> None:
    conn.execute(
        "INSERT INTO events (at, kind, job_id, detail) VALUES (?, ?, ?, ?)",
        (now(), kind, job_id, json.dumps(detail) if detail is not None else None),
    )
