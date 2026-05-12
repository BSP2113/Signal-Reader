"""
rule_test.py — Tests three rule variants against exercises.json (15 days) and backfill.json (38 days).

Rule 1 (bear_maybe_skip): On BEAR market days, skip all MAYBE-rated entries entirely.
    Only enter on TAKE-rated signals when market_state == 'bearish'.

Rule 2 (zero_take_maybe_floor): On days where zero TAKE signals appear in the session,
    require vol_ratio >= 1.5x for MAYBE entries (instead of the standard 1.0x floor).

Rule 3 (scaled_day_limit): Replace the fixed $75 daily loss limit with 1.5% of
    starting_capital for that session.
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXED_DAY_LIMIT = -75.0


def apply_rule(day, rule):
    trades     = sorted(day["trades"], key=lambda t: t["time"])
    market     = day.get("market_state", "neutral")
    start_cap  = day.get("starting_capital", 5000.0)
    has_take   = any(t["rating"] == "TAKE" for t in trades)
    day_limit  = -(start_cap * 0.015) if rule == "scaled_day_limit" else FIXED_DAY_LIMIT

    accepted       = []
    blocked        = []
    realized_pnl   = 0.0
    day_limit_hit  = False

    for trade in trades:
        if day_limit_hit:
            blocked.append(trade)
            continue

        skip = False

        if rule == "bear_maybe_skip":
            if market == "bearish" and trade["rating"] == "MAYBE":
                skip = True

        elif rule == "zero_take_maybe_floor":
            if not has_take and trade["rating"] == "MAYBE" and trade.get("vol_ratio", 0) < 1.5:
                skip = True

        elif rule == "scaled_day_limit":
            pass  # day_limit already adjusted above

        if skip:
            blocked.append(trade)
            continue

        accepted.append(trade)
        realized_pnl = round(sum(t["pnl"] for t in accepted), 2)
        if realized_pnl <= day_limit:
            day_limit_hit = True

    pnl = round(sum(t["pnl"] for t in accepted), 2)
    return pnl, len(accepted), len(blocked)


def run_dataset(filename, label):
    path = os.path.join(BASE_DIR, filename)
    with open(path) as f:
        data = json.load(f)

    days = sorted(
        [e for e in data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    rules = ["bear_maybe_skip", "zero_take_maybe_floor", "scaled_day_limit"]

    col = 16
    header = "%-12s %12s %16s %20s %16s" % (
        "Date", "Baseline", "BearMaybeSkip", "ZeroTakeMaybeFloor", "ScaledDayLimit"
    )
    print(f"\n{'='*len(header)}")
    print(f"{label}  ({len(days)} days)")
    print('='*len(header))
    print(header)
    print("-" * len(header))

    totals  = {"baseline": 0.0}
    totals.update({r: 0.0 for r in rules})
    blocked_totals = {r: 0 for r in rules}
    days_hit       = {r: 0 for r in rules}

    for day in days:
        baseline = day["total_pnl"]
        totals["baseline"] += baseline

        res = {}
        for r in rules:
            pnl, n_acc, n_blocked = apply_rule(day, r)
            res[r] = (pnl, n_blocked)
            totals[r] += pnl
            blocked_totals[r] += n_blocked
            if pnl != baseline:
                days_hit[r] += 1

        def fmt(rule):
            pnl, n_blocked = res[rule]
            s = "$%+.2f" % pnl
            if pnl != baseline:
                diff = pnl - baseline
                s += " (%s%.2f)" % ("+" if diff >= 0 else "", diff)
            return s

        print("%-12s %12s %16s %20s %16s" % (
            day["date"],
            "$%+.2f" % baseline,
            fmt("bear_maybe_skip"),
            fmt("zero_take_maybe_floor"),
            fmt("scaled_day_limit"),
        ))

    print("-" * len(header))
    print("%-12s %12s %16s %20s %16s" % (
        "TOTAL",
        "$%+.2f" % totals["baseline"],
        "$%+.2f" % totals["bear_maybe_skip"],
        "$%+.2f" % totals["zero_take_maybe_floor"],
        "$%+.2f" % totals["scaled_day_limit"],
    ))

    print()
    labels = {
        "bear_maybe_skip":      "BearMaybeSkip     ",
        "zero_take_maybe_floor": "ZeroTakeMaybeFloor",
        "scaled_day_limit":     "ScaledDayLimit    ",
    }
    for r in rules:
        diff = totals[r] - totals["baseline"]
        print("  %s: %s$%.2f  (%d days changed, %d trades blocked)" % (
            labels[r],
            "+" if diff >= 0 else "",
            diff,
            days_hit[r],
            blocked_totals[r],
        ))


if __name__ == "__main__":
    run_dataset("exercises.json", "LIVE (exercises.json)")
    run_dataset("backfill.json",  "BACKFILL (backfill.json)")
