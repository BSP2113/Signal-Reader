"""
entry_time_test.py — Tests entry time filter variants against exercises.json baseline.

The Apr 30 observation: DKNG fired a TAKE signal at 11:19 with only 41 minutes before
the 14:00 hard close. It exited NO_PROGRESS at -$11.20. TAKE signals get the largest
allocations; a late TAKE with no time to reach +3% is high cost, low upside.

Three variants tested:

Variant A (block_after_11): Skip all new entries with entry time >= 11:00.

Variant B (take_only_after_11): After 11:00, allow MAYBE entries but block TAKE
    signals — TAKE gets the biggest allocation and needs the most time.
    (Inverted from the original proposal: the concern is large allocations with no time,
    not late entries per se.)

Variant C (vol_floor_after_11): After 11:00, require >= 2.5x volume for any entry
    (TAKE or MAYBE) to filter low-probability late signals while keeping high-conviction
    late breakouts.

Replays using existing exercises.json trade data — no API calls needed.
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def apply_filter(day, variant):
    """
    Re-simulate a day's trades with the given late-entry filter applied.
    Returns (adjusted_pnl, accepted_count, blocked_count, blocked_trades).
    """
    trades = sorted(day["trades"], key=lambda t: t["time"])
    accepted = []
    blocked = []

    for trade in trades:
        entry_time = trade["time"]
        rating     = trade.get("rating", "MAYBE")
        vol_ratio  = trade.get("vol_ratio", 0.0)

        skip = False

        if variant == "block_after_11":
            if entry_time >= "11:00":
                skip = True

        elif variant == "take_only_after_11":
            # Block TAKE signals after 11:00 — they carry the biggest allocation
            # and need the most time to hit +3% take-profit
            if entry_time >= "11:00" and rating == "TAKE":
                skip = True

        elif variant == "vol_floor_after_11":
            # After 11:00, require >= 2.5x volume for any entry
            if entry_time >= "11:00" and vol_ratio < 2.5:
                skip = True

        if skip:
            blocked.append(trade)
        else:
            accepted.append(trade)

    pnl = round(sum(t["pnl"] for t in accepted), 2)
    return pnl, len(accepted), len(blocked), blocked


def run():
    path = os.path.join(BASE_DIR, "exercises.json")
    with open(path) as f:
        data = json.load(f)

    days = sorted(
        [e for e in data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    variants = ["block_after_11", "take_only_after_11", "vol_floor_after_11"]

    col_w = 18
    header = (f"{'Date':<12} {'Baseline':>{col_w}} {'BlockAfter11':>{col_w}}"
              f" {'TakeOnlyAfter11':>{col_w}} {'VolFloorAfter11':>{col_w}}")
    print(header)
    print("-" * len(header))

    totals  = {v: 0.0 for v in variants}
    totals["baseline"] = 0.0
    blocked_totals = {v: 0 for v in variants}
    days_hit = {v: 0 for v in variants}

    for day in days:
        baseline = day["total_pnl"]
        totals["baseline"] += baseline

        results = {}
        for v in variants:
            pnl, accepted, blocked, _ = apply_filter(day, v)
            results[v] = (pnl, blocked)
            totals[v] += pnl
            blocked_totals[v] += blocked
            if blocked > 0:
                days_hit[v] += 1

        def fmt(variant):
            pnl, n_blocked = results[variant]
            base = f"${pnl:+.2f}"
            if n_blocked > 0:
                diff = pnl - baseline
                sign = "+" if diff >= 0 else ""
                base += f" ({sign}{diff:.2f})"
            return base

        print(
            f"{day['date']:<12}"
            f" {f'${baseline:+.2f}':>{col_w}}"
            f" {fmt('block_after_11'):>{col_w}}"
            f" {fmt('take_only_after_11'):>{col_w}}"
            f" {fmt('vol_floor_after_11'):>{col_w}}"
        )

    print("-" * len(header))
    b  = f"${totals['baseline']:+.2f}"
    ba = f"${totals['block_after_11']:+.2f}"
    to = f"${totals['take_only_after_11']:+.2f}"
    vf = f"${totals['vol_floor_after_11']:+.2f}"
    print(f"{'TOTAL':<12} {b:>{col_w}} {ba:>{col_w}} {to:>{col_w}} {vf:>{col_w}}")

    print()
    print("Net vs baseline:")
    labels = {
        "block_after_11":      "BlockAfter11   ",
        "take_only_after_11":  "TakeOnlyAfter11",
        "vol_floor_after_11":  "VolFloorAfter11",
    }
    for v in variants:
        diff = totals[v] - totals["baseline"]
        sign = "+" if diff >= 0 else ""
        print(f"  {labels[v]}: {sign}${diff:.2f}  "
              f"({days_hit[v]} days affected, {blocked_totals[v]} trades blocked)")

    # Detail: show what was blocked on each affected day
    print()
    print("Blocked trade detail:")
    for v in variants:
        affected = []
        for day in days:
            _, _, n_blocked, blocked_trades = apply_filter(day, v)
            if n_blocked > 0:
                for t in blocked_trades:
                    s = "+" if t["pnl"] >= 0 else ""
                    affected.append(
                        f"  {day['date']}  {t['ticker']:5s} {t['time']} "
                        f"{t['rating']:5s} {t['vol_ratio']}x  "
                        f"{s}${t['pnl']:.2f} ({t['exit_reason']})"
                    )
        if affected:
            print(f"\n{labels[v]}:")
            for line in affected:
                print(line)


if __name__ == "__main__":
    run()
