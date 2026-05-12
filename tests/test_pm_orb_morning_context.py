"""
test_pm_orb_morning_context.py — Two EX2 PM_ORB refinements tested together.

Context: PM_ORB trades on tickers with morning history show a split pattern.
Tickers that proved momentum in the morning (TAKE_PROFIT) should get more
capital. Tickers that already failed once (STOP_LOSS) should be skipped.

TEST A — Exclude morning STOP_LOSS tickers from PM_ORB:
  If a ticker exited its morning ORB/GAP_GO trade via STOP_LOSS, block its
  PM_ORB signal that afternoon. TRAILING_STOP and TIME_CLOSE exits remain
  eligible (different failure mode — not a hard rejection).

TEST B — Upgrade PM_ORB MAYBE → TAKE for morning TAKE_PROFIT tickers:
  If a ticker hit TAKE_PROFIT in the morning session, its PM_ORB MAYBE signal
  gets a TAKE-sized allocation. TAKE-rated PM_ORBs are unaffected (already
  at full allocation).

TEST C — Both A and B combined.

Run: venv/bin/python3 tests/test_pm_orb_morning_context.py
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOC_RATIOS = {
    "bullish":  {"TAKE": 0.35, "MAYBE": 0.20},
    "neutral":  {"TAKE": 0.30, "MAYBE": 0.15},
    "bearish":  {"TAKE": 0.10, "MAYBE": 0.10},
}


def load_ex2(filename):
    path = os.path.join(BASE_DIR, filename)
    with open(path) as f:
        data = json.load(f)
    return sorted([e for e in data if "Exercise 2" in e["title"]], key=lambda e: e["date"])


def morning_context(day):
    """
    For each ticker, determine its morning ORB/GAP_GO exit type.
    Returns two sets: stop_loss_tickers, take_profit_tickers.
    If a ticker had both (via re-entry), STOP_LOSS takes precedence.
    Only counts exits before PM window opens (12:44).
    """
    stop_tickers = set()
    take_tickers = set()
    for t in day["trades"]:
        if t.get("signal") not in ("ORB", "GAP_GO"):
            continue
        if t.get("exit_time", "") > "12:44":
            continue
        reason = t.get("exit_reason", "")
        ticker = t["ticker"]
        if reason == "STOP_LOSS":
            stop_tickers.add(ticker)
            take_tickers.discard(ticker)   # stop overrides take
        elif reason == "TAKE_PROFIT" and ticker not in stop_tickers:
            take_tickers.add(ticker)
    return stop_tickers, take_tickers


def upgrade_alloc(trade, day):
    """Scale a MAYBE PM_ORB trade's P&L as if it had a TAKE allocation."""
    market = day.get("market_state", "neutral")
    ratios = ALLOC_RATIOS.get(market, ALLOC_RATIOS["neutral"])
    take_pct  = ratios["TAKE"]
    maybe_pct = ratios["MAYBE"]
    if maybe_pct == 0 or take_pct == maybe_pct:
        return trade["pnl"]
    scale = take_pct / maybe_pct
    return round(trade["pnl"] * scale, 2)


def run_dataset(filename, label):
    days = load_ex2(filename)

    print(f"\n{'='*74}")
    print(f"PM_ORB Morning Context  [{label}]")
    print(f"{'='*74}")

    col = 11
    hdr = (f"{'Date':<12} {'Baseline':>{col}} {'BlockSL':>{col}}"
           f" {'UpgradeTP':>{col}} {'Combined':>{col}}")
    print(f"\n{hdr}")
    print("-" * (12 + col * 4 + 3))

    total_base = total_a = total_b = total_c = 0.0
    detail = []

    for day in days:
        baseline = day["total_pnl"]
        stop_tickers, take_tickers = morning_context(day)

        pnl_a = pnl_b = pnl_c = baseline

        for t in day["trades"]:
            if t.get("signal") != "PM_ORB":
                continue
            ticker = t["ticker"]
            pnl    = t["pnl"]

            # Test A: block if morning was STOP_LOSS
            if ticker in stop_tickers:
                pnl_a -= pnl
                pnl_c -= pnl
                detail.append((day["date"], ticker, t["time"], t["rating"],
                                "STOP_LOSS", pnl, "BLOCK", None))

            # Test B: upgrade MAYBE if morning was TAKE_PROFIT
            elif ticker in take_tickers and t.get("rating") == "MAYBE":
                new_pnl = upgrade_alloc(t, day)
                delta = new_pnl - pnl
                pnl_b += delta
                pnl_c += delta
                detail.append((day["date"], ticker, t["time"], t["rating"],
                                "TAKE_PROFIT", pnl, "UPGRADE", new_pnl))

        total_base += baseline
        total_a += pnl_a
        total_b += pnl_b
        total_c += pnl_c

        def _fmt(pnl, base=baseline):
            diff = pnl - base
            s = f"${pnl:+.2f}"
            if abs(diff) > 0.01:
                s += f"({'+' if diff >= 0 else ''}{diff:.2f})"
            return s

        has_change = abs(pnl_a - baseline) > 0.01 or abs(pnl_b - baseline) > 0.01
        marker = " *" if has_change else ""
        print(f"{day['date']:<12} {_fmt(baseline):>{col}} {_fmt(pnl_a):>{col}}"
              f" {_fmt(pnl_b):>{col}} {_fmt(pnl_c):>{col}}{marker}")

    print("-" * (12 + col * 4 + 3))
    def _tot(v, base=total_base):
        d = v - base
        return f"${v:+.2f} ({'+' if d>=0 else ''}{d:.2f})"
    print(f"{'TOTAL':<12} ${total_base:>+9.2f} {_tot(total_a):>{col}} {_tot(total_b):>{col}} {_tot(total_c):>{col}}")

    return detail, total_base, total_a, total_b, total_c


def combined_summary(ex_detail, bf_detail, ex_bases, bf_bases):
    all_detail = ex_detail + bf_detail

    # Deduplicate on date+ticker+time
    seen = set()
    uniq = []
    for row in all_detail:
        key = (row[0], row[1], row[2])
        if key not in seen:
            seen.add(key)
            uniq.append(row)

    print(f"\n{'='*74}")
    print("COMBINED SUMMARY (45 days, deduplicated)")
    print(f"{'='*74}")

    # Load and dedup days
    ex = load_ex2("exercises.json")
    bf = load_ex2("backfill2.json")
    by_date = {}
    for d in bf: by_date[d["date"]] = d
    for d in ex: by_date[d["date"]] = d
    all_days = sorted(by_date.values(), key=lambda e: e["date"])

    tot_base = tot_a = tot_b = tot_c = 0.0
    for day in all_days:
        base = day["total_pnl"]
        tot_base += base
        stop_tickers, take_tickers = morning_context(day)
        pa = pb = pc = base
        for t in day["trades"]:
            if t.get("signal") != "PM_ORB":
                continue
            ticker = t["ticker"]
            pnl    = t["pnl"]
            if ticker in stop_tickers:
                pa -= pnl
                pc -= pnl
            elif ticker in take_tickers and t.get("rating") == "MAYBE":
                new_pnl = upgrade_alloc(t, day)
                pb += new_pnl - pnl
                pc += new_pnl - pnl
        tot_a += pa
        tot_b += pb
        tot_c += pc

    da = tot_a - tot_base
    db = tot_b - tot_base
    dc = tot_c - tot_base
    print(f"\n  Baseline:   ${tot_base:+.2f}")
    print(f"  Block SL:   ${tot_a:+.2f}  ({'+' if da>=0 else ''}{da:.2f})")
    print(f"  Upgrade TP: ${tot_b:+.2f}  ({'+' if db>=0 else ''}{db:.2f})")
    print(f"  Combined:   ${tot_c:+.2f}  ({'+' if dc>=0 else ''}{dc:.2f})")

    # Per-trade detail
    print(f"\n  All affected PM_ORB trades (unique, combined):")
    print(f"  {'Date':<12} {'Ticker':<6} {'Time':>5} {'Rtg':>5}  {'Morning':>12}  "
          f"{'Action':>7}  {'Orig PnL':>9}  {'New PnL':>9}  {'Delta':>8}")
    print("  " + "-" * 82)

    block_pnl = upg_delta = 0.0
    for date, ticker, time, rating, morn, pnl, action, new_pnl in sorted(uniq, key=lambda x: x[0]):
        if action == "BLOCK":
            delta = -pnl
            new_str = "BLOCKED"
            block_pnl += delta
        else:
            delta = new_pnl - pnl
            new_str = f"${new_pnl:+.2f}"
            upg_delta += delta
        sign = "+" if pnl >= 0 else ""
        print(f"  {date:<12} {ticker:<6} {time:>5} {rating:>5}  {morn:>12}  "
              f"{action:>7}  {sign}${abs(pnl):.2f}     {new_str:>9}  "
              f"{'+' if delta>=0 else ''}{delta:.2f}")

    print(f"\n  Block SL net:   {'+' if block_pnl>=0 else ''}{block_pnl:.2f}")
    print(f"  Upgrade TP net: {'+' if upg_delta>=0 else ''}{upg_delta:.2f}")
    print(f"  Combined net:   {'+' if (block_pnl+upg_delta)>=0 else ''}{block_pnl+upg_delta:.2f}")

    if dc > 5:
        print(f"\n  → Combined is positive (+${dc:.2f}) — consider shipping.")
    elif dc < -5:
        print(f"\n  → Not Pursuing: combined costs ${abs(dc):.2f}.")
    else:
        print(f"\n  → Minimal net effect ({'+' if dc>=0 else ''}{dc:.2f}) — not enough signal.")


if __name__ == "__main__":
    ex_det, ex_b, ex_a, ex_up, ex_c = run_dataset("exercises.json",  "exercises — 17 live days")
    bf_det, bf_b, bf_a, bf_up, bf_c = run_dataset("backfill2.json",  "backfill2 — 28 days")
    combined_summary(ex_det, bf_det, (ex_b, ex_a, ex_up, ex_c), (bf_b, bf_a, bf_up, bf_c))
