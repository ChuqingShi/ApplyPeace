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
    import uvicorn

    from .web.app import create_app

    uvicorn.run(create_app(p), host="127.0.0.1", port=args.port, log_level="warning")
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

    sub.add_parser("discover", help="read every enabled board and store new jobs").set_defaults(func=cmd_discover)
    sub.add_parser("status", help="jobs and applications at a glance").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="check setup, profile, lists and browser").set_defaults(func=cmd_doctor)
    sub.add_parser("setup", help="what setup still needs").set_defaults(func=cmd_setup)
    serve = sub.add_parser("serve", help="run the private phone pages on 127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    p = config.paths()
    p.home.mkdir(parents=True, exist_ok=True)
    return args.func(args, p)


if __name__ == "__main__":
    sys.exit(main())
