# GPU Outreach

This tool finds the people who work on GPU-backed loans, ranks them, and drafts a message for each channel. It then tracks each conversation. The goal is customer discovery: learn who feels the problem and how they work today.

The dashboard never sends anything. The optional `send-linkedin` command sends connection notes through your own logged-in Chrome, and it stops at the first LinkedIn warning. It never handles your password or solves a check. LinkedIn does not allow this automation, so your account carries the risk.

```
research JSON / CSV -> qualify -> score -> draft per channel -> dashboard -> you send -> track replies
```

## Setup

1. Create the environment.

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Add your name to `config.local.yaml`. Git ignores this file. Short messages work without it.

3. Import the prospects, then qualify and score them.

   ```bash
   .venv/bin/python cli.py import-research data/prospects.json
   .venv/bin/python cli.py run
   ```

4. Start the dashboard at http://127.0.0.1:5000.

   ```bash
   .venv/bin/python app.py
   ```

## Two senders: the shared queue

Two people can send from two LinkedIn accounts and never contact the same person. One shared Postgres database holds the prospect queue and a claim list. Before the sender opens a profile, it claims the person. Only one sender can hold a claim, so the other sender skips that person. The sender claims the profile URL too, so a different firm spelling still matches.

The sender releases a claim only when it skipped the person before Send. After a crash or a warning, the claim stays. If it cannot reach the database, it stops and sends nothing.

### First sender: make the database once

1. Make a free Postgres database on [Neon](https://neon.tech) or [Supabase](https://supabase.com). Copy its connection string.
2. Add the `shared` block to `config.local.yaml`. Use the example in `config.local.example.yaml`.
3. Run `sync`. It pushes your prospects to the queue and claims everyone you already contacted.

   ```bash
   .venv/bin/python cli.py sync
   ```

4. Send the connection string to the second sender in a private message. Do not put it in the repo.

### Second sender: set up

1. Clone the repo and make the environment.

   ```bash
   git clone https://github.com/jagong-cmu/outreach-recruiter.git
   cd outreach-recruiter
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Copy the example config. Fill in your name, the connection string and a sender name that is not the first sender's.

   ```bash
   cp config.local.example.yaml config.local.yaml
   ```

3. Get the queue. This also qualifies and scores the prospects.

   ```bash
   .venv/bin/python cli.py sync
   ```

4. Start Chrome for the sender. Log in to LinkedIn by hand in the new window. Keep the window open.

   ```bash
   ./scripts/start-chrome.sh
   ```

5. Do a dry run first. It stops before Send.

   ```bash
   .venv/bin/python cli.py send-linkedin --dry-run --limit 2
   .venv/bin/python cli.py send-linkedin --limit 10
   ```

Run `sync` again when either sender adds prospects. Run `shared-status` to see the claims for each sender.

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
| `run` | Qualify and score all prospects, and check draft lengths. This keeps stages and notes. |
| `list` | Show the ranked prospects. Use `--segment`, `--stage`, `--min-score`. |
| `send-linkedin` | Send notes in your Chrome. Use `--dry-run`, `--name`, `--limit`, `--delay`, `--until`. |
| `gaps` | Show the prospects that have no personal fact. |
| `sync` | Push your prospects and contacts to the shared queue, pull the others, then score. |
| `shared-status` | Show the shared queue size and the claims for each sender. |

## Prospect fields

`name, title, firm, segment, why_relevant, hook, opener, topic, linkedin_url, x_handle, email, source_url, source_date, confidence, cmu_link`

- `hook` is a public fact about the person.
- `topic` fills the "learn how ____" blank for one person. Without it, the rules in `config.yaml` choose a topic from the title and the segment.
- `opener` is an optional personal fact. Long messages use it. The LinkedIn note does not, because of the length limit.
- `source_url` is required. A prospect without a source is blocked.

## Rules in the code

- **Fit:** One sentence must link the person to both an asset term and a finance term. See `outreach/qualify.py`.
- **Do not contact:** Competitors and journalists get no drafts. Change this list in `config.yaml`.
- **Frame:** Each message says that you are a CMU student who builds in GPU financing and wants to learn. The templates do not pitch, and they do not claim customers, pilots or YC backing. A test checks this.
- **Connection note:** The note is the founder's exact wording in lower case. Only the name changes, and alumni get "I'm a fellow Tartan". Long messages carry a checked fact for the segment.
- **Channels:** `templates.enabled_channels` sets the drafts that show. Today it is the LinkedIn connection note only.
- **Calendar:** Set `calendar_url` in `config.local.yaml`. Every message carries the link.
- **CMU alumni:** The dashboard page `/alumni` has search links for your own LinkedIn account and a form to add people. Alumni get a warmer intro and a score bonus.
- **Timing:** The interview line comes from `interview_date`. It changes after the interview.
- **Length:** The LinkedIn note limit is 200 characters. Set it to 300 if you have Premium.

## Private data

The GitHub repo is public. Git ignores `data/`, `config.local.yaml` and the database. Keep prospect data and your pitch in those places.

## Tests

```bash
.venv/bin/python -m pytest -q
```
