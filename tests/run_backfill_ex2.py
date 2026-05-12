"""
Re-runs all EX2 entries in exercises.json from scratch, in chronological order,
so the afternoon breakout scan is reflected and the wallet balance compounds correctly.
"""
import json, os, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.chdir(os.path.dirname(os.path.dirname(__file__)))

from ex2 import run_ex2

DATES = [
    "2026-04-13","2026-04-14","2026-04-15","2026-04-16","2026-04-17",
    "2026-04-20","2026-04-21","2026-04-22","2026-04-23","2026-04-24",
    "2026-04-27","2026-04-28","2026-04-29","2026-04-30","2026-05-01",
    "2026-05-04",
]

MS_PATH   = "market_state.json"
EXER_PATH = "exercises.json"
HIST_PATH = "market_states_historical.json"

# --- Load historical market states ---
with open(HIST_PATH) as f:
    hist = json.load(f)
ms_by_date = {e["date"]: e for e in hist}

# May 4 comes from today's market_state.json
with open(MS_PATH) as f:
    today_ms = json.load(f)
if today_ms.get("date") == "2026-05-04":
    ms_by_date["2026-05-04"] = today_ms

# --- Strip existing EX2 entries for these dates ---
with open(EXER_PATH) as f:
    existing = json.load(f)
stripped = [e for e in existing if not (e["date"] in DATES and "Exercise 2" in e["title"])]
with open(EXER_PATH, "w") as f:
    json.dump(stripped, f, indent=2)
print(f"Stripped {len(existing) - len(stripped)} EX2 entries from exercises.json\n")

# --- Back up current market_state.json ---
shutil.copy(MS_PATH, MS_PATH + ".bak")

try:
    for date in DATES:
        ms = ms_by_date.get(date)
        if not ms:
            print(f"  WARNING: no market state for {date}, using neutral")
            ms = {"date": date, "state": "neutral", "spy_gap_pct": 0.0, "vixy_trend_pct": 0.0}
        # Write correct market state for this date
        ms_out = dict(ms)
        ms_out["date"] = date
        with open(MS_PATH, "w") as f:
            json.dump(ms_out, f, indent=2)
        print(f"{'='*60}")
        run_ex2(date)
        print()
finally:
    # Restore today's market_state.json
    shutil.copy(MS_PATH + ".bak", MS_PATH)
    os.remove(MS_PATH + ".bak")
    print("Restored market_state.json")

print("\nBackfill complete.")
