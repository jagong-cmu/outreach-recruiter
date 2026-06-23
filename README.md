# 📣 Outreach Recruiter

Find, score, and draft personalized outreach to potential recruits for your
club's **Outreach Committee** — targeting **incoming CMU freshmen, Class of
2030** with strong **outreach/community** and **marketing/social-media**
backgrounds.

```
 data source ──► [ candidate store ] ──► ATS scoring ──► email drafts ──► dashboard
 (LinkedIn /                (SQLite)        (0–100)      (for review)     (approve)
  CSV / form)
```

The scoring engine, draft generator, and dashboard are **source-agnostic** — they
work the same whether candidates come from the LinkedIn scraper, a CSV, or an
opt-in form.

---

## ⚠️ Read before scraping LinkedIn

You asked to scrape LinkedIn with Scrapy, authenticated as you (Option A). The
spider is built (`recruiter/scraper/`) and renders pages in a real headless
browser (Playwright) using your own `li_at` session cookie — but be clear-eyed:

- Scraping LinkedIn **violates its Terms of Service** and, even using your own
  account, can get it **restricted or banned**. That's the trade-off you accept.
- Most Class-of-2030 students are **minors** — extra reason to keep volumes small
  and outreach respectful.
- Two ways to get candidates: **auto-discovery** (`discover` crawls CMU's alumni
  page to find profiles for you — convenient but the most ban-prone step), or a
  **file of profile URLs** you supply. The scrape step is **polite** either way
  (one request at a time, several seconds apart, obeys robots.txt).
- It does **not** rotate proxies, randomize fingerprints, or solve CAPTCHAs. If
  LinkedIn challenges your session, refresh the cookie or switch to a CSV.

**Recommended alternative:** collect candidates via an **opt-in interest form**
(share in the CMU admit Discord/GroupMe) or a **CSV** — you get clean, consented
data *with* emails, and everything downstream is identical. Use `import-csv`.

---

## Setup

```bash
pip install -r requirements.txt
playwright install chromium     # one-time: browser for authenticated scraping
```

## Quick start (with fictional sample data)

```bash
python cli.py init           # create recruiter.db
python cli.py load-sample    # load fictional test candidates
python cli.py verify         # confirm each is CMU class of 2030 (see below)
python cli.py score          # ATS-score the verified candidates only
python cli.py draft          # generate email drafts for matches
python app.py                # dashboard at http://127.0.0.1:5000
```

(`score` runs verification automatically too — the separate `verify` step just
lets you inspect the verdicts first with `python cli.py verify --verbose`.)

## Using your own data

**CSV** (columns: `name, headline, location, profile_url, email, grad_year, school, raw_text`):
```bash
python cli.py import-csv people.csv
python cli.py score && python cli.py draft
```

**LinkedIn — authenticated, using your own session** (read the caveats above):

The spider renders pages in a real headless browser (Playwright) and reuses
*your* logged-in session via the `li_at` cookie. No password is entered or stored.

First, copy your session cookie from a browser where you're logged in:
Chrome → DevTools (⌥⌘I) → Application → Storage → Cookies →
`https://www.linkedin.com` → click `li_at` → copy its Value.

```bash
export LI_AT="<your li_at value>"
```

**Auto-discovery (no URLs needed) — find candidates for you:**
```bash
python cli.py discover --show --scrape   # find CMU '30 profiles, then scrape them
python cli.py verify --verbose           # who actually checks out as CMU '30
python cli.py score && python cli.py draft
```
`discover` opens CMU's alumni page in your session, searches "Class of 2030",
scrolls, and collects profile links (capped by `discovery.max_profiles` in
config). `--show` opens a visible browser so you can solve any CAPTCHA; `--scrape`
chains straight into scraping. ⚠️ This is the most ban-prone, most fragile step —
see the discovery warning in `recruiter/discover.py`. Some runs return few/zero.

**Or, scrape a list of URLs you already have:**
```bash
# one LinkedIn profile URL per line in urls.txt
python cli.py scrape urls.txt
python cli.py verify --verbose
python cli.py score && python cli.py draft
```

If the cookie expires or LinkedIn challenges the session, you'll see an
auth/checkpoint warning — refresh the cookie, or switch to `import-csv`. The
experience selectors are best-effort and may need tweaking when LinkedIn changes
its page markup.

## Review & send

Open the dashboard, review each candidate's **score breakdown** and **draft**,
click **Approve** / **Reject**, then **Export approved CSV** for a mail merge
(Gmail "Multi-send", a mail-merge add-on, etc.). **Nothing is ever sent
automatically** — you stay in control of every email.

---

## Verification — "is this actually a CMU Class of 2030 student?"

Naively checking "does the page mention Carnegie Mellon?" and "does it mention
2030?" produces **false positives**: a LinkedIn profile page also contains
"People also viewed", suggested profiles, and the person's feed — so the school
and the year can come from two *different* people and still both appear.

`recruiter/verification.py` fixes this by requiring CMU and 2030 to **co-occur in
the same education entry** (or the person's own headline). Each candidate gets a
status, shown as a badge in the dashboard and a banner on their detail page:

| status       | meaning                                                        | emailed? |
|--------------|----------------------------------------------------------------|----------|
| `verified`   | an Education entry lists CMU **and** an expected grad year 2030 | ✓ yes    |
| `headline`   | the person's headline states CMU + 2030 (e.g. "CMU '30")       | ✓ yes    |
| `provided`   | a CSV/form import supplied the fields; nothing to cross-check   | ✓ yes    |
| `mismatch`   | CMU but a different year, or class-of-2030 at another school    | ✗ no     |
| `unverified` | no trustworthy CMU-class-of-2030 evidence on the profile        | ✗ no     |

Only `verified` / `headline` / `provided` candidates get scored and drafted.
Run `python cli.py verify --verbose` to see exactly why anyone was rejected.

The scraper feeds this by parsing the **Education section specifically** (anchored
on LinkedIn's `id="education"` block) rather than the whole page — that scoping is
what keeps sidebar/feed content out of the decision.

## Tuning the scoring

Everything lives in `config.yaml`:

- `target` — grad year + school keywords used to filter candidates.
- `scoring.categories` — keyword → points dictionaries for **outreach** and
  **marketing** (each capped at its `weight`; both default to 50, summing to 100).
- `scoring.bonuses` — additive bonuses (e.g. leadership).
- `email` — subject/body template with `{first_name}`, `{org}`, `{highlights}`, etc.
- `club` — your name, org, and one-line pitch (used in drafts).

Each score ships with the exact keywords that earned it, shown in the dashboard,
so you can see *why* someone ranked where they did and tune accordingly.

## Project layout

```
config.yaml                 all tunables (club, target, scoring, email)
cli.py                      orchestration: init / import / scrape / score / draft / list
app.py                      Flask review dashboard
recruiter/
  config.py                 config loader
  db.py                     SQLite store (candidates / scores / drafts)
  discover.py               auto-find CMU '30 profiles on LinkedIn (Playwright)
  verification.py           confirms CMU class of 2030 (no false positives)
  scoring.py                ATS scoring engine (transparent, keyword-based)
  email_gen.py              personalized draft generator
  sample_data.py            fictional test candidates
  scraper/
    linkedin_spider.py      Scrapy spider (+ honest caveats)
    pipelines.py            writes scraped items into the store
templates/ static/          dashboard UI
```

## Legal / ethical note

Cold-emailing people (especially minors) carries real obligations — see
**CAN-SPAM** (identify yourself, honor opt-outs, no deception). Prefer
opt-in/consented data, keep volumes sane, always include a way to opt out, and
don't store more personal data than you need.
