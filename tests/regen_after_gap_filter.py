"""
regen_after_gap_filter.py — one-shot script to regenerate backfill.json,
backfill2.json, and the EX1/EX2 slices of exercises.json after the MAYBE
gap-zone filter was added to ex1.py and ex2.py (2026-05-11).

Run dates chronologically with backfill=True so wallet/streak/drawdown
accumulate correctly from $5,000.

Usage: venv/bin/python3 tests/regen_after_gap_filter.py
"""

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ex1 import run_ex1
from ex2 import run_ex2


def dates_from(path, title_filter=None):
    with open(path) as f:
        data = json.load(f)
    if title_filter:
        data = [e for e in data if e.get('title') == title_filter]
    return sorted({e['date'] for e in data})


def strip_ex(exercises_path, titles):
    with open(exercises_path) as f:
        data = json.load(f)
    keep = [e for e in data if e.get('title') not in titles]
    with open(exercises_path, 'w') as f:
        json.dump(keep, f, indent=2)
    print(f"Stripped {len(data)-len(keep)} entries from {os.path.basename(exercises_path)} "
          f"(titles: {sorted(titles)}); kept {len(keep)}.")


def wipe(path):
    with open(path, 'w') as f:
        json.dump([], f)
    print(f"Wiped {os.path.basename(path)}.")


def main():
    backfill_path  = os.path.join(BASE_DIR, 'backfill.json')
    backfill2_path = os.path.join(BASE_DIR, 'backfill2.json')
    exercises_path = os.path.join(BASE_DIR, 'exercises.json')

    # Snapshot dates BEFORE wiping
    ex1_backfill_dates  = dates_from(backfill_path)
    ex2_backfill_dates  = dates_from(backfill2_path)
    ex1_exercise_dates  = dates_from(exercises_path, 'Exercise 1 - Multi-trade')
    ex2_exercise_dates  = dates_from(exercises_path, 'Exercise 2 - Re-entry')

    print(f"EX1 backfill dates:  {len(ex1_backfill_dates)}  ({ex1_backfill_dates[0]} → {ex1_backfill_dates[-1]})")
    print(f"EX2 backfill dates:  {len(ex2_backfill_dates)}  ({ex2_backfill_dates[0]} → {ex2_backfill_dates[-1]})")
    print(f"EX1 exercise dates:  {len(ex1_exercise_dates)}  ({ex1_exercise_dates[0]} → {ex1_exercise_dates[-1]})")
    print(f"EX2 exercise dates:  {len(ex2_exercise_dates)}  ({ex2_exercise_dates[0]} → {ex2_exercise_dates[-1]})")

    print("\n--- Wiping outputs ---")
    wipe(backfill_path)
    wipe(backfill2_path)
    strip_ex(exercises_path, {'Exercise 1 - Multi-trade', 'Exercise 2 - Re-entry'})

    print("\n--- Regenerating backfill.json (EX1) ---")
    for d in ex1_backfill_dates:
        print(f"\n>>> EX1 backfill {d}")
        run_ex1(d, backfill=True)

    print("\n--- Regenerating backfill2.json (EX2) ---")
    for d in ex2_backfill_dates:
        print(f"\n>>> EX2 backfill {d}")
        run_ex2(d, backfill=True)

    print("\n--- Regenerating EX1 entries in exercises.json ---")
    for d in ex1_exercise_dates:
        print(f"\n>>> EX1 exercise {d}")
        run_ex1(d, backfill=False)

    print("\n--- Regenerating EX2 entries in exercises.json ---")
    for d in ex2_exercise_dates:
        print(f"\n>>> EX2 exercise {d}")
        run_ex2(d, backfill=False)

    print("\nDone.")


if __name__ == '__main__':
    main()
