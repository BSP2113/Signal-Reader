# CLAUDE.md — Signal Reader Project

## About Me

I have limited coding knowledge. Please explain technical decisions in plain English when relevant. Prefer simple, readable code over clever solutions. Always tell me what you're doing and why.

---

## Project Vision

This is not a hobby project. The goal is to build a fully capable trading signal system, validated through rigorous mock trading, with the end goal of graduating to real trading when performance proves it is ready.

**I approach this as a full-time job. I am obsessed with getting it right.**

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

### Completed
- 1-minute OHLC candlestick dashboard (Yahoo Finance)
- TAKE/MAYBE/SKIP signal scoring (volume floor, choppiness, dominant trend protection)
- P&L tracker with exercise logging
- Ticker tabs, day selector, zoom/pan, collapsible signals

### In Progress / Next
- **EX2** — definition forthcoming from user
- **Backtesting** — test signal logic against historical data before running live exercises
- **Stop loss logic** — exit a position if price moves X% against entry
- **Performance metrics** — win rate, avg win/loss ratio, drawdown, tracked cumulatively
- **Post-mortem logging** — after each exercise, log what worked, what didn't, what to change
- **Market context awareness** — flag earnings dates, pre-market gaps, macro events that invalidate signals
- **COIN no-signal investigation** — COIN up +5.37% on 4/20 but EMA crossover never triggered; understand why strong trending days get missed

### Future
- Paper trading via Alpaca (Phase 2)
- Live trading only after graduation criteria are met

---

## Exercise Shorthands

### EX1 — Buy Only, $1,000
- Starting capital: $1,000
- Buy only, no sells
- One entry per ticker on first qualifying BUY signal (TAKE or MAYBE, EMA9 crosses above EMA21)
- Allocation: TAKE = $350, MAYBE = $200, never exceed budget
- Hold to end of day — EOD price = official daily close from yfinance daily bar
- Log results to `exercises.json` and display in P&L tracker
- During live sessions: Claude watches in real time from 9:30 AM EST, enters as signals fire, logs at 4:00 PM
- Historic knowledge is used openly (same edge any experienced trader has); no future data is available in live sessions
- Morning setup: Terminal 1 `claude --dangerouslySkipPermissions`, Terminal 2 `cd ~/Signal && venv/bin/python3 run.py`

### EX2 — TBD
Definition forthcoming from user.

---

## Current Standard Setup

### Files
- `fetch_data.py` — fetches 1-minute OHLC + volume data via Yahoo Finance, generates `dashboard.html`
- `run.py` — re-runs fetch every 60 seconds, sweeps `.tmp` files into `tmp/`
- `dashboard.html` — auto-refreshes every 60 seconds in the browser
- `exercises.json` — stores all simulation exercise results
- `venv/` — Python virtual environment with `yfinance` installed

### How to run
```
venv/bin/python3 run.py
```
Then open `dashboard.html` in your browser.

### Tickers
**NVDA, TSLA, AMD, COIN, META, PLTR, MSTR, SMCI, NFLX, HOOD** — 10 stocks, no crypto, chosen for high volatility and solid volume. MSTR is the most volatile (Bitcoin proxy, 5–15% daily moves).

### Dashboard Features
- Ticker tabs — one chart at a time
- **P&L button** — top right, shows all exercise results
- Day dropdown — filter to specific trading day (last 5 days)
- Candlestick default, toggle to Line
- Zoom (scroll wheel) + pan (drag) + Reset Zoom
- Signals table — collapsible, with TAKE/MAYBE/SKIP ratings

---

## Signal Scoring Logic

Evaluated with **no lookahead** — only data visible at the moment the signal fires.

1. **Volume floor** — < 0.5x avg = always SKIP. 0.5–1.0x = capped at MAYBE. Only >= 1.0x can reach TAKE
2. **Volume conviction** — >= 1.5x avg adds +1 to score
3. **Choppiness** — < 3 direction flips in last 12 bars = +1, more = -1
4. **Dominant trend protection** — if price is up/down > 2% from today's open, counter-trend signals require >= 2x volume or are blocked

Ratings: **TAKE** (score >= 1, volume floor met) | **MAYBE** (score == 0 or low volume) | **SKIP** (blocked)

---

## Lessons Learned

- **2026-04-20**: Thin volume signals (< 1.0x avg) are noise — NVDA SELL at 0.5x volume = -1.28% loss
- **2026-04-20**: Dominant trend protection correctly held COIN long through +4.70% day
- **2026-04-20**: Earliest qualifying entry on a trending day outperforms all re-entries
- **2026-04-20 EX1**: COIN up +5.37% but EMA9 never crossed EMA21 during market hours — strong trending days can go undetected; investigate
- **2026-04-20 EX1**: Most entries came at 15:21 (very late) — late EMA crossovers leave little time to capture the move
- **2026-04-20 EX1**: META was our only TAKE (-1.05%) — high volume alone doesn't guarantee direction; context matters
- **2026-04-20 EX1**: MSTR +3.23% was the best trade despite only 0.8x volume — volatile stocks can perform even on thin volume days
- **General**: Counter-trend entries are luck, not skill — avoid unless volume strongly confirms
- **EOD pricing**: Always use official daily close from yfinance daily bar, not the last 1-minute bar (can differ significantly)

---

## APIs

- **Yahoo Finance** — no API key needed, currently in use
- **Alpha Vantage** — free tier, stocks and crypto, requires free API key
- **Coinbase API** — for crypto data
- **Alpaca** — for future paper/live trading only

## Do NOT Build (Until Graduation)

- Automated trade execution
- Live order placement
- Any connection to real brokerage accounts

## Key Reminders

- Always explain what an API key is and where to get one before assuming I have it
- **Always warn before any action that could cost real money or place real trades**
- Prefer paper trading and simulation over anything touching real funds
- Build the simpler version first, then iterate
- Be honest about bad signals — do not spin results
