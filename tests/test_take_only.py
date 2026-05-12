"""
test_take_only.py — TAKE-only mode: skip all MAYBE-rated entries.

Compares baseline P&L (all trades) vs TAKE-only P&L (MAYBE entries dropped)
across both exercises.json (live days) and backfill.json (historical days).

No exits are re-simulated — MAYBE trades are simply removed and the
remaining TAKE trade P&L is summed. This shows how much MAYBE entries
cost vs. contribute.

Usage: venv/bin/python3 tests/test_take_only.py
"""

import json, os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def analyze(days, label):
    baseline = 0.0
    take_only = 0.0
    maybe_trades = []
    take_trades  = []

    for day in sorted(days, key=lambda e: e["date"]):
        for t in day["trades"]:
            baseline += t["pnl"]
            if t["rating"] == "MAYBE":
                maybe_trades.append({**t, "date": day["date"]})
            else:
                take_only += t["pnl"]
                take_trades.append({**t, "date": day["date"]})

    net = take_only - baseline
    n_maybe = len(maybe_trades)
    maybe_wins   = sum(1 for t in maybe_trades if t["pnl"] > 0)
    maybe_losses = sum(1 for t in maybe_trades if t["pnl"] < 0)
    maybe_pnl    = sum(t["pnl"] for t in maybe_trades)
    maybe_stops  = sum(1 for t in maybe_trades if t["exit_reason"] == "STOP_LOSS")

    print(f"\n{'='*60}")
    print(f"{label}  ({len(days)} days)")
    print(f"{'='*60}")
    print(f"  Baseline (all trades):   ${baseline:>8.2f}")
    print(f"  TAKE-only:               ${take_only:>8.2f}  (net {net:>+.2f})")
    print()
    print(f"  MAYBE trades dropped: {n_maybe}")
    print(f"    Winners missed:  {maybe_wins}")
    print(f"    Losers avoided:  {maybe_losses}  ({maybe_stops} stop-losses)")
    print(f"    Net MAYBE P&L:   ${maybe_pnl:>+.2f}")
    print()

    # Per-day breakdown — only show days where something changes
    changed_days = {}
    for day in sorted(days, key=lambda e: e["date"]):
        day_maybe = [t for t in day["trades"] if t["rating"] == "MAYBE"]
        if not day_maybe:
            continue
        day_maybe_pnl = sum(t["pnl"] for t in day_maybe)
        day_base = sum(t["pnl"] for t in day["trades"])
        changed_days[day["date"]] = (day_base, day_maybe_pnl, day_maybe)

    print(f"  Days with MAYBE trades ({len(changed_days)}):")
    print(f"  {'Date':<12} {'DayP&L':>8} {'MAYBEcontrib':>13}  Tickers")
    print(f"  {'-'*58}")
    for date, (day_base, day_maybe_pnl, trades) in sorted(changed_days.items()):
        tickers = ", ".join(f"{t['ticker']}({t['pnl']:+.2f})" for t in trades)
        print(f"  {date:<12} {day_base:>+8.2f} {day_maybe_pnl:>+13.2f}  {tickers}")


def main():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        ex_data = json.load(f)
    with open(os.path.join(BASE_DIR, "backfill.json")) as f:
        bf_data = json.load(f)

    ex1_live = [e for e in ex_data if "Exercise 1" in e["title"]]
    ex1_back = [e for e in bf_data if "Exercise 1" in e["title"]]

    analyze(ex1_live, "EX1 — Live days (exercises.json)")
    analyze(ex1_back, "EX1 — Backfill (backfill.json)")

    # Combined
    all_days = ex1_live + ex1_back
    baseline_all = sum(t["pnl"] for e in all_days for t in e["trades"])
    take_all     = sum(t["pnl"] for e in all_days for t in e["trades"] if t["rating"] == "TAKE")
    print(f"\n{'='*60}")
    print(f"COMBINED ({len(all_days)} days)")
    print(f"{'='*60}")
    print(f"  Baseline:   ${baseline_all:>8.2f}")
    print(f"  TAKE-only:  ${take_all:>8.2f}  (net {take_all - baseline_all:>+.2f})")


if __name__ == "__main__":
    main()
