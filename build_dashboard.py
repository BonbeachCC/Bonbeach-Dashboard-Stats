#!/usr/bin/env python3
"""
Injects players_data.json into the dashboard template, stamping the real
refresh date and number of games processed. Run AFTER fetch_playhq_data.py
(and after verify_data.py reports PASS).
"""
import json, os
from datetime import datetime

DATA_FILE = "players_data.json"
META_FILE = "players_meta.json"
TEMPLATE_FILE = "dashboard_template.html"
OUTPUT_FILE = "Bonbeach-CC-Milestone-Dashboard-LIVE.html"

def main():
    with open(DATA_FILE, encoding="utf-8") as f:
        players_json = f.read()
        json.loads(players_json)  # must be valid JSON

    meta = {}
    if os.path.exists(META_FILE):
        meta = json.load(open(META_FILE, encoding="utf-8"))
    updated = datetime.now().strftime("%d %b %Y")
    games = str(meta.get("games_processed", "?"))

    html = open(TEMPLATE_FILE, encoding="utf-8").read()
    for token in ("__PLAYERS_JSON__", "__LAST_UPDATED__", "__GAMES_COUNT__"):
        if token not in html:
            raise SystemExit(f"ERROR: template is missing {token}")
    html = (html.replace("__PLAYERS_JSON__", players_json)
                .replace("__LAST_UPDATED__", updated)
                .replace("__GAMES_COUNT__", games))

    open(OUTPUT_FILE, "w", encoding="utf-8").write(html)
    print(f"Done! Open {OUTPUT_FILE} (stamped {updated}, {games} games).")

if __name__ == "__main__":
    main()
