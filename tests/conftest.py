"""Tests must never touch the real Openbase routines or phone."""

import pytest


@pytest.fixture(autouse=True)
def no_real_side_effects(monkeypatch):
    import subprocess

    real_run = subprocess.run

    def guarded(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "openbase-coder":
            raise AssertionError(f"test tried to run {cmd[:3]}")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)
    monkeypatch.setattr("applypeace.schedule.installed_home", lambda: None)
    monkeypatch.setattr("applypeace.notify.notify", lambda *a, **k: False)
