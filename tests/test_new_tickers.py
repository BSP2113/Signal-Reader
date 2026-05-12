"""
test_new_tickers.py — compare current pool (MSTR + ETHA) vs new pool (APP + RIVN + CRWD + BBAI)

Keeps the original 8 tickers identical. Only swaps the last 2 slots:
  Current:  ...NFLX, MSTR, ETHA
  New:      ...NFLX, APP, RIVN, CRWD, BBAI  (4 new instead of 2)

Runs each day from the existing backfill, shows per-day and cumulative P&L.

Run: venv/bin/python3 test_new_tickers.py
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

CORE_TICKERS = ["NVDA", "TSLA", "AMD", "COIN", "META", "PLTR", "SMCI", "NFLX"]
OLD_ADD      = ["MSTR", "ETHA"]
NEW_ADD      = ["APP", "RIVN", "CRWD", "BBAI"]

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
STREAK_TRIGGER   = 2
MAYBE_STREAK_CUT = 0.50
DRAWDOWN_WINDOW    = 5
DRAWDOWN_THRESHOLD = 0.015
DRAWDOWN_CUT       = 0.50

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


def find_orb_entry(closes, highs, lows, volumes, times, skip_orb, spy_by_time):
    if len(closes) <= ORB_BARS or skip_orb:
        return None
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    orb_high = max(closes[:ORB_BARS])
    day_open = closes[0] if closes else None

    for i in range(ORB_BARS, len(closes)):
        if times[i] > ORB_CUTOFF:
            break
        if closes[i] > orb_high:
            rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
            if rating != "SKIP":
                if spy_by_time and day_open:
                    ticker_chg = (closes[i] - day_open) / day_open
                    spy_times  = sorted(t for t in spy_by_time if t <= times[i])
                    if spy_times:
                        spy_open = spy_by_time[spy_times[0]]
                        spy_now  = spy_by_time[spy_times[-1]]
                        spy_chg  = (spy_now - spy_open) / spy_open if spy_open else 0
                        if ticker_chg <= spy_chg:
                            return None
                return {"bar": i, "time": times[i], "price": closes[i],
                        "rating": rating, "vol_ratio": round(vr, 2)}
    return None


def base_alloc(market_state, rating):
    if market_state == "bullish": return ALLOC_BULL[rating]
    if market_state == "bearish": return ALLOC_BEAR[rating]
    return ALLOC_NEUT[rating]


def portfolio_drawdown(dates_so_far, results_so_far):
    if not results_so_far:
        return False
    portfolio   = BUDGET
    port_values = []
    for d in dates_so_far:
        portfolio += results_so_far[d]
        port_values.append(portfolio)
    current = port_values[-1]
    peak    = max(port_values[-DRAWDOWN_WINDOW:])
    return current < peak * (1 - DRAWDOWN_THRESHOLD)


def simulate_day(ticker_data, market_state, atr_modifiers, loss_streak,
                 spy_by_time, in_drawdown):
    in_streak     = loss_streak >= STREAK_TRIGGER
    cash          = BUDGET
    day_pnl       = 0.0
    day_limit_hit = False

    for ticker, closes, highs, lows, volumes, times, prior_close in ticker_data:
        if day_limit_hit:
            break

        skip_orb = False
        if prior_close and closes:
            gap_pct  = (closes[0] - prior_close) / prior_close
            skip_orb = abs(gap_pct) > GAP_FILTER

        entry = find_orb_entry(closes, highs, lows, volumes, times, skip_orb, spy_by_time)
        if not entry:
            continue

        modifier = atr_modifiers.get(ticker, 1.0)
        alloc    = round(base_alloc(market_state, entry["rating"]) * modifier, 2)
        if in_streak and entry["rating"] == "MAYBE":
            alloc = round(alloc * MAYBE_STREAK_CUT, 2)
        if in_drawdown:
            alloc = round(alloc * DRAWDOWN_CUT, 2)
        alloc = min(alloc, cash)
        if alloc < 50:
            continue

        cash  -= alloc
        exit_p, _ = find_exit(closes, times, entry["price"], entry["bar"])
        pnl    = round((exit_p - entry["price"]) / entry["price"] * alloc, 2)
        cash  += alloc + pnl
        day_pnl += pnl

        if round(day_pnl, 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True

    return round(day_pnl, 2)


def run():
    print("Loading backfill...")
    with open(os.path.join(BASE_DIR, "backfill.json")) as f:
        backfill = json.load(f)

    ex1_days = sorted(
        [e for e in backfill if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )
    baseline = {e["date"]: e["total_pnl"] for e in ex1_days}
    ms_map   = {e["date"]: e.get("market_state", "neutral") for e in ex1_days}
    dates    = [e["date"] for e in ex1_days]

    print(f"Found {len(dates)} days.\n")
    print(f"  Current pool:  {CORE_TICKERS + OLD_ADD}")
    print(f"  New pool:      {CORE_TICKERS + NEW_ADD}\n")

    key, secret = _load_creds()
    client      = StockHistoricalDataClient(api_key=key, secret_key=secret)

    all_tickers     = list(dict.fromkeys(CORE_TICKERS + OLD_ADD + NEW_ADD))
    results_current = {}
    results_new     = {}
    loss_streak_cur = 0
    loss_streak_new = 0
    completed_cur   = []
    completed_new   = []

    for date in dates:
        print(f"  {date}...", end="", flush=True)
        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt   = start_dt + timedelta(days=1)

        # Daily bars for ATR + prior close
        daily = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=all_tickers, timeframe=TimeFrame.Day,
            start=start_dt - timedelta(days=21), end=start_dt, feed="iex",
        ))

        prior_closes = {}
        atr_pcts_all = {}
        for ticker in all_tickers:
            bars = daily.data.get(ticker, [])
            if bars:
                prior_closes[ticker] = bars[-1].close
                val = calc_atr_pct(bars)
                if val:
                    atr_pcts_all[ticker] = val

        def make_modifiers(tickers):
            atr_pcts = {t: atr_pcts_all[t] for t in tickers if t in atr_pcts_all}
            if not atr_pcts:
                return {t: 1.0 for t in tickers}
            med = statistics.median(atr_pcts.values())
            return {
                t: round(min(ATR_MAX_MOD, max(ATR_MIN_MOD, med / atr_pcts[t])), 3)
                for t in atr_pcts
            }

        # Intraday bars
        intraday = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=all_tickers + ["SPY"], timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))

        spy_by_time = {}
        try:
            df_spy = intraday.df
            if isinstance(df_spy.index, pd.MultiIndex):
                df_spy = df_spy.xs("SPY", level=0)
            df_spy    = df_spy.tz_convert(ET)
            spy_today = df_spy.between_time("09:30", "15:59")
            for t, row in spy_today.iterrows():
                spy_by_time[t.strftime("%H:%M")] = row["close"]
        except Exception:
            pass

        def build_ticker_data(tickers):
            data = []
            for ticker in tickers:
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
                    data.append((ticker, closes, highs, lows, volumes, times,
                                 prior_closes.get(ticker)))
                except Exception:
                    pass
            return data

        cur_tickers = CORE_TICKERS + OLD_ADD
        new_tickers = CORE_TICKERS + NEW_ADD

        in_drawdown_cur = portfolio_drawdown(completed_cur, results_current)
        in_drawdown_new = portfolio_drawdown(completed_new, results_new)

        pnl_cur = simulate_day(build_ticker_data(cur_tickers), ms_map[date],
                               make_modifiers(cur_tickers), loss_streak_cur,
                               spy_by_time, in_drawdown_cur)
        pnl_new = simulate_day(build_ticker_data(new_tickers), ms_map[date],
                               make_modifiers(new_tickers), loss_streak_new,
                               spy_by_time, in_drawdown_new)

        results_current[date] = pnl_cur
        results_new[date]     = pnl_new
        loss_streak_cur = loss_streak_cur + 1 if pnl_cur < 0 else 0
        loss_streak_new = loss_streak_new + 1 if pnl_new < 0 else 0
        completed_cur.append(date)
        completed_new.append(date)

        diff = pnl_new - pnl_cur
        flag = f"  ({diff:+.2f})" if abs(diff) > 0.01 else ""
        print(f" cur {pnl_cur:+.2f}  new {pnl_new:+.2f}{flag}")

    # --- Summary table ---
    print(f"\n{'='*72}")
    print(f"  {'Date':<12} {'Current (MSTR+ETHA)':>20} {'New (APP+RIVN+CRWD+BBAI)':>26} {'Diff':>8}")
    print(f"  {'-'*62}")

    cum_cur = cum_new = 0.0
    for date in dates:
        c = results_current[date]
        n = results_new[date]
        cum_cur += c
        cum_new += n
        flag = " <" if abs(n - c) > 1.00 else ""
        print(f"  {date:<12} {c:>+19.2f}  {n:>+25.2f}  {n-c:>+7.2f}{flag}")

    print(f"  {'-'*62}")
    print(f"  {'TOTAL':<12} {cum_cur:>+19.2f}  {cum_new:>+25.2f}  {cum_new-cum_cur:>+7.2f}")

    c_w = sum(1 for d in dates if results_current[d] > 0)
    n_w = sum(1 for d in dates if results_new[d] > 0)
    nd  = len(dates)
    print(f"\n  Current (MSTR+ETHA):          {c_w}W/{nd-c_w}L   ${cum_cur:+.2f}")
    print(f"  New     (APP+RIVN+CRWD+BBAI): {n_w}W/{nd-n_w}L   ${cum_new:+.2f}")
    print()


if __name__ == "__main__":
    run()
