"""LinkedIn profile spider (Scrapy + Playwright, authenticated session).

──────────────────────────────────────────────────────────────────────────────
READ THIS FIRST — honest limitations
──────────────────────────────────────────────────────────────────────────────
LinkedIn is hostile to scraping by design. This spider takes the least-abusive
authenticated path (Option A):

  * It renders pages in a real headless browser (Playwright), so JavaScript
    content actually loads — unlike plain Scrapy, which only sees a login wall.
  * It authenticates by reusing YOUR OWN session: set the LI_AT environment
    variable to your `li_at` cookie (copied from a browser where you're logged
    in). No password is ever entered or stored.
  * It reads a file of profile URLs (one per line) — it does NOT brute-force
    LinkedIn search, which gets you banned instantly.
  * It is polite: one request at a time, several seconds apart, autothrottled.

It does NOT rotate proxies, randomize fingerprints, or solve CAPTCHAs. Using your
own account this way still violates LinkedIn's ToS and can get your account
restricted — that's the trade-off you're accepting. The experience/profile
selectors below are best-effort: LinkedIn changes its DOM often, so expect to
adjust them. If LinkedIn blocks you, switch to `import-csv` — scoring, drafting,
and the dashboard work identically on any data source.
──────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import json
import os
import re

import scrapy

try:
    from scrapy_playwright.page import PageMethod
    HAS_PLAYWRIGHT = True
except ImportError:                       # surfaced with a helpful message below
    PageMethod = None
    HAS_PLAYWRIGHT = False


class LinkedInSpider(scrapy.Spider):
    name = "linkedin"

    custom_settings = {
        # LinkedIn's robots.txt disallows every /in/ profile path, so obeying it
        # means scraping nothing. You've explicitly chosen to scrape profiles in
        # your own session, so this is off — politeness is enforced via the delay
        # and single-concurrency settings below instead.
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 5,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "CONCURRENT_REQUESTS": 1,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        # ── Playwright wiring: render JS in a real Chromium ──────────────────
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_LAUNCH_OPTIONS": {"headless": True},
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 30000,
        "ITEM_PIPELINES": {
            "recruiter.scraper.pipelines.SQLitePipeline": 300,
        },
    }

    def __init__(self, urls_file: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.urls_file = urls_file

    def start_requests(self):
        if not HAS_PLAYWRIGHT:
            self.logger.error(
                "scrapy-playwright is not installed. Run:\n"
                "  pip install scrapy-playwright && playwright install chromium"
            )
            return
        if not self.urls_file or not os.path.exists(self.urls_file):
            self.logger.error(
                "No urls file. Create a text file of LinkedIn profile URLs "
                "(one per line) and pass urls_file=<path>."
            )
            return

        li_at = os.environ.get("LI_AT")
        if not li_at:
            self.logger.error(
                "LI_AT not set. Option A needs your own session cookie:\n"
                "  export LI_AT='<li_at value from your browser>'\n"
                "Without it you'll just hit the login wall."
            )
            return

        # Inject the cookie into the browser context via storage_state, so the
        # rendered page is authenticated as you.
        storage_state = {
            "cookies": [{
                "name": "li_at", "value": li_at,
                "domain": ".linkedin.com", "path": "/",
                "httpOnly": True, "secure": True,
            }],
            "origins": [],
        }
        meta = {
            "playwright": True,
            "playwright_context": "auth",
            "playwright_context_kwargs": {"storage_state": storage_state},
            "playwright_page_methods": [
                # Wait for the main profile column to render (best-effort).
                PageMethod("wait_for_selector", "main", timeout=20000),
            ],
        }

        with open(self.urls_file, encoding="utf-8") as fh:
            for line in fh:
                url = line.strip()
                if url and not url.startswith("#"):
                    yield scrapy.Request(url, meta=dict(meta), callback=self.parse,
                                         errback=self.on_error)

    def on_error(self, failure):
        self.logger.warning("Request failed: %s", failure.value)

    def parse(self, response):
        if "/authwall" in response.url or "/login" in response.url or "/checkpoint" in response.url:
            self.logger.warning(
                "Hit auth/login/checkpoint wall for %s — your LI_AT cookie may be "
                "expired or LinkedIn is challenging the session.", response.url
            )
            return

        item = {
            "name": None, "headline": None, "location": None,
            "profile_url": response.url.split("?")[0],
            "email": None,                 # LinkedIn never exposes email via scraping
            "grad_year": None, "school": None,
            "experience": None, "raw_text": None, "source": "linkedin",
        }

        # 1) Structured data when present (most reliable)
        for blob in response.css('script[type="application/ld+json"]::text').getall():
            try:
                self._from_jsonld(json.loads(blob), item)
            except ValueError:
                continue

        # 2) Rendered DOM fallbacks
        item["name"] = item["name"] or self._clean(
            response.css("main h1::text").get()
        ) or response.css('meta[property="og:title"]::attr(content)').get()
        item["headline"] = item["headline"] or self._clean(
            response.css("main .text-body-medium::text").get()
        ) or response.css('meta[property="og:description"]::attr(content)').get()
        item["location"] = item["location"] or self._clean(
            response.css("main .text-body-small.inline::text").get()
        )

        # 3) Education + Experience sections (best-effort; LinkedIn DOM shifts often)
        item["education"] = self._extract_section(response, "education")
        item["experience"] = self._extract_section(response, "experience")

        # 4) Whole-page text blob — used ONLY for keyword scoring, never to decide
        #    school/grad_year (that would re-introduce sidebar false positives).
        item["raw_text"] = " ".join(
            t.strip() for t in response.css("main ::text").getall() if t.strip()
        ) or item["experience"]

        # NOTE: grad_year and school are intentionally left for the verification
        # step, which requires CMU + 2030 to co-occur in one education entry.
        # See recruiter/verification.py.

        yield item

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _clean(value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @staticmethod
    def _extract_section(response, anchor_id: str) -> str | None:
        """Pull a readable summary of a profile section (e.g. "education",
        "experience").

        LinkedIn anchors each section with <div id="{anchor_id}">; the visible
        entries live in the following <section>/<ul>. This is intentionally
        forgiving and may need tweaking when LinkedIn changes its markup.
        Scoping to the anchored section (not the whole page) is what keeps
        sidebar/feed content out of the data.
        """
        section = response.xpath(
            f'//section[.//div[@id="{anchor_id}"]] | '
            f'//div[@id="{anchor_id}"]/ancestor::section[1]'
        )
        if not section:
            return None
        lines, seen = [], set()
        for li in section.css("li"):
            txt = " ".join(t.strip() for t in li.css("::text").getall() if t.strip())
            # collapse LinkedIn's duplicated accessibility text
            txt = re.sub(r"\s+", " ", txt).strip()
            if txt and txt not in seen and len(txt) > 3:
                seen.add(txt)
                lines.append("• " + txt)
            if len(lines) >= 12:
                break
        return "\n".join(lines) or None

    @staticmethod
    def _from_jsonld(data: dict, item: dict) -> None:
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("@type") == "Person":
                item["name"] = item["name"] or node.get("name")
                item["headline"] = item["headline"] or node.get("jobTitle") or item["headline"]
                addr = node.get("address") or {}
                if isinstance(addr, dict):
                    item["location"] = item["location"] or addr.get("addressLocality")
                edu = node.get("alumniOf") or []
                if isinstance(edu, dict):
                    edu = [edu]
                names = [e.get("name", "") for e in edu if isinstance(e, dict)]
                if names:
                    item["raw_text"] = (item["raw_text"] or "") + " " + " ".join(names)
