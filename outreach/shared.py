"""A shared queue and claim list, so two senders never contact the same person.

Each sender keeps a local SQLite database. This module adds one shared database
that all senders use. It holds two tables:

  queue     the prospect list. `sync` and the search push to it.
  claims    one row for each person that a sender contacts or is about to contact.
  searches  one row for each LinkedIn search term, with the sender and the time.

The sender claims a person before it opens the profile. The claim is one atomic
INSERT on a unique key, so only one sender can win it. The keys are the
"name|firm" key and the LinkedIn profile URL, so a different firm spelling on the
other list still matches after the profile search.

A claim is released only when the run skipped the person before Send. A claim
that is still "sending" after a crash stays blocked. A blocked person costs less
than a second invite.

Set `shared.database_url` and `shared.sender` in config.local.yaml. A Postgres URL
(Neon, Supabase) works for two computers. A `sqlite:///path` URL works for tests.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.parse
from contextlib import contextmanager
from typing import Iterable, Iterator, Optional

from . import db

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS claims (
        key        TEXT PRIMARY KEY,
        sender     TEXT NOT NULL,
        status     TEXT NOT NULL,
        name       TEXT,
        firm       TEXT,
        url        TEXT,
        claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS searches (
        key        TEXT PRIMARY KEY,
        sender     TEXT NOT NULL,
        status     TEXT NOT NULL,
        found      INTEGER DEFAULT 0,
        ran_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS queue (
        dedupe_key TEXT PRIMARY KEY,
        data       TEXT NOT NULL,
        added_by   TEXT,
        added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
)


class SharedError(Exception):
    """The shared database is set but not usable. The sender must not go on."""


def settings(cfg: dict) -> Optional[dict]:
    """Return {url, sender}, or None when no shared database is set."""
    shared = cfg.get("shared") or {}
    url = (os.environ.get("OUTREACH_SHARED_URL") or shared.get("database_url") or "").strip()
    if not url:
        return None
    sender = (shared.get("sender") or "").strip().lower()
    if not sender:
        raise SharedError("Set shared.sender in config.local.yaml, for example 'jonathan'.")
    return {"url": url, "sender": sender}


@contextmanager
def connect(url: str) -> Iterator["_Conn"]:
    if url.startswith("sqlite:///"):
        raw = sqlite3.connect(url[len("sqlite:///"):])
        mark = "?"
    else:
        try:
            import psycopg
        except ImportError as err:
            raise SharedError("Run: .venv/bin/pip install -r requirements.txt") from err
        try:
            raw = psycopg.connect(url, connect_timeout=15)
        except Exception as err:
            raise SharedError(f"cannot reach the shared database: {err}") from err
        mark = "%s"
    conn = _Conn(raw, mark)
    try:
        for stmt in SCHEMA:
            conn.run(stmt)
        yield conn
        raw.commit()
    finally:
        raw.close()


class _Conn:
    """A thin wrapper. The SQL here uses `?`, and Postgres gets `%s`."""

    def __init__(self, raw, mark: str):
        self.raw, self.mark = raw, mark

    def run(self, sql: str, args: Iterable = ()) -> list[tuple]:
        cur = self.raw.cursor()
        cur.execute(sql.replace("?", self.mark), tuple(args))
        rows = cur.fetchall() if cur.description else []
        cur.close()
        return rows

    def commit(self) -> None:
        self.raw.commit()


def url_key(url: str) -> Optional[str]:
    """Return 'url:/in/<vanity>' for a LinkedIn profile URL, or None."""
    path = urllib.parse.unquote(urllib.parse.urlparse(url or "").path).lower()
    m = re.search(r"/in/([^/]+)", path)
    return f"url:/in/{m.group(1)}" if m else None


def person_keys(person: dict, url: Optional[str] = None) -> list[str]:
    keys = ["name:" + db.dedupe_key(person.get("name", ""), person.get("firm") or "")]
    k = url_key(url or person.get("linkedin_url") or "")
    if k:
        keys.append(k)
    return keys


def claim(conn: _Conn, sender: str, person: dict, url: Optional[str] = None) -> Optional[str]:
    """Claim every key of this person. Return None on success, or the other sender.

    The insert is atomic. A key that this sender already holds counts as a win,
    so a retry after a browser error still works.
    """
    for key in person_keys(person, url):
        rows = conn.run(
            """INSERT INTO claims (key, sender, status, name, firm, url)
               VALUES (?, ?, 'sending', ?, ?, ?)
               ON CONFLICT (key) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
               WHERE claims.sender = excluded.sender
               RETURNING sender""",
            (key, sender, person.get("name"), person.get("firm"), url))
        conn.commit()
        if not rows:
            owner = conn.run("SELECT sender FROM claims WHERE key = ?", (key,))
            return owner[0][0] if owner else "another sender"
    return None


def mark_sent(conn: _Conn, sender: str, person: dict, url: Optional[str] = None) -> None:
    for key in person_keys(person, url):
        conn.run(
            """INSERT INTO claims (key, sender, status, name, firm, url)
               VALUES (?, ?, 'sent', ?, ?, ?)
               ON CONFLICT (key) DO UPDATE SET status = 'sent',
                 url = COALESCE(excluded.url, claims.url), updated_at = CURRENT_TIMESTAMP
               WHERE claims.sender = excluded.sender""",
            (key, sender, person.get("name"), person.get("firm"), url))
    conn.commit()


def release(conn: _Conn, sender: str, person: dict, url: Optional[str] = None) -> None:
    """Free the claims of a person that this run did not send to."""
    for key in person_keys(person, url):
        conn.run("DELETE FROM claims WHERE key = ? AND sender = ? AND status = 'sending'",
                 (key, sender))
    conn.commit()


def taken_by_others(conn: _Conn, sender: str) -> set[str]:
    return {r[0] for r in conn.run("SELECT key FROM claims WHERE sender <> ?", (sender,))}


def push(conn: _Conn, sender: str, rows: list[dict], log_rows: list[dict]) -> dict:
    """Send local prospects to the queue, and claim everyone this sender contacted."""
    queued = claimed = 0
    for p in rows:
        data = {k: p.get(k) for k in db.FIELDS}
        conn.run(
            """INSERT INTO queue (dedupe_key, data, added_by) VALUES (?, ?, ?)
               ON CONFLICT (dedupe_key) DO NOTHING""",
            (p["dedupe_key"], json.dumps(data), sender))
        queued += 1
        if p.get("stage") and p["stage"] != "new":
            for key in person_keys(p):
                got = conn.run(
                    """INSERT INTO claims (key, sender, status, name, firm, url)
                       VALUES (?, ?, 'sent', ?, ?, ?) ON CONFLICT (key) DO NOTHING
                       RETURNING key""",
                    (key, sender, p.get("name"), p.get("firm"), p.get("linkedin_url")))
                claimed += len(got)
    # The send log also has profile URLs. They catch a person under another name.
    for r in log_rows:
        key = url_key(r.get("url") or "")
        if r.get("result") == "sent" and key:
            got = conn.run(
                """INSERT INTO claims (key, sender, status, name, firm, url)
                   VALUES (?, ?, 'sent', ?, ?, ?) ON CONFLICT (key) DO NOTHING
                   RETURNING key""",
                (key, sender, r.get("name"), r.get("firm"), r.get("url")))
            claimed += len(got)
    conn.commit()
    return {"queued": queued, "claimed": claimed}


def pull(conn: _Conn) -> list[dict]:
    """Return every prospect in the shared queue."""
    return [json.loads(r[0]) for r in conn.run("SELECT data FROM queue")]


def counts(conn: _Conn) -> dict:
    # The pattern is a parameter. Postgres reads a bare "%" as a placeholder.
    rows = conn.run("SELECT sender, status, COUNT(*) FROM claims "
                    "WHERE key LIKE ? GROUP BY sender, status", ("name:%",))
    queue = conn.run("SELECT COUNT(*) FROM queue")[0][0]
    return {"queue": queue, "claims": [(s, st, n) for s, st, n in rows]}


def _stamp(dt_value) -> str:
    """The text form that both SQLite and Postgres compare as a time (UTC)."""
    return dt_value.strftime("%Y-%m-%d %H:%M:%S")


def claim_search(conn: _Conn, sender: str, key: str, rerun_days: float) -> Optional[str]:
    """Claim one search term. Return None on success, or the sender that ran it.

    A term that any sender ran in the last `rerun_days` days stays taken. One
    atomic statement decides, so two senders cannot both win the same term.
    """
    import datetime as dt
    cutoff = _stamp(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=rerun_days))
    now = _stamp(dt.datetime.now(dt.timezone.utc))
    rows = conn.run(
        """INSERT INTO searches (key, sender, status, ran_at) VALUES (?, ?, 'running', ?)
           ON CONFLICT (key) DO UPDATE SET sender = excluded.sender,
             status = 'running', found = 0, ran_at = excluded.ran_at
           WHERE searches.ran_at < ?
           RETURNING sender""",
        (key, sender, now, cutoff))
    conn.commit()
    if rows:
        return None
    owner = conn.run("SELECT sender FROM searches WHERE key = ?", (key,))
    return owner[0][0] if owner else "another sender"


def finish_search(conn: _Conn, sender: str, key: str, found: int) -> None:
    conn.run("UPDATE searches SET status = 'done', found = ? WHERE key = ? AND sender = ?",
             (found, key, sender))
    conn.commit()


def release_search(conn: _Conn, sender: str, key: str) -> None:
    """Free a term that this sender could not finish, so it can run again."""
    conn.run("DELETE FROM searches WHERE key = ? AND sender = ? AND status = 'running'",
             (key, sender))
    conn.commit()


def searches_today(conn: _Conn, sender: str) -> int:
    import datetime as dt
    since = _stamp(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
    return conn.run("SELECT COUNT(*) FROM searches WHERE sender = ? AND ran_at >= ?",
                    (sender, since))[0][0]


def known_profile_keys(conn: _Conn) -> set[str]:
    """Profile keys of everyone in the queue or the claim list."""
    keys = {r[0] for r in conn.run("SELECT key FROM claims WHERE key LIKE ?", ("url:%",))}
    for (data,) in conn.run("SELECT data FROM queue"):
        k = url_key(json.loads(data).get("linkedin_url") or "")
        if k:
            keys.add(k)
    return keys


def add_to_queue(conn: _Conn, sender: str, person: dict) -> bool:
    """Add one prospect to the queue. Return False when it is there already."""
    data = {k: person.get(k) for k in db.FIELDS}
    key = db.dedupe_key(person["name"], person.get("firm") or "")
    got = conn.run("""INSERT INTO queue (dedupe_key, data, added_by) VALUES (?, ?, ?)
                      ON CONFLICT (dedupe_key) DO NOTHING RETURNING dedupe_key""",
                   (key, json.dumps(data), sender))
    conn.commit()
    return bool(got)
