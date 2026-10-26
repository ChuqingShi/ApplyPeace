"""Keep the data home under git so every profile, answer and resume change is versioned.

The data home is a local repository only. It is never pushed unless the user
separately approves a private backup remote.
"""

from __future__ import annotations

import subprocess

from .config import Paths

GITIGNORE = "applypeace.db\napplypeace.db-*\n*.tmp\n.web_token\nartifacts/\n.*.lock\n*.log\n"


def _git(p: Paths, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(p.home), *args], capture_output=True, text=True)


def ensure_repo(p: Paths) -> None:
    p.home.mkdir(parents=True, exist_ok=True)
    if not (p.home / ".git").exists():
        _git(p, "init", "-q", "-b", "main")
        _git(p, "config", "user.name", "ApplyPeace")
        _git(p, "config", "user.email", "applypeace@localhost")
    ignore = p.home / ".gitignore"
    if not ignore.exists():
        ignore.write_text(GITIGNORE)


def commit(p: Paths, message: str) -> bool:
    """Commit any changes in the data home. Returns True if something was committed."""
    ensure_repo(p)
    _git(p, "add", "-A")
    if _git(p, "diff", "--cached", "--quiet").returncode == 0:
        return False
    return _git(p, "commit", "-q", "-m", message).returncode == 0
