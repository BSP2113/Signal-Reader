"""
test_vwap.py — test two VWAP variants vs current baseline

Variant A: VWAP entries disabled entirely (ORB only)
Variant B: VWAP slope confirmation — only enter if VWAP has risen over the last 5 bars

Compares both against current backfill (gap filter + ATR sizing).

Run: venv/bin/python3 test_vwap.py
"""

import json
import os
import statistics
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
GAP_FILTER     = 0.04
ATR_DAYS       = 14
ATR_MIN_MOD    = 0.40
ATR_MAX_MOD    = 1.50
VWAP_SLOPE_BARS = 5   # VWAP must be higher now than N bars ago

ALLOC_BULL = {"TAKE": 1750.0, "MAYBE": 1000.0}
ALLOC_NEUT = {"TAKE": 1500.0, "MAYBE":  750.0}
ALLOC_BEAR = {"TAKE":  500.0, "MAYBE":  500.0}


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


def calc_atr_pct(bars):
    if len(bars) < 2:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-ATR_DAYS:]) / min(len(trs), ATR_DAYS)
    return atr / bars[-1].close if bars[-1].close else None


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
    if len(closes_so_far) < 2 or vol_ratio < 1.0:
        return "SKIP", vol_ratio
    day_open   = closes_so_far[0]
    day_change = (closes_so_far[-1] - day_open) / day_open if day_open else 0
    if day_change < -0.02 and vol_ratio < 2.0:
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
            return price, "TIME_CLOSE"
        if price >= entry_price * (1 + TAKE_PROFIT):
            return price, "TAKE_PROFIT"
        if peak >= entry_price * (1 + TRAIL_LOCK) and price <= peak * (1 - TRAIL_STOP):
            return price, "TRAILING_STOP"
        if price <= entry_price * (1 - STOP_LOSS):
            return price, "STOP_LOSS"
    return closes[-1], "EOD"


def find_all_trades(closes, highs, lows, volumes, times, skip_orb, vwap_mode):
    """
    vwap_mode: "off" = no VWAP entries, "slope" = only if VWAP rising, "on" = current behaviour
    """
    if len(closes) <= ORB_BARS:
        return []
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    vwap     = calc_vwap(highs, lows, closes, volumes)
    orb_high = max(closes[:ORB_BARS])
    orb_used = skip_orb
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

        if not entry and vwap_mode != "off":
            for i in range(max(next_bar, ORB_BARS + 3), len(closes)):
                if times[i] > ENTRY_CLOSE:
                    break
                was_below = all(closes[i-k] < vwap[i-k] for k in range(1, 4))
                cross_up  = closes[i] > vwap[i]
                if was_below and cross_up and volumes[i] >= avg_vol * 1.5:
                    rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
                    if rating != "SKIP":
                        if vwap_mode == "slope":
                            # VWAP must be higher now than VWAP_SLOPE_BARS ago
                            if i >= VWAP_SLOPE_BARS and vwap[i] <= vwap[i - VWAP_SLOPE_BARS]:
                                continue
                        entry = {"bar": i, "time": times[i], "price": closes[i],
                                 "rating": rating, "signal": "VWAP"}
                        break

        if not entry:
            break

        exit_p, exit_r = find_exit(closes, times, entry["price"], entry["bar"])
        trades.append((entry, {"price": exit_p, "reason": exit_r}))

        if exit_r in ("STOP_LOSS", "TRAILING_STOP"):
            next_bar = entry["bar"] + COOLDOWN
        else:
            next_bar = entry["bar"] + 1 + (0 if exit_r not in ("TIME_CLOSE", "EOD") else 9999)

        if exit_r in ("TIME_CLOSE", "EOD"):
            break

    return trades


def base_alloc(market_state, rating):
    if market_state == "bullish": return ALLOC_BULL[rating]
    if market_state == "bearish": return ALLOC_BEAR[rating]
    return ALLOC_NEUT[rating]


def simulate_day(ticker_data, market_state, atr_modifiers, vwap_mode):
    cash          = BUDGET
    day_pnl       = 0.0
    day_limit_hit = False

    for ticker, closes, highs, lows, volumes, times, prior_close in ticker_data:
        if day_limit_hit:
            break

        gap_pct  = 0.0
        skip_orb = False
        if prior_close and closes:
            gap_pct  = (closes[0] - prior_close) / prior_close
            skip_orb = abs(gap_pct) > GAP_FILTER

        trades = find_all_trades(closes, highs, lows, volumes, times, skip_orb, vwap_mode)
        if not trades:
            continue

        modifier = atr_modifiers.get(ticker, 1.0)

        for trade_num, (entry, exit_) in enumerate(trades, 1):
            if day_limit_hit:
                break
            if trade_num > 1 and market_state != "bullish":
                continue

            alloc = round(base_alloc(market_state, entry["rating"]) * modifier, 2)
            alloc = min(alloc, cash)
            if alloc < 50:
                continue

            cash    -= alloc
            pnl      = round((exit_["price"] - entry["price"]) / entry["price"] * alloc, 2)
            cash    += alloc + pnl
            day_pnl += pnl

            if round(day_pnl, 2) <= DAY_LOSS_LIMIT:
                day_limit_hit = True
                break

    return round(day_pnl, 2)


def run():
    print("Loading current backfill (gap filter + ATR sizing)...")
    with open(os.path.join(BASE_DIR, "backfill.json")) as f:
        backfill = json.load(f)

    ex1_days = sorted(
        [e for e in backfill if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )
    baseline = {e["date"]: e["total_pnl"] for e in ex1_days}
    ms_map   = {e["date"]: e.get("market_state", "neutral") for e in ex1_days}
    dates    = [e["date"] for e in ex1_days]

    print(f"Found {len(dates)} days. Fetching data...\n")

    key, secret = _load_creds()
    client      = StockHistoricalDataClient(api_key=key, secret_key=secret)

    results_off   = {}
    results_slope = {}

    for date in dates:
        print(f"  {date}...", end="", flush=True)
        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt   = start_dt + timedelta(days=1)

        daily = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=TICKERS, timeframe=TimeFrame.Day,
            start=start_dt - timedelta(days=21), end=start_dt, feed="iex",
        ))

        prior_closes = {}
        atr_pcts     = {}
        for ticker in TICKERS:
            bars = daily.data.get(ticker, [])
            if bars:
                prior_closes[ticker] = bars[-1].close
                val = calc_atr_pct(bars)
                if val:
                    atr_pcts[ticker] = val

        if atr_pcts:
            med = statistics.median(atr_pcts.values())
            atr_modifiers = {
                t: round(min(ATR_MAX_MOD, max(ATR_MIN_MOD, med / atr_pcts[t])), 3)
                for t in atr_pcts
            }
        else:
            atr_modifiers = {t: 1.0 for t in TICKERS}

        intraday = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=TICKERS, timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))

        ticker_data = []
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
                ticker_data.append((ticker, closes, highs, lows, volumes, times,
                                    prior_closes.get(ticker)))
            except Exception:
                pass

        pnl_off   = simulate_day(ticker_data, ms_map[date], atr_modifiers, "off")
        pnl_slope = simulate_day(ticker_data, ms_map[date], atr_modifiers, "slope")
        results_off[date]   = pnl_off
        results_slope[date] = pnl_slope
        print(f" off={pnl_off:+.2f}  slope={pnl_slope:+.2f}  base={baseline[date]:+.2f}")

    # --- Results table ---
    print(f"\n{'='*72}")
    print(f"  {'Date':<12} {'Baseline':>10} {'VWAP Off':>10} {'VWAP Slope':>12} {'Off Diff':>9} {'Slope Diff':>11}")
    print(f"  {'-'*66}")

    cum_base = cum_off = cum_slope = 0.0
    for date in dates:
        b = baseline[date]
        o = results_off[date]
        s = results_slope[date]
        cum_base  += b
        cum_off   += o
        cum_slope += s
        print(f"  {date:<12} {b:>+9.2f}  {o:>+9.2f}  {s:>+11.2f}  {o-b:>+8.2f}  {s-b:>+10.2f}")

    print(f"  {'-'*66}")
    print(f"  {'TOTAL':<12} {cum_base:>+9.2f}  {cum_off:>+9.2f}  {cum_slope:>+11.2f}  {cum_off-cum_base:>+8.2f}  {cum_slope-cum_base:>+10.2f}")

    b_w = sum(1 for d in dates if baseline[d] > 0)
    o_w = sum(1 for d in dates if results_off[d] > 0)
    s_w = sum(1 for d in dates if results_slope[d] > 0)
    n   = len(dates)
    print(f"\n  Baseline:    {b_w}W/{n-b_w}L   ${cum_base:+.2f}")
    print(f"  VWAP Off:    {o_w}W/{n-o_w}L   ${cum_off:+.2f}")
    print(f"  VWAP Slope:  {s_w}W/{n-s_w}L   ${cum_slope:+.2f}")

    # How many VWAP entries does slope filter block?
    print(f"\n  (Baseline uses {68} VWAP entries; slope filter reduces that count)")
    print()


if __name__ == "__main__":
    run()
