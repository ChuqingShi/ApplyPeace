"""Find which ATS board (Greenhouse, Ashby, Lever) a company uses, and verify ownership.

A board only counts once its owner name matches the company. That keeps every
posting official: a slug collision with an unrelated employer is rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from .names import match_level, normalize

GENERIC_FIRST_WORDS = {
    "american", "united", "general", "first", "national", "international", "global", "the",
    "new", "southern", "northern", "western", "eastern", "public", "texas", "pacific", "bank",
    "royal", "china", "japan", "state", "advanced", "applied", "digital", "data",
}


# Large listed companies with tiny boards are usually a different, smaller employer.
MIN_JOBS_FOR_LARGE_COMPANY = 10


@dataclass
class Board:
    ats: str
    board: str
    owner_name: str | None
    match: str  # "exact" | "likely"
    job_count: int

    @property
    def verified(self) -> bool:
        return self.match == "exact"


def slug_candidates(name: str, ticker: str | None = None, website: str | None = None) -> list[str]:
    norm = normalize(name)
    words = norm.split()
    candidates = [norm.replace(" ", ""), norm.replace(" ", "-")]
    if website:
        host = re.sub(r"^(https?://)?(www\.|careers\.|jobs\.)?", "", website.lower()).split("/")[0]
        candidates.append(host.split(".")[0])
    if words and len(words[0]) >= 4 and words[0] not in GENERIC_FIRST_WORDS:
        candidates.append(words[0])
    if ticker and len(ticker) >= 3:
        candidates.append(ticker.lower())
    seen, out = set(), []
    for c in candidates:
        if c and c not in seen and re.fullmatch(r"[a-z0-9-]+", c):
            seen.add(c)
            out.append(c)
    return out


def _greenhouse(client: httpx.Client, slug: str) -> tuple[str | None, int] | None:
    jobs = client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if jobs.status_code != 200:
        return None
    count = len(jobs.json().get("jobs", []))
    meta = client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
    return (meta.json().get("name") if meta.status_code == 200 else None), count


ASHBY_ORG_QUERY = (
    "query ApiOrganizationFromHostedJobsPageName($organizationHostedJobsPageName: String!, "
    "$searchContext: OrganizationSearchContext) { organization: organizationFromHostedJobsPageName("
    "organizationHostedJobsPageName: $organizationHostedJobsPageName, searchContext: $searchContext)"
    " { name publicWebsite } }"
)


def _ashby(client: httpx.Client, slug: str) -> tuple[str | None, int] | None:
    jobs = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if jobs.status_code != 200:
        return None
    count = len(jobs.json().get("jobs", []))
    org = client.post(
        "https://jobs.ashbyhq.com/api/non-user-graphql",
        params={"op": "ApiOrganizationFromHostedJobsPageName"},
        json={
            "operationName": "ApiOrganizationFromHostedJobsPageName",
            "variables": {"organizationHostedJobsPageName": slug, "searchContext": "JobBoard"},
            "query": ASHBY_ORG_QUERY,
        },
    )
    name = None
    if org.status_code == 200:
        name = ((org.json().get("data") or {}).get("organization") or {}).get("name")
    return name, count


def _lever(client: httpx.Client, slug: str) -> tuple[str | None, int] | None:
    jobs = client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    if jobs.status_code != 200 or not isinstance(jobs.json(), list):
        return None
    count = len(jobs.json())
    page = client.get(f"https://jobs.lever.co/{slug}")
    title = re.search(r"<title>([^<]+)</title>", page.text) if page.status_code == 200 else None
    name = re.sub(r"\s*(jobs|careers)\s*$", "", title.group(1).strip(), flags=re.I) if title else None
    return name, count


PROBES = {"greenhouse": _greenhouse, "ashby": _ashby, "lever": _lever}


def find_board(
    client: httpx.Client,
    name: str,
    ticker: str | None = None,
    website: str | None = None,
    large: bool = False,
) -> Board | None:
    """Return the best board whose owner matches the company, or None.

    An exact owner-name match is verified automatically. A "likely" match, or an
    exact match on a suspiciously small board for a large company, needs the
    user's confirmation before its jobs are used.
    """
    likely = None
    for slug in slug_candidates(name, ticker, website):
        for ats, probe in PROBES.items():
            try:
                found = probe(client, slug)
            except (httpx.HTTPError, ValueError):
                continue
            if not found:
                continue
            owner, count = found
            level = match_level(owner, name)
            if count == 0 or level == "none":
                continue
            if level == "exact" and large and count < MIN_JOBS_FOR_LARGE_COMPANY:
                level = "likely"
            board = Board(ats, slug, owner, level, count)
            if board.verified:
                return board
            likely = likely or board
    return likely
