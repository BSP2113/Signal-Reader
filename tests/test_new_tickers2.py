"""
test_new_tickers2.py — test adding SHOP, SOFI, ARM, DKNG, RKLB, RDDT to the pool

Compares current 12-ticker EX2 (re-entry) results vs 18-ticker pool.
Uses backfill2.json as baseline.

Run: venv/bin/python3 test_new_tickers2.py
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

CURRENT_TICKERS = ["NVDA", "TSLA", "AMD", "COIN", "META", "PLTR", "SMCI", "NFLX", "APP", "RIVN", "CRWD", "BBAI"]
NEW_TICKERS     = ["SHOP", "SOFI", "ARM", "DKNG", "RKLB", "RDDT"]

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
REENTRY_CUTOFF     = "13:30"
REENTRY_ALLOC_MULT = 0.75
REENTRY_SETTLE     = 5


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
            return price, "TIME_CLOSE", i
        if price >= entry_price * (1 + TAKE_PROFIT):
            return price, "TAKE_PROFIT", i
        if peak >= entry_price * (1 + TRAIL_LOCK) and price <= peak * (1 - TRAIL_STOP):
            return price, "TRAILING_STOP", i
        if price <= entry_price * (1 - STOP_LOSS):
            return price, "STOP_LOSS", i
    return closes[-1], "EOD", len(closes) - 1


def find_orb_entry(closes, volumes, times, spy_by_time):
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
            if spy_by_time and day_open:
                ticker_chg = (closes[i] - day_open) / day_open
                spy_ts     = sorted(t for t in spy_by_time if t <= times[i])
                if spy_ts:
                    spy_open = spy_by_time[spy_ts[0]]
                    spy_now  = spy_by_time[spy_ts[-1]]
                    spy_chg  = (spy_now - spy_open) / spy_open if spy_open else 0
                    if ticker_chg <= spy_chg:
                        return None
            return {"bar": i, "price": closes[i], "rating": rating, "vol_ratio": vr}
    return None


def find_reentry(closes, volumes, times, exit_bar, spy_by_time, day_open):
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
            return {"bar": i, "price": closes[i], "rating": rating}
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

    for ticker, closes, volumes, times, prior_close in ticker_data:
        if day_limit_hit:
            break

        skip_orb = False
        if prior_close and closes:
            gap_pct  = (closes[0] - prior_close) / prior_close
            skip_orb = abs(gap_pct) > GAP_FILTER
        if skip_orb:
            continue

        orb = find_orb_entry(closes, volumes, times, spy_by_time)
        if not orb:
            continue

        modifier = atr_modifiers.get(ticker, 1.0)
        alloc    = round(base_alloc(market_state, orb["rating"]) * modifier, 2)
        if in_streak and orb["rating"] == "MAYBE":
            alloc = round(alloc * MAYBE_STREAK_CUT, 2)
        if in_drawdown:
            alloc = round(alloc * DRAWDOWN_CUT, 2)
        alloc = min(alloc, cash)
        if alloc < 50:
            continue

        cash -= alloc
        exit_p, exit_r, exit_bar = find_exit(closes, times, orb["price"], orb["bar"])
        pnl   = round((exit_p - orb["price"]) / orb["price"] * alloc, 2)
        cash += alloc + pnl
        day_pnl += pnl

        if round(day_pnl, 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True
            continue

        if exit_r not in ("STOP_LOSS", "TRAILING_STOP"):
            continue

        re = find_reentry(closes, volumes, times, exit_bar, spy_by_time, closes[0])
        if not re:
            continue

        re_alloc = round(alloc * REENTRY_ALLOC_MULT, 2)
        if in_drawdown:
            re_alloc = round(re_alloc * DRAWDOWN_CUT, 2)
        re_alloc = min(re_alloc, cash)
        if re_alloc < 50:
            continue

        cash -= re_alloc
        re_exit_p, _, _ = find_exit(closes, times, re["price"], re["bar"])
        re_pnl = round((re_exit_p - re["price"]) / re["price"] * re_alloc, 2)
        cash  += re_alloc + re_pnl
        day_pnl += re_pnl

        if round(day_pnl, 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True

    return round(day_pnl, 2)


def run():
    print("Loading backfill2 (current EX2 baseline)...")
    with open(os.path.join(BASE_DIR, "backfill2.json")) as f:
        backfill2 = json.load(f)

    ex2_days = sorted(
        [e for e in backfill2 if "Exercise 2" in e["title"]],
        key=lambda e: e["date"]
    )
    baseline = {e["date"]: e["total_pnl"] for e in ex2_days}
    ms_map   = {e["date"]: e.get("market_state", "neutral") for e in ex2_days}
    dates    = [e["date"] for e in ex2_days]

    all_tickers = list(dict.fromkeys(CURRENT_TICKERS + NEW_TICKERS))
    print(f"Found {len(dates)} days.")
    print(f"  Current pool ({len(CURRENT_TICKERS)}): {CURRENT_TICKERS}")
    print(f"  New pool ({len(all_tickers)}):     {all_tickers}\n")

    key, secret = _load_creds()
    client      = StockHistoricalDataClient(api_key=key, secret_key=secret)

    results_cur  = {}
    results_new  = {}
    streak_cur   = streak_new = 0
    done_cur     = []
    done_new     = []

    for date in dates:
        print(f"  {date}...", end="", flush=True)
        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt   = start_dt + timedelta(days=1)

        daily = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=all_tickers, timeframe=TimeFrame.Day,
            start=start_dt - timedelta(days=21), end=start_dt, feed="iex",
        ))
        prior_closes = {}
        atr_pcts_all = {}
        for t in all_tickers:
            bars = daily.data.get(t, [])
            if bars:
                prior_closes[t] = bars[-1].close
                val = calc_atr_pct(bars)
                if val:
                    atr_pcts_all[t] = val

        def make_modifiers(tickers):
            ap = {t: atr_pcts_all[t] for t in tickers if t in atr_pcts_all}
            if not ap:
                return {t: 1.0 for t in tickers}
            med = statistics.median(ap.values())
            return {t: round(min(ATR_MAX_MOD, max(ATR_MIN_MOD, med / ap[t])), 3) for t in ap}

        intraday = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=all_tickers + ["SPY"], timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))

        spy_by_time = {}
        try:
            df_spy = intraday.df
            if isinstance(df_spy.index, pd.MultiIndex):
                df_spy = df_spy.xs("SPY", level=0)
            df_spy = df_spy.tz_convert(ET)
            for t, row in df_spy.between_time("09:30", "15:59").iterrows():
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
                    volumes = [int(v) for v in today["volume"].tolist()]
                    times   = [t.strftime("%H:%M") for t in today.index]
                    data.append((ticker, closes, volumes, times, prior_closes.get(ticker)))
                except Exception:
                    pass
            return data

        in_dd_cur = portfolio_drawdown(done_cur, results_cur)
        in_dd_new = portfolio_drawdown(done_new, results_new)

        pnl_cur = simulate_day(build_ticker_data(CURRENT_TICKERS), ms_map[date],
                               make_modifiers(CURRENT_TICKERS), streak_cur, spy_by_time, in_dd_cur)
        pnl_new = simulate_day(build_ticker_data(all_tickers), ms_map[date],
                               make_modifiers(all_tickers), streak_new, spy_by_time, in_dd_new)

        results_cur[date] = pnl_cur
        results_new[date] = pnl_new
        streak_cur = streak_cur + 1 if pnl_cur < 0 else 0
        streak_new = streak_new + 1 if pnl_new < 0 else 0
        done_cur.append(date)
        done_new.append(date)

        diff = pnl_new - pnl_cur
        flag = f"  ({diff:+.2f})" if abs(diff) > 0.01 else ""
        print(f" cur {pnl_cur:+.2f}  new {pnl_new:+.2f}{flag}")

    print(f"\n{'='*70}")
    print(f"  {'Date':<12} {'Current 12':>12} {'New 18':>12} {'Diff':>8}")
    print(f"  {'-'*48}")
    cum_cur = cum_new = 0.0
    for date in dates:
        c = results_cur[date]
        n = results_new[date]
        cum_cur += c
        cum_new += n
        flag = " <" if abs(n - c) > 1.00 else ""
        print(f"  {date:<12} {c:>+11.2f}  {n:>+11.2f}  {n-c:>+7.2f}{flag}")
    print(f"  {'-'*48}")
    print(f"  {'TOTAL':<12} {cum_cur:>+11.2f}  {cum_new:>+11.2f}  {cum_new-cum_cur:>+7.2f}")

    w_cur = sum(1 for d in dates if results_cur[d] > 0)
    w_new = sum(1 for d in dates if results_new[d] > 0)
    nd    = len(dates)
    print(f"\n  Current 12:  {w_cur}W/{nd-w_cur}L  ${cum_cur:+.2f}")
    print(f"  New 18:      {w_new}W/{nd-w_new}L  ${cum_new:+.2f}")
    print()


if __name__ == "__main__":
    run()
