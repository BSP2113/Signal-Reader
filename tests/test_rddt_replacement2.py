"""
test_rddt_replacement2.py — Second batch of RDDT replacement candidates.
Tests ACHR, TTD, APP through EX1 over the full 43-day window
(38-day backfill + 5 additional live days).

Does NOT touch exercises.json or backfill.json. Uses a temp file, then deletes it.

Run: venv/bin/python3 test_rddt_replacement2.py
"""

import json
import os
import statistics

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, BASE_DIR)

CANDIDATES = ["ACHR", "TTD", "APP"]

DATES_BACKFILL = [
    "2026-03-02","2026-03-03","2026-03-04","2026-03-05","2026-03-06",
    "2026-03-09","2026-03-10","2026-03-11","2026-03-12","2026-03-13",
    "2026-03-16","2026-03-17","2026-03-18","2026-03-19","2026-03-20",
    "2026-03-23","2026-03-24","2026-03-25","2026-03-26","2026-03-27",
    "2026-03-31","2026-04-01","2026-04-02","2026-04-06","2026-04-07",
    "2026-04-08","2026-04-09","2026-04-10","2026-04-13","2026-04-14",
    "2026-04-15","2026-04-16","2026-04-17","2026-04-20","2026-04-21",
    "2026-04-22","2026-04-23","2026-04-24",
]

DATES_LIVE = [
    "2026-04-27","2026-04-28","2026-04-29","2026-04-30","2026-05-01",
]

ALL_DATES = DATES_BACKFILL + DATES_LIVE

TEMP_FILE = "_test_rddt_replacement2.json"


def run():
    import ex1

    temp_path = os.path.join(BASE_DIR, TEMP_FILE)
    with open(temp_path, "w") as f:
        json.dump([], f)

    orig_tickers = ex1.TICKERS[:]
    ex1.TICKERS = CANDIDATES

    print(f"\nTesting RDDT replacements (batch 2): {', '.join(CANDIDATES)}")
    print(f"Window: {ALL_DATES[0]} → {ALL_DATES[-1]}  ({len(ALL_DATES)} days)\n")
    print("(RDDT baseline: 14 trades, 21% WR, +$16.13 — mostly one $33.97 trade)")
    print("(Batch 1 best: HIMS 41% WR +$93.70 — closest but didn't clear 45% threshold)\n")

    try:
        for date in ALL_DATES:
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
    print(f"\n{'='*72}")
    print("  PER-TICKER BREAKDOWN  (43-day window)")
    print(f"{'='*72}")
    print(f"  {'Ticker':<8} {'Trades':>7} {'W':>5} {'L':>5} {'WR':>7} {'Total P&L':>11} {'Avg Win':>9} {'Avg Loss':>10}")
    print(f"  {'-'*8} {'-'*7} {'-'*5} {'-'*5} {'-'*7} {'-'*11} {'-'*9} {'-'*10}")

    ticker_results = {}
    for ticker in CANDIDATES:
        trades = [t for t in all_trades if t.get("ticker") == ticker]
        if not trades:
            print(f"  {ticker:<8} {'(no signals fired)':>50}")
            ticker_results[ticker] = None
            continue
        wins   = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]
        total  = sum(t.get("pnl", 0) for t in trades)
        wr     = len(wins) / len(trades) * 100 if trades else 0
        avg_w  = statistics.mean(t["pnl"] for t in wins)  if wins   else 0
        avg_l  = statistics.mean(t["pnl"] for t in losses) if losses else 0
        ticker_results[ticker] = {
            "trades": len(trades), "wins": len(wins), "wr": wr,
            "pnl": total, "avg_w": avg_w, "avg_l": avg_l,
        }
        print(f"  {ticker:<8} {len(trades):>7} {len(wins):>5} {len(losses):>5} "
              f"{wr:>6.0f}% {total:>+11.2f} {avg_w:>+9.2f} {avg_l:>+10.2f}")

    # ── Day-by-day table ─────────────────────────────────────────────────────
    col_w = 22
    header_tickers = "".join(f"{tk:>{col_w}}" for tk in CANDIDATES)
    print(f"\n{'='*72}")
    print("  DAY-BY-DAY  (per-ticker signals and P&L)")
    print(f"{'='*72}")
    print(f"  {'Date':<12}{header_tickers} {'Day Total':>11}")
    print(f"  {'-'*12}" + "".join(f"{'-'*col_w}" for _ in CANDIDATES) + f" {'-'*11}")

    cum = 0.0
    for day in days:
        day_trades = {}
        for t in day.get("trades", []):
            tk = t["ticker"]
            if tk not in day_trades:
                day_trades[tk] = t
        row_parts = []
        for ticker in CANDIDATES:
            t = day_trades.get(ticker)
            if t:
                cell = f"{t['rating']} {t['exit_reason'][:4]} {t['pnl']:+.2f}"
            else:
                cell = "—"
            row_parts.append(f"{cell:>{col_w}}")
        day_total = day.get("total_pnl", 0)
        cum += day_total
        print(f"  {day['date']:<12}{''.join(row_parts)} {day_total:>+11.2f}")

    sep = "  " + "─"*12 + "".join("─"*col_w for _ in CANDIDATES) + " " + "─"*11
    print(sep)
    print(f"  {'CUMULATIVE':<12}{''.join(' '*col_w for _ in CANDIDATES)} {cum:>+11.2f}")

    # ── Verdict ──────────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("  VERDICT  (threshold: ≥45% WR and positive P&L to qualify)")
    print(f"{'='*72}")
    for ticker, r in ticker_results.items():
        if r is None:
            print(f"  {ticker}: no signals fired — not a good ORB/GAP_GO candidate")
            continue
        flags = []
        if r["wr"] >= 55:
            flags.append("strong win rate")
        elif r["wr"] >= 45:
            flags.append("acceptable win rate")
        else:
            flags.append("low win rate")
        if r["pnl"] > 50:
            flags.append("solid P&L")
        elif r["pnl"] > 0:
            flags.append("marginal P&L")
        else:
            flags.append("negative P&L")
        if r["trades"] < 5:
            flags.append("too few trades — unreliable")
        verdict = "CANDIDATE" if r["wr"] >= 45 and r["pnl"] > 0 and r["trades"] >= 5 else "REJECT"
        print(f"  {ticker}: {verdict}  —  {r['trades']} trades, {r['wr']:.0f}% WR, ${r['pnl']:+.2f}  ({', '.join(flags)})")

    print()


if __name__ == "__main__":
    run()
