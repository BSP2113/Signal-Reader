"""
hybrid.py — Exercise 3: Hybrid session picker

Reads the pre-market state and routes to EX1 logic on BULL days or EX2 logic on NEUT/BEAR days.
Results log to exercises.json as "Exercise 3 - Hybrid".

BULL  → EX1: larger positions, no re-entries, let winners run
NEUT/BEAR → EX2: conviction sizing, re-entries, PM_ORB signals

Run manually:  venv/bin/python3 hybrid.py [YYYY-MM-DD]
Cron calls it: venv/bin/python3 hybrid.py  (defaults to today)
"""

import json
import os
import sys
from datetime import datetime

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SPY_BULL   =  0.004   # must stay in sync with ex1.py / ex2.py
SPY_BEAR   = -0.005
VIXY_SURGE =  0.03
TITLE      = "Exercise 3 - Hybrid"


def get_market_state(trade_date):
    """Read pre-market state from market_state.json (today) or market_states_historical.json (past)."""
    state_path = os.path.join(BASE_DIR, "market_state.json")
    hist_path  = os.path.join(BASE_DIR, "market_states_historical.json")
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
        return "bearish", spy_gap_pct, vixy_trend_pct
    elif spy_gap_pct / 100 >= SPY_BULL and vixy_trend_pct / 100 < VIXY_SURGE:
        return "bullish", spy_gap_pct, vixy_trend_pct
    return "neutral", spy_gap_pct, vixy_trend_pct


if __name__ == "__main__":
    backfill   = "--backfill" in sys.argv
    pos_args   = [a for a in sys.argv[1:] if not a.startswith("--")]
    trade_date = pos_args[0] if pos_args else datetime.now().strftime("%Y-%m-%d")

    state, spy_gap, vixy = get_market_state(trade_date)
    print(f"Hybrid — {trade_date}")
    print(f"  Pre-market: {state.upper()}  (SPY {spy_gap:+.2f}%  VIXY {vixy:+.2f}%)")

    if state == "bullish":
        print("  → EX1 mode: larger positions, no re-entries\n")
        from ex1 import run_ex1
        run_ex1(trade_date, backfill=backfill, title=TITLE)
    else:
        print(f"  → EX2 mode: conviction sizing, re-entries, PM signals\n")
        from ex2 import run_ex2
        run_ex2(trade_date, backfill=backfill, title=TITLE)
