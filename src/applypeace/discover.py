"""Discovery: pull jobs from every enabled company's official ATS board into SQLite."""

from __future__ import annotations

import concurrent.futures as cf
import sqlite3
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

import httpx

from .companies import registry as reg
from .db import log_event, now
from .sources import LISTERS, workday
from .sources.base import Job, http_client


@dataclass
class DiscoverResult:
    boards: int = 0
    seen: int = 0
    new: int = 0
    closed: int = 0
    details: int = 0  # Workday job pages read
    errors: list[str] = field(default_factory=list)


# New Workday jobs come without a description; fetch at most this many job pages per scan.
WORKDAY_DETAILS_PER_SCAN = 300


def _fetch(company: dict, titles: list[str], words: list[str] | None = None) -> tuple[dict, list[Job] | None, str | None]:
    try:
        with http_client() as client:
            if company["ats"] == "workday":  # too big to list in full: search for your titles
                return company, workday.list_jobs(client, company["board"], titles, words), None
            return company, LISTERS[company["ats"]](client, company["board"]), None
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return company, None, f"{company['name']} ({company['ats']}:{company['board']}): {exc}"


def _workday_details(job: sqlite3.Row) -> tuple[str, dict | None]:
    try:
        with http_client() as client:
            return job["id"], workday.details(client, job["board"], job["url"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return job["id"], None


def fill_workday_details(conn: sqlite3.Connection, wanted=None, max_age_days: int = 31, workers: int = 6) -> int:
    """Read the job page of each recent Workday posting not yet read (its description and real location).

    `wanted(title, country)` skips postings the filters would drop anyway. A job filtered only
    because its location was unknown ("3 Locations") is read too, then filtered again.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).date().isoformat()
    rows = conn.execute(
        "SELECT id, board, url, title, country FROM jobs WHERE ats='workday' AND description IS NULL"
        " AND status IN ('new', 'filtered') AND (posted_at IS NULL OR posted_at >= ?) ORDER BY posted_at DESC",
        (since,)).fetchall()
    todo = [r for r in rows if not wanted or wanted(r["title"], r["country"])][:WORKDAY_DETAILS_PER_SCAN]
    done = 0
    with cf.ThreadPoolExecutor(workers) as pool:
        for job_id, info in pool.map(_workday_details, todo):
            if not info:
                continue
            conn.execute("UPDATE jobs SET description=?, location=COALESCE(?, location), country=COALESCE(?, country),"
                         " remote=?, posted_at=COALESCE(?, posted_at) WHERE id=?",
                         (info["description"], info["location"], info["country"], info["remote"], info["posted_at"], job_id))
            done += 1
    conn.commit()
    return done


def upsert_jobs(conn: sqlite3.Connection, company_key: str, jobs: list[Job]) -> int:
    stamp, new = now(), 0
    for job in jobs:
        existing = conn.execute("SELECT id FROM jobs WHERE id = ?", (job.id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE jobs SET title=?, location=COALESCE(?, location), country=COALESCE(?, country),"
                " remote=COALESCE(?, remote), url=?, apply_url=?,"
                " posted_at=COALESCE(posted_at, ?), description=COALESCE(?, description), last_seen=?,"
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


def discover(conn: sqlite3.Connection, companies: list[dict], titles: list[str] | None = None,
             wanted=None, words: list[str] | None = None, workers: int = 8) -> DiscoverResult:
    result = DiscoverResult()
    boards = [c for c in companies if c.get("ats") in reg.SUPPORTED_ATS and c.get("board") and c.get("verified")]
    stamp = now()
    with cf.ThreadPoolExecutor(workers) as pool:
        for company, jobs, error in pool.map(lambda c: _fetch(c, titles or [], words), boards):
            if error:
                result.errors.append(error)
                continue
            result.boards += 1
            result.seen += len(jobs)
            result.new += upsert_jobs(conn, company["key"], jobs)
            # Postings that disappeared from a board we just read are closed. A Workday scan only
            # sees search results, so a job already in your batch isn't closed just for dropping out.
            statuses = ("new", "filtered", "ranked") + (() if company["ats"] == "workday" else ("queued",))
            closed = conn.execute(
                "UPDATE jobs SET status='closed' WHERE company_key=? AND last_seen < ?"
                f" AND status IN ({','.join('?' * len(statuses))})",
                (company["key"], stamp, *statuses),
            ).rowcount
            result.closed += closed
    result.details = fill_workday_details(conn, wanted)
    log_event(conn, "discover", detail=result.__dict__)
    conn.commit()
    return result
