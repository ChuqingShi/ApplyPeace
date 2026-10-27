"""Back up the private data folder to its private GitHub repo.

The working database stays local; what gets backed up is readable:
applications/history.csv and history.jsonl (one row per application) and the
per-job records (job description, answers sent, screenshots).
"""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

from . import history
from .companies import registry as reg
from .config import Paths
from .db import connect

PROJECT_DIR = Path(__file__).resolve().parents[2]
PLAN_SOURCE = PROJECT_DIR / ".reports" / "applypeace-plan.md"

README = """# ApplyPeace data (private)

Backed up automatically by ApplyPeace. Keep this repository private.

| Folder | What's in it |
|---|---|
| `personal/` | profile, standard answers, settings, learned preferences, resume PDFs |
| `applications/` | `history.csv` (one row per application), `history.jsonl` (full detail), and `records/<job>/` with the job description, answers sent and screenshots |
| `companies/` | `companies.yml` (every company, its lists and matched job board) and imported lists |
| `plan/` | `PLAN.md`, the current ApplyPeace plan |

The working database (`applypeace.db`) is not stored here; `applications/` is its readable export.
"""

COLUMNS = ["applied_at", "status", "applied_by", "company", "title", "location", "resume", "job_url", "record", "note", "detail"]


def export_history(p: Paths) -> int:
    """Write applications/history.csv and history.jsonl from the database. Returns the row count."""
    if not p.db.exists():
        return 0
    names = {c["key"]: c["name"] for c in reg.load(p).values()}
    with connect(p.db) as conn:
        rows = conn.execute(
            "SELECT a.*, j.title, j.company_key, j.company_name, j.location, j.url, j.resume_key AS job_resume,"
            " ap.resume_key FROM applications a"
            " JOIN jobs j ON j.id = a.job_id LEFT JOIN approvals ap ON ap.job_id = a.job_id"
            " ORDER BY COALESCE(a.finished_at, a.started_at)"
        ).fetchall()
    p.applications.mkdir(parents=True, exist_ok=True)
    with (p.applications / "history.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for r in rows:
            record = p.artifacts.name + "/" + r["job_id"].replace(":", "_")
            writer.writerow({
                "applied_at": (r["finished_at"] or r["started_at"] or "")[:19].replace("T", " "),
                "status": r["status"], "applied_by": "you" if r["method"] == "you" else "ApplyPeace",
                "company": r["company_name"] or names.get(r["company_key"], r["company_key"]), "note": r["note"] or "",
                "title": r["title"], "location": r["location"] or "", "resume": r["resume_key"] or r["job_resume"] or "",
                "job_url": r["url"], "record": record, "detail": r["detail"] or "",
            })
    with (p.applications / "history.jsonl").open("w") as f:
        for r in rows:
            item = {k: r[k] for k in r.keys()}
            item["company"] = r["company_name"] or names.get(r["company_key"], r["company_key"])
            item["submitted_values"] = json.loads(r["submitted_values"]) if r["submitted_values"] else None
            f.write(json.dumps(item, default=str) + "\n")
    return len(rows)


def sync_plan(p: Paths) -> bool:
    """Copy the latest plan into plan/PLAN.md. Returns True if it changed."""
    if not PLAN_SOURCE.exists():
        return False
    text = PLAN_SOURCE.read_text()
    if text.startswith("---\n"):  # drop the Openbase report header
        text = text.split("\n---\n", 1)[-1].lstrip()
    p.plan.parent.mkdir(parents=True, exist_ok=True)
    if p.plan.exists() and p.plan.read_text() == text:
        return False
    p.plan.write_text(text)
    return True


def has_remote(p: Paths) -> bool:
    return bool(subprocess.run(["git", "-C", str(p.home), "remote"], capture_output=True, text=True).stdout.strip())


def backup(p: Paths, message: str = "Backup", push: bool = True) -> str:
    history.ensure_repo(p)
    readme = p.home / "README.md"
    if not readme.exists() or readme.read_text() != README:
        readme.write_text(README)
    count = export_history(p)
    sync_plan(p)
    committed = history.commit(p, f"{message} ({count} applications)")
    if push and has_remote(p):
        proc = subprocess.run(["git", "-C", str(p.home), "push", "-q", "origin", "HEAD"], capture_output=True, text=True)
        if proc.returncode != 0:
            return f"Saved locally; push failed: {proc.stderr.strip()[:200]}"
        return "Backed up and pushed." if committed else "Already up to date."
    return "Saved locally (no backup repo connected)." if committed else "Nothing new."


def connect_remote(p: Paths, repo: str) -> str:
    """Point the data folder at a private GitHub repo and push everything."""
    history.ensure_repo(p)
    if not has_remote(p):
        subprocess.run(["git", "-C", str(p.home), "remote", "add", "origin", f"https://github.com/{repo}.git"], check=True)
    return backup(p, "Initial backup")

