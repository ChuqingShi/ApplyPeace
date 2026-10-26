"""Ashby public job posting API (api.ashbyhq.com/posting-api)."""

from __future__ import annotations

import httpx

from .base import Job, guess_country, html_to_text

API = "https://api.ashbyhq.com/posting-api/job-board"


def list_jobs(client: httpx.Client, board: str) -> list[Job]:
    resp = client.get(f"{API}/{board}")
    resp.raise_for_status()
    jobs = []
    for raw in resp.json().get("jobs", []):
        if raw.get("isListed") is False:
            continue
        address = ((raw.get("address") or {}).get("postalAddress") or {})
        secondary = [s.get("location", "") for s in raw.get("secondaryLocations") or []]
        country = address.get("addressCountry")
        jobs.append(
            Job(
                ats="ashby",
                board=board,
                ats_job_id=raw["id"],
                title=raw["title"].strip(),
                url=raw["jobUrl"],
                apply_url=raw.get("applyUrl"),
                location=raw.get("location"),
                country=("US" if country in ("United States", "USA", "US") else "non-US")
                if country
                else guess_country(raw.get("location"), *secondary),
                remote=bool(raw.get("isRemote")) or raw.get("workplaceType") == "Remote",
                posted_at=raw.get("publishedAt"),
                description=raw.get("descriptionPlain") or html_to_text(raw.get("descriptionHtml")),
            )
        )
    return jobs
