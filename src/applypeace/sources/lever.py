"""Lever public postings API (api.lever.co/v0/postings)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .base import Job, guess_country

API = "https://api.lever.co/v0/postings"


def list_jobs(client: httpx.Client, board: str) -> list[Job]:
    resp = client.get(f"{API}/{board}", params={"mode": "json"})
    resp.raise_for_status()
    jobs = []
    for raw in resp.json():
        categories = raw.get("categories") or {}
        location = categories.get("location")
        created = raw.get("createdAt")
        description = "\n\n".join(
            part
            for part in [
                raw.get("descriptionPlain"),
                *[
                    f"{block.get('text', '')}\n{block.get('content', '')}"
                    for block in raw.get("lists") or []
                ],
                raw.get("additionalPlain"),
            ]
            if part
        )
        country_code = raw.get("country")
        jobs.append(
            Job(
                ats="lever",
                board=board,
                ats_job_id=raw["id"],
                title=raw["text"].strip(),
                url=raw["hostedUrl"],
                apply_url=raw.get("applyUrl"),
                location=location,
                country=("US" if country_code == "US" else "non-US")
                if country_code
                else guess_country(location, *(categories.get("allLocations") or [])),
                remote=raw.get("workplaceType") == "remote",
                posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).isoformat()
                if created
                else None,
                description=description,
            )
        )
    return jobs
