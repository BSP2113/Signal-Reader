"""
test_gap_filter.py — test skipping ORB when a ticker gaps too far at open

Logic: if a ticker's first-bar close is more than X% away from the prior day's
close, skip the ORB signal for that ticker (VWAP crosses still allowed).

Tests gap thresholds: 3%, 4%, 5%
Compares all three against the 2 PM baseline from backfill.json.

Run: venv/bin/python3 test_gap_filter.py
"""

import json
import os
from datetime import datetime, timedelta, timezone
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET       = "America/New_York"

TICKERS        = ["NVDA", "TSLA", "AMD", "COIN", "META", "PLTR", "SMCI", "NFLX"]
BUDGET         = 5000.0
ORB_BARS       = 15
ORB_CUTOFF     = "11:30"
ENTRY_CLOSE    = "14:00"
TAKE_PROFIT    = 0.03
TRAIL_STOP     = 0.025
TRAIL_LOCK     = 0.01
STOP_LOSS      = 0.015
COOLDOWN       = 30
DAY_LOSS_LIMIT = -75.0
ALLOC_BULL     = {"TAKE": 1750.0, "MAYBE": 1000.0}
ALLOC_NEUT     = {"TAKE": 1500.0, "MAYBE":  750.0}
ALLOC_BEAR     = {"TAKE":  500.0, "MAYBE":  500.0}

GAP_THRESHOLDS = [0.03, 0.04, 0.05]


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


def find_exit(closes, times, entry_price, entry_bar):
    peak = entry_price
    for i in range(entry_bar + 1, len(closes)):
        price = closes[i]
        peak  = max(peak, price)
        if times[i] >= ENTRY_CLOSE:
            return {"bar": i, "price": price, "reason": "TIME_CLOSE"}
        if price >= entry_price * (1 + TAKE_PROFIT):
            return {"bar": i, "price": price, "reason": "TAKE_PROFIT"}
        if peak >= entry_price * (1 + TRAIL_LOCK) and price <= peak * (1 - TRAIL_STOP):
            return {"bar": i, "price": price, "reason": "TRAILING_STOP"}
        if price <= entry_price * (1 - STOP_LOSS):
            return {"bar": i, "price": price, "reason": "STOP_LOSS"}
    return {"bar": len(closes) - 1, "price": closes[-1], "reason": "EOD"}


def find_all_trades(closes, highs, lows, volumes, times, skip_orb=False):
    if len(closes) <= ORB_BARS:
        return []
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    vwap     = calc_vwap(highs, lows, closes, volumes)
    orb_high = max(closes[:ORB_BARS])
    orb_used = skip_orb   # treat as already used if gap filter fires
    trades   = []
    next_bar = ORB_BARS

    while True:
        if next_bar >= len(closes) or times[next_bar] > ENTRY_CLOSE:
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
                if times[i] > ENTRY_CLOSE:
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

        exit_ = find_exit(closes, times, entry["price"], entry["bar"])
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


def simulate_day(closes, highs, lows, volumes, times, market_state, skip_orb=False):
    trades        = find_all_trades(closes, highs, lows, volumes, times, skip_orb)
    cash          = BUDGET
    day_pnl       = 0.0
    day_limit_hit = False

    for trade_num, (entry, exit_) in enumerate(trades, 1):
        if day_limit_hit:
            break
        if trade_num > 1 and market_state != "bullish":
            continue
        alloc  = spy_alloc(market_state, entry["rating"])
        if cash < alloc:
            break
        cash  -= alloc
        pnl    = round((exit_["price"] - entry["price"]) / entry["price"] * alloc, 2)
        cash  += alloc + pnl
        day_pnl += pnl
        if round(day_pnl, 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True
            break

    return round(day_pnl, 2)


def run():
    print("Loading backfill data...")
    with open(os.path.join(BASE_DIR, "backfill.json")) as f:
        backfill = json.load(f)

    ex1_days = sorted(
        [e for e in backfill if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )
    dates        = [e["date"] for e in ex1_days]
    ms_by_date   = {e["date"]: e.get("market_state", "neutral") for e in ex1_days}
    baseline_pnl = {e["date"]: e["total_pnl"] for e in ex1_days}

    print(f"Found {len(dates)} days ({dates[0]} to {dates[-1]})")
    print("Fetching intraday + daily data from Alpaca...\n")

    key, secret = _load_creds()
    client      = StockHistoricalDataClient(api_key=key, secret_key=secret)

    # results[threshold][date] = pnl
    results      = {t: {} for t in GAP_THRESHOLDS}
    # gap_log: record which tickers were filtered each day
    gap_log      = {}

    for date in dates:
        print(f"  {date}...", end="", flush=True)
        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt   = start_dt + timedelta(days=1)

        # Fetch prior closes (last 5 trading days of daily bars ending at start of today)
        lookback = start_dt - timedelta(days=7)
        daily    = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=TICKERS, timeframe=TimeFrame.Day,
            start=lookback, end=start_dt, feed="iex",
        ))
        prior_close = {}
        for ticker in TICKERS:
            bars = daily.data.get(ticker, [])
            if bars:
                prior_close[ticker] = bars[-1].close

        # Fetch intraday
        intraday = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=TICKERS, timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))

        market_state = ms_by_date[date]
        gap_log[date] = {}

        # Per-threshold day totals
        day_totals = {t: 0.0 for t in GAP_THRESHOLDS}

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

                # Compute gap vs prior close
                gap_pct = 0.0
                if ticker in prior_close and prior_close[ticker] and closes:
                    gap_pct = (closes[0] - prior_close[ticker]) / prior_close[ticker]
                gap_log[date][ticker] = round(gap_pct * 100, 2)

                for threshold in GAP_THRESHOLDS:
                    skip_orb = abs(gap_pct) > threshold
                    day_totals[threshold] += simulate_day(
                        closes, highs, lows, volumes, times, market_state, skip_orb
                    )
            except Exception:
                pass

        for threshold in GAP_THRESHOLDS:
            results[threshold][date] = round(day_totals[threshold], 2)

        print(" done")

    # --- Print results table ---
    labels = {0.03: "3% gap", 0.04: "4% gap", 0.05: "5% gap"}
    print("\n" + "=" * 78)
    print(f"{'Date':<12} {'Baseline':>10} {'3% filter':>11} {'4% filter':>11} {'5% filter':>11}  {'3% diff':>8}  {'4% diff':>8}  {'5% diff':>8}")
    print("-" * 78)

    cum_base = 0.0
    cum      = {t: 0.0 for t in GAP_THRESHOLDS}

    for date in dates:
        b = baseline_pnl.get(date, 0)
        cum_base += b
        row = f"{date:<12} {b:>+9.2f}"
        diffs = []
        for t in GAP_THRESHOLDS:
            v = results[t].get(date, 0)
            cum[t] += v
            row  += f"  {v:>+9.2f}"
            diffs.append(v - b)
        for d in diffs:
            row += f"  {d:>+7.2f}"
        print(row)

    print("-" * 78)
    row = f"{'TOTAL':<12} {cum_base:>+9.2f}"
    for t in GAP_THRESHOLDS:
        row += f"  {cum[t]:>+9.2f}"
    for t in GAP_THRESHOLDS:
        row += f"  {cum[t]-cum_base:>+7.2f}"
    print(row)

    print("\nWin/Loss days:")
    print(f"  {'Baseline':<16}  {sum(1 for d in dates if baseline_pnl.get(d,0)>0)}W / {sum(1 for d in dates if baseline_pnl.get(d,0)<=0)}L")
    for t in GAP_THRESHOLDS:
        wins   = sum(1 for d in dates if results[t].get(d, 0) > 0)
        losses = sum(1 for d in dates if results[t].get(d, 0) <= 0)
        print(f"  {labels[t]:<16}  {wins}W / {losses}L")

    # --- Show which tickers were filtered and whether it helped or hurt ---
    print("\nGap filter events (tickers skipped from ORB at each threshold):")
    print(f"  {'Date':<12} {'Ticker':<6} {'Gap%':>7}   Baseline trade pnl  |  Filtered out?")
    print(f"  {'-'*65}")

    # Find days where at least one ticker would be filtered at 3%
    for date in dates:
        for ticker, gap in sorted(gap_log.get(date, {}).items(), key=lambda x: -abs(x[1])):
            if abs(gap) > 3.0:
                # Find what the baseline trade was for this ticker on this day
                day_ex = next((e for e in ex1_days if e["date"] == date), None)
                trade_pnl = None
                if day_ex:
                    for tr in day_ex.get("trades", []):
                        if tr["ticker"] == ticker:
                            trade_pnl = tr["pnl"]
                            break
                pnl_str = f"{trade_pnl:+.2f}" if trade_pnl is not None else "no trade"
                print(f"  {date:<12} {ticker:<6} {gap:>+6.1f}%   {pnl_str}")

    print()


if __name__ == "__main__":
    run()
