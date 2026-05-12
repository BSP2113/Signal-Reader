"""
test_orb_confirm.py — check whether a 1-bar ORB confirmation would help

For every ORB entry in the backfill, fetches intraday data and checks:
  - Did the bar AFTER the breakout bar close above the ORB high? (confirmed)
  - Or did it fall back below? (false breakout — the case confirmation saves)

Then simulates what would have happened with a 1-bar confirmation rule and
compares total P&L against the current baseline.

Run: venv/bin/python3 test_orb_confirm.py
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
ORB_BARS       = 15
ORB_CUTOFF     = "11:30"
ENTRY_CLOSE    = "14:00"
TAKE_PROFIT    = 0.03
TRAIL_STOP     = 0.025
TRAIL_LOCK     = 0.01
STOP_LOSS      = 0.015
GAP_FILTER     = 0.04


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
    if len(closes_so_far) < 2 or vol_ratio < 1.0:
        return "SKIP"
    day_open   = closes_so_far[0]
    day_change = (closes_so_far[-1] - day_open) / day_open if day_open else 0
    if day_change < -0.02 and vol_ratio < 2.0:
        return "SKIP"
    score  = 1 if vol_ratio >= 1.5 else 0
    recent = closes_so_far[-min(12, len(closes_so_far)):]
    flips  = sum(1 for j in range(1, len(recent) - 1)
                 if (recent[-j] - recent[-j-1]) * (recent[-j-1] - recent[-j-2]) < 0)
    score += 1 if flips < 3 else -1
    if score >= 2:   return "TAKE"
    elif score >= 0: return "MAYBE"
    else:            return "SKIP"


def find_exit(closes, times, entry_price, entry_bar):
    peak = entry_price
    for i in range(entry_bar + 1, len(closes)):
        price = closes[i]
        peak  = max(peak, price)
        if times[i] >= ENTRY_CLOSE:
            return closes[i], times[i], "TIME_CLOSE"
        if price >= entry_price * (1 + TAKE_PROFIT):
            return price, times[i], "TAKE_PROFIT"
        if peak >= entry_price * (1 + TRAIL_LOCK) and price <= peak * (1 - TRAIL_STOP):
            return price, times[i], "TRAILING_STOP"
        if price <= entry_price * (1 - STOP_LOSS):
            return price, times[i], "STOP_LOSS"
    return closes[-1], times[-1], "EOD"


def analyze_day(closes, highs, lows, volumes, times, prior_close, alloc):
    """
    Finds the ORB entry (if any), checks whether bar+1 confirms,
    and returns both the original and confirmation-bar outcomes.
    """
    if len(closes) <= ORB_BARS:
        return None

    # Gap filter — skip ORB if gap > 4%
    if prior_close and closes:
        gap = abs(closes[0] - prior_close) / prior_close
        if gap > GAP_FILTER:
            return None

    avg_vol  = sum(volumes) / len(volumes) if volumes else 1
    orb_high = max(closes[:ORB_BARS])

    for i in range(ORB_BARS, len(closes)):
        if times[i] > ORB_CUTOFF:
            break
        if closes[i] > orb_high:
            if score_signal(closes[:i+1], volumes[i], avg_vol) == "SKIP":
                continue

            # Found the ORB breakout bar
            entry_orig  = closes[i]
            confirm_bar = i + 1

            # Check confirmation: does bar+1 also close above ORB high?
            if confirm_bar < len(closes):
                confirmed    = closes[confirm_bar] > orb_high
                entry_conf   = closes[confirm_bar] if confirmed else None
            else:
                confirmed    = False
                entry_conf   = None

            # Original outcome
            exit_p, exit_t, exit_r = find_exit(closes, times, entry_orig, i)
            pnl_orig = round((exit_p - entry_orig) / entry_orig * alloc, 2)

            # Confirmation outcome (only if confirmed)
            pnl_conf = None
            if confirmed and entry_conf:
                exit_p2, exit_t2, exit_r2 = find_exit(closes, times, entry_conf, confirm_bar)
                pnl_conf = round((exit_p2 - entry_conf) / entry_conf * alloc, 2)

            return {
                "orb_high":     round(orb_high, 2),
                "entry_bar":    i,
                "entry_time":   times[i],
                "entry_orig":   round(entry_orig, 2),
                "bar1_close":   round(closes[confirm_bar], 2) if confirm_bar < len(closes) else None,
                "confirmed":    confirmed,
                "entry_conf":   round(entry_conf, 2) if entry_conf else None,
                "exit_reason":  exit_r,
                "pnl_orig":     pnl_orig,
                "pnl_conf":     pnl_conf,
            }

    return None


def run():
    print("Loading backfill data...")
    with open(os.path.join(BASE_DIR, "backfill.json")) as f:
        backfill = json.load(f)

    ex1_days = sorted(
        [e for e in backfill if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    key, secret = _load_creds()
    client      = StockHistoricalDataClient(api_key=key, secret_key=secret)

    all_results = []

    print(f"Analyzing {len(ex1_days)} days...\n")

    for ex in ex1_days:
        date         = ex["date"]
        market_state = ex.get("market_state", "neutral")
        alloc_map    = {"TAKE": 1750.0 if market_state == "bullish" else (500.0 if market_state == "bearish" else 1500.0),
                        "MAYBE": 1000.0 if market_state == "bullish" else (500.0 if market_state == "bearish" else 750.0)}

        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt   = start_dt + timedelta(days=1)

        # Prior closes
        prior_daily = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=TICKERS, timeframe=TimeFrame.Day,
            start=start_dt - timedelta(days=7), end=start_dt, feed="iex",
        ))
        prior_closes = {t: (prior_daily.data[t][-1].close if prior_daily.data.get(t) else None)
                        for t in TICKERS}

        intraday = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=TICKERS, timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))

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

                # Use TAKE alloc as default (signal score isn't re-derived here)
                alloc  = alloc_map["TAKE"]
                result = analyze_day(closes, highs, lows, volumes, times,
                                     prior_closes.get(ticker), alloc)
                if result:
                    result["date"]   = date
                    result["ticker"] = ticker
                    all_results.append(result)
            except Exception:
                pass

        print(f"  {date} done")

    # --- Analysis ---
    total           = len(all_results)
    false_breaks    = [r for r in all_results if not r["confirmed"]]
    confirmed_orbs  = [r for r in all_results if r["confirmed"]]

    fb_pnl_saved    = sum(-r["pnl_orig"] for r in false_breaks if r["pnl_orig"] < 0)
    fb_gains_lost   = sum(r["pnl_orig"]  for r in false_breaks if r["pnl_orig"] > 0)

    conf_pnl_diff   = sum((r["pnl_conf"] or 0) - r["pnl_orig"] for r in confirmed_orbs)

    print(f"\n{'='*62}")
    print(f"ORB CONFIRMATION BAR ANALYSIS — {len(ex1_days)} days, {len(TICKERS)} tickers")
    print(f"{'='*62}")
    print(f"\nTotal ORB entries found:      {total}")
    print(f"  Confirmed (bar+1 held):     {len(confirmed_orbs)}  ({len(confirmed_orbs)/total*100:.0f}%)")
    print(f"  False breakouts (bar+1 dipped): {len(false_breaks)}  ({len(false_breaks)/total*100:.0f}%)")

    print(f"\nFalse breakout trades ({len(false_breaks)} total):")
    print(f"  Of those, were losses:  {sum(1 for r in false_breaks if r['pnl_orig'] < 0)}")
    print(f"  Of those, were wins:    {sum(1 for r in false_breaks if r['pnl_orig'] > 0)}")
    print(f"  Losses we'd save:       ${fb_pnl_saved:.2f}")
    print(f"  Wins we'd miss:         ${fb_gains_lost:.2f}")
    print(f"  Net from filtering FBs: ${fb_pnl_saved - fb_gains_lost:+.2f}")

    print(f"\nConfirmed entries — entry price penalty:")
    avg_slip = sum((r["entry_conf"] - r["entry_orig"]) / r["entry_orig"] * 100
                   for r in confirmed_orbs if r["entry_conf"]) / len(confirmed_orbs) if confirmed_orbs else 0
    print(f"  Avg entry price increase: {avg_slip:+.3f}%")
    print(f"  Total P&L change on confirmed entries: ${conf_pnl_diff:+.2f}")

    print(f"\nOverall net impact of confirmation bar rule:")
    net = (fb_pnl_saved - fb_gains_lost) + conf_pnl_diff
    print(f"  ${net:+.2f}  ({'better' if net > 0 else 'worse'} than current)")

    print(f"\n--- False breakout detail ---")
    print(f"  {'Date':<12} {'Ticker':<6} {'Entry':>8} {'Bar+1':>7} {'ORB Hi':>7}  {'P&L':>8}  Exit reason")
    print(f"  {'-'*60}")
    for r in sorted(false_breaks, key=lambda x: x["pnl_orig"]):
        held = "above" if r["bar1_close"] and r["bar1_close"] > r["orb_high"] else "below"
        print(f"  {r['date']:<12} {r['ticker']:<6} {r['entry_orig']:>8.2f} "
              f"{r['bar1_close'] or 0:>7.2f} {r['orb_high']:>7.2f}  "
              f"{r['pnl_orig']:>+7.2f}   {r['exit_reason']}")

    print()


if __name__ == "__main__":
    run()
