"""Company list importers.

S&P 500 and Nasdaq-100 come from public-domain sources and are fetched
automatically. Fortune and Forbes rankings are copyrighted compilations: the
user imports them from a CSV into their private data folder, and they are
never committed to the code repository.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
WIKI_API = "https://en.wikipedia.org/w/api.php"


@dataclass
class Member:
    name: str
    ticker: str | None = None
    hq_country: str | None = None  # "US" or a country name
    website: str | None = None
    rank: int | None = None


def fetch_sp500(client: httpx.Client) -> list[Member]:
    resp = client.get(SP500_CSV)
    resp.raise_for_status()
    members: dict[str, Member] = {}
    for row in csv.DictReader(io.StringIO(resp.text)):
        cik = row.get("CIK") or row["Symbol"]
        if cik in members:  # dual share classes (e.g. GOOGL/GOOG) are one employer
            continue
        members[cik] = Member(name=row["Security"], ticker=row["Symbol"], hq_country="US")
    return list(members.values())


def fetch_nasdaq100(client: httpx.Client) -> list[Member]:
    resp = client.get(
        WIKI_API,
        params={
            "action": "parse",
            "page": "List of NASDAQ-100 companies",
            "prop": "wikitext",
            "format": "json",
            "formatversion": "2",
        },
    )
    resp.raise_for_status()
    return parse_nasdaq100_wikitext(resp.json()["parse"]["wikitext"])


_LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")


def parse_nasdaq100_wikitext(wikitext: str) -> list[Member]:
    start = wikitext.index('id="constituents"')
    table = wikitext[start : wikitext.index("|}", start)]
    members: dict[str, Member] = {}
    for row in table.split("\n|-")[1:]:
        cells = [c.strip() for c in row.strip().lstrip("|").split("||")]
        if len(cells) < 3 or cells[0].startswith("!"):
            continue
        ticker = cells[0].strip("| ")
        name = _LINK.sub(r"\1", cells[1])
        name = re.sub(r"\s*\(Class [A-Z]\)", "", name).strip()
        isin = re.search(r"([A-Z]{2})[A-Z0-9]{9}\d", cells[2])
        country = isin.group(1) if isin else None
        key = name.lower()
        if key not in members:
            members[key] = Member(name=name, ticker=ticker, hq_country="US" if country == "US" else country)
    return list(members.values())


_NAME_COLS = ("name", "company", "company name", "organization")
_COUNTRY_COLS = ("country", "hq_country", "headquarters", "hq", "country/territory", "location")
_SITE_COLS = ("website", "url", "domain", "site")
_RANK_COLS = ("rank", "#", "ranking")


def read_csv(path: Path) -> list[Member]:
    """Import a list the user saved as CSV. Only a name column is required."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    columns = {c.strip().lower(): c for c in rows[0].keys() if c}

    def col(options):
        return next((columns[o] for o in options if o in columns), None)

    name_col = col(_NAME_COLS)
    if not name_col:
        raise ValueError(f"{path.name}: needs a column named one of {', '.join(_NAME_COLS)}")
    country_col, site_col, rank_col = col(_COUNTRY_COLS), col(_SITE_COLS), col(_RANK_COLS)
    members = []
    for row in rows:
        name = (row.get(name_col) or "").strip()
        if not name:
            continue
        country = (row.get(country_col) or "").strip() if country_col else None
        rank = (row.get(rank_col) or "").strip() if rank_col else ""
        members.append(
            Member(
                name=name,
                hq_country=normalize_country(country) if country else None,
                website=(row.get(site_col) or "").strip() or None if site_col else None,
                rank=int(re.sub(r"\D", "", rank)) if re.search(r"\d", rank) else None,
            )
        )
    return members


def normalize_country(value: str) -> str:
    v = value.strip()
    if v.lower() in {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america"}:
        return "US"
    # "Cupertino, California" style headquarters strings
    from ..sources.base import guess_country

    return "US" if guess_country(v) == "US" else v
