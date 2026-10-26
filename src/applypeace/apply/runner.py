"""Submit approved applications one at a time, with a full record and hand-off to you when needed."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

from .. import answers as ans
from .. import approval, profile
from ..browser import launch
from ..config import Paths, load_settings
from ..db import log_event, now
from ..notify import notify
from . import ashby, greenhouse, lever
from .base import NeedsCode, NeedsYou

FILLERS = {"greenhouse": greenhouse, "ashby": ashby, "lever": lever}
CODE_WAIT_SECONDS = 15 * 60


def submitted_today(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM applications WHERE status='submitted' AND substr(finished_at,1,10)=?",
        (date.today().isoformat(),),
    ).fetchone()[0]


def artifact_dir(p: Paths, job_id: str) -> Path:
    d = p.artifacts / re.sub(r"[^A-Za-z0-9_.-]+", "_", job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _record(conn, job_id, status, detail="", before=None, after=None, values=None, started=None):
    conn.execute(
        "INSERT INTO applications (job_id, status, started_at, finished_at, detail, screenshot_before,"
        " screenshot_after, submitted_values) VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, finished_at=excluded.finished_at,"
        " detail=excluded.detail, screenshot_before=COALESCE(excluded.screenshot_before, screenshot_before),"
        " screenshot_after=COALESCE(excluded.screenshot_after, screenshot_after),"
        " submitted_values=COALESCE(excluded.submitted_values, submitted_values)",
        (job_id, status, started or now(), now(), detail, before, after, json.dumps(values) if values else None),
    )
    conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    log_event(conn, f"apply_{status}", job_id, {"detail": detail})
    conn.commit()


def _wait_for_code(conn: sqlite3.Connection, job_id: str) -> str | None:
    deadline = time.time() + CODE_WAIT_SECONDS
    while time.time() < deadline:
        row = conn.execute("SELECT code FROM codes WHERE job_id=? AND used=0 ORDER BY at DESC LIMIT 1", (job_id,)).fetchone()
        if row:
            conn.execute("UPDATE codes SET used=1 WHERE job_id=?", (job_id,))
            conn.commit()
            return row["code"].strip()
        time.sleep(3)
    return None


def apply_one(p: Paths, conn: sqlite3.Connection, job_id: str, dry_run: bool | None = None,
              upload: bool = True, review_url: str = "") -> str:
    """Apply to one approved job. Returns the final status."""
    settings = load_settings(p)
    dry_run = settings["submit_mode"] != "auto" if dry_run is None else dry_run
    ok, why = approval.verify(p, conn, job_id)
    if not ok:
        approval.revoke(conn, job_id, why)
        return "queued"
    job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    filler = FILLERS[job["ats"]]
    rows = ans.load(conn, job_id)
    appr = conn.execute("SELECT resume_key FROM approvals WHERE job_id=?", (job_id,)).fetchone()
    resume = p.resumes / approval.resume_entry(p, appr["resume_key"])["file"]
    folder = artifact_dir(p, job_id)
    (folder / "job_description.txt").write_text(f"{job['title']}\n{job['url']}\n\n{job['description'] or ''}")
    (folder / "answers.json").write_text(json.dumps(rows, indent=1, default=str))
    started = now()
    conn.execute("UPDATE jobs SET status='applying' WHERE id=?", (job_id,))
    conn.commit()
    before = str(folder / "filled.png")
    after = str(folder / "confirmation.png")
    page = None
    try:
        with launch() as browser:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.goto(filler.form_url(job), wait_until="networkidle", timeout=60000)
            country = (profile.load_profile(p).get("location") or {}).get("country") or "United States"
            kwargs = {"upload": upload}
            if job["ats"] == "greenhouse":
                kwargs["country_label"] = country
            report = filler.fill(page, rows, resume, **kwargs)
            page.screenshot(path=before, full_page=True)
            if dry_run:
                _record(conn, job_id, "filled", "Dry run: form filled, not submitted.", before, None, report.filled, started)
                return "filled"
            outcome = filler.submit(page)
            if outcome == "code":
                _record(conn, job_id, "needs_code", "The site emailed you a verification code.", before, None, report.filled, started)
                notify(f"{job['title']}: the site emailed you a code. Enter it on the review page within 15 minutes.",
                       open_url=f"{review_url}/jobs/{job_id}" if review_url else None)
                code = _wait_for_code(conn, job_id)
                if not code:
                    raise NeedsYou("code_timeout", "No code was entered in time.")
                outcome = filler.enter_code(page, code)
            page.screenshot(path=after, full_page=True)
            _record(conn, job_id, "submitted", "Submitted.", before, after, report.filled, started)
            return "submitted"
    except NeedsYou as exc:
        if page is not None:
            try:
                page.screenshot(path=after, full_page=True)
            except Exception:
                after = None
        _record(conn, job_id, "needs_you", f"{exc.reason}: {exc.detail}", None, after, None, started)
        notify(f"Needs you: {job['title']} ({exc.reason.replace('_', ' ')}). Your answers are ready to copy.",
               open_url=f"{review_url}/jobs/{job_id}" if review_url else None)
        return "needs_you"
    except NeedsCode:
        return "needs_code"
    except Exception as exc:  # a broken page must never leave the job stuck in 'applying'
        _record(conn, job_id, "failed", f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}", None, None, None, started)
        return "failed"


def run_queue(p: Paths, conn: sqlite3.Connection, review_url: str = "", max_jobs: int | None = None, log=print) -> dict:
    """Apply to approved jobs, respecting pause, the daily cap and spacing."""
    settings = load_settings(p)
    results: dict[str, int] = {}
    if settings.get("paused"):
        log("Paused: not applying.")
        return results
    approved = [r["job_id"] for r in conn.execute(
        "SELECT a.job_id FROM approvals a JOIN jobs j ON j.id=a.job_id WHERE j.status='approved' ORDER BY a.approved_at")]
    for i, job_id in enumerate(approved[:max_jobs] if max_jobs else approved):
        if load_settings(p).get("paused"):
            break
        if settings["submit_mode"] == "auto" and submitted_today(conn) >= settings["daily_cap"]:
            log("Daily cap reached; the rest wait for tomorrow.")
            break
        if i:
            time.sleep(settings.get("apply_gap_seconds", 120))
        status = apply_one(p, conn, job_id, review_url=review_url)
        results[status] = results.get(status, 0) + 1
        log(f"{job_id}: {status}")
    return results
