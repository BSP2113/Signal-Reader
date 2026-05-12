"""
orb_debug.py — Traces ORB logic for each ticker on a given day.
Shows: ORB high, whether any bar broke it before 11:30, and why it was blocked.
Run: venv/bin/python3 orb_debug.py [YYYY-MM-DD]
"""

import os
import yfinance as yf
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ex1 import calc_vwap, score_signal, ORB_BARS, ORB_CUTOFF, ENTRY_CLOSE

TICKERS = ["NVDA", "TSLA", "AMD", "COIN", "META", "PLTR", "MSTR", "SMCI", "NFLX", "HOOD"]


def debug_orb(trade_date):
    print(f"\nORB Debug — {trade_date}")
    print(f"Opening range: 9:30–9:44 ({ORB_BARS} bars) | Cutoff: {ORB_CUTOFF}\n")

    for ticker in TICKERS:
        data  = yf.download(ticker, period="7d", interval="1m", progress=False, auto_adjust=True)
        data  = data.tz_convert("America/New_York")
        today = data[data.index.strftime("%Y-%m-%d") == trade_date].between_time("09:30", "15:59")

        if today.empty:
            print(f"  {ticker:5s} — no data")
            continue

        closes  = [round(float(v), 2) for v in today["Close"].squeeze().tolist()]
        highs   = [round(float(v), 2) for v in today["High"].squeeze().tolist()]
        lows    = [round(float(v), 2) for v in today["Low"].squeeze().tolist()]
        volumes = [int(v) for v in today["Volume"].squeeze().tolist()]
        times   = [t.strftime("%H:%M") for t in today.index]

        if len(closes) <= ORB_BARS:
            print(f"  {ticker:5s} — not enough bars")
            continue

        avg_vol  = sum(volumes) / len(volumes)
        orb_high = max(closes[:ORB_BARS])
        open_px  = closes[0]

        breakouts = []
        for i in range(ORB_BARS, len(closes)):
            if times[i] > ORB_CUTOFF:
                break
            if closes[i] > orb_high:
                rating, vr = score_signal(closes[:i+1], volumes[i], avg_vol)
                breakouts.append((times[i], closes[i], rating, round(vr, 2)))

        if not breakouts:
            # Find how close it got
            peak_before_cutoff = max(
                (closes[i] for i in range(ORB_BARS, len(closes)) if times[i] <= ORB_CUTOFF),
                default=closes[ORB_BARS]
            )
            gap = round(orb_high - peak_before_cutoff, 2)
            pct = round(gap / orb_high * 100, 2)
            print(f"  {ticker:5s} — ORB high: ${orb_high:.2f} | open: ${open_px:.2f} | "
                  f"never broke out (came within ${gap} / {pct}% of target before {ORB_CUTOFF})")
        else:
            first = breakouts[0]
            status = "ENTERED" if first[2] != "SKIP" else f"BLOCKED ({first[2]})"
            print(f"  {ticker:5s} — ORB high: ${orb_high:.2f} | open: ${open_px:.2f} | "
                  f"broke out at {first[0]} @ ${first[1]:.2f} | vol {first[3]}x | {status}")
            if first[2] == "SKIP" and len(breakouts) > 1:
                for b in breakouts[1:]:
                    s2 = "ENTERED" if b[2] != "SKIP" else f"BLOCKED ({b[2]})"
                    print(f"          retry at {b[0]} @ ${b[1]:.2f} | vol {b[3]}x | {s2}")


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    debug_orb(date_arg)
