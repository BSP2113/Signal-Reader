"""
dry_run_multi.py — Run dry_run.py across multiple dates and produce a summary.

Captures stdout from each date's run, parses the result line, and emits a
single comparison table. Useful for spotting systemic divergence between the
live runner and the simulator across a sample of representative days.
"""

import os
import sys
import subprocess
import re
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Mix of live and backfill days, including known edge cases:
#   - 5/8: latest live day
#   - 5/6: big +$133 baseline day (TIME_CLOSE-driven wins)
#   - 4/30: KOPN +4.77% TAKE_PROFIT day
#   - 4/24: GAP_GO cluster day (ARM, AMD, SMCI, KOPN take-profits)
#   - 4/13: best live day +$246 (mostly TAKE-rated)
#   - 3/25: big backfill day +$105
#   - 3/5:  baseline -$77 day (should test daily loss limit halt)
#   - 4/28: -$47 medium-loss day
#   - 4/21: -$38 medium-loss day with multiple stops
DATES = [
    "2026-05-08",
    "2026-05-06",
    "2026-04-30",
    "2026-04-24",
    "2026-04-13",
    "2026-03-25",
    "2026-03-05",
    "2026-04-28",
    "2026-04-21",
]


def run_one(date: str) -> dict:
    cmd = ["venv/bin/python3", "dry_run.py", date]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=BASE_DIR)
    out = proc.stdout
    if proc.returncode != 0:
        return {"date": date, "error": proc.stderr.strip()[:300] or out[-300:]}

    # Parse the result block
    def grab(rgx, default=None, conv=str, flags=0):
        m = re.search(rgx, out, flags)
        return conv(m.group(1)) if m else default

    # Multi-line patterns need re.DOTALL so '.' matches newlines.
    dry_pnl    = grab(r"DRY-RUN RESULT.*?P&L:\s+\$([+-]?[\d.]+)", 0.0, float, re.DOTALL)
    sim_pnl    = grab(r"sim P&L:\s+\$([+-]?[\d.]+)",              0.0, float)
    dry_trades = grab(r"DRY-RUN RESULT.*?trades:\s+(\d+)",        0,   int,   re.DOTALL)
    dry_wins   = grab(r"DRY-RUN RESULT.*?\((\d+) wins\)",         0,   int,   re.DOTALL)
    sim_trades = grab(r"sim trades:\s+(\d+)",                     0,   int)
    sim_wins   = grab(r"sim trades:\s+\d+\s+\((\d+) wins\)",      0,   int)
    halted     = grab(r"halted:\s+(True|False)",                  "False")
    market     = grab(r"market_state:\s+(\w+)",                   "?")

    return {
        "date":       date,
        "market":     market,
        "dry_pnl":    dry_pnl,
        "sim_pnl":    sim_pnl,
        "delta":      round(dry_pnl - sim_pnl, 2),
        "dry_trades": dry_trades,
        "dry_wins":   dry_wins,
        "sim_trades": sim_trades,
        "sim_wins":   sim_wins,
        "halted":     halted == "True",
    }


if __name__ == "__main__":
    results = []
    for i, date in enumerate(DATES, 1):
        print(f"[{i}/{len(DATES)}] {date}...", flush=True)
        r = run_one(date)
        results.append(r)
        if "error" in r:
            print(f"  FAILED: {r['error'][:120]}")
        else:
            print(f"  dry ${r['dry_pnl']:+.2f} ({r['dry_trades']}t/{r['dry_wins']}w)  "
                  f"vs sim ${r['sim_pnl']:+.2f} ({r['sim_trades']}t/{r['sim_wins']}w)  "
                  f"delta {r['delta']:+.2f}  halted={r['halted']}")

    # Summary table
    print(f"\n{'═'*86}")
    print(f"  MULTI-DATE DRY RUN — {len(results)} dates")
    print(f"{'═'*86}")
    print(f"  {'date':<12}  {'mkt':<8}  {'dry $':>10}  {'sim $':>10}  {'delta':>10}  "
          f"{'dry t/w':>9}  {'sim t/w':>9}  {'halt':>5}")
    print("  " + "─" * 84)
    dry_total, sim_total = 0.0, 0.0
    for r in results:
        if "error" in r:
            print(f"  {r['date']:<12}  ERROR: {r['error'][:50]}")
            continue
        dry_total += r["dry_pnl"]
        sim_total += r["sim_pnl"]
        print(f"  {r['date']:<12}  {r['market']:<8}  "
              f"${r['dry_pnl']:>+9.2f}  ${r['sim_pnl']:>+9.2f}  ${r['delta']:>+9.2f}  "
              f"{r['dry_trades']:>3}/{r['dry_wins']:<5}  "
              f"{r['sim_trades']:>3}/{r['sim_wins']:<5}  "
              f"{'YES' if r['halted'] else '':>5}")
    print("  " + "─" * 84)
    print(f"  {'TOTAL':<12}  {'':<8}  ${dry_total:>+9.2f}  ${sim_total:>+9.2f}  "
          f"${dry_total - sim_total:>+9.2f}")
    print()
