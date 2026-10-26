"""Discovery: pull jobs from every enabled company's official ATS board into SQLite."""

from __future__ import annotations

import concurrent.futures as cf
import sqlite3
from dataclasses import dataclass, field

import httpx

from .companies import registry as reg
from .db import log_event, now
from .sources import LISTERS
from .sources.base import Job, http_client


@dataclass
class DiscoverResult:
    boards: int = 0
    seen: int = 0
    new: int = 0
    closed: int = 0
    errors: list[str] = field(default_factory=list)


def _fetch(company: dict) -> tuple[dict, list[Job] | None, str | None]:
    try:
        with http_client() as client:
            return company, LISTERS[company["ats"]](client, company["board"]), None
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return company, None, f"{company['name']} ({company['ats']}:{company['board']}): {exc}"


def upsert_jobs(conn: sqlite3.Connection, company_key: str, jobs: list[Job]) -> int:
    stamp, new = now(), 0
    for job in jobs:
        existing = conn.execute("SELECT id FROM jobs WHERE id = ?", (job.id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE jobs SET title=?, location=?, country=?, remote=?, url=?, apply_url=?,"
                " posted_at=COALESCE(posted_at, ?), description=?, last_seen=?,"
                " status=CASE WHEN status='closed' THEN 'new' ELSE status END WHERE id=?",
                (job.title, job.location, job.country, job.remote, job.url, job.apply_url,
                 job.posted_at, job.description, stamp, job.id),
            )
        else:
            conn.execute(
                "INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, location, country,"
                " remote, url, apply_url, posted_at, first_seen, last_seen, description)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job.id, company_key, job.ats, job.board, job.ats_job_id, job.title, job.location,
                 job.country, job.remote, job.url, job.apply_url, job.posted_at, stamp, stamp,
                 job.description),
            )
            new += 1
    return new


def discover(conn: sqlite3.Connection, companies: list[dict], workers: int = 6) -> DiscoverResult:
    result = DiscoverResult()
    boards = [c for c in companies if c.get("ats") in reg.SUPPORTED_ATS and c.get("board") and c.get("verified")]
    stamp = now()
    with cf.ThreadPoolExecutor(workers) as pool:
        for company, jobs, error in pool.map(_fetch, boards):
            if error:
                result.errors.append(error)
                continue
            result.boards += 1
            result.seen += len(jobs)
            result.new += upsert_jobs(conn, company["key"], jobs)
            # Postings that disappeared from a board we just read are closed.
            closed = conn.execute(
                "UPDATE jobs SET status='closed' WHERE company_key=? AND last_seen < ?"
                " AND status IN ('new','filtered','ranked','queued')",
                (company["key"], stamp),
            ).rowcount
            result.closed += closed
    log_event(conn, "discover", detail=result.__dict__)
    conn.commit()
    return result
