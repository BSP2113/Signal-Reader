"""
test_sndk_mu.py — Backtest SNDK + MU replacing RIVN + PLTR.
Runs all backfill (38d) + live (18d) EX1 dates.
Reports per-ticker stats for the two new tickers and a day-by-day P&L delta.

Run: venv/bin/python3 tests/test_sndk_mu.py
"""
import json, os, sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import ex1

NEW_TICKERS = ["NVDA", "TSLA", "AMD", "COIN", "META", "SNDK", "SMCI", "CRDO",
               "IONQ", "MU",   "DELL", "KOPN", "SHOP", "ASTS", "ARM",  "DKNG", "UPST"]

RESULT_FILE = "_test_sndk_mu.json"
RESULT_PATH = os.path.join(BASE_DIR, RESULT_FILE)

def get_dates(fname, label):
    data = json.load(open(os.path.join(BASE_DIR, fname)))
    return sorted(e["date"] for e in data if label in e["title"])

def ticker_stats(trades, name):
    if not trades:
        return f"  {name}: no trades"
    wins   = [t for t in trades if t["pnl"] >= 0]
    losses = [t for t in trades if t["pnl"] <  0]
    total  = sum(t["pnl"] for t in trades)
    wr     = 100 * len(wins) // len(trades)
    avg_w  = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
    avg_l  = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    return (f"  {name}: {len(trades)} trades  {len(wins)}W/{len(losses)}L  "
            f"({wr}% WR)  total ${total:+.2f}  avg_win ${avg_w:+.2f}  avg_loss ${avg_l:+.2f}")

# ── Baseline: RIVN and PLTR from stored data ──────────────────────────────
def baseline_ticker(ticker):
    all_trades = []
    for fname, label in [("backfill.json","Exercise 1"), ("exercises.json","Exercise 1")]:
        path = os.path.join(BASE_DIR, fname)
        data = json.load(open(path))
        for e in data:
            if label in e["title"]:
                all_trades += [t for t in e.get("trades",[]) if t["ticker"] == ticker]
    return all_trades

print("\n" + "="*70)
print("BASELINE: RIVN and PLTR (stored data, 55 combined days)")
print("="*70)
for t in ["RIVN", "PLTR"]:
    bt = [x for x in baseline_ticker(t)]
    bf_t  = []
    liv_t = []
    bf_dates = set(get_dates("backfill.json","Exercise 1"))
    liv_dates = set(get_dates("exercises.json","Exercise 1"))
    for x in bt:
        if x.get("date") in bf_dates: bf_t.append(x)
        else: liv_t.append(x)
    print(ticker_stats(bf_t,  f"{t} (backfill)"))
    print(ticker_stats(liv_t, f"{t} (live)    "))

# ── Run new tickers ───────────────────────────────────────────────────────
bf_dates   = get_dates("backfill.json",  "Exercise 1")
live_dates = get_dates("exercises.json", "Exercise 1")
all_dates  = list(dict.fromkeys(bf_dates + live_dates))   # backfill first, then live extras

orig_tickers     = ex1.TICKERS[:]
orig_early_weak  = ex1.EARLY_WEAK_SKIP.copy()
ex1.TICKERS       = NEW_TICKERS
ex1.EARLY_WEAK_SKIP = {"TSLA"}          # PLTR removed; SNDK/MU use normal early-weak check

with open(RESULT_PATH, "w") as f: json.dump([], f)

print("\n" + "="*70)
print("RUNNING ALL DATES with SNDK + MU  (this will take several minutes)...")
print("="*70)

# Load stored baseline P&L per date
stored_pnl = {}
for fname, label in [("backfill.json","Exercise 1"),("exercises.json","Exercise 1")]:
    data = json.load(open(os.path.join(BASE_DIR, fname)))
    for e in data:
        if label in e["title"]:
            stored_pnl[e["date"]] = e["total_pnl"]

try:
    for date in all_dates:
        is_bf = date in set(bf_dates)
        r = ex1.run_ex1(date, backfill=is_bf, save=True, result_file=RESULT_FILE)
finally:
    ex1.TICKERS       = orig_tickers
    ex1.EARLY_WEAK_SKIP = orig_early_weak

# ── Collect results ───────────────────────────────────────────────────────
results = [e for e in json.load(open(RESULT_PATH)) if "Exercise 1" in e["title"]]
os.remove(RESULT_PATH)

all_trades = [t for e in results for t in e.get("trades", [])]
sndk_t = [t for t in all_trades if t["ticker"] == "SNDK"]
mu_t   = [t for t in all_trades if t["ticker"] == "MU"]

print("\n" + "="*70)
print("NEW TICKER RESULTS: SNDK and MU")
print("="*70)

for ticker, trades in [("SNDK", sndk_t), ("MU", mu_t)]:
    by_date = {e["date"]: e for e in results}
    bf_set  = set(bf_dates)
    bf_t    = [t for t in trades if t.get("date") in bf_set or
               any(t in e.get("trades",[]) for e in results if e["date"] in bf_set and e["date"] == t.get("date",""))]
    # simpler: tag by result date
    bf_t   = []
    liv_t  = []
    for e in results:
        for t in e.get("trades", []):
            if t["ticker"] != ticker: continue
            if e["date"] in set(bf_dates): bf_t.append(t)
            else: liv_t.append(t)
    print(ticker_stats(bf_t,   f"{ticker} (backfill)"))
    print(ticker_stats(liv_t,  f"{ticker} (live)    "))
    print(ticker_stats(bf_t+liv_t, f"{ticker} (combined)"))
    print()
    # Per-trade detail
    combined = bf_t + liv_t
    if combined:
        print(f"  {'Date':<12} {'Time':>5} {'Signal':<8} {'Rating':<5} {'Vol':>4}  {'Exit':<16} {'PnL':>9}")
        print("  " + "-"*68)
        for e in results:
            for t in e.get("trades", []):
                if t["ticker"] != ticker: continue
                print(f"  {e['date']:<12} {t['time']:>5} {t['signal']:<8} {t['rating']:<5} "
                      f"{t['vol_ratio']:>3.1f}x  {t['exit_reason']:<16} {t['pnl']:>+9.2f}")
    print()

# ── Day-by-day delta ─────────────────────────────────────────────────────
print("="*70)
print("DAY-BY-DAY DELTA: new total P&L vs stored baseline")
print("="*70)
result_map = {e["date"]: e["total_pnl"] for e in results}

print(f"\n  {'Date':<12} {'Baseline':>10} {'New':>10} {'Delta':>8}  New-ticker trades")
print("  " + "-"*68)
total_base = total_new = 0.0
for date in sorted(result_map):
    base  = stored_pnl.get(date, 0.0)
    new   = result_map[date]
    delta = new - base
    new_t = [t["ticker"] for e in results if e["date"] == date
             for t in e.get("trades",[]) if t["ticker"] in ("SNDK","MU")]
    tag = ", ".join(new_t) if new_t else "—"
    flag = " ◄" if abs(delta) > 10 else ""
    print(f"  {date:<12} ${base:>+8.2f} ${new:>+8.2f} {delta:>+8.2f}  {tag}{flag}")
    total_base += base; total_new += new

print("  " + "-"*68)
delta = total_new - total_base
print(f"  {'TOTAL':<12} ${total_base:>+8.2f} ${total_new:>+8.2f} {delta:>+8.2f}")

print("\n" + "="*70)
print("SUMMARY")
print("="*70)
print(f"\n  Replaced: RIVN (33% WR live / 30% backfill) and PLTR (45% WR live / 46% backfill)")
for ticker, trades in [("SNDK", sndk_t), ("MU", mu_t)]:
    all_t = [t for e in results for t in e.get("trades",[]) if t["ticker"] == ticker]
    if all_t:
        wins  = sum(1 for t in all_t if t["pnl"] >= 0)
        total = sum(t["pnl"] for t in all_t)
        print(f"  {ticker}: {len(all_t)} trades  {100*wins//len(all_t)}% WR  ${total:+.2f} total")
    else:
        print(f"  {ticker}: no trades fired")
print(f"\n  Net P&L delta vs baseline: ${total_new - total_base:+.2f} over {len(result_map)} days")
