"""
Backtest proposed 19-ticker list vs current baseline for 15-day and 30-day windows.
Does NOT modify exercises.json. Results written to temp files, deleted after.
"""
import json, os, sys, statistics

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, BASE_DIR)

PROPOSED = [
    "AXON", "SNOW", "PANW", "DDOG", "TTD", "ENPH",
    "CELH", "DUOL", "ELF", "CAVA", "W", "UPST",
    "SOUN", "IONQ", "ASTS", "ACHR", "WOLF", "FSLR", "QCOM", "NET",
]

DATES_15 = [
    "2026-04-13","2026-04-14","2026-04-15","2026-04-16","2026-04-17",
    "2026-04-20","2026-04-21","2026-04-22","2026-04-23","2026-04-24",
    "2026-04-27","2026-04-28","2026-04-29","2026-04-30","2026-05-01",
]

DATES_30 = [
    "2026-03-19","2026-03-20","2026-03-23","2026-03-24","2026-03-25",
    "2026-03-26","2026-03-27","2026-03-31","2026-04-01","2026-04-02",
    "2026-04-06","2026-04-07","2026-04-08","2026-04-09","2026-04-10",
    "2026-04-13","2026-04-14","2026-04-15","2026-04-16","2026-04-17",
    "2026-04-20","2026-04-21","2026-04-22","2026-04-23","2026-04-24",
    "2026-04-27","2026-04-28","2026-04-29","2026-04-30","2026-05-01",
]


def run_test(dates, label):
    import ex1, ex2

    ex1_file = os.path.join(BASE_DIR, f"_test_ex1_{label}.json")
    ex2_file = os.path.join(BASE_DIR, f"_test_ex2_{label}.json")

    for f in [ex1_file, ex2_file]:
        with open(f, "w") as fp:
            json.dump([], fp)

    # Patch tickers
    orig_ex1 = ex1.TICKERS[:]
    orig_ex2 = ex2.TICKERS[:]
    ex1.TICKERS = PROPOSED
    ex2.TICKERS = PROPOSED

    print(f"\n{'='*60}")
    print(f"PROPOSED 19 — {label} ({len(dates)} days)")
    print(f"{'='*60}")

    try:
        for date in dates:
            r1 = ex1.run_ex1(date, save=True, result_file=os.path.basename(ex1_file))
            r2 = ex2.run_ex2(date, result_file=os.path.basename(ex2_file))
    finally:
        ex1.TICKERS = orig_ex1
        ex2.TICKERS = orig_ex2

    results = {}
    for tag, fpath, title_substr in [("EX1", ex1_file, "Exercise 1"), ("EX2", ex2_file, "Exercise 2")]:
        with open(fpath) as fp:
            data = json.load(fp)
        days = [d for d in data if title_substr in d.get("title", "")]
        all_trades = []
        for day in days:
            all_trades.extend(day.get("trades", []))
        wins = [t for t in all_trades if t.get("pnl", 0) > 0]
        losses = [t for t in all_trades if t.get("pnl", 0) <= 0]
        total_pnl = sum(t.get("pnl", 0) for t in all_trades)
        wr = len(wins) / len(all_trades) * 100 if all_trades else 0
        start = days[0]["starting_capital"] if days else 5000
        end   = days[-1]["portfolio_eod"] if days else 5000
        avg_win  = statistics.mean(t["pnl"] for t in wins)  if wins   else 0
        avg_loss = statistics.mean(t["pnl"] for t in losses) if losses else 0
        results[tag] = {
            "trades": len(all_trades), "wins": len(wins), "losses": len(losses),
            "wr": wr, "pnl": total_pnl, "start": start, "end": end,
            "avg_win": avg_win, "avg_loss": avg_loss,
        }

    os.remove(ex1_file)
    os.remove(ex2_file)
    return results


def baseline_stats(title_substr, dates):
    """Pull stats from current exercises.json + backfill.json for the given dates."""
    all_data = []
    for fname in ["exercises.json", "backfill.json", "backfill2.json"]:
        path = os.path.join(BASE_DIR, fname)
        if os.path.exists(path):
            with open(path) as f:
                all_data.extend(json.load(f))

    days = [d for d in all_data if title_substr in d.get("title","") and d["date"] in dates]
    days.sort(key=lambda d: d["date"])
    all_trades = []
    for day in days:
        all_trades.extend(day.get("trades", []))
    if not all_trades:
        return None
    wins   = [t for t in all_trades if t.get("pnl", 0) > 0]
    losses = [t for t in all_trades if t.get("pnl", 0) <= 0]
    total_pnl = sum(t.get("pnl", 0) for t in all_trades)
    wr = len(wins) / len(all_trades) * 100 if all_trades else 0
    start = days[0]["starting_capital"] if days else 5000
    end   = days[-1]["portfolio_eod"] if days else 5000
    avg_win  = statistics.mean(t["pnl"] for t in wins)  if wins   else 0
    avg_loss = statistics.mean(t["pnl"] for t in losses) if losses else 0
    return {
        "trades": len(all_trades), "wins": len(wins), "losses": len(losses),
        "wr": wr, "pnl": total_pnl, "start": start, "end": end,
        "avg_win": avg_win, "avg_loss": avg_loss,
    }


def print_comparison(label, dates, proposed):
    print(f"\n{'─'*60}")
    print(f"  COMPARISON — {label}")
    print(f"{'─'*60}")
    print(f"  {'Metric':<22} {'Baseline EX1':>14} {'Proposed EX1':>14}  {'Baseline EX2':>14} {'Proposed EX2':>14}")
    print(f"  {'-'*22} {'-'*14} {'-'*14}  {'-'*14} {'-'*14}")

    b1 = baseline_stats("Exercise 1", set(dates))
    b2 = baseline_stats("Exercise 2", set(dates))
    p1 = proposed.get("EX1", {})
    p2 = proposed.get("EX2", {})

    def fmt(val, fmt_str):
        return fmt_str.format(val) if val is not None else "  n/a"

    rows = [
        ("Trades",      b1["trades"] if b1 else 0, p1.get("trades",0), b2["trades"] if b2 else 0, p2.get("trades",0), "{:>14}"),
        ("Win rate",    f"{b1['wr']:.0f}%" if b1 else "-", f"{p1['wr']:.0f}%" if p1 else "-", f"{b2['wr']:.0f}%" if b2 else "-", f"{p2['wr']:.0f}%" if p2 else "-", "{:>14}"),
        ("Total P&L",   f"${b1['pnl']:+.2f}" if b1 else "-", f"${p1['pnl']:+.2f}" if p1 else "-", f"${b2['pnl']:+.2f}" if b2 else "-", f"${p2['pnl']:+.2f}" if p2 else "-", "{:>14}"),
        ("Portfolio",   f"${b1['end']:.2f}" if b1 else "-", f"${p1['end']:.2f}" if p1 else "-", f"${b2['end']:.2f}" if b2 else "-", f"${p2['end']:.2f}" if p2 else "-", "{:>14}"),
        ("Avg win",     f"${b1['avg_win']:+.2f}" if b1 else "-", f"${p1['avg_win']:+.2f}" if p1 else "-", f"${b2['avg_win']:+.2f}" if b2 else "-", f"${p2['avg_win']:+.2f}" if p2 else "-", "{:>14}"),
        ("Avg loss",    f"${b1['avg_loss']:+.2f}" if b1 else "-", f"${p1['avg_loss']:+.2f}" if p1 else "-", f"${b2['avg_loss']:+.2f}" if b2 else "-", f"${p2['avg_loss']:+.2f}" if p2 else "-", "{:>14}"),
    ]
    for row in rows:
        name, bv1, pv1, bv2, pv2, _ = row
        print(f"  {name:<22} {str(bv1):>14} {str(pv1):>14}  {str(bv2):>14} {str(pv2):>14}")


if __name__ == "__main__":
    print("Running proposed 19-ticker backtest...")
    print(f"Tickers: {', '.join(PROPOSED)}")

    results_15 = run_test(DATES_15, "15d")
    print_comparison("15-day window (Apr 13 – May 1)", DATES_15, results_15)

    results_30 = run_test(DATES_30, "30d")
    print_comparison("30-day window (Mar 19 – May 1)", DATES_30, results_30)
