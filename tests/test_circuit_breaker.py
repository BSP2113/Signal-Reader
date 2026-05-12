"""
test_circuit_breaker.py — Backtest an early stop-out circuit breaker.

The idea (from 4/20 and 4/21 notes): when 2 stop-loss or trailing-stop exits
fire before a time cutoff, either halt all remaining entries or cut allocations
in half for the rest of the session.

Tests multiple variants of cutoff time and response mode.
"""

import json, os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOP_TYPES = {"STOP_LOSS", "TRAILING_STOP"}

def mins(t): h, m = map(int, t.split(":")); return h * 60 + m


def simulate(trades, cutoff, n_trigger, mode):
    """
    cutoff   : time string — stop must EXIT before this to count
    n_trigger: number of stops needed to trigger the rule
    mode     : "halt" = skip new entries | "half" = 50% allocation (P&L)
    """
    by_entry = sorted(trades, key=lambda t: t["time"])

    # Find which stops fire before the cutoff
    qualifying = sorted(
        [t for t in by_entry if t["exit_reason"] in STOP_TYPES and t["exit_time"] < cutoff],
        key=lambda t: t["exit_time"]
    )

    trigger_time = qualifying[n_trigger - 1]["exit_time"] if len(qualifying) >= n_trigger else None

    total = 0.0
    for t in by_entry:
        if trigger_time and t["time"] > trigger_time:
            if mode == "halt":
                continue
            else:
                total += t["pnl"] * 0.5
        else:
            total += t["pnl"]

    return round(total, 2), trigger_time


def run(ex1_days, cutoff, mode):
    total_orig = 0.0
    total_mod  = 0.0
    triggered_days = []

    for day in ex1_days:
        date   = day["date"]
        trades = day["trades"]
        orig   = day["total_pnl"]
        mod, trigger = simulate(trades, cutoff, 2, mode)

        if trigger:
            triggered_days.append((date, trigger, orig, mod))

        total_orig += orig
        total_mod  += mod

    return total_orig, total_mod, triggered_days


def main():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        data = json.load(f)
    ex1_days = sorted(
        [e for e in data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    variants = [
        ("10:00", "halt"),
        ("10:00", "half"),
        ("10:30", "halt"),
        ("10:30", "half"),
        ("11:00", "halt"),
        ("11:00", "half"),
    ]

    print(f"{'Cutoff':<8} {'Mode':<6} {'Original':>10} {'Modified':>10} {'Net':>8}  Triggered days")
    print("-" * 72)
    for cutoff, mode in variants:
        orig, mod, triggered = run(ex1_days, cutoff, mode)
        net = round(mod - orig, 2)
        trig_str = ", ".join(f"{d[0][5:]} (trig {d[1]}, {d[2]:+.0f}→{d[3]:+.0f})" for d in triggered) or "none"
        print(f"{cutoff:<8} {mode:<6} ${orig:>9.2f} ${mod:>9.2f} {net:>+8.2f}  {trig_str}")

    # Detail on the most impactful variant
    print("\n--- Day-by-day detail: 10:30 cutoff, halt ---")
    for day in ex1_days:
        trades = day["trades"]
        orig   = day["total_pnl"]
        mod, trigger = simulate(trades, "10:30", 2, "halt")
        diff = round(mod - orig, 2)
        if trigger:
            affected = [t for t in trades if t["time"] > trigger]
            aff_str  = ", ".join(f"{t['ticker']}({t['pnl']:+.0f})" for t in affected)
            print(f"  {day['date']}  TRIGGERED at {trigger}  orig={orig:+.2f} mod={mod:+.2f} diff={diff:+.2f}  blocked: {aff_str}")
        else:
            print(f"  {day['date']}  no trigger            orig={orig:+.2f}")


if __name__ == "__main__":
    main()
