#!/usr/bin/env python3
"""GPU appraisal outreach: command line.

    python cli.py import-research data/prospects.json
    python cli.py run                  # qualify, score and draft
    python cli.py list --min-score 60
    python cli.py gaps                 # who still needs an opener
    python app.py                      # open the dashboard
"""
from __future__ import annotations

import argparse
import csv
import json
import sys

from outreach import db, messages, qualify, scoring
from outreach.config import load_config, missing_identity


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


def cmd_run(args):
    """Qualify, score and draft every prospect."""
    cfg = load_config()
    missing = missing_identity(cfg)
    if missing:
        print("No drafts yet. Fill these fields in config.local.yaml, then run again: "
              + ", ".join(missing))
    counts: dict[str, int] = {}
    drafted = no_opener = over = 0
    with db.connect() as conn:
        for row in db.all_prospects(conn):
            p = dict(row)
            verdict = qualify.qualify(p, cfg)
            db.set_fit(conn, p["id"], verdict["fit"])
            counts[verdict["fit"]] = counts.get(verdict["fit"], 0) + 1
            db.delete_messages(conn, p["id"])
            if verdict["fit"] not in qualify.PASSING:
                db.delete_score(conn, p["id"])
                continue
            result = scoring.score_prospect(p, cfg)
            db.save_score(conn, p["id"], result["total"], result["breakdown"])
            if missing:
                continue
            drafts = messages.generate(p, cfg)
            if not drafts:
                no_opener += 1
                continue
            drafted += 1
            for m in drafts:
                db.save_message(conn, p["id"], m["channel"], m["subject"], m["body"])
                if m["over"]:
                    over += 1
                    print(f"  over limit: {p['name']} {m['channel']} "
                          f"{m['chars']}/{m['limit']}")
    print("Fit:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"Drafted {drafted}. No opener yet: {no_opener}. Over length limit: {over}.")


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
    print(f"{len(rows)} scored prospects have no opener.")
    for r in rows[:args.limit]:
        print(f"  [{r['id']}] {r['name']}, {r['firm']}: {r['hook'] or '(no hook)'}")


def main(argv=None):
    p = argparse.ArgumentParser(description="GPU appraisal outreach")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("import-research", help="import a JSON array of prospects")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_import_research)

    sp = sub.add_parser("import-csv", help="import prospects from a CSV")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_import_csv)

    sub.add_parser("run", help="qualify, score and draft").set_defaults(func=cmd_run)

    sp = sub.add_parser("list", help="show ranked prospects")
    sp.add_argument("--min-score", type=int, default=0)
    sp.add_argument("--stage")
    sp.add_argument("--segment")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("gaps", help="show who still needs an opener")
    sp.add_argument("--limit", type=int, default=30)
    sp.set_defaults(func=cmd_gaps)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
