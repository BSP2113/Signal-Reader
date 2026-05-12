"""
backfill.py — Run EX1 for the past 30 trading days.
Uses SPY daily data to identify real market-open days (no weekends/holidays).
Skips dates already in exercises.json.
Run once: venv/bin/python3 backfill.py
"""

import json
import os
import sys
import yfinance as yf
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ex1 import run_ex1

def get_trading_days(n=30):
    end   = datetime.today()
    start = end - timedelta(days=60)  # fetch extra to ensure 30 trading days
    spy   = yf.download("SPY", start=start.strftime("%Y-%m-%d"),
                        end=end.strftime("%Y-%m-%d"), interval="1d",
                        progress=False, auto_adjust=True)
    days = [d.strftime("%Y-%m-%d") for d in spy.index]
    return sorted(days)[-n:]  # last 30 trading days

def already_logged():
    path = os.path.join(BASE_DIR, "exercises.json")
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {e["date"] for e in json.load(f)}

if __name__ == "__main__":
    trading_days = get_trading_days(30)
    logged       = already_logged()
    to_run       = [d for d in trading_days if d not in logged]

    print(f"30 trading days: {trading_days[0]} → {trading_days[-1]}")
    print(f"Already logged:  {sorted(logged)}")
    print(f"To backfill:     {len(to_run)} days\n")

    for date in to_run:
        print(f"\n{'='*50}")
        run_ex1(date)

    print(f"\nBackfill complete.")
