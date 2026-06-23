"""LinkedIn profile spider (Scrapy).

──────────────────────────────────────────────────────────────────────────────
READ THIS FIRST — honest limitations
──────────────────────────────────────────────────────────────────────────────
LinkedIn is hostile to scraping by design:
  * Almost every profile requires login. Logged-out requests get a truncated
    page or a redirect to /authwall.
  * Pages are JavaScript-rendered; plain Scrapy sees only the initial HTML.
  * Aggressive bot detection (rate limits, CAPTCHAs, soft bans) kicks in fast.
  * Scraping LinkedIn violates its Terms of Service, and most class-of-2030
    candidates are minors — extra reason to be cautious.

What this spider does, realistically:
  * Reads a list of LinkedIn profile URLs from a file (one per line) — it does
    NOT brute-force-crawl LinkedIn search (that gets you banned immediately).
  * Optionally attaches YOUR OWN li_at session cookie (env LI_AT) so requests
    are authenticated as you. This is the only way to see real profile content,
    and it's at your own risk re: ToS / account safety.
  * Parses whatever public/structured data it can (JSON-LD + meta + visible
    text), tags grad_year via "Class of 20XX" detection, and yields a candidate
    item into the same DB the rest of the pipeline uses.

If/when LinkedIn blocks this (likely), use a cleaner source — the scoring,
drafting, and dashboard work identically on CSV or opt-in-form data. See README.
──────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import json
import os
import re

import scrapy

GRAD_RE = re.compile(r"\bclass of\s*(20\d{2})\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20\d{2})\b")


class LinkedInSpider(scrapy.Spider):
    name = "linkedin"

    # Polite by default. Scraping responsibly is both more ethical and less
    # likely to get you blocked. Tune in config if you must, but slower is safer.
    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 5,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "CONCURRENT_REQUESTS": 1,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "USER_AGENT": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "ITEM_PIPELINES": {
            "recruiter.scraper.pipelines.SQLitePipeline": 300,
        },
    }

    def __init__(self, urls_file: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.urls_file = urls_file

    def start_requests(self):
        if not self.urls_file or not os.path.exists(self.urls_file):
            self.logger.error(
                "No urls file. Create a text file of LinkedIn profile URLs "
                "(one per line) and pass urls_file=<path>."
            )
            return
        cookies = {}
        li_at = os.environ.get("LI_AT")
        if li_at:
            cookies["li_at"] = li_at
            self.logger.info("Using LI_AT session cookie (authenticated requests).")
        else:
            self.logger.warning(
                "No LI_AT cookie set — expect a login wall on most profiles."
            )

        with open(self.urls_file, encoding="utf-8") as fh:
            for line in fh:
                url = line.strip()
                if url and not url.startswith("#"):
                    yield scrapy.Request(url, cookies=cookies, callback=self.parse)

    def parse(self, response):
        if "/authwall" in response.url or "/login" in response.url:
            self.logger.warning("Hit auth/login wall for %s", response.url)
            return

        item = {
            "name": None,
            "headline": None,
            "location": None,
            "profile_url": response.url.split("?")[0],
            "email": None,          # LinkedIn never exposes email via scraping
            "grad_year": None,
            "school": None,
            "raw_text": None,
            "source": "linkedin",
        }

        # 1) Structured data (most reliable when present)
        for blob in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(blob)
            except ValueError:
                continue
            self._from_jsonld(data, item)

        # 2) Meta tags as fallback
        item["name"] = item["name"] or response.css(
            'meta[property="og:title"]::attr(content)'
        ).get()
        item["headline"] = item["headline"] or response.css(
            'meta[property="og:description"]::attr(content)'
        ).get()

        # 3) Visible text blob for keyword scoring + grad-year detection
        text = " ".join(
            t.strip() for t in response.css("body ::text").getall() if t.strip()
        )
        item["raw_text"] = (item["raw_text"] or "") + " " + text

        full = " ".join(
            str(v) for v in (item["name"], item["headline"], item["raw_text"]) if v
        )
        m = GRAD_RE.search(full)
        if m:
            item["grad_year"] = int(m.group(1))

        low = full.lower()
        if "carnegie mellon" in low or "cmu" in low:
            item["school"] = "Carnegie Mellon University"

        yield item

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
                # Education / alumni hints
                edu = node.get("alumniOf") or []
                if isinstance(edu, dict):
                    edu = [edu]
                names = [e.get("name", "") for e in edu if isinstance(e, dict)]
                if names:
                    item["raw_text"] = (item["raw_text"] or "") + " " + " ".join(names)
