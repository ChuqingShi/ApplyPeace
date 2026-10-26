"""End to end on a local Lever-style job site: draft, approve, lock, submit, confirm, code hand-off."""

import http.server
import json
import shutil
import threading
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from applypeace import answers as ans
from applypeace import config, profile, review
from applypeace.apply import runner
from applypeace.approval import approve, verify
from applypeace.db import connect, now
from applypeace.forms import Question, lever_questions
from applypeace.sources.base import http_client

FIXTURES = Path(__file__).parent / "fixtures"
CARD = json.dumps({"fields": [
    {"type": "multiple-choice", "text": "Will you now or in the future require sponsorship?", "required": True,
     "options": [{"text": "Yes"}, {"text": "No"}]},
    {"type": "textarea", "text": "Why do you want to work here?", "required": False},
]}).replace('"', "&quot;")
FORM = f"""<html><body><form method="post" action="/submit" enctype="multipart/form-data">
<li class="application-question"><div class="application-label">Resume/CV ✱</div><input type="file" name="resume" required></li>
<li class="application-question"><div class="application-label">Full name ✱</div><input name="name" required></li>
<li class="application-question"><div class="application-label">Email ✱</div><input name="email" required></li>
<li class="application-question"><div class="application-label">Phone</div><input name="phone"></li>
<input type="hidden" name="cards[c1][baseTemplate]" value="{CARD}">
<label><input type="radio" name="cards[c1][field0]" value="Yes">Yes</label>
<label><input type="radio" name="cards[c1][field0]" value="No">No</label>
<textarea name="cards[c1][field1]"></textarea>
<button id="btn-submit" type="submit">Submit application</button></form></body></html>"""
CODE_PAGE = """<html><body><p>Enter the security code we sent to your email.</p>
<form method="post" action="/code"><input type="text" name="code" autocomplete="one-time-code">
<button type="submit">Submit</button></form></body></html>"""
THANKS = "<html><body><h1>Application submitted!</h1><p>Thanks for applying.</p></body></html>"


class Site(http.server.BaseHTTPRequestHandler):
    ask_code = False
    received: list = []

    def log_message(self, *args):
        pass

    def _send(self, body: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        self._send(FORM if self.path.startswith("/apply") else THANKS)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Site.received.append((self.path, body))
        if self.path == "/submit" and Site.ask_code:
            self._send(CODE_PAGE)
        elif self.path == "/code":
            assert parse_qs(body.decode())["code"] == ["123456"]
            self._send(THANKS)
        else:
            self._send(THANKS)


@pytest.fixture
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Site.received, Site.ask_code = [], False
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPEACE_HOME", str(tmp_path / "data"))
    p = config.paths()
    p.home.mkdir(parents=True)
    profile.add_resume(p, FIXTURES / "sample_resume.pdf", "Main")
    config.save_yaml(p.profile, {**profile.PROFILE_TEMPLATE, "first_name": "Alex", "last_name": "Rivera",
                                 "email": "alex@example.com", "phone": "555-0100"})
    a = profile.load_answers(p)
    a.update(work_authorized_us=True, needs_sponsorship_now=False, needs_sponsorship_future=True)
    config.save_yaml(p.answers, a)
    s = config.load_settings(p)
    s.update(submit_mode="auto", setup_complete=True)
    config.save_settings(s, p)
    return p


def add_job(p, conn, url):
    conn.execute(
        "INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, url, apply_url, first_seen, last_seen, status, description)"
        " VALUES ('lever:acme:1','acme','lever','acme','1','Data Analyst',?,?,?,?,'queued','Analyze data.')",
        (url, url + "/apply", now(), now()))
    with http_client() as client:
        questions = lever_questions(client, url + "/apply")
    rows = ans.draft_answers(p, {"title": "Data Analyst", "description": "x"}, questions, "Acme")
    ans.store(conn, "lever:acme:1", rows)
    return rows


def test_lever_questions_and_drafted_answers(home, site):
    with connect(home.db) as conn:
        rows = add_job(home, conn, site)
    by_label = {r["label"]: r for r in rows}
    assert by_label["Full name"]["value"] == "Alex Rivera"
    assert by_label["Will you now or in the future require sponsorship?"]["value"] == "Yes"
    assert by_label["Resume/CV"]["value"] == {"resume": True}
    assert not ans.missing_required(rows)


def test_edit_after_approval_requires_new_approval(home, site):
    with connect(home.db) as conn:
        add_job(home, conn, site)
        approve(home, conn, "lever:acme:1")
        assert verify(home, conn, "lever:acme:1")[0]
        review.set_answers(home, conn, "lever:acme:1", {"phone": "555-0199"}, set())
        assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "queued"
        assert not verify(home, conn, "lever:acme:1")[0]


def test_changed_resume_blocks_submission(home, site):
    with connect(home.db) as conn:
        add_job(home, conn, site)
        approve(home, conn, "lever:acme:1")
        resume = home.resumes / "main.pdf"
        resume.write_bytes(resume.read_bytes() + b"\n% edited")
        assert runner.apply_one(home, conn, "lever:acme:1") == "queued"
        assert not Site.received  # nothing was sent


def test_submit_and_record(home, site):
    with connect(home.db) as conn:
        add_job(home, conn, site)
        approve(home, conn, "lever:acme:1")
        assert runner.apply_one(home, conn, "lever:acme:1") == "submitted"
        app = conn.execute("SELECT * FROM applications").fetchone()
    assert Path(app["screenshot_before"]).exists() and Path(app["screenshot_after"]).exists()
    assert json.loads(app["submitted_values"])["name"] == "Alex Rivera"
    sent = Site.received[0][1]
    assert b"Alex Rivera" in sent and b"%PDF" in sent  # the resume was attached


def test_dry_run_sends_nothing(home, site):
    s = config.load_settings(home)
    s["submit_mode"] = "fill_only"
    config.save_settings(s, home)
    with connect(home.db) as conn:
        add_job(home, conn, site)
        approve(home, conn, "lever:acme:1")
        assert runner.apply_one(home, conn, "lever:acme:1") == "filled"
    assert not Site.received


def test_emailed_code_is_entered_from_the_phone_page(home, site, monkeypatch):
    Site.ask_code = True
    monkeypatch.setattr(runner, "CODE_WAIT_SECONDS", 20)
    monkeypatch.setattr("applypeace.apply.runner.notify", lambda *a, **k: True)
    # Lever has no code step; reuse the Greenhouse code entry on this simple page.
    from applypeace.apply import greenhouse, lever

    monkeypatch.setattr(lever, "enter_code", greenhouse.enter_code)

    def user_types_code():
        import time

        with connect(home.db) as c2:
            for _ in range(100):
                if c2.execute("SELECT status FROM jobs").fetchone()[0] == "needs_code":
                    review.submit_code(c2, "lever:acme:1", "123456")
                    return
                time.sleep(0.2)

    with connect(home.db) as conn:
        add_job(home, conn, site)
        approve(home, conn, "lever:acme:1")
        threading.Thread(target=user_types_code, daemon=True).start()
        assert runner.apply_one(home, conn, "lever:acme:1") == "submitted"
    assert [path for path, _ in Site.received] == ["/submit", "/code"]


def test_missing_answer_hands_off(home, site, monkeypatch):
    monkeypatch.setattr("applypeace.apply.runner.notify", lambda *a, **k: True)
    with connect(home.db) as conn:
        add_job(home, conn, site)
        approve(home, conn, "lever:acme:1")
        # The form changed after approval: a new required field we have no answer for.
        conn.execute("INSERT INTO answers (job_id, field, label, kind, required, options, value, source, position, section)"
                     " VALUES ('lever:acme:1','extra','New required question','text',1,'[]','null','none',99,'custom')")
        conn.execute("UPDATE approvals SET answers_sha=?", (__import__("applypeace.approval", fromlist=["x"]).answers_sha(ans.load(conn, "lever:acme:1")),))
        conn.commit()
        assert runner.apply_one(home, conn, "lever:acme:1") == "needs_you"
        assert "New required question" in conn.execute("SELECT detail FROM applications").fetchone()[0]
    assert not Site.received
