"""
Runs EX2 PM ORB reallocation modes 0–3 across all 17 dates silently,
then prints a side-by-side comparison table.
"""
import json, sys, io, os
sys.path.insert(0, os.path.dirname(__file__))

# Redirect stdout to suppress per-date print noise
import contextlib

DATES = [
    "2026-04-13","2026-04-14","2026-04-15","2026-04-16","2026-04-17",
    "2026-04-20","2026-04-21","2026-04-22","2026-04-23","2026-04-24",
    "2026-04-27","2026-04-28","2026-04-29","2026-04-30","2026-05-01",
    "2026-05-04","2026-05-05",
]

MODE_LABELS = {
    0: "Baseline (current)",
    1: "Mode A — MAYBE PM ORBs trigger realloc",
    2: "Mode B — PM ORBs sell 2hr-old losers only",
    3: "Mode C — PM ORBs sell anything below +2%",
}

from ex2_test import run_ex2

results = {}  # mode -> list of (date, pnl, wins)

for mode in range(4):
    mode_results = []
    for d in DATES:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ex = run_ex2(d, backfill=False, save=False, pm_realloc_mode=mode)
        pnl = ex["total_pnl"]
        win = 1 if pnl > 0 else 0
        mode_results.append((d, pnl, win))
    results[mode] = mode_results
    print(f"Mode {mode} done", file=sys.stderr)

# Print table
print()
print(f"{'Date':<12} {'Base':>8} {'Mode A':>8} {'Mode B':>8} {'Mode C':>8}  {'A-diff':>8} {'B-diff':>8} {'C-diff':>8}")
print("-" * 80)
for i, d in enumerate(DATES):
    base  = results[0][i][1]
    a     = results[1][i][1]
    b     = results[2][i][1]
    c     = results[3][i][1]
    print(f"{d:<12} {base:>+8.2f} {a:>+8.2f} {b:>+8.2f} {c:>+8.2f}  {a-base:>+8.2f} {b-base:>+8.2f} {c-base:>+8.2f}")

print("-" * 80)
base_tot  = sum(r[1] for r in results[0])
a_tot     = sum(r[1] for r in results[1])
b_tot     = sum(r[1] for r in results[2])
c_tot     = sum(r[1] for r in results[3])
base_wr   = sum(r[2] for r in results[0])
a_wr      = sum(r[2] for r in results[1])
b_wr      = sum(r[2] for r in results[2])
c_wr      = sum(r[2] for r in results[3])
n = len(DATES)
print(f"{'TOTAL':<12} {base_tot:>+8.2f} {a_tot:>+8.2f} {b_tot:>+8.2f} {c_tot:>+8.2f}  {a_tot-base_tot:>+8.2f} {b_tot-base_tot:>+8.2f} {c_tot-base_tot:>+8.2f}")
print(f"{'WIN RATE':<12} {base_wr}/{n}      {a_wr}/{n}      {b_wr}/{n}      {c_wr}/{n}")
print()
print("Mode labels:")
for m, lbl in MODE_LABELS.items():
    print(f"  {m}: {lbl}")
