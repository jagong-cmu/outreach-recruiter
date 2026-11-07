"""Find CMU alumni on LinkedIn, in the sender's own logged-in Chrome.

It opens CMU's alumni page with one search term, reads the result cards, keeps
the people whose headline fits, and adds them to the local list and the shared
queue. LinkedIn watches search more closely than invites, so this module:

  - claims each search term in the shared database first. Two senders never run
    the same term, and nobody runs a term again within `rerun_days`.
  - runs at most `daily_searches` terms a day for each sender, with a wait
    between pages.
  - stops the whole run at the first LinkedIn warning, the same as the sender.

The terms come from `alumni_searches` in config.yaml. A sender can add more in
config.local.yaml.
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

from . import db, linkedin, messages, qualify, scoring, shared
from .config import ROOT, load_config

LOCAL_URL = "sqlite:///" + os.path.join(ROOT, "data", "searches_local.db")
CARD = ".org-people-profile-card__profile-card-spacing"
NOISE = re.compile(r"degree connection|^·|mutual connection|^(connect|follow|message|pending)$",
                   re.IGNORECASE)


def _db(cfg: dict) -> dict:
    """The shared database, or a local file when no shared database is set."""
    sh = shared.settings(cfg)
    if sh:
        return sh
    os.makedirs(os.path.dirname(LOCAL_URL[len("sqlite:///"):]), exist_ok=True)
    return {"url": LOCAL_URL, "sender": "me"}


def terms(cfg: dict) -> list[str]:
    seen, out = set(), []
    for term in cfg["alumni_auto"]["terms"]:
        if term.lower() not in seen:
            seen.add(term.lower())
            out.append(term)
    return out


def parse_card(text: str) -> Optional[dict]:
    """Return {name, headline} from the text of one result card."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not NOISE.search(ln)]
    if not lines or lines[0].lower() == "linkedin member":
        return None
    return {"name": lines[0], "headline": lines[1] if len(lines) > 1 else ""}


def firm_from(headline: str, gpu_firms: list[str]) -> str:
    """The firm: the text after ' at ' or '@', else a GPU company in the headline.

    The search term is not used. A search for "Lambda" also finds people who work
    on AWS Lambda at Amazon.
    """
    m = re.search(r"(?:\s|^)(?:at|@)\s*([^|·•,]+)", headline, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    low = headline.lower()
    return next((f.title() for f in gpu_firms if _has(low, [f])), "")


def _has(text: str, words: list[str]) -> bool:
    """True when a word occurs as a whole word. "@ cmu" needs no left boundary."""
    for w in words:
        w = w.lower()
        left = r"\b" if w[:1].isalnum() else ""
        if re.search(left + re.escape(w) + r"s?\b", text):
            return True
    return False


def gpu_link(headline: str, auto: dict) -> bool:
    low = (headline or "").lower()
    return _has(low, auto["gpu_terms"]) or _has(low, auto["gpu_firms"])


def fits(headline: str, auto: dict) -> bool:
    """True when the headline has a GPU link, a finance word, and no excluded word.

    A finance word alone is not enough. "Private Credit Associate at BlackRock"
    has no link to GPUs, so the search drops it.
    """
    low = (headline or "").lower()
    return (gpu_link(low, auto) and _has(low, auto["finance_terms"])
            and not _has(low, auto["exclude_terms"]))


def segment_for(headline: str, firm: str, auto: dict) -> str:
    low = f"{headline} {firm}".lower()
    for rule in auto["segment_rules"]:
        if _has(low, rule["has"]):
            return rule["segment"]
    return auto.get("default_segment", "analyst")


def to_prospect(card: dict, url: str, term: str, cfg: dict) -> Optional[dict]:
    auto = cfg["alumni_auto"]
    if not fits(card["headline"], auto):
        return None
    firm = firm_from(card["headline"], auto["gpu_firms"])
    # The search term stays out of why_relevant. The qualify step reads that
    # text, and a term such as "GPU financing" must not make a person look
    # confirmed when the headline does not say so.
    return {
        "name": card["name"], "title": card["headline"], "firm": firm,
        "segment": segment_for(card["headline"], firm, auto),
        "why_relevant": f"{card['headline']}. Found in the CMU alumni search on LinkedIn.",
        "linkedin_url": url, "source_url": url, "confidence": "medium", "cmu_link": True,
    }


def _read_cards(page) -> list[tuple[str, str]]:
    """Return (profile URL, card text) for each card on the page."""
    out = {}
    for card in page.query_selector_all(CARD):
        link = card.query_selector('a[href*="/in/"]')
        if link:
            url = (link.get_attribute("href") or "").split("?")[0]
            if url.startswith("/"):
                url = "https://www.linkedin.com" + url
            out[url] = card.inner_text()
    return list(out.items())


def _search_term(page, term: str, auto: dict) -> list[tuple[str, str]]:
    page.goto(messages.alumni_search_url(term), wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)
    linkedin._check(page)
    found: dict[str, str] = {}
    for _ in range(auto["scrolls"]):
        found.update(_read_cards(page))
        if len(found) >= auto["max_results_per_term"]:
            break
        more = [b for b in page.query_selector_all("main button")
                if b.is_visible() and linkedin._label(b).lower() == "show more results"]
        if more:
            more[0].click()
        else:
            page.mouse.wheel(0, 4000)
        page.wait_for_timeout(auto["scroll_seconds"] * 1000)
        linkedin._check(page)
    found.update(_read_cards(page))
    return list(found.items())[:auto["max_results_per_term"]]


def _save(people: list[dict], cfg: dict) -> None:
    """Add people to the local list, then qualify and score them."""
    with db.connect() as conn:
        for person in people:
            pid = db.upsert_prospect(conn, person)
            row = dict(db.get_prospect(conn, pid))
            verdict = qualify.qualify(row, cfg)
            db.set_fit(conn, pid, verdict["fit"])
            if verdict["fit"] in qualify.PASSING:
                result = scoring.score_prospect(row, cfg)
                db.save_score(conn, pid, result["total"], result["breakdown"])


def run(page, max_terms: Optional[int] = None, cfg: Optional[dict] = None) -> dict:
    """Run the next free search terms. Return {terms, added, skipped_terms}.

    Raise linkedin.Stop at a LinkedIn warning, and shared.SharedError when the
    shared database is set but cannot be reached.
    """
    cfg = cfg or load_config()
    auto = cfg["alumni_auto"]
    store = _db(cfg)
    sender = store["sender"]
    with shared.connect(store["url"]) as conn:
        room = auto["daily_searches"] - shared.searches_today(conn, sender)
    budget = room if max_terms is None else min(room, max_terms)
    result = {"terms": 0, "added": 0, "skipped_terms": 0}
    if budget <= 0:
        print("Search: daily limit reached.", flush=True)
        return result
    for term in terms(cfg):
        if result["terms"] >= budget:
            break
        key = "alumni:" + term.lower()
        with shared.connect(store["url"]) as conn:
            owner = shared.claim_search(conn, sender, key, auto["rerun_days"])
        if owner:
            result["skipped_terms"] += 1
            continue
        try:
            cards = _search_term(page, term, auto)
        except BaseException:
            with shared.connect(store["url"]) as conn:
                shared.release_search(conn, sender, key)
            raise
        with shared.connect(store["url"]) as conn:
            known = shared.known_profile_keys(conn)
            new = []
            for url, text in cards:
                card = parse_card(text)
                person = to_prospect(card, url, term, cfg) if card else None
                if not person or shared.url_key(url) in known:
                    continue
                if store["url"] == LOCAL_URL or shared.add_to_queue(conn, sender, person):
                    new.append(person)
            shared.finish_search(conn, sender, key, len(new))
        _save(new, cfg)
        result["terms"] += 1
        result["added"] += len(new)
        print(f"  search   '{term}': {len(cards)} cards, {len(new)} new people", flush=True)
        time.sleep(auto["delay_seconds"])
    return result
