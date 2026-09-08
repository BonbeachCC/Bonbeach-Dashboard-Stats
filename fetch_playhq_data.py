#!/usr/bin/env python3
"""
Bonbeach Cricket Club — PlayHQ Live Data Fetcher
=================================================

WHAT THIS SCRIPT DOES
----------------------
1. Connects to the PlayHQ public API using your club's credentials.
2. Walks: Organisation -> Seasons -> Teams (filtered to Bonbeach CC) -> Grades -> Fixtures -> Games.
3. Skips any game whose ID is already recorded in baseline/counted_game_ids.json
   (games already reflected in the historical baseline, or already pulled in on
   a previous run).
4. Downloads the full scorecard for every remaining (new) Bonbeach game.
5. Aggregates the new games' Batting / Bowling / Fielding stats, then folds
   them into baseline/player_totals.json (the club's full career history,
   originally built from CSV exports going back decades) — so the output
   always reflects FULL history, not just what's in PlayHQ.
6. Writes out `players_data.json` in the exact format the milestone dashboard
   expects, and writes back the updated baseline files so nothing is ever
   double-counted on future runs.

FULL CAREER HISTORY — HOW THE BASELINE WORKS
-----------------------------------------------
PlayHQ's own API only has this club's data back to Summer 2023/24. To show
full career history, this script maintains two extra files under baseline/:

    baseline/player_totals.json    Raw per-player career totals (career-to-date)
    baseline/counted_game_ids.json List of PlayHQ game IDs already folded in

Every run: any PlayHQ game whose ID is NOT yet in counted_game_ids.json gets
aggregated and merged into player_totals.json, then that game's ID is added
to counted_game_ids.json so it's never counted twice. This means these two
baseline files are the club's permanent record — they must be committed to
the repo (the GitHub Actions workflow does this automatically) and should
never be manually deleted or hand-edited.

  CREDENTIALS — READ FROM ENVIRONMENT VARIABLES, NOT HARDCODED
-----------------------------------------------------------------
This script no longer contains any API credentials. It reads them from
environment variables at runtime:

    PLAYHQ_API_KEY   (required)
    PLAYHQ_ORG_ID    (required)
    PLAYHQ_TENANT    (optional, defaults to "ca" — Cricket Australia)

This means the script itself is safe to commit to a public repo. In GitHub
Actions, set these as repository secrets (Settings -> Secrets and variables
-> Actions) and pass them to the workflow step as env vars. For a one-off
local run, set them in your shell first, e.g. on Windows PowerShell:

    $env:PLAYHQ_API_KEY  = "your-key-here"
    $env:PLAYHQ_ORG_ID   = "your-org-id-here"
    python fetch_playhq_data.py

Never commit a .env file or paste real key values into the script or git
history — if a key ever leaks, ask PlayHQ / your association to reissue it.

HOW TO RUN THIS (see the README for full step-by-step instructions)
---------------------------------------------------------------------
1. Install Python 3 (https://www.python.org/downloads/) if you don't have it.
2. Open a terminal / command prompt in this folder.
3. Run:  pip install requests
4. Set the PLAYHQ_API_KEY and PLAYHQ_ORG_ID environment variables (see above).
5. Run:  python fetch_playhq_data.py
6. Wait — this can take a while (it may be fetching hundreds of historical games).
7. When it finishes, you'll have a new file: players_data.json
8. Run: python build_dashboard.py
   This drops your fresh players_data.json into the dashboard template and produces
   Bonbeach-CC-Milestone-Dashboard-LIVE.html (and index.html) — open either in your browser.
"""

import requests
import time
import json
import sys
import os
from datetime import datetime, timedelta

# =========================================================================
# CONFIG — credentials come from environment variables (see docstring above)
# =========================================================================
BASE_URL = "https://api.playhq.com"
X_API_KEY = os.environ.get("PLAYHQ_API_KEY")
X_PHQ_TENANT = os.environ.get("PLAYHQ_TENANT", "ca")  # Cricket Australia
ORGANISATION_ID = os.environ.get("PLAYHQ_ORG_ID")  # Bonbeach Cricket Club

if not X_API_KEY or not ORGANISATION_ID:
    print("ERROR: Missing required environment variables.")
    print("  PLAYHQ_API_KEY and PLAYHQ_ORG_ID must both be set.")
    print("  (PLAYHQ_TENANT is optional and defaults to 'ca'.)")
    print("See the top of this file, or the README, for how to set them.")
    sys.exit(1)

CLUB_NAME_MATCH = "bonbeach"  # used as a fallback text match on club name, lowercase

OUTPUT_FILE = "players_data.json"
CACHE_FILE = "_playhq_raw_cache.json"   # lets you resume/re-run without re-downloading everything
REQUEST_DELAY_SECONDS = 0.25            # be polite to PlayHQ's servers between calls

# Full-career-history baseline (see docstring above). These two files are the
# club's permanent record and ARE committed to the repo — do not gitignore
# them and do not hand-edit them.
BASELINE_TOTALS_FILE = "baseline/player_totals.json"
BASELINE_GAME_IDS_FILE = "baseline/counted_game_ids.json"

# Permanent, ever-growing log of every milestone ever reached (one entry per
# player/type/value, added the run it was first crossed). Unlike the baseline
# files above, this one is NOT a source of truth the pipeline depends on to
# function — if it's ever missing, the pipeline just starts a fresh history
# rather than refusing to run. Still, it SHOULD be committed to the repo so
# past milestones aren't lost. Lives in its own folder so it's easy to spot
# and upload alongside baseline/ in GitHub's web UI.
MILESTONES_LOG_FILE = "milestones/milestones_log.json"
# How many days of recently-reached milestones to surface on the dashboard
# itself each run (the full history keeps accumulating in the log above
# regardless — this just controls the "recent" window shown on the site).
MILESTONES_DISPLAY_WINDOW_DAYS = 30

# How many of Bonbeach's most recent completed matches to show on the
# dashboard's "Latest Results" section. Unlike milestones, results don't need
# a permanent log file — every run already re-walks the full fixture list for
# every season/grade Bonbeach has ever played in (that's how new games get
# discovered), so the latest results can just be recomputed fresh each time.
MAX_RESULTS_SHOWN = 15

# How many of Bonbeach's next upcoming (not-yet-played) matches to show on
# the dashboard's "Upcoming Fixtures" section. Same story as results — no
# extra API calls needed, this is recomputed fresh each run from the same
# fixture list crawl.
MAX_FIXTURES_SHOWN = 10

HEADERS = {
    "x-api-key": X_API_KEY,
    "x-phq-tenant": X_PHQ_TENANT,
}

# =========================================================================
# Low-level HTTP helpers
# =========================================================================

def api_get(path, params=None, retries=3):
    """GET a PlayHQ API path (relative to BASE_URL), with basic retry on failure."""
    url = f"{BASE_URL}{path}"
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                # Rate limited - back off and retry
                wait = 2 ** attempt
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            else:
                print(f"    WARNING: {resp.status_code} for {url} -> {resp.text[:200]}")
                return None
        except requests.RequestException as e:
            print(f"    ERROR calling {url}: {e}")
            time.sleep(1)
    return None


def paginated_get(path, params=None):
    """Yield all items across cursor-paginated PlayHQ endpoints."""
    params = dict(params or {})
    while True:
        data = api_get(path, params=params)
        if data is None:
            return
        items = data.get("data", [])
        for item in items:
            yield item
        meta = data.get("metadata", {})
        if meta.get("hasMore") and meta.get("nextCursor"):
            params["cursor"] = meta["nextCursor"]
            time.sleep(REQUEST_DELAY_SECONDS)
        else:
            return


# =========================================================================
# Step 1: Seasons for the organisation
# =========================================================================

def get_seasons():
    print("Fetching seasons for organisation...")
    seasons = list(paginated_get(f"/v1/organisations/{ORGANISATION_ID}/seasons"))
    print(f"  Found {len(seasons)} seasons")
    return seasons


# =========================================================================
# Step 2: Teams for each season, filtered down to Bonbeach's own teams
# =========================================================================

def get_bonbeach_teams_for_season(season_id):
    teams = list(paginated_get(f"/v1/seasons/{season_id}/teams"))
    bonbeach_teams = []
    for t in teams:
        club = t.get("club") or {}
        club_id = club.get("id")
        club_name = (club.get("name") or "").lower()
        if club_id == ORGANISATION_ID or CLUB_NAME_MATCH in club_name:
            bonbeach_teams.append(t)
    return bonbeach_teams


# =========================================================================
# Step 3: Fixture (games) for each grade Bonbeach plays in
# =========================================================================

def get_grade_fixture(grade_id):
    """Public fixture endpoint returns rounds -> games directly (not cursor-paginated).

    NOTE: PlayHQ's endpoint is actually /v2/grades/{id}/games — the /fixture path
    used in earlier versions of this script now 404s. Fixed 2026-08-26.
    """
    data = api_get(f"/v2/grades/{grade_id}/games")
    if not data:
        return []
    games = []
    for round_ in data.get("rounds", []):
        round_name = round_.get("name")
        for g in round_.get("games", []):
            g["_round_name"] = round_name  # internal-only annotation, not a PlayHQ field
            games.append(g)
    return games


# =========================================================================
# Step 4: Full game summary (this has the batting/bowling/fielding stats)
# =========================================================================

def get_game_summary(game_id):
    data = api_get(f"/v2/games/{game_id}/summary")
    if not data:
        return None
    return data.get("data")


# =========================================================================
# Step 4.5: Match results ("Latest Results" dashboard section)
# =========================================================================
# This is deliberately best-effort. PlayHQ's public games-list response for
# a fixture is documented (per PlayHQ's own support articles) to include a
# "competitors" array with each side's name/score/outcome — but that's
# confirmed for a couple of closely-related endpoints, not this exact one,
# and cricket-specific field shapes can vary. Rather than assume and risk a
# crash (or a whole run failing) if a field isn't where expected, every
# extraction here degrades gracefully: a missing/unexpected field just means
# that one detail is blank on the dashboard ("Result unknown", no score),
# never a broken pipeline run. If results look off once real games start
# flowing through, the Actions log will have printed a diagnostic dump (see
# _debug_dump_competitor below) to fix it from.

_debug_dumped_competitor = False  # print one diagnostic sample per run, not per game


def _get_competitors(game):
    """PlayHQ calls this array 'competitors' in its documented games-list
    endpoints, but this pipeline's own working game-filter code (below, in
    main()) has always used 'teams' for the same array — support both names
    so this doesn't silently come up empty if the two differ."""
    return game.get("competitors") or game.get("teams") or []


def _competitor_name(c):
    return c.get("name") or (c.get("club") or {}).get("name") or "Opponent"


def _competitor_score_display(c):
    """Best-effort human score string, e.g. '142/6 (40 ov)'. Returns None
    (shown as a blank score on the dashboard) if no usable score field is
    found, rather than guessing."""
    subtotals = c.get("scoreSubtotals") or []
    runs = wickets = overs = None
    for s in subtotals:
        stype = (s.get("type") or "").upper()
        val = s.get("value")
        if stype == "TOTAL_SCORE":
            runs = val
        elif stype == "TOTAL_OUTS":
            wickets = val
        elif stype == "TOTAL_OVERS":
            overs = val
    if runs is None:
        total = c.get("scoreTotal")
        if isinstance(total, (int, float)):
            runs = total
        elif isinstance(total, dict):
            runs = total.get("value")
    if runs is None:
        return None
    try:
        runs = int(runs)
    except (TypeError, ValueError):
        return None
    if wickets is not None and overs is not None:
        return f"{runs}/{int(wickets)} ({overs} ov)"
    if wickets is not None:
        return f"{runs}/{int(wickets)}"
    return str(runs)


def _competitor_outcome(c):
    """Normalise PlayHQ's outcome value into Won/Lost/Drew/Tied — or None
    (shown as 'Result unknown') for anything unrecognised. Deliberately
    conservative: an unrecognised value is left blank rather than guessed."""
    raw = str(c.get("outcome") or c.get("result") or "").upper()
    if raw in ("WON", "WIN", "WINNER"):
        return "Won"
    if raw in ("LOST", "LOSS", "LOSER"):
        return "Lost"
    if raw in ("DRAW", "DREW", "DRAWN"):
        return "Drew"
    if raw in ("TIE", "TIED"):
        return "Tied"
    return None


def extract_match_result(game, bonbeach_team_ids, bonbeach_team_names, bonbeach_grade_names):
    """Best-effort result for one FINAL game already known to involve
    Bonbeach. Returns None (game just doesn't show up in Latest Results)
    if the competitor data doesn't look like what's expected — a missing
    card is much safer than a wrong one."""
    global _debug_dumped_competitor
    try:
        competitors = _get_competitors(game)
        if len(competitors) != 2:
            return None
        bb, opp = None, None
        for c in competitors:
            if c.get("id") in bonbeach_team_ids:
                bb = c
            else:
                opp = c
        if bb is None or opp is None:
            return None

        result = {
            "game_id": game.get("id"),
            "date": (game.get("schedule") or {}).get("date") or game.get("date"),
            "bonbeach_team": bonbeach_team_names.get(bb.get("id"), "Bonbeach"),
            "grade": bonbeach_grade_names.get(bb.get("id")),
            "round": game.get("_round_name"),
            "opponent": _competitor_name(opp),
            "bonbeach_score": _competitor_score_display(bb),
            "opponent_score": _competitor_score_display(opp),
            "result": _competitor_outcome(bb),
            "venue": (game.get("venue") or {}).get("name"),
        }

        degraded = result["opponent"] == "Opponent" or result["bonbeach_score"] is None or result["result"] is None
        if degraded and not _debug_dumped_competitor:
            print("    NOTE: match-result extraction is missing some fields on this game —")
            print(f"    raw competitor keys seen: bonbeach={sorted(bb.keys())} opponent={sorted(opp.keys())}")
            print("    (this is informational only — the pipeline keeps running fine either way)")
            _debug_dumped_competitor = True

        return result
    except Exception as e:
        print(f"    WARNING: couldn't extract a match result for game {game.get('id')}: {e}")
        return None


_NOT_PLAYING_STATUS_HINTS = ("CANCEL", "ABANDON", "FORFEIT", "WASHOUT", "WASHED_OUT", "POSTPONED")


def extract_fixture(game, bonbeach_team_ids, bonbeach_team_names, bonbeach_grade_names):
    """Best-effort upcoming-fixture info for a Bonbeach game that hasn't been
    played yet (status isn't FINAL). Same defensive philosophy as
    extract_match_result: a missing field just leaves a blank on the card,
    never a broken run. Returns None for games that look like they're never
    going to be played (cancelled/abandoned/postponed) or that don't clearly
    involve one of Bonbeach's own teams."""
    try:
        status = str(game.get("status") or "").upper()
        if any(word in status for word in _NOT_PLAYING_STATUS_HINTS):
            return None

        competitors = _get_competitors(game)
        bb_id, opp = None, None
        if len(competitors) == 2:
            for c in competitors:
                if c.get("id") in bonbeach_team_ids:
                    bb_id = c.get("id")
                else:
                    opp = c
        if bb_id is None:
            # Fall back to the bare {"id": ...} shape this pipeline's own
            # game-filter already relies on, in case a not-yet-played game
            # doesn't carry the richer 'competitors'/'teams' fields yet.
            for t in (game.get("teams") or []):
                if t.get("id") in bonbeach_team_ids:
                    bb_id = t.get("id")
                elif opp is None:
                    opp = t
        if bb_id is None:
            return None

        schedule = game.get("schedule") or {}
        return {
            "game_id": game.get("id"),
            "date": schedule.get("date") or game.get("date"),
            "time": schedule.get("time"),
            "bonbeach_team": bonbeach_team_names.get(bb_id, "Bonbeach"),
            "grade": bonbeach_grade_names.get(bb_id),
            "round": game.get("_round_name"),
            "opponent": _competitor_name(opp) if opp else "TBC",
            "venue": (game.get("venue") or {}).get("name"),
        }
    except Exception as e:
        print(f"    WARNING: couldn't extract a fixture for game {game.get('id')}: {e}")
        return None


# =========================================================================
# Step 5: Aggregation logic
# =========================================================================

def blank_player():
    return {
        "matches_set": set(),   # game ids the player appeared in
        "innings": 0, "not_outs": 0, "runs": 0, "high_score": 0, "high_score_not_out": False,
        "hundreds": 0, "fifties": 0, "balls_faced": 0,
        "wickets": 0, "runs_conceded": 0, "balls_bowled": 0, "best_w": 0, "best_r": 0, "five_wkts": 0,
        "catches_wk": 0, "catches_nwk": 0, "stumpings": 0, "run_outs": 0,
        "first_name": "", "last_name": "",
    }


def stat_value(stat_list, stat_type, default=0):
    for s in stat_list:
        if s.get("type") == stat_type:
            v = s.get("value")
            return v if v is not None else default
    return default


def gentle_capitalize(s):
    """Capitalise the first letter after each word boundary (space, hyphen,
    apostrophe) WITHOUT touching any other letter's existing case. Fixes
    "jamie" -> "Jamie" while leaving names like "MacKessack" or "O'Connor"
    completely untouched (unlike str.title(), which mangles them).
    """
    if not s:
        return s
    out = []
    capitalize_next = True
    for ch in s:
        if capitalize_next and ch.isalpha():
            out.append(ch.upper())
            capitalize_next = False
        else:
            out.append(ch)
        if ch in " -'":
            capitalize_next = True
    return "".join(out)


def player_key(last, first):
    """Case-insensitive matching key so e.g. PlayHQ returning 'jamie' one game
    and 'Jamie' another never splits one real player into two entries. Must
    match the key format baseline/player_totals.json was built with."""
    return f"{last}, {first}".strip(", ").lower()


def process_game_summary(summary, bonbeach_team_ids, players):
    if not summary:
        return

    game_id = summary.get("id")
    # map appearance id -> (firstName, lastName, teamId)
    appearance_info = {}
    for a in summary.get("appearances", []):
        appearance_info[a["id"]] = {
            "firstName": a.get("firstName") or "",
            "lastName": a.get("lastName") or "",
            "teamId": a.get("teamId"),
        }

    for period in summary.get("periods", []) or []:
        for team_block in period.get("teams", []) or []:
            team_id = team_block.get("id")
            if team_id not in bonbeach_team_ids:
                continue  # only aggregate Bonbeach players' own performances
            discipline = team_block.get("discipline")

            for appearance in team_block.get("appearances", []) or []:
                app_id = appearance.get("id")
                info = appearance_info.get(app_id, {})
                first, last = info.get("firstName", ""), info.get("lastName", "")
                if not first and not last:
                    continue  # skip anonymous/fill-in placeholders with no name
                key = player_key(last, first)
                p = players.setdefault(key, blank_player())
                # Keep the first non-blank, gently-capitalised name we saw rather than
                # overwriting every game — avoids flip-flopping display casing if PlayHQ
                # returns inconsistent casing for the same player across games.
                if not p["first_name"] and not p["last_name"]:
                    p["first_name"], p["last_name"] = gentle_capitalize(first), gentle_capitalize(last)
                p["matches_set"].add(game_id)

                stats = appearance.get("statistics", []) or []

                if discipline == "BATTING":
                    status = appearance.get("status")
                    if status == "DID_NOT_BAT":
                        continue
                    runs = stat_value(stats, "TOTAL_RUNS")
                    p["innings"] += 1
                    if status == "NOT_OUT":
                        p["not_outs"] += 1
                    p["runs"] += runs
                    if runs > p["high_score"]:
                        p["high_score"] = runs
                        p["high_score_not_out"] = (status == "NOT_OUT")
                    if runs >= 100:
                        p["hundreds"] += 1
                    elif runs >= 50:
                        p["fifties"] += 1
                    p["balls_faced"] += stat_value(stats, "BALLS_FACED")

                elif discipline == "BOWLING":
                    wkts = stat_value(stats, "WICKETS")
                    runs_c = stat_value(stats, "RUNS")
                    overs = stat_value(stats, "OVERS")
                    p["wickets"] += wkts
                    p["runs_conceded"] += runs_c
                    # PlayHQ overs are float like 3.4 = 3 overs 4 balls; approximate balls:
                    whole = int(overs)
                    part = round((overs - whole) * 10)
                    p["balls_bowled"] += whole * 6 + part
                    if wkts >= 5:
                        p["five_wkts"] += 1
                    if wkts > p["best_w"] or (wkts == p["best_w"] and runs_c < p["best_r"]):
                        if p["best_w"] == 0 and p["best_r"] == 0:
                            p["best_w"], p["best_r"] = wkts, runs_c
                        elif wkts > p["best_w"] or (wkts == p["best_w"] and runs_c < p["best_r"]):
                            p["best_w"], p["best_r"] = wkts, runs_c

                    # Fielding stats live inside the bowling-team block (they were fielding).
                    # PlayHQ separates wicket-keeper catches from other catches, matching the
                    # split already present in the historical CSV baseline (catches_wk/catches_nwk).
                    p["catches_wk"] += stat_value(stats, "CATCHES_AS_WICKET_KEEPER")
                    p["catches_nwk"] += stat_value(stats, "CATCHES_AS_FIELDER")
                    p["stumpings"] += stat_value(stats, "STUMPINGS")
                    p["run_outs"] += stat_value(stats, "TOTAL_RUN_OUTS")


# =========================================================================
# Step 6: Full-career-history baseline — load, merge, save
# =========================================================================

def blank_baseline_entry():
    return {
        "first_name": "", "last_name": "",
        "matches": 0, "innings": 0, "not_outs": 0, "runs": 0,
        "high_score": 0, "high_score_not_out": False,
        "hundreds": 0, "fifties": 0, "balls_faced": 0,
        "wickets": 0, "runs_conceded": 0, "balls_bowled": 0,
        "best_w": 0, "best_r": 0, "five_wkts": 0,
        "catches_wk": 0, "catches_nwk": 0, "stumpings": 0, "run_outs": 0,
    }


def load_baseline_totals():
    try:
        with open(BASELINE_TOTALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERROR: {BASELINE_TOTALS_FILE} was not found in this checkout.")
        print("This file holds the club's full career history (decades of CSV-sourced")
        print("stats) and must be committed to the repo at all times — see the README's")
        print("'Full career history' section. Stopping now rather than silently building")
        print("a dashboard with only partial (PlayHQ-era) data. Re-add this file (from a")
        print("backup, or by re-uploading it) and try again.")
        sys.exit(1)


def load_counted_game_ids():
    try:
        with open(BASELINE_GAME_IDS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        print(f"ERROR: {BASELINE_GAME_IDS_FILE} was not found in this checkout.")
        print("Without this file every PlayHQ game would look 'new' and get double-counted")
        print("on top of the baseline totals. Stopping now rather than risking corrupted")
        print("stats. Re-add this file (from a backup, or by re-uploading it) and try again.")
        sys.exit(1)


def save_baseline_totals(baseline_totals):
    with open(BASELINE_TOTALS_FILE, "w", encoding="utf-8") as f:
        json.dump(baseline_totals, f, indent=None)


def save_counted_game_ids(counted_game_ids):
    with open(BASELINE_GAME_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(counted_game_ids), f, indent=None)


def merge_into_baseline(baseline_totals, live_deltas):
    """Fold this run's newly-crawled PlayHQ games into the career-long baseline totals.

    live_deltas is the `players` dict built by process_game_summary() during this
    run — i.e. ONLY stats from games not already in counted_game_ids.json.
    """
    for key, delta in live_deltas.items():
        delta_matches = len(delta["matches_set"])
        if delta_matches == 0:
            continue

        b = baseline_totals.setdefault(key, blank_baseline_entry())

        # Prefer names already on file (CSV baseline names are the authoritative
        # spelling); only take PlayHQ's name for brand-new players not yet seen.
        if not b["first_name"] and not b["last_name"]:
            b["first_name"], b["last_name"] = delta["first_name"], delta["last_name"]

        b["matches"] += delta_matches
        b["innings"] += delta["innings"]
        b["not_outs"] += delta["not_outs"]
        b["runs"] += delta["runs"]
        b["hundreds"] += delta["hundreds"]
        b["fifties"] += delta["fifties"]
        b["balls_faced"] += delta["balls_faced"]
        b["wickets"] += delta["wickets"]
        b["runs_conceded"] += delta["runs_conceded"]
        b["balls_bowled"] += delta["balls_bowled"]
        b["five_wkts"] += delta["five_wkts"]
        b["catches_wk"] += delta["catches_wk"]
        b["catches_nwk"] += delta["catches_nwk"]
        b["stumpings"] += delta["stumpings"]
        b["run_outs"] += delta["run_outs"]

        if delta["high_score"] > b["high_score"]:
            b["high_score"] = delta["high_score"]
            b["high_score_not_out"] = delta["high_score_not_out"]

        dw, dr = delta["best_w"], delta["best_r"]
        if dw > 0 or dr > 0:  # delta actually took a wicket-bearing spell worth comparing
            if b["best_w"] == 0 and b["best_r"] == 0:
                b["best_w"], b["best_r"] = dw, dr
            elif dw > b["best_w"] or (dw == b["best_w"] and dr < b["best_r"]):
                b["best_w"], b["best_r"] = dw, dr

    return baseline_totals


# =========================================================================
# Step 7: Build final output matching the dashboard's expected schema
# =========================================================================

def finalize(baseline_totals):
    output = []
    for key, p in baseline_totals.items():
        matches = p["matches"]
        if matches == 0:
            continue
        denom = p["innings"] - p["not_outs"]
        bat_avg = round(p["runs"] / denom, 2) if denom > 0 else float(p["runs"])
        bowl_avg = round(p["runs_conceded"] / p["wickets"], 2) if p["wickets"] > 0 else None
        economy = round(p["runs_conceded"] / (p["balls_bowled"] / 6), 2) if p["balls_bowled"] > 0 else None
        best_figures = f"{int(p['best_w'])}-{int(p['best_r'])}" if (p["wickets"] > 0 or p["balls_bowled"] > 0) else "0-0"

        display_name = f"{p['first_name']} {p['last_name']}".strip()
        total_catches = int(p["catches_wk"]) + int(p["catches_nwk"])
        record = {
            "name": key,
            "display_name": display_name if display_name else key,
            "matches": int(matches),
            "runs": int(p["runs"]),
            "innings": int(p["innings"]),
            "not_outs": int(p["not_outs"]),
            "high_score": int(p["high_score"]),
            "high_score_not_out": p["high_score_not_out"],
            "hundreds": int(p["hundreds"]),
            "fifties": int(p["fifties"]),
            "bat_average": bat_avg,
            "wickets": int(p["wickets"]),
            "runs_conceded": int(p["runs_conceded"]),
            "best_figures": best_figures,
            "five_wkts": int(p["five_wkts"]),
            "bowl_average": bowl_avg,
            "economy": economy,
            "total_catches": total_catches,
            "catches_wk": int(p["catches_wk"]),
            "catches_nwk": int(p["catches_nwk"]),
            "stumpings": int(p["stumpings"]),
            "run_outs": int(p["run_outs"]),
        }
        output.append(record)

    output.sort(key=lambda x: -x["matches"])
    return output


# =========================================================================
# Milestone tiers — module-level so both apply_milestones() (upcoming/"watch"
# milestones) and detect_milestones_reached() (milestones actually crossed
# this run) use the exact same thresholds.
# =========================================================================
MATCH_TIERS = [100, 150, 200, 250, 300, 350, 400, 450, 500]
RUN_TIERS = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000,
             5500, 6000, 6500, 7000, 7500, 8000, 8500, 9000, 9500, 10000]
WICKET_TIERS = [100, 150, 200, 250, 300, 350, 400, 450, 500, 550]
CATCH_TIERS = [100, 150, 200, 250, 300, 350, 400, 450, 500]
WATCH = {"matches": 5, "runs": 100, "wickets": 10}


def next_milestone(value, tiers, increment):
    for t in tiers:
        if value < t:
            return t
    last = tiers[-1]
    n = last
    while n <= value:
        n += increment
    return n


def crossed_tiers(pre, post, tiers, increment):
    """Every milestone tier value crossed going from `pre` to `post` (pre < t <= post),
    extending the explicit tier list indefinitely by `increment` if needed."""
    all_tiers = list(tiers)
    n = tiers[-1]
    while n < post:
        n += increment
        all_tiers.append(n)
    return [t for t in all_tiers if pre < t <= post]


def today_iso():
    """Today's date (Melbourne local, matching build_dashboard.py's date-stamping
    logic) as YYYY-MM-DD — used to date-stamp milestone log entries."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Australia/Melbourne"))
    except Exception:
        now = datetime.now()
    return now.strftime("%Y-%m-%d")


def load_milestones_log():
    """The permanent history of every milestone ever reached. Missing/corrupt
    file just means "no history yet" — this is a nice-to-have record, not a
    file the pipeline depends on to run correctly, so it never sys.exit()s."""
    try:
        with open(MILESTONES_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_milestones_log(log):
    folder = os.path.dirname(MILESTONES_LOG_FILE)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(MILESTONES_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=None)


def detect_milestones_reached(pre_stats, output, run_date):
    """Compare each player's stats from BEFORE this run's new games were folded
    in (pre_stats) against their finalized stats AFTER (output), and report
    every milestone tier actually crossed this run — for Matches, Runs,
    Wickets, and Catches alike."""
    output_by_key = {p["name"]: p for p in output}
    events = []
    for key, pre in pre_stats.items():
        post = output_by_key.get(key)
        if not post:
            continue
        checks = [
            ("Matches", pre["matches"], post["matches"], MATCH_TIERS, 50),
            ("Runs", pre["runs"], post["runs"], RUN_TIERS, 500),
            ("Wickets", pre["wickets"], post["wickets"], WICKET_TIERS, 50),
            ("Catches", pre["catches"], post["total_catches"], CATCH_TIERS, 50),
        ]
        for milestone_type, pre_val, post_val, tiers, increment in checks:
            for tier in crossed_tiers(pre_val, post_val, tiers, increment):
                events.append({
                    "date": run_date,
                    "player": post["display_name"],
                    "type": milestone_type,
                    "value": tier,
                })
    return events


def recent_milestones(log, run_date, window_days=MILESTONES_DISPLAY_WINDOW_DAYS):
    """The slice of the permanent milestones log within `window_days` of
    run_date, newest first — what actually gets shown on the dashboard.
    The full log keeps every milestone forever; this is just today's view
    of "recent" so the site doesn't grow an unbounded list."""
    try:
        cutoff = datetime.strptime(run_date, "%Y-%m-%d") - timedelta(days=window_days)
    except Exception:
        cutoff = None

    def in_window(entry):
        if cutoff is None:
            return True
        try:
            return datetime.strptime(entry.get("date", ""), "%Y-%m-%d") >= cutoff
        except Exception:
            return False

    recent = [e for e in log if in_window(e)]
    recent.sort(key=lambda e: e.get("date", ""), reverse=True)
    return recent


def apply_milestones(players):
    """Same milestone logic as the CSV pipeline, minus Catches (per club decision)."""
    # Every milestone type is watched for every player, regardless of career
    # matches played. The "only 100+" rule lives in MATCH_TIERS above instead —
    # the Matches milestone itself simply doesn't start until 100, so nobody
    # ever gets celebrated for reaching 50 games, but a Runs/Wickets alert can
    # still fire for a player who hasn't yet played 100 games.

    for p in players:
        nm = next_milestone(p["matches"], MATCH_TIERS, 50)
        nr = next_milestone(p["runs"], RUN_TIERS, 500)
        nw = next_milestone(p["wickets"], WICKET_TIERS, 50)
        nc = next_milestone(p["total_catches"], CATCH_TIERS, 50)

        p["next_match_milestone"] = nm
        p["matches_to_go"] = nm - p["matches"]
        p["next_run_milestone"] = nr
        p["runs_to_go"] = nr - p["runs"]
        p["next_wicket_milestone"] = nw
        p["wickets_to_go"] = nw - p["wickets"]
        p["next_catch_milestone"] = nc
        p["catches_to_go"] = nc - p["total_catches"]

        watches = []
        if 0 < p["matches_to_go"] <= WATCH["matches"]:
            watches.append({"type": "Matches", "current": p["matches"], "target": nm, "to_go": p["matches_to_go"]})
        if 0 < p["runs_to_go"] <= WATCH["runs"]:
            watches.append({"type": "Runs", "current": p["runs"], "target": nr, "to_go": p["runs_to_go"]})
        if 0 < p["wickets_to_go"] <= WATCH["wickets"]:
            watches.append({"type": "Wickets", "current": p["wickets"], "target": nw, "to_go": p["wickets_to_go"]})
        # NOTE: Catches deliberately excluded from milestone watch per club decision
        p["watches"] = watches
        p["is_watch"] = len(watches) > 0

    return players


# =========================================================================
# Main
# =========================================================================

def main():
    print("=" * 60)
    print("Bonbeach CC — PlayHQ Live Data Fetch")
    print("=" * 60)

    seasons = get_seasons()
    if not seasons:
        print("No seasons found — check your Organisation ID and credentials.")
        sys.exit(1)

    baseline_totals = load_baseline_totals()
    counted_game_ids = load_counted_game_ids()
    milestones_log = load_milestones_log()
    print(f"Baseline: {len(baseline_totals)} players, {len(counted_game_ids)} games already counted")
    print(f"Milestones log: {len(milestones_log)} milestones on record")

    players = {}       # NEW games only, this run
    games_processed = 0
    games_new_ids = set()
    games_seen = set()
    fixtures_seen = set()
    all_results = []   # Bonbeach match results, across every season/grade seen this run
    all_fixtures = []  # Bonbeach upcoming (not-yet-played) games, same deal

    for season in seasons:
        season_id = season.get("id")
        season_name = season.get("name")
        print(f"\nSeason: {season_name} ({season_id})")

        bonbeach_teams = get_bonbeach_teams_for_season(season_id)
        if not bonbeach_teams:
            print("  No Bonbeach teams found in this season, skipping.")
            continue
        print(f"  Bonbeach teams this season: {len(bonbeach_teams)}")

        bonbeach_team_ids = {t["id"] for t in bonbeach_teams}
        bonbeach_team_names = {t["id"]: (t.get("name") or "Bonbeach") for t in bonbeach_teams}
        bonbeach_grade_names = {t["id"]: (t.get("grade") or {}).get("name") for t in bonbeach_teams}
        grade_ids = {t["grade"]["id"] for t in bonbeach_teams if t.get("grade")}

        for grade_id in grade_ids:
            games = get_grade_fixture(grade_id)
            time.sleep(REQUEST_DELAY_SECONDS)
            relevant_games = [
                g for g in games
                if any(team.get("id") in bonbeach_team_ids for team in g.get("teams", []))
            ]
            print(f"    Grade {grade_id}: {len(relevant_games)} Bonbeach games")

            for g in relevant_games:
                game_id = g.get("id")

                if g.get("status") != "FINAL":
                    # Not played yet (or in progress) — this is fixture territory,
                    # not a result. Comes from the same fixture list already in
                    # hand, so no extra API call either.
                    if game_id not in fixtures_seen:
                        fixtures_seen.add(game_id)
                        fixture = extract_fixture(g, bonbeach_team_ids, bonbeach_team_names, bonbeach_grade_names)
                        if fixture:
                            all_fixtures.append(fixture)
                    continue

                if game_id in games_seen:
                    continue
                games_seen.add(game_id)

                # Match results come straight from the fixture list we already have in
                # hand (no extra API call), so do this for EVERY final Bonbeach game —
                # including ones already folded into the baseline on a previous run —
                # not just new ones. That way "Latest Results" has real content from
                # the next run onward, without waiting for brand-new games.
                match_result = extract_match_result(g, bonbeach_team_ids, bonbeach_team_names, bonbeach_grade_names)
                if match_result:
                    all_results.append(match_result)

                if game_id in counted_game_ids:
                    continue  # already folded into the baseline on a previous run

                summary = get_game_summary(game_id)
                time.sleep(REQUEST_DELAY_SECONDS)
                if summary:
                    process_game_summary(summary, bonbeach_team_ids, players)
                    games_new_ids.add(game_id)
                    games_processed += 1
                    if games_processed % 25 == 0:
                        print(f"      ...{games_processed} new games processed so far")

    print(f"\nNew Bonbeach games this run: {games_processed}")
    print(f"Players with new activity this run: {len(players)}")

    # Snapshot each active player's stats as they stood BEFORE this run's new
    # games get folded in, so we can tell exactly which milestones (if any)
    # they crossed as a result of today's games. Must happen before
    # merge_into_baseline() mutates baseline_totals in place.
    pre_stats = {}
    for key in players.keys():
        b = baseline_totals.get(key, {})
        pre_stats[key] = {
            "matches": b.get("matches", 0),
            "runs": b.get("runs", 0),
            "wickets": b.get("wickets", 0),
            "catches": b.get("catches_wk", 0) + b.get("catches_nwk", 0),
        }

    baseline_totals = merge_into_baseline(baseline_totals, players)
    counted_game_ids |= games_new_ids

    output = finalize(baseline_totals)
    output = apply_milestones(output)

    run_date = today_iso()
    new_milestone_events = detect_milestones_reached(pre_stats, output, run_date)
    if new_milestone_events:
        print(f"\nMilestones reached this run: {len(new_milestone_events)}")
        for e in new_milestone_events:
            print(f"  {e['player']} — {e['value']:,} {e['type']}")
    milestones_log.extend(new_milestone_events)
    milestones_reached_display = recent_milestones(milestones_log, run_date)

    # De-dupe (a game could in principle be seen twice if it spans grades data
    # oddly) and take the most recent MAX_RESULTS_SHOWN by date, newest first.
    seen_result_games = set()
    deduped_results = []
    for r in all_results:
        if r["game_id"] in seen_result_games:
            continue
        seen_result_games.add(r["game_id"])
        deduped_results.append(r)
    deduped_results.sort(key=lambda r: r.get("date") or "", reverse=True)
    latest_results = deduped_results[:MAX_RESULTS_SHOWN]

    # Fixtures: drop anything dated before today (a not-yet-FINAL game whose
    # date has already passed is most likely just pending a score update, not
    # a genuine upcoming fixture), then take the soonest MAX_FIXTURES_SHOWN.
    upcoming_fixtures = [f for f in all_fixtures if (f.get("date") or "9999-99-99") >= run_date]
    upcoming_fixtures.sort(key=lambda f: (f.get("date") or "9999-99-99", f.get("time") or ""))
    upcoming_fixtures = upcoming_fixtures[:MAX_FIXTURES_SHOWN]

    dashboard_payload = {
        "players": output,
        "milestones_reached": milestones_reached_display,
        "latest_results": latest_results,
        "upcoming_fixtures": upcoming_fixtures,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(dashboard_payload, f, indent=None)

    save_baseline_totals(baseline_totals)
    save_counted_game_ids(counted_game_ids)
    save_milestones_log(milestones_log)

    print(f"\nDone! Wrote {len(output)} players (full career history) to {OUTPUT_FILE}")
    print(f"Baseline now covers {len(counted_game_ids)} games and {len(baseline_totals)} players.")
    print(f"Milestones log now covers {len(milestones_log)} milestones ({len(milestones_reached_display)} shown on the dashboard, last {MILESTONES_DISPLAY_WINDOW_DAYS} days).")
    print(f"Latest Results: {len(deduped_results)} completed Bonbeach matches found, showing the {len(latest_results)} most recent.")
    print(f"Upcoming Fixtures: {len(all_fixtures)} not-yet-played Bonbeach games found, showing the next {len(upcoming_fixtures)}.")
    print(f"Last updated: {datetime.now().strftime('%d %b %Y %H:%M')}")
    print("\nNext step: run  python build_dashboard.py")


if __name__ == "__main__":
    main()
