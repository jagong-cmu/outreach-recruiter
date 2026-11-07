"""Read-only X API research. This module never posts, follows or sends a DM.

It finds people by stakeholder group and writes them to a review file. It does
not touch the prospect database, because the LinkedIn sender reads that database.

Each call costs money, so three controls apply:
  - x_search.max_usd stops the run before a page could pass the session budget.
  - A local cache stops repeat charges for the same query.
  - Post search asks only for posts newer than the last run of that query.
  - A failed call stops that search type and keeps the results so far.

Post search uses the app Bearer Token. User search needs a user login, so it
signs each call with OAuth 1.0a. Both only read. No write call exists here.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

API = "https://api.x.com/2"
USER_FIELDS = "description,location,url,public_metrics,verified"
ROOT = os.path.dirname(os.path.dirname(__file__))
CACHE_PATH = os.path.join(ROOT, "data", "x_cache.json")
OUT_PATH = os.path.join(ROOT, "data", "x_candidates.json")
DNC_PATH = os.path.join(ROOT, "data", "do_not_contact.csv")
ENV_PATH = os.path.join(ROOT, "data", "x.env")    # Private. Never print this file.

_CMU = re.compile(r"\bcmu\b|carnegie mellon|tepper school", re.IGNORECASE)
# "YC" here means the firm. A batch tag or "YC-backed" means a YC company.
_YC_FIRM = (r"(?:y\s*combinator|ycombinator|yc)\b"
            r"(?![\s-]*(?:[wsfx]\d{2}|backed|alum|founder|company|startup))")
_YC_ANY = re.compile(r"\b(?:yc|y\s*combinator|ycombinator)\b", re.IGNORECASE)
_YC_PARTNER = re.compile(
    r"\bgroup partner\b.*\b" + _YC_FIRM
    + r"|\b" + _YC_FIRM + r".*\bgroup partner\b"
    + r"|\bpartner\s*(?:at|of|@|,|-|–|\|)?\s*@?" + _YC_FIRM
    + r"|\b" + _YC_FIRM + r"\s+(?:visiting\s+)?partner\b", re.IGNORECASE)
_BACKED_BY = re.compile(r"\b(?:backed|funded)\s+by\b[^.\n|]*", re.IGNORECASE)
_YC_EMPLOYEE = re.compile(r"(?:\bat\s+|@\s*)" + _YC_FIRM, re.IGNORECASE)
_PRESS = re.compile(r"journalist|reporter|correspondent|staff writer", re.IGNORECASE)


USD_POST = 0.005      # X's published pay-per-use rates, per record returned.
USD_USER = 0.010
SINCE_ID_DAYS = 6     # Recent search refuses a since_id older than seven days.


class XError(RuntimeError):
    """A call failed. The run stops and keeps what it has."""


OAUTH_ENV = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")


def load_env(path: Optional[str] = None) -> None:
    """Load X_* credentials from the private file. A shell value wins."""
    path = path or ENV_PATH
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            name, sep, value = line.strip().partition("=")
            if sep and name.startswith("X_") and value:
                os.environ.setdefault(name, value)


def _pct(value: str) -> str:
    return urllib.parse.quote(str(value), safe="~")


def oauth1_signature(method: str, url: str, params: dict, consumer_secret: str,
                     token_secret: str) -> str:
    """Return the HMAC-SHA1 signature that OAuth 1.0a defines."""
    pairs = "&".join(f"{k}={v}" for k, v in sorted(
        (_pct(k), _pct(v)) for k, v in params.items()))
    base = "&".join((method.upper(), _pct(url), _pct(pairs)))
    key = f"{_pct(consumer_secret)}&{_pct(token_secret)}"
    digest = hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def oauth1_header(url: str, keys: dict) -> str:
    """Build the Authorization header for one signed GET request."""
    base, _, query = url.partition("?")
    oauth = {
        "oauth_consumer_key": keys["X_API_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": keys["X_ACCESS_TOKEN"],
        "oauth_version": "1.0",
    }
    params = {**dict(urllib.parse.parse_qsl(query, keep_blank_values=True)), **oauth}
    oauth["oauth_signature"] = oauth1_signature(
        "GET", base, params, keys["X_API_SECRET"], keys["X_ACCESS_TOKEN_SECRET"])
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


class Client:
    """A small GET client with a spend limit and a disk cache."""

    def __init__(self, token: str, max_resources: int, cache_path: Optional[str] = None,
                 fetch: Optional[Callable[[str], dict]] = None,
                 oauth: Optional[dict] = None, max_usd: Optional[float] = None,
                 min_gap_hours: float = 0):
        self.token = token
        self.oauth = oauth
        self.max_resources = max_resources
        self.cache_path = cache_path = cache_path or CACHE_PATH
        self.used = 0
        self._fetch = fetch or self._http
        self.max_usd = max_usd
        self.min_gap_hours = min_gap_hours
        self.spent = 0.0
        self.cache: dict = {}
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as fh:
                self.cache = json.load(fh)
        self.queries_path = cache_path + ".queries"
        self.queries: dict = {}
        if os.path.exists(self.queries_path):
            with open(self.queries_path, encoding="utf-8") as fh:
                self.queries = json.load(fh)

    def _http(self, url: str) -> dict:
        auth = (oauth1_header(url, self.oauth) if self.oauth and "/users/search" in url
                else f"Bearer {self.token}")
        req = urllib.request.Request(url, headers={
            "Authorization": auth, "User-Agent": "outreach-research"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "replace")[:200]
            raise XError(f"X API returned {err.code}: {body}") from err
        except urllib.error.URLError as err:
            raise XError(f"X API call failed: {err.reason}") from err

    def get(self, path: str, params: dict) -> dict:
        url = f"{API}{path}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
        if url in self.cache:
            return self.cache[url]
        if self.used >= self.max_resources:
            raise XError(f"Stopped at the limit of {self.max_resources} records.")
        posts = "/tweets/" in path
        worst = params.get("max_results", 0) * (USD_POST + USD_USER if posts else USD_USER)
        if self.max_usd is not None and self.spent + worst > self.max_usd:
            raise XError(f"Stopped before the ${self.max_usd:.2f} budget. "
                         f"Estimated spend: ${self.spent:.2f}.")
        data = self._fetch(url)
        count = len(data.get("data") or [])
        self.used += count
        # X does not say if authors returned with posts are billed, so count them.
        authors = len((data.get("includes") or {}).get("users") or [])
        self.spent += count * USD_POST + authors * USD_USER if posts else count * USD_USER
        print(f"[${self.spent:.2f}] +{count} {path} {params.get('query', '')[:60]!r}", flush=True)
        self.cache[url] = data
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as fh:
            json.dump(self.cache, fh)
        return data

    def search_users(self, query: str, size: int) -> list[dict]:
        return self.get("/users/search", {
            "query": query, "max_results": size, "user.fields": USER_FIELDS,
        }).get("data") or []

    def search_users_page(self, query: str, size: int,
                          token: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
        """Return one page of users and the token for the next page."""
        params = {"query": query, "max_results": size, "user.fields": USER_FIELDS}
        if token:
            params["next_token"] = token
        data = self.get("/users/search", params)
        return data.get("data") or [], (data.get("meta") or {}).get("next_token")

    def search_posts(self, query: str, size: int) -> list[tuple[dict, dict]]:
        """Return (author, post) pairs. Retweets and other languages are left out."""
        state = self.queries.get(query) or {}
        age = time.time() - state.get("last_run", 0)
        if age < self.min_gap_hours * 3600:
            return []
        params = {
            "query": f"({query}) -is:retweet lang:en",
            "max_results": max(10, size),       # The endpoint minimum is 10.
            "tweet.fields": "created_at,author_id",
            "expansions": "author_id",
            "user.fields": USER_FIELDS,
        }
        if state.get("since_id") and age < SINCE_ID_DAYS * 86400:
            params["since_id"] = state["since_id"]
        data = self.get("/tweets/search/recent", params)
        newest = (data.get("meta") or {}).get("newest_id")
        self.queries[query] = {"since_id": newest or state.get("since_id"),
                               "last_run": time.time()}
        with open(self.queries_path, "w", encoding="utf-8") as fh:
            json.dump(self.queries, fh)
        users = {u["id"]: u for u in (data.get("includes") or {}).get("users") or []}
        return [(users[p["author_id"]], p) for p in data.get("data") or []
                if p.get("author_id") in users]


def block_reason(user: dict, known_handles: set[str], known_names: set[str]) -> Optional[str]:
    """Return why this account must not be contacted, or None.

    A YC partner, past or present, is never a lead. A person who works at YC
    itself is held for review. A founder or employee of a YC company is a lead,
    and the word "partner" in their bio does not change that.
    """
    bio = user.get("description") or ""
    if user.get("username", "").lower() in known_handles:
        return "already in the prospect list"
    if (user.get("name") or "").strip().lower() in known_names:
        return "name is on the do-not-contact list or the prospect list"
    if _PRESS.search(bio):
        return "press"
    if _YC_PARTNER.search(bio):
        return "YC partner"
    if _YC_EMPLOYEE.search(_BACKED_BY.sub("", bio)):
        return "works at YC itself"
    return None


def to_candidate(user: dict, segment: str) -> dict:
    """Use the research JSON field names, so a reviewed file can be imported."""
    bio = (user.get("description") or "").strip()
    handle = user.get("username", "")
    return {
        "name": (user.get("name") or handle).strip(),
        "title": bio[:160],
        "firm": "",
        "segment": segment,
        "why_relevant": bio,
        "x_handle": handle,
        "source_url": f"https://x.com/{handle}",
        "confidence": "low",
        "cmu_link": bool(_CMU.search(bio)),
        "yc_company": bool(_YC_ANY.search(bio)),
        "followers": (user.get("public_metrics") or {}).get("followers_count", 0),
        "location": user.get("location") or "",
        "matched": [],
        "posts": [],
    }


def known_people(rows: list[dict], dnc_path: str = DNC_PATH) -> tuple[set[str], set[str]]:
    handles = {(r.get("x_handle") or "").lstrip("@").lower() for r in rows} - {""}
    names = {(r.get("name") or "").strip().lower() for r in rows} - {""}
    if os.path.exists(dnc_path):
        with open(dnc_path, newline="", encoding="utf-8") as fh:
            names |= {r["name"].strip().lower() for r in csv.DictReader(fh) if r.get("name")}
    return handles, names


def run_search(client: Client, cfg: dict, handles: set[str], names: set[str],
               min_followers: int = 0, skip: Optional[set[str]] = None,
               relevant: Optional[Callable[[str], bool]] = None) -> dict:
    """Run every configured query. Return {candidates, blocked, error}.

    skip holds handles from earlier runs. relevant decides on the bio and post text.
    Post search runs first, because a post record costs half of a user record.
    """
    size = cfg["page_size"]
    user_size = cfg.get("user_page_size", size)
    skip = skip or set()
    found: dict[str, dict] = {}
    blocked: dict[str, dict] = {}

    def add(user: dict, segment: str, query: str, post: Optional[dict] = None) -> None:
        key = user.get("username", "").lower()
        if not key or key in skip:
            return
        text = f"{user.get('description') or ''}\n{(post or {}).get('text', '')}"
        if relevant and key not in found and not relevant(text):
            return
        reason = block_reason(user, handles, names)
        if reason:
            blocked.setdefault(key, {"x_handle": user["username"], "name": user.get("name"),
                                     "reason": reason, "bio": user.get("description") or ""})
            return
        metrics = user.get("public_metrics")     # Browser rows have no count. Keep them.
        if metrics and metrics.get("followers_count", 0) < min_followers:
            return
        person = found.setdefault(key, to_candidate(user, segment))
        if query not in person["matched"]:
            person["matched"].append(query)
        url = f"https://x.com/{user['username']}/status/{post.get('id')}" if post else ""
        if post and url not in {seen["url"] for seen in person["posts"]}:
            person["posts"].append({
                "date": post.get("created_at", "")[:10], "text": post.get("text", ""),
                "url": url})

    errors = []
    try:
        for segment, queries in cfg["post_queries"].items():
            for q in queries:
                for user, post in client.search_posts(q, size):
                    add(user, segment, q, post)
    except XError as err:
        errors.append(f"Post search: {err}")
    try:
        for segment, queries in cfg["user_queries"].items():
            for q in queries:
                for user in client.search_users(q, user_size):
                    add(user, segment, q)
    except XError as err:
        errors.append(f"User search: {err}")

    # Broad terms return mostly unrelated people, so the local bio filter decides.
    broad = cfg.get("broad") or {}
    keep = re.compile("|".join(broad.get("keep_terms") or ["$^"]), re.IGNORECASE)
    tokens: dict = {q: None for q in broad.get("queries") or []}
    try:
        for _ in range(broad.get("max_pages", 1)):   # Each query gets one page per round.
            for q in list(tokens):
                users, next_token = client.search_users_page(q, broad["page_size"], tokens[q])
                for user in users:
                    if keep.search(user.get("description") or ""):
                        add(user, "unsorted", q)
                if next_token:
                    tokens[q] = next_token
                else:
                    del tokens[q]
    except XError as err:
        errors.append(f"Broad search: {err}")

    people = sorted(found.values(), key=lambda p: (
        -(p["cmu_link"] + p["yc_company"]), -len(p["matched"]), -p["followers"]))
    return {"candidates": people, "blocked": list(blocked.values()), "error": " ".join(errors) or None}


def _person_text(person: dict) -> str:
    return "\n".join([person.get("why_relevant") or ""]
                     + [p.get("text", "") for p in person.get("posts") or []])


def prune_review_file(relevant: Callable[[str], bool], path: Optional[str] = None) -> dict:
    """Drop stored people who fail the filter. Return what stays."""
    path = path or OUT_PATH
    if not os.path.exists(path):
        return {"candidates": [], "blocked": [], "error": None}
    with open(path, encoding="utf-8") as fh:
        stored = json.load(fh)
    stored["candidates"] = [p for p in stored["candidates"] if relevant(_person_text(p))]
    write_review_file(stored, path)
    return stored


def seen_handles(paths: list[str]) -> set[str]:
    """Handles already stored, so a later run does not add them again."""
    seen: set[str] = set()
    for path in paths:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                seen |= {p["x_handle"].lower() for p in json.load(fh)["candidates"]}
    return seen


def merge_results(old: dict, new: dict) -> dict:
    """Add this run's people to the stored list."""
    have = {p["x_handle"].lower() for p in old["candidates"]}
    blocked = {b["x_handle"].lower(): b for b in old.get("blocked") or []}
    blocked.update({b["x_handle"].lower(): b for b in new["blocked"]})
    return {"candidates": old["candidates"] + [p for p in new["candidates"]
                                               if p["x_handle"].lower() not in have],
            "blocked": list(blocked.values()), "error": new["error"]}


def write_review_file(result: dict, path: Optional[str] = None) -> None:
    path = path or OUT_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
