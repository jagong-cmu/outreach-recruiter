#!/usr/bin/env python3
"""GPU appraisal outreach: command line.

    python cli.py import-research data/prospects.json
    python cli.py run                  # qualify, score and check drafts
    python cli.py list --min-score 60
    python cli.py gaps                 # who has no personal fact yet
    python cli.py x-search             # read-only X research, for review
    python app.py                      # open the dashboard
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys

from outreach import db, messages, qualify, scoring
from outreach.config import load_config, missing_identity


def _push_after_import():
    """With a shared database, send the new prospects to the queue at once."""
    from outreach import linkedin, pipeline, shared
    try:
        sh = shared.settings(load_config())
        if sh:
            r = pipeline.sync(sh, linkedin.LOG_PATH)
            print(f"Shared queue: {r['pulled']} prospects.")
    except shared.SharedError as err:
        print(f"Not shared yet: {err}. Run sync later.")


def cmd_import_research(args):
    """Import a JSON array of prospects."""
    db.init_db()
    with open(args.file, encoding="utf-8") as fh:
        people = json.load(fh)
    n = 0
    with db.connect() as conn:
        for person in people:
            if not (person.get("name") or "").strip():
                continue
            db.upsert_prospect(conn, person)
            n += 1
    print(f"Imported {n} prospects from {args.file}")
    _push_after_import()


def cmd_import_csv(args):
    """Import a CSV. Columns use the same names as the research JSON."""
    db.init_db()
    n = 0
    with open(args.file, newline="", encoding="utf-8-sig") as fh, db.connect() as conn:
        for row in csv.DictReader(fh):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("name"):
                continue
            row["cmu_link"] = row.get("cmu_link", "").lower() in ("1", "true", "yes")
            db.upsert_prospect(conn, row)
            n += 1
    print(f"Imported {n} prospects from {args.file}")
    _push_after_import()


def cmd_run(args):
    """Qualify and score every prospect, then check the draft lengths."""
    from outreach import pipeline
    cfg = load_config()
    r = pipeline.rescore(cfg)
    print("Fit:", ", ".join(f"{k}={v}" for k, v in sorted(r["counts"].items())))
    print(f"Ready to contact: {r['ready']}. Over length limit: {r['over']}.")
    missing = missing_identity(cfg)
    if missing:
        print("Emails and long messages have no sign-off. Fill in config.local.yaml: "
              + ", ".join(missing))


def cmd_list(args):
    with db.connect() as conn:
        rows = db.ranked(conn, args.min_score, args.stage, args.segment)
    print(f"{'SCORE':>5}  {'STAGE':<8} {'SEGMENT':<18} NAME")
    for r in rows[:args.limit]:
        print(f"{r['score']:>5}  {r['stage']:<8} {(r['segment'] or ''):<18} "
              f"{r['name']} ({r['title'] or '?'}, {r['firm'] or '?'})")


def cmd_gaps(args):
    with db.connect() as conn:
        rows = [r for r in db.ranked(conn) if not r["opener"]]
    print(f"{len(rows)} scored prospects have no personal fact in long messages.")
    for r in rows[:args.limit]:
        print(f"  [{r['id']}] {r['name']}, {r['firm']}: {r['hook'] or '(no hook)'}")


def cmd_send_linkedin(args):
    """Send connection notes through the founder's logged-in Chrome."""
    from outreach import linkedin, shared
    try:
        if args.until:
            return linkedin.keep_going(dt.datetime.fromisoformat(args.until), args.delay,
                                       search=args.search)
        return linkedin.run(limit=args.limit, names=args.name, dry_run=args.dry_run,
                            delay=args.delay)
    except shared.SharedError as err:
        print(f"Stopped: {err}")          # Never send without the shared claims.
        return 1


def cmd_search_linkedin(args):
    """Run the next free alumni search terms in the logged-in Chrome."""
    from playwright.sync_api import sync_playwright
    from outreach import linkedin, linkedin_search, shared
    cfg = load_config()
    try:
        with sync_playwright() as p:
            # Its own tab, so a sender that runs at the same time keeps its tab.
            ctx = p.chromium.connect_over_cdp(cfg["linkedin"]["cdp_url"]).contexts[0]
            page = ctx.new_page()
            try:
                r = linkedin_search.run(page, args.terms, cfg)
            finally:
                page.close()
    except linkedin.Stop as why:
        print(f"Stopped: {why}")
        return 1
    except shared.SharedError as err:
        print(f"Stopped: {err}")
        return 1
    print(f"Searched {r['terms']} terms. Added {r['added']} people. "
          f"Terms that another run already did: {r['skipped_terms']}.")
    return 0


def _shared_settings():
    from outreach import shared
    sh = shared.settings(load_config())
    if not sh:
        print("No shared database. Set shared.database_url and shared.sender "
              "in config.local.yaml.")
    return sh


def cmd_sync(args):
    """Push local prospects and contacts to the shared queue, then pull the others."""
    from outreach import linkedin, pipeline, shared
    try:
        sh = _shared_settings()
        if not sh:
            return 1
        r = pipeline.sync(sh, linkedin.LOG_PATH)
    except shared.SharedError as err:
        print(f"Stopped: {err}")
        return 1
    print(f"Pushed {r['queued']} prospects. New claims for your contacts: "
          f"{r['claimed']}. Pulled {r['pulled']} prospects.")
    cmd_run(args)
    return 0


def cmd_shared_status(args):
    from outreach import shared
    try:
        sh = _shared_settings()
        if not sh:
            return 1
        with shared.connect(sh["url"]) as conn:
            c = shared.counts(conn)
    except shared.SharedError as err:
        print(f"Stopped: {err}")
        return 1
    print(f"You are '{sh['sender']}'. Shared queue: {c['queue']} prospects.")
    for sender, status, n in sorted(c["claims"]):
        print(f"  {sender:<12} {status:<8} {n}")
    return 0


def _relevant(cfg):
    """A person counts only when one sentence names both GPUs and finance."""
    return lambda text: qualify.evidence({"why_relevant": text}, cfg) is not None


def _x_setup(path):
    """Prune the stored list, then return (config, stored, known people, seen handles)."""
    from outreach import x_api, x_browser
    cfg = load_config()
    relevant = _relevant(cfg)
    stored = x_api.prune_review_file(relevant, path)
    with db.connect() as conn:
        rows = [dict(r) for r in db.all_prospects(conn)]
    handles, names = x_api.known_people(rows)
    seen = x_api.seen_handles([x_api.OUT_PATH, x_browser.OUT_PATH])
    return cfg["x_search"], stored, handles, names, seen, relevant


def _x_finish(result, stored, path, label):
    from outreach import x_api
    merged = x_api.merge_results(stored, result)
    x_api.write_review_file(merged, path)
    print(label)
    print(f"New people: {len(result['candidates'])}. "
          f"List total: {len(merged['candidates'])}. Blocked: {len(merged['blocked'])}.")
    print(f"Review file: {path}")
    if result["error"]:
        print(f"Stopped early: {result['error']}")
        return 1
    return 0


def _x_search_browser(args):
    """Backup search. It reads X in the logged-in Chrome, without API charges."""
    from outreach import x_api, x_browser
    cfg, stored, handles, names, seen, relevant = _x_setup(x_browser.OUT_PATH)
    if args.only:
        cfg = {**cfg, "user_queries": {"unsorted": args.only}, "post_queries": {}}
    if args.queries:
        import yaml
        with open(args.queries, encoding="utf-8") as fh:
            extra = yaml.safe_load(fh)
        cfg = {**cfg, "user_queries": extra.get("user_queries") or {},
               "post_queries": extra.get("post_queries") or {},
               "browser": {**cfg["browser"], **(extra.get("browser") or {})}}
    try:
        result, client = x_browser.run_browser_search(cfg, handles, names, seen, relevant)
    except x_api.XError as err:
        print(f"Stopped: {err}")          # The review file keeps its last good list.
        return 1
    return _x_finish(result, stored, x_browser.OUT_PATH,
                     f"Search pages: {client.used}. Profiles: {client.profiles}.")


def cmd_x_search(args):
    """Find people on X. This writes a review file, not database rows."""
    from outreach import x_api
    if args.browser:
        return _x_search_browser(args)
    x_api.load_env()
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        print("Set X_BEARER_TOKEN first, or save it in data/x.env. "
              "Do not put the token in a tracked file.")
        return 1
    cfg, stored, handles, names, seen, relevant = _x_setup(x_api.OUT_PATH)
    keys = {k: os.environ.get(k, "").strip() for k in x_api.OAUTH_ENV}
    oauth = keys if all(keys.values()) else None
    if not oauth:
        cfg = {**cfg, "user_queries": {}}
        print("Skipping user search. It needs " + ", ".join(x_api.OAUTH_ENV) + ".")
    budget = cfg["max_usd"] if args.max_usd is None else args.max_usd
    client = x_api.Client(token, args.max_resources or 10**9, oauth=oauth, max_usd=budget,
                          min_gap_hours=(cfg["min_gap_hours"] if args.min_gap_hours is None
                                         else args.min_gap_hours))
    result = x_api.run_search(client, cfg, handles, names, cfg["min_followers"], seen, relevant)
    return _x_finish(result, stored, x_api.OUT_PATH,
                     f"Estimated spend: ${client.spent:.2f} of ${budget:.2f}. "
                     f"Records: {client.used}. The X console shows the true charge.")


def cmd_x_dm(args):
    from outreach import x_dm
    result = x_dm.run(x_dm.load_recipients(args.file), load_config(), send=args.send,
                      gap_seconds=args.gap, limit=args.limit)
    return 1 if result.get("stopped") else 0


def cmd_x_graph(args):
    """Read follow lists in the logged-in Chrome. Writes its own review file."""
    import yaml
    from outreach import x_api, x_browser, x_graph
    with open(args.seeds, encoding="utf-8") as fh:
        plan = yaml.safe_load(fh)
    cfg = load_config()["x_search"]
    browser = {**cfg["browser"], **(plan.get("browser") or {})}
    with db.connect() as conn:
        rows = [dict(r) for r in db.all_prospects(conn)]
    handles, names = x_api.known_people(rows)
    stored = x_graph.load(x_graph.OUT_PATH)
    skip = x_api.seen_handles([x_api.OUT_PATH, x_browser.OUT_PATH, x_graph.OUT_PATH])
    try:
        result, client = x_graph.run_browser(browser, plan["seeds"], handles, names, skip)
    except x_api.XError as err:
        print(f"Stopped: {err}")
        return 1
    return _x_finish(result, stored, x_graph.OUT_PATH, f"List pages: {client.used}.")


def main(argv=None):
    p = argparse.ArgumentParser(description="GPU appraisal outreach")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("import-research", help="import a JSON array of prospects")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_import_research)

    sp = sub.add_parser("import-csv", help="import prospects from a CSV")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_import_csv)

    sub.add_parser("run", help="qualify, score and check drafts").set_defaults(func=cmd_run)

    sp = sub.add_parser("list", help="show ranked prospects")
    sp.add_argument("--min-score", type=int, default=0)
    sp.add_argument("--stage")
    sp.add_argument("--segment")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("gaps", help="show who has no personal fact yet")
    sp.add_argument("--limit", type=int, default=30)
    sp.set_defaults(func=cmd_gaps)

    sp = sub.add_parser("send-linkedin", help="send connection notes in your logged-in Chrome")
    sp.add_argument("--limit", type=int, default=10, help="most invites this run")
    sp.add_argument("--name", action="append", help="only this person (repeat for more)")
    sp.add_argument("--dry-run", action="store_true", help="stop just before Send")
    sp.add_argument("--delay", type=int, help="seconds between invites")
    sp.add_argument("--until", help="keep sending daily until this local time, for example "
                                    "2026-11-08T23:00")
    sp.add_argument("--search", action="store_true",
                    help="with --until: also run the alumni search when nobody is left")
    sp.set_defaults(func=cmd_send_linkedin)

    sp = sub.add_parser("search-linkedin", help="run the next CMU alumni search terms in Chrome")
    sp.add_argument("--terms", type=int, help="most search terms this run")
    sp.set_defaults(func=cmd_search_linkedin)

    sp = sub.add_parser("sync", help="share your prospects and contacts, and get the others")
    sp.set_defaults(func=cmd_sync)

    sub.add_parser("shared-status", help="show the shared queue and the claims per sender"
                   ).set_defaults(func=cmd_shared_status)

    sp = sub.add_parser("x-search", help="find people on X for review (read-only)")
    sp.add_argument("--max-resources", type=int, help="stop after this many records")
    sp.add_argument("--max-usd", type=float, help="session budget in dollars")
    sp.add_argument("--min-gap-hours", type=float, help="hours between runs of a post query")
    sp.add_argument("--browser", action="store_true",
                    help="backup: read X in your logged-in Chrome, not the paid API")
    sp.add_argument("--only", action="append", help="with --browser: run only this user query")
    sp.add_argument("--queries", help="with --browser: a YAML file of queries for this run")
    sp.set_defaults(func=cmd_x_search)

    sp = sub.add_parser("x-dm", help="preview or send X DMs from data/x_dm_recipients.csv")
    sp.add_argument("--send", action="store_true", help="send; without it, only preview")
    sp.add_argument("--gap", type=int, default=300, help="seconds between DMs")
    sp.add_argument("--limit", type=int, help="most DMs this run")
    sp.add_argument("--file", help="recipient CSV (handle,name,greeting,cmu_link)")
    sp.set_defaults(func=cmd_x_dm)

    sp = sub.add_parser("x-graph", help="find people in follow lists of found people (read-only)")
    sp.add_argument("seeds", help="YAML file: seeds (handle, kind, scrolls) and browser settings")
    sp.set_defaults(func=cmd_x_graph)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
