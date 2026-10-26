"""H-1B evidence database built from the DOL OFLC LCA disclosure files.

Each row is one H-1B Labor Condition Application: an employer's filing for a
specific position. A certified LCA shows the employer intended to sponsor; it
is not proof a visa was granted. The employer's FEIN is withheld by DOL, so
employers are matched by name.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx

from ..companies.names import normalize

DOL_PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
USER_AGENT = "ApplyPeace/0.1 (https://github.com/ChuqingShi/ApplyPeace)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lca (
    case_number    TEXT PRIMARY KEY,
    fy             INTEGER NOT NULL,
    status         TEXT,
    decision_date  TEXT,
    employer_name  TEXT,
    employer_norm  TEXT,
    dba_norm       TEXT,
    employer_state TEXT,
    worksite_state TEXT,
    job_title      TEXT,
    soc_code       TEXT,
    soc_title      TEXT,
    full_time      TEXT,
    wage_from      REAL,
    wage_unit      TEXT,
    wage_level     TEXT
);
CREATE INDEX IF NOT EXISTS lca_employer ON lca(employer_norm);
CREATE INDEX IF NOT EXISTS lca_employer_key ON lca(replace(employer_norm, ' ', ''));
CREATE INDEX IF NOT EXISTS lca_dba ON lca(dba_norm);
CREATE TABLE IF NOT EXISTS ingested (
    file        TEXT PRIMARY KEY,
    fy          INTEGER,
    rows        INTEGER,
    ingested_at TEXT
);
"""

COLUMNS = {
    "CASE_NUMBER": "case_number", "CASE_STATUS": "status", "DECISION_DATE": "decision_date",
    "VISA_CLASS": "visa_class", "EMPLOYER_NAME": "employer_name", "TRADE_NAME_DBA": "dba",
    "EMPLOYER_STATE": "employer_state", "WORKSITE_STATE": "worksite_state", "JOB_TITLE": "job_title",
    "SOC_CODE": "soc_code", "SOC_TITLE": "soc_title", "FULL_TIME_POSITION": "full_time",
    "WAGE_RATE_OF_PAY_FROM": "wage_from", "WAGE_UNIT_OF_PAY": "wage_unit", "PW_WAGE_LEVEL": "wage_level",
}

FILE_RE = re.compile(r"LCA_Disclosure_Data_FY(\d{4})_Q(\d)\.xlsx", re.I)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


def list_dol_files(client: httpx.Client) -> list[tuple[int, int, str]]:
    """(fiscal year, quarter, absolute URL) for every LCA disclosure file on the DOL page."""
    page = client.get(DOL_PAGE)
    page.raise_for_status()
    found = {}
    for href in re.findall(r'href="([^"]+\.xlsx)"', page.text):
        m = FILE_RE.search(href)
        if m:
            url = href if href.startswith("http") else f"https://www.dol.gov{href}"
            found[(int(m.group(1)), int(m.group(2)))] = url
    return sorted((fy, q, url) for (fy, q), url in found.items())


def _cell(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.strip() if isinstance(value, str) else value


def ingest_xlsx(conn: sqlite3.Connection, path: Path, fy: int, progress=None) -> int:
    """Stream one disclosure workbook into the lca table (H-1B rows only). Returns rows stored."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip().upper() if h else "" for h in next(rows)]
    index = {COLUMNS[h]: i for i, h in enumerate(header) if h in COLUMNS}
    missing = {"case_number", "employer_name", "visa_class"} - index.keys()
    if missing:
        raise ValueError(f"{path.name}: unexpected layout, missing {sorted(missing)}")

    def get(row, key):
        i = index.get(key)
        return _cell(row[i]) if i is not None and i < len(row) else None

    batch, stored = [], 0
    for n, row in enumerate(rows, 1):
        if (get(row, "visa_class") or "").upper() != "H-1B":
            continue
        wage = get(row, "wage_from")
        try:
            wage = float(str(wage).replace("$", "").replace(",", "")) if wage not in (None, "") else None
        except ValueError:
            wage = None
        employer = get(row, "employer_name") or ""
        dba = get(row, "dba") or ""
        batch.append((
            get(row, "case_number"), fy, get(row, "status"), get(row, "decision_date"), employer,
            normalize(employer), normalize(dba) if dba else None, get(row, "employer_state"),
            get(row, "worksite_state"), get(row, "job_title"), get(row, "soc_code"), get(row, "soc_title"),
            get(row, "full_time"), wage, get(row, "wage_unit"), get(row, "wage_level"),
        ))
        if len(batch) >= 5000:
            stored += _flush(conn, batch)
            if progress:
                progress(n)
    stored += _flush(conn, batch)
    wb.close()
    conn.execute(
        "INSERT OR REPLACE INTO ingested (file, fy, rows, ingested_at) VALUES (?, ?, ?, datetime('now'))",
        (path.name, fy, stored),
    )
    conn.commit()
    return stored


def _flush(conn: sqlite3.Connection, batch: list) -> int:
    if batch:
        conn.executemany(f"INSERT OR REPLACE INTO lca VALUES ({','.join('?' * 16)})", batch)
        conn.commit()
    count = len(batch)
    batch.clear()
    return count


def renormalize(conn: sqlite3.Connection) -> int:
    """Recompute stored name keys after the normalisation rules change. Returns rows updated."""
    cache: dict[str, str] = {}

    def norm(value: str | None) -> str | None:
        if value is None:
            return None
        if value not in cache:
            cache[value] = normalize(value)
        return cache[value]

    updates = []
    for rowid, employer, emp_norm, dba_norm in conn.execute("SELECT rowid, employer_name, employer_norm, dba_norm FROM lca"):
        new_emp, new_dba = norm(employer or ""), norm(dba_norm)  # raw DBA isn't stored; re-normalising its key is equivalent
        if new_emp != emp_norm or new_dba != dba_norm:
            updates.append((new_emp, new_dba, rowid))
    conn.executemany("UPDATE lca SET employer_norm=?, dba_norm=? WHERE rowid=?", updates)
    conn.commit()
    return len(updates)


def update(db_path: Path, raw_dir: Path, years: list[int], log=print) -> None:
    """Download and ingest the disclosure files for the given fiscal years, skipping done ones.

    Raw workbooks are deleted after ingesting to save disk space.
    """
    conn = connect(db_path)
    done = {r["file"] for r in conn.execute("SELECT file FROM ingested")}
    raw_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=600, follow_redirects=True) as client:
        files = [f for f in list_dol_files(client) if f[0] in years]
        # A newer quarter of the current year is cumulative from FY2026 on; ingesting
        # every listed file is still correct because rows are keyed by case number.
        for fy, q, url in files:
            name = url.rsplit("/", 1)[-1]
            if name in done:
                log(f"FY{fy} Q{q}: already ingested")
                continue
            dest = raw_dir / name
            if not dest.exists():
                log(f"FY{fy} Q{q}: downloading {name}")
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with dest.open("wb") as f:
                        for chunk in resp.iter_bytes(1 << 20):
                            f.write(chunk)
            log(f"FY{fy} Q{q}: reading rows (this takes a few minutes)")
            stored = ingest_xlsx(conn, dest, fy, progress=lambda n: None)
            log(f"FY{fy} Q{q}: {stored} H-1B rows")
            dest.unlink()
    conn.close()
