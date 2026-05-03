"""
fetch_data.py — pulls 1-minute OHLC data from Alpaca and generates dashboard.html

Run with: python3 fetch_data.py
Then open dashboard.html in your browser.
"""

import json
import os
import pandas as pd
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

TICKERS  = ["NVDA", "TSLA", "AMD", "COIN", "META", "PLTR", "SMCI", "CRDO", "APP", "RIVN", "CRWD", "KOPN",
            "SHOP", "SOFI", "ARM", "DKNG", "RKLB", "RDDT"]
ET       = "America/New_York"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

GROWTH_POOL = [
    ("ORB confirmation bar",
     "Require the bar immediately after the ORB breakout to also close above the ORB high before entering. "
     "Currently the system enters on the first breakout bar — one confirmation bar would filter false breakouts that reverse within minutes."),
    ("ATR-based position sizing",
     "Replace fixed dollar allocations with sizing based on each stock's recent Average True Range. "
     "SMCI moving $2/day should get a larger position than NFLX moving $5/day for the same dollar risk. "
     "Right now volatility differences aren't accounted for in sizing."),
    ("Pre-earnings blackout",
     "Automatically skip any ticker within 5 trading days of its earnings announcement. "
     "NVDA, META, and NFLX all report quarterly — holding through earnings is a binary event that the signal model isn't designed to trade."),
    ("VWAP slope confirmation",
     "Don't just check whether price crosses above VWAP — check whether VWAP itself is trending upward at the crossover. "
     "A flat or declining VWAP crossover on a ranging stock is a significantly weaker signal than one where VWAP is rising."),
    ("Take-profit ladder",
     "Instead of a single 3% take-profit exit, take half the position off at 1.5% and let the rest run with the trailing stop. "
     "This banks partial gains on moves that stall before 3% while still capturing the full run when momentum holds."),
    ("Inter-ticker confirmation",
     "If 4+ tickers all trigger ORB breakouts within 15 minutes of each other, it's a market-wide move, not stock-specific alpha. "
     "In these cases only take the 2 highest-conviction signals rather than filling all slots — crowded entries on market-wide moves tend to reverse together."),
    ("Intraday SPY momentum gate",
     "Before executing any entry after 11 AM, check whether SPY has declined 3 of the last 5 bars. "
     "Late entries into a deteriorating broad market are the primary driver of afternoon stop losses. "
     "A simple SPY trend gate would filter the weakest of these."),
    ("Daily range exhaustion filter",
     "If a ticker has already moved more than 150% of its average daily range by the time the signal fires, skip it. "
     "Entering late into an extended move dramatically increases the probability of hitting the stop loss before any meaningful gain."),
    ("Win/loss streak adjustment",
     "After 2 consecutive losing days, automatically reduce MAYBE-rated allocations by 50% until a winning day resets the counter. "
     "This acts as a dynamic circuit breaker during losing streaks without shutting trading down entirely."),
    ("Volume consistency check",
     "Currently volume is evaluated only at the signal bar. Add a check that the 3 bars prior also showed above-average volume. "
     "A sustained volume build is a far more reliable signal than a single volume spike — which can be a one-off print."),
    ("Ticker-specific risk buckets",
     "SMCI and COIN are 3-5x more volatile than NVDA/META/NFLX yet receive the same allocations. "
     "Consider capping high-volatility tickers at $750 max while allowing blue-chips to receive the full $1,500. "
     "This would equalize actual dollar risk across the portfolio rather than dollar size."),
    ("Tighten trailing stop from 2.5% to 1.5%",
     "The trailing stop is consistently the worst exit type: 7 trades in the last 10 days at 14% win rate, -$59 total. "
     "Most of these fired after the trade moved only 1-2% up, then let it drop 2.5% from that small peak — exiting near or "
     "below the stop loss level with more whipsaw. A tighter 1.5% trail would lock in gains sooner once the lock level is hit "
     "instead of giving back most of the move."),
    ("Intraday stop-loss circuit breaker",
     "On Apr 21 and Apr 23, 2-3 stop losses fired before 11am — signalling a broad market reversal that then "
     "continued to hurt remaining positions. Once 2 stop losses hit on the same day before noon, halt all remaining "
     "ORB entries for the day. The stops are telling you the breakouts are not holding — taking more entries into "
     "that environment compounds the damage."),
]

# Per-day growth opportunity notes — 3 specific insights per trading day based on actual trade review.
# Each entry: "YYYY-MM-DD": [(title1, body1), (title2, body2), (title3, body3)]
PER_DAY_GROWTH = {
    "2026-04-14": [
        (
            "SOFI (1.6x MAYBE, -1.61%) vs META (3.7x MAYBE, +2.44%) — volume ratio within MAYBE entries predicted the outcome",
            "SOFI entered at 09:56 with 1.6x average volume and stopped out at -1.61% (-$14.49). "
            "META entered at 09:47 with 3.7x volume and closed at +2.44% (+$27.74). Both were MAYBE-rated. "
            "The volume ratio within MAYBE entries was the clearest separator: the 1.6x entry lost, the 3.7x entry won by a wide margin. "
            "Volume ratio within MAYBE entries was the clearest predictor of outcome."
        ),
        (
            "SOFI entered after APP's GAP_GO had been flat for 21 minutes — early gap weakness as a MAYBE quality gate",
            "APP (GAP_GO TAKE, 09:35) entered and stalled — it would exit NO_PROGRESS at 11:05 (-$7.82). "
            "SOFI entered at 09:56 as a MAYBE with 1.6x volume, 21 minutes after APP's gap momentum had already stalled. "
            "When the first GAP_GO entry of the day is flat at T+20, morning momentum is weak. "
            "Test: if the day's first GAP_GO position is flat or negative at T+20 minutes, require ≥2.0x volume "
            "(instead of 1.5x) on all subsequent MAYBE ORB entries — filtering marginal entries when gap momentum has already failed."
        ),
        (
            "APP (GAP_GO TAKE, 09:35) stalled and exited NO_PROGRESS at -0.63% — gap momentum disappeared within the first hour",
            "APP entered on a gap-and-go signal at 09:35 and exited NO_PROGRESS at 11:05 (-0.63%, -$7.82). "
            "GAP_GO entries depend on immediate follow-through — the signal fires because of opening momentum, "
            "not intraday fundamentals. If a GAP_GO position isn't positive within 60 minutes of entry, the opening "
            "momentum has already failed. Test: apply a T+60 NO_PROGRESS window specifically for GAP_GO trades "
            "(vs T+90 for ORB) to exit stalled gap entries earlier and free capital for afternoon opportunities."
        ),
    ],
    "2026-04-15": [
        (
            "NVDA triggered the trail lock by barely +1% then reversed — near-breakeven trail exit indicates a false breakout",
            "NVDA exited at -0.93% via trailing stop, meaning it peaked at approximately +1.1% above entry (just clearing "
            "the +1% lock) before reversing 2%+ from that peak. This is a false breakout that cleared the trail lock "
            "by the minimum margin. The current +1% trail lock threshold may be catching brief opening spikes that "
            "don't represent genuine momentum. Consider raising the activation threshold to +1.5% to filter out "
            "single-bar spikes, at the cost of trailing stops triggering slightly later on genuine winners."
        ),
        (
            "Two take-profits (COIN +$22.90, KOPN +$17.29) carried the session despite 6 other positions being flat or stopped",
            "COIN (1.8x, TP +3.07%, +$22.90) and KOPN (1.1x, TP +3.94%, +$17.29) were the only take-profits in 8 entries. "
            "Both were MAYBE-rated with moderate volume — KOPN entered late at 10:24 with just 1.1x volume and still hit take-profit. "
            "With 8 positions splitting the budget, each take-profit's dollar impact was limited. "
            "Test: when a take-profit fires, redirect 25% of its freed capital into the next qualifying signal that session "
            "rather than leaving it idle — compounding the day's momentum into follow-on entries."
        ),
        (
            "APP stopped out first at -1.64% by 10:06 — but COIN hit take-profit 10 minutes later; early stop ≠ broken session",
            "APP (09:47 entry, 10:06 stop-loss at -1.64%) was the session's first exit and its worst result. "
            "COIN entered at 09:50 and hit take-profit at 10:16 — just 10 minutes after APP stopped out. "
            "The daily loss limit ($75) was unaffected by a single $10.37 stop, letting COIN's take-profit land without interference. "
            "One stop loss shouldn't shut the session. The $75 limit is fixed regardless of wallet size — as the wallet compounds "
            "positively, $75 represents a shrinking fraction of capital. Consider scaling the daily loss limit to 1.5% of "
            "the session's starting capital (e.g., $76.50 at $5,100 wallet) so the limit grows proportionally as the strategy proves itself."
        ),
    ],
    "2026-04-16": [
        (
            "Three flat early entries tied up budget while AMD's late breakout (10:46) was the only real trade",
            "META (09:47, NO_PROGRESS -0.13%) and NVDA (10:07, TIME_CLOSE +0.30%) both closed within 0.3% of breakeven. "
            "AMD (10:46, TP +3.06%, +$31.97) and ARM (10:54, TP +3.02%, +$41.47) accounted for nearly all of the day's P&L. "
            "Two early positions consumed budget and delivered nothing while the late entries drove the session. "
            "A no-progress exit rule at T+60 or T+90 would free budget for higher-conviction later signals "
            "rather than holding flat positions all session."
        ),
        (
            "Both take-profits came from late ORB entries (10:46 and 10:54) while the 09:47 early entry flatlined",
            "AMD (10:46, MAYBE 2.8x, TP +3.06%, +$31.97) and ARM (10:54, TAKE 1.7x, TP +3.02%, +$41.47) both entered "
            "after 10:30 and both hit take-profit. META entered at 09:47 and exited NO_PROGRESS at -0.13%. "
            "Late ORB entries may represent stronger confirmation — the stock consolidated above the opening range longer "
            "before breaking out. Test: track early-ORB (pre-10:00) vs late-ORB (after 10:30) take-profit rates "
            "across all 38 backfill days. If late entries outperform, apply a tighter volume floor (≥2.0x) to entries "
            "before 10:00 to reduce low-quality early entries without cutting the day's best trades."
        ),
        (
            "Entry window spanned 67 minutes (09:47 to 10:54) — wider than burst days; individual stock timing drove results",
            "On Apr 16, four entries spread over 67 minutes rather than a tight opening burst. AMD at 10:46 and "
            "ARM at 10:54 entered well after the market had shown its initial direction. Compare to Apr 17 and Apr 21 "
            "where all positions entered within 8 minutes. Wide time spread indicates stock-specific ORB breaks — "
            "narrow burst suggests market-wide noise where everything moves together. Track the per-session entry spread "
            "and test whether wide-spread days (>45 minutes) have higher take-profit rates than burst days (<10 minutes) "
            "across all 38 backfill days."
        ),
    ],
    "2026-04-17": [
        (
            "All 5 positions entered within 90 seconds — capital fully deployed before any directional signal",
            "Every trade fired between 09:45 and 09:46, committing the full session budget in under two minutes on "
            "a day with no clear trend. TSLA (+1.20%) was the lone real mover. When all signals fire simultaneously "
            "at open with similar MAYBE ratings, the model is capturing market-wide noise rather than stock-specific alpha. "
            "The full allocation was committed before any single position could demonstrate whether the session was trending."
        ),
        (
            "NO_PROGRESS exits saved Apr 17 — NVDA, META, and SMCI all cut early; session finished at breakeven instead of significant loss",
            "NVDA (-0.17%, NO_PROGRESS at 11:15), META (-0.43%, NO_PROGRESS at 11:15), and SMCI (-0.73%, NO_PROGRESS at 11:15) "
            "all exited well before 14:00. Without the no-progress rule these three would have held flat or drifted — "
            "any drift to stop loss (-1.5%) would have added $X more loss per position. "
            "The session finished at +$0.01 (essentially breakeven) — a result the no-progress exit made possible."
        ),
        (
            "RIVN hit trailing stop at +0.23% — trail lock fired on a single-bar opening spike, not sustained momentum",
            "RIVN entered at 09:45 and exited trailing stop at +0.23% (+$1.95). For the trail to activate, RIVN peaked "
            "above the +1% lock level then immediately dropped 2.0% from that peak — all on a burst-entry day where "
            "all 5 positions entered within 90 seconds. On burst-entry sessions, the trail lock fires on ORB breakout "
            "bar volatility rather than sustained direction. Test: on days where 4+ entries fire within 10 minutes, "
            "require 2 consecutive closes above entry+1% before arming the trail lock to filter single-bar spikes."
        ),
    ],
    "2026-04-21": [
        (
            "Seven entries in 8 minutes into a broad reversal — no differentiation between signals on the worst day",
            "All 7 positions entered between 09:45 and 09:53 (8-minute window), fully deploying capital before any "
            "trade provided feedback. Every trade was MAYBE-rated with volume ranging 1.2x-2.6x — nearly identical "
            "signal quality across the board. 2 stop losses and 4 trailing stops at or below breakeven. "
            "On a day where all signals fire within minutes at indistinguishable conviction levels, "
            "the model is taking market-wide noise rather than stock-specific breakouts."
        ),
        (
            "Two trailing stops at near-zero (RIVN -0.29%, CRWD +0.04%) and three full stop losses — every exit was a loss",
            "RIVN (09:45, TRAILING_STOP -0.29%) and CRWD (09:50, TRAILING_STOP +0.04%) both peaked above the +1% lock "
            "threshold in a single bar then immediately reversed 2.0%+ from that peak. SHOP (-1.53%), SMCI (-1.62%), "
            "and SOFI (-1.52%) took full stop losses. All 6 positions lost on a broad reversal day. "
            "Test: require 2 consecutive closes above entry+1% before arming the trail lock. On Apr 21, that would have "
            "rerouted both RIVN and CRWD through NO_PROGRESS instead, cutting false momentum exits."
        ),
        (
            "SHOP, SMCI, and SOFI all stopped out after RIVN had already begun reversing — a session-weakness gate could reduce late burst entries",
            "RIVN entered at 09:45 and was already reversing when SHOP (09:48), SMCI (09:52), and SOFI (09:52) all entered. "
            "The three stop losses totaled -$36.68 — nearly the entire -$42.19 session loss. By the time SMCI and SOFI signaled, "
            "RIVN's reversal was visible but the model committed capital anyway. "
            "Test: if 2+ open positions are simultaneously at or below their entry price 20 minutes after entry, "
            "block new ORB entries for 15 minutes — a session-weakness gate that could prevent committing capital "
            "into a clearly reversing open without skipping entries on genuinely trending days."
        ),
    ],
    "2026-04-22": [
        (
            "ARM's delayed breakout (10:17, +3.21%) was the day's best trade — pattern repeating from Apr 20",
            "The opening ORB cluster (09:47-09:52) produced PLTR (+1.59%), RIVN (-1.53% STOP_LOSS), "
            "TSLA (-0.16% NO_PROGRESS). ARM entered at 09:33 via GAP_GO and hit take-profit at +3.16% — the day's best ORB/gap winner. "
            "Three GAP_GO signals (COIN, ARM, KOPN) all fired before 09:36 and all produced positive exits. "
            "ARM's wide opening range means the gap-and-go bar arrives early with strong confirmation — "
            "making it more reliable than mid-morning ORB burst signals."
        ),
        (
            "3 GAP_GO hits vs 8 ORB entries — the gap signals dominated the session while ORB trades diluted budget",
            "Three GAP_GO signals (COIN TAKE, ARM MAYBE, KOPN MAYBE) all produced positive exits before 10:39 "
            "while 8 ORB entries followed. ARM's +3.16% take-profit translated to $12.65 and KOPN's +3.19% to $8.43 "
            "because budget was already spread across 11 positions. "
            "Test: on days where GAP_GO signals fire, prioritize their budget allocation over ORB entries that follow — "
            "funding each GAP_GO position first before distributing the remainder to ORB signals. "
            "On Apr 22, a 25% increase to ARM and KOPN allocations would have added ~$5 without affecting any losing trade."
        ),
        (
            "Only RIVN stopped out on Apr 22 — RIVN's quick stop (09:47 entry, 10:23 exit) was an early session warning",
            "RIVN's -1.53% stop loss at 10:23 was the only full stop-loss exit. The other 10 trades ranged from "
            "APP (-0.97% NO_PROGRESS) to ARM (+3.16% TAKE_PROFIT) — not all winners, but none at stop-loss level. "
            "RIVN stopped 36 minutes after entry while the session was still loading ORB entries. "
            "Test: if any position stops within 45 minutes of entry, pause new ORB entries for 15 minutes — "
            "a quick stop can indicate session-level choppiness before other positions have revealed their direction, "
            "without impacting trending days where stops are rare and entries develop slowly."
        ),
    ],
    "2026-04-23": [
        (
            "META entered at 11:01 after two earlier signals showed weakness — a late entry into a mixed session",
            "NVDA entered at 09:47 and exited NO_PROGRESS at 11:17 (-0.29%). AMD entered at 09:53 and exited "
            "trailing stop at 13:30 (-0.57%). ARM was the exception, hitting take-profit at 10:09 (+3.07%). "
            "META entered at 11:01 — 74 minutes into a session where two of three prior positions were in losing territory — "
            "and exited NO_PROGRESS at 12:31 (-0.25%). Session was profitable only because ARM's $23.21 gain "
            "overwhelmed the three smaller losses."
        ),
        (
            "ARM (+$24.17) carried 6 entries — without it the session loses $22.87; CRDO's late stop was the biggest avoidable loss",
            "ARM (10:09, MAYBE 2.3x, TP +3.07%, +$24.17) was the only profitable trade against 5 losses. "
            "Without ARM the session P&L is -$22.87. CRDO entered at 11:12 — after NVDA and META had already shown NO_PROGRESS "
            "and AMD was trailing — and stopped at -1.54% (-$9.02). "
            "Test: if the session has more losing exits than winning exits at any point after 11:00, block new ORB entries. "
            "Blocking CRDO's 11:12 entry on Apr 23 would have improved session P&L from +$1.30 to +$10.32."
        ),
        (
            "NVDA and META saved by NO_PROGRESS; CRDO's stop was the only full loss — late entries into weak sessions carry more risk",
            "NVDA (-0.29%, NO_PROGRESS at 11:17) and META (-0.25%, NO_PROGRESS at 12:31) were cut before drifting to stop. "
            "KOPN (-0.29%, TRAILING_STOP at 10:40) and AMD (-0.57%, TRAILING_STOP at 13:30) exited at small losses. "
            "CRDO (-1.54%, STOP_LOSS at 12:03) was the only full stop-loss exit — entering 65 minutes after META showed weakness. "
            "Exit logic contained 4 of 5 losses below -0.60%; the one outlier (CRDO) was also the latest entry of the session."
        ),
    ],
    "2026-04-28": [
        (
            "All 6 Apr 28 entries were MAYBE-rated — zero TAKE signals appeared; skipping MAYBE on BEAR days would have saved the full loss",
            "Every entry on Apr 28 (COIN, META, PLTR, SOFI, TSLA, NVDA) was MAYBE-rated with no TAKE signals in the pool. "
            "BEAR state already cut allocations to 10%, but all 6 MAYBE entries still fired and all lost (-$58.88 total). "
            "Test: on BEAR days, skip MAYBE signals entirely and only enter on TAKE-rated signals. "
            "Apr 28 had zero TAKE signals — this rule would have produced zero entries and saved the full $58.88. "
            "Check the 38-day backfill to count TAKE signals on BEAR days and measure the tradeoff: "
            "days saved vs take-profit opportunities missed."
        ),
        (
            "No positive gaps ≥3% on Apr 28 — the inverse pattern (negative gap cluster) could serve as a secondary BEAR signal",
            "Zero GAP_GO signals fired on Apr 28's broad selloff — no ticker gapped up ≥3%. "
            "The inverse was likely visible: multiple tickers opening with gaps below -3%. "
            "Test: count the number of watchlist tickers with negative opening gaps ≥3% each day. "
            "If 5+ tickers open with gaps below -3% (a 'negative gap cluster'), use this as a secondary BEAR confirmation — "
            "triggering the BEAR allocation tier or the MAYBE-skip rule even if VIXY trend hasn't fully confirmed. "
            "This would give the model an opening-bar signal rather than waiting for multi-day VIXY data."
        ),
        (
            "EX2 re-entries recovered only $1.38 on a 6-stop BEAR day — SOFI re-entered but returned just +$1.64",
            "EX2 finished -$57.50 vs EX1's -$58.88 — a gap of only $1.38. SOFI was the only re-entry, "
            "entering after its STOP_LOSS and exiting TIME_CLOSE at +0.37% (+$1.64). "
            "On a BEAR day where all initial entries failed, re-entering the same tickers that just stopped out "
            "produced almost no recovery — the same market conditions that triggered the stop persisted. "
            "Test: disable EX2 re-entries on BEAR market days and check whether EX2 BEAR-day P&L improves. "
            "If BEAR re-entries are consistently near zero or negative, blocking them on BEAR days adds "
            "a second layer of protection alongside BEAR allocation cuts."
        ),
    ],
    "2026-04-29": [
        (
            "Zero TAKE signals on Apr 29 — all 8 entries MAYBE-rated; AMD (+$6.35) masked what would have been an $8.40 loss on 7 losing trades",
            "No TAKE signals appeared in the pool on Apr 29; every entry was MAYBE-rated. AMD (09:54, 2.8x vol, TAKE_PROFIT +3.16%) "
            "was the only winner that mattered — without it the session P&L is -$8.40 on 7 losses. On zero-TAKE days the win rate "
            "dropped to 12.5% (1 of 8). Test: on days with zero TAKE signals, apply a tighter volume floor for MAYBE entries "
            "(e.g., ≥1.5x instead of ≥1.0x) to reduce low-conviction entries. Check the 38-day backfill: what is the average "
            "MAYBE win rate on zero-TAKE days vs days with at least one TAKE signal?"
        ),
        (
            "CRDO hit trailing stop at -0.94% — peaked above +1%, trail armed, then reversed immediately; same single-bar spike pattern as Apr 17 RIVN, Apr 21 RIVN and CRWD",
            "CRDO entered at 09:51 and exited trailing stop at -0.94% (-$1.07). The trail locked on a single spike above +1% "
            "then CRDO dropped more than 2% from that peak within one or two bars. This is now the third occurrence of the "
            "spike-and-reverse trailing stop pattern (Apr 17 RIVN, Apr 21 RIVN and CRWD, now Apr 29 CRDO). "
            "The 2-consecutive-close confirmation test proposed on Apr 21 would have filtered this exit — CRDO would have "
            "continued to NO_PROGRESS instead, saving the full $1.07 loss. This pattern is repeating often enough to warrant a rule change test."
        ),
        (
            "RIVN (10:29) and ARM (10:32) both entered late into a flat session and both exited NO_PROGRESS — four losses had already printed before these entries fired",
            "By 10:29, CRWD, TSLA, and RDDT had all exited NO_PROGRESS and CRDO had trailing-stopped. Four of the first five "
            "entries failed to find momentum. RIVN entered at 10:29 and lost $3.10 (NO_PROGRESS at 11:59); ARM entered at 10:32 "
            "and lost $1.47 (NO_PROGRESS at 12:03) — $4.57 in losses added after the session clearly showed no direction. "
            "Test: if more than half of completed exits are losses at any point after 10:00, block new ORB entries for the rest "
            "of the session. On Apr 29, this rule would have triggered after RDDT's NO_PROGRESS at 11:26 and blocked both RIVN and ARM entirely."
        ),
    ],
    "2026-04-30": [
        (
            "DKNG (TAKE-rated, 11:19 entry) exited NO_PROGRESS at -$11.20 — a TAKE signal entering with only 41 minutes before the 14:00 close got a large allocation and had no time to hit take-profit",
            "DKNG fired a TAKE signal at 11:19 with the second-largest allocation of the session, but with the hard 14:00 exit "
            "there were only 41 minutes for a +3% take-profit to land. It exited NO_PROGRESS at 12:49 (-1.16%, -$11.20) — "
            "the second-biggest loss of the day. TAKE signals get the largest allocations; when a TAKE fires after 11:00, the "
            "time window shrinks drastically and the risk-reward skews negative. Test: block new entries after 11:00 entirely, "
            "or require a higher volume conviction threshold (e.g., ≥2.5x) for entries that late to filter low-probability late allocations."
        ),
        (
            "ARM (TAKE, 09:45, -$9.27 stop) was the session's worst performer while 6 MAYBE entries that followed went 5W/1L — the highest-conviction early signal failed while later MAYBE entries won",
            "ARM received the first and largest early allocation as a TAKE signal but stopped at -1.53% by 09:53. COIN, PLTR, "
            "and RKLB entered one to three minutes later as MAYBE-rated and went 2W/1L. Both take-profits (KOPN and SOFI) "
            "entered after 10:30 as MAYBE signals. Rating alone (TAKE vs MAYBE) did not predict session outcome — later entries "
            "consistently outperformed the early TAKE. This reinforces the late-ORB vs early-ORB pattern from Apr 16: check whether "
            "TAKE signals before 10:00 have worse take-profit rates than TAKE signals after 10:00 across all 38 backfill days."
        ),
        (
            "KOPN exited at +4.77% (+$21.79) but EOD price was roughly 12.5% above entry — the +3% take-profit rule cut the session's biggest winner at a third of its full move",
            "KOPN (10:30 entry at $3.98, exited at $4.17) was the MVP of Apr 30. EOD close was approximately $4.67 — roughly "
            "$0.69 more per share, or ~$16 additional dollars the take-profit left on the table. This is the second time KOPN "
            "has hit take-profit and then continued running strongly (Apr 15 was similar). Consider a tiered exit for KOPN: "
            "if it clears +4%, switch to trailing stop rather than immediately taking profit at +3%, letting the trail ride "
            "the momentum instead of capping the gain at the first threshold."
        ),
    ],
    "2026-05-01": [
        (
            "PLTR's GAP_GO TAKE allocation (~$1,960) produced $30.35 of the $58.26 loss — 52% of the day's damage from one position that reversed immediately",
            "PLTR gapped up and received the largest position of the session as a TAKE-rated GAP_GO. It reversed straight into "
            "stop territory by 10:00 (-1.55%, -$30.35). GAP_GO signals fire on opening momentum; when the gap fades within "
            "the first 30 minutes, the premise has already failed before the stop triggers. Test: apply a T+30 price check "
            "for GAP_GO trades — if the position is below its entry price 30 minutes after entry, exit early rather than "
            "holding to the full -1.5% stop. On May 1, PLTR was likely below entry by 09:40; an early exit at roughly -0.5% "
            "would have saved ~$20 vs the $30.35 full stop."
        ),
        (
            "Three simultaneous stop-losses at 10:00 (PLTR, SMCI, CRWD) signaled a coordinated market selloff — COIN and ARM entered after and added only $0.74 net",
            "All three early positions hit stop in the same minute, indicating broad selling pressure rather than individual "
            "stock weakness. COIN entered at 09:57 while PLTR and SMCI were still open and eventually exited NO_PROGRESS at "
            "-$3.50. ARM entered at 10:07 after all three stops fired and closed TIME_CLOSE at +$4.24. Net from post-triple-stop "
            "entries: +$0.74 — barely meaningful against $58.26 in losses. Test: if 3+ stop-losses fire within a 15-minute "
            "window, block new ORB entries for the rest of the session. On May 1 this would have cut COIN's $3.50 loss while "
            "forfeiting only ARM's $4.24 gain — a $0.74 net cost that substantially reduces tail risk on broad-selloff days."
        ),
        (
            "ARM (+0.69%, +$4.24 TIME_CLOSE) was the sole survivor entering 35 minutes after the triple stop — its gain barely moved the needle on a $58 loss day",
            "ARM entered at 10:07 on a MAYBE ORB and closed at +$4.24, the only positive trade. With the daily loss limit at "
            "-$75 and the session already at -$58.26, the model still had $16.74 of remaining headroom — enough to allow COIN "
            "and ARM entries. But ARM's $4.24 against $58.26 in losses shows that individual wins have diminishing impact once "
            "a session is deeply negative. As the portfolio compounds, a flat $75 daily loss limit represents a shrinking "
            "fraction of capital. Consider scaling the limit to 1.5% of starting capital (e.g., ~$77 now) so the floor grows "
            "proportionally as the strategy proves itself."
        ),
    ],
    "2026-04-27": [
        (
            "PLTR's 8.2x volume spike produced a fading breakout — high volume alone did not confirm follow-through",
            "PLTR entered at 09:45 with 8.2x average volume — the highest single-bar spike in the tracked dataset — "
            "yet exited NO_PROGRESS at -0.24% by 11:15. META at 4.8x similarly delivered only +0.17%. "
            "Four of eight positions finished within 0.30% of breakeven (META +0.17%, PLTR -0.24%, CRWD +0.29%, RDDT -0.42%). "
            "Volume spikes on choppy, low-direction days reflect volatility at the open rather than real buying conviction."
        ),
        (
            "KOPN's GAP_GO trailing stop at 09:38 — just 6 minutes after entry — was an early morning reversal warning",
            "KOPN (GAP_GO TAKE, 09:32) exited trailing stop at 09:38 — 6 minutes after entry. "
            "When a GAP_GO position trails out in the first 10 minutes, morning momentum has immediately reversed. "
            "DKNG (-$14.08) and SOFI (-$14.71) later stopped out on the same choppy session. "
            "Test: if any GAP_GO position exits trailing stop before 09:45, raise the ORB entry threshold for the session "
            "to ≥2.5x volume — blocking marginal MAYBE entries before committing capital into a session "
            "that is already showing immediate reversal at the open."
        ),
        (
            "Zero take-profits on Apr 27 — no position cleared +1% by 11:00; late ORB entries added losses with no upside",
            "All eight exits were TIME_CLOSE (META, CRWD, APP), NO_PROGRESS (PLTR, RDDT), STOP_LOSS (DKNG, SOFI), "
            "or TRAILING_STOP (KOPN). Zero take-profits. No position sustained above +1% all session. "
            "RDDT (10:26 entry, -$3.32 NO_PROGRESS) and APP (10:17 entry, +$0.88 TIME_CLOSE) were added after the "
            "session had already shown no upside momentum. Test: if no open position has cleared +1% by 11:00 AM, "
            "block new ORB entries for the rest of the session — preventing late flat-day entries while preserving "
            "full capital deployment on trending days where take-profits arrive before 11:00."
        ),
    ],
}

# Links each per-day note to its improvement pool index (one entry per note in the list).
# None means the note has no linked pool item and is always visible.
# When an index is in addressed or rejected, that individual note is suppressed.
PER_DAY_GROWTH_IDX = {
    "2026-04-14": [32, 40, None],      # note 1 → low-vol MAYBE filter → rejected | note 2 → GAP_GO flat T+20 gate → rejected
    "2026-04-15": [33, None, None],   # note 1 → trail lock 1.5% → rejected
    "2026-04-16": [26, None, None],   # note 1 → no-progress exit → shipped
    "2026-04-17": [29, None, 43],      # note 1 → entry burst cap → rejected | note 3 → 2-bar trail lock → shipped
    "2026-04-21": [29, 43, 41],        # note 1 → entry burst cap → rejected | note 2 → 2-bar trail lock → shipped | note 3 → T+20 weakness gate → rejected
    "2026-04-22": [35, None, 42],      # note 1 → ARM late-signal pattern → not pursuing | note 3 → quick-stop pause gate → rejected
    "2026-04-23": [30, None, None],   # note 1 → late-session stop gate → rejected
    "2026-04-27": [31, 39, None],      # note 1 → choppiness boost → rejected | note 2 → GAP_GO early trail gate → rejected
    "2026-04-28": [None, None, None],
    "2026-04-29": [None, 43, None],    # note 2 → 2-bar trail lock → shipped
    "2026-04-30": [None, None, None],
    "2026-05-01": [None, None, None],
}

def load_growth_state():
    path = os.path.join(BASE_DIR, "growth_state.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"addressed": [], "rejected": []}


def save_growth_state(state):
    path = os.path.join(BASE_DIR, "growth_state.json")
    with open(path, "w") as f:
        json.dump(state, f, indent=2)




def _load_creds():
    path  = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    creds = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds["ALPACA_API_KEY"], creds["ALPACA_API_SECRET"]


def fetch(ticker, client):
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    bars = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
        start=start, end=end, feed="iex",
    ))
    df = bars.df
    if df.empty:
        print(f"  Warning: no data returned for {ticker}")
        return None
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level=0)
    df = df.tz_convert(ET).between_time("09:30", "15:59")
    if df.empty:
        print(f"  Warning: no market-hours data for {ticker}")
        return None

    timestamps  = [int(d.timestamp() * 1000) for d in df.index]
    opens       = [round(float(v), 2) for v in df["open"].tolist()]
    highs       = [round(float(v), 2) for v in df["high"].tolist()]
    lows        = [round(float(v), 2) for v in df["low"].tolist()]
    closes      = [round(float(v), 2) for v in df["close"].tolist()]
    volumes     = [int(v) for v in df["volume"].tolist()]
    labels      = [str(d) for d in df.index]
    dates       = sorted(set(str(d.date()) for d in df.index))

    ohlc        = [{"x": ts, "o": o, "h": h, "l": l, "c": c}
                   for ts, o, h, l, c in zip(timestamps, opens, highs, lows, closes)]
    volumes_ts  = [{"x": ts, "y": v} for ts, v in zip(timestamps, volumes)]

    # Per-day VWAP (resets at each day's open)
    vwap_ts, cum_tp_vol, cum_vol, prev_date = [], 0, 0, None
    for ts, h, l, c, v, lbl in zip(timestamps, highs, lows, closes, volumes, labels):
        d = lbl[:10]
        if d != prev_date:
            cum_tp_vol, cum_vol, prev_date = 0, 0, d
        tp = (h + l + c) / 3
        cum_tp_vol += tp * v
        cum_vol    += v
        vwap_ts.append({"x": ts, "y": round(cum_tp_vol / cum_vol if cum_vol else c, 2)})

    return {
        "ticker":     ticker,
        "labels":     labels,
        "closes":     closes,
        "volumes":    volumes,
        "ohlc":       ohlc,
        "volumes_ts": volumes_ts,
        "vwap_ts":    vwap_ts,
        "timestamps": timestamps,
        "dates":      dates,
    }


def score_signal(direction, closes_so_far, vol, avg_volume, today_start_idx):
    score     = 0
    vol_ratio = vol / avg_volume if avg_volume else 0

    todays_closes = closes_so_far[today_start_idx:]
    if len(todays_closes) < 2:
        return "SKIP"

    day_open         = todays_closes[0]
    day_change       = (todays_closes[-1] - day_open) / day_open if day_open else 0
    strong_trend     = abs(day_change) > 0.02
    dominant_up      = day_change > 0
    counter_dominant = strong_trend and (
        (direction == "SELL" and dominant_up) or (direction == "BUY" and not dominant_up)
    )
    if counter_dominant and vol_ratio < 2.0:
        return "SKIP"

    if vol_ratio < 1.0:
        if vol_ratio < 0.5:
            return "SKIP"
        return "MAYBE"

    score += 1 if vol_ratio >= 1.5 else 0

    recent = todays_closes[-min(12, len(todays_closes)):]
    flips  = sum(1 for j in range(1, len(recent) - 1)
                 if (recent[-j] - recent[-j-1]) * (recent[-j-1] - recent[-j-2]) < 0)
    score += 1 if flips < 3 else -1

    if score >= 1:
        return "TAKE"
    elif score == 0:
        return "MAYBE"
    else:
        return "SKIP"


def detect_signals(asset, date_filter=None):
    signals     = []
    closes      = asset["closes"]
    volumes     = asset["volumes"]
    labels      = asset["labels"]
    timestamps  = asset["timestamps"]
    avg_volume  = sum(volumes) / len(volumes) if volumes else 1

    today_start_idx = 0
    if date_filter:
        for k, l in enumerate(labels):
            if l.startswith(date_filter):
                today_start_idx = k
                break

    for i in range(1, len(closes)):
        if date_filter and not labels[i].startswith(date_filter):
            continue
        if closes[i - 1] == 0:
            continue
        price_change = abs(closes[i] - closes[i - 1]) / closes[i - 1]
        volume_spike = volumes[i] > avg_volume * 2

        if price_change > 0.01 or volume_spike:
            direction = "UP" if closes[i] > closes[i - 1] else "DOWN"
            reason = []
            if price_change > 0.01:
                reason.append(f"price moved {price_change:.2%} {direction}")
            if volume_spike:
                vol_ratio = volumes[i] / avg_volume if avg_volume else 0
                reason.append(f"{vol_ratio:.1f}x avg volume")
            rating = score_signal(direction, closes[:i+1], volumes[i], avg_volume, today_start_idx)
            signals.append({
                "date":      labels[i],
                "ts":        timestamps[i],
                "price":     closes[i],
                "reason":    ", ".join(reason),
                "direction": direction,
                "rating":    rating,
            })

    return signals


def current_default_date(assets):
    """Default to the last date any asset has data for."""
    all_dates = sorted(set(d for a in assets for d in a["dates"]))
    return all_dates[-1] if all_dates else ""


def build_dashboard(assets):
    all_dates    = sorted(set(d for a in assets for d in a["dates"]))
    default_date = current_default_date(assets)

    _tab_btns = [
        f'<button class="tab{"" if i else " active"}" id="tab-{a["ticker"]}" '
        f'onclick="showTicker(\'{a["ticker"]}\')">{a["ticker"]}</button>'
        for i, a in enumerate(assets)
    ]
    ticker_tabs = (
        f'<div class="tab-row">{"".join(_tab_btns[:10])}</div>'
        f'<div class="tab-row">{"".join(_tab_btns[10:])}</div>'
    )

    date_options = "".join(
        f'<option value="{d}"{"  selected" if d == default_date else ""}>{d}</option>'
        for d in all_dates
    )

    cards     = ""
    charts_js = ""

    for i, asset in enumerate(assets):
        ticker  = asset["ticker"]
        signals = detect_signals(asset, date_filter=default_date)
        signal_rows = "".join(
            f'<tr class="signal-{s["direction"].lower()}">'
            f'<td>{s["date"]}</td><td>{s["direction"]}</td>'
            f'<td><span class="rating rating-{s["rating"].lower()}">{s["rating"]}</span></td>'
            f'<td>{s["reason"]}</td></tr>'
            for s in signals
        ) or "<tr><td colspan='4'>No signals detected</td></tr>"

        display = "block" if i == 0 else "none"
        cards += f"""
        <div class="card" id="card-{ticker}" style="display:{display}">
            <div class="btn-row">
                <button class="toggle-btn active" id="toggle-{ticker}" onclick="toggleChart('{ticker}')">Line</button>
                <button class="reset-btn" onclick="resetZoom('{ticker}')">Reset Zoom</button>
            </div>
            <div class="chart-wrap" id="wrap-{ticker}">
                <canvas id="chart-{ticker}"></canvas>
            </div>
            <h3 class="collapsible" onclick="toggleSection(this)">▶ Signals</h3>
            <div class="collapsible-body" style="display:none">
                <table>
                    <thead><tr><th>Date</th><th>Direction</th><th>Rating</th><th>Reason</th></tr></thead>
                    <tbody id="signals-{ticker}">{signal_rows}</tbody>
                </table>
            </div>
        </div>
        """

        all_signals = detect_signals(asset)
        signal_pts  = [{"x": s["ts"], "y": s["price"],
                         "direction": s["direction"], "rating": s["rating"]}
                        for s in all_signals]

        labels     = json.dumps(asset["labels"])
        closes     = json.dumps(asset["closes"])
        volumes    = json.dumps(asset["volumes"])
        ohlc       = json.dumps(asset["ohlc"])
        volumes_ts = json.dumps(asset["volumes_ts"])
        vwap_ts    = json.dumps(asset["vwap_ts"])
        sig_pts    = json.dumps(signal_pts)

        charts_js += f"""
        chartData['{ticker}'] = {{
            labels:     {labels},
            closes:     {closes},
            volumes:    {volumes},
            ohlc:       {ohlc},
            volumesTs:  {volumes_ts},
            vwapTs:     {vwap_ts},
            signalPts:  {sig_pts}
        }};
        chartMode['{ticker}'] = 'candlestick';
        try {{ charts['{ticker}'] = buildChart('{ticker}', '{default_date}'); }} catch(e) {{ charts['{ticker}'] = null; console.error('Chart build failed ({ticker}):', e); }}
        """

    exercises_path = os.path.join(os.path.dirname(__file__), "exercises.json")
    exercises = []
    if os.path.exists(exercises_path):
        with open(exercises_path) as f:
            exercises = json.load(f)

    def best_prices(ticker, trade_date, entry_time):
        """Return (day_low, post_entry_peak_close) using intraday asset data."""
        asset = next((a for a in assets if a["ticker"] == ticker), None)
        if not asset:
            return None, None
        day_idx = [i for i, l in enumerate(asset["labels"]) if l[:10] == trade_date]
        if not day_idx:
            return None, None
        day_ohlc   = [asset["ohlc"][i] for i in day_idx]
        day_labels = [asset["labels"][i] for i in day_idx]
        day_low    = min(bar["l"] for bar in day_ohlc)
        entry_bar  = next((j for j, l in enumerate(day_labels) if l[11:16] >= entry_time), None)
        post_peak  = max(bar["c"] for bar in day_ohlc[entry_bar:]) if entry_bar is not None else None
        return day_low, post_peak

    def build_ex1_table(ex):
        rows       = ""
        trade_date = ex["date"]
        tol        = 0.02
        for t in ex["trades"]:
            cls = "pnl-win" if t["pnl"] >= 0 else "pnl-loss"
            day_low, post_peak = best_prices(t["ticker"], trade_date, t["time"])
            entry_hl = " best-price" if day_low  and (t["entry"] - day_low)  / day_low  <= tol else ""
            exit_hl  = " best-price" if post_peak and (post_peak - t["exit"]) / post_peak <= tol else ""
            rows += (f'<tr>'
                     f'<td>{t["ticker"]} <span class="trade-num">#{t["trade_num"]}</span></td>'
                     f'<td>{t["time"]}</td><td>{t["signal"]}</td>'
                     f'<td class="{entry_hl}">${t["entry"]:.2f}</td>'
                     f'<td class="{exit_hl}">${t["exit"]:.2f}</td>'
                     f'<td>{t["exit_time"]}</td><td>{t["exit_reason"]}</td>'
                     f'<td class="{cls}">${t["pnl"]:+.2f} ({t["pnl_pct"]:+.2f}%)</td></tr>')
        tc = "pnl-win" if ex["total_pnl"] >= 0 else "pnl-loss"
        return f"""<table>
            <thead><tr><th>Ticker</th><th>Entry</th><th>Signal</th><th>Entry $</th><th>Exit $</th><th>Exit Time</th><th>Reason</th><th>P&L</th></tr></thead>
            <tbody>{rows}</tbody>
            <tfoot><tr class="ex-totals"><td colspan="3">{ex["total_trades"]} trades</td><td colspan="4"></td>
            <td class="{tc}">${ex["total_pnl"]:+.2f} ({ex["total_pnl_pct"]:+.2f}%)</td></tr></tfoot>
            </table>"""


    def build_ex2_table(ex):
        rows = ""
        for t in ex["trades"]:
            cls   = "pnl-win" if t["pnl"] >= 0 else "pnl-loss"
            is_re = t["trade_num"] == 2
            re_tag = ' <span class="re-badge">RE</span>' if is_re else ""
            row_cls = ' class="reentry-row"' if is_re else ""
            rows += (f'<tr{row_cls}>'
                     f'<td>{t["ticker"]}{re_tag}</td>'
                     f'<td>{t["time"]}</td>'
                     f'<td>{t["signal"]}</td>'
                     f'<td>${t["entry"]:.2f}</td>'
                     f'<td>${t["exit"]:.2f}</td>'
                     f'<td>{t["exit_time"]}</td>'
                     f'<td>{t["exit_reason"]}</td>'
                     f'<td class="{cls}">${t["pnl"]:+.2f} ({t["pnl_pct"]:+.2f}%)</td></tr>')
        tc       = "pnl-win" if ex["total_pnl"] >= 0 else "pnl-loss"
        re_count = ex.get("reentry_count", 0)
        re_note  = f" &nbsp;·&nbsp; {re_count} re-entr{'ies' if re_count != 1 else 'y'}" if re_count else ""
        return f"""<table>
            <thead><tr><th>Ticker</th><th>Entry</th><th>Signal</th><th>Entry $</th><th>Exit $</th><th>Exit Time</th><th>Reason</th><th>P&L</th></tr></thead>
            <tbody>{rows}</tbody>
            <tfoot><tr class="ex-totals"><td colspan="3">{ex["total_trades"]} trades{re_note}</td><td colspan="4"></td>
            <td class="{tc}">${ex["total_pnl"]:+.2f} ({ex["total_pnl_pct"]:+.2f}%)</td></tr></tfoot>
            </table>"""

    def generate_daily_notes(ex1, date=None, resolved_idxs=None):
        if not ex1 or not ex1["trades"]:
            return "<p class='notes-text'>No trades executed today.</p>"

        parts = []
        t     = ex1["trades"]
        total = ex1["total_pnl"]

        # --- Entries ---
        orb   = [x for x in t if x["signal"] == "ORB"]
        vwap  = [x for x in t if x["signal"] == "VWAP"]
        takes = [x for x in t if x["rating"] == "TAKE"]
        maybs = [x for x in t if x["rating"] == "MAYBE"]
        n     = len(t)
        first = min(x["time"] for x in t)
        last  = max(x["time"] for x in t)
        re_entries = sum(1 for x in t if x["trade_num"] > 1)

        sig_parts = []
        if orb:  sig_parts.append(f"{len(orb)} ORB")
        if vwap: sig_parts.append(f"{len(vwap)} VWAP")
        conv = f"{len(takes)} TAKE, {len(maybs)} MAYBE" if maybs else f"{len(takes)} TAKE"
        line = (f"Ran {n} trade{'s' if n>1 else ''} ({', '.join(sig_parts)}) — {conv}"
                + (f", including {re_entries} re-entr{'ies' if re_entries!=1 else 'y'}" if re_entries else "")
                + f". First entry at {first}" + (f", last at {last}." if n > 1 else "."))
        late = [x for x in t if x["signal"] == "VWAP" and x["time"] >= "13:00"]
        if late:
            tks = ", ".join(x["ticker"] for x in late)
            line += (f" {tks} {'was a' if len(late)==1 else 'were'} late VWAP "
                     f"entr{'y' if len(late)==1 else 'ies'} after 1pm — "
                     f"late entries rarely capture much of the day's move.")
        parts.append(line)

        # --- Results ---
        wins   = [x for x in t if x["pnl"] > 0]
        losses = [x for x in t if x["pnl"] < 0]
        line   = f"{len(wins)}W / {len(losses)}L for {'+' if total >= 0 else ''}${total:.2f}."
        best   = max(t, key=lambda x: x["pnl_pct"])
        worst  = min(t, key=lambda x: x["pnl_pct"])
        if best["pnl"] > 0:
            commentary = " — strong trending move, entry timing paid off." if best["pnl_pct"] > 3 else "."
            line += f" Best: {best['ticker']} {best['pnl_pct']:+.2f}%{commentary}"
        if worst["pnl"] < 0:
            commentary = " — significant reversal after entry, signal conviction was questionable." if worst["pnl_pct"] < -3 else "."
            line += f" Worst: {worst['ticker']} {worst['pnl_pct']:+.2f}%{commentary}"
        parts.append(line)

        # --- Exit breakdown ---
        tp    = [x for x in t if x.get("exit_reason") == "TAKE_PROFIT"]
        trail = [x for x in t if x.get("exit_reason") == "TRAILING_STOP"]
        sl    = [x for x in t if x.get("exit_reason") == "STOP_LOSS"]
        tc    = [x for x in t if x.get("exit_reason") == "TIME_CLOSE"]
        ep = []
        if tp:    ep.append(f"{len(tp)} take-profit")
        if trail: ep.append(f"{len(trail)} trailing stop")
        if sl:    ep.append(f"{len(sl)} stop-loss")
        if tc:    ep.append(f"{len(tc)} time-close")
        line = f"Exits: {', '.join(ep)}." if ep else "All positions held to time close."
        cut_short = [(x["ticker"], (x.get("eod", x["exit"]) - x["exit"]) / x["entry"] * 100)
                     for x in tp if x.get("eod", x["exit"]) > x["exit"]]
        if cut_short:
            detail = ", ".join(f"{tk} (+{pct:.1f}% more by EOD)" for tk, pct in cut_short)
            line += f" Take-profits exited winners early — {detail}."
        saved = [x["ticker"] for x in trail + sl if x["exit"] > x.get("eod", x["exit"])]
        if saved:
            line += f" Stops protected {', '.join(saved)} from further downside."
        parts.append(line)

        # --- Growth opportunities (up to 3 specific notes per day) ---
        if date and date in PER_DAY_GROWTH:
            notes = PER_DAY_GROWTH[date]
            idxs  = PER_DAY_GROWTH_IDX.get(date, [None] * len(notes))
            visible = []
            for (title, body), idx in zip(notes, idxs):
                already_resolved = resolved_idxs and idx is not None and idx in resolved_idxs
                if not already_resolved:
                    visible.append(f"<li><strong>{title}:</strong> {body}</li>")
            if visible:
                parts.append(
                    f"Growth opportunity: <ul style='margin:6px 0 0 0;padding-left:18px'>"
                    + "".join(visible)
                    + "</ul>"
                )

        return "".join(f"<p class='notes-text'>{p}</p>" for p in parts)

    def cumulative_summary(exs):
        total = round(sum(e["total_pnl"] for e in exs), 2)
        days  = len(exs)
        wins  = sum(1 for e in exs if e["total_pnl"] > 0)
        cls   = "pnl-win" if total >= 0 else "pnl-loss"
        return f'<div class="ex-cumulative">{days} days &nbsp;|&nbsp; {wins}W / {days - wins}L &nbsp;|&nbsp; <span class="{cls}">{total:+.2f} cumulative P&L</span></div>'

    ex1_by_date = {e["date"]: e for e in exercises if "Exercise 1" in e["title"]}
    all_dates   = sorted(ex1_by_date.keys(), reverse=True)

    ex2_by_date = {e["date"]: e for e in exercises if "Exercise 2" in e["title"]}

    growth_state  = load_growth_state()
    addressed_set = set(growth_state.get("addressed", []))

    pnl_section = ""
    if all_dates:
        ex1_list = sorted(ex1_by_date.values(), key=lambda e: e["date"])
        ex2_list = sorted(ex2_by_date.values(), key=lambda e: e["date"])

        # --- Comparison strip ---
        def stat_card(title, exs, color, extra=""):
            n    = len(exs)
            wins = sum(1 for e in exs if e["total_pnl"] > 0)
            tot  = sum(e["total_pnl"] for e in exs)
            wr   = wins / n * 100 if n else 0
            tc   = "#4caf50" if tot >= 0 else "#f44336"
            return (f'<div class="ex-stat-card">'
                    f'<div class="ex-stat-title" style="color:{color}">{title}</div>'
                    f'<div class="ex-stat-pnl" style="color:{tc}">${tot:+,.2f}</div>'
                    f'<div class="ex-stat-meta">{n} days &nbsp;·&nbsp; {wins}W/{n-wins}L &nbsp;·&nbsp; {wr:.1f}% win{extra}</div>'
                    f'</div>')

        ex2_matched = [ex2_by_date[d] for d in all_dates if d in ex2_by_date]
        re_total = sum(t["pnl"] for e in ex2_matched for t in e["trades"] if t["trade_num"] == 2)
        re_extra = f'<br><span class="ex-stat-re">↩ re-entries: ${re_total:+.2f}</span>' if ex2_matched else ""
        comparison_strip = (f'<div class="ex-compare-strip">'
                            f'{stat_card("Exercise 1 — ORB", ex1_list, "#4f8ef7")}'
                            f'{stat_card("Exercise 2 — Re-entry", ex2_matched, "#a78bfa", re_extra)}'
                            f'</div>')

        # --- EX1 day blocks ---
        ex1_blocks = ""
        for i, date in enumerate(all_dates):
            ex1   = ex1_by_date.get(date)
            pnl1  = ex1["total_pnl"] if ex1 else None
            badge = (f'<span class="day-badge {"pnl-win" if pnl1 >= 0 else "pnl-loss"}">{pnl1:+.2f}</span>'
                     if pnl1 is not None else '')
            ex1_block = build_ex1_table(ex1) if ex1 else ""

            rejected_set  = set(growth_state.get("rejected", []))
            resolved_idxs = addressed_set | rejected_set
            notes_html = generate_daily_notes(ex1, date=date, resolved_idxs=resolved_idxs)

            expanded = "block" if i == 0 else "none"
            arrow    = "▼" if i == 0 else "▶"
            ex1_blocks += f"""
            <div class="day-block">
                <div class="day-toggle" onclick="toggleDay(this)">
                    <span class="day-arrow">{arrow}</span>
                    <span class="day-label-text">{date}</span>
                    {badge}
                </div>
                <div class="day-body" style="display:{expanded}">
                    {ex1_block}
                    <div class="notes-section">
                        <div class="notes-header" onclick="toggleNotes(this)">
                            <span class="notes-arrow">▶</span> Claude's Notes
                        </div>
                        <div class="notes-body" style="display:none">{notes_html}</div>
                    </div>
                </div>
            </div>"""

        # --- EX2 day blocks — only dates visible in EX1 ---
        ex2_dates_desc = sorted([d for d in all_dates if d in ex2_by_date], reverse=True)
        ex2_blocks = ""
        for i, date in enumerate(ex2_dates_desc):
            ex2   = ex2_by_date.get(date)
            pnl2  = ex2["total_pnl"] if ex2 else None
            re_c  = ex2.get("reentry_count", 0) if ex2 else 0
            badge = (f'<span class="day-badge {"pnl-win" if pnl2 >= 0 else "pnl-loss"}">{pnl2:+.2f}</span>'
                     if pnl2 is not None else '')
            re_tag = f'<span class="re-day-badge">{re_c} RE</span>' if re_c else ''
            ex2_block = build_ex2_table(ex2) if ex2 else ""
            expanded  = "block" if i == 0 else "none"
            arrow     = "▼" if i == 0 else "▶"
            ex2_blocks += f"""
            <div class="day-block">
                <div class="day-toggle" onclick="toggleDay(this)">
                    <span class="day-arrow">{arrow}</span>
                    <span class="day-label-text">{date}</span>
                    {badge}
                    {re_tag}
                </div>
                <div class="day-body" style="display:{expanded}">
                    {ex2_block}
                </div>
            </div>"""

        # --- Per-ticker stats (EX1) ---
        from collections import defaultdict
        ticker_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        exit_stats   = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
        for e in ex1_list:
            for t in e.get("trades", []):
                tk = t["ticker"]
                er = t.get("exit_reason", "UNKNOWN")
                ticker_stats[tk]["trades"] += 1
                ticker_stats[tk]["pnl"]    += t["pnl"]
                if t["pnl"] > 0:
                    ticker_stats[tk]["wins"] += 1
                exit_stats[er]["count"] += 1
                exit_stats[er]["pnl"]   += t["pnl"]
                if t["pnl"] > 0:
                    exit_stats[er]["wins"] += 1

        def pnl_color(v):
            return "#4caf50" if v > 0 else "#f44336" if v < 0 else "#888"

        # Per-ticker table
        ticker_rows = ""
        for tk, s in sorted(ticker_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            n    = s["trades"]; w = s["wins"]; l = n - w
            tot  = s["pnl"];    avg = tot / n if n else 0
            wr   = w / n * 100 if n else 0
            tc   = pnl_color(tot); ac = pnl_color(avg)
            ticker_rows += (f'<tr><td><strong>{tk}</strong></td><td>{n}</td><td>{w}</td><td>{l}</td>'
                            f'<td>{wr:.0f}%</td>'
                            f'<td style="color:{tc};font-weight:bold">${tot:+.2f}</td>'
                            f'<td style="color:{ac}">${avg:+.2f}</td></tr>')
        ticker_html = f"""
        <div style="max-width:620px">
            <p style="color:#888;font-size:0.83em;margin:0 0 12px">EX1 results · sorted by total P&amp;L</p>
            <table>
                <thead><tr><th>Ticker</th><th>Trades</th><th>Win</th><th>Loss</th><th>Win %</th><th>Total P&amp;L</th><th>Avg P&amp;L</th></tr></thead>
                <tbody>{ticker_rows}</tbody>
            </table>
        </div>"""

        # Exit type table
        EXIT_ORDER = ["TAKE_PROFIT", "TIME_CLOSE", "TRAILING_STOP", "STOP_LOSS"]
        exit_rows = ""
        for er in EXIT_ORDER + [k for k in exit_stats if k not in EXIT_ORDER]:
            if er not in exit_stats:
                continue
            s   = exit_stats[er]
            n   = s["count"]; w = s["wins"]; l = n - w
            tot = s["pnl"];   avg = tot / n if n else 0
            wr  = w / n * 100 if n else 0
            tc  = pnl_color(tot); ac = pnl_color(avg)
            label = er.replace("_", " ").title()
            exit_rows += (f'<tr><td><strong>{label}</strong></td><td>{n}</td><td>{w}</td><td>{l}</td>'
                          f'<td>{wr:.0f}%</td>'
                          f'<td style="color:{tc};font-weight:bold">${tot:+.2f}</td>'
                          f'<td style="color:{ac}">${avg:+.2f}</td></tr>')
        exit_html = f"""
        <div style="max-width:620px">
            <p style="color:#888;font-size:0.83em;margin:0 0 12px">EX1 results · all exits</p>
            <table>
                <thead><tr><th>Exit Type</th><th>Count</th><th>Win</th><th>Loss</th><th>Win %</th><th>Total P&amp;L</th><th>Avg P&amp;L</th></tr></thead>
                <tbody>{exit_rows}</tbody>
            </table>
        </div>"""

        stats_html = f"""
        <div style="display:flex;gap:40px;flex-wrap:wrap;align-items:flex-start">
            <div style="min-width:320px">
                <p style="color:#888;font-size:0.83em;margin:0 0 10px">Per Ticker — EX1</p>
                <table>
                    <thead><tr><th>Ticker</th><th>Trades</th><th>Win</th><th>Loss</th><th>Win %</th><th>Total P&amp;L</th><th>Avg P&amp;L</th></tr></thead>
                    <tbody>{ticker_rows}</tbody>
                </table>
            </div>
            <div style="min-width:320px">
                <p style="color:#888;font-size:0.83em;margin:0 0 10px">Exit Types — EX1</p>
                <table>
                    <thead><tr><th>Exit Type</th><th>Count</th><th>Win</th><th>Loss</th><th>Win %</th><th>Total P&amp;L</th><th>Avg P&amp;L</th></tr></thead>
                    <tbody>{exit_rows}</tbody>
                </table>
            </div>
        </div>"""

        pnl_section = f"""
        <div id="pnl-panel" style="display:none">
            <div style="display:flex;align-items:center;gap:16px;margin:36px 0 12px;border-bottom:1px solid #2a2a4a;padding-bottom:6px">
                <span style="color:#4f8ef7;font-size:1.1em;font-weight:bold">P&amp;L Tracker</span>
                <button class="ex-tab-btn pnl-top-tab active" id="btn-pnl-tracker" onclick="switchPnlTop('tracker')">Tracker</button>
                <button class="ex-tab-btn pnl-top-tab" id="btn-pnl-breakdown" onclick="switchPnlTop('breakdown')">Breakdown</button>
            </div>
            <div id="pnl-tracker-view">
                {comparison_strip}
                <div class="ex-tab-row">
                    <button class="ex-tab-btn active" id="btn-ex1" onclick="switchEx(1)">Exercise 1 — ORB</button>
                    <button class="ex-tab-btn" id="btn-ex2" onclick="switchEx(2)">Exercise 2 — Re-entry</button>
                </div>
                <div id="ex1-panel"><div class="pnl-tracker">{ex1_blocks}</div></div>
                <div id="ex2-panel" style="display:none"><div class="pnl-tracker">{ex2_blocks}</div></div>
            </div>
            <div id="pnl-breakdown-view" style="display:none;padding-top:8px">{stats_html}</div>
        </div>
        """

    # --- Graduation tab ---
    TARGET_DAYS = 30
    ex2_days    = sorted([e for e in exercises if "Exercise 1" in e["title"]], key=lambda e: e["date"])
    n           = len(ex2_days)

    if n:
        day_wins    = [e for e in ex2_days if e["total_pnl"] > 0]
        day_losses  = [e for e in ex2_days if e["total_pnl"] <= 0]
        win_rate    = len(day_wins) / n * 100
        avg_win     = sum(e["total_pnl"] for e in day_wins)  / len(day_wins)  if day_wins  else 0
        avg_loss    = sum(e["total_pnl"] for e in day_losses) / len(day_losses) if day_losses else 0
        worst_day   = min(ex2_days, key=lambda e: e["total_pnl"])
        worst_pnl   = worst_day["total_pnl"]
        worst_pct   = abs(worst_pnl) / 1000 * 100

        # market condition buckets (proxy: trade win % within each day)
        up_days     = sum(1 for e in ex2_days if e["total_pnl"] >= 5)
        down_days   = sum(1 for e in ex2_days if e["total_pnl"] <= -5)
        flat_days   = n - up_days - down_days
        cond_detail = f"{up_days} up &nbsp;/ {down_days} down / {flat_days} flat"
        cond_ok     = up_days >= 5 and down_days >= 5

        days_ok        = n >= TARGET_DAYS
        winrate_ok     = win_rate >= 55 and n >= TARGET_DAYS
        expectancy_ok  = avg_win > abs(avg_loss) if day_losses else avg_win > 0
        maxloss_ok     = worst_pct < 5
        risk_ok        = True

        def crit(label, detail, target, passing, in_progress=False):
            if in_progress:
                dot = f'<span class="grad-dot grad-pending"></span>'
                cls = "grad-card grad-inprogress"
            elif passing:
                dot = f'<span class="grad-dot grad-pass"></span>'
                cls = "grad-card grad-pass-card"
            else:
                dot = f'<span class="grad-dot grad-fail"></span>'
                cls = "grad-card grad-fail-card"
            return (f'<div class="{cls}">{dot}'
                    f'<div class="grad-label">{label}</div>'
                    f'<div class="grad-detail">{detail}</div>'
                    f'<div class="grad-target">Target: {target}</div></div>')

        bar_pct   = round(n / TARGET_DAYS * 100)
        all_ready = days_ok and winrate_ok and expectancy_ok and maxloss_ok and cond_ok and risk_ok
        banner_cls = "grad-banner-ready" if all_ready else "grad-banner-waiting"
        banner_txt = "Ready to Graduate" if all_ready else "Not Yet — Keep Going"
        wr_detail  = f"{win_rate:.1f}%  ({len(day_wins)}W / {len(day_losses)}L over {n} days)"
        ex_detail  = (f"Avg win +${avg_win:.2f} &nbsp;/ avg loss ${avg_loss:.2f}"
                      if day_losses else f"Avg win +${avg_win:.2f} — no losing days yet")
        ex_ratio   = round(avg_win / abs(avg_loss), 2) if avg_loss else "—"

        criteria_html = (
            crit("30 Trading Days",
                 f"{n} / {TARGET_DAYS} days logged",
                 "30+ days", days_ok, in_progress=not days_ok) +
            crit("Win Rate > 55%",
                 wr_detail,
                 "&ge;55% sustained over 30 days", winrate_ok,
                 in_progress=n < TARGET_DAYS) +
            crit("Avg Win > Avg Loss",
                 f"{ex_detail} &nbsp;(ratio {ex_ratio}x)",
                 "Avg win &gt; avg loss", expectancy_ok) +
            crit("Max Daily Loss &lt; 5%",
                 f"Worst day: {worst_day['date']} &nbsp;${worst_pnl:+.2f} ({worst_pct:.1f}% of capital)",
                 "&lt;5% loss on any single day", maxloss_ok) +
            crit("Multiple Market Conditions",
                 cond_detail,
                 "5+ up days, 5+ down days", cond_ok,
                 in_progress=not cond_ok) +
            crit("Risk Management Enforced",
                 "Position sizing, stops, and entry cutoffs enforced every session",
                 "Always followed", risk_ok)
        )

        # --- Readiness notes ---
        days_remaining   = TARGET_DAYS - n
        loss_days_n      = len(day_losses)
        worst_day_str    = f"{worst_day['date']} (${worst_pnl:+.2f})"
        genuine_down     = sum(1 for e in ex2_days if e["total_pnl"] <= -20)

        numbers_items = (
            f"<li><strong>{n} of {TARGET_DAYS} days logged</strong> — "
            f"{days_remaining} more needed before graduation is even on the table.</li>"
            f"<li><strong>Win rate: {win_rate:.1f}%</strong> ({len(day_wins)}W / {loss_days_n}L). "
            f"Looks strong, but a 10-day sample can be misleading — "
            f"one bad week at day 20 could move this significantly.</li>"
            f"<li><strong>Worst day: {worst_day_str}</strong> — "
            f"{'within' if worst_pct < 5 else 'outside'} the 5% daily loss limit "
            f"({worst_pct:.1f}% of starting capital).</li>"
            f"<li><strong>{genuine_down} genuine down day{'s' if genuine_down!=1 else ''}</strong> with "
            f"losses over $20 — need to see consistent performance through more adversity before trusting the model live.</li>"
        )

        concern_items = (
            f"<li><strong>April 21 was a warning.</strong> 10 losses in one day, every MAYBE entry failed. "
            f"The model had no way to detect the day was broken and kept entering. "
            f"That gap cost $75 and could cost far more on a worse market day.</li>"
            f"<li><strong>The two-stop circuit breaker is not in the code yet.</strong> "
            f"The data already proved it matters — it just hasn't been built.</li>"
            f"<li><strong>No daily loss ceiling.</strong> April 23 shows the risk: NVDA stopped out, "
            f"then META was entered anyway and stopped out too. A $30–40 daily ceiling would have prevented that.</li>"
            f"<li><strong>No ORB confirmation bar.</strong> April 24's TSLA stopped out in 11 minutes — "
            f"a 1-bar confirmation rule would have filtered that trade entirely.</li>"
            f"<li><strong>Only 2 genuine loss days in the sample.</strong> "
            f"We need to see how the model performs across 5+ down days before trusting it with real money.</li>"
        )

        need_items = (
            f"<li>Reach <strong>30 days</strong> of logged data ({days_remaining} to go)</li>"
            f"<li>Implement the <strong>two-stop circuit breaker</strong> — halt new entries after 2 stops before 10:30</li>"
            f"<li>Implement the <strong>daily loss ceiling</strong> — no new entries after $30–40 realized loss</li>"
            f"<li>Implement the <strong>1-bar ORB confirmation</strong> — filter false breakouts that reverse in minutes</li>"
            f"<li>See <strong>5+ genuine down days</strong> in the sample with consistent risk management</li>"
            f"<li>Win rate holds <strong>above 55%</strong> over the full 30-day window, not just the first 10</li>"
        )

        verdict = (
            f"The signals work and the core logic is sound. But the risk management layer has known holes "
            f"that the mock data already exposed. Going live with those holes open would be a mistake even "
            f"if the first few real days went well. The good news: we are close. "
            f"{'~' + str(days_remaining) + ' more days' if days_remaining > 0 else 'The day count is met'} "
            f"plus three specific rule additions puts this in a very different position. "
            f"When those are done and the data holds up, graduation is a real conversation."
        )

        readiness_html = f"""
        <div class="grad-readiness">
            <div class="grad-readiness-hdr">Readiness Assessment</div>
            <div class="grad-block">
                <div class="grad-block-title">What the numbers show</div>
                <ul>{numbers_items}</ul>
            </div>
            <div class="grad-block grad-concern">
                <div class="grad-block-title">What concerns me</div>
                <ul>{concern_items}</ul>
            </div>
            <div class="grad-block grad-need">
                <div class="grad-block-title">What needs to happen before going live</div>
                <ul>{need_items}</ul>
            </div>
            <div class="grad-block grad-verdict">
                <div class="grad-block-title">Honest verdict</div>
                <p>{verdict}</p>
            </div>
        </div>
        """

        grad_section = f"""
        <div id="grad-panel" style="display:none">
            <div class="section-header">Graduation Tracker</div>
            <div class="grad-banner {banner_cls}">{banner_txt}</div>
            <div class="grad-progress-wrap">
                <div class="grad-progress-label">Day {n} of {TARGET_DAYS}</div>
                <div class="grad-progress-bar"><div class="grad-progress-fill" style="width:{bar_pct}%"></div></div>
            </div>
            <div class="grad-grid">{criteria_html}</div>
            {readiness_html}
            <div class="grad-note">Based on EX1 (multi-trade). {n} day{'s' if n!=1 else ''} logged.</div>
        </div>
        """
    else:
        grad_section = '<div id="grad-panel" style="display:none"><p style="color:#555;padding:20px">No exercise data yet.</p></div>'

    # --- Improvements panel ---
    # Items removed from GROWTH_POOL but still shown on the board (keyed by archived index)
    ARCHIVED_ITEMS = {
        42: {
            "title":    "Quick-stop pause gate — block new ORB entries for 15 min after any position stops within 45 min of entry",
            "date":     "Apr 28, 2026",
            "original": "RIVN stopped out 36 minutes after entry on Apr 22 while the session was still loading ORB entries. "
                        "A quick stop early in the session may indicate session-level choppiness before other positions have "
                        "revealed their direction. Proposed: if any position stops within 45 minutes of entry, pause new ORB "
                        "entries for 15 minutes to avoid committing capital into a reversing open.",
        },
        41: {
            "title":    "T+20 weakness gate — block new ORB entries for 15 min when 2+ positions are at/below entry price at T+20",
            "date":     "Apr 28, 2026",
            "original": "On Apr 21, SHOP, SMCI, and SOFI all entered after RIVN had already begun reversing — by 09:52 multiple "
                        "open positions were below their entry prices but the model kept committing capital. Proposed: if 2+ open "
                        "positions are simultaneously at or below their entry price 20 minutes after their own entry, block new "
                        "ORB entries for 15 minutes as a session-weakness signal.",
        },
        40: {
            "title":    "GAP_GO flat at T+20 gate — raise MAYBE ORB volume floor to 2.0x when first GAP_GO stalls after 20 min",
            "date":     "Apr 28, 2026",
            "original": "On Apr 14, APP's GAP_GO entered at 09:35 and was flat by 09:55 (T+20). SOFI entered at 09:56 as a "
                        "1.6x MAYBE and stopped out. When the first GAP_GO position is flat/negative at T+20, morning momentum "
                        "is weak — proposed raising the MAYBE ORB volume bar to ≥2.0x (from 1.5x) for subsequent entries "
                        "to filter marginal signals when gap momentum has already failed.",
        },
        39: {
            "title":    "GAP_GO early trail gate — raise ORB volume bar to 2.5x when a GAP_GO exits trailing stop before 09:45",
            "date":     "Apr 28, 2026",
            "original": "KOPN (GAP_GO TAKE) trailed out at 09:38 on Apr 27 — just 6 minutes after entry — on a choppy session "
                        "that produced two more stop losses. When a GAP_GO position exits trailing stop before 09:45, morning "
                        "momentum has immediately reversed. Proposed: use that early trail as a session choppiness flag and "
                        "raise the ORB volume requirement to ≥2.5x for the rest of the day.",
        },
        38: {
            "title":    "Concurrent position cap — limit open positions to N at once before allowing new entries",
            "date":     "Apr 28, 2026",
            "original": "After Apr 17, Apr 21, and Apr 28 all showed the same pattern — full budget deployed before any "
                        "directional feedback — a concurrent cap was proposed. Unlike the burst cap (which counted entries "
                        "fired in a time window), this counts positions currently OPEN and blocks new entries until one exits. "
                        "The idea: on bad days positions linger (stops take 45–90 min), so the cap binds. On good days "
                        "take-profits fire fast, positions cycle, and the cap rarely binds.",
        },
        37: {
            "title":    "Gap-and-go signal — positive gap ≥3% enters on first close above opening bar high in first 10 minutes",
            "date":     "Apr 27, 2026",
            "original": "On Apr 20, COIN rose +5.37% without ever triggering an ORB signal — it gapped up at open and "
                        "never retraced to the opening range high, so the breakout condition was never met. A separate signal "
                        "type for strong-gap tickers that confirms continuation without requiring a pullback would capture "
                        "these moves.",
        },
        36: {
            "title":    "1-bar ORB confirmation (false breakout filter)",
            "date":     "Apr 27, 2026",
            "original": "Require the bar immediately after the ORB breakout to also close above the ORB high before entering. "
                        "Currently the system enters on the first breakout bar — one confirmation bar would filter false breakouts "
                        "that reverse within minutes (PLTR Apr 27, the Apr 21 burst cluster).",
        },
        13: {
            "title":    "Crypto ORB entry window restriction",
            "date":     "Apr 26, 2026",
            "original": "MSTR, MARA, and RIOT have 40-47% win rates vs 62% for stocks in the same period. "
                        "Their price action is choppier and ORB breakouts reverse more often. Consider restricting "
                        "crypto miner entries to the strongest window only (9:45-10:15) rather than the full 11:30 cutoff.",
        },
        14: {
            "title":    "Crypto ticker 30-day performance review",
            "date":     "Apr 26, 2026",
            "original": "MSTR, MARA, RIOT, and ETHA were added on 2026-04-25. After 30 days, run a per-ticker P&L review. "
                        "If any ticker shows a consistently negative avg P&L after 30 days, consider removing it from the pool.",
        },
        20: {
            "title":    "Drawdown-triggered size reduction",
            "date":     "pre-Apr 13, 2026",
            "original": "If the portfolio drops more than 3% from its rolling 5-day peak, cut all new "
                        "position sizes in half until recovery.",
        },
        19: {
            "title":    "Time-of-day win rate analysis",
            "date":     "pre-Apr 13, 2026",
            "original": "After 30 days we'll have enough data to run a proper analysis of which entry "
                        "windows produce the best outcomes.",
        },
        18: {
            "title":    "Relative strength entry gate",
            "date":     "pre-Apr 13, 2026",
            "original": "Before entering any ORB signal, check whether the ticker is outperforming SPY "
                        "on the day at signal time.",
        },
        21: {
            "title":    "Extend TIME_CLOSE to 15:30 on strong days",
            "date":     "Apr 26, 2026",
            "original": "On days where every open position is green by noon and zero stops have fired, "
                        "extend the time cutoff from 14:00 to 15:30 to let winners ride the full session.",
        },
        22: {
            "title":    "Raise entry volume floor to 1.5x",
            "date":     "Apr 26, 2026",
            "original": "On Apr 15, every winner had >1.5x volume and every loser was below 1.5x. "
                        "Raising the entry floor from 1.0x to 1.5x would have filtered the losers "
                        "with no impact on the day's winners.",
        },
        23: {
            "title":    "Morning cooldown — skip entries before 10:00 or 10:30",
            "date":     "Apr 26, 2026",
            "original": "On Apr 16, the two late entries (AMD 10:46, ARM 10:54) were take-profits "
                        "while the earliest entry (META 9:47) was flat. Late entries appeared to show "
                        "better trade quality, suggesting a cooldown before taking ORB signals.",
        },
        24: {
            "title":    "Early stop circuit breaker — halt or halve after 2 stops before cutoff",
            "date":     "Apr 26, 2026",
            "original": "On Apr 20 and Apr 21, multiple stop-outs fired early in the session, suggesting "
                        "breakouts weren't holding. A rule to halt or cut allocations after 2 stops "
                        "before 10:00, 10:30, or 11:00 would protect remaining entries on broken days.",
        },
        25: {
            "title":    "Lower daily loss ceiling from $75 to $30–$40",
            "date":     "Apr 26, 2026",
            "original": "On Apr 23, NVDA and AMD were open for losses at the same time META entered. "
                        "A lower realized-loss threshold ($30–$40) would block new entries earlier "
                        "on days where the first positions are underwater.",
        },
        26: {
            "title":    "No-progress exit — cut flat/negative positions at T+90",
            "date":     "Apr 27, 2026",
            "original": "If a position is flat or negative 90 minutes after entry, exit at the T+90 "
                        "bar price rather than holding to TIME_CLOSE. Frees budget for later signals "
                        "and avoids holding dead positions for hours.",
        },
        27: {
            "title":    "High-vol TAKE promotion — vol >= 2.5x treated as TAKE regardless of choppiness",
            "date":     "Apr 27, 2026",
            "original": "The TAKE scoring requires high volume AND low choppiness, but opening bars are "
                        "almost always choppy — so TAKE almost never fires. Promoting vol >= 2.5x to TAKE "
                        "directly (doubling allocation) would generate more high-conviction entries.",
        },
        28: {
            "title":    "Session stop gate — cut MAYBE allocations 50% after the first stop loss",
            "date":     "Apr 27, 2026",
            "original": "After the first stop loss of the day, reduce all remaining MAYBE allocations "
                        "by 50%. The session has shown weakness — reduce exposure to protect capital "
                        "while still allowing entries to catch recoveries.",
        },
        29: {
            "title":    "Entry burst cap — block new entries if 3+ already fired in last 10 min",
            "date":     "Apr 27, 2026",
            "original": "On Apr 17 and Apr 21, 5–7 positions entered within 90 seconds to 8 minutes, "
                        "committing the full budget before any trade provided directional feedback. "
                        "A burst cap would block the 4th+ entry if 3 have already fired in a 10-minute "
                        "window, reducing exposure when the model is capturing broad market noise rather "
                        "than individual stock alpha.",
        },
        30: {
            "title":    "Late-session stop gate — skip entries ≥ 60 min after open if stop already fired",
            "date":     "Apr 27, 2026",
            "original": "On Apr 23, META entered at 11:01 (74 min after open) after NVDA had already "
                        "stopped out at 09:47. Taking a full-allocation late entry into a session that "
                        "has already shown weakness compounds the damage. A rule blocking new entries "
                        "≥ 60 minutes after open (≥ 10:30) when a stop loss has already fired would "
                        "prevent late re-engagement on broken sessions.",
        },
        31: {
            "title":    "Choppiness boost — high vol + high chop → SKIP instead of MAYBE",
            "date":     "Apr 27, 2026",
            "original": "On Apr 27, PLTR triggered with 8.2x volume but faded immediately — the volume "
                        "spike reflected open volatility, not buying conviction. The scoring already "
                        "penalises choppiness (-1) but high volume (+1) cancels it out, yielding MAYBE. "
                        "Upgrading the choppiness penalty so that high vol + high chop → SKIP would "
                        "prevent entering on volume-driven false starts at the open.",
        },
        32: {
            "title":    "Low-conviction MAYBE filter — skip or halve entries with vol < 2.0x",
            "date":     "Apr 27, 2026",
            "original": "On Apr 14, both losses came from MAYBE entries with volume 1.0–2.0x (SOFI 1.6x, "
                        "PLTR 1.9x) while the only strong winner was META at 3.7x. The pattern suggested "
                        "that low-volume MAYBE signals (1.0–2.0x) are the primary loss source on flat/mixed "
                        "days, and that skipping or halving those entries would improve P&L.",
        },
        33: {
            "title":    "Raise trail lock threshold from 1.0% to 1.5%",
            "date":     "Apr 27, 2026",
            "original": "On Apr 15, NVDA and BBAI both peaked at barely above +1.0% (just clearing the "
                        "trail lock) before reversing sharply. A higher lock threshold of +1.5% would "
                        "require a more sustained move before the trailing stop activates, filtering out "
                        "stocks that barely tick above the lock and immediately reverse near breakeven.",
        },
        34: {
            "title":    "COIN gap-and-go detection — enter on gap-up tickers that never pull back to ORB high",
            "date":     "Apr 27, 2026",
            "original": "On Apr 20, COIN rose +5.37% without ever triggering an ORB signal — it gapped "
                        "up at open and never retraced to the opening range high, so the breakout condition "
                        "was never met. This is a structural gap: the ORB framework requires a retest of "
                        "the range high that doesn't occur on true gap-and-go moves. A separate signal type "
                        "for strong-gap tickers that confirms continuation without requiring a pullback "
                        "would capture these moves. Observed again on Apr 22 (ARM late-signal pattern).",
        },
        35: {
            "title":    "ARM late-signal pattern — ARM's wide ORB range produces later, higher-quality breaks",
            "date":     "Apr 27, 2026",
            "original": "On Apr 20 (11:19) and Apr 22 (10:17), ARM's ORB trigger arrived well after the "
                        "opening cluster and delivered the day's best return both times. ARM's opening range "
                        "is wide relative to its ATR, so the breakout bar comes later and with more "
                        "confirmation behind it. Only 2 data points — not enough to generalize into a rule.",
        },
    }

    GROWTH_RESOLUTIONS = {
        37: {
            "what":   "Implemented as a new GAP_GO signal type for tickers gapping ≥3%",
            "date":   "Apr 27, 2026",
            "detail": "For tickers that gap up ≥3% from prior close, the system now scans the first 10 minutes (09:30–09:39) "
                      "for the first bar that closes above the opening bar's high. This bypasses the existing ORB framework "
                      "and the 4% gap filter entirely — it is triggered by the gap itself. Volume floor of 1.0x still required; "
                      "TAKE rating at ≥1.5x. SPY relative strength gate still applied. RKLB is excluded (0 for 4 in testing — "
                      "gap-and-go signals on RKLB did not hold). The signal fires alongside ORB on non-gap days. On gap days, "
                      "gap-and-go replaces ORB entirely for that ticker.",
            "impact": "Backfill over 38 days: +$412.66 final portfolio vs +$196.84 baseline (ORB-only) — +$215.82 improvement. "
                      "Gap-and-go contributed +$204.13 across 13 firing days. Biggest wins: Mar 25 +$82.71 (AMD, ARM, SOFI gap cluster), "
                      "Apr 24 +$100.67 (ARM take-profit +3.21%, AMD trailing stop, SMCI take-profit +3.21%). "
                      "Biggest loss: Apr 6 -$38.68 (NFLX stop loss on gap-open). Win/loss: 19W/19L (vs 20W/18L baseline — "
                      "gap trades add large winners at the cost of a slightly higher stop rate).",
        },
        1: {
            "what":   "Implemented as 14-day ATR-based allocation scaling",
            "date":   "pre-Apr 13, 2026",
            "detail": "Each ticker's allocation is multiplied by (median_ATR / ticker_ATR), clamped between 0.40x and 1.50x. "
                      "Stable tickers (NFLX, META, NVDA ~3% ATR) get up to 1.375x their base allocation. "
                      "Volatile tickers (COIN ~6.4% ATR, SMCI ~5.7% ATR) get 0.61x–0.70x. "
                      "The modifier is recalculated fresh each trading day from the prior 14 daily bars, "
                      "so it adapts automatically when a ticker's volatility regime changes.",
            "impact": "Backfill over 39 days: +$141.67 vs gap-filter baseline +$124.77 — +$16.90 improvement. "
                      "Win/loss record improved from 19W/20L to 21W/18L.",
        },
        2: {
            "what":   "Implemented as a 4% opening gap filter",
            "date":   "pre-Apr 13, 2026",
            "detail": "If a ticker's first-minute close is more than 4% away from the prior day's close, "
                      "the ORB signal is skipped for that ticker. VWAP crosses are still allowed — they fire "
                      "later in the day once the stock has settled after the gap. The 3% threshold was too "
                      "aggressive (filtered good momentum days); 5% missed too many dangerous gaps. 4% was the sweet spot.",
            "impact": "Backfill over 39 days: +$124.77 vs baseline +$62.02 — a 101% improvement with no change to win/loss record.",
        },
        8: {
            "what":   "Implemented as a 50% MAYBE allocation cut after 2+ consecutive losing days",
            "date":   "pre-Apr 13, 2026",
            "detail": "At the start of each day, ex1.py checks the prior results in backfill.json. "
                      "If 2 or more consecutive days were losses, MAYBE-rated entries get half their "
                      "normal allocation for that day. A winning day resets the counter. "
                      "TAKE-rated entries are never reduced — only MAYBE, which carries lower conviction.",
            "impact": "Backfill over 38 days: +$258.38 vs previous +$229.34 — +$29.04 improvement. "
                      "Win/loss record unchanged at 20W/18L. Reduction was active on 11 days, "
                      "primarily during the Mar 11–19 and Mar 23–27 losing streaks.",
        },
        20: {
            "what":   "Implemented as a 1.5% drawdown gate with 50% size reduction",
            "date":   "pre-Apr 13, 2026",
            "detail": "At the start of each day, ex1.py builds a running portfolio value from all prior "
                      "backfill results and checks the rolling 5-day peak. If the current portfolio is "
                      "more than 1.5% below that peak, all position allocations are cut to 50% for the day. "
                      "A 3% threshold was tested first but never fired — losses in this strategy come in "
                      "slow clusters, so the rolling peak slides down with the portfolio. 1.5% was the "
                      "tightest threshold that triggered on actual losing streaks without being oversensitive.",
            "impact": "Backfill over 38 days: +$283.34 vs previous +$267.68 — +$15.66 improvement. "
                      "Win/loss record unchanged at 20W/18L. Gate fired on 2 days (Mar 16–17) during "
                      "the Mar 11–19 losing streak.",
        },
        3: {
            "what":   "VWAP entries removed entirely — system is now ORB-only",
            "date":   "pre-Apr 13, 2026",
            "detail": "Testing showed VWAP entries had a 41% win rate and -$1.29 average P&L per trade, "
                      "dragging the strategy down by ~$88 over 39 days. A VWAP slope filter was also tested "
                      "but produced nearly identical results to removing VWAP completely, meaning the slope "
                      "wasn't the problem — the signal itself is too weak on these tickers. "
                      "VWAP entries tended to fire on choppy, directionless days where the stock crosses "
                      "VWAP by noise rather than real momentum.",
            "impact": "Backfill over 38 days: +$229.34 vs previous +$141.67 — +$87.67 improvement. "
                      "Win/loss record: 20W/18L.",
        },
        18: {
            "what":   "Implemented as a relative strength entry gate vs SPY",
            "date":   "pre-Apr 13, 2026",
            "detail": "At the ORB entry bar, the system now compares the ticker's % change from its day open "
                      "to the entry price against SPY's % change from its day open to the same moment. "
                      "If the ticker is not outperforming SPY at the time of entry, the trade is skipped. "
                      "SPY intraday data is fetched once per day before the ticker loop and cached as a "
                      "time-keyed lookup. This filters out breakouts that are just riding broad market momentum "
                      "rather than showing genuine relative strength.",
            "impact": "Backfill over 38 days: +$267.68 vs previous +$258.38 — +$9.30 improvement. "
                      "Win/loss record unchanged at 20W/18L.",
        },
        26: {
            "what":   "No-progress exit — flat/negative positions exited at T+90",
            "date":   "Apr 27, 2026",
            "detail": "If a position is still open 90 minutes after entry and the price is at or below the "
                      "entry price, the position exits immediately at the T+90 bar close rather than holding "
                      "to TIME_CLOSE. Only applies when T+90 is on or before 14:00. The rule fires once per "
                      "position — if price has already risen above entry at T+90, the position holds normally. "
                      "Today (Apr 27) META, PLTR, NFLX, CRWD all sat within 0.3% of entry for 4+ hours and "
                      "would have been released by mid-morning under this rule.",
            "impact": "Backtest over 11 EX1 days: +$253.72 vs +$220.95 baseline — +$32.77 improvement. "
                      "Rule fired on 10 trades across 7 days. Biggest saves: Apr 23 +$31 (NVDA and META "
                      "exited near breakeven instead of at full stop losses), Apr 15 +$14 (AMD cut early). "
                      "One notable hurt: Apr 20 -$21 (CRWD was flat at T+90 but recovered to close positive). "
                      "Net positive across the sample — implemented in ex1.py and ex2.py.",
        },
        11: {
            "what":   "Tightened trailing stop from 2.5% to 2.0%",
            "date":   "Apr 26, 2026",
            "detail": "The trailing stop trails 2.0% below the peak price once the lock level (+1%) is hit, "
                      "down from 2.5%. At 2.5%, a trade peaking at +1.5% could exit at -1.0% — worse than "
                      "the stop loss. At 2.0%, the same trade exits at -0.5%, and a trade peaking at +2.0% "
                      "exits at breakeven instead of -0.5%. Tested 1.5% as well — too tight, destroyed "
                      "6 trades including DKNG Apr 24 (+$25.59 → +$0.06). 2.0% improved 11 trades, hurt 0.",
            "impact": "Backtest over 10 EX1 days: +$237.12 vs +$196.38 baseline — +$40.74 improvement. "
                      "All 11 affected trades improved; trailing stop exits cut smaller losses and "
                      "converted two losing trailing stops into gains (RIVN Apr 17, BBAI Apr 21).",
        },
    }

    def _date_sort_key(d):
        if not d: return 0
        try:
            from datetime import datetime
            return int(datetime.strptime(d, "%b %d, %Y").strftime("%Y%m%d"))
        except ValueError:
            pass
        try:
            from datetime import datetime
            return int(datetime.strptime(d, "%b %Y").strftime("%Y%m")) * 100
        except ValueError:
            return 0

    GROWTH_REJECTIONS = {
        0:  {"reason": "Tested across 167 ORB entries over 39 days. 23% of entries had bar+1 dip briefly below the ORB high, "
                       "but most of those recovered and ended as winners (would have been missed). The entry price penalty "
                       "on confirmed trades cost $257 — more than the $171 saved from filtering false breakouts. "
                       "Net: -$86 worse. Our ORB losses come from macro reversals an hour later, not immediate fake-outs.",
             "date": "pre-Apr 13, 2026"},
        6:  {"reason": "Tested across all 39 days alongside the current gap filter + ATR sizing strategy. The gate fired on "
                       "12 days but was net neutral (+$0.92 total impact). It saved losses on some down days (Mar 17 +$15, "
                       "Mar 20 +$16) but blocked winners on others (Apr 9 cost $27). Most critically, our two worst loss "
                       "days — Apr 21 and Apr 23 — were completely unaffected because entries happened before 11 AM. "
                       "Win rate dropped from 21W/18L to 18W/21L. Losses come from post-entry reversals, not late entries.",
             "date": "pre-Apr 13, 2026"},
        4:  {"reason": "Tested across 38 days. The ladder banked partial gains on stalling moves (+$10 on Apr 23, +$7.54 "
                       "on Apr 1) but directly cut into our strongest trending days — the days that carry the strategy. "
                       "Mar 31 lost $6, Apr 2 lost $5, Apr 24 lost $15. The problem is that our best days are clean "
                       "runs to +3%, and halving the position at +1.5% caps exactly those. Net: -$31.58 worse over 38 "
                       "days with no improvement to win rate. Strong trends need full allocation to pay for the losses.",
             "date": "pre-Apr 13, 2026"},
        5:  {"reason": "Tested across 38 days. The filter fired on 14 days and was -$169.86 worse — not close. The premise "
                       "was backwards: when 4+ tickers break ORB together, that's a genuine broad-market up move and these "
                       "high-beta stocks run hardest on exactly those days. Mar 31 went from +$82 to -$9, Apr 2 from +$98 "
                       "to +$36, Apr 24 from +$57 to +$6. Crowded ORB days are our best days, not our riskiest ones.",
             "date": "pre-Apr 13, 2026"},
        7:  {"reason": "Tested across 38 days — the filter never fired once. ORB entries happen before 11:30 AM, within "
                       "the first 90 minutes of trading. The intraday range at that point almost never exceeds 150% of "
                       "the average daily range. This filter was implicitly designed for late-day VWAP entries, which "
                       "have already been removed. Not applicable to the current ORB-only strategy.",
             "date": "pre-Apr 13, 2026"},
        9:  {"reason": "Tested across 38 days. -$33.93 worse and fires on nearly every day. ORB breakouts by definition "
                       "happen when volume surges suddenly on the breakout bar — the prior bars are quiet because nothing "
                       "has happened yet. Requiring 2 of 3 prior bars above average contradicts how ORB signals work. "
                       "Saved big on Apr 21 (+$41) and Mar 17 (+$20) but cost Apr 13 (-$33), Apr 16 (-$36), Mar 4 (-$20). "
                       "Win rate improved by 1 day but total P&L dropped significantly.",
             "date": "pre-Apr 13, 2026"},
        10: {"reason": "Tested across 38 days. -$16.90 worse with no win rate change. ATR-based position sizing already "
                       "does this job better — it scales allocations dynamically based on each ticker's recent volatility "
                       "rather than applying a fixed cap. Hard caps hurt on winning days when a high-vol ticker happened "
                       "to be well-behaved (Apr 2 -$10, Apr 14 -$11, Mar 4 -$7). A fixed $750 ceiling treats COIN "
                       "identically whether it's calm or wild; ATR sizing adapts. Redundant given existing logic.",
             "date": "pre-Apr 13, 2026"},
        12: {"reason": "Tested across all 11 EX1 days. Rule fires only once (Apr 21) and blocks a single "
                       "trailing stop exit (PLTR -$4.28). Net effect: +$4.28 over 11 days — statistical noise. "
                       "The structural problem: on the worst days (Apr 21), all 7 entries fire within 8 minutes "
                       "before any stop has time to register. The two stop losses that trigger the circuit "
                       "(SHOP 09:48, DKNG 09:51) happen after all entries are already committed. The circuit "
                       "cannot observe stops before the session is fully deployed. Identical finding to the "
                       "archived variant (index 24) tested at multiple time cutoffs.",
             "date": "Apr 27, 2026"},
        13: {"reason": "Not applicable — MSTR, MARA, and RIOT were never added to the ticker pool. "
                       "Crypto-adjacent tickers were considered and explicitly declined due to additional "
                       "tax filing requirements. No data to analyze.",
             "date": "Apr 27, 2026"},
        14: {"reason": "Not applicable — MSTR, MARA, RIOT, and ETHA were never added to the ticker pool. "
                       "Crypto-adjacent tickers were considered and explicitly declined due to additional "
                       "tax filing requirements. No data to analyze.",
             "date": "Apr 27, 2026"},
        19: {"reason": "Analysis run across 157 trades. No time window was weak enough to justify filtering — the "
                       "9:45–10:00 window dominates (96 trades, 50% win) and later windows have too few trades for "
                       "statistical confidence. Two follow-on tests were also rejected: (1) raising TRAIL_LOCK from 1% "
                       "to 2% was -$9.44 worse — the trailing stop at 1% is correctly catching reversals near the stop "
                       "loss level; (2) lowering the TAKE score threshold from ≥2 to ≥1 just doubles all allocations "
                       "rather than improving signal quality, adding risk without selectivity. Time close (65% win, "
                       "+$3.60 avg) and the current exit logic are working well.",
             "date": "pre-Apr 13, 2026"},
        21: {"reason": "Analyzed on Apr 13 — the best day of the 10-day run (7 trades, 7 green). Holding the 4 "
                       "TIME_CLOSE positions to EOD would have added only +$5.75 total. More importantly, PLTR reversed "
                       "after 14:00 and would have cost -$5.82 — offsetting most of the upside from the other three. "
                       "On a perfect day, extending the cutoff barely moves the needle. The protection it provides on "
                       "bad days (stopping further entries, locking in gains before afternoon reversals) is worth more.",
             "date": "Apr 26, 2026"},
        22: {"reason": "Tested across all 10 days — net -$74.83. Only 5 trades were skipped: 2 losers worth -$2.10 "
                       "avoided, but 3 winners worth +$76.93 cut. Apr 13 SOFI (1.20x, +$27.32 take-profit), "
                       "Apr 24 META (1.00x, +$24.02) and DKNG (1.40x, +$25.59 take-profit) would all have been "
                       "blocked. The Apr 15 observation was one-day pattern matching — it doesn't hold across the full "
                       "sample. Sub-1.5x volume entries include some of the strategy's best trades.",
             "date": "Apr 26, 2026"},
        23: {"reason": "Tested 10:00 and 10:30 cutoffs across all 10 days. 10:00: net -$78.51 (20 winners missed "
                       "+$289 vs 27 losers avoided -$210). 10:30: net -$139.87 (24 winners missed +$358 vs 28 losers "
                       "avoided -$218). Both cutoffs gutted the two best days — Apr 13 would have dropped from "
                       "+$109.77 to +$18.76, Apr 24 from +$86.77 to +$34.54. The strongest ORB signals fire in the "
                       "first 30 minutes on trending days — those are exactly the trades the strategy is built on.",
             "date": "Apr 26, 2026"},
        24: {"reason": "Tested 6 variants (10:00/10:30/11:00 cutoff × halt/half) across all 10 EX1 days. "
                       "The rule never triggers at 10:00 or 10:30 — all stop exits on Apr 20 and Apr 21 "
                       "happened after 10:17 at the earliest. At 11:00 cutoff it triggers once (Apr 20) "
                       "and blocks ARM — the one winner after the stop cluster — costing -$17.19. "
                       "On Apr 21 (the worst day, -$52.21) the rule never triggers because all 7 entries "
                       "fired in an 8-minute window (09:45–09:53), and stops didn't fire until 10:55+. "
                       "The circuit can't observe stops before all entries are already in.",
             "date": "Apr 26, 2026"},
        25: {"reason": "Tested $30, $40, $50, and $75 realized-loss ceilings across all 10 EX1 days. "
                       "The rule never triggers at any threshold. EX1 entries cluster in the morning "
                       "(09:45–11:01) while exits happen much later in the day — no significant realized "
                       "loss accumulates before the last entry fires. The Apr 23 premise was factually "
                       "incorrect: NVDA was still open (not yet stopped) when META entered at 11:01. "
                       "The current $75 ceiling is already well above the largest realized loss any "
                       "single trade produces before the next entry.",
             "date": "Apr 26, 2026"},
        28: {"reason": "Tested across 11 EX1 days: net -$56.05 vs baseline. The gate fires after the first "
                       "stop loss of the day and halves allocation on everything that follows — including the "
                       "day's best recovery trades. On Apr 22, ARM hit take-profit at +$24.74 after RIVN stopped "
                       "out; the gate would have cut ARM to +$12.37. On Apr 24, DKNG (+$25.79) and NVDA (+$34.82) "
                       "were both halved after TSLA's early stop loss. The rule systematically penalises the trades "
                       "most likely to recover a damaged session. A stop loss early in the day is not a reliable "
                       "predictor that later signals will also fail.",
             "date": "Apr 27, 2026"},
        29: {"reason": "Tested across 11 tracked days and 38 backfill days: net -$38 and -$115 respectively. "
                       "The rule correctly blocks losers on bad days (Apr 21 +$19, Apr 20 +$14) but destroys "
                       "value on trending days when the whole cluster follows through. Apr 2 alone blocked 4 "
                       "take-profits for -$92 on a single day. The burst itself is not the problem — market "
                       "direction is. On strong trending days, the entire ORB universe breaks simultaneously "
                       "because stocks are genuinely moving together, and those are exactly the sessions the "
                       "strategy is designed to capture.",
             "date": "Apr 27, 2026"},
        30: {"reason": "Tested across 11 tracked days and 38 backfill days: net -$49 and -$77 respectively. "
                       "Late entries after stop losses are often on sessions that have turned around — blocking "
                       "them removes recovery trades more than it avoids further damage. Apr 24 NVDA entered "
                       "at 10:34 and hit take-profit for +$35; Mar 3 blocked four winners including COIN "
                       "take-profit. The Apr 23 case (META 11:01 loss) is the exception, not the rule. "
                       "A stop loss early in the session is not a reliable signal that later entries will fail "
                       "— the data shows late entries after stops win more often than they lose.",
             "date": "Apr 27, 2026"},
        31: {"reason": "Tested across 11 tracked days and 38 backfill days: net -$53 and -$99 respectively. "
                       "The filter is structurally equivalent to 'skip all high-volume MAYBE trades': if vol "
                       "≥ 1.5x and chop is low, the signal already scores as TAKE and is unaffected; only "
                       "high-vol + high-chop signals are dropped, which is every MAYBE above 1.5x. This "
                       "blocks winners as readily as losers — Apr 24 NVDA (+$35 take-profit), Apr 16 AMD "
                       "(+$31 take-profit), Apr 14 META (+$26) all dropped. The no-progress exit already "
                       "handles the underlying problem: high-vol faders like Apr 27 PLTR exit at T+90 "
                       "(-$1.73) instead of holding to close. Prevention via scoring is too broad; "
                       "exit-based cleanup is the cleaner solution.",
             "date": "Apr 27, 2026"},
        32: {"reason": "Tested skip and half-allocation variants across 11 tracked and 38 backfill days. "
                       "Skip: -$118 / -$281. Half-alloc: -$59 / -$140. Both rejected clearly. "
                       "The 1.0–2.0x MAYBE bucket is not the dead weight Apr 14 suggested — across 38 days "
                       "it includes SOFI +$27 take-profit, ARM +$19, COIN +$22, DKNG +$26, NVDA +$35, AMD +$28. "
                       "The 1.5–2.0x tier has a 43% win rate and +$235 total P&L across 91 trades. "
                       "Apr 14 was one-day pattern matching. Low-vol MAYBE signals are not systematically "
                       "unprofitable — they are mixed, and the winners in that bucket are too large to cut.",
             "date": "Apr 27, 2026"},
        33: {"reason": "Tested across 11 tracked days (-$1) and 38 backfill days (-$55). The Apr 15 "
                       "observation was backwards: the 1% trail lock IS catching reversals earlier, not later. "
                       "With the 1.5% lock, NVDA held through a reversal to TIME_CLOSE(-$13) instead of "
                       "exiting via TRAILING_STOP(-$11) — the higher lock caused more damage on the exact "
                       "cases it was meant to protect. BBAI went from TRAILING_STOP(-$6) to STOP_LOSS(-$9). "
                       "On 38 backfill days, RKLB, RIVN, ARM, BBAI all converted from TRAILING_STOP to "
                       "harder STOP_LOSS hits. The 1% lock exits sooner on reversals, which is the right "
                       "behavior — the issue was misdiagnosed from one day's data.",
             "date": "Apr 27, 2026"},
        35: {"reason": "Only 2 data points (Apr 20 at 11:19, Apr 22 at 10:17). Not enough to generalize "
                       "ARM's wide-range ORB pattern into a rule. The model already allows entries until "
                       "11:30 — ARM is participating correctly. A ticker-specific ATR or ORB-width "
                       "adjustment would add complexity for a single-ticker observation. "
                       "Will revisit if the pattern continues to appear over 30 days.",
             "date": "Apr 27, 2026"},
        36: {"reason": "Tested across 11 tracked days (+$35.48 apparent gain) and 38 backfill days (-$37.90 net loss). "
                       "The 11-day result was misleading — it covered mostly choppy April days where confirmation helped. "
                       "The full backfill exposed the structural problem: the 1-minute delay cuts into the model's biggest "
                       "earning days. Mar 31 lost -$47.85, Apr 2 lost -$62.90, Apr 13 lost -$21.63 — three of the four "
                       "strongest days in the dataset. On genuine trending days the breakout bar fires and the move is "
                       "already underway; entering 1 bar later means entering at a higher price, reducing upside, and on "
                       "some trades missing take-profit entirely. The strategy makes most of its P&L on a small number of "
                       "large trending days. Any filter that protects against bad days but blunts those big days loses net. "
                       "Same structural failure as the burst cap, late stop gate, and choppiness boost.",
             "date": "Apr 27, 2026"},
        42: {"reason": "Tested across 12 live days and 40 total days (backfill + live). "
                       "Net: +$16.25 on 12 live days, +$6.12 on 40 days — both essentially noise. "
                       "The gate saves on clear reversal days (Mar 5 +$62, Apr 28 +$23) but blocks winners on trending days "
                       "where one ticker stops fast and the rest of the session follows through (Mar 4 -$57, Mar 9 -$26, Apr 22 -$6). "
                       "A quick stop early in the session does not reliably predict what the rest of the session will do. "
                       "The 15-minute blunt-force block is too crude — it blocks good entries alongside bad ones in equal proportion.",
             "date": "Apr 28, 2026"},
        41: {"reason": "Tested across 12 live days. Net: -$7.74. "
                       "Saves on some bad days (Apr 27 +$15, Apr 28 +$13) but blocks ARM's take-profit on Apr 23 (-$24) — "
                       "the entire session's profit — because two earlier positions happened to be below entry at T+20 on a day "
                       "that was still profitable overall. The gate is broad enough to misfire on days that start slow but "
                       "ultimately trend. Two positions being simultaneously below entry at T+20 is not a reliable reversal signal "
                       "when one of them is ARM about to hit take-profit 2 minutes later.",
             "date": "Apr 28, 2026"},
        40: {"reason": "Tested across 12 live days. Net: -$60.55. "
                       "The gate fires on Apr 24 — the best trending day in the dataset (+$166) — and blocks DKNG take-profit (+$27) "
                       "and NVDA take-profit (+$36). KOPN's GAP_GO stalled at T+20 on Apr 24 but the rest of the session was a "
                       "strong bull day. A stalling GAP_GO on one ticker does not indicate that ORB signals on other tickers "
                       "will fail. Slightly positive on Apr 27 (+$2.44) but the Apr 24 damage is severe and not recoverable.",
             "date": "Apr 28, 2026"},
        39: {"reason": "Tested across 12 live days and 40 total days. Net: -$87.79 on 12 days. "
                       "Fires on Apr 24 (best day, +$166) and blocks META time-close (+$25), DKNG take-profit (+$27), "
                       "and NVDA take-profit (+$36). KOPN trailed at 09:44 — one minute before the 09:45 cutoff — on what "
                       "turned out to be a strong trending day. One ticker fading at the open does not mean the whole session "
                       "is bad. The gate conflates a single ticker's reversal with session-wide weakness. Only helps on Apr 27 "
                       "(+$0.07, trivial). Same structural failure as gates 1-3.",
             "date": "Apr 28, 2026"},
        38: {"reason": "Tested caps of 2, 3, and 4 concurrent open positions across 12 tracked days and 38 backfill days. "
                       "Cap=3: -$93 tracked / -$150 backfill. Cap=4: -$23 tracked / -$10 backfill. "
                       "The cap does help on bad days — Apr 28 cap=3 saves $32, Apr 21 saves $19, Mar 17 saves $35. "
                       "But it destroys value on strong trending days where entries cluster AND follow through together: "
                       "Apr 13 -$52, Apr 2 -$62, Mar 31 -$28, Apr 24 -$35 under cap=3. "
                       "Cap=4 is nearly neutral (-$10 backfill) but that means it barely binds and doesn't solve the problem. "
                       "Same structural failure as the burst cap: on the strategy's best days, clustered entries are a feature "
                       "not a bug — all positions follow through. Any rule that limits simultaneous exposure at entry blocks "
                       "losers and winners in equal proportion because they look identical at the moment of entry.",
             "date": "Apr 28, 2026"},
    }

    addressed_idxs = growth_state.get("addressed", [])
    rejected_idxs  = growth_state.get("rejected",  [])

    # Collect shipped items with date for sorting
    imp_items = []
    for idx in addressed_idxs:
        if idx < len(GROWTH_POOL):
            title, suggestion = GROWTH_POOL[idx]
        elif idx in ARCHIVED_ITEMS:
            title      = ARCHIVED_ITEMS[idx]["title"]
            suggestion = ARCHIVED_ITEMS[idx]["original"]
        else:
            continue
        res  = GROWTH_RESOLUTIONS.get(idx, {})
        imp_items.append({
            "title": title, "suggestion": suggestion,
            "what": res.get("what", "Addressed"),
            "detail": res.get("detail", ""),
            "impact": res.get("impact", ""),
            "date": res.get("date", ARCHIVED_ITEMS.get(idx, {}).get("date", "pre-Apr 13, 2026")),
        })
    imp_items.sort(key=lambda x: _date_sort_key(x["date"]), reverse=True)

    imp_cards = ""
    for item in imp_items:
        date_badge = f'<span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">{item["date"]}</span>'
        imp_cards += f"""
        <div class="imp-card">
            <div class="imp-title">&#10003; {item["title"]}{date_badge}</div>
            <div class="imp-original"><em>Original concern:</em> {item["suggestion"]}</div>
            <div class="imp-what">{item["what"]}</div>
            {"<div class='imp-detail'>" + item["detail"] + "</div>" if item["detail"] else ""}
            {"<div class='imp-impact'>" + item["impact"] + "</div>" if item["impact"] else ""}
        </div>"""

    # Collect rejected items with date for sorting
    rej_items = []
    for idx in rejected_idxs:
        if idx < len(GROWTH_POOL):
            title, suggestion = GROWTH_POOL[idx]
        elif idx in ARCHIVED_ITEMS:
            title      = ARCHIVED_ITEMS[idx]["title"]
            suggestion = ARCHIVED_ITEMS[idx]["original"]
        else:
            continue
        rej_info = GROWTH_REJECTIONS.get(idx, {"reason": "Reviewed and decided not to implement.", "date": ""})
        rej_items.append({
            "title": title, "suggestion": suggestion,
            "reason": rej_info["reason"],
            "date": rej_info.get("date", ARCHIVED_ITEMS.get(idx, {}).get("date", "pre-Apr 13, 2026")),
        })
    rej_items.sort(key=lambda x: _date_sort_key(x["date"]), reverse=True)

    rej_cards = ""
    for item in rej_items:
        date_badge = f'<span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">{item["date"]}</span>'
        rej_cards += f"""
        <div class="rej-card">
            <div class="rej-title">&#10007; {item["title"]}{date_badge}</div>
            <div class="imp-original"><em>Original concern:</em> {item["suggestion"]}</div>
            <div class="rej-reason">{item["reason"]}</div>
        </div>"""

    shipped_block = (f'<p style="color:#888;font-size:0.85em;margin-bottom:16px">'
                     f'{len(addressed_idxs)} suggestion{"s" if len(addressed_idxs)!=1 else ""} live in the model.</p>'
                     f'<div class="imp-grid">{imp_cards}</div>') if addressed_idxs else \
                    '<p style="color:#555;font-size:0.85em;margin-bottom:16px">None yet.</p>'

    skipped_block = (f'<p style="color:#666;font-size:0.82em;margin-bottom:16px">Tested and decided against — kept here so we don\'t revisit them unnecessarily.</p>'
                     f'<div class="imp-grid">{rej_cards}</div>') if rej_cards else \
                    '<p style="color:#555;font-size:0.85em">None yet.</p>'

    active_logic_html = """
        <p style="color:#888;font-size:0.85em;margin-bottom:20px">Everything currently running in the model — rated by how well it's working.</p>

        <div class="active-sec-hdr active-sec-keep">&#10003; Working Well — Keep</div>

        <div class="active-card">
            <div class="active-card-title">ORB Entry Signal <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">Triggers on the first 1-minute close above the opening range high (9:30–9:44). Gives the market 15 minutes to establish direction before committing capital.</div>
            <div class="active-card-verdict verdict-keep">Consistent, non-lagging entry. The primary trigger for most winning trades.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">Volume Floor (1.0x minimum for TAKE) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">Signals below 0.5x average volume are always SKIP. Between 0.5x–1.0x are capped at MAYBE. Only signals with 1.0x+ volume can reach TAKE. Filters out thin, low-conviction moves.</div>
            <div class="active-card-verdict verdict-keep">Directly responsible for catching the NVDA thin-volume false signal on 4/20. Working as intended.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">Take Profit (+3%) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">Exits positions that reach +3% gain from entry. Locks in profits before the inevitable intraday reversal.</div>
            <div class="active-card-verdict verdict-keep">Clean exits on several strong days. Prevents giving back gains on choppy afternoons.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">Stop Loss (-1.5%) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">Hard exit at -1.5% from entry. Caps the downside on any single position, keeping losses predictable and bounded.</div>
            <div class="active-card-verdict verdict-keep">Working exactly as designed. Prevents any single bad trade from derailing the day.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">Concurrent Capital Tracking (Two-Phase Simulation) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">Apr 26, 2026</span></div>
            <div class="active-card-desc">Phase 1 collects all qualifying trades for the day. Phase 2 simulates them chronologically, only entering a position if the cash is actually available — accounting for trades that overlap in time.</div>
            <div class="active-card-verdict verdict-keep">Fixed a major bug where the same $5,000 was being reused 15x. Now the simulation reflects reality.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">Daily Loss Limit ($75) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">If cumulative P&amp;L on the day drops below -$75, no new entries are taken for the rest of the session. Stops digging the hole deeper on bad days.</div>
            <div class="active-card-verdict verdict-keep">A clean, hard circuit breaker. No ambiguity.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">Before-Date Wallet Compounding <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">Apr 26, 2026</span></div>
            <div class="active-card-desc">When calculating the starting balance for a given date, only past entries (before that date) are counted. Prevents future entries from inflating the wallet during re-runs.</div>
            <div class="active-card-verdict verdict-keep">Critical correctness fix. Without it, re-runs produce unreliable starting balances.</div>
        </div>

        <div class="active-sec-hdr active-sec-watch" style="margin-top:28px">~ Working Moderately — Keep an Eye On</div>

        <div class="active-card">
            <div class="active-card-title">Trailing Stop (-2.0% from peak, +1% lock) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">shipped Apr 24, 2026</span></div>
            <div class="active-card-desc">After entry, tracks the highest price reached. Once price clears +1% above entry (the lock), if price drops 2.0% from that peak, the position exits. Designed to protect profits while letting winners run.</div>
            <div class="active-card-verdict verdict-watch">Improved 11 trades in backtest (0 worse). But on burst-entry sessions, the +1% lock fires on single-bar ORB volatility rather than sustained momentum — can exit at near-breakeven on choppy days. Monitoring at 30 days.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">Time Close (14:00) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">All open positions exit at 2:00 PM regardless of P&amp;L. Avoids the volatile last hour of trading.</div>
            <div class="active-card-verdict verdict-watch">65% win rate on time-closed trades. But it exits early on strong trending days that keep running into the close. Trade-off being evaluated.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">ATR Allocation Modifier (0.40x–1.50x) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">Scales position size based on each ticker's recent volatility (ATR). High-volatility tickers get smaller positions; calm tickers get larger ones.</div>
            <div class="active-card-verdict verdict-watch">Sound in theory. Hard to isolate its effect at 10 days of data. Revisit at 30 days.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">MAYBE Streak Cut (50% allocation) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">After 2+ consecutive losing days, MAYBE-rated entries have their allocation halved. Intended to reduce risk during bad streaks. Note: streak cut paradox exists — smaller positions mean more trades fit the budget, potentially increasing total exposure.</div>
            <div class="active-card-verdict verdict-watch">Right instinct, imperfect execution. The streak cut paradox (more trades on bad days) is documented and being monitored.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">Drawdown Cut (50% allocation) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">After a single large losing day, allocations drop to 50% the following session. Reduces exposure coming off a bruising day.</div>
            <div class="active-card-verdict verdict-watch">Reasonable safeguard. Not yet tested against a sustained drawdown period.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">Market State Classification (BULL / NEUT / BEAR) <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">Uses SPY gap % and VIXY trend to label each day's market environment. Displayed in the Market Overview banner.</div>
            <div class="active-card-verdict verdict-watch">Classification is working. Whether it changes trade decisions meaningfully is still being evaluated — SPY direction did not correlate with individual stock outcomes in our sample.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">Dominant Trend Protection <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">pre-Apr 13, 2026</span></div>
            <div class="active-card-desc">If a ticker is up or down more than 2% from the day's open, counter-trend signals require at least 2x average volume to be considered. Blocks fading a strong trend on thin volume.</div>
            <div class="active-card-verdict verdict-watch">Correctly blocked counter-trend noise on COIN's big up day. Sample too small to confirm it doesn't block legitimate reversals.</div>
        </div>

        <div class="active-sec-hdr active-sec-bad" style="margin-top:28px">&#10007; Not Working Well</div>

        <div class="active-card">
            <div class="active-card-title">EX2 Re-entries <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">Apr 13, 2026</span></div>
            <div class="active-card-desc">After a stop-loss exit, EX2 will re-enter the same ticker later in the session if a new qualifying signal fires. Idea: catch the reversal after being stopped out.</div>
            <div class="active-card-verdict verdict-bad">Net -$5.99 across 10 days. 4 of 5 re-entries were losers. Going back into a stock that just stopped you out in the same session is not working. Monitoring at 20 and 30 day marks before dropping.</div>
        </div>
        <div class="active-card">
            <div class="active-card-title">TAKE Signal Frequency <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">Apr 13, 2026</span></div>
            <div class="active-card-desc">TAKE is the highest conviction rating — requires score ≥ 1 with volume ≥ 1.0x. In practice it almost never fires. The vast majority of trades are MAYBE, making the two-tier system largely theoretical.</div>
            <div class="active-card-verdict verdict-bad">The TAKE bar may be too high, or market conditions in our sample simply haven't produced enough strong-volume breakouts. At current firing rates, TAKE vs MAYBE is not a meaningful distinction.</div>
        </div>"""

    revisit_html = """
        <p style="color:#888;font-size:0.85em;margin-bottom:20px">Tested and showing promise — not enough data yet to ship. Revisit at the dates noted.</p>

        <div class="active-card">
            <div class="active-card-title">High-Vol TAKE Promotion — vol &ge;2.5x treated as TAKE regardless of choppiness <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">Apr 27, 2026</span></div>
            <div class="active-card-desc">
                TAKE currently requires high volume AND low choppiness — but opening bars are almost always choppy,
                so TAKE fires only twice across 11 days. Promoting vol &ge;2.5x to TAKE would double allocation
                on those signals (NEUTRAL: 30% vs 15% of wallet). Approximate backtest across 11 EX1 days:
                <br><br>
                <strong style="color:#e0e0e0">Net: +$95.45 vs baseline</strong> — strongest of three options tested. Worked well on
                trending days (Apr 14 META 3.7x +$26 doubled, Apr 16 AMD 2.8x +$31 doubled, Apr 24 SMCI 3.1x +$21 doubled).
                But amplified losses on bad days: Apr 21 SMCI 2.6x stop loss doubled to -$23, Apr 27 DKNG 2.5x stop loss doubled
                to -$27, PLTR 8.2x faded to -$12.
                <br><br>
                The +$95 gain comes from good days getting bigger — but so do bad days. This approach is only safe if high-volume
                signals are genuinely more reliable than lower-volume ones, which 11 days of data cannot confirm.
                Today (Apr 27) would have gone from -$17 to -$35 under this rule.
            </div>
            <div class="active-card-verdict verdict-watch">Revisit at 30 days. Need a larger sample to know if vol &ge;2.5x signals actually have a higher win rate. If yes, ship. If not, the signal scoring needs deeper work. Test script: <code>test_three_improvements.py</code></div>
        </div>

        <div class="active-card">
            <div class="active-card-title">MAYBE Entry Stop Losses — all stop losses across 11 EX1 days were MAYBE-rated <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">Apr 27, 2026</span></div>
            <div class="active-card-desc">
                All 8 stop losses came from MAYBE-rated entries in the 09:45–09:59 opening window.
                Zero stop losses fired on entries at 10:00 or later. Win rate by entry window:
                <strong style="color:#e0e0e0">09:45–09:59: 42% win, 15% stop rate</strong> (52 trades) vs
                <strong style="color:#e0e0e0">10:00+: 63% win, 0% stop rate</strong> (17 trades).
                <br><br>
                Vol tier stop rates: &lt;1.5x = 0%, 1.5–2.0x = 8%, 2.0–2.5x = 6%, 2.5–3.0x = 18%, &ge;3.0x = 23%.
                Higher volume does <em>not</em> protect against stops — the highest vol tiers have the most stop losses.
                <br><br>
                Three filters were tested to reduce MAYBE stop losses:
                &bull; TAKE-only: -$229.70. &bull; BEAR-day filter: $0.00 (no BEAR days in sample).
                &bull; Choppiness filter: -$153.02 (too broad — 54/59 MAYBE trades flagged choppy).
                <br><br>
                The morning cooldown was also tested (index 23) and rejected: cutting 09:45–09:59 entries
                cost -$78.51 net because the strongest trending days fire exactly in that window.
                No attribute reliably separates stop-loss MAYBE trades from winning ones.
            </div>
            <div class="active-card-verdict verdict-watch">Revisit at 30 days. If stop rate in 09:45–09:59 stays above 15% while 10:00+ stays at 0%, the pattern may be real — but any fix must not block strong trending-day entries. Test scripts: <code>test_maybe_filters.py</code>, <code>test_revisit.py</code></div>
        </div>

        <div class="active-card">
            <div class="active-card-title">Budget Priority Order — simultaneous signals assigned capital by ticker scan order, not signal quality <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">Apr 27, 2026</span></div>
            <div class="active-card-desc">
                On Apr 27, RDDT (+3.8% intraday) and APP (+3.6%) both broke their ORB highs at 09:45–09:46 —
                the exact same moment as META, PLTR, NFLX, and CRWD. When multiple signals fire simultaneously,
                the model processes them in fixed ticker scan order. META (#5), PLTR (#6), NFLX (#8) consumed
                the budget; APP (#9) and RDDT (#18) were budget-blocked.
                <br><br>
                The four trades that got in all finished within 0.30% of breakeven. The two that were blocked
                were the session's biggest movers.
                <br><br>
                <strong style="color:#e0e0e0">Vol-sort tested and rejected:</strong> Sorting same-time signals by volume ratio descending did not help —
                RDDT (1.5x) and APP (1.7x) had <em>lower</em> vol ratios than the flat trades (PLTR 8.2x, META 4.8x).
                Vol-sort also hurt Apr 17: SOFI (4.1x) jumped the queue and displaced TSLA, costing $9.33 net.
                Total across 11 days: -$9.33 vs baseline. Vol ratio at the ORB bar does not predict which ticker will run most.
                <br><br>
                The right fix is unknown but the problem is structural: the model has no way to rank simultaneous
                signals by expected outcome. Possible angles to explore: ORB breakout margin (% above range high),
                relative strength vs SPY at that bar, or a signal quality composite score beyond volume alone.
            </div>
            <div class="active-card-verdict verdict-watch">Revisit at 30 days. Need more examples of same-minute budget conflicts to measure how often the best mover is blocked, and whether any entry-time attribute predicts it. Log each occurrence with the blocked ticker and its eventual move.</div>
        </div>

        <div class="active-card">
            <div class="active-card-title">TAKE-Only Mode — skip all MAYBE-rated entries to reduce stop losses <span style="color:#555;font-size:0.76em;font-weight:normal;margin-left:8px">May 2, 2026</span></div>
            <div class="active-card-desc">
                The 15-day EX1 window showed MAYBE trades at -$90.36 (14% win rate, 14 trades) vs TAKE at +$102.48
                (45% win rate, 53 trades) — which made TAKE-only look compelling. But those 14 MAYBE trades were
                concentrated entirely on 2 days (Apr 28 and Apr 29). The 15-day picture was distorted by timing,
                not signal quality.
                <br><br>
                In the 38-day backfill (Mar 2 – Apr 24), MAYBE trades were
                <strong style="color:#e0e0e0">net +$357.09 at 41% win rate (214 trades)</strong> vs TAKE at
                <strong style="color:#e0e0e0">+$234.82 at 40% win rate (40 trades)</strong>.
                MAYBE trades contributed more total P&L and had a similar win rate — the case for cutting them
                does not hold up on a full dataset.
                <br><br>
                The Apr 28–29 loss cluster may reflect a bad market environment rather than a MAYBE problem.
                TAKE-only would have cost significant upside on trending days where MAYBE entries also ran.
            </div>
            <div class="active-card-verdict verdict-watch">Revisit at 30 days. If MAYBE win rate stays materially below TAKE across a full 30-day sample, a soft filter (e.g. skip MAYBE on BEAR days only) may be worth testing. Do not cut MAYBE entirely based on a 2-day cluster.</div>
        </div>

"""

    improvements_section = f"""
    <div id="imp-panel" style="display:none">
        <div class="section-header">Improvements</div>
        <div class="imp-subtabs">
            <button class="imp-subtab imp-sub-active" id="impsub-shipped" onclick="showImpSub('shipped')">Shipped</button>
            <button class="imp-subtab" id="impsub-active" onclick="showImpSub('active')">Active Logic</button>
            <button class="imp-subtab" id="impsub-revisit" onclick="showImpSub('revisit')">Revisit</button>
            <button class="imp-subtab" id="impsub-notpursuing" onclick="showImpSub('notpursuing')">Not Pursuing</button>
        </div>
        <div id="imp-sub-shipped">
            {shipped_block}
        </div>
        <div id="imp-sub-active" style="display:none">
            {active_logic_html}
        </div>
        <div id="imp-sub-revisit" style="display:none">
            {revisit_html}
        </div>
        <div id="imp-sub-notpursuing" style="display:none">
            {skipped_block}
        </div>
    </div>"""

    # --- Home panel ---
    home_cards = ""
    for asset in assets:
        ticker   = asset["ticker"]
        day_ohlc = [b for b, l in zip(asset["ohlc"], asset["labels"]) if l[:10] == default_date]
        day_vols = [v for v, l in zip(asset["volumes"], asset["labels"]) if l[:10] == default_date]
        if not day_ohlc:
            continue
        day_open  = day_ohlc[0]["o"]
        day_high  = max(b["h"] for b in day_ohlc)
        day_low   = min(b["l"] for b in day_ohlc)
        day_close = day_ohlc[-1]["c"]
        day_vol   = sum(day_vols)
        chg       = (day_close - day_open) / day_open * 100 if day_open else 0
        chg_cls   = "home-up" if chg >= 0 else "home-down"
        chg_sign  = "+" if chg >= 0 else ""
        vol_str   = f"{day_vol/1_000_000:.1f}M" if day_vol >= 1_000_000 else f"{day_vol/1_000:.0f}K"
        home_cards += f"""
        <div class="home-card" onclick="showTicker('{ticker}')">
            <div class="home-ticker">{ticker}</div>
            <div class="home-price">${day_close:.2f}</div>
            <div class="home-chg {chg_cls}">{chg_sign}{chg:.2f}%</div>
            <div class="home-meta">
                <span>H&nbsp;${day_high:.2f}</span>
                <span>L&nbsp;${day_low:.2f}</span>
                <span>Vol&nbsp;{vol_str}</span>
            </div>
        </div>"""

    state_path = os.path.join(os.path.dirname(__file__), "market_state.json")
    ms_banner  = ""
    if os.path.exists(state_path):
        with open(state_path) as _f:
            _ms = json.load(_f)
        _state = _ms.get("state", "neutral")
        _spy   = _ms.get("spy_gap_pct", 0)
        _vixy  = _ms.get("vixy_trend_pct", 0)
        _date  = _ms.get("date", "")
        _cls   = {"bullish": "ms-bull", "bearish": "ms-bear"}.get(_state, "ms-neut")
        ms_banner = (f'<div class="ms-banner {_cls}">'
                     f'Market: <strong>{_state.upper()}</strong> &nbsp;·&nbsp; '
                     f'SPY gap {_spy:+.2f}% &nbsp;·&nbsp; VIXY {_vixy:+.2f}% &nbsp;·&nbsp; '
                     f'as of {_date}</div>')

    home_section = f"""
    <div id="home-panel" style="display:block">
        <div class="section-header">Market Overview — {default_date}</div>
        {ms_banner}
        <div class="home-grid">{home_cards}</div>
    </div>"""

    ex1_entries    = [e for e in exercises if "Exercise 1" in e["title"]]
    ex2_entries    = [e for e in exercises if "Exercise 2" in e["title"]]
    wallet1        = round(5000 + sum(e["total_pnl"] for e in ex1_entries), 2)
    wallet2        = round(5000 + sum(e["total_pnl"] for e in ex2_entries), 2)
    w1_color       = "#4caf50" if wallet1 >= 5000 else "#f44336"
    w2_color       = "#4caf50" if wallet2 >= 5000 else "#f44336"
    wallet_html    = (
        f'<div style="margin-left:auto;display:flex;flex-direction:column;align-items:flex-end;gap:4px;text-align:right">'
        f'<div>'
        f'<span style="font-size:0.72em;color:#666;letter-spacing:0.04em;text-transform:uppercase">Wallet 1&nbsp;</span>'
        f'<span style="font-size:1.05em;font-weight:bold;color:{w1_color}">${wallet1:,.2f}</span>'
        f'</div>'
        f'<div>'
        f'<span style="font-size:0.72em;color:#666;letter-spacing:0.04em;text-transform:uppercase">Wallet 2&nbsp;</span>'
        f'<span style="font-size:1.05em;font-weight:bold;color:{w2_color}">${wallet2:,.2f}</span>'
        f'</div>'
        f'</div>'
    )

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Signal Reader Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/luxon@3/build/global/luxon.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1/dist/chartjs-adapter-luxon.umd.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial@0.2.1/dist/chartjs-chart-financial.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@1.2.1/dist/chartjs-plugin-zoom.min.js"></script>
    <style>
        html         {{ background: #0f0f1a; }}
        body         {{ font-family: sans-serif; background: #0f0f1a; color: #e0e0e0; margin: 0; padding: 20px; animation: fadeIn 0.35s ease-out; }}
        @keyframes fadeIn {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
        h1           {{ color: #4f8ef7; }}
        .controls    {{ display: flex; align-items: center; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }}
        .nav         {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 12px; }}
        .tabs        {{ display: flex; flex-direction: column; gap: 4px; }}
        .tab-row     {{ display: flex; gap: 6px; flex-wrap: wrap; }}
        .tab-row-crypto {{ border-top: 1px solid #2a2a3e; padding-top: 4px; }}
        .tab         {{ background: #1a1a2e; color: #7eb8f7; border: 1px solid #333; border-radius: 6px; padding: 6px 16px; cursor: pointer; font-size: 0.9em; }}
        .tab.active  {{ background: #4f8ef7; color: #fff; border-color: #4f8ef7; }}
        .tab:hover   {{ border-color: #4f8ef7; }}
        .date-label  {{ color: #888; font-size: 0.9em; }}
        select       {{ background: #1a1a2e; color: #e0e0e0; border: 1px solid #4f8ef7; border-radius: 6px; padding: 6px 10px; font-size: 0.9em; cursor: pointer; }}
        .card        {{ background: #1a1a2e; border-radius: 10px; padding: 20px; }}
        h3           {{ color: #aaa; font-size: 0.9em; margin-top: 20px; }}
        table        {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
        th           {{ text-align: left; color: #888; padding: 4px 8px; border-bottom: 1px solid #333; }}
        td           {{ padding: 4px 8px; }}
        .signal-up   {{ color: #4caf50; }}
        .signal-down {{ color: #f44336; }}
        .rating      {{ font-weight: bold; padding: 2px 7px; border-radius: 4px; font-size: 0.8em; }}
        .rating-take  {{ background: #1b3a1b; color: #4caf50; }}
        .rating-maybe {{ background: #2e2a10; color: #f0c040; }}
        .rating-skip  {{ background: #2a1a1a; color: #888; }}
        .meta         {{ color: #555; font-size: 0.8em; margin-top: 30px; }}
        .section-header {{ color: #4f8ef7; font-size: 1.1em; font-weight: bold; margin: 36px 0 12px; border-bottom: 1px solid #2a2a4a; padding-bottom: 6px; }}
        .pnl-tracker  {{ display: flex; flex-direction: column; gap: 16px; }}
        .ex-card      {{ background: #1a1a2e; border-radius: 10px; padding: 16px 20px; }}
        .ex-header    {{ display: flex; align-items: center; gap: 16px; margin-bottom: 12px; flex-wrap: wrap; }}
        .ex-title     {{ font-weight: bold; color: #7eb8f7; font-size: 1em; }}
        .ex-date      {{ color: #555; font-size: 0.85em; }}
        .ex-summary   {{ margin-left: auto; font-weight: bold; font-size: 0.95em; }}
        .ex-totals td {{ border-top: 1px solid #2a2a4a; color: #aaa; font-weight: bold; padding-top: 6px; }}
        .pnl-win      {{ color: #4caf50; }}
        .pnl-loss     {{ color: #f44336; }}
        .collapsible  {{ cursor: pointer; user-select: none; color: #aaa; font-size: 0.9em; margin-top: 20px; }}
        .collapsible:hover {{ color: #7eb8f7; }}
        .btn-row     {{ display: flex; gap: 8px; margin-bottom: 10px; }}
        .reset-btn, .toggle-btn {{
            background: #2a2a4a; color: #7eb8f7; border: 1px solid #4f8ef7;
            border-radius: 5px; padding: 4px 12px; cursor: pointer; font-size: 0.8em;
        }}
        .reset-btn:hover, .toggle-btn:hover {{ background: #4f8ef7; color: #fff; }}
        .toggle-btn.active {{ background: #4f8ef7; color: #fff; }}
        .ex-cumulative   {{ background: #12122a; border: 1px solid #2a2a4a; border-radius: 8px; padding: 10px 16px; margin-bottom: 16px; font-size: 0.9em; color: #aaa; }}
        .day-block       {{ margin-bottom: 8px; border: 1px solid #2a2a4a; border-radius: 8px; overflow: hidden; }}
        .day-toggle      {{ display: flex; align-items: center; gap: 10px; padding: 10px 16px; cursor: pointer; background: #12122a; user-select: none; }}
        .day-toggle:hover {{ background: #1a1a3a; }}
        .day-arrow       {{ color: #4f8ef7; font-size: 0.8em; width: 12px; }}
        .day-label-text  {{ font-weight: bold; color: #e0e0e0; font-size: 0.95em; }}
        .day-badge       {{ font-size: 0.8em; font-weight: bold; padding: 2px 8px; border-radius: 4px; background: #1a1a2e; }}
        .day-body        {{ padding: 14px 16px; display: flex; flex-direction: column; gap: 16px; }}
        .ex-sub-header   {{ color: #7eb8f7; font-size: 0.85em; font-weight: bold; margin-bottom: 6px; }}
        .trade-num       {{ color: #555; font-size: 0.8em; }}
        .best-price      {{ color: #f0c040; font-weight: bold; }}
        .re-badge        {{ display: inline-block; font-size: 0.7em; font-weight: bold; background: #2a1a4a; color: #a78bfa; border-radius: 3px; padding: 1px 5px; vertical-align: middle; margin-left: 4px; }}
        .reentry-row td  {{ background: #130e1e; }}
        .re-day-badge    {{ font-size: 0.75em; font-weight: bold; background: #2a1a4a; color: #a78bfa; border-radius: 4px; padding: 2px 7px; margin-left: 4px; }}
        .ex-compare-strip {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }}
        .ex-stat-card    {{ background: #12122a; border: 1px solid #2a2a4a; border-radius: 8px; padding: 14px 18px; }}
        .ex-stat-title   {{ font-size: 0.78em; font-weight: bold; letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 6px; }}
        .ex-stat-pnl     {{ font-size: 1.6em; font-weight: bold; margin-bottom: 4px; }}
        .ex-stat-meta    {{ font-size: 0.8em; color: #666; line-height: 1.6; }}
        .ex-stat-re      {{ color: #a78bfa; }}
        .ex-tab-row      {{ display: flex; gap: 8px; margin-bottom: 14px; }}
        .ex-tab-btn      {{ background: #12122a; color: #888; border: 1px solid #2a2a4a; border-radius: 6px; padding: 6px 18px; cursor: pointer; font-size: 0.88em; font-weight: bold; transition: all 0.15s; }}
        .ex-tab-btn:hover  {{ border-color: #4f8ef7; color: #fff; }}
        .ex-tab-btn.active {{ background: #1a1a3a; color: #fff; border-color: #4f8ef7; }}
        .pnl-top-tab     {{ padding: 3px 12px; font-size: 0.78em; }}
        .grad-banner        {{ text-align: center; font-size: 1.1em; font-weight: bold; padding: 14px; border-radius: 8px; margin-bottom: 20px; }}
        .grad-banner-waiting {{ background: #1e1a00; color: #f0c040; border: 1px solid #5a4a00; }}
        .grad-banner-ready   {{ background: #0d2a0d; color: #4caf50; border: 1px solid #1b5e20; }}
        .grad-progress-wrap {{ margin-bottom: 24px; }}
        .grad-progress-label {{ color: #888; font-size: 0.85em; margin-bottom: 6px; }}
        .grad-progress-bar  {{ background: #1a1a2e; border-radius: 6px; height: 10px; overflow: hidden; }}
        .grad-progress-fill {{ background: #4f8ef7; height: 100%; border-radius: 6px; transition: width 0.5s; }}
        .grad-grid          {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; margin-bottom: 20px; }}
        .grad-card          {{ border-radius: 8px; padding: 14px 16px; border: 1px solid #2a2a4a; background: #12122a; position: relative; }}
        .grad-pass-card     {{ border-color: #1b5e20; }}
        .grad-fail-card     {{ border-color: #7f0000; }}
        .grad-inprogress    {{ border-color: #5a4a00; }}
        .grad-dot           {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 8px; vertical-align: middle; }}
        .grad-pass          {{ background: #4caf50; }}
        .grad-fail          {{ background: #f44336; }}
        .grad-pending       {{ background: #f0c040; }}
        .grad-label         {{ font-weight: bold; font-size: 0.9em; color: #e0e0e0; margin-bottom: 6px; margin-top: 4px; }}
        .grad-detail        {{ font-size: 0.82em; color: #9ca3af; margin-bottom: 4px; }}
        .grad-target        {{ font-size: 0.78em; color: #555; }}
        .grad-note          {{ font-size: 0.78em; color: #444; margin-top: 8px; }}
        .grad-readiness     {{ margin-top: 28px; border-top: 1px solid #2a2a4a; padding-top: 20px; }}
        .grad-readiness-hdr {{ font-size: 0.95em; font-weight: bold; color: #7eb8f7; margin-bottom: 14px; letter-spacing: 0.03em; }}
        .grad-block         {{ background: #12122a; border: 1px solid #2a2a4a; border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; }}
        .grad-block-title   {{ font-size: 0.82em; font-weight: bold; color: #e0e0e0; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em; }}
        .grad-block ul      {{ margin: 0; padding-left: 18px; }}
        .grad-block li      {{ font-size: 0.82em; color: #9ca3af; line-height: 1.7; }}
        .grad-block li strong {{ color: #e0e0e0; }}
        .grad-concern li    {{ color: #f0a04a; }}
        .grad-concern       {{ border-color: #5a3a00; }}
        .grad-need li       {{ color: #7eb8f7; }}
        .grad-verdict       {{ border-color: #2a2a4a; }}
        .grad-verdict p     {{ font-size: 0.85em; color: #9ca3af; margin: 0; line-height: 1.7; }}
        .imp-grid           {{ display: flex; flex-direction: column; gap: 16px; max-width: 860px; }}
        .imp-card           {{ background: #0d1f0d; border: 1px solid #1b5e20; border-radius: 8px; padding: 16px 20px; }}
        .imp-title          {{ font-weight: bold; color: #4caf50; font-size: 1em; margin-bottom: 10px; }}
        .imp-original       {{ font-size: 0.82em; color: #666; margin-bottom: 10px; line-height: 1.5; }}
        .imp-what           {{ font-size: 0.9em; color: #b0c4b1; font-weight: bold; margin-bottom: 6px; }}
        .imp-detail         {{ font-size: 0.83em; color: #9ca3af; line-height: 1.6; margin-bottom: 8px; }}
        .imp-impact         {{ font-size: 0.83em; color: #4caf50; background: #0a1a0a; border-left: 3px solid #2e7d32; padding: 6px 10px; border-radius: 4px; }}
        .rej-card           {{ background: #1a1010; border: 1px solid #3a1a1a; border-radius: 8px; padding: 16px 20px; }}
        .rej-title          {{ font-weight: bold; color: #888; font-size: 1em; margin-bottom: 10px; }}
        .rej-reason         {{ font-size: 0.83em; color: #666; line-height: 1.6; margin-top: 8px; border-left: 3px solid #3a1a1a; padding-left: 10px; }}
        .imp-subtabs        {{ display: flex; gap: 8px; margin-bottom: 24px; border-bottom: 1px solid #2a2a4a; padding-bottom: 12px; }}
        .imp-subtab         {{ background: none; border: 1px solid #2a2a4a; border-radius: 6px; color: #666; font-size: 0.82em; padding: 6px 16px; cursor: pointer; font-family: inherit; }}
        .imp-subtab.imp-sub-active {{ background: #1a1a3e; border-color: #4f8ef7; color: #7eb8f7; font-weight: bold; }}
        .imp-subtab:hover   {{ border-color: #4f8ef7; color: #9ca3af; }}
        .active-sec-hdr     {{ font-size: 0.82em; font-weight: bold; letter-spacing: 0.06em; text-transform: uppercase; margin: 0 0 14px; padding: 7px 12px; border-radius: 4px; }}
        .active-sec-keep    {{ color: #4caf50; background: #0a1a0a; border-left: 3px solid #2e7d32; }}
        .active-sec-watch   {{ color: #f0a04a; background: #1a1200; border-left: 3px solid #8a5a00; }}
        .active-sec-bad     {{ color: #f44336; background: #1a0808; border-left: 3px solid #7a1a1a; }}
        .active-card        {{ background: #12122a; border: 1px solid #2a2a4a; border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; }}
        .active-card-title  {{ font-size: 0.9em; font-weight: bold; color: #e0e0e0; margin-bottom: 5px; }}
        .active-card-desc   {{ font-size: 0.82em; color: #9ca3af; line-height: 1.65; }}
        .active-card-verdict {{ font-size: 0.8em; font-style: italic; margin-top: 7px; }}
        .verdict-keep       {{ color: #4caf50; }}
        .verdict-watch      {{ color: #f0a04a; }}
        .verdict-bad        {{ color: #f44336; }}
        .notes-section   {{ margin-top: 12px; border-top: 1px solid #2a2a4a; padding-top: 10px; }}
        .notes-header    {{ color: #a78bfa; font-size: 0.82em; font-weight: bold; cursor: pointer; user-select: none; display: flex; align-items: center; gap: 6px; }}
        .notes-header:hover {{ color: #c4b5fd; }}
        .notes-arrow     {{ font-size: 0.75em; width: 10px; }}
        .notes-body      {{ margin-top: 8px; }}
        .notes-text      {{ color: #9ca3af; font-size: 0.82em; line-height: 1.6; margin: 0 0 6px 0; }}
        .home-grid       {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; margin-top: 16px; }}
        .home-card       {{ background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 10px; padding: 18px 16px; cursor: pointer; transition: border-color 0.15s; }}
        .home-card:hover {{ border-color: #4f8ef7; }}
        .home-ticker     {{ font-size: 1.1em; font-weight: bold; color: #7eb8f7; margin-bottom: 6px; }}
        .home-price      {{ font-size: 1.5em; font-weight: bold; color: #e0e0e0; margin-bottom: 4px; }}
        .home-chg        {{ font-size: 0.95em; font-weight: bold; margin-bottom: 10px; }}
        .home-up         {{ color: #4caf50; }}
        .home-down       {{ color: #f44336; }}
        .home-meta       {{ display: flex; flex-direction: column; gap: 3px; font-size: 0.78em; color: #666; }}
        .ms-banner       {{ border-radius: 8px; padding: 10px 16px; font-size: 0.88em; margin-bottom: 4px; }}
        .ms-bull         {{ background: #0d2a0d; color: #4caf50; border: 1px solid #1b5e20; }}
        .ms-neut         {{ background: #1a1a2e; color: #aaa;     border: 1px solid #2a2a4a; }}
        .ms-bear         {{ background: #2a0d0d; color: #f44336;  border: 1px solid #7f0000; }}
        .home-btn        {{ background: #12122a; color: #7eb8f7; border: 1px solid #2a2a4a; border-radius: 8px; padding: 6px 18px; cursor: pointer; font-size: 0.95em; font-weight: bold; transition: border-color 0.15s; }}
        .home-btn:hover  {{ border-color: #4f8ef7; color: #fff; }}
        .home-btn.active {{ background: #4f8ef7; color: #fff; border-color: #4f8ef7; }}
    </style>
</head>
<body>
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
        <h1 style="margin:0;color:#4f8ef7">Signal Reader</h1>
        <button class="home-btn active" id="btn-home" onclick="showHome()">Home</button>
        {wallet_html}
    </div>
    <div class="nav">
        <div class="controls">
            <div class="tabs">{ticker_tabs}</div>
            <span class="date-label" id="day-label">Day:</span>
            <select id="date-select" onchange="changeDate(this.value)">{date_options}</select>
            <span class="date-label" style="color:#555" id="interval-label">Interval: 1m &nbsp;|&nbsp; refresh in <span id="countdown">60</span>s</span>
        </div>
        <div style="display:flex;gap:6px;margin-left:auto">
            <button class="tab" id="tab-pnl" onclick="showPnL()">P&amp;L</button>
            <button class="tab" id="tab-grad" onclick="showGrad()">Graduation</button>
            <button class="tab" id="tab-imp" onclick="showImprovements()">Improvements</button>
        </div>
    </div>
    {home_section}
    <div id="chart-panel" style="display:none">{cards}</div>
    {pnl_section}
    {grad_section}
    {improvements_section}
    <p class="meta">Generated: {generated} — auto-refreshes every 60 seconds (keep run.py running)</p>
    <script>
        var charts    = {{}};
        var chartMode = {{}};
        var chartData = {{}};
        var currentDate = '{default_date}';

        var zoomPlugin = {{
            zoom: {{ wheel: {{ enabled: true }}, pinch: {{ enabled: true }}, mode: 'x' }},
            pan:  {{ enabled: true, mode: 'x' }}
        }};

        function filterData(ticker, dateStr) {{
            var d   = chartData[ticker];
            var idx = d.labels.map((l, i) => l.startsWith(dateStr) ? i : -1).filter(i => i >= 0);
            var startTs = idx.length ? d.ohlc[idx[0]].x : 0;
            var endTs   = idx.length ? d.ohlc[idx[idx.length-1]].x : Infinity;
            return {{
                labels:    idx.map(i => d.labels[i]),
                closes:    idx.map(i => d.closes[i]),
                volumes:   idx.map(i => d.volumes[i]),
                ohlc:      idx.map(i => d.ohlc[i]),
                volumesTs: idx.map(i => d.volumesTs[i]),
                vwapTs:    idx.map(i => d.vwapTs[i]),
                sigUp:     d.signalPts.filter(s => s.x >= startTs && s.x <= endTs && s.direction === 'UP'),
                sigDown:   d.signalPts.filter(s => s.x >= startTs && s.x <= endTs && s.direction === 'DOWN'),
            }};
        }}

        function sigColor(pts) {{
            return pts.map(s => s.rating === 'TAKE' ? '#4caf50' : s.rating === 'MAYBE' ? '#f0c040' : '#888');
        }}

        function buildChart(ticker, dateStr) {{
            var wrap = document.getElementById('wrap-' + ticker);
            wrap.innerHTML = '<canvas id="chart-' + ticker + '"></canvas>';
            var ctx  = document.getElementById('chart-' + ticker).getContext('2d');
            var d    = filterData(ticker, dateStr);
            var mode = chartMode[ticker];

            var vwapDataset = {{
                label: 'VWAP',
                type: 'line',
                data: d.vwapTs,
                borderColor: '#a78bfa',
                borderWidth: 1.5,
                pointRadius: 0,
                yAxisID: 'y',
                order: 1
            }};
            var sigUpDataset = {{
                label: 'Signal Up',
                type: 'scatter',
                data: d.sigUp,
                pointStyle: 'triangle',
                rotation: 0,
                pointRadius: 7,
                backgroundColor: '#4caf50',
                borderWidth: 0,
                yAxisID: 'y',
                order: 0
            }};
            var sigDownDataset = {{
                label: 'Signal Down',
                type: 'scatter',
                data: d.sigDown,
                pointStyle: 'triangle',
                rotation: 180,
                pointRadius: 7,
                backgroundColor: '#f44336',
                borderWidth: 0,
                yAxisID: 'y',
                order: 0
            }};

            if (mode === 'candlestick') {{
                return new Chart(ctx, {{
                    type: 'candlestick',
                    data: {{
                        datasets: [{{
                            label: ticker,
                            data: d.ohlc,
                            yAxisID: 'y',
                            color: {{ up: '#4caf50', down: '#f44336', unchanged: '#aaa' }},
                            order: 2
                        }},
                        vwapDataset, sigUpDataset, sigDownDataset,
                        {{
                            label: 'Volume',
                            type: 'bar',
                            data: d.volumesTs,
                            backgroundColor: 'rgba(150,150,150,0.3)',
                            yAxisID: 'y2',
                            order: 3
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        scales: {{
                            x:  {{ type: 'time', time: {{ unit: 'minute' }}, ticks: {{ maxTicksLimit: 10 }} }},
                            y:  {{ position: 'left',  title: {{ display: true, text: 'Price (USD)' }} }},
                            y2: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Volume' }} }}
                        }},
                        plugins: {{ zoom: zoomPlugin, legend: {{ labels: {{ filter: function(item) {{ return ['VWAP','Signal Up','Signal Down'].includes(item.text) || item.text === ticker; }} }} }} }}
                    }}
                }});
            }} else {{
                var closesTs = d.ohlc.map(function(b) {{ return {{x: b.x, y: b.c}}; }});
                return new Chart(ctx, {{
                    type: 'line',
                    data: {{
                        datasets: [{{
                            label: ticker + ' Close',
                            data: closesTs,
                            borderColor: '#4f8ef7',
                            backgroundColor: 'rgba(79,142,247,0.1)',
                            tension: 0.2,
                            pointRadius: 2,
                            yAxisID: 'y',
                            order: 2
                        }},
                        vwapDataset, sigUpDataset, sigDownDataset,
                        {{
                            label: 'Volume',
                            type: 'bar',
                            data: d.volumesTs,
                            backgroundColor: 'rgba(150,150,150,0.3)',
                            yAxisID: 'y2',
                            order: 3
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        interaction: {{ mode: 'index', intersect: false }},
                        scales: {{
                            x:  {{ type: 'time', time: {{ unit: 'minute' }}, ticks: {{ maxTicksLimit: 10 }} }},
                            y:  {{ position: 'left',  title: {{ display: true, text: 'Price (USD)' }} }},
                            y2: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Volume' }} }}
                        }},
                        plugins: {{ zoom: zoomPlugin, legend: {{ labels: {{ filter: function(item) {{ return ['VWAP','Signal Up','Signal Down'].includes(item.text) || item.text === ticker + ' Close'; }} }} }} }}
                    }}
                }});
            }}
        }}

        function hideAll() {{
            document.getElementById('home-panel').style.display   = 'none';
            document.getElementById('chart-panel').style.display  = 'none';
            var pnl  = document.getElementById('pnl-panel');  if (pnl)  pnl.style.display  = 'none';
            var grad = document.getElementById('grad-panel'); if (grad) grad.style.display = 'none';
            var imp  = document.getElementById('imp-panel');  if (imp)  imp.style.display  = 'none';
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById('btn-home').classList.remove('active');
        }}

        function showImprovements() {{
            hideAll();
            var imp = document.getElementById('imp-panel');
            if (imp) imp.style.display = 'block';
            document.getElementById('tab-imp').classList.add('active');
            document.getElementById('date-select').style.display  = 'none';
            document.getElementById('day-label').style.display    = 'none';
            document.getElementById('interval-label').style.display = 'none';
            localStorage.setItem('activePanel', 'imp');
        }}

        function showImpSub(name) {{
            ['shipped','active','revisit','notpursuing'].forEach(function(s) {{
                var panel = document.getElementById('imp-sub-' + s);
                var btn   = document.getElementById('impsub-' + s);
                if (panel) panel.style.display = (s === name) ? 'block' : 'none';
                if (btn)   btn.classList.toggle('imp-sub-active', s === name);
            }});
        }}

        function showHome() {{
            hideAll();
            document.getElementById('home-panel').style.display = 'block';
            document.getElementById('btn-home').classList.add('active');
            document.getElementById('date-select').style.display = 'none';
            document.getElementById('day-label').style.display   = 'none';
            document.getElementById('interval-label').style.display = '';
            localStorage.setItem('activePanel', 'home');
        }}

        function showTicker(ticker) {{
            hideAll();
            document.querySelectorAll('.card').forEach(c => c.style.display = 'none');
            document.getElementById('card-' + ticker).style.display = 'block';
            document.getElementById('tab-' + ticker).classList.add('active');
            document.getElementById('chart-panel').style.display = 'block';
            document.getElementById('date-select').style.display = '';
            document.getElementById('day-label').style.display = '';
            document.getElementById('interval-label').style.display = '';
            localStorage.setItem('activePanel', ticker);
        }}

        function showGrad() {{
            hideAll();
            var grad = document.getElementById('grad-panel');
            if (grad) grad.style.display = 'block';
            document.getElementById('tab-grad').classList.add('active');
            document.getElementById('date-select').style.display = 'none';
            document.getElementById('day-label').style.display = 'none';
            document.getElementById('interval-label').style.display = 'none';
            localStorage.setItem('activePanel', 'grad');
            window.scrollTo(0, 0);
        }}

        function showPnL() {{
            hideAll();
            var pnl = document.getElementById('pnl-panel');
            if (pnl) pnl.style.display = 'block';
            document.getElementById('tab-pnl').classList.add('active');
            document.getElementById('date-select').style.display = 'none';
            document.getElementById('day-label').style.display = 'none';
            document.getElementById('interval-label').style.display = 'none';
            localStorage.setItem('activePanel', 'pnl');
            window.scrollTo(0, 0);
        }}

        function switchEx(n) {{
            document.getElementById('ex1-panel').style.display = n === 1 ? 'block' : 'none';
            document.getElementById('ex2-panel').style.display = n === 2 ? 'block' : 'none';
            document.getElementById('btn-ex1').classList.toggle('active', n === 1);
            document.getElementById('btn-ex2').classList.toggle('active', n === 2);
        }}

        function switchPnlTop(name) {{
            document.getElementById('pnl-tracker-view').style.display   = name === 'tracker'   ? 'block' : 'none';
            document.getElementById('pnl-breakdown-view').style.display = name === 'breakdown' ? 'block' : 'none';
            document.getElementById('btn-pnl-tracker').classList.toggle('active',   name === 'tracker');
            document.getElementById('btn-pnl-breakdown').classList.toggle('active', name === 'breakdown');
        }}

        function toggleSection(el) {{
            var body = el.nextElementSibling;
            var open = body.style.display !== 'none';
            body.style.display = open ? 'none' : 'block';
            el.textContent = (open ? '▶' : '▼') + ' Signals';
        }}

        function changeDate(dateStr) {{
            currentDate = dateStr;
            localStorage.setItem('activeDate', dateStr);
            Object.keys(charts).forEach(ticker => {{
                charts[ticker].destroy();
                charts[ticker] = buildChart(ticker, dateStr);
            }});
        }}

        function toggleChart(ticker) {{
            chartMode[ticker] = chartMode[ticker] === 'line' ? 'candlestick' : 'line';
            charts[ticker].destroy();
            charts[ticker] = buildChart(ticker, currentDate);
            var btn = document.getElementById('toggle-' + ticker);
            if (chartMode[ticker] === 'candlestick') {{
                btn.textContent = 'Line';
                btn.classList.add('active');
            }} else {{
                btn.textContent = 'Candlestick';
                btn.classList.remove('active');
            }}
        }}

        function resetZoom(ticker) {{ charts[ticker].resetZoom(); }}

        function toggleNotes(el) {{
            var body = el.nextElementSibling;
            var open = body.style.display !== 'none';
            body.style.display = open ? 'none' : 'block';
            el.querySelector('.notes-arrow').textContent = open ? '▶' : '▼';
            var dateEl = el.closest('.day-block').querySelector('.day-label-text');
            if (dateEl) {{
                var s = JSON.parse(localStorage.getItem('notesStates') || '{{}}');
                s[dateEl.textContent] = !open;
                localStorage.setItem('notesStates', JSON.stringify(s));
            }}
        }}

        function toggleDay(el) {{
            var body = el.nextElementSibling;
            var open = body.style.display !== 'none';
            body.style.display = open ? 'none' : 'block';
            el.querySelector('.day-arrow').textContent = open ? '▶' : '▼';
            var dateEl = el.querySelector('.day-label-text');
            if (dateEl) {{
                var s = JSON.parse(localStorage.getItem('dayStates') || '{{}}');
                s[dateEl.textContent] = !open;
                localStorage.setItem('dayStates', JSON.stringify(s));
            }}
        }}

        {charts_js}

        (function() {{
            // Restore active panel FIRST so the correct view appears immediately
            var saved = localStorage.getItem('activePanel');
            if (!saved || saved === 'home') {{ showHome(); }}
            else if (saved === 'grad') {{ showGrad(); }}
            else if (saved === 'imp') {{ showImprovements(); }}
            else if (saved === 'pnl') {{
                showPnL();
                var pnlScroll = localStorage.getItem('pnlScroll');
                if (pnlScroll) {{
                    setTimeout(function() {{
                        document.getElementById('pnl-panel').scrollTop = parseInt(pnlScroll);
                    }}, 50);
                }}
            }} else if (saved && document.getElementById('tab-' + saved)) {{
                showTicker(saved);
            }}
            // Rebuild charts for saved date (slower, runs after panel is already visible)
            var savedDate = localStorage.getItem('activeDate');
            if (savedDate) {{
                var opt = document.querySelector('#date-select option[value="' + savedDate + '"]');
                if (opt) {{
                    currentDate = savedDate;
                    document.getElementById('date-select').value = savedDate;
                    Object.keys(charts).forEach(function(ticker) {{
                        try {{
                            if (charts[ticker]) charts[ticker].destroy();
                            charts[ticker] = buildChart(ticker, savedDate);
                        }} catch(e) {{ console.error('Date rebuild failed (' + ticker + '):', e); }}
                    }});
                }}
            }}
            var pnlPanel = document.getElementById('pnl-panel');
            if (pnlPanel) {{
                pnlPanel.addEventListener('scroll', function() {{
                    localStorage.setItem('pnlScroll', pnlPanel.scrollTop);
                }});
            }}
            var dayStates   = JSON.parse(localStorage.getItem('dayStates')   || '{{}}');
            var notesStates = JSON.parse(localStorage.getItem('notesStates') || '{{}}');
            document.querySelectorAll('.day-block').forEach(function(block) {{
                var dateEl = block.querySelector('.day-label-text');
                if (!dateEl) return;
                var date   = dateEl.textContent;
                var toggle = block.querySelector('.day-toggle');
                var body   = block.querySelector('.day-body');
                if (date in dayStates) {{
                    var open = dayStates[date];
                    body.style.display = open ? 'block' : 'none';
                    toggle.querySelector('.day-arrow').textContent = open ? '▼' : '▶';
                }}
                var notesHeader = block.querySelector('.notes-header');
                var notesBody   = block.querySelector('.notes-body');
                if (notesHeader && notesBody && date in notesStates) {{
                    var nOpen = notesStates[date];
                    notesBody.style.display = nOpen ? 'block' : 'none';
                    notesHeader.querySelector('.notes-arrow').textContent = nOpen ? '▼' : '▶';
                }}
            }});
            var cdEl = document.getElementById('countdown');
            function updateCountdown() {{
                var now  = new Date();
                var secs = 60 - now.getSeconds();
                if (cdEl) cdEl.textContent = secs === 60 ? 0 : secs;
            }}
            updateCountdown();
            setInterval(updateCountdown, 1000);
            (function scheduleReload() {{
                var now        = new Date();
                var msLeft     = (60 - now.getSeconds()) * 1000 - now.getMilliseconds();
                setTimeout(function() {{ location.reload(); }}, msLeft);
            }})();
        }})();
    </script>
</body>
</html>"""


if __name__ == "__main__":
    print(f"Fetching data for: {', '.join(TICKERS)}")
    key, secret = _load_creds()
    client = StockHistoricalDataClient(api_key=key, secret_key=secret)
    assets = []
    for ticker in TICKERS:
        print(f"  Downloading {ticker}...")
        result = fetch(ticker, client)
        if result:
            assets.append(result)

    if not assets:
        print("No data fetched. Check your internet connection.")
    else:
        html = build_dashboard(assets)
        out = os.path.join(os.path.dirname(__file__) or ".", "dashboard.html")
        with open(out, "w") as f:
            f.write(html)
        print(f"\nDone! Open Signal/dashboard.html in your browser.")
        print(f"Signals flagged when price moves >1% or volume spikes >2x average.")
