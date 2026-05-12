"""
test_maybe_filters.py — Compare 3 approaches to reducing MAYBE-entry stop losses.

Option 1: TAKE-only — skip all MAYBE-rated entries entirely
Option 2: Skip MAYBE on BEAR days (SPY gap <= -0.3% OR VIXY up >= 3%)
Option 3: Skip MAYBE where choppiness was negative at entry
          (flips >= 3 in the last 12 closes up to the entry bar)

Uses exercises.json trade list (no re-simulation of exits — just drops the
skipped trades and sums remaining P&L to show net impact).

Usage: venv/bin/python3 test_maybe_filters.py
"""

import json, os
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def time_to_mins(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def compute_flips(closes):
    """Count direction flips in a close price sequence."""
    recent = closes[-min(12, len(closes)):]
    return sum(
        1 for j in range(1, len(recent) - 1)
        if (recent[-j] - recent[-j-1]) * (recent[-j-1] - recent[-j-2]) < 0
    )


def get_market_state(date, spy_bars, vixy_bars):
    """
    Compute BULL/NEUTRAL/BEAR for a given date using the same thresholds as ex1.py:
      BEAR: spy_gap <= -0.3% OR vixy_trend >= +3%
      BULL: spy_gap >= +0.5% AND vixy_trend < +3%
      else: NEUTRAL
    spy_gap  = today's first bar close vs yesterday's last bar close
    vixy_trend = today's first VIXY bar close vs yesterday's last VIXY bar close
    """
    spy_today    = [b for b in spy_bars  if b["date"] == date]
    vixy_today   = [b for b in vixy_bars if b["date"] == date]
    spy_prev     = [b for b in spy_bars  if b["date"] <  date]
    vixy_prev    = [b for b in vixy_bars if b["date"] <  date]

    if not spy_today or not spy_prev:
        return "neutral"

    spy_gap_pct   = (spy_today[0]["close"]  - spy_prev[-1]["close"])  / spy_prev[-1]["close"]  * 100
    vixy_trend_pct = 0.0
    if vixy_today and vixy_prev:
        vixy_trend_pct = (vixy_today[0]["close"] - vixy_prev[-1]["close"]) / vixy_prev[-1]["close"] * 100

    if spy_gap_pct <= -0.3 or vixy_trend_pct >= 3.0:
        return "bearish"
    elif spy_gap_pct >= 0.5 and vixy_trend_pct < 3.0:
        return "bullish"
    return "neutral"


def main():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        data = json.load(f)

    ex1_days = sorted(
        [e for e in data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    key, secret = _load_creds()
    client = StockHistoricalDataClient(api_key=key, secret_key=secret)

    # Fetch SPY + VIXY across full window for market state
    first_date = ex1_days[0]["date"]
    last_date  = ex1_days[-1]["date"]
    win_start  = (datetime.strptime(first_date, "%Y-%m-%d") - timedelta(days=5)).replace(tzinfo=timezone.utc)
    win_end    = datetime.strptime(last_date,  "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)

    print("Fetching SPY and VIXY for market state...")
    spy_resp  = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols="SPY",  timeframe=TimeFrame.Minute,
        start=win_start, end=win_end, feed="iex"
    ))
    vixy_resp = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols="VIXY", timeframe=TimeFrame.Minute,
        start=win_start, end=win_end, feed="iex"
    ))

    def extract_bars(resp, sym):
        raw = resp.data.get(sym, [])
        out = []
        for bar in raw:
            ts_et = bar.timestamp.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
            if ts_et.strftime("%H:%M") >= "09:30":
                out.append({"date": ts_et.strftime("%Y-%m-%d"),
                             "time": ts_et.strftime("%H:%M"),
                             "close": bar.close})
        return sorted(out, key=lambda b: (b["date"], b["time"]))

    spy_bars  = extract_bars(spy_resp,  "SPY")
    vixy_bars = extract_bars(vixy_resp, "VIXY")

    # Pre-compute market state per day
    market_states = {
        day["date"]: get_market_state(day["date"], spy_bars, vixy_bars)
        for day in ex1_days
    }
    print("Market states:")
    for date, state in market_states.items():
        pnl = next(e["total_pnl"] for e in ex1_days if e["date"] == date)
        print(f"  {date}  {state:<10}  day P&L={pnl:+.2f}")

    # Fetch 1-min bars for option 3 (choppiness)
    print("\nFetching intraday bars for choppiness calculation...")
    all_tickers = list({t["ticker"] for e in ex1_days for t in e["trades"]})
    ticker_bars_by_date = {}  # {date: {ticker: [bar, ...]}}

    for day in ex1_days:
        date     = day["date"]
        tickers  = list({t["ticker"] for t in day["trades"]})
        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt   = start_dt + timedelta(days=1)

        try:
            resp = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=tickers, timeframe=TimeFrame.Minute,
                start=start_dt, end=end_dt, feed="iex"
            ))
        except Exception as e:
            print(f"  {date}: fetch failed — {e}")
            ticker_bars_by_date[date] = {}
            continue

        day_bars = {}
        for ticker in tickers:
            raw = resp.data.get(ticker, [])
            bars = []
            for bar in raw:
                ts_et = bar.timestamp.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
                t_str = ts_et.strftime("%H:%M")
                if "09:30" <= t_str <= "15:59":
                    bars.append({"time": t_str, "close": bar.close})
            day_bars[ticker] = sorted(bars, key=lambda b: b["time"])
        ticker_bars_by_date[date] = day_bars
        print(f"  {date}: fetched {len(tickers)} tickers")

    # Categorise each trade under each option
    print("\n--- Trade-by-trade: MAYBE entries only ---")
    print(f"{'Date':<12} {'Ticker':<6} {'Vol':>5} {'Rating':<6} {'Exit':<15} {'P&L':>8}  "
          f"{'Mkt':>8}  {'Chop':>5}  O1  O2  O3")
    print("-" * 88)

    results = {"baseline": 0.0, "opt1": 0.0, "opt2": 0.0, "opt3": 0.0}
    skipped = {"opt1": [], "opt2": [], "opt3": []}
    kept    = {"opt1": [], "opt2": [], "opt3": []}

    for day in ex1_days:
        date   = day["date"]
        state  = market_states[date]
        day_bars = ticker_bars_by_date.get(date, {})

        for t in day["trades"]:
            results["baseline"] += t["pnl"]

            if t["rating"] != "MAYBE":
                for opt in ["opt1", "opt2", "opt3"]:
                    results[opt] += t["pnl"]
                    kept[opt].append(t)
                continue

            # Option 1: skip all MAYBE
            skip1 = True

            # Option 2: skip MAYBE on BEAR days
            skip2 = (state == "bearish")

            # Option 3: skip MAYBE where choppiness was high at entry
            bars = day_bars.get(t["ticker"], [])
            entry_mins = time_to_mins(t["time"])
            closes_to_entry = [b["close"] for b in bars
                               if time_to_mins(b["time"]) <= entry_mins]
            flips = compute_flips(closes_to_entry) if len(closes_to_entry) >= 3 else 0
            choppy = flips >= 3
            skip3 = choppy

            for opt, skip in [("opt1", skip1), ("opt2", skip2), ("opt3", skip3)]:
                if skip:
                    skipped[opt].append(t)
                else:
                    results[opt] += t["pnl"]
                    kept[opt].append(t)

            mkt_label = state[:4].upper()
            print(f"{date:<12} {t['ticker']:<6} {t['vol_ratio']:>4.1f}x "
                  f"{t['rating']:<6} {t['exit_reason']:<15} {t['pnl']:>+8.2f}  "
                  f"{mkt_label:>8}  {'Y' if choppy else 'N':>5}  "
                  f"{'SKIP' if skip1 else 'keep':>4}  "
                  f"{'SKIP' if skip2 else 'keep':>4}  "
                  f"{'SKIP' if skip3 else 'keep':>4}")

    print(f"\n{'='*70}")
    print(f"SUMMARY (EX1, 10 days)")
    print(f"  Baseline (current):          ${results['baseline']:>8.2f}")
    for opt, label in [("opt1","TAKE-only"),
                       ("opt2","Skip MAYBE on BEAR days"),
                       ("opt3","Skip MAYBE when choppy")]:
        net     = round(results[opt] - results["baseline"], 2)
        n_skip  = len(skipped[opt])
        sl_skip = sum(1 for t in skipped[opt] if t["exit_reason"] == "STOP_LOSS")
        w_skip  = sum(1 for t in skipped[opt] if t["pnl"] > 0)
        pnl_skip = sum(t["pnl"] for t in skipped[opt])
        print(f"\n  {label}")
        print(f"    Total P&L:  ${results[opt]:>8.2f}  (net {net:>+.2f} vs baseline)")
        print(f"    Trades skipped: {n_skip}  "
              f"(stop losses avoided: {sl_skip}  |  winners missed: {w_skip}  |  skipped P&L: {pnl_skip:+.2f})")


if __name__ == "__main__":
    main()
