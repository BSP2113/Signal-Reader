"""
test_two_oneliners.py — Two small rule tests, 2026-05-06.

TEST 1: Block GAP_GO entries that fire on the 09:31 bar (first bar after open).
  Rationale: 09:31 fires at the peak of the opening spike with no post-gap
  consolidation. ARM May 5 reversed immediately for -$30.33 on a 09:31 GAP_GO.
  Applies to EX1 (exercises.json + backfill.json).

TEST 2: Block PM_ORB MAYBE entries after 13:00. TAKE-rated PM_ORBs after 13:00
  are unaffected.
  Rationale: With under 60 minutes until the 14:00 hard close, MAYBE-rated
  PM_ORBs have no room for EARLY_WEAK (T+45) or trail runway to develop.
  Applies to EX2 (exercises.json + backfill2.json).

Run: venv/bin/python3 tests/test_two_oneliners.py
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_ex1(filename):
    path = os.path.join(BASE_DIR, filename)
    with open(path) as f:
        data = json.load(f)
    return sorted([e for e in data if "Exercise 1" in e["title"]], key=lambda e: e["date"])


def load_ex2(filename):
    path = os.path.join(BASE_DIR, filename)
    with open(path) as f:
        data = json.load(f)
    return sorted([e for e in data if "Exercise 2" in e["title"]], key=lambda e: e["date"])


def dedup(primary, secondary):
    """Merge two day lists; primary wins on shared dates."""
    by_date = {}
    for d in secondary:
        by_date[d["date"]] = d
    for d in primary:
        by_date[d["date"]] = d
    return sorted(by_date.values(), key=lambda e: e["date"])


# ─── TEST 1: Block 09:31 GAP_GO entries ───────────────────────────────────────

def test1_first_bar_gapgo(filename, label):
    days = load_ex1(filename)

    print(f"\n{'='*70}")
    print(f"TEST 1: Block GAP_GO entries at 09:31  [{label}]")
    print(f"{'='*70}")

    col = 12
    hdr = f"{'Date':<12} {'Baseline':>{col}} {'Block09:31':>{col}}  Blocked trades"
    print(f"\n{hdr}")
    print("-" * 72)

    total_base = total_new = 0.0
    n_blocked = 0
    affected_days = 0
    detail_rows = []

    for day in days:
        baseline = day["total_pnl"]
        new_pnl  = baseline
        blocked  = []

        for t in day["trades"]:
            if t.get("signal") == "GAP_GO" and t["time"] == "09:31":
                new_pnl -= t["pnl"]
                blocked.append(t)
                n_blocked += 1

        total_base += baseline
        total_new  += new_pnl
        if blocked:
            affected_days += 1

        diff = new_pnl - baseline
        tag  = ""
        if blocked:
            tag = "  " + ", ".join(
                f"{t['ticker']} {t['rating']} {t['vol_ratio']:.1f}x {t['pnl']:+.2f}"
                for t in blocked
            )
        diff_str = f" ({'+' if diff >= 0 else ''}{diff:.2f})" if abs(diff) > 0.01 else ""
        print(f"{day['date']:<12} ${baseline:>+9.2f} ${new_pnl:>+9.2f}{diff_str:<10}{tag}")

        for t in blocked:
            detail_rows.append((day["date"], t))

    print("-" * 72)
    net = total_new - total_base
    print(f"{'TOTAL':<12} ${total_base:>+9.2f} ${total_new:>+9.2f}  net {'+' if net >= 0 else ''}{net:.2f}")
    print(f"\n  {n_blocked} trades blocked across {affected_days} days")

    return detail_rows, total_base, total_new


def test1_combined():
    ex_rows,  ex_base,  ex_new  = test1_first_bar_gapgo("exercises.json", "exercises — 17 live days")
    bf_rows,  bf_base,  bf_new  = test1_first_bar_gapgo("backfill.json",  "backfill  — 38 days")

    all_rows = ex_rows + bf_rows
    # Deduplicate on date+ticker
    seen = set()
    uniq = []
    for date, t in all_rows:
        key = (date, t["ticker"])
        if key not in seen:
            seen.add(key)
            uniq.append((date, t))

    print(f"\n{'='*70}")
    print("TEST 1: COMBINED SUMMARY (55 days, deduplicated)")
    print(f"{'='*70}")

    ex  = load_ex1("exercises.json")
    bf  = load_ex1("backfill.json")
    all_days = dedup(ex, bf)

    total_base = total_new = 0.0
    for day in all_days:
        total_base += day["total_pnl"]
        new_pnl = day["total_pnl"]
        for t in day["trades"]:
            if t.get("signal") == "GAP_GO" and t["time"] == "09:31":
                new_pnl -= t["pnl"]
        total_new += new_pnl

    net = total_new - total_base
    print(f"\n  Baseline:      ${total_base:+.2f}")
    print(f"  Block 09:31:   ${total_new:+.2f}  ({'+' if net >= 0 else ''}{net:.2f})")

    print(f"\n  All 09:31 GAP_GO trades (combined, unique):")
    print(f"  {'Date':<12} {'Ticker':<6} {'Rtg':<5} {'Vol':>5}  {'Exit':>16}  {'PnL':>9}")
    print("  " + "-" * 60)
    wins = losses = 0
    for date, t in sorted(uniq, key=lambda x: x[0]):
        sign = "+" if t["pnl"] >= 0 else ""
        reason = t.get("exit_reason", "?")
        print(f"  {date:<12} {t['ticker']:<6} {t['rating']:<5} {t['vol_ratio']:>4.1f}x"
              f"  {reason:>16}  {sign}${abs(t['pnl']):.2f}")
        if t["pnl"] >= 0:
            wins += 1
        else:
            losses += 1
    print(f"\n  Win/Loss: {wins}W / {losses}L  ({100*wins//(wins+losses) if wins+losses else 0}% WR)")

    if net < 0:
        print(f"\n  → Not Pursuing: blocking 09:31 GAP_GO costs ${abs(net):.2f} net.")
        print(f"     These trades are {100*wins//(wins+losses) if wins+losses else 0}% WR — the ARM May 5 loss is an outlier.")
    else:
        print(f"\n  → Net positive: consider shipping.")


# ─── TEST 2: Block PM_ORB MAYBE entries after 13:00 ──────────────────────────

def test2_pm_orb_late_maybe(filename, label):
    days = load_ex2(filename)

    print(f"\n{'='*70}")
    print(f"TEST 2: Block PM_ORB MAYBE after 13:00  [{label}]")
    print(f"{'='*70}")
    print("TAKE-rated PM_ORBs after 13:00 are unaffected.\n")

    col = 12
    hdr = f"{'Date':<12} {'Baseline':>{col}} {'BlockLateMaybe':>{col}}  Blocked trades"
    print(f"\n{hdr}")
    print("-" * 72)

    total_base = total_new = 0.0
    n_blocked = 0
    affected_days = 0
    detail_rows = []

    for day in days:
        baseline = day["total_pnl"]
        new_pnl  = baseline
        blocked  = []

        for t in day["trades"]:
            if (t.get("signal") == "PM_ORB"
                    and t.get("rating") == "MAYBE"
                    and t["time"] > "13:00"):
                new_pnl -= t["pnl"]
                blocked.append(t)
                n_blocked += 1

        total_base += baseline
        total_new  += new_pnl
        if blocked:
            affected_days += 1

        diff = new_pnl - baseline
        tag  = ""
        if blocked:
            tag = "  " + ", ".join(
                f"{t['ticker']} {t['time']} {t['vol_ratio']:.1f}x {t['pnl']:+.2f}"
                for t in blocked
            )
        diff_str = f" ({'+' if diff >= 0 else ''}{diff:.2f})" if abs(diff) > 0.01 else ""
        print(f"{day['date']:<12} ${baseline:>+9.2f} ${new_pnl:>+9.2f}{diff_str:<10}{tag}")

        for t in blocked:
            detail_rows.append((day["date"], t))

    print("-" * 72)
    net = total_new - total_base
    print(f"{'TOTAL':<12} ${total_base:>+9.2f} ${total_new:>+9.2f}  net {'+' if net >= 0 else ''}{net:.2f}")
    print(f"\n  {n_blocked} trades blocked across {affected_days} days")

    return detail_rows, total_base, total_new


def test2_combined():
    ex_rows, ex_base, ex_new = test2_pm_orb_late_maybe("exercises.json", "exercises — 17 live days")
    bf_rows, bf_base, bf_new = test2_pm_orb_late_maybe("backfill2.json", "backfill2 — 28 days")

    all_rows = ex_rows + bf_rows
    seen = set()
    uniq = []
    for date, t in all_rows:
        key = (date, t["ticker"], t["time"])
        if key not in seen:
            seen.add(key)
            uniq.append((date, t))

    print(f"\n{'='*70}")
    print("TEST 2: COMBINED SUMMARY (45 days, deduplicated)")
    print(f"{'='*70}")

    ex  = load_ex2("exercises.json")
    bf  = load_ex2("backfill2.json")
    all_days = dedup(ex, bf)

    total_base = total_new = 0.0
    for day in all_days:
        total_base += day["total_pnl"]
        new_pnl = day["total_pnl"]
        for t in day["trades"]:
            if (t.get("signal") == "PM_ORB"
                    and t.get("rating") == "MAYBE"
                    and t["time"] > "13:00"):
                new_pnl -= t["pnl"]
        total_new += new_pnl

    net = total_new - total_base
    print(f"\n  Baseline:           ${total_base:+.2f}")
    print(f"  Block PM_ORB MAYBE >13:00: ${total_new:+.2f}  ({'+' if net >= 0 else ''}{net:.2f})")

    print(f"\n  All PM_ORB MAYBE >13:00 trades (combined, unique):")
    print(f"  {'Date':<12} {'Ticker':<6} {'Time':>5} {'Vol':>5}  {'Exit':>16}  {'PnL':>9}")
    print("  " + "-" * 60)
    wins = losses = 0
    for date, t in sorted(uniq, key=lambda x: x[0]):
        sign = "+" if t["pnl"] >= 0 else ""
        reason = t.get("exit_reason", "?")
        print(f"  {date:<12} {t['ticker']:<6} {t['time']:>5} {t['vol_ratio']:>4.1f}x"
              f"  {reason:>16}  {sign}${abs(t['pnl']):.2f}")
        if t["pnl"] >= 0:
            wins += 1
        else:
            losses += 1
    print(f"\n  Win/Loss: {wins}W / {losses}L  ({100*wins//(wins+losses) if wins+losses else 0}% WR)")

    if net < 0:
        print(f"\n  → Not Pursuing: blocking late PM_ORB MAYBEs costs ${abs(net):.2f} net.")
    elif net > 0:
        print(f"\n  → Net positive (+${net:.2f}): consider shipping.")
    else:
        print(f"\n  → No effect.")


if __name__ == "__main__":
    test1_combined()
    test2_combined()
