"""Review actions shared by the phone page, the CLI and chat: edit, approve, skip, reject, redraft."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date

from . import answers as ans
from . import approval, llm, profile
from .config import Paths
from .db import log_event

REASON_TAGS = {
    "too_senior": "too senior",
    "too_junior": "too junior",
    "wrong_field": "wrong field",
    "location": "location",
    "company": "company",
    "sponsorship": "visa / sponsorship",
    "other": "other",
}
# Words in the AI's concerns that point at a reject reason, checked in order.
REASON_HINTS = [
    ("sponsorship", re.compile(r"u\.?s\.? person|citizen|clearance|sponsor|visa|h-?1b|green card|itar|export control", re.I)),
    ("too_senior", re.compile(r"senior|years of experience|\d+\+? years|too junior for", re.I)),
    ("location", re.compile(r"relocat|on-?site|location|in-office", re.I)),
]


def suggest_reason(judge: dict | None) -> str:
    """The likeliest reject reason for a job the AI skipped, to preselect in the dropdown."""
    text = " ".join([*(judge or {}).get("concerns", []), (judge or {}).get("sponsorship_clues") or ""])
    return next((key for key, pattern in REASON_HINTS if pattern.search(text)), "other")


PREFERENCE_HEADER = "# Preferences learned from your choices\n\nEdit freely; the job picker reads this file.\n\n"


def set_answers(p: Paths, conn: sqlite3.Connection, job_id: str, values: dict, save_defaults: set[str]) -> bool:
    """Apply edited values. Returns True if anything changed (which revokes an approval)."""
    rows = {r["field"]: r for r in ans.load(conn, job_id)}
    changed = False
    for field, value in values.items():
        row = rows.get(field)
        if row is None or row["kind"] == "file":
            continue
        if value != row["value"]:
            conn.execute("UPDATE answers SET value=?, source='user' WHERE job_id=? AND field=?",
                         (json.dumps(value), job_id, field))
            changed = True
        if field in save_defaults and value not in (None, "", []):
            stored = value
            if row["options"]:  # save the option label, so it matches other companies' option ids
                labels = {str(o["value"]): o["label"] for o in row["options"]}
                stored = [labels.get(str(v), v) for v in value] if isinstance(value, list) else labels.get(str(value), value)
                if isinstance(stored, list) and len(stored) == 1:
                    stored = stored[0]
            ans.remember(p, row["label"], stored)
    conn.commit()
    if changed:
        approval.revoke(conn, job_id, "edited")
    return changed


def set_resume(conn: sqlite3.Connection, job_id: str, resume_key: str) -> None:
    conn.execute("UPDATE jobs SET resume_key=? WHERE id=?", (resume_key, job_id))
    conn.commit()
    approval.revoke(conn, job_id, "resume changed")


def skip(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET status='skipped' WHERE id=?", (job_id,))
    log_event(conn, "skipped", job_id)
    conn.commit()


def reject(p: Paths, conn: sqlite3.Connection, job_id: str, reason: str = "other", block_company: bool = False) -> None:
    job = conn.execute("SELECT title, company_key FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.execute("DELETE FROM approvals WHERE job_id=?", (job_id,))
    conn.execute("UPDATE jobs SET status='rejected', status_reason=? WHERE id=?", (REASON_TAGS.get(reason, reason), job_id))
    log_event(conn, "rejected", job_id, {"reason": reason})
    conn.commit()
    remember_choice(p, f"Rejected ({REASON_TAGS.get(reason, reason)}): {job['title']} at {job['company_key']}")
    if block_company:
        from .config import load_settings, save_settings

        settings = load_settings(p)
        blocked = set(settings.get("blocked_companies", []))
        blocked.add(job["company_key"])
        settings["blocked_companies"] = sorted(blocked)
        save_settings(settings, p)


def restore(conn: sqlite3.Connection, job_id: str) -> None:
    """Bring a skipped or AI-skipped job back into the review queue."""
    conn.execute("UPDATE jobs SET status='queued' WHERE id=?", (job_id,))
    log_event(conn, "restored", job_id)
    conn.commit()


def remember_choice(p: Paths, line: str) -> None:
    """Append a choice to preferences.md (newest last, capped) for the job picker to read."""
    text = p.preferences.read_text() if p.preferences.exists() else PREFERENCE_HEADER
    head, _, body = text.partition("## Recent choices\n")
    lines = [l for l in body.splitlines() if l.strip()][-59:]
    lines.append(f"- {date.today().isoformat()} {line}")
    p.preferences.parent.mkdir(parents=True, exist_ok=True)
    p.preferences.write_text((head if head.strip() else PREFERENCE_HEADER) + "## Recent choices\n" + "\n".join(lines) + "\n")


REDRAFT_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def redraft(p: Paths, conn: sqlite3.Connection, job_id: str, field: str, instruction: str) -> str:
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    row = next(r for r in ans.load(conn, job_id) if r["field"] == field)
    resume_key = job["resume_key"] or (profile.resumes_index(p) or [{}])[0].get("key")
    resume = profile.resume_text(p, resume_key) if resume_key else ""
    prompt = f"""Question: {row['label']}
Current draft: {row['value'] or '(empty)'}
Candidate's instruction: {instruction or 'improve it'}
Role: {job['title']}
Job description (excerpt): {(job['description'] or '')[:5000]}
Candidate resume: {resume[:8000]}"""
    out = llm.ask_json(p, ans.ESSAY_SYSTEM + "\nRewrite the draft following the candidate's instruction.",
                       prompt, REDRAFT_SCHEMA)
    text = out["answer"].strip()
    conn.execute("UPDATE answers SET value=?, source='llm' WHERE job_id=? AND field=?", (json.dumps(text), job_id, field))
    conn.commit()
    approval.revoke(conn, job_id, "redrafted")
    return text


def approve_ready(p: Paths, conn: sqlite3.Connection) -> int:
    """Approve every queued job that has all required answers (Approve all remaining)."""
    count = 0
    for row in conn.execute("SELECT id FROM jobs WHERE status='queued'").fetchall():
        if not ans.missing_required(ans.load(conn, row["id"])):
            approval.approve(p, conn, row["id"])
            count += 1
    return count


def submit_code(conn: sqlite3.Connection, job_id: str, code: str) -> None:
    from .db import now

    conn.execute("INSERT INTO codes (job_id, code, at) VALUES (?,?,?)", (job_id, code.strip(), now()))
    conn.commit()


def mark_done_by_hand(conn: sqlite3.Connection, job_id: str, submitted: bool) -> None:
    if submitted:
        from .selfapplied import mark_applied

        mark_applied(conn, job_id, note="Finished by you after ApplyPeace handed it over")
        return
    status = "gave_up"
    from .db import now

    conn.execute("UPDATE applications SET status=?, finished_at=?, detail=COALESCE(detail,'') || ? WHERE job_id=?",
                 (status, now(), " (finished by you)" if submitted else " (given up)", job_id))
    conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    log_event(conn, f"manual_{status}", job_id)
    conn.commit()


def set_outcome(conn: sqlite3.Connection, job_id: str, outcome: str) -> None:
    """Record what happened after applying: interview, rejected_by_company, offer..."""
    conn.execute("UPDATE applications SET detail=COALESCE(detail,'') || ? WHERE job_id=?", (f" | outcome: {outcome}", job_id))
    conn.execute("UPDATE jobs SET status_reason=? WHERE id=?", (f"outcome: {outcome}", job_id))
    log_event(conn, "outcome", job_id, {"outcome": outcome})
    conn.commit()
