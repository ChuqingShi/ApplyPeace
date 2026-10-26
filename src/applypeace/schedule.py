"""Install ApplyPeace's scheduled steps as Openbase command routines, and keep the phone page running."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from .config import Paths, load_settings

PROJECT_DIR = Path(__file__).resolve().parents[2]
PREFIX = "applypeace-"


def _ap(args: str, p: Paths) -> str:
    env = f"APPLYPEACE_HOME={shlex.quote(str(p.home))}"
    return f"cd {shlex.quote(str(PROJECT_DIR))} && {env} uv run ap {args} >> {shlex.quote(str(p.home / 'ap.log'))} 2>&1"


SCAN_LEAD_MINUTES = 30  # scan this long before the notification, so the batch is fresh


def scan_times(notify_time: str, interval_hours: int) -> list[str]:
    """Daily scan times anchored to the notification: e.g. 07:00 / 8 h -> 06:30, 14:30, 22:30."""
    hours, minutes = (int(x) for x in notify_time.split(":"))
    first = (hours * 60 + minutes - SCAN_LEAD_MINUTES) % (24 * 60)
    step = max(1, int(interval_hours)) * 60
    return sorted(f"{(first + i * step) % 1440 // 60:02d}:{(first + i * step) % 60:02d}"
                  for i in range(max(1, 1440 // step)))


def routines(p: Paths, port: int = 8765) -> list[dict]:
    settings = load_settings(p)
    tz = settings.get("timezone", "America/Los_Angeles")
    scans = [
        {"name": f"{PREFIX}cycle-{i}", "command": _ap("cycle", p), "type": "daily", "time": t,
         "timezone": tz, "timeout": 3600}
        for i, t in enumerate(scan_times(settings["notify_time"], settings["scan_interval_hours"]), 1)
    ]
    return scans + [
        {"name": PREFIX + "digest", "command": _ap("digest", p), "type": "daily", "time": settings["notify_time"],
         "timezone": tz, "timeout": 300},
        {"name": PREFIX + "apply", "command": _ap("apply --max 1", p), "type": "interval", "interval": 600,
         "timeout": 1800},  # one application per tick keeps them spaced; 30 min covers a code wait
        {"name": PREFIX + "web", "command": _ap(f"serve --background --port {port}", p), "type": "interval",
         "interval": 300, "timeout": 60},  # restarts the phone page if the workspace restarted
    ]


def install(p: Paths, port: int = 8765) -> list[str]:
    out = remove(keep={r["name"] for r in routines(p, port)})  # drop routines from an older schedule
    for r in routines(p, port):
        cmd = ["openbase-coder", "routines", "create", r["name"], "--kind", "command", "--command", r["command"],
               "--command-timeout-seconds", str(r["timeout"]), "--cwd", str(PROJECT_DIR)]
        if r["type"] == "interval":
            cmd += ["--schedule-type", "interval", "--interval-seconds", str(r["interval"])]
        else:
            cmd += ["--schedule-type", "daily", "--time", r["time"], "--timezone", r["timezone"]]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out.append(f"{r['name']}: {'ok' if proc.returncode == 0 else proc.stderr.strip()[:200]}")
    return out


def installed_names() -> list[str]:
    import json

    proc = subprocess.run(["openbase-coder", "routines", "list"], capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    items = data.get("routines", data) if isinstance(data, dict) else data
    return [r["name"] for r in items if isinstance(r, dict) and r.get("name", "").startswith(PREFIX)]


def remove(keep: set[str] = frozenset()) -> list[str]:
    out = []
    for name in installed_names():
        if name in keep:
            continue
        proc = subprocess.run(["openbase-coder", "routines", "delete", name], capture_output=True, text=True)
        out.append(f"{name}: {'removed' if proc.returncode == 0 else proc.stderr.strip()[:120]}")
    return out


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def serve_background(p: Paths, port: int) -> str:
    if port_open(port):
        return f"Phone page already running on 127.0.0.1:{port}."
    exe = shutil.which("ap") or sys.argv[0]
    log = (p.home / "web.log").open("a")
    subprocess.Popen([exe, "serve", "--port", str(port)], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     start_new_session=True, env={**os.environ, "APPLYPEACE_HOME": str(p.home)})
    return f"Started the phone page on 127.0.0.1:{port}."
