"""
test_dell_ddog.py — Run DELL and DDOG through EX1 over the 30-day window.

Does NOT touch exercises.json. Uses a temp file, then deletes it.
Shows per-ticker stats and a day-by-day P&L table.

Run: venv/bin/python3 test_dell_ddog.py
"""

import json
import os
import statistics

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, BASE_DIR)

TICKERS = ["DELL", "DDOG"]

DATES_30 = [
    "2026-03-19","2026-03-20","2026-03-23","2026-03-24","2026-03-25",
    "2026-03-26","2026-03-27","2026-03-31","2026-04-01","2026-04-02",
    "2026-04-06","2026-04-07","2026-04-08","2026-04-09","2026-04-10",
    "2026-04-13","2026-04-14","2026-04-15","2026-04-16","2026-04-17",
    "2026-04-20","2026-04-21","2026-04-22","2026-04-23","2026-04-24",
    "2026-04-27","2026-04-28","2026-04-29","2026-04-30","2026-05-01",
]

TEMP_FILE = "_test_dell_ddog.json"


def run():
    import ex1

    temp_path = os.path.join(BASE_DIR, TEMP_FILE)
    with open(temp_path, "w") as f:
        json.dump([], f)

    orig_tickers = ex1.TICKERS[:]
    ex1.TICKERS = TICKERS

    print(f"\nTesting: {', '.join(TICKERS)}")
    print(f"Dates:   {DATES_30[0]} → {DATES_30[-1]}  ({len(DATES_30)} days)\n")

    try:
        for date in DATES_30:
            ex1.run_ex1(date, save=True, result_file=TEMP_FILE)
    finally:
        ex1.TICKERS = orig_tickers

    with open(temp_path) as f:
        data = json.load(f)
    os.remove(temp_path)

    days = sorted(
        [d for d in data if "Exercise 1" in d.get("title", "")],
        key=lambda d: d["date"],
    )

    all_trades = []
    for day in days:
        all_trades.extend(day.get("trades", []))

    # ── Per-ticker breakdown ─────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  PER-TICKER BREAKDOWN")
    print(f"{'='*70}")
    print(f"  {'Ticker':<8} {'Trades':>7} {'W':>5} {'L':>5} {'WR':>7} {'Total P&L':>11} {'Avg Win':>9} {'Avg Loss':>10}")
    print(f"  {'-'*8} {'-'*7} {'-'*5} {'-'*5} {'-'*7} {'-'*11} {'-'*9} {'-'*10}")

    for ticker in TICKERS:
        trades = [t for t in all_trades if t.get("ticker") == ticker]
        wins   = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]
        total  = sum(t.get("pnl", 0) for t in trades)
        wr     = len(wins) / len(trades) * 100 if trades else 0
        avg_w  = statistics.mean(t["pnl"] for t in wins)  if wins   else 0
        avg_l  = statistics.mean(t["pnl"] for t in losses) if losses else 0
        print(f"  {ticker:<8} {len(trades):>7} {len(wins):>5} {len(losses):>5} "
              f"{wr:>6.0f}% {total:>+11.2f} {avg_w:>+9.2f} {avg_l:>+10.2f}")

    # ── Day-by-day table ─────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  DAY-BY-DAY  (per-ticker signals and P&L)")
    print(f"{'='*70}")
    print(f"  {'Date':<12} {'DELL':>20} {'DDOG':>20} {'Day Total':>11}")
    print(f"  {'-'*12} {'-'*20} {'-'*20} {'-'*11}")

    cum = 0.0
    for day in days:
        day_trades = {t["ticker"]: t for t in day.get("trades", [])}
        row = {}
        for ticker in TICKERS:
            t = day_trades.get(ticker)
            if t:
                row[ticker] = f"{t['rating']} {t['exit_reason'][:4]} {t['pnl']:+.2f}"
            else:
                row[ticker] = "—"
        day_total = day.get("total_pnl", 0)
        cum += day_total
        print(f"  {day['date']:<12} {row.get('DELL','—'):>20} {row.get('DDOG','—'):>20} "
              f"{day_total:>+11.2f}")

    print(f"  {'─'*12} {'─'*20} {'─'*20} {'─'*11}")
    print(f"  {'CUMULATIVE':<12} {'':>20} {'':>20} {cum:>+11.2f}")

    # ── Overall summary ──────────────────────────────────────────────────────
    wins_all   = [t for t in all_trades if t.get("pnl", 0) > 0]
    losses_all = [t for t in all_trades if t.get("pnl", 0) <= 0]
    wr_all = len(wins_all) / len(all_trades) * 100 if all_trades else 0
    days_w = sum(1 for d in days if d.get("total_pnl", 0) > 0)

    print(f"\n  Trades:    {len(all_trades)} total  ({len(wins_all)}W / {len(losses_all)}L,  {wr_all:.0f}% win rate)")
    print(f"  Days:      {days_w}W / {len(days)-days_w}L  out of {len(days)}")
    print(f"  Total P&L: ${cum:+.2f}")
    if wins_all:
        print(f"  Avg win:   ${statistics.mean(t['pnl'] for t in wins_all):+.2f}")
    if losses_all:
        print(f"  Avg loss:  ${statistics.mean(t['pnl'] for t in losses_all):+.2f}")
    print()


if __name__ == "__main__":
    run()
