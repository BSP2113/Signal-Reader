"""
test_vol_floor.py — Backtest raising the entry volume floor from 1.0x to 1.5x.

Current rule: signals below 0.5x = SKIP, 0.5x-1.0x = capped at MAYBE, 1.0x+ can reach TAKE.
Proposed rule: skip any entry where vol_ratio < 1.5 at the time the signal fires.

Usage: venv/bin/python3 test_vol_floor.py
"""

import json
import os

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
VOL_FLOOR = 1.5


def main():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        data = json.load(f)

    ex1_days = [e for e in data if "Exercise 1" in e["title"]]
    ex1_days.sort(key=lambda e: e["date"])

    total_original = 0.0
    total_modified = 0.0
    all_skipped    = []

    for day in ex1_days:
        date   = day["date"]
        trades = day["trades"]

        day_original = sum(t["pnl"] for t in trades)
        day_modified = 0.0
        skipped      = []
        kept         = []

        for t in trades:
            vol = t.get("vol_ratio", 0)
            if vol < VOL_FLOOR:
                skipped.append(t)
                all_skipped.append({**t, "date": date})
            else:
                day_modified += t["pnl"]
                kept.append(t)

        diff = day_modified - day_original
        print(f"\n--- {date}  orig={day_original:+.2f}  modified={day_modified:+.2f}  diff={diff:+.2f} ---")

        for t in kept:
            print(f"  KEPT    {t['ticker']:6} vol={t['vol_ratio']:.2f}x  {t['exit_reason']:15}  pnl={t['pnl']:+.2f}")
        for t in skipped:
            print(f"  SKIPPED {t['ticker']:6} vol={t['vol_ratio']:.2f}x  {t['exit_reason']:15}  pnl={t['pnl']:+.2f}  (would have been {'+gain' if t['pnl']>0 else 'loss'})")

        total_original += day_original
        total_modified += day_modified

    net = total_modified - total_original
    skipped_winners = [t for t in all_skipped if t["pnl"] > 0]
    skipped_losers  = [t for t in all_skipped if t["pnl"] < 0]

    print(f"\n{'='*60}")
    print(f"SUMMARY — Volume floor raised to {VOL_FLOOR}x")
    print(f"  Original total P&L:  ${total_original:+.2f}")
    print(f"  Modified total P&L:  ${total_modified:+.2f}")
    print(f"  Net difference:      ${net:+.2f}")
    print()
    print(f"  Trades skipped: {len(all_skipped)}")
    print(f"    Would-be losers avoided:  {len(skipped_losers)}  (${sum(t['pnl'] for t in skipped_losers):+.2f})")
    print(f"    Would-be winners skipped: {len(skipped_winners)}  (${sum(t['pnl'] for t in skipped_winners):+.2f})")

    if skipped_winners:
        print("\n  Winners we'd have missed:")
        for t in skipped_winners:
            print(f"    {t['date']} {t['ticker']:6} vol={t['vol_ratio']:.2f}x  pnl={t['pnl']:+.2f}  exit={t['exit_reason']}")


if __name__ == "__main__":
    main()
