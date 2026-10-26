"""Approvals lock the exact answers and resume you saw. Any later change sends the job back to review."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from . import answers as ans
from . import profile
from .config import Paths
from .db import log_event, now


def answers_sha(rows: list[dict]) -> str:
    canonical = json.dumps([[r["field"], r["value"]] for r in rows], sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def resume_entry(p: Paths, key: str | None) -> dict:
    entries = profile.resumes_index(p)
    return next((r for r in entries if r["key"] == key), entries[0] if entries else None)


def approve(p: Paths, conn: sqlite3.Connection, job_id: str) -> None:
    rows = ans.load(conn, job_id)
    missing = ans.missing_required(rows)
    if missing:
        raise ValueError("Answer these first: " + "; ".join(r["label"] for r in missing))
    job = conn.execute("SELECT resume_key FROM jobs WHERE id=?", (job_id,)).fetchone()
    resume = resume_entry(p, job["resume_key"])
    sha = profile.sha256(p.resumes / resume["file"])
    conn.execute(
        "INSERT OR REPLACE INTO approvals (job_id, approved_at, resume_key, resume_sha, answers_sha, snapshot)"
        " VALUES (?,?,?,?,?,?)",
        (job_id, now(), resume["key"], sha, answers_sha(rows), json.dumps(rows, default=str)),
    )
    conn.execute("UPDATE jobs SET status='approved', resume_key=? WHERE id=?", (resume["key"], job_id))
    log_event(conn, "approved", job_id)
    conn.commit()


def revoke(conn: sqlite3.Connection, job_id: str, reason: str = "edited") -> None:
    """Drop an approval (e.g. after an edit) so the job must be reviewed again."""
    if conn.execute("DELETE FROM approvals WHERE job_id=?", (job_id,)).rowcount:
        conn.execute("UPDATE jobs SET status='queued' WHERE id=? AND status='approved'", (job_id,))
        log_event(conn, "approval_revoked", job_id, {"reason": reason})
    conn.commit()


def verify(p: Paths, conn: sqlite3.Connection, job_id: str) -> tuple[bool, str]:
    """True only if the answers and resume are byte-for-byte what was approved."""
    approval = conn.execute("SELECT * FROM approvals WHERE job_id=?", (job_id,)).fetchone()
    if not approval:
        return False, "not approved"
    if answers_sha(ans.load(conn, job_id)) != approval["answers_sha"]:
        return False, "answers changed after approval"
    resume = resume_entry(p, approval["resume_key"])
    if not resume or profile.sha256(p.resumes / resume["file"]) != approval["resume_sha"]:
        return False, "resume changed after approval"
    return True, "ok"
