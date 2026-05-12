"""
test_time_of_day.py — analyze win rate and P&L by entry time window

Breaks all ORB trades from backfill.json into 15-minute buckets and shows
win rate, average P&L, and total P&L for each window. If a clear winner or
loser emerges, we can test whether filtering to/from that window helps.

Run: venv/bin/python3 test_time_of_day.py
"""

import json
import os
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bucket(time_str):
    h, m = map(int, time_str.split(":"))
    minutes = h * 60 + m
    if minutes < 10 * 60:          return "09:45–10:00"
    if minutes < 10 * 60 + 15:     return "10:00–10:15"
    if minutes < 10 * 60 + 30:     return "10:15–10:30"
    if minutes < 10 * 60 + 45:     return "10:30–10:45"
    if minutes < 11 * 60:          return "10:45–11:00"
    if minutes < 11 * 60 + 15:     return "11:00–11:15"
    return                                 "11:15–11:30"


def run():
    with open(os.path.join(BASE_DIR, "backfill.json")) as f:
        backfill = json.load(f)

    trades = [
        t
        for e in backfill if "Exercise 1" in e["title"]
        for t in e["trades"]
    ]

    print(f"Total trades across backfill: {len(trades)}\n")

    # --- By entry time bucket ---
    by_bucket = defaultdict(list)
    for t in trades:
        by_bucket[bucket(t["time"])].append(t)

    order = [
        "09:45–10:00", "10:00–10:15", "10:15–10:30",
        "10:30–10:45", "10:45–11:00", "11:00–11:15", "11:15–11:30",
    ]

    print(f"  {'Window':<16} {'Trades':>6} {'Wins':>5} {'Win%':>6} {'Avg P&L':>9} {'Total P&L':>10}")
    print(f"  {'-'*57}")
    for b in order:
        ts = by_bucket.get(b, [])
        if not ts:
            continue
        wins  = sum(1 for t in ts if t["pnl"] > 0)
        total = sum(t["pnl"] for t in ts)
        avg   = total / len(ts)
        wr    = wins / len(ts) * 100
        flag  = " <" if wr < 40 or avg < -5 else ("  *" if wr >= 60 and avg > 5 else "")
        print(f"  {b:<16} {len(ts):>6} {wins:>5} {wr:>5.0f}%  {avg:>+8.2f}  {total:>+9.2f}{flag}")

    print(f"  {'-'*57}")
    wins_all  = sum(1 for t in trades if t["pnl"] > 0)
    total_all = sum(t["pnl"] for t in trades)
    print(f"  {'ALL':<16} {len(trades):>6} {wins_all:>5} {wins_all/len(trades)*100:>5.0f}%  "
          f"{total_all/len(trades):>+8.2f}  {total_all:>+9.2f}")

    # --- By exit reason ---
    print(f"\n  Exit reason breakdown:")
    by_exit = defaultdict(list)
    for t in trades:
        by_exit[t["exit_reason"]].append(t)
    for reason, ts in sorted(by_exit.items()):
        wins  = sum(1 for t in ts if t["pnl"] > 0)
        total = sum(t["pnl"] for t in ts)
        print(f"    {reason:<16} {len(ts):>4} trades  {wins/len(ts)*100:>5.0f}% win  avg {total/len(ts):>+7.2f}  total {total:>+8.2f}")

    # --- By rating ---
    print(f"\n  Signal rating breakdown:")
    by_rating = defaultdict(list)
    for t in trades:
        by_rating[t["rating"]].append(t)
    for rating, ts in sorted(by_rating.items()):
        wins  = sum(1 for t in ts if t["pnl"] > 0)
        total = sum(t["pnl"] for t in ts)
        print(f"    {rating:<8} {len(ts):>4} trades  {wins/len(ts)*100:>5.0f}% win  avg {total/len(ts):>+7.2f}  total {total:>+8.2f}")

    # --- Flag any window worth testing as a filter ---
    print()
    weak = [b for b in order if by_bucket.get(b) and
            sum(t["pnl"] for t in by_bucket[b]) / len(by_bucket[b]) < -2
            and len(by_bucket[b]) >= 3]
    strong = [b for b in order if by_bucket.get(b) and
              sum(1 for t in by_bucket[b] if t["pnl"] > 0) / len(by_bucket[b]) >= 0.60
              and len(by_bucket[b]) >= 3]
    if weak:
        print(f"  Weak windows (avg P&L < -$2, 3+ trades): {', '.join(weak)}")
        print(f"  → Worth testing a skip rule for these windows")
    if strong:
        print(f"  Strong windows (win rate ≥60%, 3+ trades): {', '.join(strong)}")
        print(f"  → These are the high-value entry slots")
    if not weak and not strong:
        print(f"  No window stands out strongly enough to filter on yet.")
    print()


if __name__ == "__main__":
    run()
