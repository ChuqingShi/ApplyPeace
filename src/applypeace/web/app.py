"""The private phone pages: setup wizard, profile, review and applications.

Served on 127.0.0.1 and reached from the phone only through a private Openbase
VPN publication. Every form carries a per-install token.
"""

from __future__ import annotations

import json
import secrets
import tempfile
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import answers as ans
from .. import config, history, profile, review
from ..approval import verify
from ..companies import registry as reg
from ..db import connect
from ..rank import h1b_note
from ..resume_reader import read_resume

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
MAX_UPLOAD = 10 * 1024 * 1024

SETUP_STEPS = [
    ("choices", "Choices"),
    ("resume", "Resume"),
    ("profile", "Details"),
    ("answers", "Answers"),
    ("targets", "Targets"),
    ("finish", "Finish"),
]


def _token(p: config.Paths) -> str:
    f = p.home / ".web_token"
    if not f.exists():
        p.home.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_urlsafe(32))
        f.chmod(0o600)
    return f.read_text().strip()


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.replace(",", "\n").splitlines() if line.strip()]


def _bool(value: str | None) -> bool | None:
    return {"yes": True, "no": False}.get(value or "")


def create_app(p: config.Paths | None = None) -> FastAPI:
    p = p or config.paths()
    app = FastAPI(title="ApplyPeace", docs_url=None, redoc_url=None, openapi_url=None)

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.setdefault("steps", SETUP_STEPS)
        ctx.setdefault("settings", config.load_settings(p))
        return TEMPLATES.TemplateResponse(request, name, {"token": _token(p), **ctx})

    async def check(request: Request) -> dict:
        form = dict(await request.form())
        if not secrets.compare_digest(str(form.get("_token", "")), _token(p)):
            raise HTTPException(403, "Form expired; reload the page.")
        return form

    def go(path: str) -> RedirectResponse:
        return RedirectResponse(path, status_code=303)

    # ------------------------------------------------------------ home

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, msg: str = ""):
        missing = profile.completeness(p)
        if missing:
            return go("/setup")
        with connect(p.db) as conn:
            return render(request, "home.html", msg=msg, **home_context(p, conn))

    # ------------------------------------------------------------ setup: choices

    @app.get("/setup", response_class=HTMLResponse)
    def setup_start(request: Request):
        return render(request, "setup_choices.html", step="choices", missing=profile.completeness(p),
                      ans=profile.load_answers(p))

    @app.post("/setup/choices")
    async def setup_choices(request: Request):
        form = await check(request)
        settings = config.load_settings(p)
        settings["llm"]["consent"] = form.get("llm_consent") == "yes"
        settings["submit_mode"] = "auto" if form.get("submit_mode") == "auto" else "fill_only"
        settings["h1b_filter"] = form.get("h1b_filter") == "yes"
        settings["backup_requested"] = form.get("backup") == "yes"
        config.save_settings(settings, p)
        answers_file = profile.load_answers(p)
        answers_file["auto_acknowledge"] = form.get("auto_acknowledge") == "yes"
        config.save_yaml(p.answers, answers_file)
        history.commit(p, "Setup: choices")
        return go("/setup/resume")

    # ------------------------------------------------------------ setup: resume

    @app.get("/setup/resume", response_class=HTMLResponse)
    def setup_resume(request: Request):
        return render(request, "setup_resume.html", step="resume", resumes=profile.resumes_index(p))

    @app.post("/setup/resume")
    async def setup_resume_upload(request: Request, file: UploadFile, label: str = Form(...), form_token: str = Form("", alias="_token")):
        if not secrets.compare_digest(form_token, _token(p)):
            raise HTTPException(403, "Form expired; reload the page.")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD or not data.startswith(b"%PDF"):
            raise HTTPException(400, "Please upload a PDF under 10 MB.")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(data)
            tmp.flush()
            entry = profile.add_resume(p, Path(tmp.name), label.strip() or "Resume")
        draft, how = read_resume(p, profile.resume_text(p, entry["key"]))
        draft["_read_by"] = how
        config.save_yaml(p.profile_draft, draft)
        history.commit(p, f"Setup: add resume {entry['key']}")
        return go("/setup/profile")

    # ------------------------------------------------------------ setup: profile details

    @app.get("/setup/profile", response_class=HTMLResponse)
    def setup_profile(request: Request):
        current = profile.load_profile(p)
        draft = config.load_yaml(p.profile_draft, {})
        merged = {**current, **{k: v for k, v in draft.items() if v and not current.get(k)}}
        return render(request, "setup_profile.html", step="profile", prof=merged, read_by=draft.get("_read_by"))

    @app.post("/setup/profile")
    async def setup_profile_save(request: Request):
        form = await check(request)
        prof = profile.load_profile(p)
        draft = config.load_yaml(p.profile_draft, {})
        for field in ("first_name", "last_name", "preferred_name", "email", "phone", "summary"):
            prof[field] = form.get(field, "").strip()
        prof["location"] = {k: form.get(f"location_{k}", "").strip() for k in ("city", "state", "country")}
        prof["links"] = {k: form.get(f"link_{k}", "").strip() for k in ("linkedin", "github", "portfolio", "other")}
        prof["skills"] = _lines(form.get("skills", ""))
        # Education and experience come from the confirmed resume draft; edit them on the Profile page later.
        for field in ("education", "experience"):
            if draft.get(field) and not prof.get(field):
                prof[field] = draft[field]
        config.save_yaml(p.profile, prof)
        history.commit(p, "Setup: confirm profile details")
        return go("/setup/answers")

    # ------------------------------------------------------------ setup: standard answers

    @app.get("/setup/answers", response_class=HTMLResponse)
    def setup_answers(request: Request):
        return render(request, "setup_answers.html", step="answers", ans=profile.load_answers(p))

    @app.post("/setup/answers")
    async def setup_answers_save(request: Request):
        form = await check(request)
        ans = profile.load_answers(p)
        for field in ("work_authorized_us", "needs_sponsorship_now", "needs_sponsorship_future", "willing_to_relocate", "over_18"):
            ans[field] = _bool(form.get(field))
        for field in ("visa_status", "relocation_notes", "salary_expectation", "earliest_start_date"):
            ans[field] = form.get(field, "").strip()
        ans["languages"] = _lines(form.get("languages", "")) or ["English"]
        ans["eeo"] = {k: form.get(f"eeo_{k}", "decline") for k in ans["eeo"]}
        config.save_yaml(p.answers, ans)
        history.commit(p, "Setup: standard answers")
        return go("/setup/targets")

    # ------------------------------------------------------------ setup: targets and lists

    @app.get("/setup/targets", response_class=HTMLResponse)
    def setup_targets(request: Request):
        registry = reg.load(p)
        counts = {}
        for key in config.LISTS:
            members = [c for c in registry.values() if key in c.get("lists", [])]
            ready = [c for c in members if c.get("verified") and c.get("ats") in reg.SUPPORTED_ATS]
            counts[key] = (len(members), len(ready))
        draft = config.load_yaml(p.profile_draft, {})
        return render(request, "setup_targets.html", step="targets", lists=config.LISTS, counts=counts,
                      suggested=draft.get("suggested_titles", []))

    @app.post("/setup/targets")
    async def setup_targets_save(request: Request):
        form = await check(request)
        settings = config.load_settings(p)
        settings["targets"]["titles"] = _lines(form.get("titles", ""))
        settings["targets"]["exclude_titles"] = _lines(form.get("exclude_titles", ""))
        settings["targets"]["locations"] = _lines(form.get("locations", ""))
        settings["targets"]["remote_ok"] = form.get("remote_ok") == "yes"
        settings["targets"]["seniority"] = "early" if form.get("seniority") == "early" else "any"
        try:
            settings["min_fit_score"] = max(1.0, min(5.0, float(form.get("min_fit_score", settings["min_fit_score"]))))
        except ValueError:
            pass
        settings["job_location"] = form.get("job_location", "us")
        settings["lists"] = {key: form.get(f"list_{key}") == "on" for key in config.LISTS}
        for field, low, high in (("batch_size", 1, 50), ("daily_cap", 1, 50)):
            try:
                settings[field] = max(low, min(high, int(form.get(field, settings[field]))))
            except ValueError:
                pass
        old_time = settings["notify_time"]
        settings["notify_time"] = form.get("notify_time", settings["notify_time"])
        config.save_settings(settings, p)
        if settings["notify_time"] != old_time:
            from .. import schedule

            schedule.resync_if_mine(p)  # keep scans aligned with the new notification time
        history.commit(p, "Setup: targets and company lists")
        return go("/setup/finish")

    # ------------------------------------------------------------ setup: finish

    @app.get("/setup/finish", response_class=HTMLResponse)
    def setup_finish(request: Request):
        missing = [m for m in profile.completeness(p) if not m.startswith("finish setup")]
        return render(request, "setup_finish.html", step="finish", missing=missing,
                      prof=profile.load_profile(p), ans=profile.load_answers(p), resumes=profile.resumes_index(p))

    @app.post("/setup/finish")
    async def setup_finish_save(request: Request):
        await check(request)
        missing = [m for m in profile.completeness(p) if not m.startswith("finish setup")]
        if missing:
            return go("/setup/finish")
        settings = config.load_settings(p)
        settings["setup_complete"] = True
        config.save_settings(settings, p)
        history.commit(p, "Setup complete")
        return go("/")

    # ------------------------------------------------------------ review

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str, msg: str = ""):
        with connect(p.db) as conn:
            ctx = job_context(p, conn, job_id)
        return render(request, "job.html", msg=msg, **ctx)

    @app.post("/jobs/{job_id}/save")
    async def job_save(request: Request, job_id: str):
        form = await request.form()
        if not secrets.compare_digest(str(form.get("_token", "")), _token(p)):
            raise HTTPException(403, "Form expired; reload the page.")
        with connect(p.db) as conn:
            rows = ans.load(conn, job_id)
            values = {r["field"]: parse_value(r, form) for r in rows if r["kind"] != "file"}
            defaults = {k.removeprefix("default__") for k in form.keys() if k.startswith("default__")}
            review.set_answers(p, conn, job_id, values, defaults)
            if form.get("resume_key"):
                current = conn.execute("SELECT resume_key FROM jobs WHERE id=?", (job_id,)).fetchone()["resume_key"]
                if form["resume_key"] != current:
                    review.set_resume(conn, job_id, str(form["resume_key"]))
            action = form.get("action", "save")
            if action == "approve":
                try:
                    from ..approval import approve

                    approve(p, conn, job_id)
                except ValueError as exc:
                    return go(f"/jobs/{job_id}?msg={exc}")
                if defaults:
                    history.commit(p, "Saved default answers")
                return go("/?msg=Approved")
        if defaults:
            history.commit(p, "Saved default answers")
        return go(f"/jobs/{job_id}?msg=Saved")

    @app.post("/jobs/{job_id}/redraft")
    async def job_redraft(request: Request, job_id: str):
        form = await check(request)
        from ..llm import LLMUnavailable

        with connect(p.db) as conn:
            try:
                review.redraft(p, conn, job_id, form["field"], form.get("instruction", ""))
            except LLMUnavailable as exc:
                return go(f"/jobs/{job_id}?msg=Redraft unavailable: {exc}")
        return go(f"/jobs/{job_id}?msg=Redrafted#f-{form['field']}")

    @app.post("/jobs/{job_id}/skip")
    async def job_skip(request: Request, job_id: str):
        await check(request)
        with connect(p.db) as conn:
            review.skip(conn, job_id)
        return go("/?msg=Kept for later")

    @app.post("/jobs/{job_id}/reject")
    async def job_reject(request: Request, job_id: str):
        form = await check(request)
        with connect(p.db) as conn:
            review.reject(p, conn, job_id, form.get("reason", "other"), form.get("block") == "yes")
        return go("/?msg=Rejected")

    @app.post("/jobs/{job_id}/restore")
    async def job_restore(request: Request, job_id: str):
        await check(request)
        with connect(p.db) as conn:
            review.restore(conn, job_id)
            if not ans.load(conn, job_id):
                from ..prepare import prepare_job

                company = reg.load(p).get(conn.execute("SELECT company_key FROM jobs WHERE id=?", (job_id,)).fetchone()[0], {})
                prepare_job(p, conn, job_id, company.get("name", ""))
        return go(f"/jobs/{job_id}")

    @app.post("/jobs/{job_id}/code")
    async def job_code(request: Request, job_id: str):
        form = await check(request)
        with connect(p.db) as conn:
            review.submit_code(conn, job_id, form.get("code", ""))
        return go(f"/jobs/{job_id}?msg=Code sent; finishing your application")

    @app.post("/jobs/{job_id}/done")
    async def job_done(request: Request, job_id: str):
        form = await check(request)
        with connect(p.db) as conn:
            review.mark_done_by_hand(conn, job_id, form.get("submitted") == "yes")
        return go("/applications")

    def backup_soon(message: str) -> None:
        import threading

        from ..backup import backup

        threading.Thread(target=backup, args=(p, message), daemon=True).start()

    @app.post("/jobs/{job_id}/applied-myself")
    async def job_applied_myself(request: Request, job_id: str):
        form = await check(request)
        from ..selfapplied import mark_applied

        with connect(p.db) as conn:
            mark_applied(conn, job_id, form.get("applied_on") or None, form.get("resume_key") or None, form.get("note", ""))
        backup_soon("Applied by you")
        return go(f"/jobs/{job_id}?msg=Recorded: you applied yourself")

    @app.get("/applications/new", response_class=HTMLResponse)
    def application_new(request: Request):
        from datetime import date

        return render(request, "application_new.html", resumes=profile.resumes_index(p), today=date.today().isoformat())

    @app.post("/applications/new")
    async def application_new_save(request: Request):
        form = await check(request)
        if not form.get("company", "").strip() or not form.get("title", "").strip():
            return go("/applications/new")
        from ..selfapplied import add_external

        with connect(p.db) as conn:
            job_id = add_external(p, conn, form["company"], form["title"], form.get("url", "").strip(),
                                  form.get("applied_on") or None, form.get("resume_key") or None,
                                  form.get("note", ""), form.get("location", ""))
        backup_soon("Logged an application you made")
        return go(f"/jobs/{job_id}?msg=Added to your applications")

    @app.post("/jobs/{job_id}/outcome")
    async def job_outcome(request: Request, job_id: str):
        form = await check(request)
        with connect(p.db) as conn:
            review.set_outcome(conn, job_id, form.get("outcome", ""))
        return go(f"/jobs/{job_id}?msg=Updated")

    @app.post("/approve-all")
    async def approve_all(request: Request):
        await check(request)
        with connect(p.db) as conn:
            n = review.approve_ready(p, conn)
        return go(f"/?msg=Approved {n} jobs")

    @app.post("/pause")
    async def pause(request: Request):
        await check(request)
        settings = config.load_settings(p)
        settings["paused"] = not settings.get("paused")
        config.save_settings(settings, p)
        return go("/?msg=" + ("Paused" if settings["paused"] else "Resumed"))

    @app.get("/schedule", response_class=HTMLResponse)
    def schedule_page(request: Request, msg: str = ""):
        from .. import schedule

        settings = config.load_settings(p)
        times = settings.get("scan_times") or schedule.scan_times(settings["notify_time"], settings["scan_interval_hours"])
        return render(request, "schedule.html", msg=msg, scan_list=times, slots=schedule.TIME_SLOTS,
                      max_scans=schedule.MAX_SCANS, notify_tz=schedule.zones_of(settings)[0],
                      scan_tz=schedule.zones_of(settings)[1],
                      notify_zones=schedule.common_zones(schedule.zones_of(settings)[0]),
                      scan_zones=schedule.common_zones(schedule.zones_of(settings)[1]),
                      gaps=[1, 2, 3, 5, 10, 15, 30, 60])

    @app.post("/schedule")
    async def schedule_save(request: Request):
        form = await check(request)
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        from .. import schedule

        settings = config.load_settings(p)
        try:
            valid = {value for value, _ in schedule.TIME_SLOTS}
            notify = form.get("notify_time", "")
            count = int(form.get("scan_count", 0))
            times = sorted({form.get(f"scan_{i}", "") for i in range(1, count + 1)} - {""})
            if notify not in valid or not times or not set(times) <= valid or not 1 <= count <= schedule.MAX_SCANS:
                raise ValueError("Pick a notification time and at least one scan time")
            zones = {}
            for key in ("notify_timezone", "scan_timezone"):
                tz = form.get(key, "") or schedule.zones_of(settings)[key == "scan_timezone"]
                try:
                    ZoneInfo(tz)
                except (ZoneInfoNotFoundError, ValueError):
                    raise ValueError(f"'{tz}' isn't a time zone name like America/New_York")
                zones[key] = tz
            settings["notify_mode"] = "daily" if form.get("notify_mode") == "daily" else "after_scan"
            settings.update(notify_time=notify, scan_times=times, timezone=zones["notify_timezone"], **zones,
                            apply_gap_seconds=max(60, int(form.get("apply_gap_minutes", 2)) * 60))
        except ValueError as exc:
            return go(f"/schedule?msg={exc}")
        config.save_settings(settings, p)
        history.commit(p, "Settings: schedule")
        applied = schedule.resync_if_mine(p)
        note = f"{len(times)} scan{'s' if len(times) != 1 else ''} a day"
        return go(f"/schedule?msg=Saved: {note}" + (", live now" if applied else " (schedule not installed here)"))

    @app.get("/applications", response_class=HTMLResponse)
    def applications(request: Request):
        with connect(p.db) as conn:
            rows = conn.execute(
                "SELECT a.*, j.title, j.company_key, j.company_name, j.url FROM applications a JOIN jobs j ON j.id=a.job_id"
                " ORDER BY a.finished_at DESC"
            ).fetchall()
        names = {c["key"]: c["name"] for c in reg.load(p).values()}
        return render(request, "applications.html", apps=rows, names=names)

    @app.get("/resumes/{key}")
    def resume_pdf(key: str):
        from fastapi.responses import FileResponse

        entry = next((r for r in profile.resumes_index(p) if r["key"] == key), None)
        if not entry:
            raise HTTPException(404)
        return FileResponse(p.resumes / entry["file"], media_type="application/pdf")

    @app.get("/artifacts/{job_id}/{name}")
    def artifact(job_id: str, name: str):
        from fastapi.responses import FileResponse

        from ..apply.runner import artifact_dir

        if name not in ("filled.png", "confirmation.png", "job_description.txt", "answers.json"):
            raise HTTPException(404)
        path = artifact_dir(p, job_id) / name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path)

    return app


def parse_value(row: dict, form):
    """Read one question's value from the submitted form, in the stored shape."""
    key = f"f__{row['field']}"
    if row["kind"] == "multiselect":
        return [v for v in form.getlist(key) if v] or None
    raw = form.get(key)
    if raw in (None, ""):
        return None
    if row["kind"] == "boolean" and not row["options"]:
        return raw == "true"
    return str(raw)


def home_context(p: config.Paths, conn) -> dict:
    names = {c["key"]: c["name"] for c in reg.load(p).values()}
    resume_labels = {r["key"]: r["label"] for r in profile.resumes_index(p)}

    def cards(status_sql: str, order: str = "final_score DESC", limit: int = 50):
        out = []
        for j in conn.execute(f"SELECT * FROM jobs WHERE {status_sql} ORDER BY {order} LIMIT {limit}").fetchall():
            verdict = json.loads(j["judge"]) if j["judge"] else None
            rows = ans.load(conn, j["id"])
            out.append({"job": j, "company": names.get(j["company_key"], j["company_key"]), "judge": verdict,
                        "missing": len(ans.missing_required(rows)), "resume": resume_labels.get(j["resume_key"])})
        return out

    batch = sorted(cards("status='queued'"), key=lambda c: (c["missing"] == 0, -(c["job"]["final_score"] or 0)))
    return {
        "batch": batch,
        "needs_you": cards("status IN ('needs_you','needs_code')", "first_seen"),
        "approved": cards("status IN ('approved','applying')"),
        "later": cards("status='skipped'"),
        "ai_skipped": [c for c in cards("status='candidate' AND judge IS NOT NULL", "final_score DESC", 200)
                       if c["judge"]["recommendation"] == "skip"][:20],
        "today": conn.execute("SELECT COUNT(*) FROM applications WHERE status='submitted' AND substr(finished_at,1,10)=date('now')").fetchone()[0],
        "ready_count": sum(1 for c in batch if c["missing"] == 0),
    }


def job_context(p: config.Paths, conn, job_id: str) -> dict:
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        raise HTTPException(404, "Job not found")
    company = reg.load(p).get(job["company_key"], {})
    rows = ans.load(conn, job_id)
    app_row = conn.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
    approved = conn.execute("SELECT approved_at FROM approvals WHERE job_id=?", (job_id,)).fetchone()
    record = None
    try:
        from ..rank import h1b_records

        record = (h1b_records([company]) or {}).get(company.get("key")) if company else None
    except Exception:
        record = None
    return {
        "job": job,
        "company": job["company_name"] or company.get("name", job["company_key"]),
        "today": __import__("datetime").date.today().isoformat(),
        "judge": json.loads(job["judge"]) if job["judge"] else None,
        "rows": rows,
        "missing": {r["field"] for r in ans.missing_required(rows)},
        "resumes": profile.resumes_index(p),
        "current_resume": job["resume_key"] or (profile.resumes_index(p) or [{}])[0].get("key"),
        "application": app_row,
        "approved": approved,
        "lock_ok": verify(p, conn, job_id)[0] if approved else None,
        "h1b": h1b_note(record),
        "summary": (job["description"] or "")[:900],
        "reasons": review.REASON_TAGS,
        "is_selected": lambda row, value: str(value) in [str(v) for v in (row["value"] if isinstance(row["value"], list) else [row["value"]])],
    }
