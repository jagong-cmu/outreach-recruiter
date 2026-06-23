#!/usr/bin/env python3
"""Outreach Recruiter — review dashboard.

A small local Flask app to review ranked candidates, see WHY each scored what it
did, read the generated email draft, and approve / reject. Nothing is ever sent
from here — approving just marks a draft so you can export and send it yourself.

Run:  python app.py   →   http://127.0.0.1:5000
"""
from __future__ import annotations

import csv
import io
import json

from flask import Flask, redirect, render_template, request, url_for, Response

from recruiter import db
from recruiter.config import load_config

app = Flask(__name__)


@app.route("/")
def index():
    min_score = request.args.get("min_score", 0, type=int)
    with db.connect() as conn:
        rows = [dict(r) for r in db.ranked(conn, min_score=min_score)]
    for r in rows:
        r["breakdown"] = json.loads(r["breakdown"]) if r.get("breakdown") else None
    cfg = load_config()
    stats = {
        "total": len(rows),
        "approved": sum(1 for r in rows if r["draft_status"] == "approved"),
        "target": cfg["target"],
    }
    return render_template("index.html", rows=rows, stats=stats, min_score=min_score)


@app.route("/candidate/<int:cid>")
def candidate(cid):
    with db.connect() as conn:
        row = db.get_candidate(conn, cid)
    if not row:
        return "Not found", 404
    cand = dict(row)
    cand["breakdown"] = json.loads(cand["breakdown"]) if cand.get("breakdown") else None
    return render_template("candidate.html", c=cand)


@app.route("/candidate/<int:cid>/status", methods=["POST"])
def set_status(cid):
    status = request.form.get("status", "pending")
    with db.connect() as conn:
        db.set_draft_status(conn, cid, status)
    return redirect(request.referrer or url_for("index"))


@app.route("/export/approved.csv")
def export_approved():
    """Download approved candidates + drafts as CSV for your mail merge."""
    with db.connect() as conn:
        rows = [dict(r) for r in db.ranked(conn)]
    rows = [r for r in rows if r["draft_status"] == "approved"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "email", "score", "subject", "body"])
    for r in rows:
        w.writerow([r["name"], r.get("email") or "", r.get("score") or 0,
                    r.get("draft_subject") or "", r.get("draft_body") or ""])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=approved.csv"},
    )


if __name__ == "__main__":
    db.init_db()
    app.run(debug=True, port=5000)
