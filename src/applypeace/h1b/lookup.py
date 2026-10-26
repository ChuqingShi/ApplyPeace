"""Answer "has this company sponsored H-1Bs, and for what roles?" from the LCA store."""

from __future__ import annotations

import sqlite3
from collections import Counter

from ..companies.names import normalize

# Words that may follow a brand in a legal-entity name ("AMAZON.COM SERVICES LLC").
ENTITY_TAIL_WORDS = {
    "com", "services", "service", "usa", "us", "america", "north", "inc", "web", "operations",
    "technologies", "technology", "labs", "global", "international", "solutions", "systems",
    "corporate", "financial", "platforms", "health", "healthcare", "data", "digital", "software",
    "markets", "payments", "commerce", "enterprises", "enterprise", "research", "development",
}
CERTIFIED = ("Certified", "Certified - Withdrawn")


def matching_employers(conn: sqlite3.Connection, company: str, aliases: list[str] | None = None) -> list[str]:
    """Normalised employer names in the LCA data that belong to this company (index lookups only)."""
    names = {normalize(n) for n in [company, *(aliases or [])] if n}
    names.discard("")
    matched: set[str] = set()
    for norm in names:
        squashed = norm.replace(" ", "")
        exact = conn.execute(
            "SELECT DISTINCT employer_norm FROM lca WHERE replace(employer_norm, ' ', '') = ?", (squashed,)
        ).fetchall()
        # Legal-entity variants that start with the brand: "amazon com services", "airbnb payments".
        prefixed = conn.execute(
            "SELECT DISTINCT employer_norm FROM lca WHERE employer_norm >= ? AND employer_norm < ?",
            (norm + " ", norm + "!"),
        ).fetchall()
        dba = conn.execute("SELECT DISTINCT employer_norm FROM lca WHERE dba_norm = ?", (norm,)).fetchall()
        matched.update(e for (e,) in exact + dba)
        for (emp,) in prefixed:
            tail = emp[len(norm):].split()
            if len(tail) <= 2 and set(tail) <= ENTITY_TAIL_WORDS:
                matched.add(emp)
    return sorted(matched)


def summary(conn: sqlite3.Connection, company: str, aliases: list[str] | None = None, min_recent: int = 2) -> dict:
    """Evidence summary used for the H-1B filter and shown on each job card."""
    employers = matching_employers(conn, company, aliases)
    if not employers:
        return {"company": company, "found": False, "sponsor": False, "by_fy": {}, "employers": []}
    marks = ",".join("?" * len(employers))
    rows = conn.execute(
        f"SELECT fy, status, employer_name, job_title, wage_level FROM lca WHERE employer_norm IN ({marks})",
        employers,
    ).fetchall()
    by_fy: dict[int, dict] = {}
    names, titles, levels = Counter(), Counter(), Counter()
    for r in rows:
        year = by_fy.setdefault(r["fy"], {"filed": 0, "certified": 0})
        year["filed"] += 1
        if r["status"] in CERTIFIED:
            year["certified"] += 1
            titles[(r["job_title"] or "").title()] += 1
            if r["wage_level"]:
                levels[r["wage_level"]] += 1
        names[r["employer_name"]] += 1
    latest = sorted(by_fy)[-2:]
    recent = sum(by_fy[y]["certified"] for y in latest)
    return {
        "company": company,
        "found": True,
        "sponsor": recent >= min_recent,
        "recent_certified": recent,
        "recent_years": latest,
        "by_fy": dict(sorted(by_fy.items())),
        "employers": [n for n, _ in names.most_common(5)],
        "top_titles": [t for t, _ in titles.most_common(8) if t],
        "wage_levels": dict(levels.most_common()),
    }


def title_count(conn: sqlite3.Connection, company: str, title: str, aliases: list[str] | None = None) -> int:
    """How many certified LCAs this company filed for titles resembling `title`."""
    employers = matching_employers(conn, company, aliases)
    if not employers:
        return 0
    words = [w for w in normalize(title).split() if len(w) > 2][:3]
    if not words:
        return 0
    marks = ",".join("?" * len(employers))
    clauses = " AND ".join("lower(job_title) LIKE ?" for _ in words)
    return conn.execute(
        f"SELECT COUNT(*) FROM lca WHERE employer_norm IN ({marks}) AND status IN ('Certified','Certified - Withdrawn') AND {clauses}",
        [*employers, *[f"%{w}%" for w in words]],
    ).fetchone()[0]
