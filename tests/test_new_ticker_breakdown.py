"""
Per-ticker breakdown for the 7 new proposed tickers over the 15-day window.
Does not touch exercises.json.
"""
import json, os, statistics

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, BASE_DIR)

NEW_TICKERS = {"AXON", "SNOW", "PANW", "DDOG", "TTD", "ENPH", "CELH", "DUOL", "ELF", "CAVA", "W", "UPST", "SOUN", "IONQ", "ASTS", "ACHR", "WOLF", "FSLR", "QCOM", "NET"}

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

FOCUS = {"IONQ", "AXON", "NET", "WOLF", "ASTS"}

def collect_stats(dates, label):
    import ex1
    orig = ex1.TICKERS[:]
    ex1.TICKERS = PROPOSED

    result_file = f"_test_breakdown_{label}.json"
    with open(os.path.join(BASE_DIR, result_file), "w") as f:
        json.dump([], f)

    for date in dates:
        ex1.run_ex1(date, save=True, result_file=result_file)

    ex1.TICKERS = orig

    with open(os.path.join(BASE_DIR, result_file)) as f:
        data = json.load(f)
    os.remove(os.path.join(BASE_DIR, result_file))

    stats = {}
    for day in data:
        for t in day.get("trades", []):
            tk = t.get("ticker", "?")
            if tk not in stats:
                stats[tk] = {"wins": 0, "losses": 0, "pnl": 0.0, "exits": []}
            if t.get("pnl", 0) > 0:
                stats[tk]["wins"] += 1
            else:
                stats[tk]["losses"] += 1
            stats[tk]["pnl"] += t.get("pnl", 0)
            stats[tk]["exits"].append(t.get("exit_reason", "?"))
    return stats


def print_table(stats, label, focus):
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    print(f"  {'Ticker':<8} {'W':>4} {'L':>4} {'WR%':>6} {'P&L':>10}  Top exit")
    print(f"  {'─'*8} {'─'*4} {'─'*4} {'─'*6} {'─'*10}  {'─'*14}")
    rows = []
    for tk, s in sorted(stats.items(), key=lambda x: -x[1]["pnl"]):
        if tk not in focus:
            continue
        total = s["wins"] + s["losses"]
        wr = s["wins"] / total * 100 if total else 0
        top_exit = max(set(s["exits"]), key=s["exits"].count)
        marker = " ★" if wr >= 50 and s["pnl"] > 0 else ""
        print(f"  {tk:<8} {s['wins']:>4} {s['losses']:>4} {wr:>5.0f}%  ${s['pnl']:>+8.2f}  {top_exit}{marker}")


stats_15 = collect_stats(DATES_15, "15d")
print_table(stats_15, "15-day window (Apr 13 – May 1)", FOCUS)

stats_30 = collect_stats(DATES_30, "30d")
print_table(stats_30, "30-day window (Mar 19 – May 1)", FOCUS)
