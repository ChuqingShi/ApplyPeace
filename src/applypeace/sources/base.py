"""Common job shape and helpers shared by the ATS sources."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

import httpx

USER_AGENT = "ApplyPeace/0.1 (https://github.com/ChuqingShi/ApplyPeace)"


def http_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)


@dataclass
class Job:
    ats: str
    board: str
    ats_job_id: str
    title: str
    url: str
    apply_url: str | None = None
    location: str | None = None
    country: str | None = None
    remote: bool | None = None
    posted_at: str | None = None
    description: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.ats}:{self.board}:{self.ats_job_id}"


US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
_US_ABBR = set(US_STATES.values())
_US_WORDS = re.compile(r"\b(united states|u\.s\.a?\.?|usa|us-remote|remote[ ,(-]*us\b|us\b)", re.I)
_US_CITIES = re.compile(
    r"\b(san francisco|new york|nyc|seattle|boston|austin|chicago|los angeles|denver|atlanta"
    r"|palo alto|mountain view|menlo park|sunnyvale|san jose|washington,? d\.?c)\b",
    re.I,
)


def guess_country(*texts: str | None) -> str | None:
    """Best-effort country for a location string: "US", "non-US", or None if unclear."""
    joined = " | ".join(t for t in texts if t)
    if not joined:
        return None
    if _US_WORDS.search(joined) or _US_CITIES.search(joined):
        return "US"
    lowered = joined.lower()
    if any(state in lowered for state in US_STATES):
        return "US"
    for part in re.split(r"[|;/]", joined):
        tokens = [t.strip() for t in part.split(",")]
        if len(tokens) >= 2 and tokens[-1].upper() in _US_ABBR:
            return "US"
    remainder = re.sub(r"\b(remote|hybrid|anywhere|global|worldwide|flexible|or|and)\b|[^a-z]+", "", lowered)
    if not remainder:
        return None  # "Remote" or "Anywhere": country unknown
    return "non-US"


_TAG = re.compile(r"<[^>]+>")


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)  # Greenhouse double-encodes its HTML
    text = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = html.unescape(_TAG.sub("", text))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()
