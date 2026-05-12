"""
test_pre10_take.py — Pre-10:00 ORB TAKE signal vol floor test.

Context: pre-10:00 ORB TAKE signals are 0/9 wins across 53 days (-$154.12).
MAYBE signals before 10:00 are fine — this is TAKE-specific.

Three variants tested against exercises.json (15d) and backfill.json (38d):

  Variant A (vol_25_floor): require vol_ratio >= 2.5x for TAKE before 10:00.
      Below 2.5x → block entirely. Above 2.5x → keep as TAKE.

  Variant B (downgrade): if TAKE before 10:00 and vol < 2.5x, downgrade to MAYBE
      (keeps the entry but at the smaller MAYBE allocation).

  Variant C (block_all): skip ALL ORB TAKE entries before 10:00, no exceptions.

Run: venv/bin/python3 test_pre10_take.py
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOC_RATIOS = {
    "bullish":  {"TAKE": 0.35, "MAYBE": 0.20},
    "neutral":  {"TAKE": 0.30, "MAYBE": 0.15},
    "bearish":  {"TAKE": 0.10, "MAYBE": 0.10},
}
MAYBE_STREAK_CUT = 0.50
DRAWDOWN_CUT     = 0.50
EARLY_TAKE_VOL   = 2.5   # vol floor for keeping TAKE before 10:00
PRE10_CUTOFF     = "10:00"


def load_ex1(filename):
    path = os.path.join(BASE_DIR, filename)
    with open(path) as f:
        data = json.load(f)
    return sorted([e for e in data if "Exercise 1" in e["title"]], key=lambda e: e["date"])


def _downgrade_alloc(trade, day):
    """
    Recalculate allocation as if this TAKE trade were rated MAYBE instead.
    Accounts for market state, streak cut, and drawdown cut.
    """
    market = day.get("market_state", "neutral")
    ratios = ALLOC_RATIOS.get(market, ALLOC_RATIOS["neutral"])
    take_pct  = ratios["TAKE"]
    maybe_pct = ratios["MAYBE"]

    if take_pct == 0:
        return trade["allocated"]  # BEAR: same pct for both, no change

    # Base ratio
    ratio = maybe_pct / take_pct

    # Streak cut applies to MAYBE but not TAKE — if streak was active, TAKE
    # had no cut but the downgraded MAYBE would. Apply the extra cut.
    in_streak  = day.get("loss_streak", 0) >= 2
    in_drawdown = day.get("in_drawdown", False)

    new_alloc = trade["allocated"] * ratio
    if in_streak:
        new_alloc *= MAYBE_STREAK_CUT
    if in_drawdown:
        new_alloc *= DRAWDOWN_CUT

    return round(new_alloc, 2)


def run_dataset(filename, label):
    days = load_ex1(filename)

    print(f"\n{'='*74}")
    print(f"Pre-10:00 TAKE Vol Floor Test  [{label}]")
    print(f"{'='*74}")

    total_base = total_a = total_b = total_c = 0.0
    changed_a  = changed_b  = changed_c  = 0
    blocked_a  = blocked_b  = blocked_c  = 0

    # Print per-day table
    col = 12
    hdr = (f"{'Date':<12} {'Baseline':>{col}} {'VolFloor2.5':>{col}}"
           f" {'Downgrade':>{col}} {'BlockAll':>{col}}")
    print(f"\n{hdr}")
    print("-" * len(hdr))

    for day in days:
        trades   = day["trades"]
        baseline = day["total_pnl"]
        total_base += baseline

        pnl_a = pnl_b = pnl_c = baseline
        blk_a = blk_b = blk_c = 0
        affected_a = affected_b = affected_c = []

        for t in trades:
            if t.get("signal") != "ORB" or t.get("rating") != "TAKE":
                continue
            if t["time"] >= PRE10_CUTOFF:
                continue

            # This trade is a pre-10:00 ORB TAKE — test all variants
            vr = t.get("vol_ratio", 0)

            # Variant A: block if vol < 2.5x
            if vr < EARLY_TAKE_VOL:
                pnl_a  -= t["pnl"]
                blk_a  += 1
                affected_a.append(t)

            # Variant B: downgrade to MAYBE if vol < 2.5x
            if vr < EARLY_TAKE_VOL:
                new_alloc = _downgrade_alloc(t, day)
                new_pnl   = round(t["pnl"] * new_alloc / t["allocated"], 2) if t["allocated"] else 0
                pnl_b    += new_pnl - t["pnl"]   # replace old pnl with smaller pnl
                blk_b    += 1
                affected_b.append((t, new_alloc, new_pnl))

            # Variant C: block all pre-10:00 TAKE regardless of vol
            pnl_c  -= t["pnl"]
            blk_c  += 1
            affected_c.append(t)

        total_a += pnl_a
        total_b += pnl_b
        total_c += pnl_c
        if abs(pnl_a - baseline) > 0.01: changed_a += 1
        if abs(pnl_b - baseline) > 0.01: changed_b += 1
        if abs(pnl_c - baseline) > 0.01: changed_c += 1
        blocked_a += blk_a
        blocked_b += blk_b
        blocked_c += blk_c

        def _fmt(pnl):
            diff = pnl - baseline
            base = f"${pnl:+.2f}"
            if abs(diff) > 0.01:
                base += f" ({'+' if diff >= 0 else ''}{diff:.2f})"
            return base

        print(f"{day['date']:<12} {f'${baseline:+.2f}':>{col}}"
              f" {_fmt(pnl_a):>{col}} {_fmt(pnl_b):>{col}} {_fmt(pnl_c):>{col}}")

    print("-" * len(hdr))
    print(f"{'TOTAL':<12} {f'${total_base:+.2f}':>{col}}"
          f" {f'${total_a:+.2f}':>{col}} {f'${total_b:+.2f}':>{col}} {f'${total_c:+.2f}':>{col}}")

    da = total_a - total_base
    db = total_b - total_base
    dc = total_c - total_base
    print(f"\n  VolFloor2.5: {'+' if da >= 0 else ''}${da:.2f}  ({changed_a} days changed, {blocked_a} trades affected)")
    print(f"  Downgrade:   {'+' if db >= 0 else ''}${db:.2f}  ({changed_b} days changed, {blocked_b} trades affected)")
    print(f"  BlockAll:    {'+' if dc >= 0 else ''}${dc:.2f}  ({changed_c} days changed, {blocked_c} trades affected)")

    # Detail: all pre-10:00 TAKE trades and what each variant does with them
    print(f"\n  All pre-10:00 ORB TAKE trades in this dataset:")
    print(f"  {'Date':<12} {'Ticker':<6} {'Entry':>5} {'Exit':>5} {'Reason':<14} {'Vol':>5}x "
          f"{'Alloc':>8} {'PnL':>9}  {'VolFloor':>9}  {'Downgrade':>9}  {'BlockAll':>9}")
    print("  " + "-" * 90)

    for day in days:
        for t in day["trades"]:
            if t.get("signal") != "ORB" or t.get("rating") != "TAKE":
                continue
            if t["time"] >= PRE10_CUTOFF:
                continue

            vr        = t.get("vol_ratio", 0)
            alloc     = t["allocated"]
            pnl       = t["pnl"]
            new_alloc = _downgrade_alloc(t, day)
            new_pnl_b = round(pnl * new_alloc / alloc, 2) if alloc else 0

            vf_str = "KEEP" if vr >= EARLY_TAKE_VOL else f"BLOCK (${-pnl:+.2f})"
            dg_str = f"KEEP" if vr >= EARLY_TAKE_VOL else f"→MAYBE ${new_pnl_b:+.2f}"
            bl_str = f"BLOCK (${-pnl:+.2f})"
            sp = "+" if pnl >= 0 else ""
            print(f"  {day['date']:<12} {t['ticker']:<6} {t['time']:>5} {t['exit_time']:>5} "
                  f"{t['exit_reason']:<14} {vr:>5.1f}x "
                  f"${alloc:>7.2f} {sp}${abs(pnl):>7.2f}  {vf_str:>9}  {dg_str:>9}  {bl_str:>9}")


if __name__ == "__main__":
    run_dataset("exercises.json", "exercises.json — 15 days")
    run_dataset("backfill.json",  "backfill.json  — 38 days")

    # Combined summary
    ex = load_ex1("exercises.json")
    bf = load_ex1("backfill.json")
    all_days = sorted(ex + bf, key=lambda e: e["date"])

    # Deduplicate (exercises wins for shared dates)
    by_date = {}
    for d in bf: by_date[d["date"]] = d
    for d in ex: by_date[d["date"]] = d
    all_days = sorted(by_date.values(), key=lambda e: e["date"])

    print(f"\n{'='*74}")
    print(f"COMBINED SUMMARY (53 days, deduplicated)")
    print(f"{'='*74}")

    tot_base = tot_a = tot_b = tot_c = 0.0
    for day in all_days:
        tot_base += day["total_pnl"]
        for t in day["trades"]:
            if t.get("signal") != "ORB" or t.get("rating") != "TAKE" or t["time"] >= PRE10_CUTOFF:
                continue
            vr = t.get("vol_ratio", 0)
            if vr < EARLY_TAKE_VOL:
                tot_a -= t["pnl"]
                new_alloc = _downgrade_alloc(t, day)
                new_pnl   = round(t["pnl"] * new_alloc / t["allocated"], 2) if t["allocated"] else 0
                tot_b += new_pnl - t["pnl"]
            tot_c -= t["pnl"]

    tot_a += tot_base
    tot_b += tot_base
    tot_c += tot_base

    da = tot_a - tot_base
    db = tot_b - tot_base
    dc = tot_c - tot_base
    print(f"\n  Baseline:    ${tot_base:+.2f}")
    print(f"  VolFloor2.5: {'+' if da >= 0 else ''}${da:.2f}  (blocks trades with vol < 2.5x)")
    print(f"  Downgrade:   {'+' if db >= 0 else ''}${db:.2f}  (shrinks allocation for vol < 2.5x)")
    print(f"  BlockAll:    {'+' if dc >= 0 else ''}${dc:.2f}  (skips all pre-10:00 TAKE signals)")
    print()
    if dc > da:
        print("  → BlockAll outperforms VolFloor2.5: high-vol early TAKEs are also losing.")
    elif da > 0 and dc <= 0:
        print("  → VolFloor2.5 is better: high-vol early TAKEs worth keeping.")
    else:
        print("  → Both positive: strong case for shipping. BlockAll is simplest rule.")
