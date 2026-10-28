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
# Jobs already in your batch are re-checked too, so a settings change also cleans the batch.
RECHECK = REVIEWABLE + ("queued",)
# Jobs you've already seen as a card (or applied to). A repost of the same role never comes back.
SHOWN = ("queued", "approved", "applying", "submitted", "filled", "needs_you", "needs_code", "skipped",
         "rejected", "gave_up")
ALREADY_APPLIED = "already applied"
ALREADY_SEEN = "same role as a job you've seen"


def role_key(company_key: str, title: str) -> tuple[str, str]:
    """One role at one company, whatever the posting id or location: ('acme', 'data analyst')."""
    return company_key, " ".join(re.findall(r"[a-z0-9+#]+", (title or "").lower()))


def _words(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9+#]+", text.lower()))
    return words | {w[:-1] for w in words if w.endswith("s") and len(w) > 3}


def _seq(text: str) -> list[str]:
    return [w[:-1] if w.endswith("s") and len(w) > 3 else w for w in re.findall(r"[a-z0-9+#]+", text.lower())]


def title_matches(title: str, targets: list[str], max_gap: int = 1) -> bool:
    """True if a target's words appear in the title in order, with at most `max_gap` extra words between.

    "AI Engineer" matches "AI Platform Engineer" but not "Sales Engineer - AI Accounts".
    """
    words = _seq(title)
    for target in targets:
        want = _seq(target)
        if not want:
            continue
        for start, word in enumerate(words):
            if word != want[0]:
                continue
            pos, ok = start, True
            for w in want[1:]:
                nxt = next((i for i in range(pos + 1, min(len(words), pos + 2 + max_gap)) if words[i] == w), None)
                if nxt is None:
                    ok = False
                    break
                pos = nxt
            if ok:
                return True
    return False


# Title words that mark a role above early-career level.
SENIOR_TITLE = re.compile(
    r"\b(senior|sr|staff|principal|lead|leader|director|head|vp|vice president|manager|chief|distinguished"
    r"|fellow|architect|iii|iv|v)\b|\b(level|l)\s*[5-9]\b", re.I)
# Titles that contain a "senior" word but aren't senior, e.g. the generic AI-lab title.
NOT_SENIOR = re.compile(r"member of (the )?technical staff", re.I)
SENIORITY_LEVELS = {"early": "Early career (no Senior, Staff, Lead, Principal or Manager titles)", "any": "Any level"}


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
    if job["company_key"] in settings.get("blocked_companies", []):
        return "company blocked"
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
    if targets["exclude_titles"] and title_matches(job["title"], targets["exclude_titles"], max_gap=0):
        return "excluded title word"
    if targets.get("seniority", "any") == "early" and SENIOR_TITLE.search(NOT_SENIOR.sub(" ", job["title"])):
        return "above your level"
    if _age_days(job["posted_at"], job["first_seen"]) > settings["max_posting_age_days"]:
        return "posting too old"
    if settings["h1b_filter"] and country != "non-US":
        if NO_SPONSOR.search(job["description"] or ""):
            return "says it won't sponsor"
        if h1b is not None and not h1b.get("sponsor"):
            return "no recent H-1B record"
    return None


def worth_reading(settings: dict):
    """Whether a posting is worth opening for its details: False when its title or a known
    country already rules it out, so a scan doesn't spend requests on jobs it will drop."""
    targets, where = settings["targets"], settings["job_location"]

    def check(title: str, country: str | None) -> bool:
        if (where == "us" and country == "non-US") or (where == "non_us" and country == "US"):
            return False
        if targets["exclude_titles"] and title_matches(title, targets["exclude_titles"], max_gap=0):
            return False
        return not (targets.get("seniority", "any") == "early" and SENIOR_TITLE.search(NOT_SENIOR.sub(" ", title)))

    return check


def apply_filters(conn: sqlite3.Connection, settings: dict, enabled_keys: set[str], h1b_by_company: dict) -> dict:
    """Set every reviewable job to 'candidate' or 'filtered'. Returns counts by reason.

    Only one posting per role gets through: a role you applied to, or already saw
    as a card, is filtered before the AI ever looks at it.
    """
    counts: dict[str, int] = {}
    applied, seen = set(), set()
    settled = [s for s in SHOWN if s not in RECHECK]  # batch jobs claim their role in the loop below
    for r in conn.execute(f"SELECT company_key, title, status FROM jobs WHERE status IN ({','.join('?' * len(settled))})", settled):
        (applied if r["status"] == "submitted" else seen).add(role_key(r["company_key"], r["title"]))
    # Batch jobs first, then the oldest posting, so the copy you already have is the one kept.
    rows = conn.execute(f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(RECHECK))})"
                        " ORDER BY status != 'queued', first_seen, id", RECHECK).fetchall()
    for job in rows:
        reason = decide(job, settings, enabled_keys, h1b_by_company.get(job["company_key"]))
        role = role_key(job["company_key"], job["title"])
        if not reason and role in applied:
            reason = ALREADY_APPLIED
        elif not reason and role in seen:
            reason = ALREADY_SEEN
        if not reason:
            seen.add(role)
        if job["status"] == "queued" and not reason:
            continue  # still fine: stays in the batch
        status = "filtered" if reason else "candidate"
        counts[reason or "passed"] = counts.get(reason or "passed", 0) + 1
        if status != job["status"] or reason != job["status_reason"]:
            conn.execute("UPDATE jobs SET status=?, status_reason=? WHERE id=?", (status, reason, job["id"]))
    conn.commit()
    return counts


def retire_duplicates(conn: sqlite3.Connection, job_id: str) -> int:
    """After you apply to a job, drop every other posting of the same role from all card lists."""
    job = conn.execute("SELECT company_key, title FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return 0
    role = role_key(job["company_key"], job["title"])
    gone = 0
    for r in conn.execute("SELECT id, title FROM jobs WHERE company_key=? AND id != ?"
                          " AND status IN ('new','filtered','candidate','queued','skipped')",
                          (job["company_key"], job_id)).fetchall():
        if role_key(job["company_key"], r["title"]) == role:
            conn.execute("UPDATE jobs SET status='filtered', status_reason=? WHERE id=?", (ALREADY_APPLIED, r["id"]))
            conn.execute("DELETE FROM approvals WHERE job_id=?", (r["id"],))
            gone += 1
    return gone
