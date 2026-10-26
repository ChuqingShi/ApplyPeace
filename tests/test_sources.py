import httpx
import respx

from applypeace.sources import ashby, greenhouse, lever
from applypeace.sources.base import guess_country, html_to_text


def test_guess_country():
    assert guess_country("San Francisco, CA | Seattle, WA") == "US"
    assert guess_country("Remote - US") == "US"
    assert guess_country("New York City") == "US"
    assert guess_country("London, United Kingdom") == "non-US"
    assert guess_country("Remote") is None
    assert guess_country("Remote - Canada") == "non-US"
    assert guess_country("Hybrid / Remote") is None
    assert guess_country(None) is None


@respx.mock
def test_greenhouse_location_beats_company_wide_offices():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{
            "id": 8, "title": "PM", "absolute_url": "u", "location": {"name": "S\u00e3o Paulo, Brazil"},
            "offices": [{"name": "San Francisco, United States", "location": "San Francisco, CA"}]}]}))
    with httpx.Client() as client:
        [job] = greenhouse.list_jobs(client, "acme")
    assert job.country == "non-US"


def test_html_to_text_handles_double_encoding():
    raw = "&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
    text = html_to_text(raw)
    assert "Build & ship" in text and "- Python" in text


@respx.mock
def test_greenhouse_list_jobs():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{
            "id": 7, "title": " Data Analyst ", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/7",
            "location": {"name": "Austin, TX"}, "offices": [], "first_published": "2026-10-20T00:00:00Z",
            "content": "&lt;p&gt;SQL&lt;/p&gt;", "company_name": "Acme"}]}))
    with httpx.Client() as client:
        [job] = greenhouse.list_jobs(client, "acme")
    assert (job.id, job.title, job.country, job.description) == ("greenhouse:acme:7", "Data Analyst", "US", "SQL")


@respx.mock
def test_ashby_skips_unlisted_and_reads_country():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(
        return_value=httpx.Response(200, json={"jobs": [
            {"id": "a", "title": "ML Engineer", "jobUrl": "u", "applyUrl": "u/application", "location": "Toronto",
             "address": {"postalAddress": {"addressCountry": "Canada"}}, "descriptionPlain": "x", "isListed": True},
            {"id": "b", "title": "Hidden", "jobUrl": "u", "isListed": False},
        ]}))
    with httpx.Client() as client:
        jobs = ashby.list_jobs(client, "acme")
    assert [(j.ats_job_id, j.country) for j in jobs] == [("a", "non-US")]


@respx.mock
def test_lever_uses_country_code_and_created_at():
    respx.get("https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(200, json=[{
            "id": "x", "text": "Analyst", "hostedUrl": "h", "applyUrl": "h/apply", "country": "US",
            "createdAt": 1790000000000, "categories": {"location": "Denver, CO"}, "workplaceType": "remote",
            "descriptionPlain": "About", "lists": [{"text": "You have", "content": "SQL"}]}]))
    with httpx.Client() as client:
        [job] = lever.list_jobs(client, "acme")
    assert job.country == "US" and job.remote and job.posted_at.startswith("2026")
    assert "You have" in job.description
