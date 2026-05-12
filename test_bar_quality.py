"""
test_bar_quality.py — Test 3 variants for large-gap GAP_GO entries (gap >= 10%).

Baseline: current EX1 logic unchanged.

Option 1 — Tighter stop loss:
  For GAP_GO with gap >= 10%, stop loss tightens from -1.5% to -1.0%.
  Limits damage when the gap reverses fast. Entry unchanged.

Option 2 — Lower wick filter at entry:
  For GAP_GO with gap >= 10%, skip signal if the entry bar's lower wick
  is >= 15% of the bar's total high-low range. A large lower wick means
  the bar dipped below its open before rallying — a sign of selling pressure.

Option 3 — Post-entry confirmation bar exit:
  For GAP_GO with gap >= 10%, if the bar immediately after entry closes in
  the lower 40% of its high-low range, exit immediately at that bar's close.
  Catches fast reversals one bar after the entry, before the full stop fires.

Does NOT modify exercises.json.
"""
import json, os, statistics as _stats
from datetime import datetime, timedelta, timezone
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

TICKERS     = ["NVDA","TSLA","AMD","COIN","META","PLTR","SMCI","CRDO","IONQ",
               "RIVN","DELL","KOPN","SHOP","ASTS","ARM","DKNG","UPST"]
BUDGET           = 5000.0
ORB_BARS         = 15
ORB_CUTOFF       = "11:30"
ENTRY_CLOSE      = "14:00"
TAKE_PROFIT      = 0.03
TRAIL_STOP       = 0.020
TRAIL_LOCK       = 0.01
STOP_LOSS        = 0.015
STOP_LOSS_TIGHT  = 0.010   # Option 1: tighter stop for large gaps
DAY_LOSS_LIMIT   = -75.0
GAP_FILTER       = 0.04
GAP_GO_THRESH    = 0.03
GAP_GO_WINDOW    = "09:39"
GAP_GO_SKIP_TICKERS = set()
LARGE_GAP_THRESH    = 0.10   # gap >= 10% triggers the variants
LOWER_WICK_MAX      = 0.15   # Option 2: lower wick must be < 15% of bar range
CONFIRM_BAR_MIN_POS = 0.60   # Option 3: next bar must close in upper 40%+ of range
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
    "2026-03-02","2026-03-03","2026-03-04","2026-03-05","2026-03-06",
    "2026-03-09","2026-03-10","2026-03-11","2026-03-12","2026-03-13",
    "2026-03-16","2026-03-17","2026-03-18","2026-03-19","2026-03-20",
    "2026-03-23","2026-03-24","2026-03-25","2026-03-26","2026-03-27",
    "2026-03-31","2026-04-01","2026-04-02","2026-04-06","2026-04-07",
    "2026-04-08","2026-04-09","2026-04-10","2026-04-13","2026-04-14",
    "2026-04-15","2026-04-16","2026-04-17","2026-04-20","2026-04-21",
    "2026-04-22","2026-04-23","2026-04-24","2026-04-27","2026-04-28",
    "2026-04-29","2026-04-30","2026-05-01","2026-05-04","2026-05-05",
    "2026-05-06",
]


def _load_creds():
    creds = {}
    with open(os.path.join(BASE_DIR, ".env")) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds["ALPACA_API_KEY"], creds["ALPACA_API_SECRET"]


def calc_atr_pct(bars):
    if len(bars) < 2:
        return None
    trs = [max(bars[i].high - bars[i].low,
               abs(bars[i].high - bars[i-1].close),
               abs(bars[i].low  - bars[i-1].close))
           for i in range(1, len(bars))]
    atr = sum(trs[-ATR_DAYS:]) / min(len(trs), ATR_DAYS)
    return atr / bars[-1].close if bars[-1].close else None


def score_signal(closes_so_far, vol, avg_volume):
    vr = vol / avg_volume if avg_volume else 0
    if len(closes_so_far) < 2 or vr < 1.0:
        return "SKIP", vr
    day_open   = closes_so_far[0]
    day_change = (closes_so_far[-1] - day_open) / day_open if day_open else 0
    if day_change < -0.02 and vr < 2.0:
        return "SKIP", vr
    recent = closes_so_far[-min(12, len(closes_so_far)):]
    flips  = sum(1 for j in range(1, len(recent)-1)
                 if (recent[-j]-recent[-j-1]) * (recent[-j-1]-recent[-j-2]) < 0)
    score = (1 if vr >= 1.5 else 0) + (1 if flips < 3 else -1)
    if score >= 2:   return "TAKE",  vr
    elif score >= 0: return "MAYBE", vr
    else:            return "SKIP",  vr


def find_exit(closes, highs, lows, times, entry_price, entry_bar,
              ticker=None, stop_loss=STOP_LOSS,
              confirm_bar_exit=False):
    """
    confirm_bar_exit: if True, exit immediately if the bar right after entry
    closes in the lower 40% of its high-low range (Option 3).
    """
    peak = entry_price
    consec_above = 0
    trail_armed  = False
    lock_level   = entry_price * (1 + TRAIL_LOCK)
    entry_mins   = int(times[entry_bar][:2])*60 + int(times[entry_bar][3:])
    t90_passed = tew_passed = False

    for i in range(entry_bar + 1, len(closes)):
        price    = closes[i]
        bar_mins = int(times[i][:2])*60 + int(times[i][3:])
        peak     = max(peak, price)

        # Option 3: confirmation bar check — only on the bar immediately after entry
        if confirm_bar_exit and i == entry_bar + 1:
            bar_range = highs[i] - lows[i]
            if bar_range > 0:
                close_pos = (closes[i] - lows[i]) / bar_range
                if close_pos < CONFIRM_BAR_MIN_POS:
                    return {"bar": i, "time": times[i], "price": price,
                            "reason": "CONFIRM_BAR_EXIT"}

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
        if price <= entry_price * (1 - stop_loss):
            return {"bar": i, "time": times[i], "price": price, "reason": "STOP_LOSS"}
        if not t90_passed and bar_mins >= entry_mins + NO_PROGRESS_MINS and entry_mins + NO_PROGRESS_MINS <= 14*60:
            t90_passed = True
            if price <= entry_price:
                return {"bar": i, "time": times[i], "price": price, "reason": "NO_PROGRESS"}
        if ticker not in EARLY_WEAK_SKIP and not tew_passed and bar_mins >= entry_mins + EARLY_WEAK_MINS:
            tew_passed = True
            if price < entry_price:
                lb = max(entry_bar+1, i - EARLY_WEAK_LOOKBACK)
                if price < closes[lb]:
                    return {"bar": i, "time": times[i], "price": price, "reason": "EARLY_WEAK"}

    return {"bar": len(closes)-1, "time": times[-1], "price": closes[-1], "reason": "EOD"}


def _spy_check(closes, times, i, day_open, spy_by_time):
    if not spy_by_time or not day_open:
        return True
    ticker_chg = (closes[i] - day_open) / day_open
    spy_ts = sorted(t for t in spy_by_time if t <= times[i])
    if not spy_ts:
        return True
    spy_chg = (spy_by_time[spy_ts[-1]] - spy_by_time[spy_ts[0]]) / spy_by_time[spy_ts[0]]
    return ticker_chg > spy_chg


def _gap_go_scan(closes, highs, lows, opens, volumes, times,
                 avg_vol, day_open, spy_by_time, ticker, gap_pct,
                 use_lower_wick_filter):
    open_bar_high = highs[0]
    for i in range(1, len(closes)):
        if times[i] > GAP_GO_WINDOW:
            break
        if closes[i] > open_bar_high:
            vr = volumes[i] / avg_vol if avg_vol else 0
            if vr < 1.0:
                continue

            # Option 2: lower wick filter for large gaps
            if use_lower_wick_filter and gap_pct >= LARGE_GAP_THRESH:
                bar_range  = highs[i] - lows[i]
                lower_wick = max(0.0, opens[i] - lows[i])
                if bar_range > 0 and lower_wick / bar_range >= LOWER_WICK_MAX:
                    continue  # too much selling below the open — skip

            rating = "TAKE" if vr >= 1.5 else "MAYBE"
            if spy_by_time and day_open:
                if not _spy_check(closes, times, i, day_open, spy_by_time):
                    return []
            entry = {"bar": i, "time": times[i], "price": closes[i],
                     "rating": rating, "vol_ratio": round(vr, 1), "signal": "GAP_GO"}
            return [entry]
    return []


def find_trades(closes, highs, lows, opens, volumes, times,
                skip_orb=False, spy_by_time=None, gap_pct=0.0, ticker=None,
                use_lower_wick_filter=False, use_tight_stop=False,
                use_confirm_bar=False):
    if len(closes) <= ORB_BARS:
        return []
    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    day_open = closes[0]

    if gap_pct >= GAP_GO_THRESH and ticker not in GAP_GO_SKIP_TICKERS:
        entries = _gap_go_scan(closes, highs, lows, opens, volumes, times,
                               avg_vol, day_open, spy_by_time, ticker, gap_pct,
                               use_lower_wick_filter)
        results = []
        for entry in entries:
            is_large = gap_pct >= LARGE_GAP_THRESH
            stop     = STOP_LOSS_TIGHT if (use_tight_stop and is_large) else STOP_LOSS
            confirm  = use_confirm_bar and is_large
            exit_ = find_exit(closes, highs, lows, times, entry["price"], entry["bar"],
                               ticker=ticker, stop_loss=stop, confirm_bar_exit=confirm)
            results.append((entry, exit_))
        return results

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
            exit_ = find_exit(closes, highs, lows, times, entry["price"], i, ticker=ticker)
            return [(entry, exit_)]
    return []


def simulate_day(ticker_data, market_state, starting_balance,
                 in_streak_cut, drawdown_active,
                 use_tight_stop=False, use_lower_wick_filter=False, use_confirm_bar=False):
    def alloc_base(rating):
        pcts = (ALLOC_PCT_BULL if market_state == "bullish"
                else ALLOC_PCT_BEAR if market_state == "bearish"
                else ALLOC_PCT_NEUT)
        return round(starting_balance * pcts[rating], 2)

    spy_by_time = ticker_data.get("_spy", {})
    potential = []
    for ticker, d in ticker_data.items():
        if ticker.startswith("_"):
            continue
        trades = find_trades(
            d["closes"], d["highs"], d["lows"], d["opens"], d["volumes"], d["times"],
            skip_orb=d["skip_orb"], spy_by_time=spy_by_time,
            gap_pct=d["gap_pct"], ticker=ticker,
            use_lower_wick_filter=use_lower_wick_filter,
            use_tight_stop=use_tight_stop,
            use_confirm_bar=use_confirm_bar,
        )
        for entry, exit_ in trades:
            alloc = round(alloc_base(entry["rating"]) * d["atr_mod"], 2)
            if in_streak_cut and entry["rating"] == "MAYBE":
                alloc = round(alloc * MAYBE_STREAK_CUT, 2)
            if drawdown_active:
                alloc = round(alloc * DRAWDOWN_CUT, 2)
            pnl = round((exit_["price"] - entry["price"]) / entry["price"] * alloc, 2)
            potential.append({
                "ticker": ticker, "time": entry["time"], "exit_time": exit_["time"],
                "rating": entry["rating"], "signal": entry["signal"],
                "gap_pct": d["gap_pct"], "allocated": alloc, "pnl": pnl,
                "exit_reason": exit_["reason"],
            })

    potential.sort(key=lambda t: t["time"])
    entries, active, day_limit_hit = [], [], False
    for trade in potential:
        if day_limit_hit:
            continue
        active = [a for a in active if a["exit_time"] > trade["time"]]
        deployed  = sum(a["allocated"] for a in active)
        if starting_balance - deployed < trade["allocated"]:
            continue
        active.append({"exit_time": trade["exit_time"], "allocated": trade["allocated"]})
        entries.append(trade)
        if round(sum(e["pnl"] for e in entries), 2) <= DAY_LOSS_LIMIT:
            day_limit_hit = True

    return round(sum(e["pnl"] for e in entries), 2), entries


def fetch_day(client, date):
    start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = start_dt + timedelta(days=1)

    prior_daily = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=TICKERS, timeframe=TimeFrame.Day,
        start=start_dt - timedelta(days=21), end=start_dt, feed="iex",
    ))
    prior_closes, atr_pcts = {}, {}
    for t in TICKERS:
        bars = prior_daily.data.get(t, [])
        if bars:
            prior_closes[t] = bars[-1].close
            v = calc_atr_pct(bars)
            if v:
                atr_pcts[t] = v

    if len(atr_pcts) >= 2:
        med = _stats.median(atr_pcts.values())
        atr_mod = {t: round(min(ATR_MAX_MOD, max(ATR_MIN_MOD, med / atr_pcts[t])), 3)
                   for t in atr_pcts}
    else:
        atr_mod = {}

    spy_by_time = {}
    try:
        spy_bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols="SPY", timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))
        df_spy = spy_bars.df
        if isinstance(df_spy.index, pd.MultiIndex):
            df_spy = df_spy.xs("SPY", level=0)
        df_spy = df_spy.tz_convert(ET)
        for ts, row in df_spy.between_time("09:30", "15:59").iterrows():
            spy_by_time[ts.strftime("%H:%M")] = row["close"]
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
            closes = list(today["close"])
            highs  = list(today["high"])
            lows   = list(today["low"])
            opens  = list(today["open"])
            vols   = list(today["volume"])
            times  = [t.strftime("%H:%M") for t in today.index]
            gap_pct = skip_orb = 0.0
            if ticker in prior_closes and closes:
                gap_pct  = (closes[0] - prior_closes[ticker]) / prior_closes[ticker]
                skip_orb = abs(gap_pct) > GAP_FILTER
            ticker_data[ticker] = {
                "closes": closes, "highs": highs, "lows": lows, "opens": opens,
                "volumes": vols, "times": times,
                "gap_pct": gap_pct, "skip_orb": skip_orb,
                "atr_mod": atr_mod.get(ticker, 1.0),
            }
        except Exception:
            continue
    return ticker_data


def load_market_states():
    path = os.path.join(BASE_DIR, "market_states_historical.json")
    SPY_BULL = 0.003
    VIXY_SURGE = 0.03
    states = {}
    if os.path.exists(path):
        with open(path) as f:
            for d in json.load(f):
                spy  = d.get("spy_gap_pct", 0) / 100
                vixy = d.get("vixy_trend_pct", 0) / 100
                if spy <= -0.005 or vixy >= VIXY_SURGE:
                    states[d["date"]] = "bearish"
                elif spy >= SPY_BULL and vixy < VIXY_SURGE:
                    states[d["date"]] = "bullish"
                else:
                    states[d["date"]] = "neutral"
    return states


def run_variant(dates, fetch_fn, market_states, flags):
    wallet = BUDGET
    streak = streak_losses = 0
    peak_5d = [BUDGET] * DRAWDOWN_WINDOW
    totals = {}
    for date in dates:
        td = fetch_fn(date)
        ms = market_states.get(date, "neutral")
        drawdown = wallet < min(peak_5d) * (1 - DRAWDOWN_THRESHOLD)
        pnl, _ = simulate_day(td, ms, wallet, streak >= STREAK_TRIGGER, drawdown, **flags)
        totals[date] = pnl
        wallet += pnl
        peak_5d = (peak_5d + [wallet])[-DRAWDOWN_WINDOW:]
        if pnl < 0:
            streak_losses += 1; streak = streak_losses
        else:
            streak_losses = 0; streak = 0
    return totals


def main():
    key, secret = _load_creds()
    client = StockHistoricalDataClient(key, secret)
    market_states = load_market_states()

    cache = {}
    def fetch_cached(date):
        if date not in cache:
            print(f"  Fetching {date}...", end="\r")
            cache[date] = fetch_day(client, date)
        return cache[date]

    variants = {
        "Baseline": dict(use_tight_stop=False, use_lower_wick_filter=False, use_confirm_bar=False),
        "Opt1-Stop": dict(use_tight_stop=True,  use_lower_wick_filter=False, use_confirm_bar=False),
        "Opt2-Wick": dict(use_tight_stop=False, use_lower_wick_filter=True,  use_confirm_bar=False),
        "Opt3-Conf": dict(use_tight_stop=False, use_lower_wick_filter=False, use_confirm_bar=True),
    }

    results = {}
    for name, flags in variants.items():
        print(f"\nRunning {name}...")
        wallet = BUDGET
        streak = streak_losses = 0
        peak_5d = [BUDGET] * DRAWDOWN_WINDOW
        day_results = {}
        for date in DATES:
            td = fetch_cached(date)
            ms = market_states.get(date, "neutral")
            drawdown = wallet < min(peak_5d) * (1 - DRAWDOWN_THRESHOLD)
            pnl, trades = simulate_day(td, ms, wallet,
                                       streak >= STREAK_TRIGGER, drawdown, **flags)
            day_results[date] = {"pnl": pnl, "trades": trades}
            wallet += pnl
            peak_5d = (peak_5d + [wallet])[-DRAWDOWN_WINDOW:]
            if pnl < 0:
                streak_losses += 1; streak = streak_losses
            else:
                streak_losses = 0; streak = 0
        results[name] = day_results

    # --- Print results ---
    names = list(variants.keys())
    print(f"\n\n{'Date':<12}", end="")
    for n in names:
        print(f"  {n:>10}", end="")
    print(f"  {'Best':>10}  Notes")
    print("-" * 95)

    totals   = {n: 0.0 for n in names}
    wins     = {n: 0   for n in names}
    changed  = []

    for date in DATES:
        pnls = {n: results[n][date]["pnl"] for n in names}
        base = pnls["Baseline"]

        # Find large-gap trades on this date in the baseline
        large_gap_tickers = [
            t["ticker"] for t in results["Baseline"][date]["trades"]
            if t["signal"] == "GAP_GO" and abs(t["gap_pct"]) >= LARGE_GAP_THRESH
        ]

        diffs = {n: round(pnls[n] - base, 2) for n in names if n != "Baseline"}
        any_changed = any(d != 0 for d in diffs.values())

        print(f"{date:<12}", end="")
        best_val = max(pnls.values())
        for n in names:
            marker = "*" if pnls[n] == best_val and any_changed else " "
            print(f"  {pnls[n]:>9.2f}{marker}", end="")

        note = ""
        if large_gap_tickers:
            note = f"  gap>=10%: {', '.join(large_gap_tickers)}"
            diffs_str = "  diffs: " + " ".join(f"{n}={diffs[n]:+.2f}" for n in names if n != "Baseline")
            note += diffs_str

        print(f"  {note}")

        for n in names:
            totals[n] += pnls[n]
            if pnls[n] > 0:
                wins[n] += 1
        if any_changed:
            changed.append(date)

    print("-" * 95)
    print(f"{'TOTAL':<12}", end="")
    best_total = max(totals.values())
    for n in names:
        marker = "*" if totals[n] == best_total else " "
        print(f"  {totals[n]:>9.2f}{marker}", end="")
    print()

    print(f"{'WIN DAYS':<12}", end="")
    for n in names:
        print(f"  {wins[n]:>9}/{len(DATES)}", end="")
    print()

    print(f"\nDates where any variant differed from baseline ({len(changed)}):")
    for date in changed:
        pnls = {n: results[n][date]["pnl"] for n in names}
        base = pnls["Baseline"]
        trades = results["Baseline"][date]["trades"]
        lg = [(t["ticker"], t["gap_pct"]*100, t["pnl"], t["exit_reason"]) for t in trades
              if t["signal"] == "GAP_GO" and abs(t["gap_pct"]) >= LARGE_GAP_THRESH]
        print(f"\n  {date}  baseline=${base:.2f}")
        for t, g, p, r in lg:
            print(f"    base trade: {t} gap={g:.1f}% pnl=${p:.2f} exit={r}")
        for n in names:
            if n == "Baseline": continue
            diff = round(pnls[n] - base, 2)
            if diff != 0:
                # Show what happened to large-gap trades in this variant
                vt = results[n][date]["trades"]
                lg_v = [(t["ticker"], t["pnl"], t["exit_reason"]) for t in vt
                        if t["signal"] == "GAP_GO" and abs(t["gap_pct"]) >= LARGE_GAP_THRESH]
                lg_str = " | ".join(f"{t} ${p:.2f} ({r})" for t,p,r in lg_v) or "trade skipped"
                print(f"    {n}: ${pnls[n]:.2f} ({diff:+.2f})  → {lg_str}")


if __name__ == "__main__":
    main()
