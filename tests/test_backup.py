import csv
import subprocess

from applypeace import backup, config
from applypeace.db import connect, now


def test_old_flat_layout_moves_into_folders(tmp_path, monkeypatch):
    home = tmp_path / "data"
    (home / "resumes").mkdir(parents=True)
    (home / "resumes" / "main.pdf").write_bytes(b"%PDF")
    (home / "settings.yml").write_text("batch_size: 3\n")
    (home / "companies.yml").write_text("companies: []\n")
    (home / "artifacts" / "job").mkdir(parents=True)
    monkeypatch.setenv("APPLYPEACE_HOME", str(home))
    p = config.paths()
    assert p.settings == home / "personal" / "settings.yml" and p.settings.exists()
    assert (home / "personal" / "resumes" / "main.pdf").exists()
    assert p.companies.exists() and (p.artifacts / "job").exists()
    assert config.load_settings(p)["batch_size"] == 3
    assert not (home / "settings.yml").exists()


def test_backup_exports_history_and_commits(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPEACE_HOME", str(tmp_path / "data"))
    plan = tmp_path / "plan.md"
    plan.write_text("---\nopenbase_report:\n  agent_name: Cole\n---\n\n# Plan\nHello\n")
    monkeypatch.setattr(backup, "PLAN_SOURCE", plan)
    p = config.paths()
    with connect(p.db) as conn:
        conn.execute("INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, url, first_seen, last_seen)"
                     " VALUES ('greenhouse:acme:1','acme','greenhouse','acme','1','Analyst','https://x',?,?)", (now(), now()))
        conn.execute("INSERT INTO applications (job_id, status, started_at, finished_at, detail)"
                     " VALUES ('greenhouse:acme:1','submitted',?,?,'Submitted.')", (now(), now()))
        conn.commit()

    assert "no backup repo" in backup.backup(p, push=True)
    rows = list(csv.DictReader((p.applications / "history.csv").open()))
    assert rows[0]["title"] == "Analyst" and rows[0]["status"] == "submitted"
    assert p.plan.read_text().startswith("# Plan")  # report header stripped
    tracked = subprocess.run(["git", "-C", str(p.home), "ls-files"], capture_output=True, text=True).stdout
    assert "applications/history.csv" in tracked and "plan/PLAN.md" in tracked and "README.md" in tracked
    assert "applypeace.db" not in tracked
