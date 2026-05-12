"""
test_realloc_options.py — Compare reallocation variants against live + backfill data.

Live    = Apr 13 onward (17 days, exercises.json)
Backfill = Mar 2 – Apr 12 (28 days, backfill2.json, pre-Apr-13 only)

Each mode runs all dates sequentially into a temp file so wallet/streak/drawdown
compound correctly from $5,000. Nothing is written to exercises.json or backfill2.json.

Results reported as: Live P&L | Backfill P&L | Combined total per mode.
"""

import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ex2 as _ex2

LIVE_DATES = [
    "2026-04-13", "2026-04-14", "2026-04-15", "2026-04-16", "2026-04-17",
    "2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24",
    "2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01",
    "2026-05-04", "2026-05-05",
]

BACKFILL_DATES = [
    "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06",
    "2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13",
    "2026-03-16", "2026-03-17", "2026-03-18", "2026-03-19", "2026-03-20",
    "2026-03-23", "2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27",
    "2026-03-31", "2026-04-01", "2026-04-02", "2026-04-06", "2026-04-07",
    "2026-04-08", "2026-04-09", "2026-04-10",
]

MODES = ["baseline", "B", "B2", "C", "C2"]


def run_mode(mode, dates):
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    os.unlink(tmp.name)

    results = {}
    for date in dates:
        r = _ex2.run_ex2(trade_date=date, backfill=False,
                         result_file=tmp.name, realloc_mode=mode)
        results[date] = r["total_pnl"]

    if os.path.exists(tmp.name):
        os.unlink(tmp.name)
    return results


def print_table(label, dates, all_results):
    modes = MODES
    headers = "  ".join(f"{m:>10}" for m in modes[1:])
    diffs   = "  ".join(f"{m+'-diff':>10}" for m in modes[1:])
    print(f"\n  {label} ({len(dates)} days)")
    print(f"  {'DATE':<12}  {'BASELINE':>10}  {headers}  |  {diffs}")
    print("  " + "─" * (14 + 13 * len(modes) + 4 + 13 * (len(modes)-1)))

    totals = {m: 0.0 for m in modes}
    for date in dates:
        b = all_results["baseline"][date]
        row = "  ".join(f"{all_results[m][date]:>+10.2f}" for m in modes[1:])
        drow = "  ".join(f"{all_results[m][date]-b:>+10.2f}" for m in modes[1:])
        for m in modes:
            totals[m] += all_results[m][date]
        print(f"  {date:<12}  {b:>+10.2f}  {row}  |  {drow}")

    print("  " + "─" * (14 + 13 * len(modes) + 4 + 13 * (len(modes)-1)))
    b = totals["baseline"]
    trow  = "  ".join(f"{totals[m]:>+10.2f}" for m in modes[1:])
    tdrow = "  ".join(f"{totals[m]-b:>+10.2f}" for m in modes[1:])
    print(f"  {'TOTAL':<12}  {b:>+10.2f}  {trow}  |  {tdrow}")
    return totals


def main():
    print("\n" + "=" * 80)
    print("  REALLOC OPTIONS TEST")
    print("=" * 80)

    live_results = {}
    bf_results   = {}

    for mode in MODES:
        print(f"\n  Running mode: {mode} (live)...")
        live_results[mode] = run_mode(mode, LIVE_DATES)
        print(f"  Running mode: {mode} (backfill)...")
        bf_results[mode]   = run_mode(mode, BACKFILL_DATES)

    # merge for table printing
    all_live = {m: live_results[m] for m in MODES}
    all_bf   = {m: bf_results[m]   for m in MODES}

    live_totals = print_table("LIVE", LIVE_DATES, all_live)
    bf_totals   = print_table("BACKFILL", BACKFILL_DATES, all_bf)

    # Combined summary
    print(f"\n\n{'=' * 80}")
    print(f"  COMBINED SUMMARY (live + backfill = {len(LIVE_DATES) + len(BACKFILL_DATES)} days)")
    print(f"  {'MODE':<12}  {'LIVE':>10}  {'BACKFILL':>10}  {'COMBINED':>10}  {'vs BASELINE':>12}")
    print("  " + "─" * 60)
    base_combined = live_totals["baseline"] + bf_totals["baseline"]
    for m in MODES:
        live_t = live_totals[m]
        bf_t   = bf_totals[m]
        comb   = live_t + bf_t
        diff   = comb - base_combined
        flag   = "  ✓" if diff > 0 else ""
        print(f"  {m:<12}  {live_t:>+10.2f}  {bf_t:>+10.2f}  {comb:>+10.2f}  {diff:>+12.2f}{flag}")
    print("=" * 80)
    print()


if __name__ == "__main__":
    main()
