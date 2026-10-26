"""Greenhouse public job board API (boards-api.greenhouse.io)."""

from __future__ import annotations

import httpx

from .base import Job, guess_country, html_to_text

API = "https://boards-api.greenhouse.io/v1/boards"


def list_jobs(client: httpx.Client, board: str) -> list[Job]:
    resp = client.get(f"{API}/{board}/jobs", params={"content": "true"})
    resp.raise_for_status()
    jobs = []
    for raw in resp.json().get("jobs", []):
        location = (raw.get("location") or {}).get("name")
        offices = " | ".join(o.get("location") or o.get("name") or "" for o in raw.get("offices") or [])
        jobs.append(
            Job(
                ats="greenhouse",
                board=board,
                ats_job_id=str(raw["id"]),
                title=raw["title"].strip(),
                url=raw["absolute_url"],
                apply_url=raw["absolute_url"],
                location=location,
                # The job's own location decides; the office list (often company-wide) is a fallback.
                country=guess_country(location) or guess_country(offices),
                remote="remote" in (location or "").lower(),
                posted_at=raw.get("first_published") or raw.get("updated_at"),
                description=html_to_text(raw.get("content")),
                extra={"company_name": raw.get("company_name")},
            )
        )
    return jobs


def board_company_name(client: httpx.Client, board: str) -> str | None:
    """The employer name Greenhouse reports for a board, used to verify ownership."""
    resp = client.get(f"{API}/{board}")
    if resp.status_code != 200:
        return None
    return resp.json().get("name")


def job_questions(client: httpx.Client, board: str, job_id: str) -> dict:
    """Every application question for a job, before any browser opens."""
    resp = client.get(f"{API}/{board}/jobs/{job_id}", params={"questions": "true"})
    resp.raise_for_status()
    return resp.json()
