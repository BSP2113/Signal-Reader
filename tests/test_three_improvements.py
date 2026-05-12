"""
test_three_improvements.py — Compare 3 improvements against EX1 baseline.

Option 1: No-progress exit — exit at T+90 if price is at or below entry
Option 2: Session stop gate — after the first stop loss of the day, cut
          remaining MAYBE allocations by 50%
Option 3: High-vol TAKE — treat vol_ratio >= 2.5x as TAKE instead of MAYBE,
          doubling allocation (NEUTRAL: TAKE=30% vs MAYBE=15% of wallet)
          Note: Option 3 is approximate — does not re-simulate budget constraints.

Usage: venv/bin/python3 test_three_improvements.py
"""

import json, os
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
T90_MINS    = 90
TAKE_VOL    = 2.5      # vol_ratio threshold to promote MAYBE → TAKE
ALLOC_RATIO = 2.0      # TAKE alloc / MAYBE alloc in NEUTRAL (30% / 15%)


def _load_creds():
    path = os.path.join(BASE_DIR, ".env")
    creds = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds["ALPACA_API_KEY"], creds["ALPACA_API_SECRET"]


def time_to_mins(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def mins_to_time(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def main():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        data = json.load(f)

    ex1_days = sorted(
        [e for e in data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    key, secret = _load_creds()
    client = StockHistoricalDataClient(api_key=key, secret_key=secret)

    totals = {"baseline": 0.0, "opt1": 0.0, "opt2": 0.0, "opt3": 0.0}
    day_results = []

    print("Fetching minute bars for Option 1 (no-progress exit)...")
    minute_prices = {}  # {date: {ticker: {time: close}}}

    for day in ex1_days:
        date    = day["date"]
        tickers = list({t["ticker"] for t in day["trades"]})
        start   = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end     = start + timedelta(days=1)
        try:
            resp = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=tickers, timeframe=TimeFrame.Minute,
                start=start, end=end, feed="iex"
            ))
            day_prices = {}
            for ticker in tickers:
                day_prices[ticker] = {}
                for bar in resp.data.get(ticker, []):
                    ts_et = bar.timestamp.astimezone(
                        __import__("zoneinfo").ZoneInfo("America/New_York")
                    )
                    day_prices[ticker][ts_et.strftime("%H:%M")] = bar.close
            minute_prices[date] = day_prices
            print(f"  {date}: {len(tickers)} tickers")
        except Exception as e:
            print(f"  {date}: fetch failed — {e}")
            minute_prices[date] = {}

    print("\n" + "="*90)
    print(f"{'Date':<12} {'Ticker':<6} {'Vol':>5} {'Rating':<5} {'OrigExit':<15} "
          f"{'Orig':>8}  {'O1':>8}  {'O2':>8}  {'O3':>8}")
    print("-" * 90)

    for day in ex1_days:
        date   = day["date"]
        trades = day["trades"]
        prices = minute_prices.get(date, {})

        # Option 2: track first stop loss of the day
        stop_fired = False
        day_totals = {"baseline": 0.0, "opt1": 0.0, "opt2": 0.0, "opt3": 0.0}

        for t in trades:
            ticker      = t["ticker"]
            entry_price = t["entry"]
            entry_time  = t["time"]
            exit_time   = t["exit_time"]
            exit_price  = t["exit"]
            exit_reason = t["exit_reason"]
            allocated   = t.get("allocated", abs(t["pnl"] / (t["pnl_pct"] / 100)) if t["pnl_pct"] else 0)
            vol_ratio   = t["vol_ratio"]
            orig_pnl    = t["pnl"]
            rating      = t["rating"]

            entry_mins = time_to_mins(entry_time)
            exit_mins  = time_to_mins(exit_time)
            t90_mins   = entry_mins + T90_MINS
            t90_time   = mins_to_time(t90_mins)

            # --- Baseline ---
            totals["baseline"]      += orig_pnl
            day_totals["baseline"]  += orig_pnl

            # --- Option 1: No-progress exit at T+90 ---
            o1_pnl = orig_pnl
            if exit_mins > t90_mins and t90_mins <= time_to_mins("14:00"):
                t90_price = None
                for offset in range(6):
                    cand = mins_to_time(t90_mins + offset)
                    if cand in prices.get(ticker, {}):
                        t90_price = prices[ticker][cand]
                        break
                if t90_price is not None and t90_price <= entry_price:
                    o1_pnl = round((t90_price - entry_price) / entry_price * allocated, 2)
            totals["opt1"]     += o1_pnl
            day_totals["opt1"] += o1_pnl

            # --- Option 2: Session stop gate ---
            o2_pnl = orig_pnl
            if stop_fired and rating == "MAYBE":
                o2_pnl = round(orig_pnl * 0.5, 2)
            if exit_reason == "STOP_LOSS":
                stop_fired = True
            totals["opt2"]     += o2_pnl
            day_totals["opt2"] += o2_pnl

            # --- Option 3: High-vol TAKE promotion ---
            o3_pnl = orig_pnl
            if rating == "MAYBE" and vol_ratio >= TAKE_VOL:
                o3_pnl = round(orig_pnl * ALLOC_RATIO, 2)
            totals["opt3"]     += o3_pnl
            day_totals["opt3"] += o3_pnl

            diff1 = o1_pnl - orig_pnl
            diff2 = o2_pnl - orig_pnl
            diff3 = o3_pnl - orig_pnl
            flag1 = f"{o1_pnl:>+8.2f}" if diff1 != 0 else f"{'(same)':>8}"
            flag2 = f"{o2_pnl:>+8.2f}" if diff2 != 0 else f"{'(same)':>8}"
            flag3 = f"{o3_pnl:>+8.2f}" if diff3 != 0 else f"{'(same)':>8}"

            print(f"{date:<12} {ticker:<6} {vol_ratio:>4.1f}x {rating:<5} {exit_reason:<15} "
                  f"{orig_pnl:>+8.2f}  {flag1}  {flag2}  {flag3}")

        d0 = day_totals['baseline']
        d1 = day_totals['opt1']
        d2 = day_totals['opt2']
        d3 = day_totals['opt3']
        print(f"  {'DAY TOTAL':<60} {d0:>+8.2f}  {d1:>+8.2f}  {d2:>+8.2f}  {d3:>+8.2f}")
        print()
        day_results.append({"date": date, **day_totals})

    # Summary
    print("=" * 90)
    print("SUMMARY (11 EX1 days)")
    print(f"  Baseline:                       ${totals['baseline']:>+8.2f}")

    labels = [
        ("opt1", "No-progress exit (T+90)"),
        ("opt2", "Session stop gate (50% cut after 1st SL)"),
        ("opt3", f"High-vol TAKE (>={TAKE_VOL}x → TAKE, ~2x alloc)  [approx]"),
    ]
    for key, label in labels:
        net = round(totals[key] - totals["baseline"], 2)
        print(f"\n  {label}")
        print(f"    Total P&L: ${totals[key]:>+8.2f}  (net {net:>+.2f} vs baseline)")

    print("\n  Day-by-day net vs baseline:")
    header = f"  {'Date':<12} {'Base':>8}  {'O1 Δ':>8}  {'O2 Δ':>8}  {'O3 Δ':>8}"
    print(header)
    for dr in day_results:
        d = dr["date"]
        b = dr["baseline"]
        print(f"  {d:<12} {b:>+8.2f}  "
              f"{dr['opt1']-b:>+8.2f}  "
              f"{dr['opt2']-b:>+8.2f}  "
              f"{dr['opt3']-b:>+8.2f}")


if __name__ == "__main__":
    main()
