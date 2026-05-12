"""
test_maybe_gap_filter.py — Test tightening MAYBE entries by gap_pct zone.

Discovery (2026-05-11): MAYBE entries with gap_pct in [+0.50%, +1.00%) have
a 0% win rate across 11 trades, -$87.42 total. Adjacent gap buckets are
healthy. This is the cleanest dead zone in the MAYBE data.

This is an entry-level filter (uses only data known at entry time), so it
does NOT fall under the structural-failure pattern that has killed every
session-state rule tested to date.

Variants:
  A — WeakGapBlock        : skip MAYBE if 0.50% <= gap_pct < 1.00%
  B — WeakGapBlockWide    : skip MAYBE if 0.40% <= gap_pct < 1.10%
  C — WeakGapBlockVolGate : variant A, but allow through if vol_ratio >= 2.5x
  D — TakeOnly            : skip all MAYBE (nuclear comparison)

Restricted to ORB + GAP_GO signals only (matches what live EX1 actually trades —
PM_ORB is EX2-only and excluded).

Reports baseline vs each variant on backfill.json (38 days) and the EX1 slice
of exercises.json (21 days), with skipped-trade breakdown (wins vs losers).

Note: this test drops skipped trades from the day total; it does not re-simulate
freed-capital effects. In EX1 (no re-entries), idle capital is the realistic
outcome of a blocked entry, so this gives a clean first-order estimate.

Usage: venv/bin/python3 tests/test_maybe_gap_filter.py
"""

import json, os, sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_ex1_trades(path, title_filter=None):
    """Return flat list of {date, ...trade fields} for EX1 ORB/GAP_GO trades."""
    with open(path) as f:
        data = json.load(f)
    if title_filter:
        data = [e for e in data if e.get('title') == title_filter]
    out = []
    for e in data:
        for t in e.get('trades', []):
            if t.get('signal') in ('ORB', 'GAP_GO'):
                out.append({**t, 'date': e['date']})
    return out


def should_skip(trade, variant):
    """Return True if `trade` would be skipped under `variant`."""
    if trade.get('rating') != 'MAYBE':
        return False
    gap = trade.get('gap_pct')
    vol = trade.get('vol_ratio')

    if variant == 'A':   # WeakGapBlock
        return gap is not None and 0.50 <= gap < 1.00
    if variant == 'B':   # WeakGapBlockWide
        return gap is not None and 0.40 <= gap < 1.10
    if variant == 'C':   # WeakGapBlockVolGate
        if gap is None or not (0.50 <= gap < 1.00):
            return False
        return vol is None or vol < 2.5
    if variant == 'D':   # TakeOnly
        return True
    raise ValueError(f"unknown variant {variant}")


def evaluate(trades, variant):
    """Run variant against trade list. Return summary dict."""
    skipped, kept = [], []
    for t in trades:
        (skipped if should_skip(t, variant) else kept).append(t)

    baseline = sum(t['pnl'] for t in trades)
    after    = sum(t['pnl'] for t in kept)
    n_skipped = len(skipped)
    skipped_wins   = sum(1 for t in skipped if t['pnl'] > 0)
    skipped_losses = sum(1 for t in skipped if t['pnl'] < 0)
    skipped_pnl    = sum(t['pnl'] for t in skipped)
    return {
        'baseline': baseline,
        'after': after,
        'delta': after - baseline,
        'n_skipped': n_skipped,
        'skipped_wins': skipped_wins,
        'skipped_losses': skipped_losses,
        'skipped_pnl': skipped_pnl,
        'skipped_trades': skipped,
    }


def report(name, trades):
    print(f"\n{'='*72}")
    print(f" {name}   ({len(trades)} MAYBE+TAKE ORB/GAP_GO trades, "
          f"{sum(1 for t in trades if t.get('rating')=='MAYBE')} MAYBE)")
    print('='*72)

    variants = [
        ('A', 'WeakGapBlock         (0.50–1.00% gap)'),
        ('B', 'WeakGapBlockWide     (0.40–1.10% gap)'),
        ('C', 'WeakGapBlockVolGate  (0.50–1.00% gap, allow if vol >= 2.5x)'),
        ('D', 'TakeOnly             (drop all MAYBE)'),
    ]

    base = sum(t['pnl'] for t in trades)
    print(f"\n  Baseline P&L: ${base:+8.2f}")

    rows = []
    for v, label in variants:
        r = evaluate(trades, v)
        rows.append((v, label, r))
        sign = '+' if r['delta'] >= 0 else ''
        print(f"\n  Variant {v} — {label}")
        print(f"    P&L after:   ${r['after']:+8.2f}   "
              f"(net {sign}${r['delta']:+.2f} vs baseline)")
        print(f"    Skipped:     {r['n_skipped']:>3} trades   "
              f"(wins: {r['skipped_wins']}  losses: {r['skipped_losses']})   "
              f"skipped P&L: ${r['skipped_pnl']:+.2f}")

    # Top-line ranking
    print(f"\n  {'-'*60}")
    print(f"  Ranked by net delta:")
    for v, label, r in sorted(rows, key=lambda x: -x[2]['delta']):
        print(f"    {v}: {r['delta']:+8.2f}   ({label.split()[0]})")
    return rows


def load_dataset(path, title_filter=None, signals=('ORB', 'GAP_GO')):
    with open(path) as f:
        data = json.load(f)
    if title_filter:
        data = [e for e in data if e.get('title') == title_filter]
    out = []
    for e in data:
        for t in e.get('trades', []):
            if t.get('signal') in signals:
                out.append({**t, 'date': e['date']})
    return out


def main():
    # EX1 datasets — ORB/GAP_GO only
    ex1_backfill  = load_dataset(os.path.join(BASE_DIR, 'backfill.json'))
    ex1_exercises = load_dataset(
        os.path.join(BASE_DIR, 'exercises.json'),
        title_filter='Exercise 1 - Multi-trade'
    )

    # EX2 datasets — ORB/GAP_GO morning entries only (re-entries and PM_ORB
    # are EX2-specific signals; the gap filter is a morning-entry filter)
    ex2_backfill  = load_dataset(os.path.join(BASE_DIR, 'backfill2.json'))
    ex2_exercises = load_dataset(
        os.path.join(BASE_DIR, 'exercises.json'),
        title_filter='Exercise 2 - Re-entry'
    )

    print("MAYBE Gap-Zone Filter Test")
    print("="*72)
    print("Variants block MAYBE entries inside a 'dead zone' gap range.")
    print("Restricted to ORB + GAP_GO morning signals.")

    # === EX1 ===
    print("\n\n##############  EX1  ##############")
    r_b1 = report('EX1 backfill.json  (Mar 02 – Apr 24)', ex1_backfill)
    r_e1 = report('EX1 exercises.json (Apr 13 – May 11)', ex1_exercises)

    # === EX2 ===
    print("\n\n##############  EX2  ##############")
    r_b2 = report('EX2 backfill2.json (Mar 02 – Apr 10)', ex2_backfill)
    r_e2 = report('EX2 exercises.json (Apr 13 – May 11)', ex2_exercises)

    # Combined ranking
    print(f"\n{'='*72}")
    print(" COMBINED — ALL FOUR DATASETS")
    print('='*72)
    print(f"  {'Variant':<8} {'EX1 BF':>10} {'EX1 EXR':>10} {'EX2 BF':>10} {'EX2 EXR':>10} {'Sum':>10}")
    for (v, _, rb1), (_, _, re1), (_, _, rb2), (_, _, re2) in zip(r_b1, r_e1, r_b2, r_e2):
        s = rb1['delta'] + re1['delta'] + rb2['delta'] + re2['delta']
        print(f"  {v:<8} {rb1['delta']:>+10.2f} {re1['delta']:>+10.2f} "
              f"{rb2['delta']:>+10.2f} {re2['delta']:>+10.2f} {s:>+10.2f}")

    # Variant A skipped detail across all four
    print(f"\n{'='*72}")
    print(" VARIANT A — trades blocked across all datasets")
    print('='*72)
    for name, ds in [
        ('EX1 backfill',  ex1_backfill),
        ('EX1 exercises', ex1_exercises),
        ('EX2 backfill',  ex2_backfill),
        ('EX2 exercises', ex2_exercises),
    ]:
        skipped = evaluate(ds, 'A')['skipped_trades']
        print(f"\n  {name}: {len(skipped)} trades blocked")
        for t in skipped:
            print(f"    {t['date']}  {t['ticker']:5s}  gap={t['gap_pct']:+.2f}%  "
                  f"vol={t['vol_ratio']:.1f}x  pnl=${t['pnl']:+6.2f}  "
                  f"exit={t['exit_reason']}")


if __name__ == '__main__':
    main()
