"""SQLite store for prospects, scores, messages and the outreach pipeline.

Every source (research JSON, CSV) writes prospect rows here. Qualification,
scoring, message generation and the dashboard all read from here.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

DB_PATH = os.environ.get(
    "OUTREACH_DB",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "outreach.db"),
)

# Pipeline order. "passed" means the person declined or is not relevant.
STAGES = ("new", "sent", "replied", "booked", "held", "passed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key    TEXT UNIQUE NOT NULL,   -- lower-case "name|firm"
    name          TEXT NOT NULL,
    title         TEXT,
    firm          TEXT,
    segment       TEXT,
    why_relevant  TEXT,
    hook          TEXT,                   -- public fact, with its source
    opener        TEXT,                   -- short sentence used in messages
    linkedin_url  TEXT,
    x_handle      TEXT,
    email         TEXT,
    source_url    TEXT,
    source_date   TEXT,
    confidence    TEXT,
    cmu_link      INTEGER DEFAULT 0,
    fit           TEXT,                   -- see qualify.py
    stage         TEXT DEFAULT 'new',
    sent_channel  TEXT,
    sent_at       TEXT,
    followed_up_at TEXT,
    notes         TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scores (
    prospect_id INTEGER PRIMARY KEY REFERENCES prospects(id) ON DELETE CASCADE,
    total       INTEGER NOT NULL,
    breakdown   TEXT,
    scored_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL,
    subject     TEXT,
    body        TEXT,
    PRIMARY KEY (prospect_id, channel)
);
"""

FIELDS = ("name", "title", "firm", "segment", "why_relevant", "hook", "opener",
          "linkedin_url", "x_handle", "email", "source_url", "source_date",
          "confidence", "cmu_link")


@contextmanager
def connect(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: str | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def dedupe_key(name: str, firm: str) -> str:
    return f"{' '.join((name or '').lower().split())}|{' '.join((firm or '').lower().split())}"


def upsert_prospect(conn: sqlite3.Connection, p: dict) -> int:
    """Insert a prospect, or refresh its research fields.

    A re-import never changes stage, notes or send history. An empty incoming
    field never erases a stored value.
    """
    row = {k: p.get(k) or None for k in FIELDS}
    row["cmu_link"] = 1 if p.get("cmu_link") else 0
    row["dedupe_key"] = dedupe_key(p["name"], p.get("firm", ""))
    updates = ", ".join(f"{k}=COALESCE(excluded.{k}, prospects.{k})"
                        for k in FIELDS if k != "cmu_link")
    conn.execute(
        f"""INSERT INTO prospects (dedupe_key, {', '.join(FIELDS)})
            VALUES (:dedupe_key, {', '.join(':' + k for k in FIELDS)})
            ON CONFLICT(dedupe_key) DO UPDATE SET {updates},
              cmu_link=MAX(excluded.cmu_link, prospects.cmu_link)""",
        row,
    )
    got = conn.execute("SELECT id FROM prospects WHERE dedupe_key = ?",
                       (row["dedupe_key"],)).fetchone()
    return got["id"]


def set_fit(conn: sqlite3.Connection, prospect_id: int, fit: str) -> None:
    conn.execute("UPDATE prospects SET fit=? WHERE id=?", (fit, prospect_id))


def save_score(conn: sqlite3.Connection, prospect_id: int, total: int,
               breakdown: dict) -> None:
    conn.execute(
        """INSERT INTO scores (prospect_id, total, breakdown) VALUES (?, ?, ?)
           ON CONFLICT(prospect_id) DO UPDATE SET total=excluded.total,
             breakdown=excluded.breakdown, scored_at=datetime('now')""",
        (prospect_id, total, json.dumps(breakdown)),
    )


def delete_score(conn: sqlite3.Connection, prospect_id: int) -> None:
    conn.execute("DELETE FROM scores WHERE prospect_id=?", (prospect_id,))


def save_message(conn: sqlite3.Connection, prospect_id: int, channel: str,
                 subject: str, body: str) -> None:
    conn.execute(
        """INSERT INTO messages (prospect_id, channel, subject, body)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(prospect_id, channel) DO UPDATE SET
             subject=excluded.subject, body=excluded.body""",
        (prospect_id, channel, subject, body),
    )


def delete_messages(conn: sqlite3.Connection, prospect_id: int) -> None:
    conn.execute("DELETE FROM messages WHERE prospect_id=?", (prospect_id,))


def get_messages(conn: sqlite3.Connection, prospect_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM messages WHERE prospect_id=?",
                        (prospect_id,)).fetchall()


def set_opener(conn: sqlite3.Connection, prospect_id: int, opener: str) -> None:
    conn.execute("UPDATE prospects SET opener=? WHERE id=?",
                 (opener.strip() or None, prospect_id))


def set_notes(conn: sqlite3.Connection, prospect_id: int, notes: str) -> None:
    conn.execute("UPDATE prospects SET notes=? WHERE id=?", (notes, prospect_id))


def set_stage(conn: sqlite3.Connection, prospect_id: int, stage: str,
              channel: Optional[str] = None) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    if stage == "sent":
        conn.execute(
            """UPDATE prospects SET stage='sent', sent_channel=?,
               sent_at=COALESCE(sent_at, datetime('now')) WHERE id=?""",
            (channel, prospect_id),
        )
    else:
        conn.execute("UPDATE prospects SET stage=? WHERE id=?", (stage, prospect_id))


def mark_followed_up(conn: sqlite3.Connection, prospect_id: int) -> None:
    conn.execute("UPDATE prospects SET followed_up_at=datetime('now') WHERE id=?",
                 (prospect_id,))


def all_prospects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM prospects").fetchall()


_JOIN = """
    SELECT p.*, s.total AS score, s.breakdown AS breakdown,
      (julianday('now') - julianday(p.sent_at)) AS days_since_sent
    FROM prospects p LEFT JOIN scores s ON s.prospect_id = p.id
"""


def ranked(conn: sqlite3.Connection, min_score: int = 0,
           stage: str | None = None, segment: str | None = None) -> list[sqlite3.Row]:
    """Scored prospects, best first."""
    sql = _JOIN + " WHERE s.total IS NOT NULL AND s.total >= ?"
    args: list = [min_score]
    if stage:
        sql += " AND p.stage = ?"
        args.append(stage)
    if segment:
        sql += " AND p.segment = ?"
        args.append(segment)
    return conn.execute(sql + " ORDER BY s.total DESC, p.name ASC", args).fetchall()


def get_prospect(conn: sqlite3.Connection, prospect_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(_JOIN + " WHERE p.id = ?", (prospect_id,)).fetchone()


def followups_due(conn: sqlite3.Connection, after_days: float) -> list[sqlite3.Row]:
    return conn.execute(
        _JOIN + """ WHERE p.stage='sent' AND p.followed_up_at IS NULL
                    AND (julianday('now') - julianday(p.sent_at)) >= ?
                    ORDER BY s.total DESC""",
        (after_days,),
    ).fetchall()


def stage_counts(conn: sqlite3.Connection) -> dict:
    counts = {s: 0 for s in STAGES}
    for row in conn.execute("SELECT stage, COUNT(*) n FROM prospects GROUP BY stage"):
        counts[row["stage"]] = row["n"]
    counts["sent_today"] = conn.execute(
        "SELECT COUNT(*) n FROM prospects WHERE date(sent_at)=date('now')"
    ).fetchone()["n"]
    return counts
