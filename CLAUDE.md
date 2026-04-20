# CLAUDE.md — Project Context & Goals

## About Me

I have limited coding knowledge. Please explain technical decisions in plain English when relevant. Prefer simple, readable code over clever solutions. Always tell me what you're doing and why.

## Project Goal

Build a **market signal spotter** — a tool that pulls live and historical pricing data from financial APIs, identifies trends, and flags potential buy/sell opportunities.

This is a **monitoring and analysis tool first**. Automated trade execution is a future consideration only after signals are validated over time.

## Current Standard Setup (Phase 1 — Complete)

### Files
- `fetch_data.py` — fetches 1-minute OHLC + volume data via Yahoo Finance, generates `dashboard.html`
- `run.py` — re-runs fetch every 60 seconds to keep dashboard live
- `dashboard.html` — auto-refreshes every 60 seconds in the browser
- `exercises.json` — stores all simulation exercise results, displayed in the P&L tracker
- `venv/` — Python virtual environment with `yfinance` installed

### How to run
```
venv/bin/python3 run.py
```
Then open `dashboard.html` in your browser.

### Dashboard features
- **Ticker tabs** (NVDA, TSLA, COIN, SOL-USD) — one chart at a time, chosen for high volatility + solid volume
- **P&L button** — top right, opens the P&L tracker showing all exercise results
- **Day dropdown** — filters chart to a specific trading day (last 5 days available)
- **Candlestick view by default** — toggle to Line chart and back
- **Zoom** (scroll wheel) + **pan** (click and drag) + **Reset Zoom** button
- **Signals table** — collapsible (click ▶ Signals to expand), flags 1-minute candles where price moved >1% or volume spiked >2x average
- **TAKE / MAYBE / SKIP rating** on each signal

### Signal scoring logic
Each signal is scored on three factors — evaluated with no lookahead (only uses data visible at the moment the signal fires):

1. **Volume floor** — below 0.5x average = always SKIP. 0.5x–1.0x = capped at MAYBE. Only >= 1.0x can reach TAKE.
2. **Volume conviction** — >= 1.5x average adds +1 to score
3. **Choppiness** — fewer than 3 direction flips in last 12 bars = +1, more = -1
4. **Dominant trend protection** — if price is up/down >2% from today's open, counter-trend signals are blocked unless volume >= 2x average

Ratings:
- **TAKE** — score >= 1 with volume floor met
- **MAYBE** — score == 0 or volume between 0.5x–1.0x
- **SKIP** — thin volume, counter-trend without conviction, or choppy

### Scoring lessons learned (2026-04-20 simulation)
- Thin volume signals (< 1.0x avg) are noise — NVDA SELL at 0.5x volume caused a -1.28% loss
- Dominant trend protection correctly held COIN long through a +4.7% day (+3.88%)
- Earliest qualifying entry on a trending day outperforms re-entries
- TSLA counter-trend BUY was luck, not signal quality

## Exercise Shorthands

### EX1 — Buy Only, $1,000
- Starting capital: $1,000
- Buy only, no sells
- Walk through the day chronologically with no future knowledge
- One entry per ticker on first qualifying BUY signal (TAKE or MAYBE, EMA9 crosses above EMA21)
- Allocation: TAKE = $350, MAYBE = $200, never exceed budget
- Hold to end of day
- Log results to `exercises.json` and display in P&L tracker

### EX2 — TBD
Definition forthcoming from user.

## APIs

- **Yahoo Finance** — no API key needed, currently in use
- **Alpha Vantage** — free tier, stocks and crypto, requires free API key
- **Coinbase API** — for crypto data
- **Alpaca** — for future paper/live trading only

## Do NOT build

- Automated trade execution
- Live order placement
- Any connection to real brokerage accounts

## Phase 2 — Paper Trading (Future)

Once signals have been monitored and validated over several weeks:

- Connect to Alpaca paper trading (simulated trades, no real money)
- Test whether signals actually predict price movements
- Only consider live trading after paper trading proves consistent results

## Tech Approach

- Keep dependencies minimal
- Output should be viewable in a browser (HTML dashboard)
- Store data locally for now — no database required initially
- Use Python or JavaScript, whichever is simpler for each task

## Key Reminders

- Always explain what an API key is and where to get one before assuming I have it
- Warn me before making any changes that could cost money or place real trades
- Prefer paper trading and simulation over anything touching real funds
- When in doubt, build the simpler version first and iterate
