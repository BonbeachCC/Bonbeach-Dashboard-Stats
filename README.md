# Bonbeach CC — Live Milestone Tracker: Setup Guide

This folder pulls live stats from PlayHQ and rebuilds your milestone dashboard.
You do **not** need to understand the code — just follow the steps below.

There are two ways to run this. **Option A is by far the easiest.**

---

## Option A: Use Claude Code (recommended)

1. Install **Claude Code** (Anthropic's tool for exactly this kind of task) — ask me
   "how do I install Claude Code" if you're not sure, or see claude.com/claude-code.
2. Open Claude Code and point it at this folder.
3. Just say: *"Run fetch_playhq_data.py, then build_dashboard.py"*
4. Claude Code will run everything, and — importantly — if anything errors out
   (APIs occasionally return data slightly differently than documented), Claude Code
   can read the error and fix the script on the spot. That's the main advantage over
   Option B.
5. When it's done, open `Bonbeach-CC-Milestone-Dashboard-LIVE.html` in your browser.

## Option B: Run it yourself manually

**One-time setup:**
1. Install Python from https://www.python.org/downloads/ (tick "Add to PATH" during
   install on Windows).
2. Open a terminal (Mac: "Terminal" app. Windows: "Command Prompt").
3. Navigate into this folder, e.g.: `cd Downloads/playhq_pipeline`
4. Run: `pip install requests`

**Every time you want fresh data:**
1. In the terminal, in this folder, run:
   ```
   python fetch_playhq_data.py
   ```
   This will print progress as it works through every season, grade, and game.
   **This can take a while** — potentially many minutes if your club has a long
   history — because it's making one API call per historical game.
2. When it finishes, run:
   ```
   python verify_data.py
   ```
   This compares the new data to your 25 Aug CSV numbers. It must say PASS.
   If it says FAIL, don't publish — paste the output into Claude.
3. Then run:
   ```
   python build_dashboard.py
   ```
4. Open the new file `Bonbeach-CC-Milestone-Dashboard-LIVE.html` — double-click it,
   it opens in your browser like any webpage.

---

## If something goes wrong

PlayHQ's API is well-documented but real-world data occasionally has small
surprises (a missing field, an unexpected status value, etc). If the script
errors out partway through:

- **If using Claude Code (Option A):** just tell it what error you saw — it can
  usually fix it and re-run immediately.
- **If running manually (Option B):** copy the error message and send it back to
  me (Claude, in claude.ai) — paste the error text and I'll tell you exactly what
  to change.

## What this does NOT do

- It does not update automatically / live — you need to re-run the two scripts
  whenever you want fresh numbers (e.g. weekly, or after each round).
- It only counts matches marked `FINAL` in PlayHQ (in-progress games are skipped).
- Wicketkeeper vs non-wicketkeeper catch splits aren't separated in this version
  (they're combined into total catches) — the CSV-based version had this detail,
  the live version can be extended to add it back if you want it later.

## Files in this folder

| File | What it is |
|---|---|
| `fetch_playhq_data.py` | Talks to PlayHQ, downloads every Bonbeach game, aggregates stats |
| `verify_data.py` | Checks the new data against the Aug CSV baseline (PASS/WARN/FAIL) |
| `baseline_aug25.json` | The 25 Aug 2026 CSV totals used for the check |
| `build_dashboard.py` | Injects the fresh data into the dashboard template |
| `dashboard_template.html` | The dashboard design/logic (don't need to touch this) |
| `players_data.json` | Generated — the aggregated stats, machine-readable |
| `Bonbeach-CC-Milestone-Dashboard-LIVE.html` | Generated — open this one in your browser |

**Reminder:** `fetch_playhq_data.py` contains your club's API credentials near the
top of the file. Don't share this file publicly (forums, GitHub, etc).
