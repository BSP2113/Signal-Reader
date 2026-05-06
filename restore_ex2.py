"""Restore backfill2.json and exercises.json EX2 entries to baseline (noon_range, realloc=baseline)."""
import json
from ex2 import run_ex2

LIVE_DATES = [
    "2026-04-13","2026-04-14","2026-04-15","2026-04-16","2026-04-17",
    "2026-04-20","2026-04-21","2026-04-22","2026-04-23","2026-04-24",
    "2026-04-27","2026-04-28","2026-04-29","2026-04-30","2026-05-01",
    "2026-05-04","2026-05-05",
]

# Hardcoded — backfill ends Apr 10, live starts Apr 13. Never derive from the file
# itself since test runs can pollute it with live-date entries.
BACKFILL_DATES = [
    "2026-03-02","2026-03-03","2026-03-04","2026-03-05","2026-03-06",
    "2026-03-09","2026-03-10","2026-03-11","2026-03-12","2026-03-13",
    "2026-03-16","2026-03-17","2026-03-18","2026-03-19","2026-03-20",
    "2026-03-23","2026-03-24","2026-03-25","2026-03-26","2026-03-27",
    "2026-03-31","2026-04-01","2026-04-02","2026-04-06","2026-04-07",
    "2026-04-08","2026-04-09","2026-04-10",
]

print("=== Restoring backfill2.json (28 backfill dates) ===")
for d in BACKFILL_DATES:
    print(f"  {d}...", flush=True)
    run_ex2(trade_date=d, backfill=True, result_file="backfill2.json",
            realloc_mode="baseline", pm_ref="morning_high", save=True)

print("\n=== Restoring exercises.json (17 live dates) ===")
for d in LIVE_DATES:
    print(f"  {d}...", flush=True)
    run_ex2(trade_date=d, backfill=False, result_file="exercises.json",
            realloc_mode="baseline", pm_ref="morning_high", save=True)

print("\nDone — both files restored to baseline.")
