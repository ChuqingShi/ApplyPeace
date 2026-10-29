"""AI-assisted job search: a search plan made once per change, and a cheap title screener.

Your roles (the short list on the targets page) describe what you want. Two small
AI steps turn that into a search:

1. The search plan. Made once whenever your roles or level change: a one-line
   summary of your background, and a few broad words to type into Workday's search
   box instead of one search per role.
2. The title screener. After each scan, job titles nobody has screened yet go to the
   cheapest model in batches of 500, which answers which ones fit. Every answer is
   saved, so a title is never asked about twice (until your roles change).

Without AI (no consent, or the CLI missing), both fall back to word matching:
a title matches a role when the role's words appear in it in order.
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone

from . import llm, profile
from .config import Paths, load_settings, load_yaml, save_yaml
from .db import now

BATCH = 500  # titles per screening request
WORKERS = 4
MAX_BATCHES_PER_SCAN = 40  # about 20,000 titles; the rest wait for the next scan

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "one sentence: the candidate's background and strengths"},
        "search_words": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                         "description": "2-6 broad words or short phrases to type into a job site's search box"},
    },
    "required": ["summary", "search_words"],
}
PLAN_SYSTEM = (
    "You plan job searches. From the candidate's resumes and the roles they want, write a one-sentence summary "
    "of their background, and choose the fewest broad search-box queries (2 to 6, one to three words each) that "
    "together surface every role they want on a big employer's career site, e.g. 'data scientist', "
    "'machine learning', 'quantitative'. Prefer broad words over full titles."
)
SCREEN_SCHEMA = {
    "type": "object",
    "properties": {"fit": {"type": "array", "items": {"type": "integer"},
                           "description": "numbers of the titles that fit"}},
    "required": ["fit"],
}
SCREEN_SYSTEM = (
    "You screen job titles for one candidate. Answer with the numbers of the titles whose role fits what they "
    "want: the same role under another name, a close variant, or a specialised version (e.g. 'Scientist, "
    "Machine Learning', 'Quant Researcher'). Ignore level words such as Senior or II; level is checked "
    "elsewhere. Leave out other functions: sales, support, HR, recruiting, finance operations, facilities, "
    "hardware, and pure software roles unless the candidate wants them."
)


def roles_key(settings: dict) -> str:
    """Changes whenever your roles or level change, so old plans and answers are redone."""
    targets = settings["targets"]
    text = json.dumps([sorted(t.lower() for t in targets["titles"]), targets.get("seniority", "any")])
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def plan_path(p: Paths):
    return p.personal / "search_plan.yml"


def plan(p: Paths, log=print) -> dict:
    """The current search plan, made (one AI request) if your roles changed since the last one."""
    settings = load_settings(p)
    key = roles_key(settings)
    saved = load_yaml(plan_path(p), {})
    if saved.get("roles_key") == key or not settings["targets"]["titles"] or not llm.available(p):
        return saved if saved.get("roles_key") == key else {}
    resumes = "\n\n".join(profile.resume_text(p, r["key"])[:6000] for r in profile.resumes_index(p))
    try:
        made = llm.ask_json(p, PLAN_SYSTEM, f"Roles wanted: {', '.join(settings['targets']['titles'])}\n\n{resumes}",
                            PLAN_SCHEMA)
    except llm.LLMUnavailable as exc:
        log(f"Search plan not updated ({exc}); using your roles as search words.")
        return {}
    made = {"roles_key": key, "made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": made["summary"], "search_words": [w.strip() for w in made["search_words"] if w.strip()]}
    save_yaml(plan_path(p), made)
    log(f"New search plan: {', '.join(made['search_words'])}")
    return made


def norm(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#&/]+", (title or "").lower()))


def verdicts(conn: sqlite3.Connection, key: str) -> dict[str, bool]:
    return {r["title"]: bool(r["fit"]) for r in conn.execute("SELECT title, fit FROM title_screen WHERE roles_key=?", (key,))}


def screen(p: Paths, conn: sqlite3.Connection, titles: list[str], log=print, progress=None) -> int:
    """Ask the AI about titles not screened yet for your current roles. Returns how many were screened."""
    settings = load_settings(p)
    if not llm.available(p) or not settings["targets"]["titles"]:
        return 0
    key = roles_key(settings)
    known = verdicts(conn, key)
    todo = sorted({norm(t) for t in titles} - set(known) - {""})
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)][:MAX_BATCHES_PER_SCAN]
    if not batches:
        return 0
    summary = load_yaml(plan_path(p), {}).get("summary", "")
    wanted = (f"Roles wanted: {', '.join(settings['targets']['titles'])}\n"
              f"Background: {summary or '(see roles)'}\n\n")
    model = settings["llm"].get("screen_model", "haiku")

    def ask(batch: list[str]):
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(batch, 1))
        try:
            fit = llm.ask_json(p, SCREEN_SYSTEM, wanted + listing, SCREEN_SCHEMA, model=model)["fit"]
        except llm.LLMUnavailable as exc:
            return batch, None, exc
        return batch, {i for i in fit if isinstance(i, int)}, None

    done = 0
    with cf.ThreadPoolExecutor(WORKERS) as pool:
        for batch, fit, exc in pool.map(ask, batches):
            if exc:
                log(f"Title screening stopped ({exc}); unscreened titles use word matching.")
                break
            conn.executemany("INSERT OR REPLACE INTO title_screen (title, roles_key, fit, at) VALUES (?,?,?,?)",
                             [(t, key, int(i in fit), now()) for i, t in enumerate(batch, 1)])
            conn.commit()
            done += len(batch)
            if progress:
                progress(done, len(todo))
    return done
