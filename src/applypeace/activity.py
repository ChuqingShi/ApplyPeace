"""The Activity log: one line per scan, and under it the jobs from that scan you applied to."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo


def local(stamp: str, tz: str) -> str:
    when = datetime.fromisoformat(stamp)
    return when.astimezone(ZoneInfo(tz)).strftime("%a %b %-d, %-I:%M %p")


def entries(conn: sqlite3.Connection, names: dict[str, str], tz: str, limit: int = 60) -> list[dict]:
    applied = {r["id"]: r for r in conn.execute(
        "SELECT j.id, j.title, j.company_key, j.company_name, a.method FROM jobs j"
        " LEFT JOIN applications a ON a.job_id = j.id WHERE j.status = 'submitted'")}
    out = []
    for r in conn.execute("SELECT at, detail FROM events WHERE kind='cycle' ORDER BY id DESC LIMIT ?", (limit,)):
        detail = json.loads(r["detail"]) if r["detail"] else {}
        queued = detail.get("queued") if isinstance(detail.get("queued"), list) else []
        jobs = [{"id": j, "title": applied[j]["title"], "by_you": applied[j]["method"] == "you",
                 "company": applied[j]["company_name"] or names.get(applied[j]["company_key"], applied[j]["company_key"])}
                for j in queued if j in applied]
        out.append({"at": local(r["at"], tz), "added": len(queued), "jobs": jobs})
    return out
