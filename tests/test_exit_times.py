"""
test_exit_times.py — compare exit time variants against the 2 PM baseline

Runs the full ex3 simulation three ways for every day in backfill.json:
  Baseline : ENTRY_CLOSE = 14:00  (current)
  Test A   : ENTRY_CLOSE = 15:00  (3 PM hold)
  Test B   : ENTRY_CLOSE = 15:59  (4 PM / EOD hold)

Does NOT modify exercises.json or backfill.json.
Run: venv/bin/python3 test_exit_times.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET       = "America/New_York"

TICKERS       = ["NVDA", "TSLA", "AMD", "COIN", "META", "PLTR", "SMCI", "NFLX"]
BUDGET        = 5000.0
ORB_BARS      = 15
ORB_CUTOFF    = "11:30"
TAKE_PROFIT   = 0.03
TRAIL_STOP    = 0.025
TRAIL_LOCK    = 0.01
STOP_LOSS     = 0.015
COOLDOWN      = 30
DAY_LOSS_LIMIT = -75.0
ALLOC_BULL    = {"TAKE": 1750.0, "MAYBE": 1000.0}
ALLOC_NEUT    = {"TAKE": 1500.0, "MAYBE":  750.0}
ALLOC_BEAR    = {"TAKE":  500.0, "MAYBE":  500.0}

VARIANTS = {
    "Baseline (2pm)": "14:00",
    "Test A (3pm)":   "15:00",
    "Test B (4pm)":   "15:59",
}


def _load_creds():
    path  = os.path.join(BASE_DIR, ".env")
    creds = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds["ALPACA_API_KEY"], creds["ALPACA_API_SECRET"]


def calc_vwap(highs, lows, closes, volumes):
    cum_tp_vol, cum_vol, result = 0, 0, []
    for h, l, c, v in zip(highs, lows, closes, volumes):
        tp = (h + l + c) / 3
        cum_tp_vol += tp * v
        cum_vol    += v
        result.append(cum_tp_vol / cum_vol if cum_vol else c)
    return result


def score_signal(closes_so_far, vol, avg_volume):
    vol_ratio = vol / avg_volume if avg_volume else 0
    if len(closes_so_far) < 2:
        return "SKIP", vol_ratio
    day_open   = closes_so_far[0]
    day_change = (closes_so_far[-1] - day_open) / day_open if day_open else 0
    if day_change < -0.02 and vol_ratio < 2.0:
        return "SKIP", vol_ratio
    if vol_ratio < 1.0:
        return "SKIP", vol_ratio
    score  = 1 if vol_ratio >= 1.5 else 0
    recent = closes_so_far[-min(12, len(closes_so_far)):]
    flips  = sum(1 for j in range(1, len(recent) - 1)
                 if (recent[-j] - recent[-j-1]) * (recent[-j-1] - recent[-j-2]) < 0)
    score += 1 if flips < 3 else -1
    if score >= 2:   return "TAKE",  vol_ratio
    elif score >= 0: return "MAYBE", vol_ratio
    else:            return "SKIP",  vol_ratio


def find_exit(closes, times, entry_price, entry_bar, entry_close):
    peak = entry_price
    for i in range(entry_bar + 1, len(closes)):
        price = closes[i]
        peak  = max(peak, price)
        if times[i] >= entry_close:
            return {"bar": i, "time": times[i], "price": price, "reason": "TIME_CLOSE"}
        if price >= entry_price * (1 + TAKE_PROFIT):
            return {"bar": i, "time": times[i], "price": price, "reason": "TAKE_PROFIT"}
        if peak >= entry_price * (1 + TRAIL_LOCK) and price <= peak * (1 - TRAIL_STOP):
            return {"bar": i, "time": times[i], "price": price, "reason": "TRAILING_STOP"}
        if price <= entry_price * (1 - STOP_LOSS):
            return {"bar": i, "time": times[i], "price": price, "reason": "STOP_LOSS"}
    return {"bar": len(closes) - 1, "time": times[-1], "price": closes[-1], "reason": "EOD"}


def find_all_trades(closes, highs, lows, volumes, times, entry_close):
    if len(closes) <= ORB_BARS:
        return []
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    vwap     = calc_vwap(highs, lows, closes, volumes)
    orb_high = max(closes[:ORB_BARS])
    orb_used = False
    trades   = []
    next_bar = ORB_BARS

    while True:
        if next_bar >= len(closes) or times[next_bar] > entry_close:
            break
        entry = None

        if not orb_used:
            for i in range(next_bar, len(closes)):
                if times[i] > ORB_CUTOFF:
                    break
                if closes[i] > orb_high:
                    rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
                    if rating != "SKIP":
                        entry    = {"bar": i, "time": times[i], "price": closes[i],
                                    "rating": rating, "signal": "ORB"}
                        orb_used = True
                        break

        if not entry:
            for i in range(max(next_bar, ORB_BARS + 3), len(closes)):
                if times[i] > entry_close:
                    break
                was_below = all(closes[i-k] < vwap[i-k] for k in range(1, 4))
                cross_up  = closes[i] > vwap[i]
                if was_below and cross_up and volumes[i] >= avg_vol * 1.5:
                    rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
                    if rating != "SKIP":
                        entry = {"bar": i, "time": times[i], "price": closes[i],
                                 "rating": rating, "signal": "VWAP"}
                        break

        if not entry:
            break

        exit_ = find_exit(closes, times, entry["price"], entry["bar"], entry_close)
        trades.append((entry, exit_))

        if exit_["reason"] in ("STOP_LOSS", "TRAILING_STOP"):
            next_bar = exit_["bar"] + COOLDOWN
        else:
            next_bar = exit_["bar"] + 1

        if exit_["reason"] in ("TIME_CLOSE", "EOD"):
            break

    return trades


def spy_alloc(market_state, rating):
    if market_state == "bullish": return ALLOC_BULL[rating]
    if market_state == "bearish": return ALLOC_BEAR[rating]
    return ALLOC_NEUT[rating]


def simulate_day(closes, highs, lows, volumes, times, market_state, entry_close):
    trades        = find_all_trades(closes, highs, lows, volumes, times, entry_close)
    entries       = []
    cash          = BUDGET
    day_limit_hit = False

    for trade_num, (entry, exit_) in enumerate(trades, 1):
        if day_limit_hit:
            break
        if trade_num > 1 and market_state != "bullish":
            continue
        alloc = spy_alloc(market_state, entry["rating"])
        if cash < alloc:
            break
        cash -= alloc
        pnl   = round((exit_["price"] - entry["price"]) / entry["price"] * alloc, 2)
        cash += alloc + pnl
        entries.append(pnl)
        if round(sum(entries), 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True
            break

    return round(sum(entries), 2)


def run():
    print("Loading backfill data...")
    path = os.path.join(BASE_DIR, "backfill.json")
    with open(path) as f:
        backfill = json.load(f)

    ex1_days = sorted(
        [e for e in backfill if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )
    dates = [e["date"] for e in ex1_days]
    ms_by_date = {e["date"]: e.get("market_state", "neutral") for e in ex1_days}
    baseline_pnl = {e["date"]: e["total_pnl"] for e in ex1_days}

    print(f"Found {len(dates)} days ({dates[0]} to {dates[-1]})\n")
    print("Fetching intraday data from Alpaca (this takes a few minutes)...")

    key, secret = _load_creds()
    client      = StockHistoricalDataClient(api_key=key, secret_key=secret)

    # Results: results[variant_name][date] = pnl
    results = {"Baseline (2pm)": baseline_pnl}
    for variant in ["Test A (3pm)", "Test B (4pm)"]:
        results[variant] = {}

    for date in dates:
        print(f"  {date}...", end="", flush=True)
        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt   = start_dt + timedelta(days=1)
        market_state = ms_by_date[date]

        # Fetch all tickers for this day
        intraday = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=TICKERS, timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))

        for variant_name, entry_close in [("Test A (3pm)", "15:00"), ("Test B (4pm)", "15:59")]:
            day_total = 0.0
            for ticker in TICKERS:
                try:
                    df = intraday.df
                    if isinstance(df.index, pd.MultiIndex):
                        df = df.xs(ticker, level=0)
                    df    = df.tz_convert(ET)
                    today = df.between_time("09:30", "15:59")
                    if today.empty:
                        continue
                    closes  = [round(float(v), 2) for v in today["close"].tolist()]
                    highs   = [round(float(v), 2) for v in today["high"].tolist()]
                    lows    = [round(float(v), 2) for v in today["low"].tolist()]
                    volumes = [int(v) for v in today["volume"].tolist()]
                    times   = [t.strftime("%H:%M") for t in today.index]
                    day_total += simulate_day(closes, highs, lows, volumes, times, market_state, entry_close)
                except Exception:
                    pass
            results[variant_name][date] = round(day_total, 2)

        print(" done")

    # --- Print comparison ---
    print("\n" + "=" * 74)
    print(f"{'Date':<12} {'Baseline (2pm)':>15} {'Test A (3pm)':>14} {'Test B (4pm)':>14}  {'A vs Base':>10}  {'B vs Base':>10}")
    print("-" * 74)

    cum = {"Baseline (2pm)": 0, "Test A (3pm)": 0, "Test B (4pm)": 0}
    for date in dates:
        b  = results["Baseline (2pm)"].get(date, 0)
        ta = results["Test A (3pm)"].get(date, 0)
        tb = results["Test B (4pm)"].get(date, 0)
        cum["Baseline (2pm)"] += b
        cum["Test A (3pm)"]   += ta
        cum["Test B (4pm)"]   += tb
        diff_a = ta - b
        diff_b = tb - b
        print(f"{date:<12} {b:>+14.2f}  {ta:>+13.2f}  {tb:>+13.2f}  {diff_a:>+9.2f}   {diff_b:>+9.2f}")

    print("-" * 74)
    b  = cum["Baseline (2pm)"]
    ta = cum["Test A (3pm)"]
    tb = cum["Test B (4pm)"]
    print(f"{'TOTAL':<12} {b:>+14.2f}  {ta:>+13.2f}  {tb:>+13.2f}  {ta-b:>+9.2f}   {tb-b:>+9.2f}")

    print("\nWin/Loss days:")
    for vname in ["Baseline (2pm)", "Test A (3pm)", "Test B (4pm)"]:
        wins   = sum(1 for d in dates if results[vname].get(d, 0) > 0)
        losses = sum(1 for d in dates if results[vname].get(d, 0) <= 0)
        total  = sum(results[vname].get(d, 0) for d in dates)
        print(f"  {vname:<20} {wins}W / {losses}L   cumulative: {total:+.2f}")

    print()


if __name__ == "__main__":
    run()
