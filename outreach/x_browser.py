"""Backup X research through the founder's logged-in Chrome. The paid API is primary.

This client has the same search methods as x_api.Client, so x_api.run_search
applies the same queries, blocks, bio filter and ranking. It only opens and reads
pages. It never posts, follows, likes or sends a DM, and it uses its own tab, so
the LinkedIn sender keeps its tab.

A warning from X stops the whole run. The tool does not try to get past it.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from typing import Callable, Optional

from .config import ROOT, load_config
from .x_api import XError

CACHE_PATH = os.path.join(ROOT, "data", "x_browser_cache.json")
OUT_PATH = os.path.join(ROOT, "data", "x_candidates_browser.json")
STOP_URL = ("/login", "/i/flow/login", "/i/jf/", "/onboarding", "/account/access", "/logout")
STOP_TEXT = ("rate limit exceeded", "something went wrong. try reloading",
             "your account has been locked", "your account is suspended",
             "verify you are human", "unusual activity", "we sent you a security code")
_HANDLE = re.compile(r"^/([A-Za-z0-9_]{1,15})$")
_BUTTONS = ("Follow", "Following", "Follows you", "Follow back", "Pending", "Subscribe")

USERS_JS = r"""() => [...document.querySelectorAll('[data-testid="UserCell"]')].map(c => {
  const href = [...c.querySelectorAll('a[href^="/"]')].map(a => a.getAttribute('href'))
    .find(h => /^\/[A-Za-z0-9_]{1,15}$/.test(h));
  return {href, text: c.innerText};
})"""

POSTS_JS = r"""() => [...document.querySelectorAll('article[data-testid="tweet"]')].map(a => {
  const box = a.querySelector('[data-testid="User-Name"]');
  const time = a.querySelector('a[href*="/status/"] time');
  const text = a.querySelector('[data-testid="tweetText"]');
  return {
    href: box ? box.querySelector('a[href^="/"]')?.getAttribute('href') : null,
    name: box ? box.innerText.split('\n')[0] : '',
    text: text ? text.innerText : '',
    url: time ? time.closest('a').getAttribute('href') : '',
    date: time ? time.getAttribute('datetime') : '',
  };
})"""

PROFILE_JS = r"""() => {
  const read = q => document.querySelector(q)?.innerText?.trim() || '';
  const followers = document.querySelector(
    'a[href$="/verified_followers"], a[href$="/followers"]');
  return {
    name: read('[data-testid="UserName"]').split('\n')[0],
    bio: read('[data-testid="UserDescription"]'),
    location: read('[data-testid="UserLocation"]'),
    followers: followers ? followers.innerText : '',
  };
}"""


def parse_count(text: str) -> int:
    """Read "1,234 Followers" or "12.3K Followers"."""
    found = re.search(r"([\d.,]+)\s*([KMB]?)", text or "", re.IGNORECASE)
    if not found:
        return 0
    scale = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[found.group(2).upper()]
    return int(float(found.group(1).replace(",", "")) * scale)


def parse_user_cell(cell: dict) -> Optional[dict]:
    """Turn one raw search row into the API's user shape."""
    found = _HANDLE.match(cell.get("href") or "")
    if not found:
        return None
    handle = found.group(1)
    # A row reads: name, @handle, a follow button, then the bio. The bio breaks
    # its line around each mention, so the lines join back into one string.
    lines = [t.strip() for t in (cell.get("text") or "").split("\n") if t.strip()]
    if f"@{handle}" not in lines:
        return None
    at = lines.index(f"@{handle}")
    bio = [t for t in lines[at + 1:] if t not in _BUTTONS]
    # A browser row has no follower count. Leaving out public_metrics means "unknown".
    return {"id": handle, "username": handle, "name": " ".join(lines[:at]) or handle,
            "description": " ".join(bio)}


def parse_post(raw: dict) -> Optional[dict]:
    found = _HANDLE.match(raw.get("href") or "")
    if not found or not raw.get("url"):
        return None
    status = raw["url"].rstrip("/").split("/status/")[-1].split("/")[0]
    return {"handle": found.group(1), "name": raw.get("name") or found.group(1),
            "id": status, "text": raw.get("text") or "", "created_at": raw.get("date") or ""}


class BrowserClient:
    """Read X search pages at a slow, fixed pace, with limits and a disk cache."""

    def __init__(self, page, cfg: dict, cache_path: Optional[str] = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.page = page
        self.cfg = cfg
        self.sleep = sleep
        self.cache_path = cache_path or CACHE_PATH
        self.max_resources = cfg["max_searches"]
        self.skip: set[str] = set()   # Stored handles need no profile visit.
        self.relevant: Optional[Callable[[str], bool]] = None
        self.used = 0                # Search pages opened in this run.
        self.profiles = 0            # Profile pages opened in this run.
        self.stopped: Optional[str] = None
        self.cache: dict = {}
        if os.path.exists(self.cache_path):
            with open(self.cache_path, encoding="utf-8") as fh:
                self.cache = json.load(fh)

    def _save(self, key: str, value) -> None:
        self.cache[key] = value
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as fh:
            json.dump(self.cache, fh)

    def check(self) -> None:
        """Stop the run if X shows a login page, a limit or a warning."""
        url = self.page.url.lower()
        text = (self.page.inner_text("body") or "").lower()
        reason = next((u for u in STOP_URL if u in url), None) or next(
            (t for t in STOP_TEXT if t in text), None)
        if reason:
            self.stopped = f"X showed: {reason}"
            raise XError(self.stopped)

    def _open(self, url: str, selector: str) -> None:
        if self.stopped:
            raise XError(self.stopped)
        self.sleep(self.cfg["delay_seconds"])
        self.page.goto(url, wait_until="domcontentloaded")
        try:
            self.page.wait_for_selector(selector, timeout=12_000)
        except Exception:             # An empty search has no rows. The check below decides.
            pass
        self.check()

    def _search(self, query: str, tab: str, selector: str, script: str, size: int) -> list[dict]:
        if self.used >= self.max_resources:
            raise XError(f"Stopped at the limit of {self.max_resources} search pages.")
        self.used += 1
        params = {"q": query, "src": "typed_query"}
        if tab != "top":                # The Top tab is the default, with no f value.
            params["f"] = tab
        self._open("https://x.com/search?" + urllib.parse.urlencode(params), selector)
        rows: dict = {}
        for _ in range(self.cfg["scrolls"] + 1):
            before = len(rows)
            for row in self.page.evaluate(script):
                rows.setdefault(row.get("url") or row.get("href"), row)
            if len(rows) >= size or (before and len(rows) == before):
                break                  # Stop when a scroll adds nothing.
            self.page.mouse.wheel(0, 2400)
            self.sleep(self.cfg["scroll_seconds"])
        print(f"[{self.used}/{self.max_resources}] +{len(rows)} {tab} {query[:60]!r}", flush=True)
        if not rows and "no results for" not in self.page.inner_text("body").lower():
            # An unexplained empty page is a failure. Do not store it as a result.
            self.stopped = f"X showed no rows and no empty-search message for {query[:40]!r}"
            raise XError(self.stopped)
        return list(rows.values())

    def search_users(self, query: str, size: int) -> list[dict]:
        key = f"users|{query}"
        if key not in self.cache:
            rows = self._search(query, "user", '[data-testid="UserCell"]', USERS_JS, size)
            # No bio means no block check and nothing to judge fit, so skip the account.
            self._save(key, [u for u in map(parse_user_cell, rows)
                             if u and u["description"]])
        return self.cache[key]

    def search_users_page(self, query: str, size: int,
                          token: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
        return self.search_users(query, size), None    # Scrolling replaces paging.

    def profile(self, handle: str) -> Optional[dict]:
        """Read one profile. Post search shows no bio, and the blocks need the bio."""
        key = f"profile|{handle.lower()}"
        if key in self.cache:
            return self.cache[key]
        if self.profiles >= self.cfg["max_profiles"]:
            return None
        self.profiles += 1
        self._open(f"https://x.com/{handle}", '[data-testid="UserName"]')
        raw = self.page.evaluate(PROFILE_JS)
        if not raw.get("name"):
            return None
        user = {"id": handle, "username": handle, "name": raw["name"],
                "description": raw.get("bio") or "", "location": raw.get("location") or "",
                "public_metrics": {"followers_count": parse_count(raw.get("followers", ""))}}
        self._save(key, user)
        return user

    def user_list(self, handle: str, kind: str, scrolls: int) -> list[dict]:
        """Read who one account follows ("following") or who follows it ("followers").

        Each row has a bio, so one page gives many people without profile visits.
        """
        key = f"list|{kind}|{handle.lower()}"
        if key in self.cache:
            return self.cache[key]
        if self.used >= self.max_resources:
            raise XError(f"Stopped at the limit of {self.max_resources} pages.")
        self.used += 1
        self._open(f"https://x.com/{handle}/{kind}", '[data-testid="UserCell"]')
        rows: dict = {}
        still = 0
        for _ in range(scrolls + 1):
            before = len(rows)
            for row in self.page.evaluate(USERS_JS):
                rows.setdefault(row.get("href"), row)
            still = still + 1 if len(rows) == before else 0
            if still >= 3:
                break                  # Three scrolls with nothing new: the list ended.
            self.page.mouse.wheel(0, 3000)
            self.sleep(self.cfg["scroll_seconds"])
        self.check()
        print(f"[{self.used}/{self.max_resources}] +{len(rows)} {kind} of @{handle}", flush=True)
        body = self.page.inner_text("body").lower()
        if not rows and any(t in body for t in ("protected", "isn't following anyone",
                                                 "doesn't have any followers",
                                                 "this account doesn't exist")):
            self._save(key, [])        # A private or empty list. Go to the next seed.
            return []
        if not rows:
            self.stopped = f"X showed no rows for @{handle}/{kind}"
            raise XError(self.stopped)
        users = [u for u in map(parse_user_cell, rows.values()) if u and u["description"]]
        self._save(key, users)
        return users

    def search_posts(self, query: str, size: int) -> list[tuple[dict, dict]]:
        tab = self.cfg.get("post_tab", "live")
        key = f"posts|{tab}|{query}"
        if key not in self.cache:
            rows = self._search(f"({query}) -filter:retweets lang:en", tab,
                                'article[data-testid="tweet"]', POSTS_JS, size)
            self._save(key, [p for p in map(parse_post, rows) if p])
        pairs = []
        for post in self.cache[key]:
            if post["handle"].lower() in self.skip:
                continue
            if self.relevant and not self.relevant(post["text"]):
                continue
            user = self.profile(post["handle"])     # No bio means no block check, so skip.
            if user:
                pairs.append((user, post))
        return pairs


def logged_in(cookies: list[dict]) -> bool:
    return any(c.get("name") == "auth_token" and c.get("value") for c in cookies)


def run_browser_search(cfg: dict, handles: set[str], names: set[str],
                       skip: Optional[set[str]] = None,
                       relevant: Optional[Callable[[str], bool]] = None
                       ) -> tuple[dict, BrowserClient]:
    """Open one new tab, run every query, and close only that tab."""
    from playwright.sync_api import sync_playwright

    from . import x_api

    with sync_playwright() as p:
        ctx = p.chromium.connect_over_cdp(load_config()["linkedin"]["cdp_url"]).contexts[0]
        # Check the login first. A logged-out visit can make X text a code to the phone.
        if not logged_in(ctx.cookies("https://x.com")):
            raise XError("Chrome is not logged in to X. Log in by hand in that Chrome window.")
        page = ctx.new_page()
        client = BrowserClient(page, cfg["browser"])
        client.skip = skip or set()
        client.relevant = relevant
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded")
            page.wait_for_timeout(4_000)
            client.check()
            result = x_api.run_search(client, cfg, handles, names, cfg["min_followers"],
                                      skip, relevant)
        except XError as err:
            result = {"candidates": [], "blocked": [], "error": str(err)}
        finally:
            page.close()
    return result, client
