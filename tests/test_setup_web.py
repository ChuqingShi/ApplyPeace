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
    c.post("/schedule", data={"_token": t, "notify_timezone": "America/New_York", "notify_time": "07:00",
                              "scan_timezone": "Asia/Tokyo", "scan_count": "4", "notify_mode": "daily", "scan_1": "06:30", "scan_2": "12:00", "scan_3": "18:00",
                              "scan_4": "23:30", "apply_gap_minutes": "3"})
    s = config.load_settings(p)
    assert s["notify_time"] == "07:00" and s["scan_times"] == ["06:30", "12:00", "18:00", "23:30"]
    assert s["apply_gap_seconds"] == 180 and s["notify_timezone"] == "America/New_York"
    routines = schedule.routines(p)
    assert [r["time"] for r in routines if "cycle" in r["name"]] == ["06:30", "12:00", "18:00", "23:30"]
    assert {r["timezone"] for r in routines if "cycle" in r["name"]} == {"Asia/Tokyo"}
    assert next(r for r in routines if r["name"].endswith("digest"))["timezone"] == "America/New_York"
    r = c.post("/schedule", data={"_token": t, "scan_timezone": "Mars/Base", "notify_time": "07:00",
                                  "scan_count": "1", "scan_1": "06:30", "apply_gap_minutes": "3"})
    assert "time zone" in r.text and config.load_settings(p)["scan_timezone"] == "Asia/Tokyo"
    r = c.post("/schedule", data={"_token": t, "notify_time": "07:13",
                                  "scan_count": "1", "scan_1": "06:30"})
    assert "Pick" in r.text


def test_after_scan_mode_notifies_only_when_jobs_are_added(client, monkeypatch):
    from applypeace import pipeline, schedule

    c, p = client
    s = config.load_settings(p)
    assert s["notify_mode"] == "after_scan"
    assert not any(r["name"].endswith("digest") for r in schedule.routines(p))  # no separate daily alert
    sent = []
    monkeypatch.setattr(pipeline, "notify", lambda msg, **k: sent.append(msg))
    monkeypatch.setattr(pipeline, "require_setup", lambda p: None)
    from applypeace.db import connect, now
    with connect(p.db) as conn:
        conn.execute("INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, url, first_seen, last_seen, status)"
                     " VALUES ('x:a:1','a','x','a','1','Analyst','u',?,?,'queued')", (now(), now()))
        conn.commit()
    pipeline.digest(p, force=True, new_jobs=1)
    assert sent == ["1 new job to review (1 in your batch)."]


def test_activity_log_lists_scans_and_what_you_applied_to(client):
    from applypeace.db import connect, log_event, now
    from applypeace.selfapplied import mark_applied

    c, p = client
    with connect(p.db) as conn:
        for jid, status in (("x:a:1", "queued"), ("x:a:2", "queued")):
            conn.execute("INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, url, first_seen, last_seen, status)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?)", (jid, "acme", "x", "a", jid[-1], f"Analyst {jid[-1]}", "u", now(), now(), status))
        log_event(conn, "cycle", detail={"trigger": "scheduled", "new": 12, "queued": ["x:a:1", "x:a:2"]})
        log_event(conn, "cycle", detail={"trigger": "manual", "new": 0, "queued": []})
        mark_applied(conn, "x:a:1")
        log_event(conn, "apply_needs_you", "x:a:2", {"detail": "stuck"})
        conn.commit()
    html = c.get("/activity").text
    assert html.count("Scan ·") == 2 and "2 new for you" in html and "0 new for you" in html
    assert "Analyst 1" in html and "(by you)" in html
    assert "Analyst 2" not in html and "stuck" not in html  # not applied; no per-application cards


def test_a_role_you_applied_to_never_comes_back(client):
    from applypeace.db import connect, now
    from applypeace.rank.filters import ALREADY_APPLIED, ALREADY_SEEN, apply_filters
    from applypeace.selfapplied import mark_applied

    _, p = client
    settings = config.load_settings(p)
    with connect(p.db) as conn:
        rows = [("x:a:1", "Data Analyst", "queued"), ("x:a:2", "Data Analyst", "skipped"),  # same role, two postings
                ("x:a:3", "data analyst ", "new"), ("x:a:4", "ML Engineer", "new"), ("x:a:5", "ML Engineer", "new"),
                ("x:a:6", "Platform Engineer", "queued"), ("x:a:7", "Platform Engineer", "new")]
        for jid, title, status in rows:
            conn.execute("INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, country, url, first_seen, last_seen, status)"
                         " VALUES (?,?,?,?,?,?,'US','u',?,?,?)", (jid, "acme", "x", "a", jid[-1], title, now(), now(), status))
        mark_applied(conn, "x:a:1")
        status = dict(conn.execute("SELECT id, status FROM jobs").fetchall())
        assert status["x:a:2"] == "filtered"  # gone from "Kept for later" right away
        apply_filters(conn, settings, {"acme"}, {})
        jobs = {r["id"]: (r["status"], r["status_reason"]) for r in conn.execute("SELECT * FROM jobs")}
    assert jobs["x:a:1"] == ("submitted", "applied by you")
    assert jobs["x:a:3"] == ("filtered", ALREADY_APPLIED)
    assert jobs["x:a:4"][0] == "candidate" and jobs["x:a:5"] == ("filtered", ALREADY_SEEN)
    assert jobs["x:a:6"][0] == "queued" and jobs["x:a:7"] == ("filtered", ALREADY_SEEN)


def test_scan_now_starts_a_manual_scan_and_always_notifies(client, monkeypatch):
    from applypeace import pipeline, schedule
    from applypeace.discover import DiscoverResult
    from applypeace.rank import RankResult

    c, p = client
    started = []
    monkeypatch.setattr(pipeline, "require_setup", lambda p: None)
    c.get("/setup")  # creates the data folder
    t = token(c.get("/schedule").text)
    with monkeypatch.context() as m:
        m.setattr(schedule.subprocess, "Popen", lambda cmd, **k: started.append(cmd))
        m.setattr(schedule.time, "sleep", lambda s: None)
        r = c.post("/scan-now", data={"_token": t}, follow_redirects=False)
    assert r.headers["location"] == "/" and started and started[0][1:] == ["cycle", "--manual"]

    with pipeline.single_run(p, "cycle"):  # a scan is running: no second one
        assert schedule.scanning(p) and not schedule.scan_now(p)

    sent = []
    monkeypatch.setattr(pipeline, "notify", lambda msg, **k: sent.append(msg) or True)
    monkeypatch.setattr(pipeline, "discover", lambda conn, companies, titles=None: DiscoverResult())
    monkeypatch.setattr(pipeline, "rank", lambda p, conn, log: RankResult())
    monkeypatch.setattr(pipeline, "select_batch", lambda p, conn: [])
    monkeypatch.setattr(pipeline, "prepare_queue", lambda p, conn, log: {"prepared": 0})
    pipeline.cycle(p, log=lambda *a: None, trigger="manual")
    assert sent == ["Scan done: no new jobs for you this time."]
    sent.clear()
    pipeline.cycle(p, log=lambda *a: None, trigger="scheduled")
    assert sent == []  # scheduled scans stay quiet when nothing is new
    from applypeace.db import connect
    with connect(p.db) as conn:
        assert schedule.last_scan(conn, "America/Los_Angeles")["added"] == 0


def test_next_scan_time():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from applypeace.schedule import next_scan

    s = {**config.DEFAULT_SETTINGS, "scan_timezone": "America/Los_Angeles", "scan_times": ["06:30", "14:30", "22:30"]}
    at = lambda h, m: datetime(2026, 10, 28, h, m, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert next_scan(s, at(9, 0)) == "today at 2:30 PM PDT"
    assert next_scan(s, at(22, 30)) == "tomorrow at 6:30 AM PDT"


def test_quick_reject_from_skipped_by_ai(client):
    import json

    from applypeace import review
    from applypeace.db import connect, now

    c, p = client
    judge = {"score": 2, "recommendation": "skip", "reasons": [], "concerns": ["Requires U.S. person status (ITAR)"],
             "sponsorship_clues": "", "resume_key": "x"}
    with connect(p.db) as conn:
        conn.execute("INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, url, first_seen, last_seen, status, judge)"
                     " VALUES ('x:a:1','acme','x','a','1','Analyst','u',?,?,'candidate',?)", (now(), now(), json.dumps(judge)))
        conn.commit()
    html = c.get("/jobs/x:a:1").text
    assert "Bring back for review" in html and 'class="quick-reject"' in html
    assert '<option value="sponsorship" selected>' in html
    c.post("/jobs/x:a:1/reject", data={"_token": token(html), "reason": "sponsorship"})
    with connect(p.db) as conn:
        assert tuple(conn.execute("SELECT status, status_reason FROM jobs").fetchone()) == ("rejected", "visa / sponsorship")
    assert review.suggest_reason({"concerns": ["Needs 8+ years of experience"]}) == "too_senior"
    assert review.suggest_reason(None) == "other"


def test_workday_jobs_are_yours_to_apply_and_never_auto_approved(client):
    from applypeace import review
    from applypeace.apply.runner import apply_one
    from applypeace.db import connect, now
    from applypeace.forms import questions_for

    c, p = client
    profile.add_resume(p, FIXTURES / "sample_resume.pdf", "Data analyst")
    with connect(p.db) as conn:
        conn.execute("INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, url, first_seen, last_seen, status, resume_key)"
                     " VALUES ('workday:acme.wd5.Ext:R1','acme','workday','acme.wd5.Ext','R1','Analyst',"
                     " 'https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/x/R1',?,?,'queued','data-analyst')", (now(), now()))
        conn.commit()
        assert questions_for(None, conn.execute("SELECT * FROM jobs").fetchone()) == []
        assert review.approve_ready(p, conn) == 0
        from applypeace import approval
        approval.approve(p, conn, "workday:acme.wd5.Ext:R1")
        assert apply_one(p, conn, "workday:acme.wd5.Ext:R1") == "needs_you"  # never crashes if one slips through
        conn.execute("UPDATE jobs SET status='queued'")
        conn.commit()
    conn.close()
    html = c.get("/jobs/workday:acme.wd5.Ext:R1").text
    assert "Workday site" in html and "Open the application" in html and "Skip for now" in html
    assert "<details class=\"card\" open>" in html and "/resumes/data-analyst" in html
