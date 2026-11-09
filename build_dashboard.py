#!/usr/bin/env python3
"""
Bonbeach CC — Dashboard Builder
================================
Takes players_data.json (produced by fetch_playhq_data.py) and injects it into
the dashboard template, producing a single self-contained HTML file you can
open in any browser or upload anywhere.

Run this AFTER fetch_playhq_data.py has produced players_data.json.
"""

import json
from datetime import datetime

DATA_FILE = "players_data.json"
TEMPLATE_FILE = "dashboard_template.html"
OUTPUT_FILE = "Bonbeach-CC-Milestone-Dashboard-LIVE.html"
# Also written so GitHub Pages (serving from the repo root) shows the
# dashboard at the site's base URL without needing the exact filename.
PAGES_OUTPUT_FILE = "index.html"

# Second page: fixtures, ladder and results — same players_data.json, a
# separate lightweight template, linked to/from the main dashboard above.
FIXTURES_TEMPLATE_FILE = "fixtures_template.html"
FIXTURES_OUTPUT_FILE = "fixtures.html"

def current_date_display():
    """Today's date, in Melbourne local time where possible (the workflow's
    schedule is pinned to AEST/AEDT — using UTC here would sometimes show
    yesterday's date on a run that already happened this morning Melbourne
    time). Falls back to plain local time if the timezone database isn't
    available in whatever environment this runs in."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Australia/Melbourne"))
    except Exception:
        now = datetime.now()
    return now.strftime("%d %b %Y")

def build_page(template_file, players_json, last_updated, *output_files):
    """Inject the same players_data.json payload into one template and
    write the result to one or more output paths (index.html and the
    friendly-named file both get the main dashboard's content, for
    instance)."""
    with open(template_file, "r", encoding="utf-8") as f:
        html = f.read()

    if "__PLAYERS_JSON__" not in html:
        raise SystemExit(f"ERROR: {template_file} is missing the __PLAYERS_JSON__ placeholder.")
    if "__LAST_UPDATED__" not in html:
        raise SystemExit(f"ERROR: {template_file} is missing the __LAST_UPDATED__ placeholder.")

    html = html.replace("__PLAYERS_JSON__", players_json)
    html = html.replace("__LAST_UPDATED__", last_updated)

    for output_file in output_files:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html)


def main():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        players_json = f.read()
        json.loads(players_json)  # sanity check it's valid JSON before we embed it

    last_updated = current_date_display()

    build_page(TEMPLATE_FILE, players_json, last_updated, OUTPUT_FILE, PAGES_OUTPUT_FILE)
    build_page(FIXTURES_TEMPLATE_FILE, players_json, last_updated, FIXTURES_OUTPUT_FILE)

    print(f"Done! Open {OUTPUT_FILE} (or {PAGES_OUTPUT_FILE}) — and {FIXTURES_OUTPUT_FILE} for fixtures/ladder — in your browser.")

if __name__ == "__main__":
    main()
