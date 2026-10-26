"""The private phone pages: setup wizard, profile, review and applications.

Served on 127.0.0.1 and reached from the phone only through a private Openbase
VPN publication. Every form carries a per-install token.
"""

from __future__ import annotations

import secrets
import tempfile
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import config, history, profile
from ..companies import registry as reg
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
    def home(request: Request):
        missing = profile.completeness(p)
        if missing:
            return go("/setup")
        return render(request, "home.html", missing=missing)

    # ------------------------------------------------------------ setup: choices

    @app.get("/setup", response_class=HTMLResponse)
    def setup_start(request: Request):
        return render(request, "setup_choices.html", step="choices", missing=profile.completeness(p))

    @app.post("/setup/choices")
    async def setup_choices(request: Request):
        form = await check(request)
        settings = config.load_settings(p)
        settings["llm"]["consent"] = form.get("llm_consent") == "yes"
        settings["submit_mode"] = "auto" if form.get("submit_mode") == "auto" else "fill_only"
        settings["h1b_filter"] = form.get("h1b_filter") == "yes"
        settings["backup_requested"] = form.get("backup") == "yes"
        config.save_settings(settings, p)
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
        config.save_yaml(p.home / "profile.draft.yml", draft)
        history.commit(p, f"Setup: add resume {entry['key']}")
        return go("/setup/profile")

    # ------------------------------------------------------------ setup: profile details

    @app.get("/setup/profile", response_class=HTMLResponse)
    def setup_profile(request: Request):
        current = profile.load_profile(p)
        draft = config.load_yaml(p.home / "profile.draft.yml", {})
        merged = {**current, **{k: v for k, v in draft.items() if v and not current.get(k)}}
        return render(request, "setup_profile.html", step="profile", prof=merged, read_by=draft.get("_read_by"))

    @app.post("/setup/profile")
    async def setup_profile_save(request: Request):
        form = await check(request)
        prof = profile.load_profile(p)
        draft = config.load_yaml(p.home / "profile.draft.yml", {})
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
        for field in ("work_authorized_us", "needs_sponsorship_now", "needs_sponsorship_future", "willing_to_relocate"):
            ans[field] = _bool(form.get(field))
        for field in ("visa_status", "relocation_notes", "salary_expectation", "earliest_start_date"):
            ans[field] = form.get(field, "").strip()
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
        draft = config.load_yaml(p.home / "profile.draft.yml", {})
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
        settings["job_location"] = form.get("job_location", "us")
        settings["lists"] = {key: form.get(f"list_{key}") == "on" for key in config.LISTS}
        for field, low, high in (("batch_size", 1, 50), ("daily_cap", 1, 50)):
            try:
                settings[field] = max(low, min(high, int(form.get(field, settings[field]))))
            except ValueError:
                pass
        settings["notify_time"] = form.get("notify_time", settings["notify_time"])
        config.save_settings(settings, p)
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

    return app
