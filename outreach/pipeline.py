"""Steps that the CLI and the automatic loop share: rescore and sync."""
from __future__ import annotations

import csv
import os
from typing import Optional

from . import db, messages, qualify, scoring, shared
from .config import load_config


def rescore(cfg: Optional[dict] = None) -> dict:
    """Qualify and score every prospect. Return the fit counts and draft checks."""
    cfg = cfg or load_config()
    counts: dict[str, int] = {}
    ready = over = 0
    with db.connect() as conn:
        for row in db.all_prospects(conn):
            p = dict(row)
            verdict = qualify.qualify(p, cfg)
            db.set_fit(conn, p["id"], verdict["fit"])
            counts[verdict["fit"]] = counts.get(verdict["fit"], 0) + 1
            if verdict["fit"] not in qualify.PASSING:
                db.delete_score(conn, p["id"])
                continue
            result = scoring.score_prospect(p, cfg)
            db.save_score(conn, p["id"], result["total"], result["breakdown"])
            ready += 1
            for m in messages.generate(p, cfg):
                over += m["over"]
    return {"counts": counts, "ready": ready, "over": over}


def sync(sh: dict, log_path: str) -> dict:
    """Push local prospects and contacts to the shared queue, then pull the others."""
    db.init_db()
    with db.connect() as conn:
        rows = [dict(r) for r in db.all_prospects(conn)]
    log_rows = []
    if os.path.exists(log_path):
        with open(log_path, newline="", encoding="utf-8") as fh:
            log_rows = list(csv.DictReader(fh))
    with shared.connect(sh["url"]) as conn:
        pushed = shared.push(conn, sh["sender"], rows, log_rows)
        people = shared.pull(conn)
    with db.connect() as conn:
        for person in people:
            if (person.get("name") or "").strip():
                db.upsert_prospect(conn, person)
    return {**pushed, "pulled": len(people)}
