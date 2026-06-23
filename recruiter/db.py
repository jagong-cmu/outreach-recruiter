"""SQLite storage for candidates, scores, and email drafts.

A single normalized store sits at the center of the system. Every data source
(LinkedIn scraper, CSV import, opt-in form, sample data) writes Candidate rows
here; scoring and the dashboard read from here. This is what makes the pipeline
source-agnostic.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

DB_PATH = os.environ.get(
    "RECRUITER_DB",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "recruiter.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    headline     TEXT,
    location     TEXT,
    profile_url  TEXT UNIQUE,
    email        TEXT,
    grad_year    INTEGER,
    school       TEXT,
    education    TEXT,            -- structured education entries (one per line)
    verified     TEXT,            -- verification status (see verification.py)
    experience   TEXT,            -- human-readable summary of prior experience
    raw_text     TEXT,            -- concatenated about/experience/education
    source       TEXT,            -- linkedin | csv | form | sample
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scores (
    candidate_id INTEGER PRIMARY KEY REFERENCES candidates(id) ON DELETE CASCADE,
    total        INTEGER NOT NULL,
    breakdown    TEXT,            -- JSON: per-category points + matched keywords
    scored_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS drafts (
    candidate_id INTEGER PRIMARY KEY REFERENCES candidates(id) ON DELETE CASCADE,
    subject      TEXT,
    body         TEXT,
    status       TEXT DEFAULT 'pending',   -- pending | approved | rejected | sent
    created_at   TEXT DEFAULT (datetime('now'))
);
"""


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


def upsert_candidate(conn: sqlite3.Connection, c: dict) -> int:
    """Insert a candidate (or update if profile_url already seen). Returns id."""
    cols = ("name", "headline", "location", "profile_url", "email",
            "grad_year", "school", "education", "experience", "raw_text", "source")
    row = {k: c.get(k) for k in cols}
    cur = conn.execute(
        """
        INSERT INTO candidates (name, headline, location, profile_url, email,
                                grad_year, school, education, experience, raw_text, source)
        VALUES (:name, :headline, :location, :profile_url, :email,
                :grad_year, :school, :education, :experience, :raw_text, :source)
        ON CONFLICT(profile_url) DO UPDATE SET
            name=excluded.name, headline=excluded.headline,
            location=excluded.location, email=COALESCE(excluded.email, candidates.email),
            grad_year=excluded.grad_year, school=excluded.school,
            education=excluded.education, experience=excluded.experience,
            raw_text=excluded.raw_text, source=excluded.source
        """,
        row,
    )
    if cur.lastrowid:
        return cur.lastrowid
    got = conn.execute(
        "SELECT id FROM candidates WHERE profile_url = ?", (row["profile_url"],)
    ).fetchone()
    return got["id"]


def update_verification(conn: sqlite3.Connection, candidate_id: int, status: str,
                        grad_year: Optional[int], school: Optional[str]) -> None:
    """Store a verification verdict. grad_year/school only overwrite when the
    verifier actually found a value (COALESCE keeps imported data otherwise)."""
    conn.execute(
        """UPDATE candidates
           SET verified = ?,
               grad_year = COALESCE(?, grad_year),
               school = COALESCE(?, school)
           WHERE id = ?""",
        (status, grad_year, school, candidate_id),
    )


def save_score(conn: sqlite3.Connection, candidate_id: int, total: int,
               breakdown: dict) -> None:
    conn.execute(
        """INSERT INTO scores (candidate_id, total, breakdown)
           VALUES (?, ?, ?)
           ON CONFLICT(candidate_id) DO UPDATE SET
             total=excluded.total, breakdown=excluded.breakdown,
             scored_at=datetime('now')""",
        (candidate_id, total, json.dumps(breakdown)),
    )


def save_draft(conn: sqlite3.Connection, candidate_id: int, subject: str,
               body: str) -> None:
    conn.execute(
        """INSERT INTO drafts (candidate_id, subject, body)
           VALUES (?, ?, ?)
           ON CONFLICT(candidate_id) DO UPDATE SET
             subject=excluded.subject, body=excluded.body""",
        (candidate_id, subject, body),
    )


def set_draft_status(conn: sqlite3.Connection, candidate_id: int,
                     status: str) -> None:
    conn.execute("UPDATE drafts SET status=? WHERE candidate_id=?",
                 (status, candidate_id))


def all_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM candidates").fetchall()


def ranked(conn: sqlite3.Connection, min_score: int = 0) -> list[sqlite3.Row]:
    """Candidates joined with scores + draft status, best first."""
    return conn.execute(
        """
        SELECT c.*, s.total AS score, s.breakdown AS breakdown,
               d.subject AS draft_subject, d.body AS draft_body,
               COALESCE(d.status, 'none') AS draft_status
        FROM candidates c
        LEFT JOIN scores s ON s.candidate_id = c.id
        LEFT JOIN drafts d ON d.candidate_id = c.id
        WHERE COALESCE(s.total, 0) >= ?
        ORDER BY COALESCE(s.total, 0) DESC, c.name ASC
        """,
        (min_score,),
    ).fetchall()


def get_candidate(conn: sqlite3.Connection, candidate_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT c.*, s.total AS score, s.breakdown AS breakdown,
               d.subject AS draft_subject, d.body AS draft_body,
               COALESCE(d.status, 'none') AS draft_status
        FROM candidates c
        LEFT JOIN scores s ON s.candidate_id = c.id
        LEFT JOIN drafts d ON d.candidate_id = c.id
        WHERE c.id = ?
        """,
        (candidate_id,),
    ).fetchone()
