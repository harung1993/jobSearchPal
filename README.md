# JobSearchPal

A self-hosted job application assistant. Every weekday morning, [Claude Code](https://claude.com/claude-code)
runs headless on a small always-on server. It finds new roles, picks the best fits, tailors your
resume and cover letter, and fills the real application forms with Playwright, PDF uploads
included. **It never presses Submit.** You review each application on a private dashboard and send
it yourself.

![Dashboard](docs/dashboard.png)

<details><summary>Stats page (dark mode) and phone layout</summary>

![Stats](docs/stats-dark.png)
<img src="docs/dashboard-phone.png" width="300" alt="Dashboard on a phone">
</details>

The screenshots use fictional demo data (`scripts/demo-data.py`).

## How it works

```
cron 6:30am ─► claude -p prompts/daily.md
                 ├─ autoapply sync-email               replies, recruiters, proof copies, sorted by a model
                 ├─ autoapply find / alerts / add      roles from job boards, feeds, alert emails
                 ├─ tailored resume + cover letter     python-docx + LibreOffice → PDF
                 ├─ autoapply scan <url>               the form's questions as JSON
                 └─ autoapply fill plan.json           Playwright fills and uploads, then stops
               ─► autoapply brief --send               morning email: picks, replies, status
you ───────► dashboard ─► review answers, decide consents ─► Submit
             or: autoapply-finish <job> on your laptop ─► a real Chrome window, filled in, you submit
```

- **Sources:** public job-board APIs (Greenhouse, Lever, Ashby, Workable, SmartRecruiters) for the
  companies you watch, the RemoteOK, Remotive and HN "Who is hiring?" feeds, and your
  LinkedIn / Built In / Indeed / Glassdoor / Wellfound alert emails.
- **Dashboard:** what's ready for you, latest replies, recruiters, applications per week, and a
  full application list. Light and dark mode, works on a phone.
- **Stats page:** response and interview rates, median time to reply, a funnel, results by
  application platform, reply-time buckets, weeks at your work-search minimum, and pipeline trends.
- **Work-search log:** if you claim unemployment benefits, every application and recruiter
  contact is grouped by Sunday-to-Saturday week, with the original emails and submit screenshots
  saved as proof and a weekly zip in work-search-log columns.
- **Email sorting:** keyword rules only gather candidate emails. A model reads each one and files it as a
  confirmation, interview, rejection, offer, recruiter contact or not job-related, and matches it to the
  application. The **Settings** page sets which sources sort them and in what order, with a model
  picker, a Test button and 30-day usage and cost for each: your Claude subscription (Claude Code,
  no tools), your ChatGPT subscription (Codex CLI, read-only), TypeSafe's Jev decision model or any
  chat model on OpenRouter, or a local Ollama model. If one fails or isn't sure, the next one gets the
  email. Nothing counts until it's sorted, and the **Emails** page lets you change any call; your
  choice survives rescans. The Settings page also picks the Claude model for the daily run.
- **Email:** reads Gmail over read-only IMAP with an app password (nothing is marked, moved or
  deleted), and sends you one morning summary over SMTP.

## What it will not do

- **Submit for you.** The CLI has no submit command. The only Submit button is on the dashboard,
  behind a review tick box, and each approval covers one click.
- **Answer consents or self-ID questions.** Privacy acknowledgements, AI-policy consents and
  demographic questions are always held for you (`hold_for_review` in `profile.yaml`).
- **Get past bot checks.** No captcha solving, no reading verification codes, no logins, no
  account creation. When a site asks for a code, use `autoapply-finish` and enter it yourself.
- **Workday and other account-based sites.** Materials are prepared and the role is flagged
  "apply by hand".

`CLAUDE.md` gives Claude these rules and `.claude/settings.json` keeps it from editing the tool's
own code. Those are guardrails, not a sandbox: the review step is the real safety net.

## Setup (about 30 minutes)

You need an always-on Linux box (Ubuntu 24.04 tested), a Claude subscription for Claude Code,
and ideally [Tailscale](https://tailscale.com) so the dashboard is reachable only from your devices.

1. Clone this repo to `~/auto-apply` on the server and run `bash scripts/install.sh`.
2. Copy the examples and fill them in:
   - `config.example.yaml` → `config.yaml` (boards to watch, title keywords, dashboard host,
     work-search label and weekly minimum)
   - `profile.example.yaml` → `profile.yaml` (your contact details and standard answers)
   - `.env.example` → `.env` (a long random `AUTOAPPLY_TOKEN`, `REVIEW_URL`, Gmail app password)
3. Put your files in `materials/` (see `materials/README.md`), and write `CLAUDE.local.md` with
   personal facts Claude should use: work authorization, titles and dates, pay asks, location
   preferences, how to name files. Both are gitignored.
4. Run `claude` once in the folder to sign in and trust it.
5. `pytest` to check the form filler, then `bash scripts/daily.sh` for a first run.
6. Start the dashboard as a service and schedule the daily run:
   ```
   cp scripts/autoapply-review.service ~/.config/systemd/user/
   systemctl --user enable --now autoapply-review
   sudo loginctl enable-linger $USER
   crontab -e   # add the line from the top of scripts/daily.sh
   ```
7. Open `REVIEW_URL?t=<your token>` once on each device. A cookie remembers it.
8. Optional, on your laptop: `bash scripts/mac-setup.sh <ssh-host>` installs `autoapply-finish`.

Updating later: `git pull && .venv/bin/pip install -e . && systemctl --user restart autoapply-review`.

## Commands

```
autoapply find                         new matching roles from boards and feeds
autoapply board add ATS SLUG           watch another company's board (checked first)
autoapply alerts [--days N]            job-alert emails as text
autoapply sync-email [--days N] [--rebuild]   replies and recruiter emails → sorted → dashboard and proof
autoapply review-emails                sort emails still waiting (see email_classifier)
autoapply emails [--pending]           filed emails as JSON;  emails set ID KIND  settles one by hand
autoapply import-history FILE.json     load past applications (tracker rows)
autoapply add URL / list / show ID / skip ID
autoapply scan URL                     a form's questions as JSON
autoapply fill PLAN.json [--job ID]    fill, upload, screenshot, stop before Submit
autoapply brief [--send]               the morning summary
autoapply serve                        the dashboard
autoapply finish JOB_ID                (laptop) open the filled form in Chrome for you to submit
```

## Layout

```
autoapply/cli.py         the commands Claude calls
autoapply/fillers/       scan.js reads any form; base.py fills and submits per ATS
autoapply/finder.py      job-board APIs and feeds
autoapply/emailsync.py   gathers candidate emails, proof copies, applies a settled kind
autoapply/classify.py    model review of waiting emails (Claude Code, Codex, Jev, OpenRouter, Ollama)
autoapply/prefs.py       Settings page choices (data/settings.json) and saved API keys
autoapply/stats.py       dashboard and stats numbers
autoapply/web.py         dashboard, review page, work-search log
autoapply/templates/     HTML (no build step, no JavaScript framework)
prompts/daily.md         the daily instructions for Claude
CLAUDE.md                standing rules for Claude (personal facts go in CLAUDE.local.md)
tests/                   local fixture forms and tests
```

## Development

```
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/playwright install chromium
.venv/bin/pytest
python3 scripts/demo-data.py /tmp/demo && cd /tmp/demo && autoapply serve   # http://127.0.0.1:8799
```

The filler is tested against local copies of each ATS's form layout (`tests/fixtures/`). Real
forms change, so expect to tweak labels and selectors in `autoapply/fillers/`. Issues and pull
requests are welcome, especially new ATS fillers and better reply classification.

## License

MIT. See [LICENSE](LICENSE).
