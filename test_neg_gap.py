"""
test_neg_gap.py — Test three growth-op alternatives vs EX1 baseline.

Variant A (Op1): ORB on any negative-gap ticker requires >=2.0x vol AND caps at MAYBE
Variant B (Op2): ORB on gap_pct < -1% downgrades rating one step (TAKE→MAYBE, MAYBE→SKIP)
Variant C (Op3): max 4 simultaneous open positions cap (all signals unchanged)

Fetches data once per date, runs all variants in memory.
Does NOT modify exercises.json.
"""
import json
import os
import statistics as _stats
from datetime import datetime, timedelta, timezone
import pandas as pd
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
DAY_LOSS_LIMIT   = -75.0
GAP_FILTER       = 0.04
GAP_GO_THRESH    = 0.03
GAP_GO_WINDOW    = "09:39"
GAP_GO_SKIP_TICKERS = set()
ATR_DAYS         = 14
ATR_MIN_MOD      = 0.40
ATR_MAX_MOD      = 1.50
STREAK_TRIGGER   = 2
MAYBE_STREAK_CUT = 0.50
NO_PROGRESS_MINS    = 90
EARLY_WEAK_MINS     = 45
EARLY_WEAK_LOOKBACK = 5
EARLY_WEAK_SKIP     = {"TSLA", "PLTR"}
DRAWDOWN_WINDOW    = 5
DRAWDOWN_THRESHOLD = 0.015
DRAWDOWN_CUT       = 0.50
ALLOC_PCT_BULL = {"TAKE": 0.35, "MAYBE": 0.20}
ALLOC_PCT_NEUT = {"TAKE": 0.30, "MAYBE": 0.15}
ALLOC_PCT_BEAR = {"TAKE": 0.10, "MAYBE": 0.10}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ET = "America/New_York"

DATES = [
    "2026-04-13", "2026-04-14", "2026-04-15", "2026-04-16", "2026-04-17",
    "2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24",
    "2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30",
    "2026-05-01", "2026-05-04",
]


def _load_creds():
    path = os.path.join(BASE_DIR, ".env")
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


def find_exit(closes, times, entry_price, entry_bar, ticker=None):
    peak = entry_price
    consec_above = 0
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


def _gap_go_scan(closes, highs, volumes, times, avg_vol, day_open, spy_by_time, ticker):
    """Shared GAP_GO logic — identical for all variants."""
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


def _spy_check(closes, times, i, day_open, spy_by_time):
    """Return True if ticker is outperforming SPY at bar i."""
    if not spy_by_time or not day_open:
        return True
    ticker_chg = (closes[i] - day_open) / day_open
    spy_times  = sorted(t for t in spy_by_time if t <= times[i])
    if not spy_times:
        return True
    spy_open = spy_by_time[spy_times[0]]
    spy_now  = spy_by_time[spy_times[-1]]
    spy_chg  = (spy_now - spy_open) / spy_open if spy_open else 0
    return ticker_chg > spy_chg


def find_trades_baseline(closes, highs, lows, volumes, times, skip_orb=False,
                          spy_by_time=None, gap_pct=0.0, ticker=None):
    if len(closes) <= ORB_BARS:
        return []
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    day_open = closes[0]
    if gap_pct >= GAP_GO_THRESH and ticker not in GAP_GO_SKIP_TICKERS:
        return _gap_go_scan(closes, highs, volumes, times, avg_vol, day_open, spy_by_time, ticker)
    if skip_orb:
        return []
    orb_high = max(closes[:ORB_BARS])
    for i in range(ORB_BARS, len(closes)):
        if times[i] > ORB_CUTOFF:
            break
        if closes[i] > orb_high:
            rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
            if rating == "SKIP":
                continue
            if rating == "TAKE" and times[i] < "10:00":
                continue
            if not _spy_check(closes, times, i, day_open, spy_by_time):
                return []
            entry = {"bar": i, "time": times[i], "price": closes[i],
                     "rating": rating, "vol_ratio": round(vr, 1), "signal": "ORB"}
            return [(entry, find_exit(closes, times, entry["price"], i, ticker=ticker))]
    return []


def find_trades_var_a(closes, highs, lows, volumes, times, skip_orb=False,
                       spy_by_time=None, gap_pct=0.0, ticker=None):
    """Variant A: negative-gap ORBs require >=2.0x vol, capped at MAYBE."""
    if len(closes) <= ORB_BARS:
        return []
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    day_open = closes[0]
    if gap_pct >= GAP_GO_THRESH and ticker not in GAP_GO_SKIP_TICKERS:
        return _gap_go_scan(closes, highs, volumes, times, avg_vol, day_open, spy_by_time, ticker)
    if skip_orb:
        return []
    orb_high = max(closes[:ORB_BARS])
    for i in range(ORB_BARS, len(closes)):
        if times[i] > ORB_CUTOFF:
            break
        if closes[i] > orb_high:
            rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
            if rating == "SKIP":
                continue
            if rating == "TAKE" and times[i] < "10:00":
                continue
            if gap_pct < 0:
                if vr < 2.0:
                    continue  # neg-gap ORB requires high conviction vol
                rating = "MAYBE"  # cap regardless of score
            if not _spy_check(closes, times, i, day_open, spy_by_time):
                return []
            entry = {"bar": i, "time": times[i], "price": closes[i],
                     "rating": rating, "vol_ratio": round(vr, 1), "signal": "ORB"}
            return [(entry, find_exit(closes, times, entry["price"], i, ticker=ticker))]
    return []


def find_trades_var_b(closes, highs, lows, volumes, times, skip_orb=False,
                       spy_by_time=None, gap_pct=0.0, ticker=None):
    """Variant B: gap < -1% downgrades ORB rating one step (TAKE→MAYBE, MAYBE→SKIP)."""
    if len(closes) <= ORB_BARS:
        return []
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    day_open = closes[0]
    if gap_pct >= GAP_GO_THRESH and ticker not in GAP_GO_SKIP_TICKERS:
        return _gap_go_scan(closes, highs, volumes, times, avg_vol, day_open, spy_by_time, ticker)
    if skip_orb:
        return []
    orb_high = max(closes[:ORB_BARS])
    for i in range(ORB_BARS, len(closes)):
        if times[i] > ORB_CUTOFF:
            break
        if closes[i] > orb_high:
            rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
            if rating == "SKIP":
                continue
            if rating == "TAKE" and times[i] < "10:00":
                continue
            if gap_pct < -0.01:
                if rating == "TAKE":
                    rating = "MAYBE"
                elif rating == "MAYBE":
                    rating = "SKIP"
            if rating == "SKIP":
                continue
            if not _spy_check(closes, times, i, day_open, spy_by_time):
                return []
            entry = {"bar": i, "time": times[i], "price": closes[i],
                     "rating": rating, "vol_ratio": round(vr, 1), "signal": "ORB"}
            return [(entry, find_exit(closes, times, entry["price"], i, ticker=ticker))]
    return []


def simulate_day(ticker_data, market_state, starting_balance, in_streak_cut, drawdown_active,
                 find_trades_fn, max_positions=None):
    """Run one day. Returns (total_pnl, list_of_trades)."""
    def spy_alloc(rating):
        if market_state == "bullish": return round(starting_balance * ALLOC_PCT_BULL[rating], 2)
        if market_state == "bearish": return round(starting_balance * ALLOC_PCT_BEAR[rating], 2)
        return round(starting_balance * ALLOC_PCT_NEUT[rating], 2)

    spy_by_time = ticker_data.get("_spy", {})
    potential = []
    for ticker, d in ticker_data.items():
        if ticker.startswith("_"):
            continue
        trades = find_trades_fn(
            d["closes"], d["highs"], d["lows"], d["volumes"], d["times"],
            skip_orb=d["skip_orb"], spy_by_time=spy_by_time,
            gap_pct=d["gap_pct"], ticker=ticker
        )
        for trade_num, (entry, exit_) in enumerate(trades, 1):
            alloc = round(spy_alloc(entry["rating"]) * d["atr_mod"], 2)
            if in_streak_cut and entry["rating"] == "MAYBE":
                alloc = round(alloc * MAYBE_STREAK_CUT, 2)
            if drawdown_active:
                alloc = round(alloc * DRAWDOWN_CUT, 2)
            pnl = round((exit_["price"] - entry["price"]) / entry["price"] * alloc, 2)
            potential.append({
                "ticker": ticker, "time": entry["time"], "exit_time": exit_["time"],
                "entry": entry["price"], "exit": exit_["price"],
                "exit_reason": exit_["reason"], "rating": entry["rating"],
                "allocated": alloc, "pnl": pnl,
            })

    potential.sort(key=lambda t: t["time"])
    entries = []
    active  = []
    day_limit_hit = False

    for trade in potential:
        if day_limit_hit:
            continue
        active = [a for a in active if a["exit_time"] > trade["time"]]
        if max_positions is not None and len(active) >= max_positions:
            continue
        deployed  = sum(a["allocated"] for a in active)
        available = starting_balance - deployed
        if available < trade["allocated"]:
            continue
        active.append({"exit_time": trade["exit_time"], "allocated": trade["allocated"]})
        entries.append(trade)
        if round(sum(e["pnl"] for e in entries), 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True

    return round(sum(e["pnl"] for e in entries), 2), entries


def fetch_day(client, date):
    """Fetch all market data for one date. Returns ticker_data dict."""
    start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = start_dt + timedelta(days=1)

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
        median_atr  = _stats.median(atr_pcts.values())
        atr_modifier = {
            t: round(min(ATR_MAX_MOD, max(ATR_MIN_MOD, median_atr / atr_pcts[t])), 3)
            for t in atr_pcts
        }
    else:
        atr_modifier = {}

    spy_by_time = {}
    try:
        spy_intraday = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols="SPY", timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))
        df_spy = spy_intraday.df
        if isinstance(df_spy.index, pd.MultiIndex):
            df_spy = df_spy.xs("SPY", level=0)
        df_spy = df_spy.tz_convert(ET)
        for t, row in df_spy.between_time("09:30", "15:59").iterrows():
            spy_by_time[t.strftime("%H:%M")] = row["close"]
    except Exception:
        pass

    ticker_data = {"_spy": spy_by_time}
    for ticker in TICKERS:
        try:
            intraday = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
                start=start_dt, end=end_dt, feed="iex",
            ))
            df = intraday.df
            if df.empty:
                continue
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
            gap_pct  = 0.0
            skip_orb = False
            if ticker in prior_closes and prior_closes[ticker] and closes:
                gap_pct  = (closes[0] - prior_closes[ticker]) / prior_closes[ticker]
                skip_orb = abs(gap_pct) > GAP_FILTER
            ticker_data[ticker] = {
                "closes":   closes, "highs": highs, "lows": lows,
                "volumes":  volumes, "times": times,
                "gap_pct":  gap_pct, "skip_orb": skip_orb,
                "atr_mod":  atr_modifier.get(ticker, 1.0),
            }
        except Exception:
            pass

    return ticker_data


def wallet_balance(trade_date):
    path = os.path.join(BASE_DIR, "exercises.json")
    if not os.path.exists(path):
        return BUDGET
    with open(path) as f:
        data = json.load(f)
    ex1 = [e for e in data if "Exercise 1" in e["title"] and e["date"] < trade_date]
    return round(BUDGET + sum(e["total_pnl"] for e in ex1), 2)


def loss_streak(trade_date):
    path = os.path.join(BASE_DIR, "backfill.json")
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        data = json.load(f)
    past = sorted(
        [e for e in data if "Exercise 1" in e["title"] and e["date"] < trade_date],
        key=lambda e: e["date"]
    )
    streak = 0
    for e in reversed(past):
        if e["total_pnl"] < 0:
            streak += 1
        else:
            break
    return streak


def in_drawdown(trade_date):
    path = os.path.join(BASE_DIR, "backfill.json")
    if not os.path.exists(path):
        return False
    with open(path) as f:
        data = json.load(f)
    past = sorted(
        [e for e in data if "Exercise 1" in e["title"] and e["date"] < trade_date],
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


def main():
    key, secret = _load_creds()
    client = StockHistoricalDataClient(api_key=key, secret_key=secret)

    # Read market state for each date from exercises.json
    ex_path = os.path.join(BASE_DIR, "exercises.json")
    with open(ex_path) as f:
        ex_data = json.load(f)
    market_by_date = {
        e["date"]: e.get("market_state", "neutral")
        for e in ex_data if "Exercise 1" in e["title"]
    }

    variants = [
        ("Baseline", find_trades_baseline, None),
        ("Var A (neg-gap 2x+MAYBE)", find_trades_var_a, None),
        ("Var B (gap<-1% downgrade)", find_trades_var_b, None),
        ("Var C (max 4 positions)",   find_trades_baseline, 4),
    ]

    totals = {name: 0.0 for name, _, _ in variants}
    wins   = {name: 0   for name, _, _ in variants}

    print(f"\n{'Date':<12} {'Baseline':>10} {'Var A':>10} {'Var B':>10} {'Var C':>10}")
    print("-" * 56)

    for date in DATES:
        print(f"  Fetching {date}...", flush=True)
        ticker_data = fetch_day(client, date)

        market_state  = market_by_date.get(date, "neutral")
        balance       = wallet_balance(date)
        streak        = loss_streak(date)
        in_streak_cut = streak >= STREAK_TRIGGER
        drawdown      = in_drawdown(date)

        day_results = {}
        for name, fn, max_pos in variants:
            pnl, _ = simulate_day(
                ticker_data, market_state, balance,
                in_streak_cut, drawdown, fn, max_pos
            )
            day_results[name] = pnl
            totals[name] += pnl
            if pnl > 0:
                wins[name] += 1

        print(f"{date:<12} "
              f"{day_results['Baseline']:>+10.2f} "
              f"{day_results['Var A (neg-gap 2x+MAYBE)']:>+10.2f} "
              f"{day_results['Var B (gap<-1% downgrade)']:>+10.2f} "
              f"{day_results['Var C (max 4 positions)']:>+10.2f}")

    print("-" * 56)
    print(f"{'TOTAL':<12} "
          f"{totals['Baseline']:>+10.2f} "
          f"{totals['Var A (neg-gap 2x+MAYBE)']:>+10.2f} "
          f"{totals['Var B (gap<-1% downgrade)']:>+10.2f} "
          f"{totals['Var C (max 4 positions)']:>+10.2f}")
    print(f"{'WIN DAYS':<12} "
          f"{wins['Baseline']:>10} "
          f"{wins['Var A (neg-gap 2x+MAYBE)']:>10} "
          f"{wins['Var B (gap<-1% downgrade)']:>10} "
          f"{wins['Var C (max 4 positions)']:>10}")
    print()
    for name, _, _ in variants:
        delta = totals[name] - totals['Baseline']
        print(f"  {name}: ${totals[name]:+.2f} ({delta:+.2f} vs baseline, {wins[name]}/16 win days)")


if __name__ == "__main__":
    main()
