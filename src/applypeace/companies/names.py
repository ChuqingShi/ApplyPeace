"""Company-name normalisation, shared by list merging and board verification."""

from __future__ import annotations

import re
import unicodedata

_SUFFIXES = re.compile(
    r"\b(incorporated|inc|corporation|corp|company|co|holdings?|group|plc|ltd|limited|llc|l l c|llp|lp|pbc|pc"
    r"|gmbh|pte|pvt|private|bv|kk|sarl|ulc"
    r"|nv|sa|ag|se|the|class [a-c]|technologies|technology)\b",
    re.I,
)


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = text.replace("&", " and ")
    text = re.sub(r"\(.*?\)", " ", text).lower()
    text = re.sub(r"\b([a-z])\.(?=[a-z]\.)", r"\1", text)  # "l.l.c." -> "llc.", "u.s.a." -> "usa."
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = _SUFFIXES.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def key_for(name: str) -> str:
    return normalize(name).replace(" ", "-") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# Words a brand often drops from its legal name ("Robinhood Markets" posts as "Robinhood").
GENERIC_EXTRAS = {"markets", "enterprise", "enterprises", "international", "worldwide", "global",
                  "systems", "labs", "platforms", "networks", "software", "solutions", "brands"}


def match_level(owner: str | None, company: str | None) -> str:
    """How well a board owner's name matches a company: "exact", "likely" or "none".

    "exact": same name, or the owner adds only generic words ("Rocket Lab Corporation").
    "likely": the owner dropped only generic words ("Robinhood" for "Robinhood Markets").
    Anything else is "none": a shared word alone ("Analog" vs "Analog Devices", "Fox" vs
    "Fox Creek Veterinary Hospital") does not make two employers the same.
    """
    if not owner or not company:
        return "none"
    no, nc = normalize(owner), normalize(company)
    if not no or not nc:
        return "none"
    if no.replace(" ", "") == nc.replace(" ", ""):
        return "exact"
    to, tc = set(no.split()), set(nc.split())
    if tc < to and to - tc <= GENERIC_EXTRAS:
        return "exact"
    if to < tc and tc - to <= GENERIC_EXTRAS:
        return "likely"
    return "none"


def same_company(a: str | None, b: str | None) -> bool:
    """True when two names plausibly refer to the same employer (either direction)."""
    return match_level(a, b) != "none" or match_level(b, a) != "none"
