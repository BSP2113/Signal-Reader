#!/usr/bin/env python3
"""
test_early_warning_gates.py
Tests 4 early session warning gates individually on the 12 live EX1 trading days.

Gate 1 — GAP_GO early trail:     if any GAP_GO exits TRAILING_STOP before 09:45,
                                   raise ORB volume bar to >=2.5x for the rest of the day.
Gate 2 — GAP_GO flat at T+20:    if the first GAP_GO is flat/negative 20 min after entry,
                                   require >=2.0x vol on subsequent MAYBE ORBs.
Gate 3 — T+20 weakness:          if 2+ open positions are simultaneously at/below entry
                                   price at any T+20 check, block new ORB entries for 15 min.
Gate 4 — Quick-stop pause:        if any position stops within 45 min of entry,
                                   pause new ORB entries for 15 min.

Gates 1 and 4 use exercises.json only.
Gates 2 and 3 fetch 1-minute intraday data from Alpaca to check mid-session prices.

Results show: which trades get blocked per day, P&L impact, and net across all 12 days.
"""

import json, os, sys
from datetime import datetime, timedelta, timezone
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = "America/New_York"

TICKERS = ["NVDA","TSLA","AMD","COIN","META","PLTR","SMCI","CRDO","APP","RIVN",
           "CRWD","KOPN","SHOP","SOFI","ARM","DKNG","RKLB","RDDT"]


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_creds():
    creds = {}
    with open(os.path.join(BASE_DIR, ".env")) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds["ALPACA_API_KEY"], creds["ALPACA_API_SECRET"]


def t2m(t):
    """'HH:MM' → minutes since midnight."""
    return int(t[:2]) * 60 + int(t[3:])


def m2t(m):
    """minutes since midnight → 'HH:MM'."""
    return f"{m//60:02d}:{m%60:02d}"


def fetch_intraday(client, date_str):
    """
    Fetch 1-min closes for all tickers on date_str.
    Returns dict: {ticker: {time_str: close_price}}
    """
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end   = start + timedelta(days=1)
    try:
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=TICKERS, timeframe=TimeFrame.Minute,
            start=start, end=end, feed="iex",
        ))
        df = bars.df
        if df.empty:
            return {}
        if isinstance(df.index, pd.MultiIndex):
            result = {}
            for ticker in TICKERS:
                try:
                    sub = df.xs(ticker, level=0).tz_convert(ET)
                    session = sub.between_time("09:30", "15:59")
                    result[ticker] = {t.strftime("%H:%M"): row["close"]
                                      for t, row in session.iterrows()}
                except Exception:
                    pass
            return result
    except Exception as e:
        print(f"    Fetch error: {e}")
    return {}


def price_at(ticker_prices, ticker, time_str):
    """Return the most recent close price at or before time_str, or None."""
    prices = ticker_prices.get(ticker, {})
    result = None
    for t in sorted(prices):
        if t <= time_str:
            result = prices[t]
        else:
            break
    return result


# ── gate logic ───────────────────────────────────────────────────────────────

def gate1_blocked(trades):
    """
    GAP_GO early trail gate (exercises.json only — no intraday needed).
    Trigger: any GAP_GO exits TRAILING_STOP at or before 09:45.
    Effect:  block subsequent ORB entries with vol_ratio < 2.5.
    """
    CUTOFF   = "09:45"
    VOL_BAR  = 2.5

    triggers = [t for t in trades
                if t.get("signal") == "GAP_GO"
                and t.get("exit_reason") == "TRAILING_STOP"
                and t.get("exit_time", "99:99") <= CUTOFF]
    if not triggers:
        return [], None

    gate_time = min(t["exit_time"] for t in triggers)
    blocked = [t for t in trades
               if t.get("signal") == "ORB"
               and t["time"] > gate_time
               and t.get("vol_ratio", 99) < VOL_BAR]
    return blocked, gate_time


def gate4_blocked(trades):
    """
    Quick-stop pause gate (exercises.json only — no intraday needed).
    Trigger: any position stops (STOP_LOSS) within 45 min of entry.
    Effect:  block new ORB entries in the 15-min window after that stop.
    """
    STOP_WINDOW = 45
    PAUSE       = 15

    blocked = []
    blocked_ids = set()

    quick_stops = []
    for t in trades:
        if t.get("exit_reason") == "STOP_LOSS":
            dur = t2m(t["exit_time"]) - t2m(t["time"])
            if dur <= STOP_WINDOW:
                quick_stops.append(t)

    if not quick_stops:
        return [], None

    gate_times = []
    for qs in quick_stops:
        stop_mins = t2m(qs["exit_time"])
        end_mins  = stop_mins + PAUSE
        gate_times.append(qs["exit_time"])
        for t in trades:
            tid = id(t)
            if tid in blocked_ids:
                continue
            if (t.get("signal") == "ORB"
                    and stop_mins < t2m(t["time"]) <= end_mins):
                blocked.append(t)
                blocked_ids.add(tid)

    first_gate = min(gate_times) if gate_times else None
    return blocked, first_gate


def gate2_blocked(trades, intraday):
    """
    GAP_GO flat at T+20 gate (requires intraday prices).
    Trigger: first GAP_GO position is flat/negative 20 min after entry.
    Effect:  block subsequent MAYBE ORB entries with vol_ratio < 2.0.
    """
    T20     = 20
    VOL_BAR = 2.0

    gap_trades = [t for t in trades if t.get("signal") == "GAP_GO"]
    if not gap_trades:
        return [], None

    first = min(gap_trades, key=lambda t: t["time"])
    t20   = m2t(t2m(first["time"]) + T20)

    # If the GAP_GO exited before T+20, use its exit price; otherwise look up intraday
    if first.get("exit_time", "99:99") <= t20:
        p20 = first["exit"]
    else:
        p20 = price_at(intraday, first["ticker"], t20)

    if p20 is None:
        return [], None

    if p20 > first["entry"]:
        return [], None  # positive at T+20 — gate doesn't fire

    blocked = [t for t in trades
               if t.get("signal") == "ORB"
               and t.get("rating") == "MAYBE"
               and t["time"] > t20
               and t.get("vol_ratio", 99) < VOL_BAR
               and t is not first]
    return blocked, t20


def gate3_blocked(trades, intraday):
    """
    T+20 weakness gate (requires intraday prices).
    Trigger: 2+ open positions simultaneously at/below entry price at any T+20 check.
    Effect:  block new ORB entries for 15 min after the trigger time.
    """
    T20       = 20
    BLOCK_DUR = 15

    sorted_trades = sorted(trades, key=lambda t: t["time"])

    # Check points: T+20 after each entry (unique times)
    check_times = sorted({m2t(t2m(t["time"]) + T20) for t in sorted_trades})

    gate_time = None
    for ct in check_times:
        # Positions open at check_time
        open_pos = [t for t in sorted_trades
                    if t["time"] <= ct and t.get("exit_time", "99:99") > ct]
        below = 0
        for pos in open_pos:
            p = price_at(intraday, pos["ticker"], ct)
            if p is not None and p <= pos["entry"]:
                below += 1
        if below >= 2:
            gate_time = ct
            break

    if gate_time is None:
        return [], None

    end_mins = t2m(gate_time) + BLOCK_DUR
    blocked = [t for t in sorted_trades
               if t.get("signal") == "ORB"
               and gate_time < t["time"] <= m2t(end_mins)]
    return blocked, gate_time


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        all_data = json.load(f)

    ex1_days = sorted(
        [e for e in all_data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    key, secret = _load_creds()
    client = StockHistoricalDataClient(api_key=key, secret_key=secret)

    gates = {
        "Gate 1 — GAP_GO early trail (≥2.5x after trail<09:45)": None,
        "Gate 2 — GAP_GO flat at T+20 (≥2.0x MAYBE after gap stall)": None,
        "Gate 3 — T+20 weakness (block 15min when 2+ positions below entry)": None,
        "Gate 4 — Quick-stop pause (block 15min after stop within 45min)": None,
    }

    results = {g: {"days": [], "total_delta": 0.0} for g in gates}

    print(f"\nTesting on {len(ex1_days)} live days...\n")

    for ex in ex1_days:
        date = ex["date"]
        trades = ex["trades"]
        base_pnl = ex["total_pnl"]

        print(f"  {date}  (EX1 P&L: ${base_pnl:+.2f})")

        # Fetch intraday only once per day (needed for gates 2 & 3)
        print(f"    Fetching intraday data...", end=" ", flush=True)
        intraday = fetch_intraday(client, date)
        print("done")

        # --- Gate 1 ---
        blocked, gt = gate1_blocked(trades)
        delta = -sum(t["pnl"] for t in blocked)
        alt   = round(base_pnl + delta, 2)
        results["Gate 1 — GAP_GO early trail (≥2.5x after trail<09:45)"]["days"].append(
            (date, base_pnl, alt, blocked, gt))
        results["Gate 1 — GAP_GO early trail (≥2.5x after trail<09:45)"]["total_delta"] += delta

        # --- Gate 2 ---
        blocked, gt = gate2_blocked(trades, intraday)
        delta = -sum(t["pnl"] for t in blocked)
        alt   = round(base_pnl + delta, 2)
        results["Gate 2 — GAP_GO flat at T+20 (≥2.0x MAYBE after gap stall)"]["days"].append(
            (date, base_pnl, alt, blocked, gt))
        results["Gate 2 — GAP_GO flat at T+20 (≥2.0x MAYBE after gap stall)"]["total_delta"] += delta

        # --- Gate 3 ---
        blocked, gt = gate3_blocked(trades, intraday)
        delta = -sum(t["pnl"] for t in blocked)
        alt   = round(base_pnl + delta, 2)
        results["Gate 3 — T+20 weakness (block 15min when 2+ positions below entry)"]["days"].append(
            (date, base_pnl, alt, blocked, gt))
        results["Gate 3 — T+20 weakness (block 15min when 2+ positions below entry)"]["total_delta"] += delta

        # --- Gate 4 ---
        blocked, gt = gate4_blocked(trades)
        delta = -sum(t["pnl"] for t in blocked)
        alt   = round(base_pnl + delta, 2)
        results["Gate 4 — Quick-stop pause (block 15min after stop within 45min)"]["days"].append(
            (date, base_pnl, alt, blocked, gt))
        results["Gate 4 — Quick-stop pause (block 15min after stop within 45min)"]["total_delta"] += delta

    # ── print results ─────────────────────────────────────────────────────────
    print("\n" + "="*70)
    for gate_name, data in results.items():
        print(f"\n{gate_name}")
        print("-" * 70)
        triggered = False
        for (date, base, alt, blocked, gt) in data["days"]:
            if not blocked:
                continue
            triggered = True
            delta = round(alt - base, 2)
            sign  = "+" if delta >= 0 else ""
            print(f"  {date}  base ${base:+.2f}  →  alt ${alt:+.2f}  "
                  f"({sign}${delta:.2f})  [gate at {gt}]")
            for t in blocked:
                print(f"    BLOCKED: {t['ticker']} {t.get('signal','?')} "
                      f"{t['time']} {t.get('rating','?')} {t.get('vol_ratio','?')}x  "
                      f"exit {t.get('exit_reason','?')} ${t['pnl']:+.2f}")
        if not triggered:
            print("  No days triggered.")
        net = round(data["total_delta"], 2)
        sign = "+" if net >= 0 else ""
        print(f"\n  Net across 12 days: {sign}${net:.2f}")
    print("\n" + "="*70)


if __name__ == "__main__":
    main()
