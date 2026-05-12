"""
build_market_states.py — Reconstructs historical market state (BULL/NEUT/BEAR) for
every date in backfill.json using the same logic as market_check.py.

Fetches per date:
  - SPY: prior daily close + last premarket bar (4AM–9:29AM ET) for gap %
  - VIXY: last two daily closes for day-over-day trend %

Classification (mirrors market_check.py):
  BEAR  — SPY gap <= -0.5%  OR  VIXY trend >= +3%
  BULL  — SPY gap >= +0.5%  AND VIXY trend < +3%
  NEUT  — everything else

Saves results to market_states_historical.json.
Run once; reuse for rule testing.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET       = "America/New_York"
SPY_BULL =  0.005
SPY_BEAR = -0.005
VIXY_SURGE = 0.03


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


def get_dates():
    results = {}
    for fname in ["backfill.json", "exercises.json"]:
        path = os.path.join(BASE_DIR, fname)
        if not os.path.exists(path):
            continue
        with open(path) as f:
            data = json.load(f)
        for e in data:
            if "Exercise 1" in e["title"]:
                results[e["date"]] = True
    return sorted(results.keys())


def classify(spy_gap, vixy_trend):
    if spy_gap <= SPY_BEAR or vixy_trend >= VIXY_SURGE:
        return "bearish"
    if spy_gap >= SPY_BULL and vixy_trend < VIXY_SURGE:
        return "bullish"
    return "neutral"


def run():
    key, secret = _load_creds()
    client = StockHistoricalDataClient(api_key=key, secret_key=secret)

    dates = get_dates()
    print(f"Fetching market state for {len(dates)} dates...\n")

    out_path = os.path.join(BASE_DIR, "market_states_historical.json")
    existing = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            for entry in json.load(f):
                existing[entry["date"]] = entry

    results = []
    for date in dates:
        if date in existing:
            print(f"  {date}  [cached]  {existing[date]['state'].upper()}")
            results.append(existing[date])
            continue

        trade_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        # Prior daily bars for SPY prev close and VIXY trend
        daily = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=["SPY", "VIXY"],
            timeframe=TimeFrame.Day,
            start=trade_dt - timedelta(days=7),
            end=trade_dt,
            feed="iex",
        ))

        spy_prev_close = None
        vixy_trend     = 0.0
        vixy_note      = "no data"

        if "SPY" in daily.data and daily.data["SPY"]:
            spy_prev_close = daily.data["SPY"][-1].close

        if "VIXY" in daily.data and len(daily.data["VIXY"]) >= 2:
            v = daily.data["VIXY"]
            vixy_trend = (v[-1].close - v[-2].close) / v[-2].close if v[-2].close else 0.0
            vixy_note = f"{v[-2].close:.2f} → {v[-1].close:.2f} ({vixy_trend*100:+.2f}%)"
        elif "VIXY" in daily.data and len(daily.data["VIXY"]) == 1:
            vixy_note = "only 1 bar"

        # Premarket SPY bars (9:00–9:29 AM ET = 13:00–13:29 UTC)
        pm_start = trade_dt.replace(hour=13, minute=0)
        pm_end   = trade_dt.replace(hour=13, minute=30)

        spy_premarket = None
        spy_gap       = 0.0
        gap_note      = "no premarket data"

        try:
            pm = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols="SPY",
                timeframe=TimeFrame.Minute,
                start=pm_start,
                end=pm_end,
                feed="iex",
            ))
            if "SPY" in pm.data and pm.data["SPY"]:
                spy_premarket = pm.data["SPY"][-1].close
                if spy_prev_close:
                    spy_gap = (spy_premarket - spy_prev_close) / spy_prev_close
                    gap_note = f"${spy_prev_close:.2f} → ${spy_premarket:.2f} ({spy_gap*100:+.2f}%)"
        except Exception as e:
            gap_note = f"error: {e}"

        state = classify(spy_gap, vixy_trend)

        entry = {
            "date":           date,
            "state":          state,
            "spy_gap_pct":    round(spy_gap * 100, 3),
            "vixy_trend_pct": round(vixy_trend * 100, 3),
            "spy_prev_close": round(spy_prev_close, 2) if spy_prev_close else None,
            "spy_premarket":  round(spy_premarket, 2)  if spy_premarket  else None,
        }
        results.append(entry)

        # Save incrementally after each date
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)

        print(f"  {date}  {state.upper():<8}  SPY: {gap_note:<36}  VIXY: {vixy_note}")
        time.sleep(0.3)  # avoid rate limit

    print(f"\nDone. Saved {len(results)} entries to market_states_historical.json")

    # Summary
    states = [r["state"] for r in results]
    print(f"  BULL: {states.count('bullish')}  NEUT: {states.count('neutral')}  BEAR: {states.count('bearish')}")


if __name__ == "__main__":
    run()
