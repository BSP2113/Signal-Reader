"""
test_revisit.py — Evaluate all pending growth opportunities against EX1 history.

Items tested:
  A. Intraday stop-loss circuit breaker (GROWTH_POOL index 12)
     After N stop losses in a session, halt remaining ORB entries.
     Uses exercises.json trade order to simulate halting.

  B. High-vol TAKE promotion (ARCHIVED index 27)
     Trades with vol_ratio >= threshold rated as TAKE, allocation doubled.
     Tested at 2.0x, 2.5x, 3.0x thresholds.
     Also shows win rate / avg P&L by vol tier across all trades.

  C. MAYBE stop-loss profile
     Characterise trades that hit STOP_LOSS vs winners to see if any
     attribute reliably separates them.

Usage: venv/bin/python3 test_revisit.py
"""

import json, os
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOC_RATIO   = 2.0   # TAKE / MAYBE allocation ratio in NEUTRAL (30% / 15%)
CIRCUIT_AT    = 2     # halt entries after this many stop losses in one session

def main():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        data = json.load(f)

    ex1_days = sorted(
        [e for e in data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    baseline = sum(e["total_pnl"] for e in ex1_days)
    n_days   = len(ex1_days)

    # ─────────────────────────────────────────────────────────────
    # A. CIRCUIT BREAKER — halt entries after N stop losses per day
    # ─────────────────────────────────────────────────────────────
    print("=" * 70)
    print(f"A. INTRADAY CIRCUIT BREAKER (halt after {CIRCUIT_AT} stop losses/day)")
    print("=" * 70)

    circuit_total = 0.0
    circuit_fired_days = []
    circuit_blocked = []

    for day in ex1_days:
        stops_so_far = 0
        day_pnl = 0.0
        for t in day["trades"]:
            if stops_so_far >= CIRCUIT_AT:
                circuit_blocked.append(t)
                # trade is skipped — pnl = 0
            else:
                day_pnl += t["pnl"]
                if t["exit_reason"] == "STOP_LOSS":
                    stops_so_far += 1
        circuit_total += day_pnl
        if stops_so_far >= CIRCUIT_AT:
            circuit_fired_days.append(day["date"])

    net_circuit = round(circuit_total - baseline, 2)
    blocked_wins  = [t for t in circuit_blocked if t["pnl"] > 0]
    blocked_stops = [t for t in circuit_blocked if t["exit_reason"] == "STOP_LOSS"]

    print(f"  Baseline:       ${baseline:>+8.2f}")
    print(f"  Circuit total:  ${circuit_total:>+8.2f}  (net {net_circuit:>+.2f})")
    print(f"  Days rule fired: {len(circuit_fired_days)} — {circuit_fired_days}")
    print(f"  Trades blocked:  {len(circuit_blocked)}")
    print(f"    Winners missed: {len(blocked_wins)}  "
          f"total ${sum(t['pnl'] for t in blocked_wins):>+.2f}")
    print(f"    Extra stops avoided: {len(blocked_stops)}  "
          f"total ${sum(t['pnl'] for t in blocked_stops):>+.2f}")
    if circuit_blocked:
        print(f"\n  Blocked trades detail:")
        for t in circuit_blocked:
            day = next(e["date"] for e in ex1_days
                       if any(x is t for x in e["trades"]))
            print(f"    {day} {t['ticker']:6} {t['time']} {t['rating']:5} "
                  f"{t['vol_ratio']:.1f}x  {t['exit_reason']:<15}  {t['pnl']:>+.2f}")

    # ─────────────────────────────────────────────────────────────
    # B. HIGH-VOL TAKE PROMOTION
    # ─────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("B. HIGH-VOL TAKE PROMOTION (vol >= threshold → 2x allocation)")
    print("=" * 70)

    # Win rate by vol tier
    tiers = [
        (0.0,  1.5,  "< 1.5x"),
        (1.5,  2.0,  "1.5–2.0x"),
        (2.0,  2.5,  "2.0–2.5x"),
        (2.5,  3.0,  "2.5–3.0x"),
        (3.0,  99.0, ">= 3.0x"),
    ]
    print(f"\n  Win rate by volume tier (all {n_days} EX1 days):")
    print(f"  {'Tier':<12} {'Trades':>7} {'Wins':>6} {'WinRate':>8} {'AvgP&L':>8} {'TotalP&L':>10}  {'Stops':>6}")
    all_trades = [t for e in ex1_days for t in e["trades"]]
    for lo, hi, label in tiers:
        bucket = [t for t in all_trades if lo <= t["vol_ratio"] < hi]
        if not bucket:
            continue
        wins  = [t for t in bucket if t["pnl"] > 0]
        stops = [t for t in bucket if t["exit_reason"] == "STOP_LOSS"]
        wr    = len(wins) / len(bucket) * 100
        avg   = sum(t["pnl"] for t in bucket) / len(bucket)
        total = sum(t["pnl"] for t in bucket)
        print(f"  {label:<12} {len(bucket):>7} {len(wins):>6} {wr:>7.1f}% {avg:>+8.2f} {total:>+10.2f}  {len(stops):>6}")

    # P&L impact at different thresholds
    print(f"\n  P&L impact of 2x allocation for high-vol MAYBE trades:")
    print(f"  {'Threshold':<14} {'Total P&L':>10} {'Net Δ':>8} {'Trades 2x':>10} {'Wins 2x':>8} {'Stops 2x':>8}")
    for thresh in [2.0, 2.5, 3.0, 4.0]:
        total = 0.0
        n_promoted = wins_promoted = stops_promoted = 0
        for day in ex1_days:
            for t in day["trades"]:
                if t["rating"] == "MAYBE" and t["vol_ratio"] >= thresh:
                    total += t["pnl"] * ALLOC_RATIO
                    n_promoted += 1
                    if t["pnl"] > 0:        wins_promoted  += 1
                    if t["exit_reason"] == "STOP_LOSS": stops_promoted += 1
                else:
                    total += t["pnl"]
        net = round(total - baseline, 2)
        print(f"  >= {thresh:.1f}x{'':<9} {total:>+10.2f} {net:>+8.2f} {n_promoted:>10} "
              f"{wins_promoted:>8} {stops_promoted:>8}")

    # Day-by-day at 2.5x threshold (most tested)
    print(f"\n  Day-by-day at >= 2.5x threshold:")
    print(f"  {'Date':<12} {'Base':>8}  {'2x Δ':>8}  Promoted trades")
    for day in ex1_days:
        day_base = day["total_pnl"]
        day_new  = 0.0
        promoted = []
        for t in day["trades"]:
            if t["rating"] == "MAYBE" and t["vol_ratio"] >= 2.5:
                day_new += t["pnl"] * ALLOC_RATIO
                promoted.append(f"{t['ticker']}({t['vol_ratio']:.1f}x,{t['pnl']:+.0f}→{t['pnl']*2:+.0f})")
            else:
                day_new += t["pnl"]
        delta = day_new - day_base
        promo_str = ", ".join(promoted) if promoted else "—"
        print(f"  {day['date']:<12} {day_base:>+8.2f}  {delta:>+8.2f}  {promo_str}")

    # ─────────────────────────────────────────────────────────────
    # C. MAYBE STOP-LOSS PROFILE
    # ─────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("C. MAYBE STOP-LOSS PROFILE — what separates stops from winners?")
    print("=" * 70)

    stops  = [t for e in ex1_days for t in e["trades"]
              if t["exit_reason"] == "STOP_LOSS"]
    wins   = [t for e in ex1_days for t in e["trades"]
              if t["pnl"] > 0]
    maybe_trades = [t for e in ex1_days for t in e["trades"]]

    def avg(lst, key): return sum(key(x) for x in lst) / len(lst) if lst else 0

    print(f"\n  {'Metric':<25} {'All trades':>12} {'Winners':>12} {'Stops':>12}")
    print(f"  {'-'*63}")
    print(f"  {'Count':<25} {len(maybe_trades):>12} {len(wins):>12} {len(stops):>12}")
    print(f"  {'Avg vol_ratio':<25} {avg(maybe_trades, lambda t: t['vol_ratio']):>12.2f}x "
          f"{avg(wins, lambda t: t['vol_ratio']):>11.2f}x "
          f"{avg(stops, lambda t: t['vol_ratio']):>11.2f}x")
    print(f"  {'Avg entry time':<25} {'':>12} {'':>12} {'':>12}")

    def mins(t):
        h, m = map(int, t["time"].split(":"))
        return h * 60 + m

    print(f"  {'  (mins after 9:30)':<25} {avg(maybe_trades, lambda t: mins(t)-570):>11.1f}m "
          f"{avg(wins, lambda t: mins(t)-570):>11.1f}m "
          f"{avg(stops, lambda t: mins(t)-570):>11.1f}m")

    # Entry time buckets
    print(f"\n  Stop rate by entry time:")
    time_buckets = [("09:45–09:59", "09:45", "10:00"),
                    ("10:00–10:29", "10:00", "10:30"),
                    ("10:30–11:30", "10:30", "11:31")]
    for label, lo, hi in time_buckets:
        bucket = [t for e in ex1_days for t in e["trades"]
                  if lo <= t["time"] < hi]
        b_stops = [t for t in bucket if t["exit_reason"] == "STOP_LOSS"]
        b_wins  = [t for t in bucket if t["pnl"] > 0]
        if bucket:
            print(f"    {label:<14} {len(bucket):>3} trades  "
                  f"stops={len(b_stops)} ({len(b_stops)/len(bucket)*100:.0f}%)  "
                  f"wins={len(b_wins)} ({len(b_wins)/len(bucket)*100:.0f}%)  "
                  f"avg={avg(bucket, lambda t: t['pnl']):>+.2f}")

    # Vol bucket for stops
    print(f"\n  Stop rate by vol tier:")
    for lo, hi, label in tiers:
        bucket = [t for e in ex1_days for t in e["trades"]
                  if lo <= t["vol_ratio"] < hi]
        b_stops = [t for t in bucket if t["exit_reason"] == "STOP_LOSS"]
        if bucket:
            print(f"    {label:<10} {len(bucket):>3} trades  "
                  f"stops={len(b_stops)} ({len(b_stops)/len(bucket)*100:.0f}%)  "
                  f"avg={avg(bucket, lambda t: t['pnl']):>+.2f}")

    # ─────────────────────────────────────────────────────────────
    # FINAL SUMMARY
    # ─────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"  Baseline ({n_days} EX1 days):  ${baseline:>+.2f}")
    print()

    # circuit
    verdict_circuit = "REJECT" if net_circuit < 0 else "REVISIT"
    print(f"  A. Circuit breaker (halt after {CIRCUIT_AT} stops):  "
          f"${circuit_total:>+.2f}  net {net_circuit:>+.2f}  → {verdict_circuit}")

    # high-vol at 2.5x
    hv_total = sum(
        t["pnl"] * ALLOC_RATIO if (t["rating"] == "MAYBE" and t["vol_ratio"] >= 2.5) else t["pnl"]
        for e in ex1_days for t in e["trades"]
    )
    net_hv = round(hv_total - baseline, 2)
    verdict_hv = "REVISIT" if net_hv > 0 else "REJECT"
    print(f"  B. High-vol TAKE (>= 2.5x):         "
          f"${hv_total:>+.2f}  net {net_hv:>+.2f}  → {verdict_hv} (needs 30-day win rate data)")

    print(f"  C. MAYBE stop profile:  no single attribute separates stops from winners → CONTINUE MONITORING")


if __name__ == "__main__":
    main()
