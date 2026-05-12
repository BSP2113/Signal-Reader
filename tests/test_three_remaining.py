"""
test_three_remaining.py — Three remaining quick tests, 2026-05-06.

TEST 1: Flat-gap re-entry block (EX2)
  Block EX2 re-entries (signal=REENTRY) when ticker's gap_pct is between
  -0.5% and +0.5%. On flat-gap days the initial stop-out revealed no
  pre-market directional commitment; re-entering the same stock amplifies
  exposure without edge. Motivated by UPST May 5 (-$18.53 combined two legs).

TEST 2: Flat-gap ORB score penalty (EX1)
  Apply -1 score penalty to ORB entries where gap_pct is between -0.5%
  and +0.5%, downgrading flat-gap TAKEs to MAYBE allocation.
  Variant A: downgrade TAKE → MAYBE (scale P&L by MAYBE/TAKE ratio).
  Variant B: block all flat-gap TAKEs entirely.

TEST 3: Late TAKE scrutiny — TAKE-rated ORBs after 10:30 (EX1)
  The Apr 13 note flagged KOPN entering at 10:36 as TAKE with a full
  large allocation and only ~3.5 hours left. Check whether post-10:30 TAKE
  entries are net positive and whether a filter or cutoff improves results.
  Variant A: block all post-10:30 TAKE entries.
  Variant B: downgrade to MAYBE allocation.
  Variant C: block only post-11:00 TAKE entries (tighter cutoff).

Run: venv/bin/python3 tests/test_three_remaining.py
"""

import json, os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOC_RATIOS = {
    "bullish": {"TAKE": 0.35, "MAYBE": 0.20},
    "neutral":  {"TAKE": 0.30, "MAYBE": 0.15},
    "bearish":  {"TAKE": 0.10, "MAYBE": 0.10},
}
FLAT_GAP_LO, FLAT_GAP_HI = -0.5, 0.5   # gap_pct stored as percent


def load_ex1(fname):
    path = os.path.join(BASE_DIR, fname)
    with open(path) as f: data = json.load(f)
    return sorted([e for e in data if "Exercise 1" in e["title"]], key=lambda e: e["date"])


def load_ex2(fname):
    path = os.path.join(BASE_DIR, fname)
    with open(path) as f: data = json.load(f)
    return sorted([e for e in data if "Exercise 2" in e["title"]], key=lambda e: e["date"])


def dedup(primary, secondary):
    by_date = {}
    for d in secondary: by_date[d["date"]] = d
    for d in primary:   by_date[d["date"]] = d
    return sorted(by_date.values(), key=lambda e: e["date"])


def downgrade_pnl(trade, day):
    """Scale a TAKE trade's P&L as if rated MAYBE."""
    mkt = day.get("market_state", "neutral")
    r   = ALLOC_RATIOS.get(mkt, ALLOC_RATIOS["neutral"])
    if r["TAKE"] == 0 or r["TAKE"] == r["MAYBE"]: return trade["pnl"]
    return round(trade["pnl"] * r["MAYBE"] / r["TAKE"], 2)


def is_flat(trade): return FLAT_GAP_LO <= trade.get("gap_pct", 0) <= FLAT_GAP_HI


# ─── TEST 1: Flat-gap re-entry block (EX2) ────────────────────────────────────

def test1(label, days):
    base = new = 0.0
    detail = []
    for day in days:
        b = day["total_pnl"]; n = b
        for t in day["trades"]:
            if t.get("signal") == "REENTRY" and is_flat(t):
                n -= t["pnl"]
                detail.append((day["date"], t))
        base += b; new += n
    return base, new, detail


def run_test1():
    print(f"\n{'='*70}")
    print("TEST 1: Flat-gap re-entry block (EX2, gap ±0.5%)")
    print(f"{'='*70}")

    ex  = load_ex2("exercises.json");  bf = load_ex2("backfill2.json")
    all_days = dedup(ex, bf)

    tb = tn = 0.0; all_detail = []
    for label, days in [("exercises (17d)", ex), ("backfill2 (28d)", bf)]:
        b, n, det = test1(label, days)
        tb += b; tn += n; all_detail += det
        net = n - b
        print(f"  {label:<22} baseline ${b:+.2f}  blocked ${n:+.2f}  net {'+' if net>=0 else ''}{net:.2f}  ({len(det)} trades)")

    net = tn - tb
    print(f"  {'Combined (45d)':<22} baseline ${tb:+.2f}  blocked ${tn:+.2f}  net {'+' if net>=0 else ''}{net:.2f}")

    # per-trade detail
    print(f"\n  All flat-gap REENTRY trades:")
    print(f"  {'Date':<12} {'Ticker':<6} {'Gap':>6}  {'Exit':<16} {'PnL':>9}  Delta")
    print("  " + "-"*58)
    seen = set()
    for date, t in sorted(all_detail, key=lambda x: x[0]):
        key = (date, t["ticker"])
        if key in seen: continue
        seen.add(key)
        delta = -t["pnl"]
        print(f"  {date:<12} {t['ticker']:<6} {t['gap_pct']:>+5.2f}%"
              f"  {t['exit_reason']:<16} {t['pnl']:>+9.2f}  {'+' if delta>=0 else ''}{delta:.2f}")

    print(f"\n  Net combined: {'+' if net>=0 else ''}{net:.2f}  ({len(seen)} unique flat-gap re-entries across 45 days)")
    return net


# ─── TEST 2: Flat-gap ORB TAKE penalty (EX1) ─────────────────────────────────

def run_test2():
    print(f"\n{'='*70}")
    print("TEST 2: Flat-gap ORB TAKE penalty (EX1, gap ±0.5%)")
    print(f"{'='*70}")
    print("Variant A: downgrade TAKE → MAYBE allocation")
    print("Variant B: block all flat-gap TAKEs entirely\n")

    col = 11
    hdr = f"{'Date':<12} {'Baseline':>{col}} {'Downgrade':>{col}} {'BlockAll':>{col}}"

    detail_rows = []

    for fname, label in [("exercises.json","exercises (17d)"), ("backfill.json","backfill (38d)")]:
        days = load_ex1(fname)
        print(f"  [{label}]")
        print(f"  {hdr}")
        print("  " + "-"*(12+col*3+2))
        tb = ta = tbl = 0.0
        for day in days:
            b = day["total_pnl"]; pa = pb = b
            for t in day["trades"]:
                if t.get("signal") != "ORB" or t["rating"] != "TAKE": continue
                if not is_flat(t): continue
                dg = downgrade_pnl(t, day)
                pa += dg - t["pnl"]
                pb -= t["pnl"]
                detail_rows.append((day["date"], t, dg, label))
            tb += b; ta += pa; tbl += pb
            da = pa-b; dbl = pb-b
            tag_a = f"({'+' if da>=0 else ''}{da:.2f})" if abs(da)>0.01 else ""
            tag_b = f"({'+' if dbl>=0 else ''}{dbl:.2f})" if abs(dbl)>0.01 else ""
            print(f"  {day['date']:<12} ${b:>+9.2f} ${pa:>+9.2f}{tag_a:<8} ${pb:>+9.2f}{tag_b}")
        print("  " + "-"*(12+col*3+2))
        da = ta-tb; dbl = tbl-tb
        print(f"  {'TOTAL':<12} ${tb:>+9.2f} ${ta:>+9.2f}({'+' if da>=0 else ''}{da:.2f}) ${tbl:>+9.2f}({'+' if dbl>=0 else ''}{dbl:.2f})\n")

    # combined
    ex = load_ex1("exercises.json"); bf = load_ex1("backfill.json")
    all_days = dedup(ex, bf)
    cb = ca = cbl = 0.0
    for day in all_days:
        b = day["total_pnl"]; pa = pb = b
        for t in day["trades"]:
            if t.get("signal") != "ORB" or t["rating"] != "TAKE": continue
            if not is_flat(t): continue
            pa += downgrade_pnl(t, day) - t["pnl"]
            pb -= t["pnl"]
        cb += b; ca += pa; cbl += pb

    da = ca-cb; dbl = cbl-cb
    print(f"  Combined (55d): baseline ${cb:+.2f}  downgrade ${ca:+.2f} ({'+' if da>=0 else ''}{da:.2f})"
          f"  block ${cbl:+.2f} ({'+' if dbl>=0 else ''}{dbl:.2f})")

    # per-trade detail (unique)
    print(f"\n  All flat-gap TAKE ORB trades:")
    print(f"  {'Date':<12} {'Ticker':<6} {'Time':>5} {'Gap':>6} {'Vol':>5}  {'Exit':<16} {'PnL':>9}  {'Downgraded':>10}")
    print("  " + "-"*72)
    seen = set()
    for date, t, dg, lbl in sorted(detail_rows, key=lambda x: x[0]):
        key = (date, t["ticker"])
        if key in seen: continue
        seen.add(key)
        print(f"  {date:<12} {t['ticker']:<6} {t['time']:>5} {t['gap_pct']:>+5.2f}% {t['vol_ratio']:>4.1f}x"
              f"  {t['exit_reason']:<16} {t['pnl']:>+9.2f}  {dg:>+10.2f}")

    return da, dbl


# ─── TEST 3: Late TAKE scrutiny after 10:30 (EX1) ────────────────────────────

def run_test3():
    print(f"\n{'='*70}")
    print("TEST 3: Late TAKE ORB entries after 10:30 (EX1)")
    print(f"{'='*70}")
    print("Variant A: block all TAKE entries after 10:30")
    print("Variant B: downgrade to MAYBE allocation")
    print("Variant C: block only TAKE entries after 11:00 (tighter cutoff)\n")

    col = 10
    hdr = f"{'Date':<12} {'Baseline':>{col}} {'BlockAll':>{col}} {'Downgrade':>{col}} {'Block11+':>{col}}"

    detail_rows = []

    for fname, label in [("exercises.json","exercises (17d)"), ("backfill.json","backfill (38d)")]:
        days = load_ex1(fname)
        print(f"  [{label}]")
        print(f"  {hdr}")
        print("  " + "-"*(12+col*4+3))
        tb = ta = tdg = tc = 0.0
        for day in days:
            b = day["total_pnl"]; pa = pdg = pc = b
            for t in day["trades"]:
                if t.get("signal") != "ORB" or t["rating"] != "TAKE": continue
                if t["time"] < "10:30": continue
                dg = downgrade_pnl(t, day)
                pa  -= t["pnl"]
                pdg += dg - t["pnl"]
                if t["time"] >= "11:00":
                    pc -= t["pnl"]
                detail_rows.append((day["date"], t, dg, label))
            tb += b; ta += pa; tdg += pdg; tc += pc
            da = pa-b; ddg = pdg-b; dc = pc-b
            def tag(d): return f"({'+' if d>=0 else ''}{d:.2f})" if abs(d)>0.01 else ""
            print(f"  {day['date']:<12} ${b:>+8.2f} ${pa:>+8.2f}{tag(da):<8} ${pdg:>+8.2f}{tag(ddg):<8} ${pc:>+8.2f}{tag(dc)}")
        print("  " + "-"*(12+col*4+3))
        da = ta-tb; ddg = tdg-tb; dc = tc-tb
        print(f"  {'TOTAL':<12} ${tb:>+8.2f} ${ta:>+8.2f}({'+' if da>=0 else ''}{da:.2f}) ${tdg:>+8.2f}({'+' if ddg>=0 else ''}{ddg:.2f}) ${tc:>+8.2f}({'+' if dc>=0 else ''}{dc:.2f})\n")

    # combined
    ex = load_ex1("exercises.json"); bf = load_ex1("backfill.json")
    all_days = dedup(ex, bf)
    cb = ca = cdg = cc = 0.0
    for day in all_days:
        b = day["total_pnl"]; pa = pdg = pc = b
        for t in day["trades"]:
            if t.get("signal") != "ORB" or t["rating"] != "TAKE": continue
            if t["time"] < "10:30": continue
            dg = downgrade_pnl(t, day)
            pa  -= t["pnl"]
            pdg += dg - t["pnl"]
            if t["time"] >= "11:00":
                pc -= t["pnl"]
        cb += b; ca += pa; cdg += pdg; cc += pc

    da = ca-cb; ddg = cdg-cb; dc = cc-cb
    print(f"  Combined (55d): baseline ${cb:+.2f}")
    print(f"    BlockAll:   ${ca:+.2f} ({'+' if da>=0 else ''}{da:.2f})")
    print(f"    Downgrade:  ${cdg:+.2f} ({'+' if ddg>=0 else ''}{ddg:.2f})")
    print(f"    Block11+:   ${cc:+.2f} ({'+' if dc>=0 else ''}{dc:.2f})")

    # per-trade detail
    print(f"\n  All TAKE ORB trades after 10:30:")
    print(f"  {'Date':<12} {'Ticker':<6} {'Time':>5} {'Vol':>5}  {'Exit':<16} {'PnL':>9}  {'Downgraded':>10}")
    print("  " + "-"*70)
    seen = set()
    wins = losses = 0
    for date, t, dg, lbl in sorted(detail_rows, key=lambda x: x[0]):
        key = (date, t["ticker"])
        if key in seen: continue
        seen.add(key)
        print(f"  {date:<12} {t['ticker']:<6} {t['time']:>5} {t['vol_ratio']:>4.1f}x"
              f"  {t['exit_reason']:<16} {t['pnl']:>+9.2f}  {dg:>+10.2f}")
        if t["pnl"] >= 0: wins += 1
        else: losses += 1
    print(f"\n  Win/Loss: {wins}W / {losses}L  ({100*wins//(wins+losses) if wins+losses else 0}% WR)")

    return da, ddg, dc


if __name__ == "__main__":
    net1 = run_test1()
    da2, dbl2 = run_test2()
    da3, ddg3, dc3 = run_test3()

    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"  Test 1 (flat-gap re-entry block EX2):  {'+' if net1>=0 else ''}{net1:.2f}")
    print(f"  Test 2 (flat-gap TAKE downgrade EX1):  {'+' if da2>=0 else ''}{da2:.2f} downgrade  /  {'+' if dbl2>=0 else ''}{dbl2:.2f} block")
    print(f"  Test 3 (late TAKE after 10:30 EX1):    {'+' if da3>=0 else ''}{da3:.2f} block  /  {'+' if ddg3>=0 else ''}{ddg3:.2f} downgrade  /  {'+' if dc3>=0 else ''}{dc3:.2f} block11+")
