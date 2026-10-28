"""Install ApplyPeace's scheduled steps as Openbase command routines, and keep the phone page running."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Paths, load_settings

PROJECT_DIR = Path(__file__).resolve().parents[2]
PREFIX = "applypeace-"


def _ap(args: str, p: Paths) -> str:
    env = f"APPLYPEACE_HOME={shlex.quote(str(p.home))}"
    return f"cd {shlex.quote(str(PROJECT_DIR))} && {env} uv run ap {args} >> {shlex.quote(str(p.home / 'ap.log'))} 2>&1"


MAX_SCANS = 8


def _label(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


# Every half hour of the day, as (value "HH:MM", label "7:30 AM").
TIME_SLOTS = [(f"{t // 60:02d}:{t % 60:02d}", _label(t)) for t in range(0, 1440, 30)]

COMMON_ZONES = [
    ("America/Los_Angeles", "Pacific (Los Angeles)"), ("America/Denver", "Mountain (Denver)"),
    ("America/Phoenix", "Arizona (Phoenix)"), ("America/Chicago", "Central (Chicago)"),
    ("America/New_York", "Eastern (New York)"), ("America/Anchorage", "Alaska"), ("Pacific/Honolulu", "Hawaii"),
    ("America/Toronto", "Toronto"), ("America/Vancouver", "Vancouver"), ("Europe/London", "London"),
    ("Europe/Berlin", "Berlin / Paris"), ("Asia/Kolkata", "India"), ("Asia/Shanghai", "China"),
    ("Asia/Singapore", "Singapore"), ("Asia/Tokyo", "Tokyo"), ("Australia/Sydney", "Sydney"),
]


def common_zones(current: str) -> list[tuple[str, str]]:
    """Common zones, plus the current one if it isn't in the list."""
    return COMMON_ZONES if any(z == current for z, _ in COMMON_ZONES) else [(current, current)] + COMMON_ZONES


def zones_of(settings: dict) -> tuple[str, str]:
    """(notification time zone, scan time zone); both fall back to the older single setting."""
    base = settings.get("timezone", "America/Los_Angeles")
    return settings.get("notify_timezone") or base, settings.get("scan_timezone") or base


SCAN_LEAD_MINUTES = 30  # scan this long before the notification, so the batch is fresh


def scan_times(notify_time: str, interval_hours: int) -> list[str]:
    """Daily scan times anchored to the notification: e.g. 07:00 / 8 h -> 06:30, 14:30, 22:30."""
    hours, minutes = (int(x) for x in notify_time.split(":"))
    first = (hours * 60 + minutes - SCAN_LEAD_MINUTES) % (24 * 60)
    step = max(1, int(interval_hours)) * 60
    return sorted(f"{(first + i * step) % 1440 // 60:02d}:{(first + i * step) % 60:02d}"
                  for i in range(max(1, 1440 // step)))


def parse_times(text: str) -> list[str]:
    """'6:30, 2:30pm 22:30' -> ['06:30', '14:30', '22:30']. Raises ValueError on a bad time."""
    out = []
    for token in re.split(r"[,\s;]+", text.strip().lower()):
        if not token:
            continue
        m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", token)
        if not m:
            raise ValueError(f"'{token}' isn't a time like 6:30 or 14:30")
        h, mnt, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ampm:
            if not 1 <= h <= 12:
                raise ValueError(f"'{token}' isn't a valid time")
            h = h % 12 + (12 if ampm == "pm" else 0)
        if h > 23 or mnt > 59:
            raise ValueError(f"'{token}' isn't a valid time")
        out.append(f"{h:02d}:{mnt:02d}")
    return sorted(set(out))


def next_scan(settings: dict, now: datetime | None = None) -> str:
    """When the next scheduled scan runs, e.g. "today at 2:30 PM PDT" or "tomorrow at 6:30 AM PDT"."""
    tz = ZoneInfo(zones_of(settings)[1])
    now = (now or datetime.now(tz)).astimezone(tz)
    times = settings.get("scan_times") or scan_times(settings["notify_time"], settings["scan_interval_hours"])
    for days, word in ((0, "today"), (1, "tomorrow")):
        day = now.date() + timedelta(days=days)
        for t in sorted(times):
            h, m = (int(x) for x in t.split(":"))
            when = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            if when > now:
                return f"{word} at {when.strftime('%-I:%M %p %Z')}"
    return "on schedule"


def routines(p: Paths, port: int = 8765) -> list[dict]:
    settings = load_settings(p)
    notify_tz, scan_tz = zones_of(settings)
    times = settings.get("scan_times") or scan_times(settings["notify_time"], settings["scan_interval_hours"])
    scans = [
        {"name": f"{PREFIX}cycle-{i}", "command": _ap("cycle", p), "type": "daily", "time": t,
         "timezone": scan_tz, "timeout": 3600}
        for i, t in enumerate(times, 1)
    ]
    daily = [{"name": PREFIX + "digest", "command": _ap("digest", p), "type": "daily", "time": settings["notify_time"],
              "timezone": notify_tz, "timeout": 300}] if settings.get("notify_mode") == "daily" else []
    return scans + daily + [
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


def _installed() -> list[dict]:
    import json

    proc = subprocess.run(["openbase-coder", "routines", "list"], capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    items = data.get("routines", data) if isinstance(data, dict) else data
    return [r for r in items if isinstance(r, dict) and r.get("name", "").startswith(PREFIX)]


def installed_names() -> list[str]:
    return [r["name"] for r in _installed()]


def installed_home() -> str | None:
    """The data folder the installed routines run against, if any."""
    for r in _installed():
        m = re.search(r"APPLYPEACE_HOME=(\S+)", r.get("command") or "")
        if m:
            return shlex.split(m.group(1))[0]
    return None


def resync_if_mine(p: Paths) -> bool:
    """Re-install the schedule only if it already belongs to this data folder.

    Settings changes in any other data folder (tests, a dev copy) must never
    repoint the real schedule.
    """
    if installed_home() != str(p.home):
        return False
    install(p)
    return True


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


def scanning(p: Paths) -> bool:
    """True while a scan (scheduled or "Scan now") is running."""
    from .pipeline import single_run

    with single_run(p, "cycle") as free:
        return not free


def scan_now(p: Paths) -> bool:
    """Start a scan in the background right away. False if one is already running."""
    if scanning(p):
        return False
    exe = shutil.which("ap") or sys.argv[0]
    log = (p.home / "ap.log").open("a")
    subprocess.Popen([exe, "cycle", "--manual"], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     start_new_session=True, env={**os.environ, "APPLYPEACE_HOME": str(p.home)})
    return True


def ensure_published(name: str = "applypeace") -> str:
    """Bring the phone page's gateway back if Openbase left it stopped (e.g. after a workspace restart)."""
    listing = subprocess.run(["openbase-coder", "service", "list"], capture_output=True, text=True).stdout
    line = next((l for l in listing.splitlines() if l.split()[:1] == [name]), "")
    if not line or "stopped" not in line:
        return ""
    proc = subprocess.run(["openbase-coder", "service", "restore"], capture_output=True, text=True)
    return "Restored the phone page gateway." if proc.returncode == 0 else f"Gateway restore failed: {proc.stderr.strip()[:200]}"
