"""Phone notifications through the Openbase app, with a printed fallback."""

from __future__ import annotations

import shutil
import subprocess


def notify(message: str, open_url: str | None = None, label: str = "ApplyPeace") -> bool:
    """Show a short message on the phone (as a notification if the app is in the background).

    Returns True if Openbase accepted it. Message text should not contain personal details.
    """
    if shutil.which("openbase-coder"):
        cmd = ["openbase-coder", "user", "phone", "show-text", "--text-stdin", "--label", label]
        if open_url:
            cmd += ["--open", open_url]
        try:
            proc = subprocess.run(cmd, input=message, capture_output=True, text=True, timeout=60)
            if proc.returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
    print(f"[notify] {message}" + (f" -> {open_url}" if open_url else ""), flush=True)
    return False
