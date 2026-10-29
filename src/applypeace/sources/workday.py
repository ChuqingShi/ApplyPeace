"""Workday career sites (<tenant>.<pod>.myworkdayjobs.com), read through the JSON the site itself uses.

A board is "<tenant>.<pod>.<site>", e.g. "nvidia.wd5.NVIDIAExternalCareerSite".
Big employers list thousands of jobs, so a scan types a few broad words from your
search plan into the site's search box (or each of your roles, without a plan),
keeps titles that loosely match, and stops paging once results stop matching.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx

from .base import Job, guess_country, html_to_text

PAGE = 20
MAX_PAGES = 5  # per search term
POD_HINT = re.compile(r"^\d+ Locations?$", re.I)  # "3 Locations": the real list is on the job page


def split_board(board: str) -> tuple[str, str, str]:
    tenant, pod, site = board.split(".", 2)
    return tenant, pod, site


def host(board: str) -> str:
    tenant, pod, _ = split_board(board)
    return f"https://{tenant}.{pod}.myworkdayjobs.com"


def api(board: str) -> str:
    tenant, _, site = split_board(board)
    return f"{host(board)}/wday/cxs/{tenant}/{site}"


def search_terms(titles: list[str]) -> list[str]:
    """The fewest searches that cover your titles: "Data Scientist" also finds "Applied Data Scientist"."""
    norm = sorted({" ".join(t.lower().split()) for t in titles if t.strip()}, key=len)
    terms: list[str] = []
    for t in norm:
        if not any(re.search(rf"\b{re.escape(s)}\b", t) for s in terms):
            terms.append(t)
    return terms or [""]


def posted_date(text: str | None, today: datetime | None = None) -> str | None:
    """"Posted Today" / "Posted Yesterday" / "Posted 3 Days Ago" / "Posted 30+ Days Ago" -> ISO date."""
    if not text:
        return None
    today = today or datetime.now(timezone.utc)
    lowered = text.lower()
    if "today" in lowered:
        days = 0
    elif "yesterday" in lowered:
        days = 1
    else:
        m = re.search(r"(\d+)\+?\s*days?", lowered)
        if not m:
            return None
        days = int(m.group(1)) + (1 if "+" in lowered else 0)
    return (today - timedelta(days=days)).date().isoformat()


def _job(board: str, raw: dict) -> Job:
    _, _, site = split_board(board)
    path = raw["externalPath"]
    where = raw.get("locationsText") or ""
    many = bool(POD_HINT.match(where))
    return Job(
        ats="workday",
        board=board,
        ats_job_id=path.rstrip("/").rsplit("/", 1)[-1],
        title=raw["title"].strip(),
        url=f"{host(board)}/en-US/{site}{path}",
        location=None if many else where or None,
        country=None if many else guess_country(where),
        remote="remote" in where.lower() or None,
        posted_at=posted_date(raw.get("postedOn")),
    )


def loosely_matches(title: str, titles: list[str], words: list[str]) -> bool:
    """Worth keeping for the AI screener: a role matches, or every word of a search phrase is in the title."""
    from ..rank.filters import _words, title_matches

    have = _words(title)
    return title_matches(title, titles) or any(_words(w) <= have for w in words if w.strip())


def list_jobs(client: httpx.Client, board: str, titles: list[str] | None = None,
              words: list[str] | None = None) -> list[Job]:
    """Search the site for each search phrase (from your search plan, else your roles)."""
    titles, words = titles or [], [w for w in (words or []) if w.strip()]
    found: dict[str, Job] = {}
    for term in words or search_terms(titles):
        for page in range(MAX_PAGES):
            resp = client.post(f"{api(board)}/jobs",
                               json={"appliedFacets": {}, "limit": PAGE, "offset": page * PAGE, "searchText": term})
            resp.raise_for_status()
            postings = resp.json().get("jobPostings") or []
            hits = [r for r in postings if r.get("externalPath") and r.get("title")
                    and (not titles or loosely_matches(r["title"], titles, words))]
            for raw in hits:
                job = _job(board, raw)
                found.setdefault(job.id, job)
            if len(postings) < PAGE or not hits:
                break  # results are sorted by relevance: a page with no matching titles ends the search
    return list(found.values())


def details(client: httpx.Client, board: str, url: str) -> dict:
    """Description, location, country and posting date from the job's own page."""
    _, _, site = split_board(board)
    path = url.split(f"/{site}", 1)[1]
    resp = client.get(f"{api(board)}{path}")
    resp.raise_for_status()
    info = resp.json().get("jobPostingInfo") or {}
    country = ((info.get("jobRequisitionLocation") or {}).get("country") or {}).get("alpha2Code")
    locations = [info.get("location"), *(info.get("additionalLocations") or [])]
    location = " | ".join(l for l in locations if l)
    return {
        "description": html_to_text(info.get("jobDescription")),
        "location": location or None,
        "country": ("US" if country == "US" else "non-US") if country else guess_country(location),
        "remote": "remote" in (info.get("remoteType") or location).lower(),
        "posted_at": info.get("startDate"),
    }
