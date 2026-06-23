#!/usr/bin/env python3
"""Outreach Recruiter — command-line orchestration.

Typical flow:
    python cli.py init                 # create the database
    python cli.py load-sample          # load fictional test candidates
    python cli.py score                # ATS-score everyone
    python cli.py draft                # generate email drafts for matches
    python app.py                      # open the dashboard to review/approve

Real data sources (instead of load-sample):
    python cli.py import-csv people.csv
    python cli.py scrape urls.txt      # LinkedIn — read the spider's caveats
"""
from __future__ import annotations

import argparse
import csv
import sys

from recruiter import db
from recruiter.config import load_config
from recruiter import email_gen, scoring
from recruiter.sample_data import SAMPLE_CANDIDATES


def cmd_init(args):
    db.init_db()
    print(f"Initialized database at {db.DB_PATH}")


def cmd_load_sample(args):
    db.init_db()
    with db.connect() as conn:
        for c in SAMPLE_CANDIDATES:
            db.upsert_candidate(conn, c)
    print(f"Loaded {len(SAMPLE_CANDIDATES)} sample candidates.")


def cmd_import_csv(args):
    """Import candidates from a CSV. Recognized columns: name, headline,
    location, profile_url, email, grad_year, school, raw_text."""
    db.init_db()
    n = 0
    with open(args.file, newline="", encoding="utf-8") as fh, db.connect() as conn:
        for row in csv.DictReader(fh):
            row = {k.strip(): (v.strip() if isinstance(v, str) else v)
                   for k, v in row.items()}
            if row.get("grad_year"):
                try:
                    row["grad_year"] = int(row["grad_year"])
                except ValueError:
                    row["grad_year"] = None
            row.setdefault("source", "csv")
            if not row.get("profile_url"):
                row["profile_url"] = f"csv://{row.get('name','')}-{n}"
            db.upsert_candidate(conn, row)
            n += 1
    print(f"Imported {n} candidates from {args.file}")


def cmd_scrape(args):
    """Run the Scrapy LinkedIn spider against a file of profile URLs."""
    from scrapy.crawler import CrawlerProcess
    from recruiter.scraper.linkedin_spider import LinkedInSpider

    process = CrawlerProcess()
    process.crawl(LinkedInSpider, urls_file=args.urls_file)
    process.start()
    print("Scrape complete (check warnings above for auth walls / blocks).")


def cmd_score(args):
    cfg = load_config()
    scored = skipped = 0
    with db.connect() as conn:
        for row in db.all_candidates(conn):
            cand = dict(row)
            if not args.no_filter and not scoring.matches_target(cand, cfg):
                skipped += 1
                continue
            result = scoring.score_text(scoring.candidate_text(cand), cfg)
            db.save_score(conn, cand["id"], result["total"], result["breakdown"])
            scored += 1
    print(f"Scored {scored} candidate(s); skipped {skipped} (off-target).")
    if args.no_filter:
        print("  (--no-filter: scored everyone regardless of grad year/school)")


def cmd_draft(args):
    cfg = load_config()
    n = 0
    with db.connect() as conn:
        for row in db.ranked(conn, min_score=args.min_score):
            cand = dict(row)
            draft = email_gen.generate(cand, cfg)
            db.save_draft(conn, cand["id"], draft["subject"], draft["body"])
            n += 1
    print(f"Generated {n} draft(s) for candidates scoring >= {args.min_score}.")


def cmd_list(args):
    with db.connect() as conn:
        rows = db.ranked(conn, min_score=args.min_score)
    if not rows:
        print("No scored candidates. Run: score")
        return
    print(f"{'SCORE':>5}  {'STATUS':<9}  NAME")
    print("-" * 50)
    for r in rows:
        print(f"{(r['score'] or 0):>5}  {r['draft_status']:<9}  {r['name']}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Outreach Recruiter CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the database").set_defaults(func=cmd_init)
    sub.add_parser("load-sample", help="load fictional test candidates").set_defaults(func=cmd_load_sample)

    sp = sub.add_parser("import-csv", help="import candidates from a CSV")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_import_csv)

    sp = sub.add_parser("scrape", help="scrape LinkedIn profile URLs (see caveats)")
    sp.add_argument("urls_file")
    sp.set_defaults(func=cmd_scrape)

    sp = sub.add_parser("score", help="ATS-score candidates")
    sp.add_argument("--no-filter", action="store_true",
                    help="score everyone, ignoring grad-year/school target")
    sp.set_defaults(func=cmd_score)

    sp = sub.add_parser("draft", help="generate email drafts")
    sp.add_argument("--min-score", type=int, default=40)
    sp.set_defaults(func=cmd_draft)

    sp = sub.add_parser("list", help="show ranked candidates")
    sp.add_argument("--min-score", type=int, default=0)
    sp.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
