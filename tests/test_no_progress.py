"""
test_no_progress.py — Backtest a "no-progress cut" rule against EX1 history.

Rule: if a position is flat or negative 90 minutes after entry, exit immediately
at the T+90 price rather than holding to TIME_CLOSE or a stop/take-profit.

Only applies when:
  - The original exit happened AFTER the T+90 mark (i.e., the position was still
    open at 90 minutes — if it already stopped out before T+90, no change)
  - T+90 is on or before 14:00 (positions entered after 12:30 are already
    governed by TIME_CLOSE before 90 min is up)

Threshold tested: price at T+90 <= entry price (flat or negative = no progress).

Usage: venv/bin/python3 test_no_progress.py
"""

import json
import os
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CUTOFF_MIN = 90          # minutes after entry before we check progress
THRESHOLD  = -0.005      # exit if gain at T+90 is <= this (-0.5% = meaningfully negative)


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


def time_to_mins(tstr):
    h, m = map(int, tstr.split(":"))
    return h * 60 + m


def mins_to_time(mins):
    return f"{mins // 60:02d}:{mins % 60:02d}"


def main():
    # Load all EX1 trades
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        data = json.load(f)

    ex1_days = [e for e in data if "Exercise 1" in e["title"]]
    ex1_days.sort(key=lambda e: e["date"])

    key, secret = _load_creds()
    client = StockHistoricalDataClient(api_key=key, secret_key=secret)

    total_original = 0.0
    total_modified = 0.0
    all_changes = []

    for day in ex1_days:
        date      = day["date"]
        trades    = day["trades"]
        tickers   = list({t["ticker"] for t in trades})

        start_dt  = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt    = start_dt + timedelta(days=1)

        print(f"\n--- {date} ({len(trades)} trades) ---")

        # Fetch 1-minute bars for all tickers that traded this day
        try:
            bars_resp = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=tickers, timeframe=TimeFrame.Minute,
                start=start_dt, end=end_dt, feed="iex",
            ))
        except Exception as e:
            print(f"  Data fetch failed: {e}")
            continue

        # Build time-keyed price lookup per ticker
        minute_prices = {}
        for ticker in tickers:
            ticker_bars = bars_resp.data.get(ticker, [])
            minute_prices[ticker] = {}
            for bar in ticker_bars:
                # Convert bar timestamp to ET HH:MM
                ts_et = bar.timestamp.astimezone(
                    __import__("zoneinfo").ZoneInfo("America/New_York")
                )
                key_str = ts_et.strftime("%H:%M")
                minute_prices[ticker][key_str] = bar.close

        day_original = 0.0
        day_modified = 0.0

        for t in trades:
            ticker      = t["ticker"]
            entry_price = t["entry"]
            entry_time  = t["time"]
            exit_time   = t["exit_time"]
            exit_price  = t["exit"]
            exit_reason = t["exit_reason"]
            allocated   = t["allocated"]
            orig_pnl    = t["pnl"]

            entry_mins  = time_to_mins(entry_time)
            exit_mins   = time_to_mins(exit_time)
            t90_mins    = entry_mins + CUTOFF_MIN
            t90_time    = mins_to_time(t90_mins)

            day_original += orig_pnl

            # Rule only applies if position was still open at T+90
            # and T+90 is on or before 14:00
            if exit_mins <= t90_mins or t90_mins > time_to_mins("14:00"):
                # Position already closed before T+90 — no change
                day_modified += orig_pnl
                print(f"  {ticker:6} {entry_time}→{exit_time}  {exit_reason:15}  "
                      f"orig={orig_pnl:+.2f}  [rule N/A — exited before T+90]")
                continue

            # Find price at T+90
            prices = minute_prices.get(ticker, {})
            t90_price = None
            for offset in range(0, 6):   # try T+90 then up to T+95
                candidate = mins_to_time(t90_mins + offset)
                if candidate in prices:
                    t90_price = prices[candidate]
                    break

            if t90_price is None:
                day_modified += orig_pnl
                print(f"  {ticker:6} {entry_time}→{exit_time}  {exit_reason:15}  "
                      f"orig={orig_pnl:+.2f}  [no T+90 bar found]")
                continue

            t90_pct  = (t90_price - entry_price) / entry_price
            t90_gain = t90_pct * 100

            if t90_pct <= THRESHOLD:
                # Apply the cut — exit at T+90 price
                new_pnl = round((t90_price - entry_price) / entry_price * allocated, 2)
                diff    = new_pnl - orig_pnl
                day_modified += new_pnl
                label = f"CUT at {t90_time} ({t90_gain:+.2f}%) → pnl={new_pnl:+.2f}  diff={diff:+.2f}"
                all_changes.append({
                    "date": date, "ticker": ticker,
                    "orig": orig_pnl, "new": new_pnl, "diff": diff,
                    "t90_pct": t90_gain, "orig_exit": exit_reason,
                })
            else:
                # No-progress cut doesn't fire — keep original result
                day_modified += orig_pnl
                label = f"kept  (T+90={t90_gain:+.2f}% — progressing)"

            print(f"  {ticker:6} {entry_time}→{exit_time}  {exit_reason:15}  "
                  f"orig={orig_pnl:+.2f}  {label}")

        day_diff = day_modified - day_original
        print(f"  Day total:  original={day_original:+.2f}  modified={day_modified:+.2f}  diff={day_diff:+.2f}")
        total_original += day_original
        total_modified += day_modified

    # Summary
    net_diff = total_modified - total_original
    print(f"\n{'='*60}")
    print(f"SUMMARY — No-Progress Cut (threshold: flat or negative at T+{CUTOFF_MIN})")
    print(f"  Original total P&L:  ${total_original:+.2f}")
    print(f"  Modified total P&L:  ${total_modified:+.2f}")
    print(f"  Net difference:      ${net_diff:+.2f}")
    print()
    print(f"  Trades where rule fired: {len(all_changes)}")
    if all_changes:
        helped  = [c for c in all_changes if c["diff"] > 0]
        hurt    = [c for c in all_changes if c["diff"] < 0]
        neutral = [c for c in all_changes if c["diff"] == 0]
        print(f"    Helped (saved loss or reduced loss): {len(helped)}")
        print(f"    Hurt   (cut a position that recovered): {len(hurt)}")
        print(f"    Neutral: {len(neutral)}")
        print()
        if hurt:
            print("  Cases where cutting early was WORSE (recovery after T+90):")
            for c in hurt:
                print(f"    {c['date']} {c['ticker']:6}  T+90={c['t90_pct']:+.2f}%  "
                      f"orig={c['orig']:+.2f}  cut_to={c['new']:+.2f}  diff={c['diff']:+.2f}  "
                      f"(original exit: {c['orig_exit']})")


if __name__ == "__main__":
    main()
