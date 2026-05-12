"""
ex2.py — Exercise 2: Re-entry + Afternoon Breakout, $5,000

Builds on Exercise 1. Same ORB entry logic, same exit rules.
Added: after a STOP_LOSS or TRAILING_STOP exit, looks for one re-entry
on the same ticker if price forms a new breakout before 13:30.
Added: afternoon breakout scan — from 13:00, watches all tickers for a
volume spike 50x+ morning average with close above morning high.

Re-entry rules:
  - Only after STOP_LOSS or TRAILING_STOP (not after TAKE_PROFIT or TIME_CLOSE)
  - Must be TAKE-rated — we already got stopped once, require strong confirmation
  - 5-bar consolidation window after exit before scanning for re-entry signal
  - Re-entry cutoff: 13:30 (extended from ORB 11:30 cutoff)
  - Allocation: 75% of original position size
  - Same exit rules apply (take profit 3%, trailing stop, stop loss 1.5%, time close 2pm)
  - SPY relative strength gate still applies to re-entries

Afternoon breakout rules:
  - Scan all tickers from 13:00 onward
  - Trigger: first bar where close > morning high AND volume >= 50x morning avg
  - Always TAKE-rated; allocation = 75% of normal TAKE size * ATR modifier
  - SPY relative strength gate applies
  - Time close: 15:30 (instead of 14:00)

Run manually:  venv/bin/python3 ex2.py [YYYY-MM-DD]
Cron calls it: venv/bin/python3 ex2.py  (defaults to today)
"""

import json
import os
import sys
import statistics as _stats
import pandas as pd
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

TICKERS     = ["NVDA", "TSLA", "AMD", "COIN", "META", "PLTR", "SMCI", "CRDO", "IONQ", "RIVN", "DELL", "KOPN",
               "SHOP", "ASTS", "ARM", "DKNG", "UPST"]
BUDGET      = 5000.0
ORB_BARS    = 15
ORB_CUTOFF  = "11:30"
ENTRY_CLOSE = "14:00"
TAKE_PROFIT = 0.03
TRAIL_STOP  = 0.020
TRAIL_LOCK  = 0.01
STOP_LOSS   = 0.015
NO_PROGRESS_MINS    = 90   # exit flat/negative positions this many minutes after entry
EARLY_WEAK_MINS     = 45   # cut failing trades 45 min after entry
EARLY_WEAK_LOOKBACK = 5    # bars back to confirm still moving down
EARLY_WEAK_SKIP     = {"TSLA", "PLTR"}  # slow starters — excluded; monitor for revisit
DAY_LOSS_LIMIT = -75.0
GAP_FILTER          = 0.04
GAP_GO_THRESH       = 0.03   # positive gap >= 3% qualifies for gap-and-go
GAP_GO_WINDOW       = "09:39"  # scan only the first 10 minutes for gap-and-go
GAP_GO_SKIP_TICKERS = set()
ATR_DAYS       = 14
ATR_MIN_MOD    = 0.40
ATR_MAX_MOD    = 1.50
STREAK_TRIGGER   = 2
MAYBE_STREAK_CUT = 0.50
DRAWDOWN_WINDOW    = 5
DRAWDOWN_THRESHOLD = 0.015
DRAWDOWN_CUT       = 0.50
SPY_BULL       =  0.004   # premarket gap > +0.4% = bullish (matches market_check.py)
SPY_BEAR       = -0.005   # premarket gap < -0.5% = bearish
VIXY_SURGE     =  0.03    # VIXY up >3% = bearish weight
REALLOC_MIN_TIME    = "11:00"  # only reallocate after the morning ORB window
REALLOC_MAX_PNL_PCT = 0.5      # only sell positions currently below +0.5% gain
PM_ORB_RANGE_START  = "12:00"  # afternoon consolidation range start
PM_ORB_RANGE_END    = "12:44"  # afternoon consolidation range end
PM_ORB_CUTOFF       = "13:30"  # latest allowed PM_ORB entry
PM_ORB_MIN_BARS     = 10       # minimum bars in range to form valid level
PM_ORB_TAKE_FLOOR   = 2.0      # minimum vol ratio vs PM window avg to earn TAKE; 1.5x earns MAYBE
ALLOC_PCT_BULL = {"TAKE": 0.35, "MAYBE": 0.20}
ALLOC_PCT_NEUT = {"TAKE": 0.30, "MAYBE": 0.15}
ALLOC_PCT_BEAR = {"TAKE": 0.10, "MAYBE": 0.10}

TAKE_TRAIL_GATE    = "13:00"   # TAKE signals: trail exit blocked before this time
REENTRY_CUTOFF     = "13:30"   # how late we'll still attempt a re-entry
REENTRY_ALLOC_MULT = 0.75      # re-entry gets 75% of original allocation
REENTRY_SETTLE     = 5         # bars to wait after exit before scanning for re-entry

AFTERNOON_VOL_THRESH = 50      # volume must be 50x morning average to qualify
AFTERNOON_SCAN_START = "13:00" # begin scanning for afternoon breakouts
AFTERNOON_ALLOC_MULT = 0.75    # 75% of normal TAKE allocation
AFTERNOON_TIME_CLOSE = "15:30" # afternoon trades exit by 3:30pm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ET       = "America/New_York"


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


def get_wallet_balance(filename="exercises.json", before_date=None):
    """Return cumulative portfolio value: $5000 + EX2 P&L up to (not including) before_date."""
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return BUDGET
    with open(path) as f:
        data = json.load(f)
    ex2 = [e for e in data if "Exercise 2" in e["title"]]
    if before_date:
        ex2 = [e for e in ex2 if e["date"] < before_date]
    return round(BUDGET + sum(e["total_pnl"] for e in ex2), 2)


def loss_streak_count(trade_date, filename):
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        data = json.load(f)
    past = sorted(
        [e for e in data if "Exercise 2" in e["title"] and e["date"] < trade_date],
        key=lambda e: e["date"]
    )
    streak = 0
    for e in reversed(past):
        if e["total_pnl"] < 0:
            streak += 1
        else:
            break
    return streak


def drawdown_check(trade_date, filename):
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return False
    with open(path) as f:
        data = json.load(f)
    past = sorted(
        [e for e in data if "Exercise 2" in e["title"] and e["date"] < trade_date],
        key=lambda e: e["date"]
    )
    if not past:
        return False
    portfolio   = BUDGET
    port_values = []
    for e in past:
        portfolio += e["total_pnl"]
        port_values.append(portfolio)
    current = port_values[-1]
    peak    = max(port_values[-DRAWDOWN_WINDOW:])
    return current < peak * (1 - DRAWDOWN_THRESHOLD)


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


def find_exit(closes, times, entry_price, entry_bar, ticker=None, time_close=None, rating=None):
    if time_close is None:
        time_close = ENTRY_CLOSE
    peak         = entry_price
    consec_above = 0       # consecutive closes >= entry+1% (trail arm requires 2)
    trail_armed  = False
    lock_level   = entry_price * (1 + TRAIL_LOCK)
    entry_mins   = int(times[entry_bar][:2]) * 60 + int(times[entry_bar][3:])
    t90_mins     = entry_mins + NO_PROGRESS_MINS
    t90_passed   = False
    tew_mins     = entry_mins + EARLY_WEAK_MINS
    tew_passed   = False

    for i in range(entry_bar + 1, len(closes)):
        price    = closes[i]
        bar_mins = int(times[i][:2]) * 60 + int(times[i][3:])
        peak     = max(peak, price)

        # Require 2 consecutive closes above entry+1% before arming the trail.
        # Prevents single-bar spikes from triggering the trail lock prematurely.
        if price >= lock_level:
            consec_above += 1
        else:
            consec_above = 0
        if consec_above >= 2:
            trail_armed = True

        if times[i] >= time_close:
            return {"bar": i, "time": times[i], "price": price, "reason": "TIME_CLOSE"}
        if price >= entry_price * (1 + TAKE_PROFIT):
            return {"bar": i, "time": times[i], "price": price, "reason": "TAKE_PROFIT"}
        trail_gated = (rating == "TAKE" and times[i] < TAKE_TRAIL_GATE)
        if trail_armed and not trail_gated and price <= peak * (1 - TRAIL_STOP):
            return {"bar": i, "time": times[i], "price": price, "reason": "TRAILING_STOP"}
        if price <= entry_price * (1 - STOP_LOSS):
            return {"bar": i, "time": times[i], "price": price, "reason": "STOP_LOSS"}
        if not t90_passed and bar_mins >= t90_mins and t90_mins <= 14 * 60:
            t90_passed = True
            if price <= entry_price:
                return {"bar": i, "time": times[i], "price": price, "reason": "NO_PROGRESS"}
        if ticker not in EARLY_WEAK_SKIP and not tew_passed and bar_mins >= tew_mins:
            tew_passed = True
            if price < entry_price:
                lookback = max(entry_bar + 1, i - EARLY_WEAK_LOOKBACK)
                if price < closes[lookback]:
                    return {"bar": i, "time": times[i], "price": price, "reason": "EARLY_WEAK"}

    return {"bar": len(closes) - 1, "time": times[-1], "price": closes[-1], "reason": "EOD"}


def find_orb_entry(closes, volumes, times, spy_by_time, ticker=None):
    """Return (entry, exit) for the first valid ORB breakout, or None."""
    if len(closes) <= ORB_BARS:
        return None
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    orb_high = max(closes[:ORB_BARS])
    day_open = closes[0]

    for i in range(ORB_BARS, len(closes)):
        if times[i] > ORB_CUTOFF:
            break
        if closes[i] > orb_high:
            rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
            if rating == "SKIP":
                continue
            # Pre-10:00 ORB TAKE signals are 0/9 wins across 53 days (-$154).
            # Opening-range highs are set during the noisiest 15 min of the day;
            # first breakouts before 10am are crowded fakeouts, not real momentum.
            # MAYBE signals before 10:00 are unaffected (positive net across both datasets).
            if rating == "TAKE" and times[i] < "10:00":
                continue
            if spy_by_time and day_open:
                ticker_chg = (closes[i] - day_open) / day_open
                spy_ts     = sorted(t for t in spy_by_time if t <= times[i])
                if spy_ts:
                    spy_open = spy_by_time[spy_ts[0]]
                    spy_now  = spy_by_time[spy_ts[-1]]
                    spy_chg  = (spy_now - spy_open) / spy_open if spy_open else 0
                    if ticker_chg <= spy_chg:
                        return None
            entry = {"bar": i, "time": times[i], "price": closes[i],
                     "rating": rating, "vol_ratio": round(vr, 1), "signal": "ORB"}
            exit_ = find_exit(closes, times, entry["price"], entry["bar"], ticker=ticker, rating=rating)
            return (entry, exit_)
    return None


def find_gap_go_entry(closes, highs, volumes, times, spy_by_time, ticker=None):
    """Return (entry, exit) for a gap-and-go signal (first 10 min), or None."""
    if not closes:
        return None
    avg_vol       = sum(volumes) / len(volumes) if volumes else 1
    open_bar_high = highs[0]
    day_open      = closes[0]

    for i in range(1, len(closes)):
        if times[i] > GAP_GO_WINDOW:
            break
        if closes[i] > open_bar_high:
            vr = volumes[i] / avg_vol if avg_vol else 0
            if vr < 1.0:
                continue
            rating = "TAKE" if vr >= 1.5 else "MAYBE"
            if spy_by_time and day_open:
                ticker_chg = (closes[i] - day_open) / day_open
                spy_ts     = sorted(t for t in spy_by_time if t <= times[i])
                if spy_ts:
                    spy_open = spy_by_time[spy_ts[0]]
                    spy_now  = spy_by_time[spy_ts[-1]]
                    spy_chg  = (spy_now - spy_open) / spy_open if spy_open else 0
                    if ticker_chg <= spy_chg:
                        return None
            entry = {"bar": i, "time": times[i], "price": closes[i],
                     "rating": rating, "vol_ratio": round(vr, 1), "signal": "GAP_GO"}
            exit_ = find_exit(closes, times, entry["price"], entry["bar"], ticker=ticker, rating=rating)
            return (entry, exit_)
    return None


def find_reentry(closes, volumes, times, exit_bar, spy_by_time, day_open, ticker=None):
    """
    After a stop/trail exit, find one TAKE-rated re-entry.
    Waits REENTRY_SETTLE bars for consolidation, then watches for a close
    above the post-exit high with TAKE signal before 13:30.
    """
    settle_end = exit_bar + REENTRY_SETTLE
    if settle_end >= len(closes):
        return None

    avg_vol     = sum(volumes) / len(volumes) if volumes else 1
    settle_high = max(closes[exit_bar:settle_end + 1])

    for i in range(settle_end + 1, len(closes)):
        if times[i] > REENTRY_CUTOFF:
            break
        if closes[i] > settle_high:
            rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
            if rating != "TAKE":
                continue
            if spy_by_time and day_open:
                ticker_chg = (closes[i] - day_open) / day_open
                spy_ts     = sorted(t for t in spy_by_time if t <= times[i])
                if spy_ts:
                    spy_open = spy_by_time[spy_ts[0]]
                    spy_now  = spy_by_time[spy_ts[-1]]
                    spy_chg  = (spy_now - spy_open) / spy_open if spy_open else 0
                    if ticker_chg <= spy_chg:
                        return None
            entry = {"bar": i, "time": times[i], "price": closes[i],
                     "rating": rating, "vol_ratio": round(vr, 1), "signal": "REENTRY"}
            exit_ = find_exit(closes, times, entry["price"], entry["bar"], ticker=ticker, rating=rating)
            return (entry, exit_)
    return None


def find_pm_orb(closes, volumes, times, ticker=None, spy_by_time=None):
    """Post-lunch consolidation breakout: first close above 12:00–12:44 range high."""
    day_open = closes[0]

    pm_range_vols   = [volumes[i] for i in range(len(times))
                       if PM_ORB_RANGE_START <= times[i] <= PM_ORB_RANGE_END]
    pm_range_closes = [closes[i] for i in range(len(times))
                       if PM_ORB_RANGE_START <= times[i] <= PM_ORB_RANGE_END]
    if len(pm_range_closes) < PM_ORB_MIN_BARS:
        return None
    # Use PM consolidation window avg volume — full-day avg is biased by heavy morning
    # volume and would SKIP all lunchtime bars even when volume is elevated for the time.
    pm_avg_vol = sum(pm_range_vols) / len(pm_range_vols) if pm_range_vols else 1
    pm_high    = max(pm_range_closes)

    for i in range(len(times)):
        if times[i] <= PM_ORB_RANGE_END:
            continue
        if times[i] > PM_ORB_CUTOFF:
            break
        if closes[i] > pm_high:
            rating, vr = score_signal(closes[:i+1], volumes[i], pm_avg_vol)
            if rating == "SKIP":
                continue
            if spy_by_time and day_open:
                ticker_chg = (closes[i] - day_open) / day_open
                spy_times  = sorted(t for t in spy_by_time if t <= times[i])
                if spy_times:
                    spy_open = spy_by_time[spy_times[0]]
                    spy_now  = spy_by_time[spy_times[-1]]
                    spy_chg  = (spy_now - spy_open) / spy_open if spy_open else 0
                    if ticker_chg <= spy_chg:
                        continue  # was return None — keep scanning after first RS fail
            if rating == "TAKE" and vr < PM_ORB_TAKE_FLOOR:
                rating = "MAYBE"
            entry = {"bar": i, "time": times[i], "price": closes[i],
                     "rating": rating, "vol_ratio": round(vr, 1), "signal": "PM_ORB"}
            return (entry, find_exit(closes, times, entry["price"], i, ticker=ticker))

    return None


def _price_at(ticker, hhmm, ticker_cache):
    """Return the latest 1-min close at or before hhmm for ticker."""
    td     = ticker_cache.get(ticker, {})
    times  = td.get("times", [])
    closes = td.get("closes", [])
    for i in range(len(times) - 1, -1, -1):
        if times[i] <= hhmm:
            return closes[i]
    return closes[0] if closes else 0.0


def find_afternoon_entry(closes, highs, volumes, times, morning_high, morning_avg_vol, spy_by_time, day_open, ticker=None):
    """
    From 13:00 onward, find first bar where close > morning high and volume >= 50x morning avg.
    Always TAKE-rated. Time close is 15:30 instead of the normal 14:00.
    """
    if not closes or morning_avg_vol < 1:
        return None
    for i, t in enumerate(times):
        if t < AFTERNOON_SCAN_START:
            continue
        if closes[i] <= morning_high:
            continue
        vol_ratio = volumes[i] / morning_avg_vol
        if vol_ratio < AFTERNOON_VOL_THRESH:
            continue
        if spy_by_time and day_open:
            ticker_chg = (closes[i] - day_open) / day_open
            spy_ts     = sorted(s for s in spy_by_time if s <= times[i])
            if spy_ts:
                spy_open = spy_by_time[spy_ts[0]]
                spy_now  = spy_by_time[spy_ts[-1]]
                spy_chg  = (spy_now - spy_open) / spy_open if spy_open else 0
                if ticker_chg <= spy_chg:
                    return None
        entry = {"bar": i, "time": times[i], "price": closes[i],
                 "rating": "TAKE", "vol_ratio": round(vol_ratio, 1), "signal": "AFTERNOON"}
        exit_ = find_exit(closes, times, entry["price"], entry["bar"], ticker=ticker,
                          time_close=AFTERNOON_TIME_CLOSE, rating="TAKE")
        return (entry, exit_)
    return None


def run_ex2(trade_date=None, backfill=False, result_file=None, save=True, pm_realloc_mode=0):
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    next_day = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"EX2 — {trade_date}")

    key, secret = _load_creds()
    client   = StockHistoricalDataClient(api_key=key, secret_key=secret)
    start_dt = datetime.strptime(trade_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = datetime.strptime(next_day,   "%Y-%m-%d").replace(tzinfo=timezone.utc)

    prior_daily = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=TICKERS, timeframe=TimeFrame.Day,
        start=start_dt - timedelta(days=21), end=start_dt, feed="iex",
    ))
    prior_closes = {}
    atr_pcts     = {}
    for t in TICKERS:
        bars = prior_daily.data.get(t, [])
        if bars:
            prior_closes[t] = bars[-1].close
            val = calc_atr_pct(bars)
            if val:
                atr_pcts[t] = val

    if len(atr_pcts) >= 2:
        med_atr      = _stats.median(atr_pcts.values())
        atr_modifier = {
            t: round(min(ATR_MAX_MOD, max(ATR_MIN_MOD, med_atr / atr_pcts[t])), 3)
            for t in atr_pcts
        }
    else:
        atr_modifier = {}

    state_path     = os.path.join(BASE_DIR, "market_state.json")
    hist_path      = os.path.join(BASE_DIR, "market_states_historical.json")
    market_state   = "neutral"
    spy_gap_pct    = 0.0
    vixy_trend_pct = 0.0

    if os.path.exists(state_path):
        with open(state_path) as f:
            ms = json.load(f)
        if ms.get("date") == trade_date:
            spy_gap_pct    = ms.get("spy_gap_pct", 0.0)
            vixy_trend_pct = ms.get("vixy_trend_pct", 0.0)

    if spy_gap_pct == 0.0 and os.path.exists(hist_path):
        with open(hist_path) as f:
            hist_map = {e["date"]: e for e in json.load(f)}
        if trade_date in hist_map:
            hd = hist_map[trade_date]
            spy_gap_pct    = hd.get("spy_gap_pct", 0.0)
            vixy_trend_pct = hd.get("vixy_trend_pct", 0.0)

    if spy_gap_pct / 100 <= SPY_BEAR or vixy_trend_pct / 100 >= VIXY_SURGE:
        market_state = "bearish"
    elif spy_gap_pct / 100 >= SPY_BULL and vixy_trend_pct / 100 < VIXY_SURGE:
        market_state = "bullish"
    else:
        market_state = "neutral"
    tight_state = market_state

    print(f"  Market state: {market_state.upper()} (SPY {spy_gap_pct:+.2f}%, VIXY {vixy_trend_pct:+.2f}%)")

    def base_alloc(rating):
        if market_state == "bullish": return round(starting_balance * ALLOC_PCT_BULL[rating], 2)
        if market_state == "bearish": return round(starting_balance * ALLOC_PCT_BEAR[rating], 2)
        return round(starting_balance * ALLOC_PCT_NEUT[rating], 2)

    spy_by_time = {}
    try:
        spy_bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols="SPY", timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))
        df_spy = spy_bars.df
        if isinstance(df_spy.index, pd.MultiIndex):
            df_spy = df_spy.xs("SPY", level=0)
        df_spy    = df_spy.tz_convert(ET)
        spy_today = df_spy.between_time("09:30", "15:59")
        for t, row in spy_today.iterrows():
            spy_by_time[t.strftime("%H:%M")] = row["close"]
    except Exception:
        pass

    filename    = result_file or ("backfill2.json" if backfill else "exercises.json")
    streak      = loss_streak_count(trade_date, filename)
    in_streak   = streak >= STREAK_TRIGGER
    in_drawdown = drawdown_check(trade_date, filename)

    if in_streak:
        print(f"  Losing streak: {streak} days — MAYBE allocations reduced to 50%")
    if in_drawdown:
        print(f"  Drawdown active — all allocations reduced to 50%")

    starting_balance = BUDGET if backfill else get_wallet_balance(filename, before_date=trade_date)
    if not backfill:
        print(f"  Wallet balance: ${starting_balance:,.2f}")

    potential    = []
    entries      = []
    skipped      = []
    ticker_cache = {}   # stores bar data for the afternoon scan

    for ticker in TICKERS:
        print(f"  Analyzing {ticker}...")

        intraday = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))
        df = intraday.df
        if df.empty:
            skipped.append(f"{ticker}(no data)")
            continue
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(ticker, level=0)
        df    = df.tz_convert(ET)
        today = df.between_time("09:30", "15:59")
        if today.empty:
            skipped.append(f"{ticker}(no data)")
            continue

        closes  = [round(float(v), 2) for v in today["close"].tolist()]
        highs   = [round(float(v), 2) for v in today["high"].tolist()]
        volumes = [int(v) for v in today["volume"].tolist()]
        times   = [t.strftime("%H:%M") for t in today.index]

        # Cache full bar data for afternoon scan (runs after main loop)
        morning_vols  = [v for v, t in zip(volumes, times) if t < AFTERNOON_SCAN_START]
        morning_highs = [h for h, t in zip(highs,   times) if t < AFTERNOON_SCAN_START]
        ticker_cache[ticker] = {
            "closes":           closes,
            "highs":            highs,
            "volumes":          volumes,
            "times":            times,
            "morning_high":     max(morning_highs) if morning_highs else 0,
            "morning_avg_vol":  sum(morning_vols) / len(morning_vols) if morning_vols else 0,
            "day_open":         closes[0] if closes else None,
        }

        gap_pct  = 0.0
        skip_orb = False
        if ticker in prior_closes and closes:
            gap_pct  = (closes[0] - prior_closes[ticker]) / prior_closes[ticker]
            skip_orb = abs(gap_pct) > GAP_FILTER

        # Gap-and-go: positive gap >= 3%, scan first 10 min (no re-entry for gap-and-go)
        if gap_pct >= GAP_GO_THRESH and ticker not in GAP_GO_SKIP_TICKERS:
            gag = find_gap_go_entry(closes, highs, volumes, times, spy_by_time, ticker=ticker)
            if not gag:
                skipped.append(f"{ticker}(gap-go-no-signal)")
                continue
            entry, exit_ = gag
            modifier = atr_modifier.get(ticker, 1.0)
            alloc    = round(base_alloc(entry["rating"]) * modifier, 2)
            if in_streak and entry["rating"] == "MAYBE":
                alloc = round(alloc * MAYBE_STREAK_CUT, 2)
            if in_drawdown:
                alloc = round(alloc * DRAWDOWN_CUT, 2)
            pnl = round((exit_["price"] - entry["price"]) / entry["price"] * alloc, 2)
            potential.append({
                "_id":         f"{ticker}#1",
                "_parent":     None,
                "ticker":      ticker,
                "trade_num":   1,
                "signal":      entry["signal"],
                "time":        entry["time"],
                "entry":       entry["price"],
                "exit":        exit_["price"],
                "exit_time":   exit_["time"],
                "exit_reason": exit_["reason"],
                "allocated":   alloc,
                "rating":      entry["rating"],
                "vol_ratio":   entry["vol_ratio"],
                "gap_pct":     round(gap_pct * 100, 2),
                "atr_modifier": modifier,
                "pnl":         pnl,
                "pnl_pct":     round((exit_["price"] - entry["price"]) / entry["price"] * 100, 2),
            })
            continue  # no re-entry on gap-and-go trades

        if skip_orb:
            print(f"    Gap filter: {gap_pct*100:+.1f}% — skipping")
            skipped.append(f"{ticker}(gap {gap_pct*100:+.1f}%)")
            continue

        # --- Original ORB entry ---
        orb = find_orb_entry(closes, volumes, times, spy_by_time, ticker=ticker)
        if not orb:
            skipped.append(f"{ticker}(no signal)")
            continue

        entry, exit_ = orb
        modifier = atr_modifier.get(ticker, 1.0)
        alloc    = round(base_alloc(entry["rating"]) * modifier, 2)
        if in_streak and entry["rating"] == "MAYBE":
            alloc = round(alloc * MAYBE_STREAK_CUT, 2)
        if in_drawdown:
            alloc = round(alloc * DRAWDOWN_CUT, 2)
        pnl = round((exit_["price"] - entry["price"]) / entry["price"] * alloc, 2)

        trade_id = f"{ticker}#1"
        potential.append({
            "_id":         trade_id,
            "_parent":     None,
            "ticker":      ticker,
            "trade_num":   1,
            "signal":      entry["signal"],
            "time":        entry["time"],
            "entry":       entry["price"],
            "exit":        exit_["price"],
            "exit_time":   exit_["time"],
            "exit_reason": exit_["reason"],
            "allocated":   alloc,
            "rating":      entry["rating"],
            "vol_ratio":   entry["vol_ratio"],
            "gap_pct":     round(gap_pct * 100, 2),
            "atr_modifier": modifier,
            "pnl":         pnl,
            "pnl_pct":     round((exit_["price"] - entry["price"]) / entry["price"] * 100, 2),
        })

        # --- Re-entry: collect only if stopped out ---
        if exit_["reason"] not in ("STOP_LOSS", "TRAILING_STOP"):
            continue

        re = find_reentry(closes, volumes, times, exit_["bar"], spy_by_time, closes[0], ticker=ticker)
        if not re:
            continue

        re_entry, re_exit = re
        re_alloc = round(alloc * REENTRY_ALLOC_MULT, 2)
        if in_drawdown:
            re_alloc = round(re_alloc * DRAWDOWN_CUT, 2)
        re_pnl = round((re_exit["price"] - re_entry["price"]) / re_entry["price"] * re_alloc, 2)

        potential.append({
            "_id":         f"{ticker}#2",
            "_parent":     trade_id,
            "ticker":      ticker,
            "trade_num":   2,
            "signal":      re_entry["signal"],
            "time":        re_entry["time"],
            "entry":       re_entry["price"],
            "exit":        re_exit["price"],
            "exit_time":   re_exit["time"],
            "exit_reason": re_exit["reason"],
            "allocated":   re_alloc,
            "rating":      re_entry["rating"],
            "vol_ratio":   re_entry["vol_ratio"],
            "gap_pct":     round(gap_pct * 100, 2),
            "atr_modifier": modifier,
            "pnl":         re_pnl,
            "pnl_pct":     round((re_exit["price"] - re_entry["price"]) / re_entry["price"] * 100, 2),
        })

    # --- Afternoon scans (all tickers, independent of morning trades) ---
    for ticker in TICKERS:
        if ticker not in ticker_cache:
            continue
        d        = ticker_cache[ticker]
        modifier = atr_modifier.get(ticker, 1.0)

        # Existing high-volume afternoon breakout
        af = find_afternoon_entry(
            d["closes"], d["highs"], d["volumes"], d["times"],
            d["morning_high"], d["morning_avg_vol"],
            spy_by_time, d["day_open"], ticker=ticker,
        )
        if af:
            af_entry, af_exit = af
            af_alloc  = round(base_alloc("TAKE") * modifier * AFTERNOON_ALLOC_MULT, 2)
            if in_drawdown:
                af_alloc = round(af_alloc * DRAWDOWN_CUT, 2)
            af_pnl    = round((af_exit["price"] - af_entry["price"]) / af_entry["price"] * af_alloc, 2)
            prior_cnt = sum(1 for p in potential if p["ticker"] == ticker)
            potential.append({
                "_id":         f"{ticker}#AF",
                "_parent":     None,
                "ticker":      ticker,
                "trade_num":   prior_cnt + 1,
                "signal":      "AFTERNOON",
                "time":        af_entry["time"],
                "entry":       af_entry["price"],
                "exit":        af_exit["price"],
                "exit_time":   af_exit["time"],
                "exit_reason": af_exit["reason"],
                "allocated":   af_alloc,
                "rating":      af_entry["rating"],
                "vol_ratio":   af_entry["vol_ratio"],
                "gap_pct":     0.0,
                "atr_modifier": modifier,
                "pnl":         af_pnl,
                "pnl_pct":     round((af_exit["price"] - af_entry["price"]) / af_entry["price"] * 100, 2),
            })

        # PM_ORB: post-lunch consolidation breakout
        pm = find_pm_orb(d["closes"], d["volumes"], d["times"],
                         ticker=ticker, spy_by_time=spy_by_time)
        if pm:
            pm_entry, pm_exit = pm
            pm_alloc = round(base_alloc(pm_entry["rating"]) * modifier, 2)
            if in_streak and pm_entry["rating"] == "MAYBE":
                pm_alloc = round(pm_alloc * MAYBE_STREAK_CUT, 2)
            if in_drawdown:
                pm_alloc = round(pm_alloc * DRAWDOWN_CUT, 2)
            pm_pnl     = round((pm_exit["price"] - pm_entry["price"]) / pm_entry["price"] * pm_alloc, 2)
            pm_pnl_pct = round((pm_exit["price"] - pm_entry["price"]) / pm_entry["price"] * 100, 2)
            prior_cnt  = sum(1 for p in potential if p["ticker"] == ticker)
            print(f"    PM_ORB signal: {pm_entry['time']} {pm_entry['rating']} {pm_entry['vol_ratio']}x")
            potential.append({
                "_id":         f"{ticker}#PM",
                "_parent":     None,
                "ticker":      ticker,
                "trade_num":   prior_cnt + 1,
                "signal":      "PM_ORB",
                "time":        pm_entry["time"],
                "entry":       pm_entry["price"],
                "exit":        pm_exit["price"],
                "exit_time":   pm_exit["time"],
                "exit_reason": pm_exit["reason"],
                "allocated":   pm_alloc,
                "rating":      pm_entry["rating"],
                "vol_ratio":   pm_entry["vol_ratio"],
                "gap_pct":     0.0,
                "atr_modifier": modifier,
                "pnl":         pm_pnl,
                "pnl_pct":     pm_pnl_pct,
            })

    # --- Phase 2: Chronological simulation with concurrent capital tracking ---
    potential.sort(key=lambda t: t["time"])
    active        = []   # {exit_time, allocated, ticker, entry_idx}
    executed_ids  = set()
    day_limit_hit = False

    for trade in potential:
        if day_limit_hit:
            skipped.append(f"{trade['ticker']}#{trade['trade_num']}(day-limit)")
            continue
        # Re-entry only executes if its parent first entry was executed
        if trade["_parent"] and trade["_parent"] not in executed_ids:
            skipped.append(f"{trade['ticker']}#2(parent-skipped)")
            continue
        # Release capital from positions that have already exited
        active    = [a for a in active if a["exit_time"] > trade["time"]]
        deployed  = sum(a["allocated"] for a in active)
        available = starting_balance - deployed
        if trade["allocated"] < 50:
            skipped.append(f"{trade['ticker']}#{trade['trade_num']}(insufficient cash)")
            continue
        if available < trade["allocated"]:
            # Determine if reallocation should fire for this trade
            is_pm_orb = trade["signal"] == "PM_ORB"
            std_realloc = trade["rating"] == "TAKE" and trade["time"] >= REALLOC_MIN_TIME
            # Mode A: MAYBE PM ORBs also trigger realloc (same +0.5% sell threshold)
            mode_a = pm_realloc_mode == 1 and is_pm_orb and trade["time"] >= REALLOC_MIN_TIME
            # Mode B: PM ORBs sell positions open 2+ hrs and below entry price
            mode_b = pm_realloc_mode == 2 and is_pm_orb and trade["time"] >= REALLOC_MIN_TIME
            # Mode C: PM ORBs sell positions below +2% gain (more aggressive threshold)
            mode_c = pm_realloc_mode == 3 and is_pm_orb and trade["time"] >= REALLOC_MIN_TIME

            if (std_realloc or mode_a or mode_b or mode_c) and active:
                candidates = []
                for a in active:
                    curr     = _price_at(a["ticker"], trade["time"], ticker_cache)
                    orig     = entries[a["entry_idx"]]["entry"]
                    curr_pct = (curr - orig) / orig * 100
                    if mode_b:
                        # Only sell positions open 2+ hours AND below entry
                        entry_time = entries[a["entry_idx"]]["time"]
                        eh, em = int(entry_time[:2]), int(entry_time[3:])
                        th, tm = int(trade["time"][:2]), int(trade["time"][3:])
                        open_mins = (th * 60 + tm) - (eh * 60 + em)
                        if open_mins >= 120 and curr_pct < 0:
                            candidates.append((curr_pct, a["entry_idx"], a, curr))
                    elif mode_c:
                        # Sell positions below +2% gain
                        if curr_pct < 2.0:
                            candidates.append((curr_pct, a["entry_idx"], a, curr))
                    else:
                        # Standard: sell positions below +0.5% gain
                        if curr_pct < REALLOC_MAX_PNL_PCT:
                            candidates.append((curr_pct, a["entry_idx"], a, curr))
                candidates.sort()  # worst PnL% first

                for _, _, worst_a, worst_price in candidates:
                    t = entries[worst_a["entry_idx"]]
                    t["exit"]        = worst_price
                    t["exit_time"]   = trade["time"]
                    t["exit_reason"] = "REALLOC"
                    t["pnl"]         = round((worst_price - t["entry"]) / t["entry"] * t["allocated"], 2)
                    t["pnl_pct"]     = round((worst_price - t["entry"]) / t["entry"] * 100, 2)
                    worst_a["exit_time"] = trade["time"]
                    active    = [a for a in active if a["exit_time"] > trade["time"]]
                    deployed  = sum(a["allocated"] for a in active)
                    available = starting_balance - deployed
                    print(f"    REALLOC: sold {t['ticker']} @ ${worst_price:.2f} "
                          f"({t['pnl_pct']:+.2f}%) to fund {trade['ticker']}")
                    if available >= trade["allocated"]:
                        break

            if available < trade["allocated"]:
                skipped.append(f"{trade['ticker']}#{trade['trade_num']}(budget)")
                continue
        entries.append({k: v for k, v in trade.items() if not k.startswith("_")})
        active.append({"exit_time": trade["exit_time"], "allocated": trade["allocated"],
                       "ticker": trade["ticker"], "entry_idx": len(entries) - 1})
        executed_ids.add(trade["_id"])
        if round(sum(e["pnl"] for e in entries), 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True

    total_pnl       = round(sum(e["pnl"] for e in entries), 2)
    reentries       = [e for e in entries if e.get("signal") == "REENTRY"]
    afternoon_trades = [e for e in entries if e.get("signal") == "AFTERNOON"]

    print(f"\n=== EX2 Results: {trade_date} ===\n")
    for e in entries:
        sig = e.get("signal", "")
        if sig == "REENTRY":
            tag = " [RE]"
        elif sig == "AFTERNOON":
            tag = " [AF]"
        else:
            tag = "     "
        s = "+" if e["pnl"] >= 0 else ""
        print(f"  {e['ticker']:5s}{tag} | {e['time']} → {e['exit_time']} ({e['exit_reason']:<16}) | "
              f"{e['rating']} {e['vol_ratio']}x | ${e['entry']:.2f} → ${e['exit']:.2f} | "
              f"{s}${e['pnl']:.2f}")

    if skipped:
        print(f"\n  Skipped: {', '.join(skipped)}")

    print(f"\n  Trades: {len(entries)} | Re-entries: {len(reentries)} | Afternoon: {len(afternoon_trades)} | "
          f"P&L: ${total_pnl:+.2f} | Portfolio EOD: ${round(starting_balance + total_pnl, 2):.2f}")
    if reentries:
        re_pnl_total = sum(e["pnl"] for e in reentries)
        print(f"  Re-entry contribution: ${re_pnl_total:+.2f}")
    if afternoon_trades:
        af_pnl_total = sum(e["pnl"] for e in afternoon_trades)
        print(f"  Afternoon contribution: ${af_pnl_total:+.2f}")

    exercise = {
        "title":            "Exercise 2 - Re-entry",
        "date":             trade_date,
        "starting_capital": starting_balance,
        "trades":           entries,
        "total_trades":     len(entries),
        "reentry_count":    len(reentries),
        "afternoon_count":  len(afternoon_trades),
        "total_pnl":        total_pnl,
        "total_pnl_pct":    round(total_pnl / starting_balance * 100, 2),
        "portfolio_eod":    round(starting_balance + total_pnl, 2),
        "market_state":     market_state,
        "tight_state":      tight_state,
        "spy_gap_pct":      spy_gap_pct,
        "vixy_trend_pct":   vixy_trend_pct,
        "loss_streak":      streak,
        "in_drawdown":      in_drawdown,
    }

    if not entries:
        print(f"\n  No trades — skipping save.")
        return exercise

    if not save:
        return exercise

    path     = os.path.join(BASE_DIR, filename)
    existing = []
    if os.path.exists(path):
        with open(path) as f:
            existing = json.load(f)
    existing = [e for e in existing if not (e["date"] == trade_date and e["title"] == exercise["title"])]
    existing.append(exercise)
    existing.sort(key=lambda e: (e["date"], e["title"]))
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"\n  Saved to {filename}")

    return exercise


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    backfill = "--backfill" in sys.argv
    run_ex2(date_arg, backfill=backfill)
