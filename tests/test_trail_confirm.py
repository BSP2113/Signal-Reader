"""
test_trail_confirm.py
Test: require 2 consecutive closes above entry+1% before arming the trailing stop,
instead of the current single-bar rule.

Runs on the 15 most recent EX1 days. Prints per-day comparison and summary.
Does NOT write to exercises.json.
"""

import json, os, sys, io, contextlib
import importlib.util

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── load ex1 module ──────────────────────────────────────────────────────────
spec   = importlib.util.spec_from_file_location("ex1", os.path.join(BASE_DIR, "ex1.py"))
ex1mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex1mod)

# ── patched exit function: 2-consecutive-close trail lock ───────────────────
def find_exit_2bar(closes, times, entry_price, entry_bar):
    peak         = entry_price
    consec_above = 0
    trail_armed  = False
    lock_level   = entry_price * (1 + ex1mod.TRAIL_LOCK)

    entry_mins = int(times[entry_bar][:2]) * 60 + int(times[entry_bar][3:])
    t90_mins   = entry_mins + ex1mod.NO_PROGRESS_MINS
    t90_passed = False

    for i in range(entry_bar + 1, len(closes)):
        price    = closes[i]
        bar_mins = int(times[i][:2]) * 60 + int(times[i][3:])
        peak     = max(peak, price)

        if price >= lock_level:
            consec_above += 1
        else:
            consec_above = 0
        if consec_above >= 2:
            trail_armed = True

        if times[i] >= ex1mod.ENTRY_CLOSE:
            return {"bar": i, "time": times[i], "price": price, "reason": "TIME_CLOSE"}
        if price >= entry_price * (1 + ex1mod.TAKE_PROFIT):
            return {"bar": i, "time": times[i], "price": price, "reason": "TAKE_PROFIT"}
        if trail_armed and price <= peak * (1 - ex1mod.TRAIL_STOP):
            return {"bar": i, "time": times[i], "price": price, "reason": "TRAILING_STOP"}
        if price <= entry_price * (1 - ex1mod.STOP_LOSS):
            return {"bar": i, "time": times[i], "price": price, "reason": "STOP_LOSS"}
        if not t90_passed and bar_mins >= t90_mins and t90_mins <= 14 * 60:
            t90_passed = True
            if price <= entry_price:
                return {"bar": i, "time": times[i], "price": price, "reason": "NO_PROGRESS"}

    return {"bar": len(closes) - 1, "time": times[-1], "price": closes[-1], "reason": "EOD"}


@contextlib.contextmanager
def suppress_stdout():
    with io.StringIO() as buf, contextlib.redirect_stdout(buf):
        yield


def run_day(date_str, patched=False):
    if patched:
        original      = ex1mod.find_exit
        ex1mod.find_exit = find_exit_2bar
    try:
        with suppress_stdout():
            result = ex1mod.run_ex1(date_str, backfill=False, save=False)
    finally:
        if patched:
            ex1mod.find_exit = original
    return result


def get_last_n_ex1_dates(n=15):
    path = os.path.join(BASE_DIR, "exercises.json")
    with open(path) as f:
        data = json.load(f)
    dates = sorted({e["date"] for e in data if "Exercise 1" in e["title"]})
    return dates[-n:]


# ── main ────────────────────────────────────────────────────────────────────
dates = get_last_n_ex1_dates(15)
print(f"2-bar trail lock test — {dates[0]} to {dates[-1]}\n")
print(f"{'Date':<12} {'Orig $':>9} {'New $':>9} {'Diff':>8}  Trade changes")
print("─" * 75)

total_orig   = 0.0
total_new    = 0.0
changed_days = 0

for date in dates:
    sys.stdout.write(f"  fetching {date}...\r")
    sys.stdout.flush()
    orig = run_day(date, patched=False)
    new  = run_day(date, patched=True)

    if orig is None or new is None:
        print(f"{date:<12}  (no data)")
        continue

    orig_pnl = orig["total_pnl"]
    new_pnl  = new["total_pnl"]
    diff     = round(new_pnl - orig_pnl, 2)
    total_orig += orig_pnl
    total_new  += new_pnl

    orig_map = {t["ticker"]: t for t in orig["trades"]}
    new_map  = {t["ticker"]: t for t in new["trades"]}
    changes  = []
    for ticker, ot in orig_map.items():
        nt = new_map.get(ticker)
        if nt and ot["exit_reason"] != nt["exit_reason"]:
            delta = round(nt["pnl"] - ot["pnl"], 2)
            changes.append(
                f"{ticker}: {ot['exit_reason']}→{nt['exit_reason']} "
                f"({ot['pnl_pct']:+.2f}%→{nt['pnl_pct']:+.2f}%, Δ${delta:+.2f})"
            )

    flag = " ◄" if abs(diff) > 0.01 else ""
    print(f"{date:<12} ${orig_pnl:>8.2f} ${new_pnl:>8.2f} ${diff:>+7.2f}{flag}")
    for c in changes:
        print(f"             {c}")
    if changes:
        changed_days += 1

print("─" * 75)
total_diff = round(total_new - total_orig, 2)
print(f"{'TOTAL':<12} ${total_orig:>8.2f} ${total_new:>8.2f} ${total_diff:>+7.2f}")
print(f"\n{changed_days} of {len(dates)} days had trade-level changes.")
if total_diff > 0:
    print(f"Result: +${total_diff:.2f} improvement over 15 days.")
else:
    print(f"Result: ${total_diff:.2f} vs baseline.")
