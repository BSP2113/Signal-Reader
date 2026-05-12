"""
test_growth_ops.py — 5 growth opportunity tests, 2026-05-03.

Run: venv/bin/python3 test_growth_ops.py

1. GAP_GO T+30/T+60 early no-progress (vs standard T+90)
2. Pre-10:00 TAKE signal analysis vs post-10:00 (ORB only, stats)
3. KOPN tiered exit — left-on-table analysis vs EOD
4. GAP_GO budget priority over ORB (sort + skip variants)
5. Early GAP_GO reversal gate (TS before 09:45 → 2.5x vol floor)

Tests 1,2,4,5 run on exercises.json and backfill.json separately.
Test 3 pools all available data.
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY_LOSS_LIMIT = -75.0


def _to_mins(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def load_ex1(filename):
    path = os.path.join(BASE_DIR, filename)
    with open(path) as f:
        data = json.load(f)
    return sorted([e for e in data if "Exercise 1" in e["title"]], key=lambda e: e["date"])


# ─── TEST 1: GAP_GO Early No-Progress ────────────────────────────────────────

def test1_gap_go_early_exit(days, label):
    """
    Hypothesis: GAP_GO momentum fails fast — apply T+30 or T+60 no-progress
    check instead of the standard T+90.

    Only NO_PROGRESS exits are definitively improved by this rule; SL/TS/TP
    exits already fired before the no-progress check would trigger.

    Approximation: a NO_PROGRESS at T+90 implies price was <= entry at T+90.
    We assume price was also <= entry at T+30 and T+60 (conservative), so the
    earlier exit also happens at roughly entry price (pnl ~ 0).
    """
    print(f"\n{'='*72}")
    print(f"TEST 1: GAP_GO Early No-Progress (T+30 / T+60)  [{label}]")
    print(f"{'='*72}")
    print("Definitive cases only: NO_PROGRESS exits — these are unambiguously")
    print("improved by an earlier check. Non-NO_PROGRESS exits are unaffected.\n")

    t30_gain = t60_gain = 0.0
    t30_n = t60_n = 0
    rows = []

    for day in days:
        for t in day["trades"]:
            if t.get("signal") != "GAP_GO":
                continue
            entry_m  = _to_mins(t["time"])
            exit_m   = _to_mins(t["exit_time"])
            dur      = exit_m - entry_m
            reason   = t["exit_reason"]
            pnl      = t["pnl"]
            t30_imp  = t60_imp = None

            if reason == "NO_PROGRESS":
                if dur >= 30:
                    t30_imp   = -pnl          # improvement: current pnl → 0
                    t30_gain += t30_imp
                    t30_n    += 1
                if dur >= 60:
                    t60_imp   = -pnl
                    t60_gain += t60_imp
                    t60_n    += 1

            rows.append((day["date"], t["ticker"], t["time"], t["exit_time"],
                         reason, dur, pnl, t30_imp, t60_imp))

    hdr = f"{'Date':<12} {'Ticker':<6} {'Entry':>5} {'Exit':>5} {'Dur':>5}m  {'Reason':<14} {'PnL':>9}  {'T+30Δ':>8}  {'T+60Δ':>8}"
    print(hdr)
    print("-" * len(hdr))

    for date, ticker, entry, exit_, reason, dur, pnl, t30, t60 in rows:
        s    = "+" if pnl >= 0 else ""
        t30s = f"+${t30:.2f}" if t30 is not None else "—"
        t60s = f"+${t60:.2f}" if t60 is not None else "—"
        flag = "  ***" if t30 is not None else ""
        print(f"{date:<12} {ticker:<6} {entry:>5} {exit_:>5} {dur:>5}m  {reason:<14} {s}${abs(pnl):>7.2f}  {t30s:>8}  {t60s:>8}{flag}")

    print()
    if t30_n == 0:
        print("  No GAP_GO NO_PROGRESS exits in this dataset — rule has no effect.")
    else:
        print(f"  T+30: +${t30_gain:.2f} improvement across {t30_n} trade(s)")
        print(f"  T+60: +${t60_gain:.2f} improvement across {t60_n} trade(s)")
    print(f"  Total GAP_GO trades scanned: {len(rows)}")


# ─── TEST 2: Pre-10:00 TAKE Analysis ─────────────────────────────────────────

def test2_pre10_take_analysis(days, label):
    """
    Split ORB trades by entry time (<10:00 vs >=10:00) and rating (TAKE vs MAYBE).
    Report win rate, TP rate, SL/TS rate, avg pnl, total pnl.
    No rule change — pure stats.
    """
    print(f"\n{'='*72}")
    print(f"TEST 2: Pre-10:00 vs Post-10:00 ORB Signal Analysis  [{label}]")
    print(f"{'='*72}")

    buckets = {
        "ORB <10:00  TAKE":  [],
        "ORB <10:00  MAYBE": [],
        "ORB >=10:00 TAKE":  [],
        "ORB >=10:00 MAYBE": [],
    }
    detail_early_takes = []

    for day in days:
        for t in day["trades"]:
            if t.get("signal") != "ORB":
                continue
            rating = t.get("rating", "MAYBE")
            slot   = "<10:00 " if t["time"] < "10:00" else ">=10:00"
            key    = f"ORB {slot} {rating}"
            if key in buckets:
                buckets[key].append(t)
            if rating == "TAKE" and t["time"] < "10:00":
                detail_early_takes.append((day["date"], t["ticker"], t["time"],
                                           t["exit_time"], t["exit_reason"],
                                           t.get("vol_ratio", 0), t["pnl"]))

    print(f"\n{'Group':<22} {'N':>5} {'WinRate':>8} {'TPRate':>8} {'SL+TSRate':>10} {'AvgPnL':>9} {'TotalPnL':>10}")
    print("-" * 74)
    for key in sorted(buckets.keys()):
        trd = buckets[key]
        n   = len(trd)
        if n == 0:
            print(f"{key:<22} {'0':>5}")
            continue
        wins  = sum(1 for t in trd if t["pnl"] > 0)
        tps   = sum(1 for t in trd if t["exit_reason"] == "TAKE_PROFIT")
        slts  = sum(1 for t in trd if t["exit_reason"] in ("STOP_LOSS", "TRAILING_STOP"))
        total = round(sum(t["pnl"] for t in trd), 2)
        avg   = total / n
        print(f"{key:<22} {n:>5} {wins/n*100:>7.1f}% {tps/n*100:>7.1f}% {slts/n*100:>9.1f}% {avg:>+9.2f} {total:>+10.2f}")

    if detail_early_takes:
        print(f"\n  Pre-10:00 TAKE detail:")
        print(f"  {'Date':<12} {'Ticker':<6} {'Entry':>5} {'Exit':>5} {'Reason':<14} {'Vol':>5}x  {'PnL':>9}")
        print("  " + "-" * 62)
        for date, ticker, entry, exit_, reason, vol, pnl in sorted(detail_early_takes):
            s = "+" if pnl >= 0 else ""
            print(f"  {date:<12} {ticker:<6} {entry:>5} {exit_:>5} {reason:<14} {vol:>5.1f}x  {s}${abs(pnl):.2f}")
        wins_e = sum(1 for _, _, _, _, _, _, p in detail_early_takes if p > 0)
        total_e = sum(p for _, _, _, _, _, _, p in detail_early_takes)
        n_e     = len(detail_early_takes)
        print(f"\n  Pre-10:00 TAKE summary: {wins_e}/{n_e} wins ({wins_e/n_e*100:.0f}%)  total ${total_e:+.2f}")
    else:
        print("\n  No pre-10:00 ORB TAKE trades found in this dataset.")


# ─── TEST 3: KOPN Tiered Exit ─────────────────────────────────────────────────

def test3_kopn_tiered_exit(days, label):
    """
    For KOPN TAKE_PROFIT exits, compare the +3% take-profit pnl against what
    would have been captured at EOD (if the position were held open).

    EOD pnl is a lower bound on what a trailing stop from +4% might capture —
    the actual trailing stop may differ depending on the intraday peak.
    A negative 'left on table' means EOD was below the +3% exit (rare).
    """
    print(f"\n{'='*72}")
    print(f"TEST 3: KOPN Tiered Exit — Left-on-Table Analysis  [{label}]")
    print(f"{'='*72}")
    print("EOD pnl = hypothetical gain if KOPN TAKE_PROFIT position held to close.")
    print("Positive 'left' = gain foregone. Negative = exit was better than EOD.\n")

    hdr = (f"{'Date':<12} {'Sig':>6} {'Entry':>5} {'Exit':>5} "
           f"{'Alloc':>8} {'EntryP':>8} {'ExitP':>8} {'EOD':>8} "
           f"{'ExitPnL':>9} {'EODPnL':>9} {'Left':>9}")
    print(hdr)
    print("-" * len(hdr))

    total_left   = 0.0
    kopn_tp_rows = 0

    for day in days:
        for t in day["trades"]:
            if t["ticker"] != "KOPN" or t["exit_reason"] != "TAKE_PROFIT":
                continue
            alloc   = t["allocated"]
            entry_p = t["entry"]
            exit_p  = t["exit"]
            eod_p   = t.get("eod", exit_p)
            eod_pnl = round((eod_p - entry_p) / entry_p * alloc, 2)
            left    = round(eod_pnl - t["pnl"], 2)
            total_left   += left
            kopn_tp_rows += 1

            sig  = t.get("signal", "ORB")
            se   = "+" if t["pnl"] >= 0 else ""
            sd   = "+" if eod_pnl  >= 0 else ""
            sl   = "+" if left     >= 0 else ""
            print(f"{day['date']:<12} {sig:>6} {t['time']:>5} {t['exit_time']:>5} "
                  f"${alloc:>7.2f} {entry_p:>8.2f} {exit_p:>8.2f} {eod_p:>8.2f} "
                  f"{se}${abs(t['pnl']):>7.2f} {sd}${abs(eod_pnl):>7.2f} {sl}${abs(left):>7.2f}")

    print()
    if kopn_tp_rows == 0:
        print("  No KOPN TAKE_PROFIT exits in this dataset.")
    else:
        sl = "+" if total_left >= 0 else ""
        print(f"  {kopn_tp_rows} KOPN TAKE_PROFIT exit(s) total")
        print(f"  Total left on table vs EOD: {sl}${total_left:.2f}")
        print(f"  Caveat: trailing stop from +4% would capture less than EOD on")
        print(f"  reversing days. Use EOD as a directional upper-bound only.")


# ─── TEST 4: GAP_GO Budget Priority ──────────────────────────────────────────

def test4_gap_go_budget_priority(days, label):
    """
    Two variants:
    A) priority_sort: sort GAP_GO before ORB within the same entry minute.
       Expected minimal change — GAP_GO fires 09:31-09:38, ORB fires 09:45+.
    B) skip_orb_on_gap_day: on any day where >= 1 GAP_GO fires, skip all ORB
       entries entirely. Tests whether concentrating capital on gap trades helps.

    Uses stored trade pnl values — cannot add budget-blocked trades back in.
    """
    print(f"\n{'='*72}")
    print(f"TEST 4: GAP_GO Budget Priority  [{label}]")
    print(f"{'='*72}")

    total_base = total_sort = total_skip = 0.0
    changed_sort = changed_skip = 0
    gap_days = 0

    print(f"\n{'Date':<12} {'Baseline':>10} {'PrioritySort':>14} {'SkipORB':>10}  Gap?")
    print("-" * 58)

    for day in days:
        trades   = day["trades"]
        baseline = day["total_pnl"]
        total_base += baseline

        has_gap = any(t.get("signal") == "GAP_GO" for t in trades)

        # Variant A: sort GAP_GO before ORB within same minute
        sorted_a = sorted(trades, key=lambda t: (t["time"], 0 if t.get("signal") == "GAP_GO" else 1))
        start    = day.get("starting_capital", 5000.0)
        active_a = []
        acc_a    = []
        dl_hit   = False
        for trade in sorted_a:
            if dl_hit:
                continue
            active_a = [a for a in active_a if a["exit_time"] > trade["time"]]
            if start - sum(a["allocated"] for a in active_a) >= trade["allocated"]:
                active_a.append({"exit_time": trade["exit_time"], "allocated": trade["allocated"]})
                acc_a.append(trade)
                if round(sum(t["pnl"] for t in acc_a), 2) <= DAY_LOSS_LIMIT:
                    dl_hit = True
        pnl_a = round(sum(t["pnl"] for t in acc_a), 2)
        total_sort += pnl_a

        # Variant B: skip ORB on gap days
        if has_gap:
            gap_days += 1
            pnl_b = round(sum(t["pnl"] for t in trades if t.get("signal") == "GAP_GO"), 2)
        else:
            pnl_b = baseline
        total_skip += pnl_b

        diff_a = pnl_a - baseline
        diff_b = pnl_b - baseline
        if abs(diff_a) > 0.01:
            changed_sort += 1
        if abs(diff_b) > 0.01:
            changed_skip += 1

        sa = "+" if baseline >= 0 else ""
        sb = f"{'+' if diff_a >= 0 else ''}${diff_a:.2f}" if abs(diff_a) > 0.01 else "—"
        sc = f"{'+' if diff_b >= 0 else ''}${diff_b:.2f}" if abs(diff_b) > 0.01 else "—"
        gap_flag = " (gap day)" if has_gap else ""
        print(f"{day['date']:<12} {sa}${abs(baseline):>8.2f} {sb:>14} {sc:>10}{gap_flag}")

    print("-" * 58)
    sb = "+" if total_base >= 0 else ""
    ss = "+" if total_sort >= 0 else ""
    sk = "+" if total_skip >= 0 else ""
    print(f"{'TOTAL':<12} {sb}${abs(total_base):>8.2f} {ss}${abs(total_sort):>8.2f} {sk}${abs(total_skip):>8.2f}")

    diff_sort = total_sort - total_base
    diff_skip = total_skip - total_base
    print(f"\n  PrioritySort: {'+' if diff_sort >= 0 else ''}${diff_sort:.2f}  ({changed_sort} days changed)")
    print(f"  SkipORBonGap: {'+' if diff_skip >= 0 else ''}${diff_skip:.2f}  ({changed_skip} days changed, {gap_days} gap days total)")
    if changed_sort == 0:
        print(f"  → PrioritySort no-ops: GAP_GO fires 09:31-09:38, ORB fires 09:45+.")
        print(f"    Temporal separation means budget priority is already natural.")


# ─── TEST 5: Early GAP_GO Reversal Gate ───────────────────────────────────────

def test5_gap_go_reversal_gate(days, label):
    """
    Two variants of the early GAP_GO reversal gate:

    Variant A (any_ts_gate): If ANY GAP_GO exits via TS before 09:45, raise
    vol floor to >= 2.5x for the rest of the session. Original proposal.

    Variant B (all_losers_gate): Only gate if ALL completed GAP_GO exits by
    09:45 are losses (SL or TS). A winning GAP_GO before the gate clears it,
    meaning the session has real momentum — don't block ORB entries.
    Rationale: Apr 24 had KOPN TS at 09:44 but ARM TP at 09:34 was already a
    winner; the gate should not have fired. Apr 27 had only one GAP_GO (KOPN
    TS at 09:38) — correct to gate there.
    """
    print(f"\n{'='*72}")
    print(f"TEST 5: Early GAP_GO Reversal Gate  [{label}]")
    print(f"{'='*72}")
    print("Variant A (any_ts): gate fires on first GAP_GO TS before 09:45.")
    print("Variant B (all_losers): gate only fires if ALL GAP_GO completed by 09:45 are losses.\n")

    totals = {"base": 0.0, "varA": 0.0, "varB": 0.0}
    trigger_a = trigger_b = 0
    blocked_a = blocked_b = 0

    print(f"{'Date':<12} {'Baseline':>10} {'VarA':>10} {'VarAΔ':>8}  {'VarB':>10} {'VarBΔ':>8}")
    print("-" * 64)

    for day in days:
        trades   = sorted(day["trades"], key=lambda t: t["time"])
        baseline = day["total_pnl"]
        totals["base"] += baseline

        def _simulate_gate(gate_time):
            if gate_time is None:
                return baseline, 0
            acc = []
            blk = 0
            for t in trades:
                if t["time"] <= gate_time:
                    acc.append(t)
                elif t.get("vol_ratio", 0) >= 2.5:
                    acc.append(t)
                else:
                    blk += 1
            return round(sum(t["pnl"] for t in acc), 2), blk

        # Variant A: first GAP_GO TS before 09:45
        gate_a = None
        for t in trades:
            if (t.get("signal") == "GAP_GO"
                    and t["exit_reason"] == "TRAILING_STOP"
                    and t["exit_time"] < "09:45"):
                gate_a = t["exit_time"]
                break

        # Variant B: all completed GAP_GO exits by 09:45 are losses
        gap_exits_by_45 = [
            t for t in trades
            if t.get("signal") == "GAP_GO" and t["exit_time"] <= "09:45"
        ]
        gate_b = None
        if gap_exits_by_45:
            all_losers = all(
                t["exit_reason"] in ("STOP_LOSS", "TRAILING_STOP") and t["pnl"] <= 0
                for t in gap_exits_by_45
            )
            last_exit = max(t["exit_time"] for t in gap_exits_by_45)
            if all_losers:
                gate_b = last_exit

        pnl_a, blk_a = _simulate_gate(gate_a)
        pnl_b, blk_b = _simulate_gate(gate_b)
        totals["varA"] += pnl_a
        totals["varB"] += pnl_b
        blocked_a += blk_a
        blocked_b += blk_b
        if gate_a:
            trigger_a += 1
        if gate_b:
            trigger_b += 1

        diff_a = pnl_a - baseline
        diff_b = pnl_b - baseline
        sa = "+" if baseline >= 0 else ""
        fmt_a = f"{'+' if diff_a >= 0 else ''}${abs(diff_a):.2f}" if gate_a else "—"
        fmt_b = f"{'+' if diff_b >= 0 else ''}${abs(diff_b):.2f}" if gate_b else "—"
        pnl_a_s = f"{'+' if pnl_a >= 0 else ''}${abs(pnl_a):.2f}" if gate_a else "—"
        pnl_b_s = f"{'+' if pnl_b >= 0 else ''}${abs(pnl_b):.2f}" if gate_b else "—"
        gate_note = ""
        if gate_a and not gate_b:
            gate_note = "  ← A fires, B suppressed (gap winner existed)"
        elif gate_b and not gate_a:
            gate_note = "  ← B fires only"
        elif gate_a and gate_b:
            gate_note = "  ← both fire"
        print(f"{day['date']:<12} {sa}${abs(baseline):>8.2f} {pnl_a_s:>10} {fmt_a:>8}  {pnl_b_s:>10} {fmt_b:>8}{gate_note}")

    print("-" * 64)
    sb = "+" if totals["base"] >= 0 else ""
    sa = "+" if totals["varA"] >= 0 else ""
    sc = "+" if totals["varB"] >= 0 else ""
    da = totals["varA"] - totals["base"]
    db = totals["varB"] - totals["base"]
    print(f"{'TOTAL':<12} {sb}${abs(totals['base']):>8.2f} {sa}${abs(totals['varA']):>8.2f} {'+' if da >= 0 else ''}${abs(da):>6.2f}  {sc}${abs(totals['varB']):>8.2f} {'+' if db >= 0 else ''}${abs(db):>6.2f}")
    print(f"\n  Var A: {'+' if da >= 0 else ''}${da:.2f}  ({trigger_a} days triggered, {blocked_a} trades blocked)")
    print(f"  Var B: {'+' if db >= 0 else ''}${db:.2f}  ({trigger_b} days triggered, {blocked_b} trades blocked)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    exercises = load_ex1("exercises.json")
    backfill  = load_ex1("backfill.json")

    # Combine for test 3 — deduplicate by date, exercises.json wins for shared dates
    by_date = {}
    for e in backfill:
        by_date[e["date"]] = e
    for e in exercises:
        by_date[e["date"]] = e
    all_days = sorted(by_date.values(), key=lambda e: e["date"])

    print("\n" + "="*72)
    print("GROWTH OPS TEST SUITE — 2026-05-03")
    print(f"exercises.json: {len(exercises)} days  |  backfill.json: {len(backfill)} days")
    print("="*72)

    # Tests 1, 2, 4, 5: run on each dataset separately
    for test_fn in [test1_gap_go_early_exit, test2_pre10_take_analysis,
                    test4_gap_go_budget_priority, test5_gap_go_reversal_gate]:
        test_fn(exercises, "exercises.json 15d")
        test_fn(backfill,  "backfill.json 38d")

    # Test 3: pool all data (KOPN is sparse, need max sample size)
    test3_kopn_tiered_exit(all_days, "all data combined")


if __name__ == "__main__":
    main()
