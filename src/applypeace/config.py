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
    home: Path

    @property
    def settings(self) -> Path:
        return self.home / "settings.yml"

    @property
    def profile(self) -> Path:
        return self.home / "profile.yml"

    @property
    def answers(self) -> Path:
        return self.home / "answers.yml"

    @property
    def companies(self) -> Path:
        return self.home / "companies.yml"

    @property
    def resumes(self) -> Path:
        return self.home / "resumes"

    @property
    def lists_dir(self) -> Path:
        return self.home / "lists"

    @property
    def preferences(self) -> Path:
        return self.home / "preferences.md"

    @property
    def db(self) -> Path:
        return self.home / "applypeace.db"

    @property
    def artifacts(self) -> Path:
        return self.home / "artifacts"


def paths() -> Paths:
    return Paths(data_home())


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
    "max_posting_age_days": 30,
    "llm": {"consent": False, "judge_daily_limit": 30},
    "submit_mode": "fill_only",  # fill_only | auto
    "targets": {"titles": [], "exclude_titles": [], "locations": [], "remote_ok": True},
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
