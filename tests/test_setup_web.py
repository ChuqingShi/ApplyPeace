"""Walk the phone setup wizard end to end with a fictional persona (no LLM)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from applypeace import config, profile
from applypeace.web.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPEACE_HOME", str(tmp_path / "data"))
    p = config.paths()
    return TestClient(create_app(p)), p


def token(html: str) -> str:
    return re.search(r'name="_token" value="([^"]+)"', html).group(1)


def test_first_visit_redirects_to_setup(client):
    c, _ = client
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"


def test_forms_reject_missing_token(client):
    c, _ = client
    assert c.post("/setup/choices", data={"llm_consent": "no"}).status_code == 403


def test_full_setup_without_llm(client):
    c, p = client
    t = token(c.get("/setup").text)
    c.post("/setup/choices", data={"_token": t, "llm_consent": "no", "submit_mode": "fill_only", "h1b_filter": "yes"})
    assert config.load_settings(p)["h1b_filter"] is True

    with (FIXTURES / "sample_resume.pdf").open("rb") as f:
        r = c.post("/setup/resume", data={"_token": t, "label": "Data analyst"},
                   files={"file": ("resume.pdf", f, "application/pdf")})
    assert r.status_code == 200 and "Check your details" in r.text
    assert 'value="alex.rivera@example.com"' in r.text  # basic reader found the contact details
    assert profile.resumes_index(p)[0]["key"] == "data-analyst"

    c.post("/setup/profile", data={"_token": t, "first_name": "Alex", "last_name": "Rivera",
                                   "email": "alex.rivera@example.com", "phone": "(555) 010-2030",
                                   "skills": "SQL, Python"})
    c.post("/setup/answers", data={"_token": t, "work_authorized_us": "yes", "needs_sponsorship_now": "no",
                                   "needs_sponsorship_future": "yes", "willing_to_relocate": "yes", "over_18": "yes",
                                   "visa_status": "F-1 STEM OPT"})
    ans = profile.load_answers(p)
    assert ans["needs_sponsorship_future"] is True and ans["eeo"]["gender"] == "decline"

    c.post("/setup/targets", data={"_token": t, "titles": "Data Analyst\nAnalytics Engineer",
                                   "job_location": "us", "list_sp500": "on", "list_forbes_ai50": "on",
                                   "batch_size": "8", "daily_cap": "12", "notify_time": "08:30"})
    settings = config.load_settings(p)
    assert settings["targets"]["titles"] == ["Data Analyst", "Analytics Engineer"]
    assert settings["lists"]["sp500"] and not settings["lists"]["nasdaq100"]
    assert settings["batch_size"] == 8

    assert profile.completeness(p) == ["finish setup (review and confirm)"]
    r = c.post("/setup/finish", data={"_token": t})
    assert r.status_code == 200 and "Today's batch" in r.text
    assert profile.completeness(p) == []

    log = __import__("subprocess").run(["git", "-C", str(p.home), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "Setup complete" in log and "add resume data-analyst" in log
