# GPU Outreach

This tool finds the people who work on GPU-backed loans, ranks them, and drafts a message for each channel. It then tracks each conversation. The goal is customer discovery: learn who feels the problem and how they work today.

You send every message yourself. The tool never sends a message, and it never logs in to LinkedIn or X.

```
research JSON / CSV -> qualify -> score -> draft per channel -> dashboard -> you send -> track replies
```

## Setup

1. Create the environment.

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Fill in `config.local.yaml`. Git ignores this file.

3. Import the prospects, then qualify, score and draft them.

   ```bash
   .venv/bin/python cli.py import-research data/prospects.json
   .venv/bin/python cli.py run
   ```

4. Start the dashboard at http://127.0.0.1:5000.

   ```bash
   .venv/bin/python app.py
   ```

## Daily use

1. Open the dashboard and sort by score.
2. Open a prospect. Read the source. Check the role if the page warns you.
3. Copy the message and send it yourself.
4. Click "I sent this".
5. Update the stage when the person replies. Write your call notes on the same page.

## Commands

| Command | Action |
|---|---|
| `import-research FILE` | Import a JSON array of prospects. |
| `import-csv FILE` | Import a CSV. The columns use the same names as the JSON keys. |
| `run` | Qualify, score and draft all prospects. This keeps stages and notes. |
| `list` | Show the ranked prospects. Use `--segment`, `--stage`, `--min-score`. |
| `gaps` | Show the prospects that have no opener. |

## Prospect fields

`name, title, firm, segment, why_relevant, hook, opener, linkedin_url, x_handle, email, source_url, source_date, confidence, cmu_link`

- `hook` is a public fact about the person.
- `opener` is the short sentence that starts each message. A prospect without an opener gets no drafts.
- `source_url` is required. A prospect without a source is blocked.

## Rules in the code

- **Fit:** One sentence must link the person to both an asset term and a finance term. See `outreach/qualify.py`.
- **Do not contact:** Competitors and journalists get no drafts. Change this list in `config.yaml`.
- **Honest copy:** The templates do not claim customers, pilots or YC backing. A test checks this.
- **Length:** The LinkedIn note limit is 200 characters. Set it to 300 if you have Premium.

## Private data

The GitHub repo is public. Git ignores `data/`, `config.local.yaml` and the database. Keep prospect data and your pitch in those places.

## Tests

```bash
.venv/bin/python -m pytest -q
```
