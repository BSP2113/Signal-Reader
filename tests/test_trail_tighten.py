"""
test_trail_tighten.py — Backtest tightening TRAIL_PCT from 2.5% to 1.5% and 2.0%.

Current parameters:
  TRAIL_LOCK = 1.0%  — trailing stop activates after price rises 1% from entry
  TRAIL_PCT  = 2.5%  — trails 2.5% below the peak once active

Tested:
  TRAIL_PCT = 2.0%  (tighter)
  TRAIL_PCT = 1.5%  (tightest)

All other exits unchanged: TAKE_PROFIT=3%, STOP_LOSS=1.5%, TIME_CLOSE=14:00.

Uses actual 1-minute OHLC bars from Alpaca to re-simulate each trade.
High of each bar used to update peak / check take profit.
Low of each bar used to check stop loss and trailing stop trigger.

Usage: venv/bin/python3 test_trail_tighten.py
"""

import json, os
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
TRAIL_LOCK = 0.01
TAKE_PROFIT = 0.03
STOP_LOSS   = -0.015
TIME_CLOSE  = "14:00"

TRAIL_VARIANTS = [0.025, 0.020, 0.015]


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


def simulate_trade(bars, entry_price, entry_time, units, trail_pct):
    """
    Walk 1-minute bars from entry_time forward and return (exit_price, exit_reason).
    bars: list of dicts with keys: time (HH:MM), open, high, low, close
    """
    entry_mins = time_to_mins(entry_time)
    tc_mins    = time_to_mins(TIME_CLOSE)
    peak       = entry_price
    trail_active = False

    for bar in bars:
        bar_mins = time_to_mins(bar["time"])
        if bar_mins < entry_mins:
            continue

        h = bar["high"]
        l = bar["low"]

        # Update peak and check take profit using high
        if h > peak:
            peak = h
        if peak >= entry_price * (1 + TRAIL_LOCK):
            trail_active = True
        if h >= entry_price * (1 + TAKE_PROFIT):
            return entry_price * (1 + TAKE_PROFIT), "TAKE_PROFIT"

        # Check stops using low
        trail_trigger = peak * (1 - trail_pct) if trail_active else None
        if trail_trigger is not None and l <= trail_trigger:
            return trail_trigger, "TRAILING_STOP"
        if l <= entry_price * (1 + STOP_LOSS):
            return entry_price * (1 + STOP_LOSS), "STOP_LOSS"

        # Time close
        if bar_mins >= tc_mins:
            return bar["close"], "TIME_CLOSE"

    # Ran out of bars
    return bars[-1]["close"] if bars else entry_price, "TIME_CLOSE"


def main():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        data = json.load(f)

    ex1_days = sorted(
        [e for e in data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    key, secret = _load_creds()
    client = StockHistoricalDataClient(api_key=key, secret_key=secret)

    # Results per variant: {trail_pct: total_pnl}
    totals = {v: 0.0 for v in TRAIL_VARIANTS}
    original_total = 0.0
    all_changes = {v: [] for v in TRAIL_VARIANTS if v != 0.025}

    for day in ex1_days:
        date   = day["date"]
        trades = day["trades"]
        tickers = list({t["ticker"] for t in trades})

        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt   = start_dt + timedelta(days=1)

        print(f"\n--- {date} ---")

        try:
            bars_resp = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=tickers, timeframe=TimeFrame.Minute,
                start=start_dt, end=end_dt, feed="iex",
            ))
        except Exception as e:
            print(f"  Data fetch failed: {e}")
            continue

        # Build sorted bar list per ticker
        minute_bars = {}
        for ticker in tickers:
            raw = bars_resp.data.get(ticker, [])
            bar_list = []
            for bar in raw:
                ts_et = bar.timestamp.astimezone(
                    __import__("zoneinfo").ZoneInfo("America/New_York")
                )
                t_str = ts_et.strftime("%H:%M")
                if "09:30" <= t_str <= "16:00":
                    bar_list.append({
                        "time":  t_str,
                        "open":  bar.open,
                        "high":  bar.high,
                        "low":   bar.low,
                        "close": bar.close,
                    })
            minute_bars[ticker] = sorted(bar_list, key=lambda b: b["time"])

        for t in trades:
            ticker      = t["ticker"]
            entry_price = t["entry"]
            entry_time  = t["time"]
            units       = t["units"]
            orig_pnl    = t["pnl"]
            orig_reason = t["exit_reason"]

            original_total += orig_pnl
            bars = minute_bars.get(ticker, [])

            if not bars:
                for v in TRAIL_VARIANTS:
                    totals[v] += orig_pnl
                print(f"  {ticker:6} {entry_time}  no bars — keeping original")
                continue

            results = {}
            for trail_pct in TRAIL_VARIANTS:
                exit_px, exit_reason = simulate_trade(bars, entry_price, entry_time, units, trail_pct)
                sim_pnl = round((exit_px - entry_price) * units, 2)
                results[trail_pct] = (sim_pnl, exit_reason)
                totals[trail_pct] += sim_pnl

            base_pnl, base_reason = results[0.025]
            label_parts = [f"orig={orig_pnl:+.2f}({orig_reason[:4]})  sim2.5%={base_pnl:+.2f}({base_reason[:4]})"]
            for v in [0.020, 0.015]:
                p, r = results[v]
                diff = round(p - base_pnl, 2)
                label_parts.append(f"{int(v*100)}%={p:+.2f}({r[:4]},Δ{diff:+.2f})")
                if r != base_reason or abs(diff) > 0.01:
                    all_changes[v].append({
                        "date": date, "ticker": ticker,
                        "orig_pnl": orig_pnl, "orig_reason": orig_reason,
                        "new_pnl": p, "new_reason": r, "diff": round(p - orig_pnl, 2),
                    })
            print(f"  {ticker:6} {entry_time}  {'  '.join(label_parts)}")

    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"  Original total P&L:          ${original_total:>8.2f}")
    for v in TRAIL_VARIANTS:
        net = round(totals[v] - original_total, 2)
        label = f"TRAIL_PCT = {int(v*100)}%"
        print(f"  Simulated {label}:   ${totals[v]:>8.2f}   net {net:>+.2f} vs original")

    print()
    for v in [0.020, 0.015]:
        changes = all_changes[v]
        helped  = [c for c in changes if c["diff"] > 0]
        hurt    = [c for c in changes if c["diff"] < 0]
        print(f"\n  TRAIL_PCT {int(v*100)}% — {len(changes)} trades changed: "
              f"{len(helped)} better, {len(hurt)} worse")
        if hurt:
            print(f"  Trades hurt (trail cut a position that would have recovered):")
            for c in hurt:
                print(f"    {c['date']} {c['ticker']:6}  "
                      f"orig={c['orig_pnl']:+.2f}({c['orig_reason'][:4]})  "
                      f"new={c['new_pnl']:+.2f}({c['new_reason'][:4]})  diff={c['diff']:+.2f}")


if __name__ == "__main__":
    main()
