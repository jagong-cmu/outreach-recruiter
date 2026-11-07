"""Send connection notes through the founder's own logged-in Chrome.

Start Chrome yourself with --remote-debugging-port and log in by hand. This
module never sees a password, disguises nothing, and never solves a check. It
stops the whole run at the first sign of a LinkedIn warning.

It sends only when all of these hold:
  - one search result matches the person's name and the person's firm
  - the profile page shows the same name
  - the invite box names the same person
  - the note in the box is the exact note, and it fits the limit
  - with a shared database set, this sender holds the claim on the person
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import re
import time
import unicodedata
import urllib.parse
from typing import Optional

from . import db, messages, qualify, shared
from .config import ROOT, load_config

LOG_PATH = os.path.join(ROOT, "data", "linkedin_log.csv")
FILLER = {"the", "a", "an", "of", "and", "group", "inc", "llc", "corp", "corporation"}
STOP_TEXT = ("weekly invitation limit", "reached the weekly", "invitation limit",
             "unusual activity", "security verification", "quick security check",
             "account has been restricted", "temporarily restricted",
             "too many requests", "try again later")
STOP_URL = ("/checkpoint", "/authwall", "/uas/login", "/login", "captcha")


class Stop(Exception):
    """LinkedIn showed a warning. End the run."""


class Skip(Exception):
    """This person cannot be sent safely. Log it and go on."""


class Taken(Skip):
    """Another sender holds the claim on this person."""


class AlreadyInvited(Skip):
    """This account already has a pending invite to this person."""


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", text.lower())


def same_person(found: str, wanted: str) -> bool:
    """True when the first and last names of `wanted` both appear in `found`."""
    wanted_clean = re.sub(r"\(.*?\)", " ", wanted or "")
    want = _norm(wanted_clean).split()
    have = _norm(found).split()
    return len(want) >= 2 and want[0] in have and want[-1] in have


def firm_term(firm: str) -> str:
    """Return the first real word of a firm name, used to confirm a result."""
    words = [w for w in _norm(re.sub(r"\(.*?\)", " ", firm or "")).split() if w not in FILLER]
    return words[0] if words else ""


def stop_reason(text: str, url: str) -> Optional[str]:
    low = (text or "").lower()
    for phrase in STOP_TEXT:
        if phrase in low:
            return f"LinkedIn text: {phrase}"
    path = urllib.parse.urlparse(url or "").path.lower()
    for part in STOP_URL:
        if part in path:
            return f"LinkedIn page: {path}"
    return None


def _label(el) -> str:
    return (el.get_attribute("aria-label") or el.inner_text() or "").strip()


def _is_chat(box) -> bool:
    """True for the LinkedIn chat overlay. A reply opens it as a role=dialog element."""
    return (box.evaluate("x => !!x.closest('[class*=\"msg-overlay\"]')")
            or "Close your conversation" in box.inner_text())


def _boxes(page) -> list:
    return [d for d in page.query_selector_all('[role="dialog"], [role="alertdialog"]')
            if d.is_visible() and not _is_chat(d)]


def _dialog(page):
    boxes = _boxes(page)
    return boxes[0] if boxes else None


def _check(page) -> None:
    """Raise Stop if the current page or any dialog or alert shows a warning."""
    alerts = [e for e in page.query_selector_all('[role="alert"]') if e.is_visible()]
    text = " ".join(e.inner_text() for e in _boxes(page) + alerts)
    reason = stop_reason(text, page.url)
    if reason:
        raise Stop(reason)


def _find_profile(page, person: dict) -> str:
    """Return the one profile URL that matches the person's name and firm."""
    query = f"{person['name']} {person.get('firm') or ''}".strip()
    url = "https://www.linkedin.com/search/results/people/?keywords=" + urllib.parse.quote_plus(query)
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)
    _check(page)
    term = firm_term(person.get("firm") or "")
    matches: dict[str, str] = {}
    for a in page.query_selector_all('main a[href*="/in/"]'):
        href = (a.get_attribute("href") or "").split("?")[0]
        # The link's own text decides. A card also links to mutual connections.
        own = [ln.strip() for ln in a.inner_text().splitlines() if ln.strip()]
        if own and same_person(own[0].split("•")[0], person["name"]):
            matches[href] = a.evaluate(
                "x => (x.closest('li, [role=\"listitem\"]') || x.parentElement.parentElement"
                ".parentElement.parentElement).innerText")
    if not matches:
        raise Skip("no search result with this name")
    firm_hits = [h for h, t in matches.items() if not term or term in _norm(t).split()]
    if len(firm_hits) != 1:
        raise Skip(f"{len(matches)} name matches, {len(firm_hits)} with firm '{term}'")
    return firm_hits[0]


def _open_invite(page, person: dict, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)
    _check(page)
    h1 = page.query_selector("main h1") or page.query_selector("h1")
    heading = h1.inner_text() if h1 else page.title().split("|")[0]
    if not same_person(heading, person["name"]):
        raise Skip(f"profile heading is '{heading}'")
    labels = [_label(b) for b in page.query_selector_all("main button") if b.is_visible()]
    if any(re.match(r"pending", lab, re.I) for lab in labels):
        raise AlreadyInvited("invite already pending")

    direct = [b for b in page.query_selector_all("main button")
              if b.is_visible() and re.match(r"invite .* to connect", _label(b), re.I)
              and same_person(_label(b), person["name"])]
    vanity = urllib.parse.unquote(url.rstrip("/").split("/")[-1])
    anchors = [a for a in page.query_selector_all('main a[href*="custom-invite"]')
               if a.is_visible()
               and f"vanityName={vanity}" in urllib.parse.unquote(a.get_attribute("href") or "")]
    if direct:
        direct[0].click()
    elif anchors:
        anchors[0].click()
    else:
        more = [b for b in page.query_selector_all("main button")
                if b.is_visible() and _label(b) == "More"]
        if not more:
            raise Skip("no Connect or More button")
        more[0].click()
        page.wait_for_timeout(1200)
        links = [a for m in page.query_selector_all('[role="menu"]') if m.is_visible()
                 for a in m.query_selector_all("a") if a.inner_text().strip() == "Connect"
                 and vanity in urllib.parse.unquote(a.get_attribute("href") or "")]
        if not links:
            page.keyboard.press("Escape")
            raise Skip("no Connect option in the More menu")
        links[0].click()
    page.wait_for_timeout(2500)
    _check(page)


def _close(page) -> None:
    box = _dialog(page)
    while box:
        close = [b for b in box.query_selector_all("button")
                 if re.fullmatch(r"Dismiss|Close|Discard", _label(b))]
        if not close:
            return
        close[0].click()
        page.wait_for_timeout(1000)
        box = _dialog(page)


def _prepare_note(page, person: dict, note: str, limit: int) -> None:
    box = _dialog(page)
    if not box:
        raise Skip("no invite box")
    if not same_person(box.inner_text(), person["name"]):
        _close(page)
        raise Skip("invite box does not name this person")
    add = [b for b in box.query_selector_all("button")
           if b.is_visible() and _label(b).lower().startswith("add a note")]
    if not add:
        _close(page)
        raise Skip("no Add a note button")
    add[0].click()
    page.wait_for_timeout(1500)
    box = _dialog(page)
    area = box.query_selector("textarea") if box else None
    if not area:
        _close(page)
        raise Skip("no note box")
    area.fill(note)
    page.wait_for_timeout(800)
    _check(page)
    if area.input_value() != note or len(note) > limit:
        _close(page)
        raise Skip("note text or length check failed")


def _send(page) -> None:
    box = _dialog(page)
    send = [b for b in box.query_selector_all("button")
            if b.is_visible() and _label(b) in ("Send invitation", "Send")]
    if len(send) != 1 or send[0].is_disabled():
        raise Stop("Send button is not in the expected state")
    send[0].click()
    page.wait_for_timeout(3000)
    _check(page)
    if _dialog(page):
        raise Stop("invite box stayed open after Send")


def _confirm_pending(page, url: str) -> bool:
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)
    _check(page)
    labels = [_label(b) for b in page.query_selector_all("main button") if b.is_visible()]
    if any(lab.lower().startswith("pending") for lab in labels):
        return True
    more = [b for b in page.query_selector_all("main button")
            if b.is_visible() and _label(b) == "More"]
    if more:
        more[0].click()
        page.wait_for_timeout(1200)
        text = " ".join(m.inner_text() for m in page.query_selector_all('[role="menu"]')
                        if m.is_visible())
        page.keyboard.press("Escape")
        return "Pending" in text or "Withdraw" in text or "Connect" not in text
    return False


def _log(name: str, firm: str, result: str, detail: str, url: str = "") -> None:
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["time", "name", "firm", "result", "detail", "url"])
        w.writerow([dt.datetime.now().isoformat(timespec="seconds"), name, firm,
                    result, detail, url])
    print(f"  {result:<9} {name} ({firm}) {detail}", flush=True)


def skipped_names() -> set[str]:
    """People that the sender already skipped. A rerun does not retry them."""
    if not os.path.exists(LOG_PATH):
        return set()
    with open(LOG_PATH, newline="", encoding="utf-8") as fh:
        return {r["name"] for r in csv.DictReader(fh) if r["result"] == "skipped"}


def targets(names: Optional[list[str]] = None) -> list[dict]:
    """People not contacted yet by any sender, best first."""
    skipped = skipped_names()
    with db.connect() as conn:
        rows = [dict(r) for r in db.ranked(conn, stage="new")
                if r["fit"] in qualify.PASSING and r["name"] not in skipped]
    sh = shared.settings(load_config())
    if sh:
        with shared.connect(sh["url"]) as conn:
            taken = shared.taken_by_others(conn, sh["sender"])
        rows = [r for r in rows if not taken.intersection(shared.person_keys(r))]
    if names:
        wanted = {n.lower() for n in names}
        rows = [r for r in rows if r["name"].lower() in wanted]
    return rows


def _attach(p, cdp_url: str):
    """Connect to Chrome and return a LinkedIn tab, or a new tab."""
    ctx = p.chromium.connect_over_cdp(cdp_url).contexts[0]
    page = next((x for x in ctx.pages if "linkedin.com" in x.url), None)
    return page or ctx.new_page()


def profile_url(person: dict) -> Optional[str]:
    """The known profile URL, when the person was found on LinkedIn itself.

    Research files can hold an old or wrong URL, so those people still go
    through the name and firm search. The profile heading check runs either way.
    """
    url = person.get("linkedin_url") or ""
    if url and url == person.get("source_url") and shared.url_key(url):
        return url
    return None


def _shared(sh: Optional[dict], fn, *args):
    """Run one call on the shared database. Return None when none is set."""
    if not sh:
        return None
    with shared.connect(sh["url"]) as conn:
        return fn(conn, sh["sender"], *args)


def _claim(sh: Optional[dict], person: dict, url: Optional[str] = None) -> None:
    owner = _shared(sh, shared.claim, person, url)
    if owner:
        raise Taken(f"claimed by {owner}")


def _send_one(page, person: dict, cfg: dict, dry_run: bool,
              sh: Optional[dict] = None) -> None:
    """Handle one person. Raise Skip, Stop or a browser error if it fails.

    A claim stays after a Stop or a browser error, because an invite can be out.
    A claim goes only after a Skip, which always comes before Send.
    """
    note = messages.generate(person, cfg)[0]["body"]
    _claim(sh, person)
    url = None
    try:
        url = profile_url(person) or _find_profile(page, person)
        _claim(sh, person, url)
        _open_invite(page, person, url)
        _prepare_note(page, person, note, cfg["limits"]["linkedin_note"])
    except Taken:
        raise
    except AlreadyInvited:
        _shared(sh, shared.mark_sent, person, url)
        raise
    except Skip:
        _shared(sh, shared.release, person, url)
        raise
    if dry_run:
        _close(page)
        _shared(sh, shared.release, person, url)
        _log(person["name"], person.get("firm"), "dry-run", "ready to send", url)
        return
    _send(page)
    with db.connect() as conn:
        db.set_linkedin_url(conn, person["id"], url)
        db.set_stage(conn, person["id"], "sent", "linkedin_note")
    _shared(sh, shared.mark_sent, person, url)
    ok = _confirm_pending(page, url)
    _log(person["name"], person.get("firm"), "sent",
         "pending confirmed" if ok else "PENDING NOT CONFIRMED", url)
    if not ok:
        raise Stop(f"could not confirm the invite to {person['name']}")


def run(limit: int, names: Optional[list[str]] = None, dry_run: bool = False,
        delay: Optional[int] = None, deadline: Optional[dt.datetime] = None) -> int:
    from playwright.sync_api import Error as BrowserError, sync_playwright

    cfg = load_config()
    li = cfg["linkedin"]
    sh = shared.settings(cfg)
    delay = li["delay_seconds"] if delay is None else delay
    with db.connect() as conn:
        room = li["daily_limit"] - db.stage_counts(conn)["sent_today"]
    limit = min(limit, room)
    people = targets(names)
    print(f"{len(people)} eligible. Sending up to {limit}. Dry run: {dry_run}. "
          f"Shared queue: {sh['sender'] if sh else 'off'}.", flush=True)
    sent = lost = 0
    with sync_playwright() as p:
        page = _attach(p, li["cdp_url"])
        queue = list(people)
        while queue and sent < limit:
            if deadline and dt.datetime.now() >= deadline:
                break
            person = queue.pop(0)
            try:
                _send_one(page, person, cfg, dry_run, sh)
                sent += 1          # A dry run counts too, so --limit always holds.
                lost = 0
                if sent < limit:
                    time.sleep(min(delay, 5) if dry_run else delay)
            except Skip as why:
                _log(person["name"], person.get("firm"), "skipped", str(why))
                time.sleep(min(delay, 20))
            except Stop as why:
                _log(person["name"], person.get("firm"), "STOPPED", str(why))
                print(f"Stopped: {why}", flush=True)
                return 1
            except BrowserError as why:
                # A lost tab is not a LinkedIn warning. Retry this person. Their
                # profile shows Pending if the invite already went out.
                lost += 1
                short = str(why).splitlines()[0][:100]
                _log(person["name"], person.get("firm"), "browser", short)
                if lost >= 3:
                    print("Stopped: the browser failed three times in a row.", flush=True)
                    return 1
                queue.insert(0, person)
                time.sleep(30)
                page = _attach(p, li["cdp_url"])
    print(f"Done. Sent {sent}.", flush=True)
    return 0


def seconds_to_midnight(now: Optional[dt.datetime] = None) -> int:
    now = now or dt.datetime.now()
    tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds())


def _search_batch(cfg: dict, terms: int) -> dict:
    """Run a few alumni search terms in the logged-in Chrome."""
    from playwright.sync_api import sync_playwright
    from . import linkedin_search
    with sync_playwright() as p:
        return linkedin_search.run(_attach(p, cfg["linkedin"]["cdp_url"]), terms, cfg)


def _sync(cfg: dict) -> None:
    from . import pipeline
    sh = shared.settings(cfg)
    if sh:
        r = pipeline.sync(sh, LOG_PATH)
        print(f"Sync: pushed {r['queued']}, pulled {r['pulled']}.", flush=True)
    pipeline.rescore(cfg)


def keep_going(until: dt.datetime, delay: Optional[int] = None, search: bool = False) -> int:
    """Send daily until `until`, and pick up new prospects as they arrive.

    It obeys the daily limit, and it ends at the first LinkedIn warning. The
    deadline matters: the note says "this weekend". With a shared database, it
    syncs the queue before each batch. With `search`, it also runs the alumni
    search when nobody is left to contact.
    """
    cfg = load_config()
    while dt.datetime.now() < until:
        # With a shared database, every batch starts with a sync. A sender that
        # only takes from the queue then gets new research while it runs.
        if search or shared.settings(cfg):
            _sync(cfg)
        with db.connect() as conn:
            room = cfg["linkedin"]["daily_limit"] - db.stage_counts(conn)["sent_today"]
        if room <= 0:
            wait = min(seconds_to_midnight(), (until - dt.datetime.now()).total_seconds())
            print(f"Daily limit reached. Waiting {int(wait)} seconds.", flush=True)
            time.sleep(max(wait, 1))
            continue
        if not targets():
            if search:
                try:
                    found = _search_batch(cfg, 3)
                except Stop as why:
                    print(f"Stopped: {why}", flush=True)
                    return 1
                if found["added"]:
                    continue
            print("Queue empty. Checking again in 10 minutes.", flush=True)
            time.sleep(600)
            continue
        # One small batch at a time, so the queue and the deadline are re-read.
        code = run(limit=min(room, 5), delay=delay, deadline=until)
        if code:
            return code
    print("Deadline reached. Stopped.", flush=True)
    return 0
