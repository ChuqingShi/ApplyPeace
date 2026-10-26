"""Stage 1: free rule filters. Re-run every cycle, so changing a setting takes effect at once."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

# Phrases that mean the employer will not sponsor (checked only for US jobs with the H-1B filter on).
NO_SPONSOR = re.compile(
    r"(unable|not able|will not|won't|cannot|can't|do(es)? not|are not able)( to)? (provide |offer )?"
    r"(visa )?sponsor|no (visa )?sponsorship|without (the need for )?(current or future )?(visa )?sponsorship"
    r"|must be (a )?u\.?s\.? citizen|u\.?s\.? citizenship (is )?required|requires? (u\.?s\.? )?citizenship"
    r"|(active|current) (secret|top secret|ts/sci)( security)? clearance|security clearance (is )?required"
    r"|green card holders? only|\bitar\b",
    re.I,
)

REVIEWABLE = ("new", "filtered", "candidate")


def _words(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9+#]+", text.lower()))
    return words | {w[:-1] for w in words if w.endswith("s") and len(w) > 3}


def title_matches(title: str, targets: list[str]) -> bool:
    words = _words(title)
    return any(set(re.findall(r"[a-z0-9+#]+", t.lower())) <= words for t in targets if t.strip())


def _age_days(posted_at: str | None, first_seen: str) -> float:
    stamp = posted_at or first_seen
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        when = datetime.fromisoformat(first_seen)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when) / timedelta(days=1)


def decide(job: sqlite3.Row, settings: dict, enabled_keys: set[str], h1b: dict | None) -> str | None:
    """Return a filter reason, or None if the job passes."""
    targets = settings["targets"]
    if job["company_key"] not in enabled_keys:
        return "company list switched off"
    country, remote = job["country"], bool(job["remote"])
    where = settings["job_location"]
    if where == "us" and country != "US" and not (country is None and remote and targets["remote_ok"]):
        return "outside the US"
    if where == "non_us" and country == "US":
        return "in the US"
    if remote and not targets["remote_ok"]:
        return "remote"
    if targets["locations"] and not (remote and targets["remote_ok"]):
        loc = (job["location"] or "").lower()
        if not any(l.lower() in loc for l in targets["locations"]):
            return "not in preferred locations"
    if targets["titles"] and not title_matches(job["title"], targets["titles"]):
        return "title not targeted"
    if targets["exclude_titles"] and title_matches(job["title"], targets["exclude_titles"]):
        return "excluded title word"
    if _age_days(job["posted_at"], job["first_seen"]) > settings["max_posting_age_days"]:
        return "posting too old"
    if settings["h1b_filter"] and country != "non-US":
        if NO_SPONSOR.search(job["description"] or ""):
            return "says it won't sponsor"
        if h1b is not None and not h1b.get("sponsor"):
            return "no recent H-1B record"
    return None


def apply_filters(conn: sqlite3.Connection, settings: dict, enabled_keys: set[str], h1b_by_company: dict) -> dict:
    """Set every reviewable job to 'candidate' or 'filtered'. Returns counts by reason."""
    counts: dict[str, int] = {}
    rows = conn.execute(f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(REVIEWABLE))})", REVIEWABLE).fetchall()
    for job in rows:
        reason = decide(job, settings, enabled_keys, h1b_by_company.get(job["company_key"]))
        status = "filtered" if reason else "candidate"
        counts[reason or "passed"] = counts.get(reason or "passed", 0) + 1
        if status != job["status"] or reason != job["status_reason"]:
            conn.execute("UPDATE jobs SET status=?, status_reason=? WHERE id=?", (status, reason, job["id"]))
    conn.commit()
    return counts
