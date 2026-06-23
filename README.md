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

You asked to scrape LinkedIn with Scrapy. The spider is built (`recruiter/scraper/`),
but be clear-eyed about the realities:

- **LinkedIn requires login** for nearly all profile content and is **JS-rendered**,
  so plain Scrapy mostly sees a login wall.
- Scraping LinkedIn **violates its Terms of Service** and can get your account
  banned. Most Class-of-2030 students are **minors** — extra caution warranted.
- The spider reads a **file of profile URLs** (it does *not* brute-force LinkedIn
  search — that gets you blocked instantly), is **polite** (rate-limited, obeys
  robots.txt), and can use **your own** `li_at` session cookie at your own risk.

**Recommended alternative:** collect candidates via an **opt-in interest form**
(share in the CMU admit Discord/GroupMe) or a **CSV** — you get clean, consented
data *with* emails, and everything downstream is identical. Use `import-csv`.

---

## Setup

```bash
pip install -r requirements.txt
```

## Quick start (with fictional sample data)

```bash
python cli.py init           # create recruiter.db
python cli.py load-sample    # load fictional test candidates
python cli.py score          # ATS-score (filters to CMU + class of 2030)
python cli.py draft          # generate email drafts for matches
python app.py                # dashboard at http://127.0.0.1:5000
```

## Using your own data

**CSV** (columns: `name, headline, location, profile_url, email, grad_year, school, raw_text`):
```bash
python cli.py import-csv people.csv
python cli.py score && python cli.py draft
```

**LinkedIn** (read the caveats above):
```bash
# 1. Put one LinkedIn profile URL per line in urls.txt
# 2. (Optional, at your own risk) export your session cookie:
export LI_AT="<your li_at cookie value>"
python cli.py scrape urls.txt
python cli.py score && python cli.py draft
```

## Review & send

Open the dashboard, review each candidate's **score breakdown** and **draft**,
click **Approve** / **Reject**, then **Export approved CSV** for a mail merge
(Gmail "Multi-send", a mail-merge add-on, etc.). **Nothing is ever sent
automatically** — you stay in control of every email.

---

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
