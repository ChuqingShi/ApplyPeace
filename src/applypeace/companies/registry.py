"""The company registry (companies.yml in the data home).

Each employer appears once, tagged with every list it belongs to, plus the ATS
board it was matched to. List membership is rebuilt from the list sources;
board matches and manual overrides persist across updates.
"""

from __future__ import annotations

import concurrent.futures as cf
from datetime import date, timedelta

from ..config import LISTS, Paths, load_yaml, save_yaml
from . import ats
from ..sources.base import http_client
from .lists import Member
from .names import key_for, normalize

SUPPORTED_ATS = ("greenhouse", "ashby", "lever", "workday")
# Job systems ApplyPeace can fill and submit for you; for the others you apply yourself.
AUTO_APPLY_ATS = ("greenhouse", "ashby", "lever")
# Bump when the board finder learns a new job system, so companies it missed are looked up again.
RESOLVER_VERSION = 2
# Lists of large companies, where a tiny board usually belongs to someone else.
LARGE_COMPANY_LISTS = {"fortune500", "sp500", "nasdaq100", "forbes_global2000_us", "forbes_global2000_intl"}


def load(p: Paths) -> dict[str, dict]:
    data = load_yaml(p.companies, {"companies": []})
    return {c["key"]: c for c in data.get("companies", [])}


def save(p: Paths, registry: dict[str, dict]) -> None:
    ordered = sorted(registry.values(), key=lambda c: c["name"].lower())
    save_yaml(p.companies, {"companies": ordered})


def _find(registry: dict[str, dict], member: Member) -> dict | None:
    norm = normalize(member.name)
    for company in registry.values():
        if member.ticker and company.get("ticker") == member.ticker:
            return company
        if normalize(company["name"]) == norm or norm in [normalize(a) for a in company.get("aliases", [])]:
            return company
    return None


def set_list(registry: dict[str, dict], list_key: str, members: list[Member]) -> tuple[int, int]:
    """Replace a list's membership. Returns (joined, left) counts."""
    if list_key not in LISTS:
        raise ValueError(f"unknown list {list_key!r}; choose from {', '.join(LISTS)}")
    before = {k for k, c in registry.items() if list_key in c.get("lists", [])}
    for company in registry.values():
        if list_key in company.get("lists", []):
            company["lists"].remove(list_key)
    after = set()
    for member in members:
        company = _find(registry, member)
        if company is None:
            key = key_for(member.name)
            while key in registry:
                key += "-2"
            company = registry[key] = {"key": key, "name": member.name, "lists": []}
        for field in ("ticker", "hq_country", "website"):
            value = getattr(member, field)
            if value and not company.get(field):
                company[field] = value
        company["lists"].append(list_key)
        after.add(company["key"])
    # Drop companies that are on no list and were never added by hand.
    for key in [k for k, c in registry.items() if not c["lists"] and not c.get("manual")]:
        del registry[key]
    return len(after - before), len(before - after)


def add_custom(registry: dict[str, dict], name: str, ats_name: str | None = None, board: str | None = None) -> dict:
    company = _find(registry, Member(name=name))
    if company is None:
        key = key_for(name)
        company = registry[key] = {"key": key, "name": name, "lists": []}
    if "custom" not in company["lists"]:
        company["lists"].append("custom")
    company["manual"] = True
    if ats_name and board:
        company.update(ats=ats_name, board=board, verified=True, resolved_at=str(date.today()))
    return company


def confirm(company: dict, accept: bool) -> None:
    """Record the user's decision on a board that needed confirmation."""
    company["confirm"] = "accepted" if accept else "rejected"
    company["verified"] = accept


def pending_confirmation(companies: list[dict]) -> list[dict]:
    return [c for c in companies if c.get("confirm") == "pending" and c.get("ats") in SUPPORTED_ATS]


def enabled(registry: dict[str, dict], settings: dict) -> list[dict]:
    on = {k for k, v in settings["lists"].items() if v}
    return [c for c in registry.values() if on & set(c.get("lists", []))]


def needs_resolution(company: dict, refresh_days: int = 30) -> bool:
    if (company.get("manual") and company.get("board")) or company.get("confirm") in ("accepted", "rejected"):
        return False
    resolved = company.get("resolved_at")
    if not resolved:
        return True
    if company.get("ats") == "not_found" and company.get("resolver", 1) < RESOLVER_VERSION:
        return True
    stale = date.fromisoformat(resolved) < date.today() - timedelta(days=refresh_days)
    return stale and company.get("ats") not in SUPPORTED_ATS


def resolve(registry: dict[str, dict], companies: list[dict], workers: int = 8, progress=None) -> dict[str, int]:
    """Probe ATS boards for the given companies (in place). Returns counts by outcome."""
    todo = [c for c in companies if needs_resolution(c)]
    counts = {"verified": 0, "to_confirm": 0, "not_found": 0}

    def work(company: dict):
        with http_client() as client:
            large = bool(LARGE_COMPANY_LISTS & set(company.get("lists", [])))
            return company, ats.find_board(client, company["name"], company.get("ticker"), company.get("website"), large)

    with cf.ThreadPoolExecutor(workers) as pool:
        for i, (company, board) in enumerate(pool.map(work, todo), 1):
            company["resolved_at"] = str(date.today())
            company["resolver"] = RESOLVER_VERSION
            if board is None:
                company.update(ats="not_found", board=None, verified=False, owner_name=None, job_count=0)
                company.pop("confirm", None)
                counts["not_found"] += 1
            else:
                company.update(ats=board.ats, board=board.board, verified=board.verified,
                               owner_name=board.owner_name, job_count=board.job_count)
                if board.verified:
                    company.pop("confirm", None)
                else:
                    company["confirm"] = "pending"  # the user confirms or rejects this board once
                counts["verified" if board.verified else "to_confirm"] += 1
            if progress:
                progress(i, len(todo), company)
    return counts
