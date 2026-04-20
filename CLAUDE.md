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
- `venv/` — Python virtual environment with `yfinance` installed

### How to run
```
venv/bin/python3 run.py
```
Then open `dashboard.html` in your browser.

### Dashboard features
- **Ticker tabs** — one chart at a time (NVDA, TSLA, COIN, SOL-USD)
- **Day dropdown** — filters chart to a specific trading day (last 5 days available)
- **Candlestick view by default** — toggle to Line chart and back
- **Zoom** (scroll wheel) + **pan** (click and drag) + **Reset Zoom** button
- **Signals table** — flags 1-minute candles where price moved >1% or volume spiked >2x average
- **TAKE / MAYBE / SKIP rating** on each signal, scored using trend direction, volume, and choppiness — evaluated with no lookahead (only uses data visible at the moment the signal fires)

### Signal scoring logic
Each signal is rated on three factors:
1. **Trend alignment** — is the signal in the same direction as the last 20 bars?
2. **Volume** — is volume above 1.5x average (confirmed) or below 0.5x (thin/weak)?
3. **Choppiness** — have there been fewer than 3 direction changes in the last 10 bars?

- **TAKE** — all three factors aligned
- **MAYBE** — mixed signals
- **SKIP** — counter-trend or thin volume

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
