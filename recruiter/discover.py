"""Auto-discover candidate profile URLs on LinkedIn.

WHAT THIS DOES
──────────────
Instead of you pasting profile URLs, this opens CMU's alumni/people page in YOUR
authenticated browser session (via the LI_AT cookie), types the target keyword
("Class of 2030") into the alumni search box, scrolls to load more people, and
collects their profile links. Those links then feed the normal
scrape → verify → score pipeline.

⚠️ HONEST WARNING
─────────────────
This is the most ban-prone and most fragile part of the whole system. Crawling
LinkedIn's people pages is exactly what their bot-detection watches for, even on
your own account. Expect:
  * Throttling, CAPTCHAs, or "unusual activity" checkpoints.
  * A monthly cap on how many search/people results a free account can view.
  * Layout changes that break the selectors below (they're best-effort).
  * Some runs returning few or zero links.

Mitigations baked in: it uses your real session, a modest hard cap
(config: discovery.max_profiles), and human-paced scrolling. Run with
`--show` to watch the browser and solve any CAPTCHA by hand.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .config import load_config

PROFILE_RE = re.compile(r"/in/[^/?#]+")


def _clean(href: str) -> Optional[str]:
    """Normalize a LinkedIn href to a canonical profile URL, or None."""
    if not href:
        return None
    m = PROFILE_RE.search(href)
    if not m:
        return None
    return "https://www.linkedin.com" + m.group(0) + "/"


def discover_profiles(max_profiles: Optional[int] = None,
                      show: bool = False,
                      config: Optional[dict] = None) -> list[str]:
    """Return a list of discovered LinkedIn profile URLs.

    Raises RuntimeError with a helpful message if Playwright isn't installed or
    LI_AT isn't set.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright is not installed. Run:\n"
            "  pip install playwright && playwright install chromium"
        ) from e

    li_at = os.environ.get("LI_AT")
    if not li_at:
        raise RuntimeError(
            "LI_AT not set. Discovery needs your own session cookie:\n"
            "  export LI_AT='<li_at value from your browser>'"
        )

    cfg = (config or load_config()).get("discovery", {})
    url = cfg.get("school_people_url",
                  "https://www.linkedin.com/school/carnegie-mellon-university/people/")
    keyword = cfg.get("keyword", "")
    max_profiles = max_profiles or cfg.get("max_profiles", 50)
    max_scrolls = cfg.get("max_scrolls", 25)

    found: set[str] = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not show)
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"),
            viewport={"width": 1280, "height": 900},
        )
        context.add_cookies([{
            "name": "li_at", "value": li_at,
            "domain": ".linkedin.com", "path": "/",
            "httpOnly": True, "secure": True,
        }])
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        if _is_blocked(page):
            browser.close()
            raise RuntimeError(
                "LinkedIn redirected to a login/checkpoint wall — your LI_AT "
                "cookie may be expired, or the session is being challenged. "
                "Re-copy the cookie, or rerun with --show to solve a CAPTCHA."
            )

        # Type the keyword into the alumni search box (best-effort).
        if keyword:
            _search_alumni(page, keyword)

        # Scroll-and-collect loop.
        stagnant = 0
        for _ in range(max_scrolls):
            for a in page.query_selector_all("a[href*='/in/']"):
                cleaned = _clean(a.get_attribute("href") or "")
                if cleaned:
                    found.add(cleaned)
            if len(found) >= max_profiles:
                break
            before = len(found)
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(2500)
            _click_show_more(page)
            stagnant = stagnant + 1 if len(found) == before else 0
            if stagnant >= 3:        # no new results after 3 scrolls — stop
                break

        browser.close()

    return sorted(found)[:max_profiles]


def _is_blocked(page) -> bool:
    u = page.url
    return any(x in u for x in ("/authwall", "/login", "/checkpoint", "/uas/login"))


def _search_alumni(page, keyword: str) -> None:
    """Type into the alumni keyword search box, trying a few known selectors."""
    selectors = [
        "input[placeholder*='Search alumni']",
        "input[aria-label*='Search alumni']",
        "input[placeholder*='by name']",
        "input[type='text']",
    ]
    for sel in selectors:
        box = page.query_selector(sel)
        if box:
            try:
                box.click()
                box.fill(keyword)
                box.press("Enter")
                page.wait_for_timeout(3500)
                return
            except Exception:
                continue


def _click_show_more(page) -> None:
    """Click a 'Show more results' button if LinkedIn renders one."""
    for sel in ("button:has-text('Show more results')",
                "button:has-text('Show more')"):
        btn = page.query_selector(sel)
        if btn:
            try:
                btn.click()
                page.wait_for_timeout(2000)
                return
            except Exception:
                pass
