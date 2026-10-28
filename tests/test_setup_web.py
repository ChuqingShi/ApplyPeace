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


def test_job_page_offers_each_resume_and_switches(client):
    from applypeace import answers as ans
    from applypeace.db import connect, now

    c, p = client
    profile.add_resume(p, FIXTURES / "sample_resume.pdf", "Data analyst")
    profile.add_resume(p, FIXTURES / "sample_resume.pdf", "Product")
    config.save_yaml(p.profile, {**profile.PROFILE_TEMPLATE, "first_name": "A", "last_name": "B", "email": "a@b.c", "phone": "1"})
    a = profile.load_answers(p)
    a.update(work_authorized_us=True, needs_sponsorship_now=False, needs_sponsorship_future=False)
    config.save_yaml(p.answers, a)
    s = config.load_settings(p)
    s["targets"]["titles"], s["setup_complete"] = ["Analyst"], True
    config.save_settings(s, p)
    with connect(p.db) as conn:
        conn.execute("INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, url, first_seen, last_seen, status, resume_key)"
                     " VALUES ('lever:x:1','x','lever','x','1','Analyst','u',?,?,'queued','data-analyst')", (now(), now()))
        ans.store(conn, "lever:x:1", [{"field": "resume", "label": "Resume/CV", "kind": "file", "required": True,
                                       "options": [], "value": {"resume": True}, "source": "profile", "position": 0, "section": "standard"}])
    html = c.get("/jobs/lever:x:1").text
    assert 'value="data-analyst" checked' in html and 'value="product"' in html and "/resumes/product" in html
    assert c.get("/resumes/product").headers["content-type"] == "application/pdf"
    t = token(html)
    c.post("/jobs/lever:x:1/save", data={"_token": t, "resume_key": "product"})
    with connect(p.db) as conn:
        assert conn.execute("SELECT resume_key FROM jobs").fetchone()[0] == "product"


def test_schedule_page_saves_custom_times(client):
    from applypeace import schedule

    c, p = client
    html = c.get("/schedule").text
    assert 'name="scan_count"' in html and "7:30 AM" in html and 'id="tz-banner"' in html
    t = token(html)
    c.post("/schedule", data={"_token": t, "timezone": "America/New_York", "notify_time": "07:00",
                              "scan_count": "4", "scan_1": "06:30", "scan_2": "12:00", "scan_3": "18:00",
                              "scan_4": "23:30", "apply_gap_minutes": "3"})
    s = config.load_settings(p)
    assert s["notify_time"] == "07:00" and s["scan_times"] == ["06:30", "12:00", "18:00", "23:30"]
    assert s["apply_gap_seconds"] == 180 and s["timezone"] == "America/New_York"
    assert [r["time"] for r in schedule.routines(p) if "cycle" in r["name"]] == ["06:30", "12:00", "18:00", "23:30"]
    r = c.post("/schedule", data={"_token": t, "timezone": "Mars/Base", "notify_time": "07:00",
                                  "scan_count": "1", "scan_1": "06:30", "apply_gap_minutes": "3"})
    assert "time zone" in r.text and config.load_settings(p)["timezone"] == "America/New_York"
    r = c.post("/schedule", data={"_token": t, "timezone": "America/New_York", "notify_time": "07:13",
                                  "scan_count": "1", "scan_1": "06:30"})
    assert "Pick" in r.text
