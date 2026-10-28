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


def test_title_matching_respects_word_order_and_level():
    from applypeace.rank.filters import SENIOR_TITLE, title_matches

    assert title_matches("AI Platform Engineer", ["AI Engineer"])
    assert title_matches("Data Scientists, Growth", ["Data Scientist"])
    assert not title_matches("Senior Sales Engineer - AI Accounts", ["AI Engineer"])
    assert not title_matches("Data Engineer, Science Team", ["Data Scientist"])
    assert SENIOR_TITLE.search("Senior Data Scientist") and SENIOR_TITLE.search("Staff ML Engineer")
    assert SENIOR_TITLE.search("Data Scientist III") and SENIOR_TITLE.search("Lead Forward Deployed Engineer")
    assert not SENIOR_TITLE.search("Data Scientist II") and not SENIOR_TITLE.search("Applied Scientist")
    from applypeace.rank.filters import NOT_SENIOR

    assert not SENIOR_TITLE.search(NOT_SENIOR.sub(" ", "Member of Technical Staff, Machine Learning"))


def test_hispanic_gate_question_is_not_read_as_race():
    from applypeace.answers import _profile_value
    from applypeace.forms import Question

    q = Question("hispanic_ethnicity", "Are you Hispanic or Latino?", "select", False,
                 [{"label": l, "value": l} for l in ("Yes", "No", "Decline To Self Identify")], "eeo")
    answers = {"eeo": {"race": "Asian", "hispanic_latino": "decline"}}
    assert _profile_value(q, {}, answers, "Acme")[0] == "Decline To Self Identify"
    answers["eeo"]["hispanic_latino"] = "No"
    assert _profile_value(q, {}, answers, "Acme")[0] == "No"


def test_workday_search_terms_and_posted_dates():
    from datetime import datetime, timezone

    from applypeace.sources import workday

    assert workday.search_terms(["Data Scientist", "Applied Data Scientist", "AI Engineer"]) == ["ai engineer", "data scientist"]
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    assert workday.posted_date("Posted Today", now) == "2026-10-28"
    assert workday.posted_date("Posted Yesterday", now) == "2026-10-27"
    assert workday.posted_date("Posted 3 Days Ago", now) == "2026-10-25"
    assert workday.posted_date("Posted 30+ Days Ago", now) == "2026-09-27"


@respx.mock
def test_workday_lists_matching_titles_and_reads_details():
    from applypeace.sources import workday

    api = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External"
    page1 = [{"title": f"Data Scientist {i}", "externalPath": f"/job/US-CA/Data-Scientist_R{i}",
              "locationsText": "US, CA, San Jose", "postedOn": "Posted Today"} for i in range(19)]
    page1.append({"title": "Data Scientist", "externalPath": "/job/x/Data-Scientist_R99", "locationsText": "2 Locations",
                  "postedOn": "Posted 2 Days Ago"})
    page2 = [{"title": "Office Manager", "externalPath": f"/job/x/Office_R{i}", "locationsText": "Austin, TX"} for i in range(20)]
    route = respx.post(f"{api}/jobs").mock(side_effect=[httpx.Response(200, json={"jobPostings": page1}),
                                                       httpx.Response(200, json={"jobPostings": page2})])
    with httpx.Client() as client:
        jobs = workday.list_jobs(client, "acme.wd5.External", ["Data Scientist"])
    assert route.call_count == 2  # page two had no matching titles, so the search stopped there
    assert len(jobs) == 20 and jobs[0].id == "workday:acme.wd5.External:Data-Scientist_R0" and jobs[0].country == "US"
    many = jobs[-1]
    assert many.location is None and many.country is None  # "2 Locations": read from the job page instead
    assert many.url == "https://acme.wd5.myworkdayjobs.com/en-US/External/job/x/Data-Scientist_R99"

    respx.get(f"{api}/job/x/Data-Scientist_R99").mock(return_value=httpx.Response(200, json={"jobPostingInfo": {
        "jobDescription": "<p>Build models</p>", "location": "Austin, TX", "additionalLocations": ["Toronto"],
        "startDate": "2026-10-26", "jobRequisitionLocation": {"country": {"alpha2Code": "US"}}}}))
    with httpx.Client() as client:
        info = workday.details(client, many.board, many.url)
    assert info == {"description": "Build models", "location": "Austin, TX | Toronto", "country": "US",
                    "remote": False, "posted_at": "2026-10-26"}


def test_workday_pages_worth_reading():
    from applypeace import config
    from applypeace.rank.filters import worth_reading

    s = {**config.DEFAULT_SETTINGS, "targets": {**config.DEFAULT_SETTINGS["targets"], "seniority": "early",
                                                "exclude_titles": ["Intern"]}}
    wanted = worth_reading(s)
    assert wanted("Data Scientist", None) and wanted("Data Scientist", "US")
    assert not wanted("Data Scientist", "non-US") and not wanted("Senior Data Scientist", "US")
    assert not wanted("Data Scientist Intern", "US")


def test_mid_level_keeps_iii_and_drops_senior():
    from applypeace.rank.filters import too_senior

    assert too_senior("Data Scientist III", "early") and not too_senior("Data Scientist III", "mid")
    assert too_senior("Senior Data Scientist", "mid") and too_senior("Lead Applied Scientist", "mid")
    assert not too_senior("Applied Scientist II", "mid") and not too_senior("Senior Data Scientist", "any")
    assert not too_senior("Member of Technical Staff", "mid")
