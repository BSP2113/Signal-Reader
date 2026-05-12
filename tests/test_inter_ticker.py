"""
test_inter_ticker.py — test inter-ticker crowding filter vs current ORB-only baseline

Rule: if 4+ tickers fire ORB breakouts within any 15-minute window on the same day,
it's a market-wide move. Keep only the 2 highest-conviction entries (TAKE > MAYBE,
then by vol_ratio as tiebreaker). All other entries are skipped for that day.

Compares against current backfill (gap filter + ATR sizing + ORB only).

Run: venv/bin/python3 test_inter_ticker.py
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
DAY_LOSS_LIMIT = -75.0
GAP_FILTER     = 0.04
ATR_DAYS       = 14
ATR_MIN_MOD    = 0.40
ATR_MAX_MOD    = 1.50

CROWD_WINDOW   = 15   # minutes — window to detect crowded ORB
CROWD_THRESH   = 4    # if this many tickers fire within the window, it's crowded
CROWD_KEEP     = 2    # keep only this many entries on crowded days

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


def find_orb_entry(closes, highs, lows, volumes, times, skip_orb):
    if len(closes) <= ORB_BARS or skip_orb:
        return None
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    orb_high = max(closes[:ORB_BARS])
    for i in range(ORB_BARS, len(closes)):
        if times[i] > ORB_CUTOFF:
            break
        if closes[i] > orb_high:
            rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
            if rating != "SKIP":
                return {"bar": i, "time": times[i], "price": closes[i],
                        "rating": rating, "vol_ratio": round(vr, 2)}
    return None


def time_to_min(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def is_crowded(entries):
    """
    Returns True if CROWD_THRESH or more entries fire within CROWD_WINDOW minutes.
    entries: list of {"ticker": ..., "time": "HH:MM", ...}
    """
    if len(entries) < CROWD_THRESH:
        return False
    times = sorted(time_to_min(e["time"]) for e in entries)
    for i in range(len(times) - CROWD_THRESH + 1):
        if times[i + CROWD_THRESH - 1] - times[i] <= CROWD_WINDOW:
            return True
    return False


def top_entries(entries):
    """Rank by TAKE > MAYBE, then by vol_ratio. Return top CROWD_KEEP."""
    rating_rank = {"TAKE": 1, "MAYBE": 0}
    ranked = sorted(entries, key=lambda e: (rating_rank.get(e["rating"], 0), e["vol_ratio"]), reverse=True)
    return set(e["ticker"] for e in ranked[:CROWD_KEEP])


def base_alloc(market_state, rating):
    if market_state == "bullish": return ALLOC_BULL[rating]
    if market_state == "bearish": return ALLOC_BEAR[rating]
    return ALLOC_NEUT[rating]


def simulate_day(ticker_data, market_state, atr_modifiers, use_filter):
    # First pass: find all ORB entries
    all_entries = []
    ticker_map  = {}
    for ticker, closes, highs, lows, volumes, times, prior_close in ticker_data:
        skip_orb = False
        if prior_close and closes:
            gap_pct  = (closes[0] - prior_close) / prior_close
            skip_orb = abs(gap_pct) > GAP_FILTER
        entry = find_orb_entry(closes, highs, lows, volumes, times, skip_orb)
        if entry:
            entry["ticker"] = ticker
            all_entries.append(entry)
            ticker_map[ticker] = (closes, highs, lows, volumes, times, prior_close)

    if not all_entries:
        return 0.0, False, []

    crowded     = use_filter and is_crowded(all_entries)
    keep_set    = top_entries(all_entries) if crowded else {e["ticker"] for e in all_entries}

    cash          = BUDGET
    day_pnl       = 0.0
    day_limit_hit = False
    detail        = []

    for entry in all_entries:
        if day_limit_hit:
            break
        if entry["ticker"] not in keep_set:
            continue

        closes, highs, lows, volumes, times, _ = ticker_map[entry["ticker"]]
        modifier = atr_modifiers.get(entry["ticker"], 1.0)
        alloc    = round(base_alloc(market_state, entry["rating"]) * modifier, 2)
        alloc    = min(alloc, cash)
        if alloc < 50:
            continue

        cash -= alloc
        exit_p, exit_r = find_exit(closes, times, entry["price"], entry["bar"])
        pnl = round((exit_p - entry["price"]) / entry["price"] * alloc, 2)
        cash    += alloc + pnl
        day_pnl += pnl
        detail.append((entry["ticker"], entry["time"], entry["rating"], pnl))

        if round(day_pnl, 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True

    return round(day_pnl, 2), crowded, detail


def run():
    print("Loading current backfill (ORB-only baseline)...")
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

    results   = {}
    crowded_days = []

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

        pnl, crowded, detail = simulate_day(ticker_data, ms_map[date], atr_modifiers, use_filter=True)
        results[date] = pnl
        crowd_tag = " [CROWDED]" if crowded else ""
        print(f" {pnl:+.2f}  (base {baseline[date]:+.2f}){crowd_tag}")
        if crowded:
            crowded_days.append((date, baseline[date], pnl, detail))

    # --- Results table ---
    print(f"\n{'='*60}")
    print(f"  {'Date':<12} {'Baseline':>10} {'Filtered':>10} {'Diff':>8}")
    print(f"  {'-'*44}")

    cum_base = cum_filt = 0.0
    for date in dates:
        b = baseline[date]
        f = results[date]
        cum_base += b
        cum_filt += f
        flag = " <" if abs(f - b) > 0.50 else ""
        print(f"  {date:<12} {b:>+9.2f}  {f:>+9.2f}  {f-b:>+7.2f}{flag}")

    print(f"  {'-'*44}")
    print(f"  {'TOTAL':<12} {cum_base:>+9.2f}  {cum_filt:>+9.2f}  {cum_filt-cum_base:>+7.2f}")

    b_w = sum(1 for d in dates if baseline[d] > 0)
    f_w = sum(1 for d in dates if results[d] > 0)
    n   = len(dates)
    print(f"\n  Baseline: {b_w}W/{n-b_w}L   ${cum_base:+.2f}")
    print(f"  Filtered: {f_w}W/{n-f_w}L   ${cum_filt:+.2f}")
    print(f"\n  Crowded days detected: {len(crowded_days)}")

    if crowded_days:
        print(f"\n--- Crowded day detail ---")
        print(f"  {'Date':<12} {'Base':>8} {'Filt':>8}  Kept entries")
        print(f"  {'-'*52}")
        for date, b, f, detail in crowded_days:
            kept = ", ".join(f"{t}({r} {p:+.0f})" for t, _, r, p in detail)
            print(f"  {date:<12} {b:>+7.2f}  {f:>+7.2f}  {kept}")
    print()


if __name__ == "__main__":
    run()
