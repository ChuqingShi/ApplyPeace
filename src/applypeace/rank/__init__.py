"""Picking jobs: rule filters -> keyword pre-rank -> LLM judge -> top-N batch."""

from __future__ import annotations

import concurrent.futures as cf
import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from .. import llm, profile
from ..companies import registry as reg
from ..config import Paths, load_settings
from ..db import log_event
from .filters import SENIORITY_LEVELS, _age_days, apply_filters
from .judge import judge_job
from .keywords import tfidf_scores

# Weights for the final score; recency decays with a two-week half-life-ish time constant.
FIT_WEIGHT, RECENCY_WEIGHT, H1B_BONUS, RECENCY_DAYS = 0.75, 0.25, 0.05, 14
JUDGE_WORKERS = 4  # AI judgements run side by side; each takes 10-20 seconds


@dataclass
class RankResult:
    filters: dict = field(default_factory=dict)
    candidates: int = 0
    judged: int = 0
    judge_errors: int = 0
    queued: list[str] = field(default_factory=list)


def h1b_records(companies: list[dict]) -> dict[str, dict] | None:
    """H-1B summary per company key, or None when the H-1B database isn't loaded yet."""
    from .. import h1b
    from ..h1b.lookup import summary
    from ..h1b.store import connect

    if not h1b.default_db().exists():
        return None
    with connect(h1b.default_db()) as conn:
        if not conn.execute("SELECT 1 FROM ingested LIMIT 1").fetchone():
            return None
        return {c["key"]: summary(conn, c["name"], c.get("aliases")) for c in companies}


def h1b_note(record: dict | None) -> str:
    if not record:
        return "unknown"
    if not record.get("found"):
        return "no H-1B filings found"
    years = "/".join(f"FY{y}" for y in record["recent_years"])
    return f"{record['recent_certified']} certified H-1B LCAs in {years}"


def _recency(job: sqlite3.Row) -> float:
    return math.exp(-max(_age_days(job["posted_at"], job["first_seen"]), 0) / RECENCY_DAYS)


def judged_today(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM events WHERE kind='judge' AND substr(at, 1, 10) = ?", (date.today().isoformat(),)
    ).fetchone()[0]


def rank(p: Paths, conn: sqlite3.Connection, log=print) -> RankResult:
    settings = load_settings(p)
    registry = reg.load(p)
    companies = reg.enabled(registry, settings)
    enabled_keys = {c["key"] for c in companies}
    with_jobs = {r[0] for r in conn.execute("SELECT DISTINCT company_key FROM jobs WHERE status IN ('new','filtered','candidate')")}
    records = h1b_records([c for c in companies if c["key"] in with_jobs]) if settings["h1b_filter"] else None
    if settings["h1b_filter"] and records is None:
        log("H-1B database not loaded yet (`ap h1b update`); only the job-text sponsorship check is applied.")
    result = RankResult(filters=apply_filters(conn, settings, enabled_keys, records or {}))

    candidates = conn.execute("SELECT * FROM jobs WHERE status='candidate'").fetchall()
    result.candidates = len(candidates)
    resumes = {r["key"]: profile.resume_text(p, r["key"]) for r in profile.resumes_index(p)}
    if not candidates or not resumes:
        return result

    # Stage 2: keyword pre-rank against the best-matching resume variant.
    docs = {j["id"]: f"{j['title']}\n{j['description'] or ''}" for j in candidates}
    best: dict[str, tuple[float, str]] = {}
    for key, text in resumes.items():
        for job_id, score in tfidf_scores(text, docs).items():
            if score > best.get(job_id, (-1, ""))[0]:
                best[job_id] = (score, key)
    top = max(s for s, _ in best.values()) or 1.0
    for job_id, (score, key) in best.items():
        conn.execute("UPDATE jobs SET keyword_score=?, resume_key=? WHERE id=?", (score / top, key, job_id))
    conn.commit()

    # Stage 3: LLM judge on the best unjudged candidates, within the daily limit.
    if llm.available(p):
        budget = max(0, settings["llm"]["judge_daily_limit"] - judged_today(conn))
        todo = conn.execute(
            "SELECT * FROM jobs WHERE status='candidate' AND judge IS NULL ORDER BY keyword_score DESC LIMIT ?",
            (budget,),
        ).fetchall()
        prof, answers = profile.load_profile(p), profile.load_answers(p)
        candidate = {
            "name": f"{prof.get('first_name', '')} {prof.get('last_name', '')}".strip(),
            "needs_sponsorship_now": answers.get("needs_sponsorship_now"),
            "needs_sponsorship_future": answers.get("needs_sponsorship_future"),
            "visa_status": answers.get("visa_status"),
            "target_titles": settings["targets"]["titles"],
            "seniority_wanted": SENIORITY_LEVELS.get(settings["targets"].get("seniority"), SENIORITY_LEVELS["any"])[1],
            "preferred_locations": settings["targets"]["locations"],
        }
        preferences = p.preferences.read_text() if p.preferences.exists() else ""
        names = {c["key"]: c["name"] for c in companies}

        def judge(job):
            payload = {**dict(job), "company": names.get(job["company_key"], job["company_key"]),
                       "h1b_note": h1b_note((records or {}).get(job["company_key"]))}
            try:
                return job, judge_job(p, payload, resumes, candidate, preferences), None
            except llm.LLMUnavailable as exc:
                return job, None, exc

        with cf.ThreadPoolExecutor(JUDGE_WORKERS) as pool:
            verdicts = list(pool.map(judge, todo))
        for job, verdict, exc in verdicts:
            if exc:
                result.judge_errors += 1
                log(f"Judge unavailable ({exc}); remaining jobs use keyword ranking.")
                break
            conn.execute("UPDATE jobs SET judge=?, resume_key=? WHERE id=?",
                         (json.dumps(verdict), verdict["resume_key"], job["id"]))
            log_event(conn, "judge", job["id"], {"score": verdict["score"], "rec": verdict["recommendation"]})
            conn.commit()
            result.judged += 1

    # Final score for every candidate.
    for job in conn.execute("SELECT * FROM jobs WHERE status='candidate'").fetchall():
        verdict = json.loads(job["judge"]) if job["judge"] else None
        fit = (verdict["score"] - 1) / 4 if verdict else (job["keyword_score"] or 0)
        bonus = H1B_BONUS if (records or {}).get(job["company_key"], {}).get("sponsor") else 0
        final = FIT_WEIGHT * fit + RECENCY_WEIGHT * _recency(job) + bonus
        conn.execute("UPDATE jobs SET final_score=? WHERE id=?", (final, job["id"]))
    conn.commit()
    return result


def select_batch(p: Paths, conn: sqlite3.Connection) -> list[str]:
    """Move the top N candidates into the review queue. AI 'skip' picks stay out (visible as Skipped by AI)."""
    settings = load_settings(p)
    already = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]
    room = max(0, settings["batch_size"] - already)
    rows = conn.execute(
        "SELECT id, judge FROM jobs WHERE status='candidate' ORDER BY final_score DESC"
    ).fetchall()
    min_score = settings.get("min_fit_score", 3)
    ai_on = llm.available(p)
    # Drop batch jobs the AI now rates below your bar (e.g. after the bar was raised).
    for row in conn.execute("SELECT id, judge FROM jobs WHERE status='queued' AND judge IS NOT NULL").fetchall():
        verdict = json.loads(row["judge"])
        if verdict["recommendation"] == "skip" or verdict["score"] < min_score:
            conn.execute("UPDATE jobs SET status='candidate' WHERE id=?", (row["id"],))
    room = max(0, settings["batch_size"] - conn.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0])
    picked = []
    for row in rows:
        if len(picked) >= room:
            break
        if row["judge"]:
            verdict = json.loads(row["judge"])
            # Better a short batch than a padded one: only good fits get in.
            if verdict["recommendation"] == "skip" or verdict["score"] < min_score:
                continue
        elif ai_on:
            continue  # wait for the AI to score it in a later scan rather than queue it unreviewed
        picked.append(row["id"])
    for job_id in picked:
        conn.execute("UPDATE jobs SET status='queued' WHERE id=?", (job_id,))
        log_event(conn, "queued", job_id)
    conn.commit()
    return picked
