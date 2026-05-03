### Preferences
- 

### Completed
- 1-minute OHLC candlestick dashboard (Alpaca IEX feed)
- TAKE/MAYBE/SKIP signal scoring (volume floor, choppiness, dominant trend protection)
- P&L tracker with exercise logging (exercises.json, compounding wallet)
- Ticker tabs, day selector, zoom/pan, collapsible signals
- EX1 — Buy Only, $5,000 mock exercise with full exit logic (stop loss, take profit, trailing stop, time close)
- EX2 — Buy Only with re-entry logic (re-enters after STOP_LOSS or TRAILING_STOP if new signal fires before 13:30)
- Stop loss (-1.5%), Take profit (+3%), Trailing stop (-2.0% from +1% peak), Time close (14:00)
- No-progress exit — flat/negative positions exited at T+90 minutes after entry (if before 14:00)
- Daily loss limit ($75) — no new entries once realized loss hits -$75 for the session
- Concurrent capital tracking — two-phase simulation enforces overlapping positions can't exceed budget
- ATR-based position sizing (14-day lookback, 0.40x–1.50x modifier per ticker)
- 4% opening gap filter — skips ORB on tickers that opened >4% from prior close
- Gap-and-go signal — for positive gaps ≥3%, enters on first close above opening bar's high within first 10 minutes; bypasses ORB; RKLB excluded (0/4 win rate)
- SPY relative strength entry gate — ticker must outperform SPY at the moment of entry
- VWAP entries removed — system is now ORB + GAP_GO only (VWAP had 41% win rate, dragged strategy)
- Win/loss streak adjustment — after 2+ consecutive losing days, MAYBE allocations cut to 50%
- Drawdown cut — if portfolio is >1.5% below rolling 5-day peak, all allocations cut to 50%
- Market state classification (BULL/NEUT/BEAR) via SPY gap + VIXY trend; banner on home panel
- Improvements board with Shipped / Active Logic / Revisit / Not Pursuing sub-tabs
- Graduation criteria tracker panel
- Per-day growth opportunity log — 3 specific, actionable notes per trading day
- Ticker swap: BBAI and NFLX removed (0% win rate over 12 days); KOPN and CRDO added (Apr 28, 2026)
- Ticker swap: CRWD removed (worst performer, -$51.03, 42% win rate over 19 trades); DELL added (May 2, 2026) — DELL tested at 62% win rate, +$109.84 over 30 days in isolation

### LESSONS LEARNED
- **General**: Counter-trend entries are luck, not skill — avoid unless volume strongly confirms
- **EOD pricing**: Always use the official daily close from the Alpaca daily bar, not the last 1-minute bar (can differ significantly). ex1.py and ex2.py fetch this via `TimeFrame.Day` at the start of each run.
- **Re-entries**: Going back into a ticker that just stopped you out is net negative over 12 days — monitoring at 30-day mark before dropping
- **Streak cut paradox**: Cutting MAYBE allocations to 50% shrinks position sizes, which lets more trades fit the budget — can increase total exposure on bad days instead of reducing it. Monitoring at 30 days
- **Trade count cap tested and dropped**: A hard max-trades-per-day cap created a self-reinforcing feedback loop. Removed entirely
- **Morning strength check failed**: SPY direction at open does not correlate with individual stock performance in our sample
- **Concurrent position cap rejected**: Tested caps of 2, 3, 4 — same structural failure as burst cap. Trending days cluster entries AND follow through together; the cap blocks both equally. Net: -$150 over 38 days at cap=3
- **1-bar ORB confirmation rejected**: Looked good on 11 choppy days (+$35) but -$38 over 38 days. The delay hurts on the strategy's biggest trending days (Mar 31, Apr 2, Apr 13)
- **Gap-and-go catches what ORB misses**: Strong gap-up tickers never pull back to the ORB high. The GAP_GO signal added +$204 over 38 backfill days. Apr 24 alone: ARM +7.7% gap hit take-profit in 3 minutes (+$51), SMCI +3.2% take-profit (+$44)
- **BEAR days + all-MAYBE = structural sweep risk**: Apr 28 saw 7 consecutive stop losses. BEAR allocations (10%) kept total damage to $59. The model has no mechanism to observe early session weakness before committing capital
- **Gap-and-go is self-limiting on BEAR days**: The signal requires positive gaps ≥3%, which don't appear on broad selloff days. It adds exposure only when there's genuine upside momentum