"""The AI search plan and title screener (with the AI faked)."""

import pytest

from applypeace import config, llm, profile, search
from applypeace.db import connect, now
from applypeace.rank.filters import apply_filters

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPEACE_HOME", str(tmp_path / "data"))
    p = config.paths()
    s = config.load_settings(p)
    s["llm"]["consent"], s["targets"]["titles"] = True, ["Data Scientist", "Machine Learning"]
    config.save_settings(s, p)
    monkeypatch.setattr(llm, "available", lambda p: True)
    return p


def add_jobs(p, *titles):
    with connect(p.db) as conn:
        for i, title in enumerate(titles):
            conn.execute("INSERT INTO jobs (id, company_key, ats, board, ats_job_id, title, country, url, first_seen, last_seen)"
                         " VALUES (?,?,?,?,?,?,'US','u',?,?)", (f"x:a:{i}", "acme", "x", "a", str(i), title, now(), now()))
        conn.commit()


def test_screener_asks_once_per_title_and_overrides_word_matching(home, monkeypatch):
    p = home
    add_jobs(p, "Scientist, Machine Learning", "Data Scientist", "Sales Manager", "Data Scientist", "Office Assistant")
    calls = []

    def fake(p, system, prompt, schema, model=None, **k):
        calls.append((model, prompt))
        listing = [line.split(". ", 1)[1] for line in prompt.splitlines() if line[:1].isdigit()]
        return {"fit": [i for i, t in enumerate(listing, 1) if "scientist" in t]}

    monkeypatch.setattr(llm, "ask_json", fake)
    s = config.load_settings(p)
    with connect(p.db) as conn:
        assert search.screen(p, conn, [r[0] for r in conn.execute("SELECT title FROM jobs")]) == 4  # unique titles
        assert search.screen(p, conn, ["Data Scientist"]) == 0  # already answered: no second request
        apply_filters(conn, s, {"acme"}, {})
        status = dict(conn.execute("SELECT title, status FROM jobs").fetchall())
    assert len(calls) == 1 and calls[0][0] == "haiku"
    assert status["Scientist, Machine Learning"] == "candidate"  # word order differs, but the AI said yes
    assert status["Sales Manager"] == "filtered"


def test_changing_roles_screens_again(home, monkeypatch):
    p = home
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"fit": [1]})
    with connect(p.db) as conn:
        assert search.screen(p, conn, ["Statistician"]) == 1
        s = config.load_settings(p)
        s["targets"]["titles"].append("Statistician")
        config.save_settings(s, p)
        assert search.screen(p, conn, ["Statistician"]) == 1


def test_without_ai_titles_use_word_matching(home, monkeypatch):
    p = home
    monkeypatch.setattr(llm, "available", lambda p: False)
    add_jobs(p, "Senior Data Scientist", "Scientist, Data")
    with connect(p.db) as conn:
        assert search.screen(p, conn, ["Senior Data Scientist"]) == 0
        apply_filters(conn, config.load_settings(p), {"acme"}, {})
        status = dict(conn.execute("SELECT title, status FROM jobs").fetchall())
    assert status == {"Senior Data Scientist": "candidate", "Scientist, Data": "filtered"}  # words out of order


def test_search_plan_is_made_once_per_roles(home, monkeypatch):
    p = home
    profile.add_resume(p, FIXTURES / "sample_resume.pdf", "Main")
    calls = []
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: calls.append(1) or {
        "summary": "Math PhD in data science", "search_words": ["data scientist", " machine learning ", ""]})
    assert search.plan(p, log=lambda *a: None)["search_words"] == ["data scientist", "machine learning"]
    search.plan(p, log=lambda *a: None)
    assert len(calls) == 1


def test_workday_keeps_loose_matches_for_the_screener():
    from applypeace.sources.workday import loosely_matches

    assert loosely_matches("Scientist, Machine Learning", ["Data Scientist"], ["machine learning"])
    assert not loosely_matches("Machine Operator", ["Data Scientist"], ["machine learning"])
