# Bonbeach CC — Live Milestone Tracker

This repo pulls live stats from PlayHQ and rebuilds a milestone dashboard,
automatically, once a day, and publishes it as a website via GitHub Pages.

---

## How it works

- `fetch_playhq_data.py` talks to the PlayHQ API, walks every season/grade/
  game for Bonbeach CC, and finds any games not yet reflected in the
  baseline (see below). It aggregates just those NEW games, folds them into
  the career-long baseline totals, and writes the combined result to
  `players_data.json`.
- `build_dashboard.py` injects that data into `dashboard_template.html` and
  writes two identical output files: `Bonbeach-CC-Milestone-Dashboard-LIVE.html`
  (a friendly name) and `index.html` (so GitHub Pages shows it at your site's
  base URL automatically).
- `.github/workflows/update-dashboard.yml` runs both scripts once a day
  (early morning) and whenever you trigger it manually, then commits the
  regenerated HTML (and the updated baseline files) back to this repo —
  which GitHub Pages then serves.

### Full career history — the `baseline/` files

PlayHQ's API only has this club's data back to Summer 2023/24 — that's when
Bonbeach's competitions started being recorded in PlayHQ at all, confirmed
directly on PlayHQ's own public club page. To show the dashboard's real,
full career history (players with 100+ matches going back decades), this
pipeline keeps two extra files:

- `baseline/player_totals.json` — every player's career totals to date,
  originally built from the club's CSV exports and updated automatically
  after that.
- `baseline/counted_game_ids.json` — the list of PlayHQ game IDs already
  folded into the totals above, so a game is never counted twice.

Every run, `fetch_playhq_data.py` only downloads and adds games whose ID
isn't already in that list, then updates both files. **These two files are
the club's permanent record — they must stay committed to the repo (the
workflow does this for you automatically) and should never be manually
deleted or hand-edited.** If they ever go missing, the dashboard will fall
back to only showing PlayHQ-era data (2023/24 onward) until they're
restored from a backup or rebuilt from the CSV exports.

### Milestones Reached — the `milestones/` folder

Every run, the pipeline compares each active player's stats before and
after that run's new games are folded in, and records any milestone tier
(Matches, Runs, Wickets or Catches) they crossed as a result. That record
is kept forever in `milestones/milestones_log.json` and shown on the
dashboard under **"🎉 Milestones Reached"** — a rolling view of the last
30 days, so a milestone reached, say, last week is still visible next time
someone opens the site, not just on the day it happened. **This file should
stay committed to the repo (the workflow does this automatically) so that
history isn't lost.** If it's ever missing, the pipeline just starts a
fresh history from scratch rather than failing — it's a nice-to-have
record, not something the pipeline depends on to run.

---

## One-time setup (things only you can do — GitHub account required)

Claude Code prepared all the code and the automation, but creating things in
*your* GitHub account has to be done by you. Checklist:

1. **Create a new GitHub repository** (public or private, your call — since
   credentials are no longer in the code, it's safe to make it public).
   Don't initialize it with a README (this folder already has one).
2. **Push this folder to it.** From a terminal in this folder:
   ```
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git branch -M main
   git push -u origin main
   ```
3. **Add three repository secrets** (Settings -> Secrets and variables ->
   Actions -> New repository secret):
   - `PLAYHQ_API_KEY`
   - `PLAYHQ_ORG_ID`
   - `PLAYHQ_TENANT` (value: `ca`)
   (Ask Claude, in this conversation, for the actual key/org-ID values if you
   need them again — they're not stored in any file anymore.)
4. **Enable GitHub Pages** (Settings -> Pages):
   - Source: **Deploy from a branch**
   - Branch: **main**, folder: **/ (root)**
   - Save. GitHub will show you the live URL (something like
     `https://<your-username>.github.io/<your-repo>/`). It'll 404 until step 5.
5. **Trigger the workflow once manually** to populate the site immediately,
   rather than waiting for tomorrow's scheduled run: go to the **Actions**
   tab -> **Update Bonbeach Milestone Dashboard** -> **Run workflow**. After
   it finishes (~1-2 minutes), refresh the Pages URL from step 4.

That's it — from here on, it updates itself daily.

---

## Running it yourself locally (optional)

You don't need to do this if the GitHub Actions automation above is set up —
it's only useful for testing changes to the scripts before pushing.

1. Install Python from https://www.python.org/downloads/ (tick "Add to PATH"
   during install on Windows), then `pip install requests tzdata` (`tzdata`
   gives Windows the timezone database it doesn't ship with by default, so
   fixture/result dates convert correctly to Melbourne local time — Mac and
   Linux already have this built in, but installing it there too is
   harmless).
2. Set the credentials as environment variables (PowerShell example):
   ```
   $env:PLAYHQ_API_KEY = "your-key-here"
   $env:PLAYHQ_ORG_ID  = "your-org-id-here"
   ```
   (`PLAYHQ_TENANT` defaults to `ca` if not set.)
3. Run `python fetch_playhq_data.py`, then `python build_dashboard.py`.
4. Open `index.html` in your browser.

---

## If something goes wrong

PlayHQ's API is well-documented but real-world data occasionally has small
surprises (a missing field, an unexpected status value, an endpoint that's
moved — this has already happened once: `/v2/grades/{id}/fixture` silently
became `/v2/grades/{id}/games`). If a scheduled or manual run fails, check
the Actions tab for the error log, and bring the error text back to Claude —
it can usually diagnose and fix the script directly.

## What this does NOT do

- It only counts matches marked `FINAL` in PlayHQ (in-progress, abandoned,
  and cancelled games are skipped).
- It can't independently discover history before Summer 2023/24 from
  PlayHQ — that part comes from the `baseline/` files (see above), so keep
  those committed.

## Files in this folder

| File | What it is |
|---|---|
| `fetch_playhq_data.py` | Talks to PlayHQ, downloads new Bonbeach games, merges them into the baseline. Reads credentials from environment variables — no secrets in this file. |
| `build_dashboard.py` | Injects the fresh data into the dashboard template |
| `dashboard_template.html` | The dashboard design/logic (don't need to touch this) |
| `.github/workflows/update-dashboard.yml` | The daily automation |
| `baseline/player_totals.json` | **Committed** — full career totals per player. This is the club's permanent record; never delete or hand-edit it. |
| `baseline/counted_game_ids.json` | **Committed** — PlayHQ game IDs already counted, so games are never double-counted. |
| `milestones/milestones_log.json` | **Committed** — permanent record of every milestone ever reached, powering the "Milestones Reached" section. |
| `players_data.json` | Generated, gitignored — the combined (baseline + new PlayHQ games) stats, machine-readable |
| `Bonbeach-CC-Milestone-Dashboard-LIVE.html` / `index.html` | Generated, **committed** (see note below) — open either in your browser, or visit the live GitHub Pages URL |

**Note on `index.html`:** unlike `players_data.json`, this one *is* meant to
be committed — GitHub Pages serves it directly, and the daily workflow
updates it in place each run.
