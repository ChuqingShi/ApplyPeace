"""Paths and user settings.

All personal data lives in the data home (APPLYPEACE_HOME), never in the code
repository. Machine-local runtime files (browser, libraries) live in the
runtime directory (APPLYPEACE_RUNTIME).
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path

import yaml


def _projects_dir() -> Path:
    return Path(os.environ.get("OPENBASE_CODER_PROJECTS_DIR", Path.home()))


def data_home() -> Path:
    return Path(os.environ.get("APPLYPEACE_HOME", _projects_dir() / "ApplyPeace-data"))


def runtime_dir() -> Path:
    return Path(os.environ.get("APPLYPEACE_RUNTIME", _projects_dir() / ".applypeace-runtime"))


@dataclass(frozen=True)
class Paths:
    """Layout of the private data folder (also the private backup repo).

    personal/      your profile, answers, settings, preferences and resume PDFs
    applications/  readable history of every application, with per-job records
    companies/     the company registry and any lists you imported
    plan/          a copy of the ApplyPeace plan, refreshed on every backup
    applypeace.db  the working database (not backed up; applications/ is its readable export)
    """

    home: Path

    @property
    def personal(self) -> Path:
        return self.home / "personal"

    @property
    def settings(self) -> Path:
        return self.personal / "settings.yml"

    @property
    def profile(self) -> Path:
        return self.personal / "profile.yml"

    @property
    def profile_draft(self) -> Path:
        return self.personal / "profile.draft.yml"

    @property
    def answers(self) -> Path:
        return self.personal / "answers.yml"

    @property
    def resumes(self) -> Path:
        return self.personal / "resumes"

    @property
    def preferences(self) -> Path:
        return self.personal / "preferences.md"

    @property
    def companies_dir(self) -> Path:
        return self.home / "companies"

    @property
    def companies(self) -> Path:
        return self.companies_dir / "companies.yml"

    @property
    def lists_dir(self) -> Path:
        return self.companies_dir / "lists"

    @property
    def applications(self) -> Path:
        return self.home / "applications"

    @property
    def artifacts(self) -> Path:
        return self.applications / "records"

    @property
    def plan(self) -> Path:
        return self.home / "plan" / "PLAN.md"

    @property
    def db(self) -> Path:
        return self.home / "applypeace.db"


# Files that lived at the top of the data folder before it was split into folders.
_OLD_LAYOUT = {
    "settings.yml": "personal/settings.yml",
    "profile.yml": "personal/profile.yml",
    "profile.draft.yml": "personal/profile.draft.yml",
    "answers.yml": "personal/answers.yml",
    "preferences.md": "personal/preferences.md",
    "resumes": "personal/resumes",
    "companies.yml": "companies/companies.yml",
    "lists": "companies/lists",
    "artifacts": "applications/records",
}


def migrate_layout(p: Paths) -> list[str]:
    """Move files from the old flat layout into the folders. Safe to call every time."""
    moved = []
    for old, new in _OLD_LAYOUT.items():
        src, dest = p.home / old, p.home / new
        if src.exists() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
            moved.append(f"{old} -> {new}")
    return moved


def paths() -> Paths:
    p = Paths(data_home())
    if p.home.exists():
        migrate_layout(p)
    return p


# Company lists a user can switch on. A job qualifies if its employer is on any enabled list.
LISTS: dict[str, str] = {
    "fortune500": "Fortune 500",
    "sp500": "S&P 500",
    "nasdaq100": "Nasdaq-100",
    "forbes_global2000_us": "Forbes Global 2000 (US HQ)",
    "forbes_global2000_intl": "Forbes Global 2000 (non-US HQ)",
    "forbes_ai50": "Forbes AI 50",
    "custom": "My companies",
}

DEFAULT_SETTINGS: dict = {
    "lists": {key: True for key in LISTS} | {"forbes_global2000_intl": False},
    "job_location": "us",  # us | non_us | both
    "h1b_filter": True,
    "batch_size": 10,
    "daily_cap": 15,
    "notify_time": "08:00",
    "timezone": "America/Los_Angeles",
    "scan_interval_hours": 8,
    "scan_times": [],  # custom daily scan times ("HH:MM"); empty = derived from notify_time + interval
    "max_posting_age_days": 30,
    "llm": {"consent": False, "judge_daily_limit": 30},
    "submit_mode": "fill_only",  # fill_only (dry run, nothing sent) | auto
    "paused": False,
    "apply_gap_seconds": 150,
    "targets": {"titles": [], "exclude_titles": [], "locations": [], "remote_ok": True, "seniority": "any"},
    "min_fit_score": 3,  # AI fit score (1-5) a job needs to enter your batch
    "setup_complete": False,
}


def _merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_yaml(path: Path, default=None):
    if not path.exists():
        return copy.deepcopy(default)
    with path.open() as f:
        data = yaml.safe_load(f)
    return copy.deepcopy(default) if data is None else data


def save_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


def load_settings(p: Paths | None = None) -> dict:
    p = p or paths()
    return _merge(DEFAULT_SETTINGS, load_yaml(p.settings, {}))


def save_settings(settings: dict, p: Paths | None = None) -> None:
    save_yaml((p or paths()).settings, settings)
