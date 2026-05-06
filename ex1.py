"""
ex1.py — Exercise 1: Multi-trade, $5,000

Entry logic:
  ORB only — first close above the 9:30–9:44 high, before 11:30am
  VWAP cross entries were tested and removed (net -$88 drag over 39 days)

Exit logic (first condition wins):
  1. Take profit   — exit when price >= entry * 1.03  (+3%)
  2. Trailing stop — exit when price <= peak * 0.980  (-2.0% from highest point, locks after +1%)
  3. Stop loss     — exit when price <= entry * 0.985  (-1.5% from entry)
  4. Time close    — exit any open position at 2:00pm

Run manually:  venv/bin/python3 ex1.py [YYYY-MM-DD]
Cron calls it: venv/bin/python3 ex1.py  (defaults to today)
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
DAY_LOSS_LIMIT = -75.0
GAP_FILTER          = 0.04   # skip ORB if ticker gaps >4% vs prior close
GAP_GO_THRESH       = 0.03   # positive gap >= 3% qualifies for gap-and-go
GAP_GO_WINDOW       = "09:39"  # scan only the first 10 minutes for gap-and-go
GAP_GO_SKIP_TICKERS = set()
ATR_DAYS    = 14     # lookback for ATR calculation
ATR_MIN_MOD    = 0.40   # never allocate below 40% of base
ATR_MAX_MOD    = 1.50   # never allocate above 150% of base
STREAK_TRIGGER = 2      # consecutive losing days before MAYBE reduction kicks in
MAYBE_STREAK_CUT = 0.50 # reduce MAYBE allocations to 50% during a losing streak
NO_PROGRESS_MINS    = 90   # exit flat/negative positions this many minutes after entry
EARLY_WEAK_MINS     = 45   # cut failing trades 45 min after entry
EARLY_WEAK_LOOKBACK = 5    # bars back to confirm still moving down
EARLY_WEAK_SKIP     = {"TSLA", "PLTR"}  # slow starters — excluded; monitor for revisit
DRAWDOWN_WINDOW    = 5    # rolling days for peak calculation
DRAWDOWN_THRESHOLD = 0.015 # 1.5% drop from rolling peak triggers size reduction
DRAWDOWN_CUT       = 0.50  # cut all allocations to 50% during drawdown
REALLOC_MIN_TIME    = "11:00"  # only reallocate after the morning ORB window
REALLOC_MAX_PNL_PCT = 0.5      # only sell positions currently below +0.5% gain
PM_ORB_RANGE_START  = "12:00"  # afternoon consolidation range start
PM_ORB_RANGE_END    = "12:44"  # afternoon consolidation range end
PM_ORB_CUTOFF       = "13:30"  # latest allowed PM_ORB entry
PM_ORB_MIN_BARS     = 10       # minimum bars in range to form valid level
PM_ORB_TAKE_FLOOR   = 2.0      # minimum vol ratio vs PM window avg to earn TAKE; 1.5x earns MAYBE
SPY_BULL       =  0.004   # premarket gap > +0.4% = bullish (matches market_check.py)
SPY_BEAR       = -0.005   # premarket gap < -0.5% = bearish
VIXY_SURGE     =  0.03    # VIXY up >3% = bearish weight
ALLOC_PCT_BULL = {"TAKE": 0.35, "MAYBE": 0.20}
ALLOC_PCT_NEUT = {"TAKE": 0.30, "MAYBE": 0.15}
ALLOC_PCT_BEAR = {"TAKE": 0.10, "MAYBE": 0.10}
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
ET          = "America/New_York"


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


def get_wallet_balance(filename="exercises.json", before_date=None, title_prefix="Exercise 1"):
    """Return cumulative portfolio value: $5000 + P&L up to (not including) before_date."""
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return BUDGET
    with open(path) as f:
        data = json.load(f)
    ex1 = [e for e in data if title_prefix in e["title"]]
    if before_date:
        ex1 = [e for e in ex1 if e["date"] < before_date]
    return round(BUDGET + sum(e["total_pnl"] for e in ex1), 2)


def loss_streak_count(trade_date, filename="backfill.json", title_prefix="Exercise 1"):
    """Return number of consecutive losing days immediately before trade_date."""
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        data = json.load(f)
    past = sorted(
        [e for e in data if title_prefix in e["title"] and e["date"] < trade_date],
        key=lambda e: e["date"]
    )
    streak = 0
    for e in reversed(past):
        if e["total_pnl"] < 0:
            streak += 1
        else:
            break
    return streak


def drawdown_check(trade_date, filename="backfill.json", title_prefix="Exercise 1"):
    """Return True if portfolio is >1.5% below its rolling 5-day peak."""
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return False
    with open(path) as f:
        data = json.load(f)
    past = sorted(
        [e for e in data if title_prefix in e["title"] and e["date"] < trade_date],
        key=lambda e: e["date"]
    )
    if not past:
        return False
    portfolio = BUDGET
    port_values = []
    for e in past:
        portfolio += e["total_pnl"]
        port_values.append(portfolio)
    current = port_values[-1]
    peak    = max(port_values[-DRAWDOWN_WINDOW:])
    return current < peak * (1 - DRAWDOWN_THRESHOLD)


def calc_atr_pct(bars):
    """14-day ATR as a % of the latest close. bars = list of daily bar objects."""
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
    if len(closes_so_far) < 2:
        return "SKIP", vol_ratio

    day_open   = closes_so_far[0]
    day_change = (closes_so_far[-1] - day_open) / day_open if day_open else 0

    if day_change < -0.02 and vol_ratio < 2.0:
        return "SKIP", vol_ratio

    if vol_ratio < 1.0: return "SKIP", vol_ratio

    score  = 1 if vol_ratio >= 1.5 else 0
    recent = closes_so_far[-min(12, len(closes_so_far)):]
    flips  = sum(1 for j in range(1, len(recent) - 1)
                 if (recent[-j] - recent[-j-1]) * (recent[-j-1] - recent[-j-2]) < 0)
    score += 1 if flips < 3 else -1

    if score >= 2:   return "TAKE",  vol_ratio
    elif score >= 0: return "MAYBE", vol_ratio
    else:            return "SKIP",  vol_ratio


def find_exit(closes, times, entry_price, entry_bar, ticker=None):
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

        if times[i] >= ENTRY_CLOSE:
            return {"bar": i, "time": times[i], "price": price, "reason": "TIME_CLOSE"}
        if price >= entry_price * (1 + TAKE_PROFIT):
            return {"bar": i, "time": times[i], "price": price, "reason": "TAKE_PROFIT"}
        if trail_armed and price <= peak * (1 - TRAIL_STOP):
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


def _price_at(ticker, hhmm, ticker_data):
    """Return the latest 1-min close at or before hhmm for ticker."""
    td     = ticker_data.get(ticker, {})
    times  = td.get("times", [])
    closes = td.get("closes", [])
    for i in range(len(times) - 1, -1, -1):
        if times[i] <= hhmm:
            return closes[i]
    return closes[0] if closes else 0.0


def find_all_trades(closes, highs, lows, volumes, times, skip_orb=False, spy_by_time=None, gap_pct=0.0, ticker=None):
    """Return ORB or GAP_GO (entry, exit) pair for the day if signal fires."""
    if len(closes) <= ORB_BARS:
        return []

    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    orb_high = max(closes[:ORB_BARS])
    day_open = closes[0]

    # Gap-and-go: positive gap >= 3%, scan first 10 min for close above opening bar's high
    if gap_pct >= GAP_GO_THRESH and ticker not in GAP_GO_SKIP_TICKERS:
        open_bar_high = highs[0]
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
                    spy_times  = sorted(t for t in spy_by_time if t <= times[i])
                    if spy_times:
                        spy_open = spy_by_time[spy_times[0]]
                        spy_now  = spy_by_time[spy_times[-1]]
                        spy_chg  = (spy_now - spy_open) / spy_open if spy_open else 0
                        if ticker_chg <= spy_chg:
                            return []
                entry = {"bar": i, "time": times[i], "price": closes[i],
                         "rating": rating, "vol_ratio": round(vr, 1), "signal": "GAP_GO"}
                return [(entry, find_exit(closes, times, entry["price"], i, ticker=ticker))]
        return []

    if skip_orb:
        return []

    for i in range(ORB_BARS, len(closes)):
        if times[i] > ORB_CUTOFF:
            break
        if closes[i] > orb_high:
            rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
            if rating != "SKIP":
                # Pre-10:00 ORB TAKE signals are 0/9 wins across 53 days (-$154).
                # Opening-range highs are set during the noisiest 15 min of the day;
                # first breakouts before 10am are crowded fakeouts, not real momentum.
                # MAYBE signals before 10:00 are unaffected (positive net across both datasets).
                if rating == "TAKE" and times[i] < "10:00":
                    continue
                if spy_by_time and day_open:
                    ticker_chg = (closes[i] - day_open) / day_open
                    spy_times  = sorted(t for t in spy_by_time if t <= times[i])
                    if spy_times:
                        spy_open = spy_by_time[spy_times[0]]
                        spy_now  = spy_by_time[spy_times[-1]]
                        spy_chg  = (spy_now - spy_open) / spy_open if spy_open else 0
                        if ticker_chg <= spy_chg:
                            return []   # not outperforming SPY — skip
                entry = {"bar": i, "time": times[i], "price": closes[i],
                         "rating": rating, "vol_ratio": round(vr, 1), "signal": "ORB"}
                exit_ = find_exit(closes, times, entry["price"], entry["bar"], ticker=ticker)
                return [(entry, exit_)]

    return []


def run_ex1(trade_date=None, backfill=False, save=True, result_file=None, title="Exercise 1 - Multi-trade"):
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    next_day = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"EX1 — {trade_date}")
    print("Fetching official closing prices...")

    key, secret = _load_creds()
    client   = StockHistoricalDataClient(api_key=key, secret_key=secret)
    start_dt = datetime.strptime(trade_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = datetime.strptime(next_day,   "%Y-%m-%d").replace(tzinfo=timezone.utc)

    daily_bars = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=TICKERS, timeframe=TimeFrame.Day,
        start=start_dt, end=end_dt, feed="iex",
    ))
    eod_prices = {}
    for t in TICKERS:
        try:
            symbol_bars = daily_bars.data.get(t, [])
            if symbol_bars:
                eod_prices[t] = round(symbol_bars[0].close, 2)
        except Exception:
            pass

    # Fetch prior daily bars — 21 calendar days gives ~14 trading days for ATR
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

    # ATR modifier: scale each ticker's allocation by median_atr / ticker_atr, clamped
    if len(atr_pcts) >= 2:
        median_atr   = _stats.median(atr_pcts.values())
        atr_modifier = {
            t: round(min(ATR_MAX_MOD, max(ATR_MIN_MOD, median_atr / atr_pcts[t])), 3)
            for t in atr_pcts
        }
    else:
        atr_modifier = {}

    print(f"  ATR modifiers: " +
          ", ".join(f"{t} {atr_modifier.get(t, 1.0):.2f}x" for t in TICKERS))

    # Read market state — live file for today, historical file for past dates
    state_path    = os.path.join(BASE_DIR, "market_state.json")
    hist_path     = os.path.join(BASE_DIR, "market_states_historical.json")
    market_state  = "neutral"
    spy_gap_pct   = 0.0
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

    # Classify using current thresholds (recomputed so changes to SPY_BULL take effect)
    if spy_gap_pct / 100 <= SPY_BEAR or vixy_trend_pct / 100 >= VIXY_SURGE:
        market_state = "bearish"
    elif spy_gap_pct / 100 >= SPY_BULL and vixy_trend_pct / 100 < VIXY_SURGE:
        market_state = "bullish"
    else:
        market_state = "neutral"
    tight_state = market_state  # tight_state now identical — single threshold

    print(f"  Market state: {market_state.upper()} (SPY {spy_gap_pct:+.2f}%, VIXY {vixy_trend_pct:+.2f}%)")

    def spy_alloc(rating):
        if market_state == "bullish": return round(starting_balance * ALLOC_PCT_BULL[rating], 2)
        if market_state == "bearish": return round(starting_balance * ALLOC_PCT_BEAR[rating], 2)
        return round(starting_balance * ALLOC_PCT_NEUT[rating], 2)

    # Fetch SPY intraday for relative strength gate
    spy_by_time = {}
    try:
        spy_intraday = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols="SPY", timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))
        df_spy = spy_intraday.df
        if isinstance(df_spy.index, pd.MultiIndex):
            df_spy = df_spy.xs("SPY", level=0)
        df_spy    = df_spy.tz_convert(ET)
        spy_today = df_spy.between_time("09:30", "15:59")
        for t, row in spy_today.iterrows():
            spy_by_time[t.strftime("%H:%M")] = row["close"]
    except Exception:
        pass

    # Losing streak check: reduce MAYBE allocations after 2+ consecutive losing days
    filename     = result_file or ("backfill.json" if backfill else "exercises.json")
    title_prefix = title.split(" - ")[0]
    streak       = loss_streak_count(trade_date, filename, title_prefix=title_prefix)
    in_streak    = streak >= STREAK_TRIGGER
    if in_streak:
        print(f"  Losing streak: {streak} consecutive losing days — MAYBE allocations reduced to 50%")

    # Drawdown check: cut all allocations 50% if portfolio >1.5% below rolling 5-day peak
    in_drawdown = drawdown_check(trade_date, filename, title_prefix=title_prefix)
    if in_drawdown:
        print(f"  Drawdown active — all allocations reduced to 50%")

    starting_balance = get_wallet_balance(filename, before_date=trade_date, title_prefix=title_prefix)
    print(f"  Wallet balance: ${starting_balance:,.2f}")
    entries       = []
    skipped       = []
    ticker_data   = {}   # ticker -> {closes, times} for reallocation price lookups

    # --- Phase 1: collect all potential trades for every ticker (no cash checking yet) ---
    potential = []
    for ticker in TICKERS:
        if ticker not in eod_prices:
            skipped.append(f"{ticker}(no data)")
            continue

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
        lows    = [round(float(v), 2) for v in today["low"].tolist()]
        volumes = [int(v) for v in today["volume"].tolist()]
        times   = [t.strftime("%H:%M") for t in today.index]
        ticker_data[ticker] = {"closes": closes, "times": times}

        # Gap filter: skip ORB if ticker gapped >4% vs prior close
        gap_pct  = 0.0
        skip_orb = False
        if ticker in prior_closes and prior_closes[ticker] and closes:
            gap_pct  = (closes[0] - prior_closes[ticker]) / prior_closes[ticker]
            skip_orb = abs(gap_pct) > GAP_FILTER
        if skip_orb:
            print(f"    Gap filter: {ticker} opened {gap_pct*100:+.1f}% vs prior close — ORB skipped")

        ticker_trades = find_all_trades(closes, highs, lows, volumes, times, skip_orb,
                                        spy_by_time=spy_by_time, gap_pct=gap_pct, ticker=ticker)

        modifier = atr_modifier.get(ticker, 1.0)

        if not ticker_trades:
            if gap_pct >= GAP_GO_THRESH and ticker not in GAP_GO_SKIP_TICKERS:
                reason = "gap-go-no-signal"
            elif skip_orb:
                reason = f"gap-{gap_pct*100:+.1f}%"
            else:
                reason = "no signal"
            skipped.append(f"{ticker}({reason})")
        else:
            for trade_num, (entry, exit_) in enumerate(ticker_trades, 1):
                if trade_num > 1 and market_state != "bullish":
                    skipped.append(f"{ticker}#{trade_num}(no-reentry-{market_state})")
                    continue

                alloc = round(spy_alloc(entry["rating"]) * modifier, 2)
                if in_streak and entry["rating"] == "MAYBE":
                    alloc = round(alloc * MAYBE_STREAK_CUT, 2)
                if in_drawdown:
                    alloc = round(alloc * DRAWDOWN_CUT, 2)

                pnl     = round((exit_["price"] - entry["price"]) / entry["price"] * alloc, 2)
                pnl_pct = round((exit_["price"] - entry["price"]) / entry["price"] * 100, 2)
                potential.append({
                    "ticker":      ticker,
                    "trade_num":   trade_num,
                    "action":      "BUY",
                    "signal":      entry["signal"],
                    "time":        entry["time"],
                    "exit_time":   exit_["time"],
                    "entry":       entry["price"],
                    "exit":        exit_["price"],
                    "exit_reason": exit_["reason"],
                    "eod":         eod_prices[ticker],
                    "allocated":   alloc,
                    "spy_state":   market_state,
                    "units":       round(alloc / entry["price"], 4),
                    "pnl":         pnl,
                    "pnl_pct":     pnl_pct,
                    "rating":      entry["rating"],
                    "vol_ratio":   entry["vol_ratio"],
                    "gap_pct":     round(gap_pct * 100, 2),
                    "atr_modifier": modifier,
                })

        # PM_ORB — always check, independent of morning signal
        pm = find_pm_orb(closes, volumes, times, ticker=ticker, spy_by_time=spy_by_time)
        if pm:
            pm_entry, pm_exit = pm
            pm_alloc = round(spy_alloc(pm_entry["rating"]) * modifier, 2)
            if in_streak and pm_entry["rating"] == "MAYBE":
                pm_alloc = round(pm_alloc * MAYBE_STREAK_CUT, 2)
            if in_drawdown:
                pm_alloc = round(pm_alloc * DRAWDOWN_CUT, 2)
            pm_pnl     = round((pm_exit["price"] - pm_entry["price"]) / pm_entry["price"] * pm_alloc, 2)
            pm_pnl_pct = round((pm_exit["price"] - pm_entry["price"]) / pm_entry["price"] * 100, 2)
            print(f"    PM_ORB signal: {pm_entry['time']} {pm_entry['rating']} {pm_entry['vol_ratio']}x")
            potential.append({
                "ticker":      ticker,
                "trade_num":   len(ticker_trades) + 1,
                "action":      "BUY",
                "signal":      "PM_ORB",
                "time":        pm_entry["time"],
                "exit_time":   pm_exit["time"],
                "entry":       pm_entry["price"],
                "exit":        pm_exit["price"],
                "exit_reason": pm_exit["reason"],
                "eod":         eod_prices[ticker],
                "allocated":   pm_alloc,
                "spy_state":   market_state,
                "units":       round(pm_alloc / pm_entry["price"], 4),
                "pnl":         pm_pnl,
                "pnl_pct":     pm_pnl_pct,
                "rating":      pm_entry["rating"],
                "vol_ratio":   pm_entry["vol_ratio"],
                "gap_pct":     round(gap_pct * 100, 2),
                "atr_modifier": modifier,
            })

    # --- Phase 2: simulate chronologically so concurrent positions share the same capital ---
    # Sort by entry time so earlier entries get first claim on capital
    potential.sort(key=lambda t: t["time"])
    active        = []   # {exit_time, allocated, ticker, entry_idx}
    day_limit_hit = False

    for trade in potential:
        if day_limit_hit:
            skipped.append(f"{trade['ticker']}#{trade['trade_num']}(day-limit)")
            continue

        # Release capital from any positions that exited before this entry
        active = [a for a in active if a["exit_time"] > trade["time"]]
        deployed  = sum(a["allocated"] for a in active)
        available = starting_balance - deployed

        if available < trade["allocated"]:
            # Reallocation: if this is a TAKE signal after the morning window,
            # sell the worst open position(s) to free up capital
            if trade["rating"] == "TAKE" and trade["time"] >= REALLOC_MIN_TIME and active:
                candidates = []
                for a in active:
                    curr     = _price_at(a["ticker"], trade["time"], ticker_data)
                    orig     = entries[a["entry_idx"]]["entry"]
                    curr_pct = (curr - orig) / orig * 100
                    if curr_pct < REALLOC_MAX_PNL_PCT:
                        candidates.append((curr_pct, a, curr))
                candidates.sort()  # worst PnL% first

                for _, worst_a, worst_price in candidates:
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

        entries.append(trade)
        active.append({"exit_time": trade["exit_time"], "allocated": trade["allocated"],
                       "ticker": trade["ticker"], "entry_idx": len(entries) - 1})

        if round(sum(e["pnl"] for e in entries), 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True

    total_pnl = round(sum(e["pnl"] for e in entries), 2)

    print(f"\n=== Results: {trade_date} ===\n")
    for e in entries:
        s = "+" if e["pnl"] >= 0 else ""
        print(f"  {e['ticker']:5s} #{e['trade_num']} | {e['time']} entry | "
              f"{e['exit_time']} exit ({e['exit_reason']}) | "
              f"{e['signal']:4s} | ${e['entry']:.2f} → ${e['exit']:.2f} | "
              f"{e['rating']} {e['vol_ratio']}x | {s}${e['pnl']:.2f} ({s}{e['pnl_pct']:.2f}%)")

    if skipped:
        print(f"\n  Skipped: {', '.join(skipped)}")

    print(f"\n  Trades: {len(entries)} | P&L: ${total_pnl:+.2f} | "
          f"Portfolio EOD: ${round(starting_balance + total_pnl, 2):.2f}")

    exercise = {
        "title":         title,
        "date":          trade_date,
        "starting_capital": starting_balance,
        "trades":        entries,
        "total_trades":  len(entries),
        "total_pnl":     total_pnl,
        "total_pnl_pct": round(total_pnl / starting_balance * 100, 2),
        "portfolio_eod": round(starting_balance + total_pnl, 2),
        "market_state":  market_state,
        "tight_state":   tight_state,
        "spy_gap_pct":   spy_gap_pct,
        "vixy_trend_pct": vixy_trend_pct,
        "loss_streak":   streak,
        "in_drawdown":   in_drawdown,
    }

    if not entries:
        print(f"\n  No trades — skipping save.")
        return exercise

    if not save:
        return exercise

    filename = result_file or ("backfill.json" if backfill else "exercises.json")
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
    backfill = "--backfill" in sys.argv
    pos_args = [a for a in sys.argv[1:] if not a.startswith("--")]
    date_arg = pos_args[0] if pos_args else None
    run_ex1(date_arg, backfill=backfill)
