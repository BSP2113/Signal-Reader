"""
test_early_weakness.py — two variations of the early weakness exit rule

Variation 1 — 45-min checkpoint (no price floor):
  45 minutes after entry, if price is below entry AND lower than 5 bars ago,
  exit early. More time for slow starters to develop.

Variation 2 — 20-min checkpoint + -0.5% floor:
  20 minutes after entry, only exit if price is at least 0.5% below entry AND
  still moving down. Filters out mild dips that could recover.

Both tested against:
  - 15-day window  (exercises.json dates)
  - 30-day backfill (backfill.json dates)

Run: venv/bin/python3 test_early_weakness.py
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

TICKERS        = ["NVDA", "TSLA", "AMD", "COIN", "META", "PLTR", "SMCI", "CRDO",
                  "IONQ", "RIVN", "DELL", "KOPN", "SHOP", "ASTS", "ARM", "DKNG",
                  "RKLB", "RDDT"]
BUDGET         = 5000.0
ORB_BARS       = 15
ORB_CUTOFF     = "11:30"
ENTRY_CLOSE    = "14:00"
TAKE_PROFIT    = 0.03
TRAIL_STOP     = 0.020
TRAIL_LOCK     = 0.01
STOP_LOSS      = 0.015
DAY_LOSS_LIMIT = -75.0
GAP_FILTER     = 0.04
GAP_GO_THRESH  = 0.03
GAP_GO_WINDOW  = "09:39"
GAP_GO_SKIP    = {"RKLB"}
ATR_DAYS       = 14
ATR_MIN_MOD    = 0.40
ATR_MAX_MOD    = 1.50
STREAK_TRIGGER   = 2
MAYBE_STREAK_CUT = 0.50
DRAWDOWN_WINDOW    = 5
DRAWDOWN_THRESHOLD = 0.015
DRAWDOWN_CUT       = 0.50
NO_PROGRESS_MINS   = 90

EARLY_WEAK_LOOKBACK = 5    # bars back to determine direction

# Variation 1: 45-min checkpoint, no price floor, TSLA/PLTR excluded
VAR1_MINS      = 45
VAR1_FLOOR_PCT = 0.0
VAR1_SKIP      = {"TSLA", "PLTR"}

# Variation 2: earlier checkpoint with price floor
VAR2_MINS      = 20
VAR2_FLOOR_PCT = 0.005   # must be at least 0.5% below entry

ALLOC_BULL = {"TAKE": 0.35, "MAYBE": 0.20}
ALLOC_NEUT = {"TAKE": 0.30, "MAYBE": 0.15}
ALLOC_BEAR = {"TAKE": 0.10, "MAYBE": 0.10}


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


def find_exit(closes, times, entry_price, entry_bar, ew_mins=None, ew_floor_pct=0.0):
    """
    Returns (exit_price, exit_time, reason).
    ew_mins: if set, enables early weakness check at that many minutes post-entry.
    ew_floor_pct: minimum % below entry required to trigger (0.0 = any negative).
    """
    peak         = entry_price
    consec_above = 0
    trail_armed  = False
    lock_level   = entry_price * (1 + TRAIL_LOCK)
    entry_mins   = int(times[entry_bar][:2]) * 60 + int(times[entry_bar][3:])
    t90_mins     = entry_mins + NO_PROGRESS_MINS
    t90_passed   = False
    tew_mins     = entry_mins + ew_mins if ew_mins is not None else None
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
            return price, times[i], "TIME_CLOSE"
        if price >= entry_price * (1 + TAKE_PROFIT):
            return price, times[i], "TAKE_PROFIT"
        if trail_armed and price <= peak * (1 - TRAIL_STOP):
            return price, times[i], "TRAILING_STOP"
        if price <= entry_price * (1 - STOP_LOSS):
            return price, times[i], "STOP_LOSS"
        if not t90_passed and bar_mins >= t90_mins and t90_mins <= 14 * 60:
            t90_passed = True
            if price <= entry_price:
                return price, times[i], "NO_PROGRESS"

        if tew_mins is not None and not tew_passed and bar_mins >= tew_mins:
            tew_passed = True
            floor_price = entry_price * (1 - ew_floor_pct)
            if price < floor_price:
                lookback = max(entry_bar + 1, i - EARLY_WEAK_LOOKBACK)
                if price < closes[lookback]:
                    return price, times[i], "EARLY_WEAK"

    return closes[-1], times[-1], "EOD"


def find_entry(closes, highs, volumes, times, gap_pct, ticker, spy_by_time):
    """Returns entry dict or None."""
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    day_open = closes[0] if closes else None

    # GAP_GO
    if gap_pct >= GAP_GO_THRESH and ticker not in GAP_GO_SKIP:
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
                            return None
                return {"bar": i, "time": times[i], "price": closes[i],
                        "rating": rating, "vol_ratio": round(vr, 1), "signal": "GAP_GO"}
        return None

    # ORB
    if abs(gap_pct) > GAP_FILTER:
        return None
    if len(closes) <= ORB_BARS:
        return None

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
                    "rating": rating, "vol_ratio": round(vr, 1), "signal": "ORB"}
    return None


def base_alloc(market_state, rating, starting_balance):
    if market_state == "bullish": return round(starting_balance * ALLOC_BULL[rating], 2)
    if market_state == "bearish": return round(starting_balance * ALLOC_BEAR[rating], 2)
    return round(starting_balance * ALLOC_NEUT[rating], 2)


def in_portfolio_drawdown(completed_dates, daily_pnls):
    if not completed_dates:
        return False
    wallet = BUDGET
    port_vals = []
    for d in completed_dates:
        wallet += daily_pnls[d]
        port_vals.append(wallet)
    current = port_vals[-1]
    peak    = max(port_vals[-DRAWDOWN_WINDOW:])
    return current < peak * (1 - DRAWDOWN_THRESHOLD)


def collect_potentials(ticker_data, market_state, atr_modifiers, loss_streak,
                       spy_by_time, in_drawdown, starting_balance):
    """
    Phase 1: find all valid entries and compute exits for all three versions.
    Returns list of potential trade dicts sorted by entry time.
    """
    in_streak  = loss_streak >= STREAK_TRIGGER
    potentials = []

    for ticker, closes, highs, lows, volumes, times, prior_close, gap_pct in ticker_data:
        entry = find_entry(closes, highs, volumes, times, gap_pct, ticker, spy_by_time)
        if not entry:
            continue

        modifier = atr_modifiers.get(ticker, 1.0)
        alloc    = base_alloc(market_state, entry["rating"], starting_balance) * modifier
        alloc    = round(alloc, 2)
        if in_streak and entry["rating"] == "MAYBE":
            alloc = round(alloc * MAYBE_STREAK_CUT, 2)
        if in_drawdown:
            alloc = round(alloc * DRAWDOWN_CUT, 2)
        if alloc < 50:
            continue

        ep, et, er = find_exit(closes, times, entry["price"], entry["bar"])
        v1_mins = None if ticker in VAR1_SKIP else VAR1_MINS
        v1p, v1t, v1r = find_exit(closes, times, entry["price"], entry["bar"],
                                   v1_mins, VAR1_FLOOR_PCT)
        v2p, v2t, v2r = find_exit(closes, times, entry["price"], entry["bar"],
                                   VAR2_MINS, VAR2_FLOOR_PCT)

        def pnl(exit_p):
            return round((exit_p - entry["price"]) / entry["price"] * alloc, 2)

        potentials.append({
            "ticker":     ticker,
            "entry_p":    entry["price"],
            "entry_time": entry["time"],
            "alloc":      alloc,
            "base":  {"exit_p": ep,  "exit_t": et,  "reason": er,  "pnl": pnl(ep)},
            "var1":  {"exit_p": v1p, "exit_t": v1t, "reason": v1r, "pnl": pnl(v1p)},
            "var2":  {"exit_p": v2p, "exit_t": v2t, "reason": v2r, "pnl": pnl(v2p)},
        })

    potentials.sort(key=lambda t: t["entry_time"])
    return potentials


def simulate_chronological(potentials, starting_balance, version_key):
    """
    Phase 2: replay trades chronologically, freeing capital only when
    a position's exit time passes — same logic as ex1.py.
    Returns (day_pnl, early_weak_list).
    """
    active        = []   # {"exit_t", "alloc"} for currently held positions
    day_pnl       = 0.0
    day_limit_hit = False
    ew_trades     = []

    for p in potentials:
        if day_limit_hit:
            continue

        v = p[version_key]

        # Free capital from positions that exited before this entry
        active   = [a for a in active if a["exit_t"] > p["entry_time"]]
        deployed = sum(a["alloc"] for a in active)
        available = starting_balance - deployed

        if available < p["alloc"]:
            continue

        active.append({"exit_t": v["exit_t"], "alloc": p["alloc"]})
        day_pnl += v["pnl"]

        if v["reason"] == "EARLY_WEAK":
            base = p["base"]
            ew_trades.append({
                "ticker":    p["ticker"],
                "entry_p":   p["entry_p"],
                "early_pnl": v["pnl"],
                "base_pnl":  base["pnl"],
                "base_r":    base["reason"],
                "saved":     round(v["pnl"] - base["pnl"], 2),
            })

        if round(day_pnl, 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True

    return round(day_pnl, 2), ew_trades


def _print_variation(label, dates, results_base, results_var, all_ew):
    """Print summary table and detail for one variation vs baseline."""
    print(f"\n  ── {label} ──")
    print(f"  {'Date':<12} {'Baseline':>10} {'Variation':>10} {'Diff':>8}  Trades cut")
    print(f"  {'-'*65}")
    cum_base = cum_var = 0.0
    for date in dates:
        b   = results_base[date]
        v   = results_var[date]
        ew  = all_ew[date]
        cum_base += b
        cum_var  += v
        cut_str = ", ".join(t["ticker"] for t in ew) if ew else ""
        flag = " <" if abs(v - b) > 0.50 else ""
        print(f"  {date:<12} {b:>+9.2f}  {v:>+9.2f}  {v-b:>+7.2f}{flag}  {cut_str}")
    print(f"  {'-'*65}")
    print(f"  {'TOTAL':<12} {cum_base:>+9.2f}  {cum_var:>+9.2f}  {cum_var-cum_base:>+7.2f}")
    b_w = sum(1 for d in dates if results_base[d] > 0)
    v_w = sum(1 for d in dates if results_var[d] > 0)
    nd  = len(dates)
    print(f"  Baseline:  {b_w}W/{nd-b_w}L  ${cum_base:+.2f}")
    print(f"  Variation: {v_w}W/{nd-v_w}L  ${cum_var:+.2f}   net {cum_var-cum_base:+.2f}")

    any_ew = [(d, t) for d in dates for t in all_ew[d]]
    if any_ew:
        print(f"\n  Early cuts ({len(any_ew)} trades):")
        print(f"  {'Date':<12} {'Ticker':<6} {'EarlyP&L':>9} {'BaseP&L':>9} {'BaseExit':<14} {'Saved':>7}")
        print(f"  {'-'*62}")
        for date, t in any_ew:
            print(f"  {date:<12} {t['ticker']:<6} "
                  f"${t['early_pnl']:>+7.2f}   "
                  f"${t['base_pnl']:>+7.2f}   "
                  f"{t['base_r']:<14} "
                  f"${t['saved']:>+6.2f}")


def run_window(label, dates, ms_lookup, client):
    print(f"\n{'='*68}")
    print(f"  {label}  ({len(dates)} days)")
    print(f"{'='*68}")

    results_base = {}
    results_var1 = {}   # 45-min, no floor
    results_var2 = {}   # 20-min, 0.5% floor
    ew_var1      = {}
    ew_var2      = {}
    loss_streak  = 0
    completed    = []
    wallet_base  = BUDGET
    wallet_v1    = BUDGET
    wallet_v2    = BUDGET

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
            atr_modifiers = {}

        all_syms = TICKERS + ["SPY"]
        intraday = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=all_syms, timeframe=TimeFrame.Minute,
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
                gap_pct = (closes[0] - prior_closes[ticker]) / prior_closes[ticker] \
                          if prior_closes.get(ticker) and closes else 0.0
                ticker_data.append((ticker, closes, highs, lows, volumes, times,
                                    prior_closes.get(ticker), gap_pct))
            except Exception:
                pass

        market_state = ms_lookup.get(date, "neutral")
        in_drawdown  = in_portfolio_drawdown(completed, results_base)

        potentials = collect_potentials(ticker_data, market_state, atr_modifiers,
                                        loss_streak, spy_by_time, in_drawdown, wallet_base)

        pnl_base, _   = simulate_chronological(potentials, wallet_base, "base")
        pnl_v1,   ew1 = simulate_chronological(potentials, wallet_v1,   "var1")
        pnl_v2,   ew2 = simulate_chronological(potentials, wallet_v2,   "var2")

        results_base[date] = pnl_base
        results_var1[date] = pnl_v1
        results_var2[date] = pnl_v2
        ew_var1[date]      = ew1
        ew_var2[date]      = ew2
        loss_streak = loss_streak + 1 if pnl_base < 0 else 0
        completed.append(date)
        wallet_base += pnl_base
        wallet_v1   += pnl_v1
        wallet_v2   += pnl_v2

        d1 = pnl_v1 - pnl_base
        d2 = pnl_v2 - pnl_base
        f1 = f"({d1:+.2f})" if abs(d1) > 0.01 else "     "
        f2 = f"({d2:+.2f})" if abs(d2) > 0.01 else "     "
        print(f" base {pnl_base:+.2f}  45min {pnl_v1:+.2f} {f1}  20min+floor {pnl_v2:+.2f} {f2}")

    _print_variation(
        f"Var 1: 45-min checkpoint, no floor, skip TSLA/PLTR  (VAR1)",
        dates, results_base, results_var1, ew_var1,
    )
    _print_variation(
        f"Var 2: 20-min checkpoint, -0.5% floor  (VAR2)",
        dates, results_base, results_var2, ew_var2,
    )


def run():
    # Load market state lookup (historical)
    ms_hist_path = os.path.join(BASE_DIR, "market_states_historical.json")
    ms_lookup    = {}
    if os.path.exists(ms_hist_path):
        with open(ms_hist_path) as f:
            for entry in json.load(f):
                ms_lookup[entry["date"]] = entry.get("state", "neutral")

    # Also grab market state from exercises.json entries (covers recent dates)
    ex_path = os.path.join(BASE_DIR, "exercises.json")
    with open(ex_path) as f:
        ex_data = json.load(f)
    for entry in ex_data:
        if entry.get("date") and entry.get("market_state"):
            ms_lookup.setdefault(entry["date"], entry["market_state"])

    # 15-day window: exercises.json dates
    ex1_dates = sorted(
        {e["date"] for e in ex_data if "Exercise 1" in e.get("title", "")},
    )

    # 30-day backfill window: backfill.json dates
    bf_path = os.path.join(BASE_DIR, "backfill.json")
    with open(bf_path) as f:
        bf_data = json.load(f)
    bf_dates = sorted(
        {e["date"] for e in bf_data if "Exercise 1" in e.get("title", "")},
    )

    key, secret = _load_creds()
    client      = StockHistoricalDataClient(api_key=key, secret_key=secret)

    run_window("15-DAY WINDOW (exercises.json)", ex1_dates, ms_lookup, client)
    run_window("30-DAY BACKFILL (backfill.json)", bf_dates, ms_lookup, client)


if __name__ == "__main__":
    run()
