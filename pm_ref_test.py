"""
Compare PM ORB reference levels: noon_range vs morning_high vs vwap
Runs across all live dates (Apr 13+) and all backfill dates separately.
No writes to exercises.json or backfill2.json.
Uses flat $5k per day (backfill=True) for apples-to-apples comparison.
"""
import json, os, sys
from ex2 import run_ex2

LIVE_DATES = [
    "2026-04-13","2026-04-14","2026-04-15","2026-04-16","2026-04-17",
    "2026-04-20","2026-04-21","2026-04-22","2026-04-23","2026-04-24",
    "2026-04-27","2026-04-28","2026-04-29","2026-04-30","2026-05-01",
    "2026-05-04","2026-05-05",
]

with open("backfill2.json") as f:
    bf_data = json.load(f)
BACKFILL_DATES = [e["date"] for e in bf_data]

MODES = ["noon_range", "morning_high", "vwap"]

def run_mode(dates, pm_ref):
    total = 0.0
    wins = losses = pm_signals = 0
    devnull = open(os.devnull, "w")
    for d in dates:
        sys.stdout = devnull
        result = run_ex2(trade_date=d, backfill=True, result_file=None, pm_ref=pm_ref, save=False)
        sys.stdout = sys.__stdout__
        pnl = result["total_pnl"]
        total += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        pm_signals += sum(1 for t in result["trades"] if t.get("signal") == "PM_ORB")
    devnull.close()
    return round(total, 2), wins, losses, pm_signals

print("\n=== PM ORB REFERENCE LEVEL COMPARISON ===\n")

results = {}
for mode in MODES:
    print(f"Running {mode} backfill ({len(BACKFILL_DATES)} days)...", flush=True)
    bf_pnl, bf_w, bf_l, bf_pm = run_mode(BACKFILL_DATES, mode)
    print(f"Running {mode} live ({len(LIVE_DATES)} days)...", flush=True)
    lv_pnl, lv_w, lv_l, lv_pm = run_mode(LIVE_DATES, mode)
    results[mode] = (bf_pnl, lv_pnl, bf_w+lv_w, bf_l+lv_l, bf_pm+lv_pm)

print()
print(f"{'Mode':<14} {'Backfill P&L':>13} {'Live P&L':>11} {'Total P&L':>11} {'Win Days':>10} {'PM Signals':>12}")
print("-" * 73)
for mode in MODES:
    bf_pnl, lv_pnl, wins, losses, pm_signals = results[mode]
    total = round(bf_pnl + lv_pnl, 2)
    total_days = wins + losses
    print(f"{mode:<14} ${bf_pnl:>+11.2f}   ${lv_pnl:>+9.2f}   ${total:>+9.2f}   {wins}/{total_days:>2}      {pm_signals:>4}")

print("\n--- Delta vs noon_range ---")
base_bf, base_lv, _, _, _ = results["noon_range"]
base_tot = round(base_bf + base_lv, 2)
for mode in ["morning_high", "vwap"]:
    bf_pnl, lv_pnl, _, _, _ = results[mode]
    total = round(bf_pnl + lv_pnl, 2)
    print(f"{mode}: backfill {bf_pnl - base_bf:+.2f} | live {lv_pnl - base_lv:+.2f} | total {total - base_tot:+.2f}")
