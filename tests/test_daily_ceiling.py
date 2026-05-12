"""
test_daily_ceiling.py — Backtest a lower daily loss ceiling across EX1 history.

Current rule: stop new entries once realized P&L < -$75 for the session.
Proposed: lower the threshold to -$30 or -$40.

Simulation:
  - Process trades in entry-time order
  - Before each entry, compute realized P&L from all trades that have already CLOSED
  - If realized P&L < threshold, skip the entry
  - All trades are treated as if overlapping is fine (same concurrent-capital rules apply)

Usage: venv/bin/python3 test_daily_ceiling.py
"""

import json
import os

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
CEILINGS  = [-30, -40, -50, -75]


def simulate(trades, threshold):
    """
    threshold: negative number — e.g. -30 means stop entries once realized P&L < -30
    """
    by_entry = sorted(trades, key=lambda t: t["time"])

    total = 0.0
    skipped = []
    kept = []
    triggered = False
    trigger_info = None

    # Build realized-P&L snapshot: for each entry event (entry time),
    # sum P&L of all trades whose exit_time < this trade's entry time
    for t in by_entry:
        # Realized P&L from trades that closed before this one opened
        realized = sum(
            prev["pnl"] for prev in by_entry
            if prev["exit_time"] < t["time"] and prev is not t
        )

        if realized < threshold:
            if not triggered:
                triggered = True
                trigger_info = {"realized_at_trigger": realized}
            skipped.append(t)
        else:
            total += t["pnl"]
            kept.append(t)

    return round(total, 2), skipped, trigger_info


def main():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        data = json.load(f)

    ex1_days = sorted(
        [e for e in data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    print(f"{'Ceiling':>8}  {'Original':>10}  {'Modified':>10}  {'Net':>8}  Days triggered")
    print("-" * 72)

    for threshold in CEILINGS:
        total_orig = 0.0
        total_mod  = 0.0
        trig_days  = []

        for day in ex1_days:
            orig  = day["total_pnl"]
            mod, skipped, tinfo = simulate(day["trades"], threshold)
            total_orig += orig
            total_mod  += mod
            if tinfo:
                trig_days.append(f"{day['date'][5:]} (realized={tinfo['realized_at_trigger']:+.0f}, "
                                  f"skipped {len(skipped)}: {', '.join(t['ticker'] for t in skipped)})")

        net = round(total_mod - total_orig, 2)
        trig_str = "; ".join(trig_days) or "none"
        print(f"${threshold:>7}  ${total_orig:>9.2f}  ${total_mod:>9.2f}  {net:>+8.2f}  {trig_str}")

    # Day-by-day detail for -$30 ceiling
    print("\n--- Day-by-day detail: -$30 ceiling ---")
    for day in ex1_days:
        orig = day["total_pnl"]
        mod, skipped, tinfo = simulate(day["trades"], -30)
        diff = round(mod - orig, 2)
        if tinfo:
            skip_str = ", ".join(f"{t['ticker']}({t['pnl']:+.0f})" for t in skipped)
            print(f"  {day['date']}  TRIGGERED (realized={tinfo['realized_at_trigger']:+.0f})  "
                  f"orig={orig:+.2f}  mod={mod:+.2f}  diff={diff:+.2f}  "
                  f"skipped: {skip_str}")
        else:
            print(f"  {day['date']}  no trigger  orig={orig:+.2f}")


if __name__ == "__main__":
    main()
