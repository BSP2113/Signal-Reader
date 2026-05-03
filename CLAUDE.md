# CLAUDE.md — Signal Reader Project

## About Me

I have limited coding knowledge. Please explain technical decisions in plain English when relevant. Prefer simple, readable code over clever solutions. Always tell me what you're doing and why.

---

## Project Vision

This is not a hobby project. The goal is to build a fully capable trading signal system, validated through rigorous mock trading, with the end goal of graduating to real trading when performance proves it is ready.

**I approach this as a full-time job. I am obsessed with getting it right. I actively try and maximize gains while keeping losses low.**

The user will monitor progress across mock trading exercises. Graduation to real trading only happens when the system proves itself consistently — not based on one good day, not based on gut feeling, but based on data.

---

## Graduation Standard (Mock → Real Trading)

The system must prove ALL of the following before real money is ever considered:

- **30+ trading days** of logged mock exercises
- **Win rate above 55%** sustained across those days
- **Average win larger than average loss** (positive expectancy)
- **No single day loses more than 5% of capital**
- **Signals hold up across multiple market conditions** — up days, down days, choppy days
- **Risk management is enforced** — position sizing, stop losses, max daily loss respected every session

---

## What Needs to Be Built (Roadmap)

### For completed tests refer to /home/ben/Signal/memory.md. Update anything completed on this file.
- Refer to additional memories at /home/ben/.claude/projects/-home-ben/memory/ when necessary.

### In Progress / Next
- **30-day mark review of EX2 re-entries** — net slightly negative over 12 days; decide keep or drop at 30-day mark
- **30-day mark review of KOPN and CRDO** — new tickers added Apr 28; evaluate per-ticker P&L at 30 days

### Future
- Paper trading via Alpaca (Phase 2)
- Live trading only after graduation criteria are met

---

## Exercise Shorthands

### EX1 — Buy Only, $5,000
- Starting capital: $5,000, compounding from prior sessions (adds/subtracts prior P&L)
- Buy only — no shorts, no sells mid-session
- One entry per ticker on first qualifying BUY signal (TAKE or MAYBE)
- Signal types:
  - **ORB** — first 1-minute close above the 9:30–9:44 opening range high (before 11:30)
  - **GAP_GO** — for tickers gapping up ≥3% at open, first close above opening bar's high in first 10 minutes (09:30–09:39); replaces ORB for that ticker that day; RKLB excluded
- Relative strength gate: ticker must be outperforming SPY at the moment the signal fires
- Allocations (% of starting balance, then scaled by ATR modifier 0.40x–1.50x):
  - BULL market: TAKE = 35%, MAYBE = 20%
  - NEUT market: TAKE = 30%, MAYBE = 15%
  - BEAR market: TAKE = 10%, MAYBE = 10%
- Streak cut: after 2+ consecutive losing days, MAYBE allocations × 0.50
- Drawdown cut: if portfolio >1.5% below rolling 5-day peak, all allocations × 0.50
- **Exits (first trigger wins):**
  1. Take Profit — price reaches +3% from entry
  2. Trailing Stop — price drops 2.0% from peak (only activates after +1% peak)
  3. Stop Loss — price drops 1.5% from entry
  4. No-Progress — if price is at or below entry 90 minutes after entry, exit immediately (only before 14:00)
  5. Time Close — 14:00 hard exit for all open positions
  6. Daily Loss Limit — no new entries once session P&L < -$75
- Log results to `exercises.json`, run via: `venv/bin/python3 ex1.py [YYYY-MM-DD]`
- Log three growth opportunities on how to improve the model based on that days scenarios in the Claude's Notes section. Update JSON to reflect
	- This is important and must be done every day.

**Live session setup:**
- Terminal 1: `claude --dangerouslySkipPermissions`
- Terminal 2: `cd ~/Signal && venv/bin/python3 run.py`
- Cron fires ex1.py at 9:30 AM, logs and pushes at 4:00 PM automatically

### EX2 — Buy Only with Re-entries, $5,000
- All rules identical to EX1, plus:
- After a STOP_LOSS or TRAILING_STOP exit, may re-enter the same ticker if a new qualifying ORB signal fires before 13:30
- Re-entry allocation = 75% of original allocation
- Must wait 5 bars (5 minutes) after exit before scanning for re-entry signal
- Re-entry uses same exit logic (stop loss, take profit, trailing stop, time close)
- GAP_GO trades are not eligible for re-entry (gap window closes by 09:39)
- **Current status:** Re-entries slightly net negative over 12 days — monitoring at 30-day mark
- Log results to `exercises.json`, run via: `venv/bin/python3 ex2.py [YYYY-MM-DD]`

---

## exercises.json Rules

- **Never update exercises.json without the user explicitly saying to.** This is the live record — modifying it during testing corrupts the historical data.
- When testing changes, run a single date to stdout only (do not save). Only write to exercises.json after the user approves the change.
- When re-running approved changes: strip the affected exercise's entries, re-run dates in chronological order with `backfill=False` so the wallet balance accumulates correctly from $5,000.

---

## Current Standard Setup

### Files
- `fetch_data.py` — fetches 1-minute OHLC + volume data via Alpaca IEX, generates `dashboard.html`
- `ex1.py` — EX1 simulation (buy only, compounding wallet, full exit logic)
- `ex2.py` — EX2 simulation (same as EX1 plus re-entry logic)
- `run.py` — re-runs fetch every 60 seconds, sweeps `.tmp` files into `tmp/`
- `dashboard.html` — auto-refreshes every 60 seconds in the browser
- `exercises.json` — stores all simulation exercise results (never modify without permission)
- `backfill.json` — EX1 results across historical dates used for streak/drawdown calculations
- `backfill2.json` — EX2 results across historical dates
- `growth_state.json` — tracks which improvements have been addressed or rejected
- `market_state.json` — current BULL/NEUT/BEAR classification (SPY gap + VIXY trend)
- `venv/` — Python virtual environment with `alpaca-py` installed

### How to run
```
venv/bin/python3 run.py
```
Then open `dashboard.html` in your browser.

### Tickers
**NVDA, TSLA, AMD, COIN, META, PLTR, SMCI, CRDO, IONQ, RIVN, DELL, KOPN, SHOP, ASTS, ARM, DKNG, RKLB, RDDT** — 18 high-volatility, high-volume names.
- BBAI removed: 0% win rate over 12 tracked days, worst P&L in pool
- NFLX removed: 0% win rate over 12 tracked days, second worst P&L
- KOPN added Apr 28, 2026 — low-priced ($2–4), ATR modifier ~0.57–0.77x
- CRDO added Apr 28, 2026 — mid-cap semiconductor, good intraday volume
- CRWD removed May 2, 2026 — worst performer, -$51.03 over 19 trades, 42% win rate
- DELL added May 2, 2026 — 62% win rate, +$109.84 over 30-day backtest
- No crypto tickers — MSTR, MARA, RIOT, ETHA considered and declined (additional tax filing requirements)

### Dashboard Features
- Ticker tabs — one chart at a time
- **P&L tab** — cumulative exercise results, per-day breakdown for EX1 and EX2
- **Improvements tab** — four sub-tabs:
  - *Shipped* — improvements live in the model with backtest impact
  - *Active Logic* — all current rules rated: working well / keep an eye on / not working well
  - *Revisit* — ideas that need more data before a decision
  - *Not Pursuing* — tested and decided against, with rationale
- **Graduation tab** — tracks progress toward all 30-day graduation criteria
- Day dropdown — filter to specific trading day
- Candlestick default, toggle to Line
- Zoom (scroll wheel) + pan (drag) + Reset Zoom
- Signals table — collapsible, with TAKE/MAYBE/SKIP ratings

---

## Signal Scoring Logic

Evaluated with **no lookahead** — only data visible at the moment the signal fires.

### ORB Signal (standard)
1. **Volume floor** — < 1.0x avg = SKIP
2. **Volume conviction** — >= 1.5x avg adds +1 to score
3. **Choppiness** — < 3 direction flips in last 12 bars = +1, more = -1
4. **Dominant trend protection** — if price is up/down > 2% from today's open, counter-trend signals require >= 2x volume or are blocked

Ratings: **TAKE** (score >= 2) | **MAYBE** (score >= 0, volume >= 1.0x) | **SKIP** (blocked or volume < 1.0x)

### GAP_GO Signal
- Triggers when ticker gaps up ≥3% from prior close
- Scans 09:30–09:39 for first bar that closes above the opening bar's high
- Volume floor: ≥1.0x avg required; TAKE at ≥1.5x, MAYBE otherwise
- SPY relative strength gate still applies
- RKLB excluded (0/4 win rate in testing)
- Replaces ORB entirely for that ticker on gap days

---

## Lessons Learned

- All lessons learned are located in the memory.md file. Continue to update this as more tests are completed.

---

## APIs

- **Alpaca IEX** — used for all 1-minute bar data; requires API key in `.env`
- **Alpaca trading API** — for future paper/live trading only

## Do NOT Build (Until Graduation)

- Automated trade execution
- Live order placement
- Any connection to real brokerage accounts

## Key Reminders

- Always explain what an API key is and where to get one before assuming I have it
- **Always warn before any action that could cost real money or place real trades**
- Prefer paper trading and simulation over anything touching real funds
- Continue to update memory.md file with user's preferences, testing data, and lessons learned moving forward.
- Build the simpler version first, then iterate
- Be honest about bad signals — do not spin results
- **Never update exercises.json without the user explicitly saying to** — it is the live record
