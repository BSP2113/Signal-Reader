"""
gate_test.py — Tests three mid-session gate variants against exercises.json baseline.

Gate 1 (triple_stop): Block new entries for the rest of the session if 3+ STOP_LOSS
    or TRAILING_STOP exits fire within any 15-minute window.

Gate 2 (half_loss): After 10:00, block new entries if more than 50% of completed
    exits are losses at the moment a new entry is being considered.

Gate 3 (no_momentum): After 11:00, block new entries if no position entered before
    11:00 has cleared +1% (via TAKE_PROFIT, TRAILING_STOP, or pnl_pct >= 1.0).

Replays using existing exercises.json trade data — no API calls needed.
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _to_mins(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def apply_gate(day, gate):
    """
    Re-simulate a day's trades with the given gate applied.
    Returns (adjusted_pnl, accepted_count, blocked_count).
    """
    trades = sorted(day["trades"], key=lambda t: t["time"])
    accepted = []
    blocked_count = 0
    gate_triggered = False

    for trade in trades:
        if gate_triggered:
            blocked_count += 1
            continue

        # Completed exits = accepted trades whose exit has already happened
        completed = [t for t in accepted if t["exit_time"] <= trade["time"]]

        if gate == "triple_stop":
            stops = [t for t in completed
                     if t["exit_reason"] in ("STOP_LOSS", "TRAILING_STOP")]
            if len(stops) >= 3:
                stop_times = sorted(_to_mins(s["exit_time"]) for s in stops)
                for i in range(len(stop_times) - 2):
                    if stop_times[i + 2] - stop_times[i] <= 15:
                        gate_triggered = True
                        break

        elif gate == "half_loss":
            if trade["time"] >= "10:00" and len(completed) >= 2:
                losses = sum(1 for t in completed if t["pnl"] < 0)
                if losses / len(completed) > 0.5:
                    gate_triggered = True

        elif gate == "no_momentum":
            if trade["time"] >= "11:00":
                early = [t for t in accepted if t["time"] < "11:00"]
                if early:
                    has_momentum = any(
                        t["exit_reason"] in ("TAKE_PROFIT", "TRAILING_STOP")
                        or t["pnl_pct"] >= 1.0
                        for t in early
                    )
                    if not has_momentum:
                        gate_triggered = True

        if gate_triggered:
            blocked_count += 1
        else:
            accepted.append(trade)

    pnl = round(sum(t["pnl"] for t in accepted), 2)
    return pnl, len(accepted), blocked_count


def run():
    path = os.path.join(BASE_DIR, "exercises.json")
    with open(path) as f:
        data = json.load(f)

    days = sorted(
        [e for e in data if "Exercise 1" in e["title"]],
        key=lambda e: e["date"]
    )

    gates = ["triple_stop", "half_loss", "no_momentum"]

    col_w = 14
    header = f"{'Date':<12} {'Baseline':>{col_w}} {'TripleStop':>{col_w}} {'HalfLoss':>{col_w}} {'NoMomentum':>{col_w}}"
    print(header)
    print("-" * len(header))

    totals = {"baseline": 0.0, "triple_stop": 0.0, "half_loss": 0.0, "no_momentum": 0.0}
    blocked_totals = {"triple_stop": 0, "half_loss": 0, "no_momentum": 0}
    days_changed = {"triple_stop": 0, "half_loss": 0, "no_momentum": 0}

    for day in days:
        baseline = day["total_pnl"]
        totals["baseline"] += baseline

        results = {}
        for gate in gates:
            pnl, accepted, blocked = apply_gate(day, gate)
            results[gate] = (pnl, blocked)
            totals[gate] += pnl
            blocked_totals[gate] += blocked
            if blocked > 0:
                days_changed[gate] += 1

        def fmt(pnl, gate=None):
            base = f"${pnl:+.2f}"
            if gate and results[gate][1] > 0:
                diff = results[gate][0] - baseline
                sign = "+" if diff >= 0 else ""
                base += f" ({sign}{diff:.2f})"
            return base

        print(
            f"{day['date']:<12}"
            f" {f'${baseline:+.2f}':>{col_w}}"
            f" {fmt(results['triple_stop'][0], 'triple_stop'):>{col_w}}"
            f" {fmt(results['half_loss'][0], 'half_loss'):>{col_w}}"
            f" {fmt(results['no_momentum'][0], 'no_momentum'):>{col_w}}"
        )

    print("-" * len(header))
    b  = f"${totals['baseline']:+.2f}"
    ts = f"${totals['triple_stop']:+.2f}"
    hl = f"${totals['half_loss']:+.2f}"
    nm = f"${totals['no_momentum']:+.2f}"
    print(
        f"{'TOTAL':<12}"
        f" {b:>{col_w}}"
        f" {ts:>{col_w}}"
        f" {hl:>{col_w}}"
        f" {nm:>{col_w}}"
    )
    print()
    print("Net vs baseline:")
    for gate in gates:
        diff = totals[gate] - totals["baseline"]
        sign = "+" if diff >= 0 else ""
        label = gate.replace("_", " ").title()
        print(f"  {label:<14}: {sign}${diff:.2f}  "
              f"({days_changed[gate]} days triggered, {blocked_totals[gate]} trades blocked)")


if __name__ == "__main__":
    run()
