"""Your profile, standard answers and resume variants (all in the data home)."""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import date
from pathlib import Path

from .config import Paths, load_settings, load_yaml, save_yaml

PROFILE_TEMPLATE: dict = {
    "first_name": "",
    "last_name": "",
    "preferred_name": "",
    "email": "",
    "phone": "",
    "location": {"city": "", "state": "", "country": ""},
    "links": {"linkedin": "", "github": "", "portfolio": "", "other": ""},
    "summary": "",
    "education": [],   # {school, degree, field, start, end}
    "experience": [],  # {company, title, start, end, highlights: []}
    "skills": [],
}

ANSWERS_TEMPLATE: dict = {
    "work_authorized_us": None,       # True / False
    "needs_sponsorship_now": None,    # True / False
    "needs_sponsorship_future": None, # True / False
    "visa_status": "",                # e.g. "F-1 OPT", "H-1B"
    "willing_to_relocate": None,
    "relocation_notes": "",
    "salary_expectation": "",
    "earliest_start_date": "",
    "over_18": None,
    "languages": ["English"],
    "auto_acknowledge": False,         # tick single "I agree / I have read" boxes (opt-in at setup)
    "eeo": {"gender": "decline", "race": "decline", "hispanic_latino": "decline",
            "veteran": "decline", "disability": "decline"},
    "saved": [],  # defaults you saved during review: {question, answer}
}


def load_profile(p: Paths) -> dict:
    return load_yaml(p.profile, PROFILE_TEMPLATE)


def load_answers(p: Paths) -> dict:
    return load_yaml(p.answers, ANSWERS_TEMPLATE)


def resumes_index(p: Paths) -> list[dict]:
    return load_yaml(p.resumes / "resumes.yml", {"resumes": []})["resumes"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_resume(p: Paths, source: Path, label: str) -> dict:
    """Copy a resume PDF into the data home under a short key and index it."""
    key = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "resume"
    p.resumes.mkdir(parents=True, exist_ok=True)
    dest = p.resumes / f"{key}.pdf"
    shutil.copyfile(source, dest)
    entries = [r for r in resumes_index(p) if r["key"] != key]
    entry = {"key": key, "label": label, "file": dest.name, "sha256": sha256(dest), "added": str(date.today())}
    entries.append(entry)
    save_yaml(p.resumes / "resumes.yml", {"resumes": entries})
    return entry


def resume_text(p: Paths, key: str) -> str:
    from pypdf import PdfReader

    entry = next(r for r in resumes_index(p) if r["key"] == key)
    reader = PdfReader(p.resumes / entry["file"])
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def completeness(p: Paths) -> list[str]:
    """What setup still needs. Empty means ApplyPeace may scan, rank and apply."""
    missing = []
    prof, ans, settings = load_profile(p), load_answers(p), load_settings(p)
    for field in ("first_name", "last_name", "email", "phone"):
        if not prof.get(field):
            missing.append(f"profile: {field.replace('_', ' ')}")
    if not resumes_index(p):
        missing.append("at least one resume PDF")
    for field in ("work_authorized_us", "needs_sponsorship_now", "needs_sponsorship_future"):
        if ans.get(field) is None:
            missing.append(f"answer: {field.replace('_', ' ')}")
    if not settings["targets"]["titles"]:
        missing.append("targets: at least one job title or keyword")
    if not settings.get("setup_complete"):
        missing.append("finish setup (review and confirm)")
    return missing
