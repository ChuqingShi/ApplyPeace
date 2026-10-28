"""The `ap` command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .companies import lists as company_lists
from .companies import registry as reg
from .db import connect
from .sources.base import http_client


def _out(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- lists


def cmd_lists_show(args, p: config.Paths) -> int:
    settings = config.load_settings(p)
    registry = reg.load(p)
    _out("Company lists (a job qualifies if its employer is on any list that is ON):\n")
    for key, label in config.LISTS.items():
        members = [c for c in registry.values() if key in c.get("lists", [])]
        auto = [c for c in members if c.get("verified") and c.get("ats") in reg.SUPPORTED_ATS]
        state = "ON " if settings["lists"].get(key) else "off"
        detail = f"{len(members):4d} companies, {len(auto):3d} auto-apply ready" if members else "   not imported yet"
        _out(f"  [{state}] {label:32s} {detail}   ({key})")
    on = reg.enabled(registry, settings)
    ready = [c for c in on if c.get("verified") and c.get("ats") in reg.SUPPORTED_ATS]
    _out(f"\nEnabled total: {len(on)} unique companies, {len(ready)} with a verified Greenhouse/Ashby/Lever board.")
    _out(f"Job location: {settings['job_location']}   H-1B filter: {'on' if settings['h1b_filter'] else 'off'}")
    return 0


def cmd_lists_toggle(args, p: config.Paths) -> int:
    settings = config.load_settings(p)
    for key in args.lists:
        if key not in config.LISTS:
            _out(f"Unknown list {key!r}. Choose from: {', '.join(config.LISTS)}")
            return 2
        settings["lists"][key] = args.command == "on"
    config.save_settings(settings, p)
    return cmd_lists_show(args, p)


def _apply_list(p: config.Paths, key: str, members: list[company_lists.Member]) -> None:
    registry = reg.load(p)
    joined, left = reg.set_list(registry, key, members)
    reg.save(p, registry)
    _out(f"{config.LISTS[key]}: {len(members)} companies ({joined} joined, {left} left).")


def cmd_lists_update(args, p: config.Paths) -> int:
    with http_client() as client:
        for key, fetch in (("sp500", company_lists.fetch_sp500), ("nasdaq100", company_lists.fetch_nasdaq100)):
            if args.lists and key not in args.lists:
                continue
            _apply_list(p, key, fetch(client))
    _out("Fortune 500, Forbes Global 2000 and Forbes AI 50 are imported from a CSV you save: `ap lists import <list> <file.csv>`.")
    return 0


def cmd_lists_import(args, p: config.Paths) -> int:
    members = company_lists.read_csv(Path(args.file))
    if args.list == "forbes_global2000":
        _apply_list(p, "forbes_global2000_us", [m for m in members if m.hq_country == "US"])
        _apply_list(p, "forbes_global2000_intl", [m for m in members if m.hq_country != "US"])
        unknown = sum(1 for m in members if not m.hq_country)
        if unknown:
            _out(f"Note: {unknown} rows had no country column value and were treated as non-US.")
    else:
        _apply_list(p, args.list, members)
    return 0


def cmd_lists_add(args, p: config.Paths) -> int:
    registry = reg.load(p)
    ats_name = board = None
    if args.board:
        ats_name, _, board = args.board.partition(":")
        if ats_name not in reg.SUPPORTED_ATS or not board:
            _out("--board must look like greenhouse:<slug>, ashby:<slug> or lever:<slug>")
            return 2
    company = reg.add_custom(registry, args.name, ats_name, board)
    if not board:
        reg.resolve(registry, [company], workers=1)
    reg.save(p, registry)
    status = f"{company.get('ats')}:{company.get('board')}" if company.get("board") else "no supported board found"
    _out(f"Added {company['name']} to My companies ({status}{'' if company.get('verified', False) else ', unverified'}).")
    return 0


def cmd_lists_resolve(args, p: config.Paths) -> int:
    settings = config.load_settings(p)
    registry = reg.load(p)
    targets = reg.enabled(registry, settings) if not args.all else list(registry.values())
    todo = [c for c in targets if reg.needs_resolution(c)]
    _out(f"Looking up job boards for {len(todo)} companies (Greenhouse, Ashby, Lever)...")

    def progress(i, total, company):
        if company.get("verified"):
            _out(f"  [{i}/{total}] {company['name']}: {company['ats']}:{company['board']}")
        elif i % 25 == 0 or i == total:
            _out(f"  [{i}/{total}] ...")
        if i % 50 == 0:
            reg.save(p, registry)

    counts = reg.resolve(registry, todo, progress=progress)
    reg.save(p, registry)
    _out(f"Done: {counts['verified']} verified, {counts['to_confirm']} to confirm, {counts['not_found']} not on a supported ATS.")
    if counts["to_confirm"]:
        _out("Review the near-matches with `ap lists confirm`.")
    return 0


def cmd_lists_confirm(args, p: config.Paths) -> int:
    registry = reg.load(p)
    if args.key:
        company = registry.get(args.key)
        if not company or args.answer not in ("yes", "no"):
            _out("Usage: ap lists confirm <company key> yes|no")
            return 2
        reg.confirm(company, args.answer == "yes")
        reg.save(p, registry)
        _out(f"{company['name']}: board {company['ats']}:{company['board']} {'accepted' if args.answer == 'yes' else 'rejected'}.")
        return 0
    pending = reg.pending_confirmation(list(registry.values()))
    if not pending:
        _out("No job boards waiting for confirmation.")
        return 0
    _out("These boards look like the company but aren't an exact name match. Confirm each once:\n")
    for c in pending:
        _out(f"  {c['key']:28s} {c['name'][:30]:30s} -> {c['ats']}:{c['board']} (owner '{c.get('owner_name')}', {c.get('job_count')} jobs)")
    _out("\nAccept or reject with: ap lists confirm <key> yes|no")
    return 0


# ---------------------------------------------------------------- discover / status


def cmd_discover(args, p: config.Paths) -> int:
    from .discover import discover

    settings = config.load_settings(p)
    companies = reg.enabled(reg.load(p), settings)
    with connect(p.db) as conn:
        result = discover(conn, companies)
    _out(f"Read {result.boards} boards: {result.seen} open jobs, {result.new} new, {result.closed} closed.")
    for error in result.errors[:10]:
        _out(f"  ! {error}")
    return 0


def cmd_status(args, p: config.Paths) -> int:
    settings = config.load_settings(p)
    _out(f"ApplyPeace data folder: {p.home}")
    _out(f"Setup complete: {'yes' if settings['setup_complete'] else 'no (run `ap setup`)'}")
    if not p.db.exists():
        _out("No jobs yet. Run `ap discover`.")
        return 0
    with connect(p.db) as conn:
        rows = conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status ORDER BY n DESC").fetchall()
        _out("Jobs by status: " + ", ".join(f"{r['status']} {r['n']}" for r in rows))
        apps = conn.execute(
            "SELECT j.title, j.company_key, a.status, a.finished_at FROM applications a"
            " JOIN jobs j ON j.id = a.job_id ORDER BY a.finished_at DESC LIMIT 10"
        ).fetchall()
        if apps:
            _out("\nRecent applications:")
            for a in apps:
                _out(f"  {a['company_key']:20s} {a['title'][:40]:40s} {a['status']:10s} {a['finished_at'] or ''}")
    return 0


def cmd_doctor(args, p: config.Paths) -> int:
    from .profile import completeness

    ok = True
    _out(f"Data folder: {p.home} ({'exists' if p.home.exists() else 'missing'})")
    missing = completeness(p)
    if missing:
        ok = False
        _out("Profile is incomplete:")
        for item in missing:
            _out(f"  - {item}")
    else:
        _out("Profile: complete")
    registry = reg.load(p)
    if not registry:
        ok = False
        _out("No company lists imported yet: run `ap lists update`.")
    from .browser import check_browser

    from . import h1b

    if not h1b.default_db().exists():
        _out("H-1B data: not loaded (run `ap h1b update`); the H-1B filter falls back to job-text checks.")
    else:
        _out(f"H-1B data: {h1b.default_db()}")
    browser_ok, detail = check_browser()
    ok &= browser_ok
    _out(f"Browser: {detail}")
    return 0 if ok else 1


# ---------------------------------------------------------------- h1b


def cmd_h1b_update(args, p: config.Paths) -> int:
    from datetime import date

    from . import h1b
    from .h1b.store import update

    this_fy = date.today().year + (1 if date.today().month >= 10 else 0)
    years = args.years or [this_fy - 1, this_fy]
    update(h1b.default_db(), h1b.raw_dir(), years, log=_out)
    return 0


def cmd_h1b_lookup(args, p: config.Paths) -> int:
    from . import h1b
    from .h1b.lookup import summary
    from .h1b.store import connect

    with connect(h1b.default_db()) as conn:
        s = summary(conn, args.name)
    if not s["found"]:
        _out(f"No H-1B filings found for {args.name!r} in the loaded years.")
        return 1
    _out(f"{args.name}: {'sponsors' if s['sponsor'] else 'little recent sponsorship'} "
         f"({s['recent_certified']} certified LCAs in FY{'/'.join(map(str, s['recent_years']))})")
    for fy, counts in s["by_fy"].items():
        _out(f"  FY{fy}: {counts['certified']} certified of {counts['filed']} filed")
    _out(f"  Filed as: {', '.join(s['employers'])}")
    _out(f"  Top titles: {', '.join(s['top_titles'][:6])}")
    _out(f"  Wage levels: {s['wage_levels']}")
    return 0


def cmd_h1b_serve(args, p: config.Paths) -> int:
    import uvicorn

    from .h1b.api import create_api

    uvicorn.run(create_api(), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


# ---------------------------------------------------------------- setup / serve


def cmd_setup(args, p: config.Paths) -> int:
    from .profile import completeness

    missing = completeness(p)
    if not missing:
        _out("Setup is complete. Edit anything on the phone page (Profile) or re-run the wizard there.")
        return 0
    _out("Setup happens on the private phone page (`ap serve`, then open its URL).")
    _out("Still needed:")
    for item in missing:
        _out(f"  - {item}")
    return 1


def cmd_serve(args, p: config.Paths) -> int:
    if args.background:
        from .schedule import ensure_published, serve_background

        _out(serve_background(p, args.port))
        restored = ensure_published()  # the keep-alive also revives a stopped gateway
        if restored:
            _out(restored)
        return 0
    import uvicorn

    from .web.app import create_app

    uvicorn.run(create_app(p), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


# ---------------------------------------------------------------- scheduled steps


def _scheduled(step: str, fn, p: config.Paths) -> int:
    from .pipeline import SetupIncomplete, single_run

    with single_run(p, step) as acquired:
        if not acquired:
            _out(f"{step} is already running.")
            return 0
        try:
            fn()
        except SetupIncomplete as exc:
            _out(str(exc))
            return 1
    return 0


def cmd_cycle(args, p: config.Paths) -> int:
    from .pipeline import cycle

    return _scheduled("cycle", lambda: cycle(p, log=_out, trigger="manual" if args.manual else "scheduled"), p)


def cmd_digest(args, p: config.Paths) -> int:
    from .pipeline import digest

    return _scheduled("digest", lambda: _out(digest(p, force=args.force) or "Nothing to send."), p)


def cmd_apply(args, p: config.Paths) -> int:
    from .pipeline import apply_step

    return _scheduled("apply", lambda: _out(str(apply_step(p, max_jobs=args.max, log=_out))), p)


# ---------------------------------------------------------------- review from the terminal or chat


def cmd_queue(args, p: config.Paths) -> int:
    import json

    from . import answers as ans

    names = {c["key"]: c["name"] for c in reg.load(p).values()}
    with connect(p.db) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status IN ('queued','approved','needs_you','needs_code','skipped')"
            " ORDER BY status, final_score DESC").fetchall()
        if not rows:
            _out("Nothing to review.")
        for j in rows:
            verdict = json.loads(j["judge"]) if j["judge"] else {}
            missing = len(ans.missing_required(ans.load(conn, j["id"])))
            _out(f"[{j['status']:10s}] {j['id']}")
            _out(f"    {j['title']} · {names.get(j['company_key'], j['company_key'])} · {j['location'] or ''}"
                 + (f" · AI {verdict['score']}/5 {verdict['recommendation']}" if verdict else "")
                 + (f" · {missing} question(s) for you" if missing else ""))
    return 0


def cmd_show(args, p: config.Paths) -> int:
    from . import answers as ans

    with connect(p.db) as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (args.job,)).fetchone()
        if not job:
            _out("No such job.")
            return 2
        _out(f"{job['title']} ({job['status']})\n{job['url']}\n")
        for r in ans.load(conn, args.job):
            value = r["value"]
            if r["options"] and value is not None:
                labels = {str(o["value"]): o["label"] for o in r["options"]}
                value = [labels.get(str(v), v) for v in value] if isinstance(value, list) else labels.get(str(value), value)
            flag = "*" if r["required"] else " "
            _out(f" {flag} {r['field']}\n     {r['label'][:110]}\n     = {value!r}  [{r['source']}]"
                 + (f"\n     options: {[o['label'] for o in r['options']][:8]}" if r["options"] and value is None else ""))
    return 0


def cmd_set(args, p: config.Paths) -> int:
    from . import answers as ans
    from . import review

    with connect(p.db) as conn:
        row = next((r for r in ans.load(conn, args.job) if r["field"] == args.field), None)
        if not row:
            _out("No such question on that job.")
            return 2
        value = args.value
        if row["options"]:  # accept the visible option text
            match = next((o["value"] for o in row["options"] if o["label"].lower() == value.lower()), None) \
                or next((o["value"] for o in row["options"] if value.lower() in o["label"].lower()), None)
            if match is None:
                _out(f"Not an option. Choose from: {[o['label'] for o in row['options']]}")
                return 2
            value = [match] if row["kind"] == "multiselect" else match
        elif row["kind"] == "boolean":
            value = value.lower() in ("yes", "true", "y")
        review.set_answers(p, conn, args.job, {args.field: value}, {args.field} if args.default else set())
    _out("Saved" + (" (and as your default)" if args.default else "") + ".")
    return 0


def cmd_approve(args, p: config.Paths) -> int:
    from . import review
    from .approval import approve

    with connect(p.db) as conn:
        if args.job == "all":
            _out(f"Approved {review.approve_ready(p, conn)} ready jobs.")
            return 0
        try:
            approve(p, conn, args.job)
        except ValueError as exc:
            _out(str(exc))
            return 1
    _out("Approved.")
    return 0


def cmd_reject(args, p: config.Paths) -> int:
    from . import review

    with connect(p.db) as conn:
        review.reject(p, conn, args.job, args.reason, args.block)
    _out("Rejected.")
    return 0


def cmd_skip(args, p: config.Paths) -> int:
    from . import review

    with connect(p.db) as conn:
        review.skip(conn, args.job)
    _out("Kept for later.")
    return 0


def cmd_applied(args, p: config.Paths) -> int:
    from .selfapplied import add_external, job_id_from_url, mark_applied

    with connect(p.db) as conn:
        target = args.job_or_url
        job_id = target if conn.execute("SELECT 1 FROM jobs WHERE id=?", (target,)).fetchone() else None
        if job_id:
            mark_applied(conn, job_id, args.date, args.resume, args.note or "")
        elif args.company and args.title:
            job_id = add_external(p, conn, args.company, args.title, target if "://" in target else "",
                                  args.date, args.resume, args.note or "")
        elif "://" in target and job_id_from_url(conn, target):
            job_id = job_id_from_url(conn, target)
            mark_applied(conn, job_id, args.date, args.resume, args.note or "")
        else:
            _out("Unknown job. Give a job id or posting link, or add --company and --title.")
            return 2
    _out(f"Recorded: you applied ({job_id}).")
    return 0


def cmd_pause(args, p: config.Paths) -> int:
    settings = config.load_settings(p)
    settings["paused"] = args.command == "pause"
    config.save_settings(settings, p)
    _out("Paused: nothing will be submitted." if settings["paused"] else "Resumed.")
    return 0


def cmd_schedule(args, p: config.Paths) -> int:
    from . import schedule

    if args.command == "show":
        for r in schedule.routines(p, args.port):
            when = f"every {r['interval'] // 60} min" if r["type"] == "interval" else f"daily at {r['time']} {r['timezone']}"
            _out(f"{r['name']:20s} {when:28s} {r['command']}")
        return 0
    lines = schedule.install(p, args.port) if args.command == "install" else schedule.remove()
    for line in lines:
        _out(line)
    return 0


def cmd_publish(args, p: config.Paths) -> int:
    import re
    import subprocess

    from .schedule import serve_background

    import time

    from .schedule import port_open

    _out(serve_background(p, args.port))
    for _ in range(40):  # wait for the page to accept connections
        if port_open(args.port):
            break
        time.sleep(0.5)
    cmd = ["openbase-coder", "service", "publish", args.name, str(args.port)] + (["--persist"] if args.persist else [])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    url = re.search(r"https://\S+", proc.stdout + proc.stderr)
    if proc.returncode != 0 or not url:
        _out("Publishing failed:\n" + (proc.stdout + proc.stderr).strip())
        return 1
    settings = config.load_settings(p)
    settings["review_url"] = url.group(0).rstrip("/.")
    config.save_settings(settings, p)
    _out(f"Phone page: {settings['review_url']} (private to your devices on Openbase VPN)")
    from .notify import notify
    from .profile import completeness

    if completeness(p):  # first start: the product asks you for your details itself
        notify("Welcome to ApplyPeace: let's set up your profile.", open_url=settings["review_url"] + "/setup")
        _out("Sent the setup invitation to your phone.")
    return 0


def cmd_backup(args, p: config.Paths) -> int:
    from . import backup

    if args.connect:
        _out(backup.connect_remote(p, args.connect))
    else:
        _out(backup.backup(p, "Manual backup"))
    return 0


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ap", description="ApplyPeace: sponsor-aware job applications, with your approval.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    lists = sub.add_parser("lists", help="company lists and their toggles")
    lsub = lists.add_subparsers(dest="command")
    lists.set_defaults(func=cmd_lists_show)
    lsub.add_parser("show").set_defaults(func=cmd_lists_show)
    for state in ("on", "off"):
        t = lsub.add_parser(state, help=f"switch lists {state}")
        t.add_argument("lists", nargs="+", choices=list(config.LISTS))
        t.set_defaults(func=cmd_lists_toggle)
    up = lsub.add_parser("update", help="refresh S&P 500 and Nasdaq-100 from public sources")
    up.add_argument("lists", nargs="*")
    up.set_defaults(func=cmd_lists_update)
    imp = lsub.add_parser("import", help="import a Fortune/Forbes list from a CSV you saved")
    imp.add_argument("list", choices=["fortune500", "forbes_global2000", "forbes_ai50", "sp500", "nasdaq100"])
    imp.add_argument("file")
    imp.set_defaults(func=cmd_lists_import)
    add = lsub.add_parser("add", help="add a company to My companies")
    add.add_argument("name")
    add.add_argument("--board", help="e.g. greenhouse:anthropic, ashby:openai, lever:palantir")
    add.set_defaults(func=cmd_lists_add)
    res = lsub.add_parser("resolve", help="find each enabled company's Greenhouse/Ashby/Lever board")
    res.add_argument("--all", action="store_true", help="resolve every company, not just enabled lists")
    res.set_defaults(func=cmd_lists_resolve)
    conf = lsub.add_parser("confirm", help="accept or reject near-match job boards")
    conf.add_argument("key", nargs="?")
    conf.add_argument("answer", nargs="?", choices=["yes", "no"])
    conf.set_defaults(func=cmd_lists_confirm)

    h1b = sub.add_parser("h1b", help="H-1B sponsorship evidence (DOL LCA data)")
    hsub = h1b.add_subparsers(dest="command", required=True)
    hu = hsub.add_parser("update", help="download and load DOL LCA disclosure files")
    hu.add_argument("years", nargs="*", type=int, help="fiscal years, default: last and current")
    hu.set_defaults(func=cmd_h1b_update)
    hl = hsub.add_parser("lookup", help="show a company's H-1B record")
    hl.add_argument("name")
    hl.set_defaults(func=cmd_h1b_lookup)
    hs = hsub.add_parser("serve", help="run the H-1B evidence API on 127.0.0.1")
    hs.add_argument("--port", type=int, default=8766)
    hs.set_defaults(func=cmd_h1b_serve)

    cy = sub.add_parser("cycle", help="scan, rank and prepare the review batch")
    cy.add_argument("--manual", action="store_true", help="record this run as started by hand")
    cy.set_defaults(func=cmd_cycle)
    dg = sub.add_parser("digest", help="send the daily 'batch ready' notification")
    dg.add_argument("--force", action="store_true")
    dg.set_defaults(func=cmd_digest)
    ap_ = sub.add_parser("apply", help="send approved applications (scheduled every few minutes)")
    ap_.add_argument("--max", type=int, default=1)
    ap_.set_defaults(func=cmd_apply)
    sub.add_parser("queue", help="jobs waiting for review or action").set_defaults(func=cmd_queue)
    sh = sub.add_parser("show", help="a job's questions and drafted answers")
    sh.add_argument("job")
    sh.set_defaults(func=cmd_show)
    st = sub.add_parser("set", help="change one answer (option text is fine)")
    st.add_argument("job")
    st.add_argument("field")
    st.add_argument("value")
    st.add_argument("--default", action="store_true", help="also save as your default answer")
    st.set_defaults(func=cmd_set)
    apr = sub.add_parser("approve", help="approve a job, or 'all' for every ready job")
    apr.add_argument("job")
    apr.set_defaults(func=cmd_approve)
    rj = sub.add_parser("reject", help="reject a job")
    rj.add_argument("job")
    rj.add_argument("--reason", default="other", choices=["too_senior", "too_junior", "wrong_field", "location", "company", "sponsorship", "other"])
    rj.add_argument("--block", action="store_true", help="never show this company again")
    rj.set_defaults(func=cmd_reject)
    sk = sub.add_parser("skip", help="keep a job for later")
    sk.add_argument("job")
    sk.set_defaults(func=cmd_skip)
    ad = sub.add_parser("applied", help="record an application you made yourself")
    ad.add_argument("job_or_url", help="a job id from `ap queue`, or the posting link")
    ad.add_argument("--company")
    ad.add_argument("--title")
    ad.add_argument("--date", help="YYYY-MM-DD, default today")
    ad.add_argument("--resume", help="resume key, e.g. ai")
    ad.add_argument("--note")
    ad.set_defaults(func=cmd_applied)
    for name in ("pause", "resume"):
        sub.add_parser(name, help=f"{name} submitting").set_defaults(func=cmd_pause, command=name)

    sub.add_parser("discover", help="read every enabled board and store new jobs").set_defaults(func=cmd_discover)
    sub.add_parser("status", help="jobs and applications at a glance").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="check setup, profile, lists and browser").set_defaults(func=cmd_doctor)
    sub.add_parser("setup", help="what setup still needs").set_defaults(func=cmd_setup)
    serve = sub.add_parser("serve", help="run the private phone pages on 127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--background", action="store_true", help="start detached unless already running")
    serve.set_defaults(func=cmd_serve)
    sched = sub.add_parser("schedule", help="install/remove the Openbase routines that run ApplyPeace")
    sched.add_argument("command", choices=["show", "install", "remove"])
    sched.add_argument("--port", type=int, default=8765)
    sched.set_defaults(func=cmd_schedule)
    bk = sub.add_parser("backup", help="export history, refresh the plan copy, commit and push the private data repo")
    bk.add_argument("--connect", metavar="OWNER/REPO", help="connect a private GitHub repo first")
    bk.set_defaults(func=cmd_backup)
    pub = sub.add_parser("publish", help="make the phone page reachable from your phone over Openbase VPN")
    pub.add_argument("--name", default="applypeace")
    pub.add_argument("--port", type=int, default=8765)
    pub.add_argument("--persist", action="store_true", help="keep the publication after the workspace restarts")
    pub.set_defaults(func=cmd_publish)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    p = config.paths()
    p.home.mkdir(parents=True, exist_ok=True)
    return args.func(args, p)


if __name__ == "__main__":
    sys.exit(main())
