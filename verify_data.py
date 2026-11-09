#!/usr/bin/env python3
"""
Bonbeach CC — Data Verification
================================
Run this after fetch_playhq_data.py and BEFORE build_dashboard.py.

It checks the freshly pulled players_data.json against the 25 Aug 2026 PlayHQ
CSV exports (baseline_aug25.json). Career stats only ever go UP, so for any
player who appears in both:
  - live matches / runs / wickets / catches should be >= the August figures
  - a big drop means the API pull is incomplete (e.g. missed seasons)

It prints PASS / WARN / FAIL and exits non-zero on FAIL so automation (e.g. a
scheduled GitHub Action) can stop before publishing bad data.
"""
import json, sys

LIVE = "players_data.json"
BASE = "baseline_aug25.json"

live = {p["name"]: p for p in json.load(open(LIVE, encoding="utf-8"))}
base = {p["name"]: p for p in json.load(open(BASE, encoding="utf-8"))}

fails, warns = [], []

# 1. Basic sanity
if len(live) < 50:
    fails.append(f"Only {len(live)} players in live data (baseline has {len(base)}). Pull looks incomplete.")

for name, p in live.items():
    if p["matches"] <= 0:
        fails.append(f"{name}: zero matches")
    if p["runs"] < 0 or p["wickets"] < 0 or p["total_catches"] < 0:
        fails.append(f"{name}: negative stat")
    if p.get("bat_average") is not None and p["bat_average"] < 0:
        fails.append(f"{name}: negative batting average")

# 2. Coverage vs baseline
missing = [n for n in base if n not in live]
coverage = 1 - len(missing) / len(base)
print(f"Baseline players found in live data: {len(base) - len(missing)}/{len(base)} ({coverage:.0%})")
if coverage < 0.6:
    fails.append(f"Only {coverage:.0%} of August players found live. Likely missing older seasons "
                 "(check the API returns all seasons) or the name format differs.")
elif coverage < 0.9:
    warns.append(f"{coverage:.0%} coverage of August players (some may be old players PlayHQ no longer lists).")

# 3. Career totals should not go backwards (allow small tolerance for data corrections)
regress = []
for name, b in base.items():
    l = live.get(name)
    if not l:
        continue
    for key, tol in (("matches", 2), ("runs", 25), ("wickets", 3), ("total_catches", 3)):
        if l[key] < b[key] - tol:
            regress.append((name, key, b[key], l[key]))
if regress:
    print(f"\nPlayers whose live total is LOWER than August ({len(regress)}):")
    for n, k, bv, lv in regress[:15]:
        print(f"  {n:28s} {k:14s} Aug={bv:<6} Live={lv}")
    pct = len(regress) / max(1, len(base) - len(missing))
    (fails if pct > 0.25 else warns).append(
        f"{len(regress)} players have lower career totals than August ({pct:.0%} of matched players).")

# 4. Spot check: club's all-time leaders
print("\nTop 5 by runs (live vs August):")
for p in sorted(live.values(), key=lambda x: -x["runs"])[:5]:
    b = base.get(p["name"])
    print(f"  {p['display_name']:24s} live={p['runs']:<6} aug={b['runs'] if b else 'n/a'}")
print("Top 5 by wickets (live vs August):")
for p in sorted(live.values(), key=lambda x: -x["wickets"])[:5]:
    b = base.get(p["name"])
    print(f"  {p['display_name']:24s} live={p['wickets']:<6} aug={b['wickets'] if b else 'n/a'}")

# 5. Milestone fields present
if not all("watches" in p and "is_watch" in p for p in live.values()):
    fails.append("Milestone fields missing from some players.")
else:
    print(f"\nPlayers on milestone watch: {sum(p['is_watch'] for p in live.values())}")

print("\n" + "=" * 50)
for w in warns:
    print("WARN:", w)
for f in fails:
    print("FAIL:", f)
if fails:
    print("\nRESULT: FAIL. Do not publish this data.")
    sys.exit(1)
print("RESULT: " + ("PASS with warnings" if warns else "PASS"))
