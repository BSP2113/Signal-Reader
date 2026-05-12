"""
test_entry_cooldown.py — Backtest a morning cooldown: skip entries before a cutoff time.

Tests two thresholds: 10:00 and 10:30.

Usage: venv/bin/python3 test_entry_cooldown.py
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUTOFFS  = ["10:00", "10:30"]


def time_to_mins(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def run(ex1_days, cutoff):
    total_original = 0.0
    total_modified = 0.0
    all_skipped    = []

    print(f"\n{'='*60}")
    print(f"CUTOFF: entries before {cutoff} are skipped")
    print(f"{'='*60}")

    cutoff_mins = time_to_mins(cutoff)

    for day in ex1_days:
        date   = day["date"]
        trades = day["trades"]

        day_original = sum(t["pnl"] for t in trades)
        day_modified = 0.0
        skipped = []
        kept    = []

        for t in trades:
            if time_to_mins(t["time"]) < cutoff_mins:
                skipped.append(t)
                all_skipped.append({**t, "date": date})
            else:
                day_modified += t["pnl"]
                kept.append(t)

        diff = day_modified - day_original
        print(f"\n--- {date}  orig={day_original:+.2f}  modified={day_modified:+.2f}  diff={diff:+.2f} ---")
        for t in kept:
            print(f"  KEPT    {t['ticker']:6} {t['time']}  vol={t['vol_ratio']:.1f}x  {t['exit_reason']:15}  pnl={t['pnl']:+.2f}")
        for t in skipped:
            tag = "+gain" if t["pnl"] > 0 else "loss"
            print(f"  SKIPPED {t['ticker']:6} {t['time']}  vol={t['vol_ratio']:.1f}x  {t['exit_reason']:15}  pnl={t['pnl']:+.2f}  ({tag})")

        total_original += day_original
        total_modified += day_modified

    net             = total_modified - total_original
    skipped_winners = [t for t in all_skipped if t["pnl"] > 0]
    skipped_losers  = [t for t in all_skipped if t["pnl"] < 0]

    print(f"\nSUMMARY — {cutoff} cooldown")
    print(f"  Original total P&L:  ${total_original:+.2f}")
    print(f"  Modified total P&L:  ${total_modified:+.2f}")
    print(f"  Net difference:      ${net:+.2f}")
    print(f"  Trades skipped: {len(all_skipped)}  "
          f"(losers avoided: {len(skipped_losers)} ${sum(t['pnl'] for t in skipped_losers):+.2f}  |  "
          f"winners missed: {len(skipped_winners)} ${sum(t['pnl'] for t in skipped_winners):+.2f})")


def main():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        data = json.load(f)

    ex1_days = sorted(
        [e for e in data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    for cutoff in CUTOFFS:
        run(ex1_days, cutoff)


if __name__ == "__main__":
    main()
