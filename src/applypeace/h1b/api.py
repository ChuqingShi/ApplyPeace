"""A small HTTP API over the H-1B store, so other tools can reuse it."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query

from . import default_db
from .lookup import summary
from .store import connect


def create_api(db_path: Path | None = None) -> FastAPI:
    db_path = db_path or default_db()
    api = FastAPI(title="ApplyPeace H-1B evidence API")

    @api.get("/verify")
    def verify(name: str = Query(..., description="Company name, e.g. 'Airbnb'"), alias: list[str] = Query(default=[])):
        with connect(db_path) as conn:
            return summary(conn, name, alias)

    @api.get("/employers")
    def employers(q: str = Query(..., min_length=2), limit: int = 20):
        with connect(db_path) as conn:
            rows = conn.execute(
                "SELECT employer_name, COUNT(*) n FROM lca WHERE employer_name LIKE ? GROUP BY employer_name"
                " ORDER BY n DESC LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
        return [{"employer": r["employer_name"], "filings": r["n"]} for r in rows]

    @api.get("/coverage")
    def coverage():
        with connect(db_path) as conn:
            return [dict(r) for r in conn.execute("SELECT file, fy, rows, ingested_at FROM ingested ORDER BY file")]

    return api
