"""The Activity log: every scan, daily summary and application, newest first."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

# What you did with a job, from its current status.
YOUR_ACTION = {
    "queued": ("Waiting for you", "wait"), "approved": ("You approved", "done"), "applying": ("You approved", "done"),
    "submitted": ("Applied", "done"), "filled": ("You approved (dry run)", "done"),
    "rejected": ("You rejected", "done"), "skipped": ("You kept it for later", "done"),
    "needs_you": ("Needs you", "wait"), "needs_code": ("Needs your code", "wait"),
    "gave_up": ("You gave up", "done"), "closed": ("Posting closed", "gone"),
    "candidate": ("Back in the pool", "gone"), "filtered": ("Filtered out after a settings change", "gone"),
}
APPLY_WORDS = {"submitted": "Submitted", "filled": "Filled (dry run, not sent)", "needs_you": "Handed to you",
               "needs_code": "Waiting for an emailed code", "failed": "Failed", "queued": "Sent back to review"}


def scan_summary(d: dict) -> str:
    parts = []
    if d.get("boards") is not None:
        parts.append(f"checked {d['boards']} job boards")
    parts.append(f"{d.get('new', 0)} new postings")
    if d.get("passed") is not None:
        parts.append(f"{d['passed']} matched your filters")
    if d.get("judged"):
        parts.append(f"AI scored {d['judged']}")
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


def local(stamp: str, tz: str) -> str:
    when = datetime.fromisoformat(stamp)
    return when.astimezone(ZoneInfo(tz)).strftime("%a %b %-d, %-I:%M %p")


def entries(conn: sqlite3.Connection, names: dict[str, str], tz: str, limit: int = 60) -> list[dict]:
    jobs = {r["id"]: r for r in conn.execute("SELECT id, title, company_key, company_name, status FROM jobs")}

    def job_view(job_id: str) -> dict:
        j = jobs.get(job_id)
        if not j:
            return {"id": job_id, "title": job_id, "company": "", "action": ("Unknown", "gone")}
        return {"id": job_id, "title": j["title"], "company": j["company_name"] or names.get(j["company_key"], j["company_key"]),
                "action": YOUR_ACTION.get(j["status"], (j["status"], "gone"))}

    rows = conn.execute(
        "SELECT * FROM events WHERE kind='cycle' OR kind='notify' OR kind LIKE 'apply_%' ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    out = []
    for r in rows:
        detail = json.loads(r["detail"]) if r["detail"] else {}
        item = {"at": local(r["at"], tz), "kind": r["kind"]}
        if r["kind"] == "cycle":
            queued = detail.get("queued")
            item.update(
                title="Manual scan" if detail.get("trigger") == "manual" else "Scheduled scan",
                summary=scan_summary(detail),
                jobs=[job_view(j) for j in queued] if isinstance(queued, list) else [],
                added=len(queued) if isinstance(queued, list) else queued or 0,
                notified=detail.get("notified"), message=detail.get("message"),
                errors=detail.get("errors") or [])
        elif r["kind"] == "notify":
            item.update(title="Daily summary", summary=detail.get("message", ""), jobs=[], added=None,
                        notified=detail.get("sent"), message=None, errors=[])
        else:
            status = r["kind"].removeprefix("apply_")
            item.update(title=f"Application: {APPLY_WORDS.get(status, status)}", summary=(detail.get("detail") or ""),
                        jobs=[job_view(r["job_id"])] if r["job_id"] else [], added=None, notified=None, message=None,
                        errors=[])
        out.append(item)
    return out
