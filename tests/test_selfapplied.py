import csv

from applypeace import backup, config
from applypeace.db import connect, now
from applypeace.selfapplied import add_external, job_id_from_url, mark_applied


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPEACE_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(backup, "PLAN_SOURCE", tmp_path / "none.md")
    p = config.paths()
    conn = connect(p.db)
    conn.execute("INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, url, first_seen, last_seen, status)"
                 " VALUES ('ashby:pinecone:edf2a2a5-64e2-45b8-9f2e-4f354af350b8','pinecone','ashby','pinecone',"
                 "'edf2a2a5-64e2-45b8-9f2e-4f354af350b8','Applied AI Engineer','https://jobs.ashbyhq.com/pinecone/x',?,?,'gave_up')",
                 (now(), now()))
    conn.execute("INSERT INTO applications (job_id, status, detail) VALUES"
                 " ('ashby:pinecone:edf2a2a5-64e2-45b8-9f2e-4f354af350b8','gave_up','given up')")
    conn.commit()
    return p, conn


def test_gave_up_job_can_be_marked_applied_by_you(tmp_path, monkeypatch):
    p, conn = setup(tmp_path, monkeypatch)
    mark_applied(conn, "ashby:pinecone:edf2a2a5-64e2-45b8-9f2e-4f354af350b8", "2026-10-27", "ai", "on their site")
    app = conn.execute("SELECT * FROM applications").fetchone()
    assert (app["status"], app["method"], app["note"]) == ("submitted", "you", "on their site")
    assert app["finished_at"].startswith("2026-10-27")
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "submitted"


def test_link_matches_known_job_and_unknown_company_is_logged(tmp_path, monkeypatch):
    p, conn = setup(tmp_path, monkeypatch)
    url = "https://jobs.ashbyhq.com/pinecone/edf2a2a5-64e2-45b8-9f2e-4f354af350b8/application"
    assert job_id_from_url(conn, url) == "ashby:pinecone:edf2a2a5-64e2-45b8-9f2e-4f354af350b8"
    assert add_external(p, conn, "Pinecone", "Applied AI Engineer", url) == job_id_from_url(conn, url)

    job_id = add_external(p, conn, "Acme Robotics", "ML Engineer", "https://www.linkedin.com/jobs/view/1",
                          "2026-10-20", note="referral")
    assert job_id.startswith("manual:")
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert job["company_name"] == "Acme Robotics" and job["status"] == "submitted"

    backup.backup(p, push=False)
    rows = {r["title"]: r for r in csv.DictReader((p.applications / "history.csv").open())}
    assert rows["ML Engineer"]["applied_by"] == "you" and rows["ML Engineer"]["company"] == "Acme Robotics"
    assert rows["ML Engineer"]["note"] == "referral"
