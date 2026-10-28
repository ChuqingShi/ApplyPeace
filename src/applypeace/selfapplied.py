"""Track applications you made yourself, outside ApplyPeace, in the same history."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date
from urllib.parse import parse_qs, urlparse

from .companies import registry as reg
from .companies.names import key_for, same_company
from .config import Paths
from .db import log_event, now
from .rank.filters import retire_duplicates

_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([^/?#]+)/jobs/(\d+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})")),
    ("lever", re.compile(r"jobs\.lever\.co/([^/?#]+)/([0-9a-f-]{36})")),
]


def job_id_from_url(conn: sqlite3.Connection, url: str) -> str | None:
    """The ApplyPeace job id for a posting URL, if it points at a known or supported ATS posting."""
    for ats, pattern in _PATTERNS:
        m = pattern.search(url)
        if m:
            return f"{ats}:{m.group(1).lower()}:{m.group(2)}"
    gh = parse_qs(urlparse(url).query).get("gh_jid")  # Greenhouse jobs embedded on a company site
    if gh:
        row = conn.execute("SELECT id FROM jobs WHERE ats='greenhouse' AND ats_job_id=?", (gh[0],)).fetchone()
        if row:
            return row["id"]
    row = conn.execute("SELECT id FROM jobs WHERE url=? OR apply_url=?", (url, url)).fetchone()
    return row["id"] if row else None


def mark_applied(conn: sqlite3.Connection, job_id: str, applied_on: str | None = None,
                 resume_key: str | None = None, note: str = "") -> None:
    """Record that you applied to this job yourself (overrides skip, reject or give-up)."""
    when = (applied_on or date.today().isoformat())
    stamp = when if "T" in when else f"{when}T12:00:00+00:00"
    conn.execute("DELETE FROM approvals WHERE job_id=?", (job_id,))
    conn.execute(
        "INSERT INTO applications (job_id, status, started_at, finished_at, detail, method, note)"
        " VALUES (?, 'submitted', ?, ?, 'Applied by you', 'you', ?)"
        " ON CONFLICT(job_id) DO UPDATE SET status='submitted', finished_at=excluded.finished_at,"
        " detail='Applied by you', method='you', note=excluded.note",
        (job_id, stamp, stamp, note or None),
    )
    if resume_key:
        conn.execute("UPDATE jobs SET resume_key=? WHERE id=?", (resume_key, job_id))
    conn.execute("UPDATE jobs SET status='submitted', status_reason='applied by you' WHERE id=?", (job_id,))
    retire_duplicates(conn, job_id)
    log_event(conn, "applied_by_you", job_id, {"on": when})
    conn.commit()


def add_external(p: Paths, conn: sqlite3.Connection, company: str, title: str, url: str = "",
                 applied_on: str | None = None, resume_key: str | None = None, note: str = "",
                 location: str = "") -> str:
    """Log an application made anywhere (LinkedIn, a referral, a company site). Returns the job id."""
    job_id = job_id_from_url(conn, url) if url else None
    if job_id and conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
        mark_applied(conn, job_id, applied_on, resume_key, note)
        return job_id
    known = next((c for c in reg.load(p).values() if same_company(c["name"], company)), None)
    company_key = known["key"] if known else key_for(company)
    if job_id:
        ats, board, ats_job_id = job_id.split(":", 2)
    else:
        digest = hashlib.sha1(f"{company}|{title}|{url}".lower().encode()).hexdigest()[:10]
        ats, board, ats_job_id = "manual", company_key, digest
        job_id = f"manual:{company_key}:{digest}"
    stamp = now()
    conn.execute(
        "INSERT OR IGNORE INTO jobs (id, company_key, company_name, ats, board, ats_job_id, title, location, url,"
        " first_seen, last_seen, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,'submitted')",
        (job_id, company_key, company.strip(), ats, board, ats_job_id, title.strip(), location or None, url or "",
         stamp, stamp),
    )
    mark_applied(conn, job_id, applied_on, resume_key, note)
    return job_id
