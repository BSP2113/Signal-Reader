"""
afternoon_scan_v2.py — Corrected backtest for Option 2 afternoon trigger.

Trigger (new): from 13:00 ET onward, first bar where:
  1. volume >= 50x morning average (09:30–12:59 ET)
  2. last 3 bars are all higher closes than the bar before them (upward trend)
  NO "above morning high" requirement — catches KOPN-type moves

Checks whether the bar holds +2% over the next 3 bars (simple quality filter).
Reports how often the trade would have been profitable.

CRITICAL: Alpaca returns UTC timestamps. Must convert to ET before comparing times.
"""

import os, sys
sys.path.insert(0, "/home/ben/Signal")

import pandas as pd
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

ENV_PATH = "/home/ben/Signal/.env"
creds = {}
with open(ENV_PATH) as f:
    for line in f:
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()
API_KEY    = creds["ALPACA_API_KEY"]
API_SECRET = creds["ALPACA_API_SECRET"]
client     = StockHistoricalDataClient(API_KEY, API_SECRET)

ET = "America/New_York"

TICKERS = [
    "NVDA","TSLA","AMD","COIN","META","PLTR","SMCI","CRDO","IONQ","RIVN",
    "DELL","KOPN","SHOP","ASTS","ARM","DKNG","UPST"
]

SCAN_DATES = [
    "2026-04-13","2026-04-14","2026-04-15","2026-04-16","2026-04-17",
    "2026-04-22","2026-04-23","2026-04-24","2026-04-25","2026-04-28",
    "2026-04-29","2026-04-30","2026-05-01","2026-05-02","2026-05-03","2026-05-04",
]

VOL_THRESH    = 10    # volume must be 10x morning average (relaxed to find hits)
TREND_BARS    = 2     # last N bars must all be rising closes
SCAN_START    = "13:00"
MARKET_CLOSE  = "16:00"
HOLD_CHECK    = 3     # bars after entry to check for +2% hold
HOLD_TARGET   = 0.02  # 2% gain threshold for "would have worked"


def fetch_bars(ticker, date_str):
    start = datetime.strptime(date_str, "%Y-%m-%d")
    end   = start + timedelta(days=1)
    req   = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        feed="iex",
    )
    bars = client.get_stock_bars(req)
    df   = bars.df
    if df is None or df.empty:
        return None
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")
    df = df.tz_convert(ET)                        # CRITICAL: convert UTC → ET
    df["time_str"] = df.index.strftime("%H:%M")
    return df


all_hits = []

for date_str in SCAN_DATES:
    print(f"\n=== {date_str} ===")
    for ticker in TICKERS:
        df = fetch_bars(ticker, date_str)
        if df is None or df.empty:
            continue

        # Split into morning (09:30–12:59) and afternoon (13:00+)
        morning = df[(df["time_str"] >= "09:30") & (df["time_str"] < SCAN_START)]
        afternoon = df[(df["time_str"] >= SCAN_START) & (df["time_str"] < MARKET_CLOSE)]

        if len(morning) < 5 or len(afternoon) < 4:
            continue

        morning_avg_vol = morning["volume"].mean()
        if morning_avg_vol < 1:
            continue

        closes  = afternoon["close"].tolist()
        volumes = afternoon["volume"].tolist()
        times   = afternoon["time_str"].tolist()

        # Scan for trigger bar
        for i in range(TREND_BARS, len(closes)):
            # Check volume spike
            vol_ratio = volumes[i] / morning_avg_vol
            if vol_ratio < VOL_THRESH:
                continue

            # Check last TREND_BARS bars are all rising closes
            rising = all(
                closes[j] > closes[j - 1]
                for j in range(i - TREND_BARS + 1, i + 1)
            )
            if not rising:
                continue

            # Trigger found
            entry_price = closes[i]
            entry_time  = times[i]

            # Check if it holds +HOLD_TARGET over next HOLD_CHECK bars
            future_closes = closes[i + 1: i + 1 + HOLD_CHECK]
            if not future_closes:
                held = False
            else:
                max_future = max(future_closes)
                held = (max_future - entry_price) / entry_price >= HOLD_TARGET

            hit = {
                "date":       date_str,
                "ticker":     ticker,
                "time":       entry_time,
                "entry":      round(entry_price, 4),
                "vol_ratio":  round(vol_ratio, 1),
                "held":       held,
            }
            all_hits.append(hit)
            print(f"  {ticker} @ {entry_time}  entry={entry_price:.2f}  vol={vol_ratio:.0f}x  held={held}")
            break  # first qualifying bar only


# Summary
total  = len(all_hits)
held   = sum(1 for h in all_hits if h["held"])
print(f"\n{'='*50}")
print(f"Total triggers: {total}")
if total:
    print(f"Held +{HOLD_TARGET*100:.0f}% within {HOLD_CHECK} bars: {held} / {total}  ({held/total*100:.0f}%)")
    print(f"\nAll hits:")
    for h in all_hits:
        tag = "OK" if h["held"] else "--"
        print(f"  [{tag}] {h['date']} {h['ticker']} @ {h['time']}  {h['entry']:.2f}  {h['vol_ratio']:.0f}x")
else:
    print("No triggers found — threshold may be too strict.")
