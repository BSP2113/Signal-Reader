"""
write_growth_ops.py — Auto-generate daily growth ops for EX1, EX2, and EX3.

EX1 ops: ORB/GAP_GO signal quality, sizing, exits — what failed or was left on the table.
EX2 ops: re-entry quality, PM_ORB performance, how the extra signals compared to the base layer.
EX3 ops: hybrid routing analysis — was the right mode selected, and what did the choice cost or earn.

Run manually:  venv/bin/python3 write_growth_ops.py [YYYY-MM-DD]
Cron calls it: venv/bin/python3 write_growth_ops.py  (defaults to today)
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CLAUDE_BIN = "/home/ben/.local/bin/claude"
FDP        = os.path.join(BASE_DIR, "fetch_data.py")


# ── data helpers ─────────────────────────────────────────────────────────────

def _load_exercises():
    with open(os.path.join(BASE_DIR, "exercises.json")) as f:
        return json.load(f)

def get_ex(trade_date, label):
    return next(
        (e for e in _load_exercises() if label in e.get("title", "") and e["date"] == trade_date),
        None,
    )

def trade_lines(trades):
    lines = []
    for t in trades:
        lines.append(
            f"  {t['ticker']} #{t['trade_num']} | {t['signal']} | {t['rating']} "
            f"{t['vol_ratio']}x vol | gap {t.get('gap_pct', 0):+.1f}% | "
            f"entry {t['time']} ${t['entry']:.2f} → exit {t['exit_time']} "
            f"${t['exit']:.2f} ({t['exit_reason']}) | "
            f"P&L ${t['pnl']:+.2f} ({t['pnl_pct']:+.2f}%)"
        )
    return "\n".join(lines)


# ── prompt builders ───────────────────────────────────────────────────────────

def prompt_ex1(ex1):
    return f"""You are analyzing mock trading results for a signal system. Today is {ex1['date']}.

Market: {ex1.get('market_state','neutral').upper()}  |  EX1 P&L: ${ex1['total_pnl']:+.2f}

EX1 trades (ORB / GAP_GO only, one entry per ticker):
{trade_lines(ex1['trades'])}

Generate exactly 3 growth opportunities focused on EX1 signal and exit quality. Rules:
- Reference specific tickers, dollar amounts, times, and exit reasons from the data above
- Identify concrete patterns: false breakouts, take-profits that capped winners, stops that fired early, late entries that had no runway
- Each must end with a sentence starting with "Test:" proposing a specific, testable rule change to the ORB/GAP_GO/exit logic
- No generic observations — every point must be traceable to actual trades above

Return ONLY a JSON array, no other text:
[
  {{"title": "short specific title with ticker/signal/amount", "body": "detailed analysis... Test: exact rule."}},
  {{"title": "...", "body": "..."}},
  {{"title": "...", "body": "..."}}
]"""


def prompt_ex2(ex1, ex2):
    re_trades   = [t for t in ex2["trades"] if t.get("signal") == "REENTRY"]
    pm_trades   = [t for t in ex2["trades"] if t.get("signal") == "PM_ORB"]
    af_trades   = [t for t in ex2["trades"] if t.get("signal") == "AFTERNOON"]
    base_trades = [t for t in ex2["trades"] if t.get("signal") not in ("REENTRY", "PM_ORB", "AFTERNOON")]

    re_pnl = sum(t["pnl"] for t in re_trades)
    pm_pnl = sum(t["pnl"] for t in pm_trades)
    af_pnl = sum(t["pnl"] for t in af_trades)

    return f"""You are analyzing mock trading results for a signal system. Today is {ex2['date']}.

Market: {ex2.get('market_state','neutral').upper()}  |  EX2 P&L: ${ex2['total_pnl']:+.2f}  |  EX1 P&L: ${ex1['total_pnl']:+.2f}  |  EX2 vs EX1: ${ex2['total_pnl'] - ex1['total_pnl']:+.2f}

EX2 base trades (ORB/GAP_GO — same entry logic as EX1):
{trade_lines(base_trades) or '  (none)'}

Re-entries (net: ${re_pnl:+.2f}):
{trade_lines(re_trades) or '  (none)'}

PM_ORB signals (net: ${pm_pnl:+.2f}):
{trade_lines(pm_trades) or '  (none)'}

Afternoon breakouts (net: ${af_pnl:+.2f}):
{trade_lines(af_trades) or '  (none)'}

Generate exactly 3 growth opportunities focused on what EX2's EXTRA signals (re-entries, PM_ORB, afternoon) contributed or cost today. Rules:
- Focus on the re-entry and PM_ORB layer specifically — were they worth it? Did they add or subtract vs EX1?
- Reference specific tickers, entry conditions, and outcomes from the extra signals above
- Identify patterns: re-entries that doubled down on losing setups, PM_ORBs that fired too late, afternoon signals that succeeded or failed
- Each must end with "Test:" proposing a change to re-entry rules, PM_ORB filtering, or afternoon logic
- Do NOT repeat EX1 signal quality points — those belong in EX1 notes

Return ONLY a JSON array, no other text:
[
  {{"title": "short specific title", "body": "detailed analysis... Test: exact rule."}},
  {{"title": "...", "body": "..."}},
  {{"title": "...", "body": "..."}}
]"""


def prompt_ex3(ex1, ex2, ex3):
    mode         = "EX1" if ex3.get("market_state") == "bullish" else "EX2"
    other_mode   = "EX2" if mode == "EX1" else "EX1"
    other_pnl    = ex2["total_pnl"] if mode == "EX1" else ex1["total_pnl"]
    routing_gain = ex3["total_pnl"] - other_pnl

    return f"""You are analyzing hybrid routing decisions for a signal system. Today is {ex3['date']}.

Market pre-market: {ex3.get('market_state','neutral').upper()}
Hybrid (EX3) routed to: {mode} mode
EX3 P&L: ${ex3['total_pnl']:+.2f}  |  {other_mode} P&L today: ${other_pnl:+.2f}  |  Routing gain vs alternative: ${routing_gain:+.2f}

EX3 trades (ran as {mode}):
{trade_lines(ex3['trades'])}

{mode} reference P&L breakdown: ${ex3['total_pnl']:+.2f}
{other_mode} reference P&L breakdown: ${other_pnl:+.2f}

Generate exactly 3 growth opportunities focused on the HYBRID ROUTING DECISION and its consequences today. Rules:
- Analyze whether routing to {mode} was the right call given how the day actually played out
- Reference specific trades where the {mode} approach helped or hurt relative to what {other_mode} would have done differently
- Consider: was the pre-market classification accurate? Did the day behave as a {ex3.get('market_state','neutral')} day should?
- Each must end with "Test:" proposing a refinement to the routing logic, the BULL/NEUT/BEAR threshold, or a hybrid rule
- Focus on the routing layer — not on individual signal quality (that belongs in EX1/EX2 notes)

Return ONLY a JSON array, no other text:
[
  {{"title": "short specific title about routing", "body": "detailed routing analysis... Test: exact rule."}},
  {{"title": "...", "body": "..."}},
  {{"title": "...", "body": "..."}}
]"""


# ── insertion helpers ─────────────────────────────────────────────────────────

def py_str(s):
    return '"' + json.dumps(s)[1:-1] + '"'

def build_entry(trade_date, ops):
    tuples = []
    for op in ops:
        tuples.append(
            f"        (\n"
            f"            {py_str(op['title'])},\n"
            f"            {py_str(op['body'])}\n"
            f"        )"
        )
    return f'    "{trade_date}": [\n' + ",\n".join(tuples) + "\n    ],"

def insert_before_marker(content, entry, marker):
    idx = content.find(marker)
    if idx == -1:
        print(f"  ERROR: marker not found: {marker!r}")
        sys.exit(1)
    return content[:idx] + "\n" + entry + content[idx:]

def already_written(trade_date, dict_name):
    with open(FDP) as f:
        content = f.read()
    start = content.find(f"{dict_name} = {{")
    end   = content.find("\n}", start)
    return f'"{trade_date}"' in content[start:end]


# ── Claude call ───────────────────────────────────────────────────────────────

def call_claude(prompt, label):
    print(f"  Calling Claude for {label}...")
    result = subprocess.run(
        [CLAUDE_BIN, "-p", prompt],
        capture_output=True, text=True, cwd=BASE_DIR, timeout=120,
    )
    if result.returncode != 0:
        print(f"  Claude error ({label}): {result.stderr[:300]}")
        return None
    raw   = result.stdout.strip()
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        print(f"  Could not parse JSON ({label}):\n{raw[:300]}")
        return None
    ops = json.loads(match.group(0))
    if len(ops) != 3:
        print(f"  Expected 3 ops, got {len(ops)} ({label})")
        return None
    return ops


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pos_args   = [a for a in sys.argv[1:] if not a.startswith("--")]
    trade_date = pos_args[0] if pos_args else datetime.now().strftime("%Y-%m-%d")

    print(f"Growth ops — {trade_date}")

    ex1 = get_ex(trade_date, "Exercise 1")
    ex2 = get_ex(trade_date, "Exercise 2")
    ex3 = get_ex(trade_date, "Exercise 3")

    if not ex1:
        print("  No EX1 data, skipping.")
        sys.exit(0)

    wrote_any = False

    # --- EX1 ---
    if already_written(trade_date, "PER_DAY_GROWTH"):
        print("  EX1 notes already written.")
    else:
        ops1 = call_claude(prompt_ex1(ex1), "EX1")
        if ops1:
            with open(FDP) as f:
                content = f.read()
            content = insert_before_marker(content, build_entry(trade_date, ops1),
                                           "\n}\n\n# Links each per-day note")
            content = insert_before_marker(content,
                                           f'    "{trade_date}": [None, None, None],',
                                           "\n}\n\n# Per-day Claude's Notes for Exercise 2")
            with open(FDP, "w") as f:
                f.write(content)
            print("  EX1 notes written.")
            wrote_any = True

    # --- EX2 ---
    if not ex2:
        print("  No EX2 data, skipping EX2 notes.")
    elif already_written(trade_date, "PER_DAY_GROWTH_EX2"):
        print("  EX2 notes already written.")
    else:
        ops2 = call_claude(prompt_ex2(ex1, ex2), "EX2")
        if ops2:
            with open(FDP) as f:
                content = f.read()
            content = insert_before_marker(content, build_entry(trade_date, ops2),
                                           "\n}\n\n# Per-day Claude's Notes for Exercise 3")
            with open(FDP, "w") as f:
                f.write(content)
            print("  EX2 notes written.")
            wrote_any = True

    # --- EX3 ---
    if not ex3:
        print("  No EX3 data, skipping EX3 notes.")
    elif already_written(trade_date, "PER_DAY_GROWTH_EX3"):
        print("  EX3 notes already written.")
    else:
        ops3 = call_claude(prompt_ex3(ex1, ex2 or ex1, ex3), "EX3")
        if ops3:
            with open(FDP) as f:
                content = f.read()
            content = insert_before_marker(content, build_entry(trade_date, ops3),
                                           "\n}\n\ndef load_growth_state")
            with open(FDP, "w") as f:
                f.write(content)
            print("  EX3 notes written.")
            wrote_any = True

    if wrote_any:
        subprocess.run(
            [sys.executable, os.path.join(BASE_DIR, "fetch_data.py")],
            capture_output=True, cwd=BASE_DIR,
        )
        print("  Dashboard regenerated.")

    print("Done.")
