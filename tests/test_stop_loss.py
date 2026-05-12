"""
test_stop_loss.py — Backtest widening STOP_LOSS from 1.5% to 1.75% and 2.0%.

Current parameters:
  STOP_LOSS  = 1.5%  — exit when price drops 1.5% from entry
  TRAIL_STOP = 2.0%  — (already updated)
  TRAIL_LOCK = 1.0%  — trailing stop activates after +1% gain

Tested:
  STOP_LOSS = 1.75%
  STOP_LOSS = 2.0%

All other exits unchanged: TAKE_PROFIT=3%, TRAIL_STOP=2.0%, TIME_CLOSE=14:00.

Uses actual 1-minute OHLC bars from Alpaca.
High of each bar used to update peak / check take profit.
Low of each bar used to check stop loss and trailing stop.

Usage: venv/bin/python3 test_stop_loss.py
"""

import json, os
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
TRAIL_LOCK  = 0.01
TRAIL_STOP  = 0.020
TAKE_PROFIT = 0.03
TIME_CLOSE  = "14:00"

STOP_VARIANTS = [0.015, 0.0175, 0.020]


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


def simulate_trade(bars, entry_price, entry_time, units, stop_loss_pct):
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

        if h > peak:
            peak = h
        if peak >= entry_price * (1 + TRAIL_LOCK):
            trail_active = True
        if h >= entry_price * (1 + TAKE_PROFIT):
            return entry_price * (1 + TAKE_PROFIT), "TAKE_PROFIT"

        trail_trigger = peak * (1 - TRAIL_STOP) if trail_active else None
        if trail_trigger is not None and l <= trail_trigger:
            return trail_trigger, "TRAILING_STOP"
        if l <= entry_price * (1 - stop_loss_pct):
            return entry_price * (1 - stop_loss_pct), "STOP_LOSS"

        if bar_mins >= tc_mins:
            return bar["close"], "TIME_CLOSE"

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

    totals   = {v: 0.0 for v in STOP_VARIANTS}
    changes  = {v: [] for v in STOP_VARIANTS if v != 0.015}

    for day in ex1_days:
        date    = day["date"]
        trades  = day["trades"]
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
            bars        = minute_bars.get(ticker, [])

            if not bars:
                for v in STOP_VARIANTS:
                    totals[v] += orig_pnl
                print(f"  {ticker:6} {entry_time}  no bars — keeping original")
                continue

            results = {}
            for sl in STOP_VARIANTS:
                exit_px, exit_reason = simulate_trade(bars, entry_price, entry_time, units, sl)
                sim_pnl = round((exit_px - entry_price) * units, 2)
                results[sl] = (sim_pnl, exit_reason)
                totals[sl] += sim_pnl

            base_pnl, base_reason = results[0.015]
            parts = [f"orig={orig_pnl:+.2f}({orig_reason[:4]})  sim1.5%={base_pnl:+.2f}({base_reason[:4]})"]
            for v in [0.0175, 0.020]:
                p, r = results[v]
                diff = round(p - base_pnl, 2)
                label = f"{v*100:.2f}".rstrip('0').rstrip('.')
                parts.append(f"{label}%={p:+.2f}({r[:4]},Δ{diff:+.2f})")
                if abs(diff) > 0.01:
                    changes[v].append({
                        "date": date, "ticker": ticker,
                        "orig_pnl": orig_pnl, "orig_reason": orig_reason,
                        "new_pnl": p, "new_reason": r,
                        "diff": round(p - orig_pnl, 2),
                    })
            print(f"  {ticker:6} {entry_time}  {'  '.join(parts)}")

    print(f"\n{'='*70}")
    print(f"SUMMARY")
    for v in STOP_VARIANTS:
        label = f"{v*100:.2f}".rstrip('0').rstrip('.')
        net   = round(totals[v] - totals[0.015], 2)
        marker = " ← current" if v == 0.015 else f"  net {net:+.2f} vs current"
        print(f"  STOP_LOSS {label}%:  ${totals[v]:>8.2f}{marker}")

    print()
    for v in [0.0175, 0.020]:
        label   = f"{v*100:.2f}".rstrip('0').rstrip('.')
        ch      = changes[v]
        helped  = [c for c in ch if c["diff"] > 0]
        hurt    = [c for c in ch if c["diff"] < 0]
        print(f"  STOP_LOSS {label}% — {len(ch)} trades changed: {len(helped)} better, {len(hurt)} worse")
        if hurt:
            print(f"  Trades worse (wider stop held longer and lost more):")
            for c in hurt:
                print(f"    {c['date']} {c['ticker']:6}  "
                      f"orig={c['orig_pnl']:+.2f}({c['orig_reason'][:4]})  "
                      f"new={c['new_pnl']:+.2f}({c['new_reason'][:4]})  diff={c['diff']:+.2f}")


if __name__ == "__main__":
    main()
