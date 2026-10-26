"""Prepare each queued job for review: read its form questions and draft every answer."""

from __future__ import annotations

import sqlite3

import httpx

from . import answers as ans
from .companies import registry as reg
from .config import Paths
from .db import log_event
from .forms import questions_for
from .sources.base import http_client


def prepare_job(p: Paths, conn: sqlite3.Connection, job_id: str, company_name: str) -> int:
    """Fetch questions and draft answers. Returns the number of required questions left blank."""
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    with http_client() as client:
        questions = questions_for(client, job)
    rows = ans.draft_answers(p, dict(job), questions, company_name)
    ans.store(conn, job_id, rows)
    missing = len(ans.missing_required(rows))
    log_event(conn, "prepared", job_id, {"questions": len(rows), "missing": missing})
    conn.commit()
    return missing


def prepare_queue(p: Paths, conn: sqlite3.Connection, log=print) -> dict:
    names = {c["key"]: c["name"] for c in reg.load(p).values()}
    todo = conn.execute(
        "SELECT j.id, j.company_key FROM jobs j WHERE j.status='queued'"
        " AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.job_id = j.id)"
    ).fetchall()
    result = {"prepared": 0, "needs_input": 0, "closed": 0}
    for row in todo:
        try:
            missing = prepare_job(p, conn, row["id"], names.get(row["company_key"], row["company_key"]))
        except (httpx.HTTPStatusError, ValueError) as exc:
            conn.execute("UPDATE jobs SET status='closed', status_reason=? WHERE id=?", (f"form unavailable: {exc}"[:200], row["id"]))
            conn.commit()
            result["closed"] += 1
            continue
        result["prepared"] += 1
        result["needs_input"] += bool(missing)
    return result
