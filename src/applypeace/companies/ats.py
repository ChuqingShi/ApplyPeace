"""Find which ATS board (Greenhouse, Ashby, Lever, Workday) a company uses, and verify ownership.

A board only counts once its owner name matches the company. That keeps every
posting official: a slug collision with an unrelated employer is rejected.
"""

from __future__ import annotations

import html
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


# Workday hosts each employer on one numbered pod; these cover nearly all of them.
WORKDAY_PODS = ("wd1", "wd5", "wd3", "wd12", "wd10", "wd2", "wd103", "wd501", "wd108", "wd4", "wd6", "wd101")
WORKDAY_COMMON_SITES = ("External", "Careers", "External_Careers", "{T}Careers", "{T}_Careers", "{T}External",
                        "ExternalCareerSite", "Search")
# Career sites that aren't the main one for experienced hires.
WORKDAY_SKIP_SITE = re.compile(r"intern|internal|contractor|alumni|employee|campus|student|university|grad", re.I)


def _workday_sites(client: httpx.Client, tenant: str) -> tuple[str, list[str]] | None:
    """The tenant's pod and its career site names, from the robots.txt each Workday host publishes."""
    for pod in WORKDAY_PODS:
        resp = client.get(f"https://{tenant}.{pod}.myworkdayjobs.com/robots.txt")
        if resp.status_code == 200 and "Sitemap" in resp.text:
            return pod, re.findall(r"myworkdayjobs\.com/([^/\s]+)/siteMap\.xml", resp.text)
        if resp.status_code == 401:  # the tenant lives here but hides its site list: try the usual names
            return pod, [s.replace("{T}", t) for s in WORKDAY_COMMON_SITES for t in dict.fromkeys(
                [tenant, tenant.capitalize(), tenant.upper()])]
    return None


def _workday_total(client: httpx.Client, tenant: str, pod: str, site: str) -> int:
    resp = client.post(f"https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                       json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    return int(resp.json().get("total") or 0) if resp.status_code == 200 else 0


def _workday_page(client: httpx.Client, tenant: str, pod: str, site: str) -> tuple[str | None, str]:
    """(employer name from the page title, the page's title and description text)."""
    page = client.get(f"https://{tenant}.{pod}.myworkdayjobs.com/en-US/{site}")
    if page.status_code != 200:
        return None, ""
    meta = dict(re.findall(r'property="og:(title|description)" content="([^"]*)"', page.text))
    title = html.unescape(meta.get("title", ""))
    name = re.sub(r"\b(careers?|jobs?|at|join|us|with|work|search|opportunities|external|site|portal|the)\b|[^\w&.' -]",
                  " ", title, flags=re.I)
    return " ".join(name.split()) or None, f"{title} {html.unescape(meta.get('description', ''))}"


def _workday(client: httpx.Client, slug: str, company: str) -> tuple[str | None, int, str, str] | None:
    """Find the company's main Workday site. Verified when the tenant is the company's full name,
    the site's title names the company, or (for a first-word tenant like "abbott") the site
    describes itself with that word and is a real, busy site."""
    if not re.fullmatch(r"[a-z0-9]+", slug):
        return None
    found = _workday_sites(client, slug)
    if not found:
        return None
    pod, sites = found
    best = max(((_workday_total(client, slug, pod, site), site) for site in dict.fromkeys(sites[:24])
                if not WORKDAY_SKIP_SITE.search(site)), default=(0, ""))
    if not best[0]:
        return None
    count, site = best
    owner, text = _workday_page(client, slug, pod, site)
    words = normalize(company).split()
    level = match_level(owner, company)
    if slug == "".join(words):
        level = "exact"
    elif level == "none" and words and slug == words[0] and count >= MIN_JOBS_FOR_LARGE_COMPANY \
            and re.search(rf"\b{re.escape(words[0])}\b", normalize(text)):
        level = "exact"
    return owner or company if level != "none" else owner, count, f"{slug}.{pod}.{site}", level


PROBES = {"greenhouse": _greenhouse, "ashby": _ashby, "lever": _lever, "workday": _workday}


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
                found = probe(client, slug, name) if ats == "workday" else probe(client, slug)
            except (httpx.HTTPError, ValueError):
                continue
            if not found:
                continue
            if ats == "workday":
                owner, count, board_name, level = found
            else:
                (owner, count), board_name = found, slug
                level = match_level(owner, name)
            if count == 0 or level == "none":
                continue
            if level == "exact" and large and count < MIN_JOBS_FOR_LARGE_COMPANY:
                level = "likely"
            board = Board(ats, board_name, owner, level, count)
            if board.verified:
                return board
            likely = likely or board
    return likely
