"""
test_item1_item2.py — Two targeted growth op tests, 2026-05-03.

Test 1: GAP_GO High-Vol Tiered Exit
  For GAP_GO trades with vol_ratio >= 10x that exit TAKE_PROFIT at +3%,
  compare exit pnl vs EOD pnl (upper bound on what a trail from +4% would capture).
  EOD is a directional proxy — actual trailing stop captures less on reversal days.

Test 2: TSLA Per-Ticker Analysis
  TSLA stats vs pool average. Simulate three variants:
  A) Skip TSLA entirely
  B) TSLA vol floor >= 2.0x (skip entries below threshold)
  C) TSLA vol floor >= 2.5x

Run: venv/bin/python3 test_item1_item2.py
"""

import json, os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_ex1(filename):
    path = os.path.join(BASE_DIR, filename)
    with open(path) as f:
        data = json.load(f)
    return sorted([e for e in data if "Exercise 1" in e["title"]], key=lambda e: e["date"])


# ─── TEST 1: GAP_GO High-Vol Tiered Exit ──────────────────────────────────────

def test1_gap_go_highvol_tiered(days, label):
    VOL_THRESHOLD = 10.0

    print(f"\n{'='*72}")
    print(f"TEST 1: GAP_GO High-Vol Tiered Exit (vol >= {VOL_THRESHOLD:.0f}x)  [{label}]")
    print(f"{'='*72}")
    print(f"Rule: for GAP_GO trades with vol_ratio >= {VOL_THRESHOLD:.0f}x that hit TAKE_PROFIT,")
    print(f"trail from +4% instead of capping at +3%. EOD = upper-bound proxy for trail capture.")
    print(f"Negative 'left' means EOD reversed below the +3% exit (rule would have been worse).\n")

    hdr = (f"{'Date':<12} {'Ticker':<6} {'Vol':>5}x  "
           f"{'EntryP':>7} {'ExitP':>7} {'EOD':>7}  "
           f"{'ExitPnL':>9} {'EODPnL':>9} {'Left':>9}  {'Reason':<16}")
    print(hdr)
    print("-" * len(hdr))

    total_left = 0.0
    tp_rows    = []
    non_tp     = []

    for day in days:
        for t in day["trades"]:
            if t.get("signal") != "GAP_GO":
                continue
            if t.get("vol_ratio", 0) < VOL_THRESHOLD:
                continue

            alloc   = t["allocated"]
            entry_p = t["entry"]
            exit_p  = t["exit"]
            eod_p   = t.get("eod", exit_p)
            eod_pnl = round((eod_p - entry_p) / entry_p * alloc, 2)
            left    = round(eod_pnl - t["pnl"], 2)
            reason  = t["exit_reason"]

            if reason == "TAKE_PROFIT":
                total_left += left
                tp_rows.append((day["date"], t, alloc, entry_p, exit_p, eod_p, eod_pnl, left))
                se = "+" if t["pnl"] >= 0 else ""
                sd = "+" if eod_pnl  >= 0 else ""
                sl = "+" if left     >= 0 else ""
                print(f"{day['date']:<12} {t['ticker']:<6} {t['vol_ratio']:>5.1f}x  "
                      f"{entry_p:>7.2f} {exit_p:>7.2f} {eod_p:>7.2f}  "
                      f"{se}${abs(t['pnl']):>7.2f} {sd}${abs(eod_pnl):>7.2f} {sl}${abs(left):>7.2f}  "
                      f"{reason:<16}")
            else:
                non_tp.append((day["date"], t))

    print()
    if not tp_rows:
        print("  No GAP_GO vol>=10x trades hit TAKE_PROFIT in this dataset.")
    else:
        sl = "+" if total_left >= 0 else ""
        print(f"  {len(tp_rows)} TAKE_PROFIT exit(s) affected")
        print(f"  Total left on table vs EOD: {sl}${total_left:.2f}")
        print(f"  Caveat: EOD is an upper bound. Trailing stop from +4% with 2% trail")
        print(f"  captures less than EOD on days the stock reverses after the +3% mark.")

    if non_tp:
        print(f"\n  GAP_GO vol>=10x exits NOT at TAKE_PROFIT (unaffected by this rule):")
        for date, t in non_tp:
            s = "+" if t["pnl"] >= 0 else ""
            print(f"    {date:<12} {t['ticker']:<6} {t['vol_ratio']:.1f}x  "
                  f"{t['exit_reason']:<16} {s}${abs(t['pnl']):.2f}")


# ─── TEST 2: TSLA Per-Ticker Analysis ─────────────────────────────────────────

def test2_tsla_analysis(days, label):
    print(f"\n{'='*72}")
    print(f"TEST 2: TSLA Per-Ticker Analysis  [{label}]")
    print(f"{'='*72}")

    all_trades   = [t for day in days for t in day["trades"]]
    tsla_trades  = [t for t in all_trades if t["ticker"] == "TSLA"]
    other_trades = [t for t in all_trades if t["ticker"] != "TSLA"]
    baseline     = sum(day["total_pnl"] for day in days)

    def stats_line(trades, lbl):
        n = len(trades)
        if n == 0:
            print(f"  {lbl:<22} n=  0")
            return
        wins   = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total  = sum(t["pnl"] for t in trades)
        avg_w  = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
        avg_l  = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        print(f"  {lbl:<22} n={n:>3}  win={len(wins)/n*100:>5.1f}%  "
              f"total={total:>+9.2f}  avg/trade={total/n:>+7.2f}  "
              f"avg_win={avg_w:>+7.2f}  avg_loss={avg_l:>+7.2f}")

    print()
    stats_line(tsla_trades,  "TSLA")
    stats_line(other_trades, "All other tickers")
    stats_line(all_trades,   "Full pool")

    # Exit reason breakdown
    print(f"\n  TSLA exit breakdown:")
    reasons = {}
    for t in tsla_trades:
        reasons.setdefault(t["exit_reason"], []).append(t["pnl"])
    for r, pnls in sorted(reasons.items()):
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        print(f"    {r:<18} n={n:>2}  wins={wins:>2}  total={sum(pnls):>+8.2f}  avg={sum(pnls)/n:>+7.2f}")

    # Trade detail
    print(f"\n  TSLA trade detail:")
    hdr2 = f"  {'Date':<12} {'Sig':<8} {'Rtg':<6} {'Vol':>5}x  {'Entry':>5} {'Exit':>5}  {'Reason':<18} {'PnL':>9}"
    print(hdr2)
    print("  " + "-" * (len(hdr2) - 2))
    for day in days:
        for t in day["trades"]:
            if t["ticker"] != "TSLA":
                continue
            s = "+" if t["pnl"] >= 0 else ""
            print(f"  {day['date']:<12} {t.get('signal','ORB'):<8} {t.get('rating','?'):<6} "
                  f"{t.get('vol_ratio', 0):>5.1f}x  {t['time']:>5} {t['exit_time']:>5}  "
                  f"{t['exit_reason']:<18} {s}${abs(t['pnl']):>7.2f}")

    # Simulate variants
    print(f"\n  Baseline total P&L [{label}]: ${baseline:+.2f}")
    print(f"  Variant simulations (P&L delta only — budget reallocation not modeled):\n")

    # Var A: Skip TSLA entirely
    delta_a = -sum(t["pnl"] for t in tsla_trades)
    n_wins  = sum(1 for t in tsla_trades if t["pnl"] > 0)
    n_loss  = sum(1 for t in tsla_trades if t["pnl"] <= 0)
    print(f"  Var A — Skip TSLA entirely:")
    print(f"    Removes {len(tsla_trades)} trades ({n_wins}W / {n_loss}L)")
    print(f"    Delta: {'+' if delta_a >= 0 else ''}${delta_a:.2f}  →  New total: ${baseline + delta_a:+.2f}")

    # Var B: vol floor 2.0x
    for floor, var_label in [(2.0, "B"), (2.5, "C")]:
        skipped = [(day["date"], t) for day in days for t in day["trades"]
                   if t["ticker"] == "TSLA" and t.get("vol_ratio", 0) < floor]
        delta = -sum(t["pnl"] for _, t in skipped)
        n_w   = sum(1 for _, t in skipped if t["pnl"] > 0)
        n_l   = sum(1 for _, t in skipped if t["pnl"] <= 0)
        print(f"\n  Var {var_label} — TSLA vol floor >= {floor}x (skip {len(skipped)} entries, {n_w}W / {n_l}L):")
        for date, t in skipped:
            s = "+" if t["pnl"] >= 0 else ""
            print(f"    Skipped: {date}  vol={t.get('vol_ratio',0):.1f}x  "
                  f"{t['exit_reason']:<18} {s}${abs(t['pnl']):.2f}")
        print(f"    Delta: {'+' if delta >= 0 else ''}${delta:.2f}  →  New total: ${baseline + delta:+.2f}")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    exercises = load_ex1("exercises.json")
    backfill  = load_ex1("backfill.json")

    print("\n" + "="*72)
    print("ITEM 1 & 2 TEST SUITE — 2026-05-03")
    print(f"exercises.json: {len(exercises)} days  |  backfill.json: {len(backfill)} days")
    print("="*72)

    test1_gap_go_highvol_tiered(exercises, "exercises.json 15d")
    test1_gap_go_highvol_tiered(backfill,  "backfill.json 38d")

    test2_tsla_analysis(exercises, "exercises.json 15d")
    test2_tsla_analysis(backfill,  "backfill.json 38d")


if __name__ == "__main__":
    main()
