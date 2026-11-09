#!/usr/bin/env python3
"""
Bonbeach Cricket Club — PlayHQ Live Data Fetcher
=================================================

WHAT THIS SCRIPT DOES
----------------------
1. Connects to the PlayHQ public API using your club's credentials.
2. Walks: Organisation -> Seasons -> Teams (filtered to Bonbeach CC) -> Grades -> Fixtures -> Games.
3. Downloads the full scorecard for every Bonbeach game it finds.
4. Aggregates every player's career Batting / Bowling / Fielding stats across all those games.
5. Writes out `players_data.json` in the exact format the milestone dashboard expects.

  IMPORTANT — KEEP THIS FILE PRIVATE
-----------------------------------
This file contains your club's PlayHQ API credentials below. Do not post this file,
or paste its contents, anywhere public (forums, GitHub, Discord, etc). Treat it like a
password. If it ever leaks, ask PlayHQ / your association to reissue a new Client ID.

HOW TO RUN THIS (see the README for full step-by-step instructions)
---------------------------------------------------------------------
1. Install Python 3 (https://www.python.org/downloads/) if you don't have it.
2. Open a terminal / command prompt in this folder.
3. Run:  pip install requests
4. Run:  python fetch_playhq_data.py
5. Wait — this can take a while (it may be fetching hundreds of historical games).
6. When it finishes, you'll have a new file: players_data.json
7. Run: python build_dashboard.py
   This drops your fresh players_data.json into the dashboard template and produces
   Bonbeach-CC-Milestone-Dashboard-LIVE.html — open that file in your browser.
"""

import requests
import time
import json
import sys
from datetime import datetime

# =========================================================================
# CONFIG — your club's PlayHQ credentials (keep this file private, see above)
# =========================================================================
BASE_URL = "https://api.playhq.com"
X_API_KEY = "48527813-22ce-48fe-b655-b5b6e878c898"
X_PHQ_TENANT = "ca"  # Cricket Australia
ORGANISATION_ID = "317c512c-8695-4c18-b74c-dce44a48a504"  # Bonbeach Cricket Club

CLUB_NAME_MATCH = "bonbeach"  # used as a fallback text match on club name, lowercase

OUTPUT_FILE = "players_data.json"
CACHE_FILE = "_playhq_raw_cache.json"   # lets you resume/re-run without re-downloading everything
REQUEST_DELAY_SECONDS = 0.25            # be polite to PlayHQ's servers between calls

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
    """Public fixture endpoint returns rounds -> games directly (not cursor-paginated)."""
    data = api_get(f"/v2/grades/{grade_id}/fixture")
    if not data:
        return []
    games = []
    for round_ in data.get("rounds", []):
        for g in round_.get("games", []):
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
# Step 5: Aggregation logic
# =========================================================================

def blank_player():
    return {
        "matches_set": set(),   # game ids the player appeared in
        "innings": 0, "not_outs": 0, "runs": 0, "high_score": 0, "high_score_not_out": False,
        "hundreds": 0, "fifties": 0, "balls_faced": 0,
        "wickets": 0, "runs_conceded": 0, "balls_bowled": 0, "best_w": 0, "best_r": 0, "five_wkts": 0,
        "total_catches": 0, "stumpings": 0, "run_outs": 0,
        "first_name": "", "last_name": "",
    }


def stat_value(stat_list, stat_type, default=0):
    for s in stat_list:
        if s.get("type") == stat_type:
            v = s.get("value")
            return v if v is not None else default
    return default


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
                key = f"{last}, {first}".strip(", ")
                p = players.setdefault(key, blank_player())
                p["first_name"], p["last_name"] = first, last
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

                    # Fielding stats live inside the bowling-team block (they were fielding)
                    p["total_catches"] += stat_value(stats, "TOTAL_CATCHES")
                    p["stumpings"] += stat_value(stats, "STUMPINGS")
                    p["run_outs"] += stat_value(stats, "TOTAL_RUN_OUTS")


# =========================================================================
# Step 6: Build final output matching the dashboard's expected schema
# =========================================================================

def finalize(players):
    output = []
    for key, p in players.items():
        matches = len(p["matches_set"])
        if matches == 0:
            continue
        denom = p["innings"] - p["not_outs"]
        bat_avg = round(p["runs"] / denom, 2) if denom > 0 else float(p["runs"])
        bowl_avg = round(p["runs_conceded"] / p["wickets"], 2) if p["wickets"] > 0 else None
        economy = round(p["runs_conceded"] / (p["balls_bowled"] / 6), 2) if p["balls_bowled"] > 0 else None
        best_figures = f"{int(p['best_w'])}-{int(p['best_r'])}" if (p["wickets"] > 0 or p["balls_bowled"] > 0) else "0-0"

        display_name = f"{p['first_name']} {p['last_name']}".strip()
        record = {
            "name": key,
            "display_name": display_name if display_name else key,
            "matches": matches,
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
            "total_catches": int(p["total_catches"]),
            "catches_wk": 0,   # not separated in v2 summary without deeper per-position parsing
            "catches_nwk": int(p["total_catches"]),
            "stumpings": int(p["stumpings"]),
            "run_outs": int(p["run_outs"]),
        }
        output.append(record)

    output.sort(key=lambda x: -x["matches"])
    return output


def apply_milestones(players):
    """Same milestone logic as the CSV pipeline, minus Catches (per club decision)."""
    def next_milestone(value, tiers, increment):
        for t in tiers:
            if value < t:
                return t
        last = tiers[-1]
        n = last
        while n <= value:
            n += increment
        return n

    MATCH_TIERS = [50, 100, 150, 200, 250]
    RUN_TIERS = [1000, 2500, 5000]
    WICKET_TIERS = [50, 100, 250, 500]
    CATCH_TIERS = [25, 50, 100]
    # --- Milestone watch windows (edit these to tune who shows up) ---
    WATCH = {"matches": 5, "wickets": 10}
    RUN_WINDOW_MIN = 100      # always at least this many runs away counts as "near"
    RUN_WINDOW_PCT = 0.10     # bigger milestones get a bigger window: 10% of the target
                              # (500 -> 100 runs, 1000 -> 100, 5000 -> 500, 7500 -> 750)

    for p in players:
        nm = next_milestone(p["matches"], MATCH_TIERS, 50)
        nr = next_milestone(p["runs"], RUN_TIERS, 2500)
        nw = next_milestone(p["wickets"], WICKET_TIERS, 250)
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
        run_window = max(RUN_WINDOW_MIN, int(nr * RUN_WINDOW_PCT))
        if 0 < p["runs_to_go"] <= run_window:
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

    players = {}
    games_processed = 0
    games_seen = set()

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
                if game_id in games_seen or g.get("status") != "FINAL":
                    continue
                games_seen.add(game_id)

                summary = get_game_summary(game_id)
                time.sleep(REQUEST_DELAY_SECONDS)
                if summary:
                    process_game_summary(summary, bonbeach_team_ids, players)
                    games_processed += 1
                    if games_processed % 25 == 0:
                        print(f"      ...{games_processed} games processed so far")

    print(f"\nTotal Bonbeach games processed: {games_processed}")
    print(f"Total players found: {len(players)}")

    output = finalize(players)
    output = apply_milestones(output)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=None)

    with open("players_meta.json", "w", encoding="utf-8") as mf:
        json.dump({"games_processed": games_processed,
                   "players": len(output),
                   "generated": datetime.now().strftime("%d %b %Y %H:%M")}, mf)

    print(f"\nDone! Wrote {len(output)} players to {OUTPUT_FILE}")
    print(f"Last updated: {datetime.now().strftime('%d %b %Y %H:%M')}")
    print("\nNext step: run  python build_dashboard.py")


if __name__ == "__main__":
    main()
