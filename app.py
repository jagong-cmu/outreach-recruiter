#!/usr/bin/env python3
"""GPU appraisal outreach: local dashboard.

Review ranked prospects, copy a message, send it yourself, then record what
happened. Nothing is sent from here.

Run:  python app.py   ->   http://127.0.0.1:5000
"""
from __future__ import annotations

import csv
import io
import json

from flask import Flask, Response, abort, redirect, render_template, request, url_for

from outreach import db, messages, qualify, scoring
from outreach.config import load_config, missing_identity

app = Flask(__name__)


@app.route("/")
def index():
    min_score = request.args.get("min_score", 0, type=int)
    stage = request.args.get("stage") or None
    segment = request.args.get("segment") or None
    cfg = load_config()
    with db.connect() as conn:
        rows = [dict(r) for r in db.ranked(conn, min_score, stage, segment)]
        enabled = cfg["templates"].get("enabled_channels")
        due = ([] if enabled and "followup" not in enabled else
               [dict(r) for r in db.followups_due(conn, cfg["pipeline"]["followup_after_days"])])
        counts = db.stage_counts(conn)
        segments = sorted({r["segment"] for r in db.all_prospects(conn) if r["segment"]})
    for r in rows:
        r["channel"] = messages.best_channel(r)
    return render_template(
        "index.html", rows=rows, due=due, counts=counts, stages=db.STAGES,
        segments=segments, min_score=min_score, stage=stage, segment=segment,
        missing=missing_identity(cfg), labels=messages.CHANNEL_LABELS,
    )


@app.route("/prospect/<int:pid>")
def prospect(pid):
    cfg = load_config()
    with db.connect() as conn:
        row = db.get_prospect(conn, pid)
        if not row:
            abort(404)
    p = dict(row)
    p["breakdown"] = json.loads(p["breakdown"]) if p.get("breakdown") else None
    # Drafts render on each view, so "in two days" stays correct.
    msgs = [{**m, "label": messages.CHANNEL_LABELS[m["channel"]]}
            for m in messages.generate(p, cfg)]
    msgs.sort(key=lambda m: list(messages.CHANNEL_LABELS).index(m["channel"]))
    return render_template(
        "prospect.html", p=p, msgs=msgs, verdict=qualify.qualify(p), topic=messages.topic_for(p, cfg),
        best=messages.best_channel(p), links=messages.search_links(p),
        stages=db.STAGES, questions=cfg["discovery_questions"],
    )


@app.post("/prospect/<int:pid>/stage")
def set_stage(pid):
    stage = request.form.get("stage", "")
    if stage not in db.STAGES:
        abort(400)
    with db.connect() as conn:
        db.set_stage(conn, pid, stage, request.form.get("channel") or None)
    return redirect(url_for("prospect", pid=pid))


@app.post("/prospect/<int:pid>/followed-up")
def followed_up(pid):
    with db.connect() as conn:
        db.mark_followed_up(conn, pid)
    return redirect(url_for("index"))


@app.post("/prospect/<int:pid>/notes")
def save_notes(pid):
    with db.connect() as conn:
        db.set_notes(conn, pid, request.form.get("notes", ""))
    return redirect(url_for("prospect", pid=pid))


@app.post("/prospect/<int:pid>/personalize")
def personalize(pid):
    with db.connect() as conn:
        db.set_personalization(conn, pid, request.form.get("opener", ""),
                               request.form.get("topic", ""))
    return redirect(url_for("prospect", pid=pid))


@app.route("/alumni")
def alumni():
    cfg = load_config()
    searches = cfg["alumni_searches"]
    build = lambda items: [(k, messages.alumni_search_url(k)) for k in items]
    with db.connect() as conn:
        rows = [dict(r) for r in db.ranked(conn) if r["cmu_link"]]
    return render_template("alumni.html", roles=build(searches["roles"]), rows=rows,
                           firms=build(searches["firms"]), segments=cfg["scoring"]["segments"])


@app.post("/prospect/add")
def add_prospect():
    form = request.form
    name, url = form.get("name", "").strip(), form.get("linkedin_url", "").strip()
    if not name or not url.startswith("http"):
        abort(400)
    cfg = load_config()
    person = {"name": name, "title": form.get("title", "").strip(),
              "firm": form.get("firm", "").strip(), "segment": form.get("segment", ""),
              "linkedin_url": url, "source_url": url, "confidence": "high",
              "cmu_link": True,
              "why_relevant": "CMU alum that you found on LinkedIn."}
    with db.connect() as conn:
        pid = db.upsert_prospect(conn, person)
        row = dict(db.get_prospect(conn, pid))
        verdict = qualify.qualify(row, cfg)
        db.set_fit(conn, pid, verdict["fit"])
        if verdict["fit"] in qualify.PASSING:
            result = scoring.score_prospect(row, cfg)
            db.save_score(conn, pid, result["total"], result["breakdown"])
    return redirect(url_for("prospect", pid=pid))


@app.route("/export/pipeline.csv")
def export_pipeline():
    with db.connect() as conn:
        rows = [dict(r) for r in db.ranked(conn)]
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["name", "title", "firm", "segment", "score", "stage", "sent_channel",
            "sent_at", "email", "x_handle", "linkedin_url", "notes"]
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c) if r.get(c) is not None else "" for c in cols])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=pipeline.csv"})


if __name__ == "__main__":
    db.init_db()
    app.run(debug=False, port=5000)
