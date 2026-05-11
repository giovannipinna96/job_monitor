# Job Monitor

Automated monitor for company career pages, with real-time Telegram
notifications when new job postings are published.

## What it does

Tired of job aggregators (LinkedIn, Indeed) full of noise? Want to track
only the companies you actually care about, but manually visiting 40 career
sites every day is impractical?

This monitor:

- Visits every 30 minutes the career pages of **39 preconfigured companies**
  (Google, Anthropic, OpenAI, Stripe, JP Morgan, Mistral, etc.).
- Extracts the list of all open job postings.
- Compares against the previous state and identifies **new** ones.
- Sends a **Telegram** notification for each new posting with title,
  location and direct link.
- Persists the state of seen jobs to disk, so reopening the script doesn't
  spam old notifications.

## How it works

The challenge with career pages is that every company uses a different
Applicant Tracking System (ATS): Greenhouse, Lever, Ashby, Workday, Oracle
Cloud, Eightfold, Phenom... each with its own HTML format and API.

The monitor uses a hybrid strategy:

### "API-first" strategy

For the main ATS platforms, the monitor calls the **public JSON APIs**
directly instead of scraping rendered HTML:

| ATS | API endpoint |
|---|---|
| **Greenhouse** | `https://boards-api.greenhouse.io/v1/boards/{token}/jobs` |
| **Lever** | `https://api.lever.co/v0/postings/{company}?mode=json` |
| **Ashby** | POST GraphQL `https://jobs.ashbyhq.com/api/non-user-graphql` |
| **Workday** | POST `https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` |
| **Oracle HCM** | GET `https://{tenant}.fa.oraclecloud.com/hcmRestApi/...` |
| **Eightfold** | GET `https://{tenant}.eightfold.ai/api/apply/v2/jobs` |
| **Amazon** | GET `https://www.amazon.jobs/en/search.json` |
| **Microsoft** | GET `https://gcsservices.careers.microsoft.com/search/api/v1/search` |
| **Booking.com (Phenom)** | GET `https://jobs.booking.com/api/jobs` |
| **SAP** | static scraping `https://jobs.sap.com/search/` |

This approach is:
- Much faster (~1s vs ~15s with Playwright)
- Robust against CSS selector changes
- Returns ALL postings, even past the first page

### "Playwright fallback" strategy

For sites that don't expose a usable API (either because they don't have
one, or because they disabled it to prevent scraping), the monitor uses
**Playwright**, a headless browser that renders the page exactly like
Chrome. For these sites the monitor:

- Opens the page with a real headless browser.
- Dismisses cookie banners using ~30 selectors (English + Italian).
- Waits for dynamic JS-driven loading.
- Scrolls / paginates through results.
- Extracts titles via a library of CSS selectors specific to each platform.

Playwright is used for: Google, Meta, McKinsey, Goldman Sachs, Optiver,
Uber, Microsoft (new SPA), Generali, UniCredit, UBS, Morgan Stanley, Jane
Street, Revolut, Samsung, Booking (advanced search), Zalando, Bayer, Bain,
Bending Spoons, and all Phenom-based sites (BCG X, Snowflake, Allianz,
Barclays).

### Handler autodetection

When you add a new site to `sites.json`, the monitor recognizes the ATS
platform automatically from the URL:

```python
"boards.greenhouse.io"   -> Greenhouse handler
"lever.co"               -> Lever handler
"ashbyhq.com"            -> Ashby handler
"myworkdayjobs.com"      -> Workday handler
"oraclecloud.com"        -> Oracle handler
"eightfold.ai"           -> Eightfold handler
... etc
```

For sites with unrecognizable URLs there is an explicit map of custom
handlers (e.g. `careers.snowflake.com` -> Phenom, `jobs.sap.com` -> SAP
scraper). Everything else falls back to the generic Playwright handler.

You can always **force a specific handler** in `sites.json` by adding the
`"type"` field:
```json
{ "name": "Nexi", "url": "...", "type": "oracle" }
```

## Project structure

```
job-monitor/
├── job_monitor.py              Main script
├── sites.json                  List of sites to monitor
├── settings.json               Telegram token, chat ID, interval
├── seen_jobs.json              Already-seen state (auto-updated)
├── requirements.txt            Python dependencies
├── job_monitor.log             Execution log (auto-generated)
├── README.md                   This file
├── SETUP_GITHUB_ACTIONS.md     GitHub Actions deployment guide
├── .gitignore                  Excludes settings/sites/log from repo
└── .github/
    └── workflows/
        └── job-monitor.yml     GitHub Actions workflow (cron 30 min)
```

### Anatomy of `job_monitor.py`

The code is organized in logical sections, each marked with a `# ---`
comment:

| Section | What it contains |
|---|---|
| **Helpers** | `safe_get`, `safe_post`, `make_id`, `site_key` |
| **ATS detection** | `detect_handler()` - URL -> handler name mapping |
| **API handlers** | one function per ATS: `handle_greenhouse`, `handle_lever`, `handle_ashby`, `handle_workday`, `handle_oracle`, `handle_microsoft`, `handle_amazon`, `handle_eightfold`, `handle_sap`, `handle_phenom_booking`, `handle_stripe_native` |
| **Playwright handler** | `handle_playwright()` with profiles (selectors, pagination, cookie banner) |
| **Phenom** | `handle_phenom()` - Playwright wrapper with Phenom-specific selectors |
| **HTML extraction** | `extract_with_profile()` - tries selectors in cascade |
| **Dispatcher** | `HANDLERS` dict + `fetch_jobs()` |
| **Telegram** | `send_telegram`, `format_message` |
| **State** | `load_state`, `save_state`, `cleanup_state` |
| **Main loop** | `check_site`, `run_cycle`, `main` |

Adding a new handler for an unsupported ATS requires:
1. Write `handle_xxx(url, name) -> list[dict]` returning
   `[{"title", "url", "location"}, ...]`.
2. Register it in the `HANDLERS` dict.
3. Add it to `detect_handler()` if you want URL-based autodetection.

## Setup

### Requirements
- Python 3.10+
- pip
- ~200 MB free disk space (for Playwright's Chromium)

### Installation

```bash
pip install -r requirements.txt
playwright install chromium
```

### Telegram configuration

1. On Telegram, search **@BotFather**, send `/newbot`, follow the
   instructions and copy the **token** it gives you.
2. Search **@userinfobot** and copy your **chat ID** (an integer).
3. Create `settings.json` (or edit the existing one):
   ```json
   {
     "telegram_token": "1234567890:AAaaBBbbCCccDDdd...",
     "telegram_chat_id": "123456789",
     "check_interval_minutes": 30
   }
   ```

## Usage

### Test mode (recommended for the first run)

Runs a single cycle without sending notifications and without saving state.
Useful to verify that every site returns > 0 postings.

```bash
python job_monitor.py --test
```

Test a single site:
```bash
python job_monitor.py --test --site=Anthropic
python job_monitor.py --test --site="Morgan Stanley"
```

### Local mode (continuous loop)

Starts the monitor in foreground, checks all sites every
`check_interval_minutes` minutes (default 30), sends notifications for new
postings and saves state.

```bash
python job_monitor.py
```

Stop with `Ctrl+C`. To keep it running after closing the window, use Task
Scheduler on Windows, systemd or `nohup` on Linux.

### "Once" mode (for cron / CI)

A single cycle, sends notifications, saves state, then exits. Designed for
external schedulers (cron, GitHub Actions, etc.).

```bash
python job_monitor.py --once
```

## Adding or removing sites

Edit `sites.json` adding a line:

```json
{ "name": "MyCompany", "url": "https://boards.greenhouse.io/mycompany" }
```

The file is reloaded **on every cycle**: no restart needed. On the next
loop the monitor will include the new site.

If the URL doesn't match a known ATS automatically, force a handler:

```json
{ "name": "MyCompany", "url": "https://...", "type": "playwright" }
```

Possible values for `"type"`: `greenhouse`, `lever`, `ashby`, `workday`,
`oracle`, `eightfold`, `amazon`, `microsoft`, `sap`, `phenom`,
`phenom_booking`, `stripe_native`, `playwright`, `generic`.

## Deployment with GitHub Actions

If you don't want to keep your PC always on, you can run the monitor on
GitHub Actions with cron. The full step-by-step setup is in
[SETUP_GITHUB_ACTIONS.md](./SETUP_GITHUB_ACTIONS.md); here are the
essentials.

### Architecture

```
GitHub Actions (Ubuntu runner)
   |
   | every 30 min (cron)
   v
1. checkout repo
2. install Python + playwright + chromium (cached)
3. rebuild settings.json and sites.json from Secrets
4. run: python job_monitor.py --once
5. if seen_jobs.json changed: git commit + git push
```

### File mapping

| File | Where it lives |
|---|---|
| `job_monitor.py`, `requirements.txt`, `README.md` | in the **public repo** |
| `.github/workflows/job-monitor.yml` | in the **public repo** (the workflow itself) |
| `seen_jobs.json` | in the **public repo**, committed by the bot on every run |
| `settings.json` | in **GitHub Secrets** as `SETTINGS_JSON` (excluded from repo via `.gitignore`) |
| `sites.json` | in **GitHub Secrets** as `SITES_JSON` (excluded from repo via `.gitignore`) |
| `job_monitor.log` | not committed, uploaded as run artifact (retained 7 days) |

This way, even with a **public** repo, the list of sites you monitor and
the Telegram credentials remain private. The `seen_jobs.json` contains only
titles of publicly accessible job postings - safe to keep in the repo.

### Repo visibility

**Public**: unlimited Actions minutes. Recommended.

**Private**: free plan grants you 2000 min/month, but a 30-min cycle eats
~7200 min/month - you'd run out. You'd need to drop to every 90-120 min
or upgrade to a paid plan.

### The 4 setup steps (see the guide for exact commands)

1. **Create a public repo** on GitHub and `git push` the code. The
   `.gitignore` automatically excludes `settings.json` and `sites.json`.
2. **Add two GitHub Secrets** in *Settings > Secrets and variables >
   Actions*:
   - `SETTINGS_JSON`: paste the contents of your `settings.json`.
   - `SITES_JSON`: paste the contents of your `sites.json`.
3. **Set permissions**: *Settings > Actions > General > Workflow
   permissions > Read and write permissions* (required to commit
   `seen_jobs.json`).
4. **Test manual run**: tab *Actions > Job Monitor > Run workflow*.

After the first run, the workflow runs by itself every 30 minutes.

### Post-setup changes

| Change | How |
|---|---|
| Add/remove sites | update the `SITES_JSON` secret |
| Change Telegram token | update the `SETTINGS_JSON` secret |
| Change cron frequency | edit `.github/workflows/job-monitor.yml`, `git push` |
| Change code | `git push` of `job_monitor.py` |

### Limits to be aware of

- **Cron is not punctual**: GitHub Actions cron doesn't guarantee timing.
  You can have 5-15 minute delays during peak load.
- **Runner IP**: GitHub runners use known Azure IP ranges. Some aggressive
  anti-bot sites (Cloudflare, Eightfold with PCSX disabled) block them.
  Not an issue for standard API handlers (Greenhouse, Lever, Ashby,
  Workday, Oracle). Can be an issue for Playwright sites.
- **Concurrency**: the workflow uses `concurrency: job-monitor` to prevent
  overlapping runs. If a run takes longer than 30 min, the next one is
  queued instead of starting in parallel.

## Persistent state

The state of already-seen postings is saved in `seen_jobs.json`:

```json
{
  "a3b2c1d0e9f8": {
    "name": "Anthropic",
    "jobs": {
      "1f2e3d4c5b6a7890": {
        "title": "Software Engineer, Frontiers",
        "seen": "2026-05-09T15:30:42.123456"
      }
    }
  }
}
```

- The site key is `sha256(name|url)[:12]`.
- The job key is `sha256(title|url)[:16]`.
- Postings not seen for more than 90 days are removed automatically (see
  `cleanup_state()`).

**Full reset**: delete `seen_jobs.json`. It will be recreated on the next
run.

## Logging

All activity is logged to:
- **stdout** (so you see it in the terminal)
- **`job_monitor.log`** (append-only)

Example output:
```
2026-05-10 10:15:32 [INFO] Sito: Anthropic
2026-05-10 10:15:32 [INFO]   URL: https://job-boards.greenhouse.io/anthropic
2026-05-10 10:15:32 [INFO]   Handler: greenhouse
2026-05-10 10:15:33 [INFO]   -> 424 offerte trovate
2026-05-10 10:15:33 [INFO]   NEW: Research Engineer, Alignment
```

## Troubleshooting

### A site returns 0 postings

1. **Check the URL**: open it in your browser. If it returns 404 or
   redirects, update it in `sites.json`. Companies change ATS often.
2. **Single-site test**: `python job_monitor.py --test --site="Name"` and
   inspect the detailed log.
3. **Platform change**: if the company moved (e.g. from Greenhouse to
   Ashby), update the URL. The autodetection will take care of the rest.

### A site returns 403 / blocks

Some sites have anti-bot protection (Cloudflare, Eightfold PCSX, etc.).
Possible workarounds:
- Force `"type": "playwright"`: the headless browser is less suspicious
  than raw HTTP calls.
- On GitHub Actions the runner IP may be blacklisted: use the local
  monitor from your PC.

### Playwright error "Executable doesn't exist"

```bash
playwright install chromium
```

### Telegram doesn't receive notifications

- Check token and chat ID in `settings.json`.
- Manually send a message to your bot from Telegram (the bot must have
  written to you at least once for the chat ID to be valid).
- Quick test:
  ```bash
  curl "https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>&text=test"
  ```

## License

Personal use. No guarantee of perpetual operation: career pages change
often and may require updating URLs in `sites.json`.
