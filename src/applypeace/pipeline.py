"""The scheduled jobs: the 8-hourly cycle, the daily digest and the apply step."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from datetime import date

from . import profile
from .companies import registry as reg
from .config import Paths, load_settings, save_settings
from .db import connect, log_event, now
from .discover import discover
from .notify import notify
from .prepare import prepare_queue
from .rank import rank, select_batch


class SetupIncomplete(RuntimeError):
    pass


@contextmanager
def single_run(p: Paths, name: str):
    """Never run two copies of the same step at once (e.g. a slow apply and the next tick)."""
    p.home.mkdir(parents=True, exist_ok=True)
    with (p.home / f".{name}.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def require_setup(p: Paths) -> None:
    missing = profile.completeness(p)
    if missing:
        raise SetupIncomplete("Setup isn't finished: " + "; ".join(missing))


def cycle(p: Paths, log=print, trigger: str = "scheduled") -> dict:
    """Scan official boards, rank, fill the review batch and draft its answers.

    Every run leaves one "cycle" event for the Activity log: what was scanned,
    which jobs were added, and whether you were notified.
    """
    require_setup(p)
    settings = load_settings(p)
    started = now()
    with connect(p.db) as conn:
        found = discover(conn, reg.enabled(reg.load(p), settings))
        log(f"Scanned {found.boards} boards: {found.new} new jobs, {found.closed} closed.")
        ranked = rank(p, conn, log=log)
        log(f"{ranked.candidates} jobs passed the filters; AI judged {ranked.judged}.")
        queued = select_batch(p, conn)
        prepared = prepare_queue(p, conn, log=log)
        log(f"Added {len(queued)} jobs to your review batch.")
    from .backup import backup

    log(backup(p, "After scan"))
    sent: dict = {}
    if queued and (trigger == "manual" or load_settings(p).get("notify_mode", "after_scan") == "after_scan"):
        log(digest(p, force=True, new_jobs=len(queued), result=sent) or "")
    elif trigger == "manual":  # you asked for this scan, so always say how it went
        message = "Scan done: no new jobs for you this time."
        sent = {"sent": bool(notify(message, open_url=load_settings(p).get("review_url") or None)), "message": message}
    detail = {
        "trigger": trigger, "started": started, "boards": found.boards, "seen": found.seen, "new": found.new,
        "closed": found.closed, "passed": ranked.candidates, "judged": ranked.judged, "queued": queued,
        "errors": found.errors[:5], "notified": sent.get("sent"), "message": sent.get("message"),
        **{k: v for k, v in prepared.items() if k != "prepared"},
    }
    with connect(p.db) as conn:
        log_event(conn, "cycle", detail=detail)
        conn.commit()
    return {"new": found.new, "queued": len(queued), **prepared}


def digest(p: Paths, force: bool = False, new_jobs: int = 0, result: dict | None = None) -> str | None:
    """Send the "batch ready" notification, if there's anything to review.

    Called right after a scan that added jobs (notify_mode "after_scan"), or once
    a day at the chosen time (notify_mode "daily").
    """
    require_setup(p)
    settings = load_settings(p)
    if not force and settings.get("last_digest") == date.today().isoformat():
        return None
    from . import answers as ans

    with connect(p.db) as conn:
        queued = [r["id"] for r in conn.execute("SELECT id FROM jobs WHERE status='queued'")]
        needs_input = sum(1 for j in queued if ans.missing_required(ans.load(conn, j)))
        stuck = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('needs_you','needs_code')").fetchone()[0]
    if not queued and not stuck:
        return None
    if new_jobs:
        parts = [f"{new_jobs} new job{'s' if new_jobs != 1 else ''} to review ({len(queued)} in your batch)"]
    else:
        parts = [f"Batch ready: {len(queued)} job{'s' if len(queued) != 1 else ''}"]
    if needs_input:
        parts.append(f"{needs_input} need your input")
    if stuck:
        parts.append(f"{stuck} waiting for you to finish")
    message = ", ".join(parts) + "."
    ok = notify(message, open_url=settings.get("review_url") or None)
    if result is not None:
        result.update(sent=bool(ok), message=message)
    if not new_jobs:  # the daily summary gets its own log entry; after-scan alerts live on the scan's entry
        with connect(p.db) as conn:
            log_event(conn, "notify", detail={"sent": bool(ok), "message": message})
            conn.commit()
    settings["last_digest"] = date.today().isoformat()
    save_settings(settings, p)
    return message


def apply_step(p: Paths, max_jobs: int = 1, log=print) -> dict:
    """Send up to `max_jobs` approved applications (the scheduler calls this every few minutes)."""
    from .apply.runner import run_queue

    require_setup(p)
    settings = load_settings(p)
    with connect(p.db) as conn:
        results = run_queue(p, conn, review_url=settings.get("review_url", ""), max_jobs=max_jobs, log=log)
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='approved'").fetchone()[0]
        if results and not pending:
            # The batch is finished: summarise everything since the last summary.
            since = settings.get("last_done_notice") or date.today().isoformat()
            counts = dict(conn.execute(
                "SELECT status, COUNT(*) FROM applications WHERE finished_at > ? GROUP BY status", (since,)).fetchall())
            labels = {"submitted": "submitted", "filled": "filled in dry run", "needs_you": "need you",
                      "needs_code": "waiting for a code", "failed": "failed"}
            summary = [f"{n} {labels.get(k, k)}" for k, n in counts.items() if n]
            if summary:
                notify("Done: " + ", ".join(summary) + ".", open_url=settings.get("review_url") or None)
            from .db import now

            settings = load_settings(p)
            settings["last_done_notice"] = now()
            save_settings(settings, p)
    if results:
        from .backup import backup

        log(backup(p, "After applying"))
    return results
