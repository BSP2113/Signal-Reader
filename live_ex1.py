"""
live_ex1.py — Real-money executor for the Signal Reader EX1 strategy.

This is the live runner. Where ex1.py simulates trades against historical bars,
this module runs in real time during market hours and places actual orders via
broker.py. EX1 LOGIC ONLY — no PM_ORB, no afternoon breakouts, no re-entries.
Cash account constraints (see CLAUDE.md "Live Trading Constraints") forbid
those EX2 features.

Today's scope (Sat 2026-05-09):
  ✓ Polling loop, market-hours guard
  ✓ Bar accumulation per ticker (closes/highs/lows/volumes/times arrays)
  ✓ Signal detection via ex1.find_all_trades
  ✓ Entry placement (market buy) with native stop-loss attached
  ✓ Native take-profit limit attached on MAYBE-rated entries
  ✓ Position state in-memory + persisted to live_state.json (crash recovery)
  ✓ Telegram alert on every order

Tomorrow's scope (Sun 2026-05-10):
  ☐ Custom exit polling: trail with 2-bar arm, T+45 weakness, T+90 no-progress
  ☐ 14:00 hard time-close (cancel + market sell all)
  ☐ Daily loss limit kill switch (-$75)
  ☐ EOD reconciliation script
  ☐ Full paper-trading dry run

Run paper Monday, real Tuesday/Wednesday after paper validates clean.

Usage:
  venv/bin/python3 live_ex1.py
"""

import os
import sys
import json
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame

import ex1
import broker
import alerts


BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
ET          = "America/New_York"
STATE_FILE  = os.path.join(BASE_DIR, "live_state.json")
TRADES_FILE = os.path.join(BASE_DIR, "trades_live.json")

# How often to poll for new bars during the session (seconds).
POLL_SECONDS = 30

# Phase 1 — sub-bar exit polling. When True, open positions are checked for
# trailing-stop and time-close triggers every EXIT_POLL_SECONDS via REST quote
# snapshots, instead of waiting for the next 30s bar tick. Reduces give-back
# on trail exits (each trail trigger can give back 0.3-0.5% in 30 seconds).
# EARLY_WEAK and NO_PROGRESS are minute-checkpointed and stay on bar cadence.
EXIT_POLL_REALTIME = True
EXIT_POLL_SECONDS  = 2


# ── State (in-memory; persisted to STATE_FILE on every change) ────────────────
state = {
    "session_date":    None,             # "YYYY-MM-DD"
    "market_state":    "neutral",        # bull/neut/bear
    "starting_cash":   0.0,
    "open_positions":  {},               # ticker → position dict
    "session_pnl":     0.0,
    "halted":          False,            # daily loss limit tripped
    "completed_trades": [],
}


def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def load_state():
    """Load state from disk if it exists AND matches today. Otherwise start fresh."""
    global state
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            saved = json.load(f)
        if saved.get("session_date") == today:
            state = saved
            print(f"[state] resumed session for {today} "
                  f"({len(state['open_positions'])} open positions)")
            return
    state["session_date"] = today
    print(f"[state] fresh session for {today}")


# ── Market state ──────────────────────────────────────────────────────────────
def read_market_state() -> str:
    """Read today's market state (bull/neut/bear) from market_state.json,
    written by market_check.py at 9:20am via cron."""
    path = os.path.join(BASE_DIR, "market_state.json")
    try:
        with open(path) as f:
            return json.load(f).get("state", "neutral")
    except Exception:
        return "neutral"


# ── Bar fetching ──────────────────────────────────────────────────────────────
def _data_client():
    key, secret = ex1._load_creds()
    return StockHistoricalDataClient(api_key=key, secret_key=secret)


def fetch_today_bars(client, tickers: list[str]) -> dict:
    """Pull today's 1-min bars for each ticker through the most recent available
    bar. Returns dict of ticker → {closes,highs,lows,volumes,times}."""
    today    = datetime.now().strftime("%Y-%m-%d")
    start_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = start_dt + timedelta(days=1)

    out = {}
    for t in tickers:
        try:
            bars = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=t, timeframe=TimeFrame.Minute,
                start=start_dt, end=end_dt, feed="iex",
            ))
            df = bars.df
            if df.empty:
                continue
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(t, level=0)
            df    = df.tz_convert(ET)
            today = df.between_time("09:30", "15:59")
            if today.empty:
                continue
            out[t] = {
                "closes":  [round(float(v), 2) for v in today["close"].tolist()],
                "highs":   [round(float(v), 2) for v in today["high"].tolist()],
                "lows":    [round(float(v), 2) for v in today["low"].tolist()],
                "volumes": [int(v) for v in today["volume"].tolist()],
                "times":   [t.strftime("%H:%M") for t in today.index],
            }
        except Exception as e:
            print(f"[bars] {t} fetch failed: {e}")
    return out


def fetch_spy_intraday(client) -> dict:
    """SPY by-time map for the relative-strength gate."""
    today    = datetime.now().strftime("%Y-%m-%d")
    start_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = start_dt + timedelta(days=1)
    spy_by_time = {}
    try:
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols="SPY", timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        ))
        df = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs("SPY", level=0)
        df = df.tz_convert(ET).between_time("09:30", "15:59")
        for t, row in df.iterrows():
            spy_by_time[t.strftime("%H:%M")] = row["close"]
    except Exception as e:
        print(f"[bars] SPY fetch failed: {e}")
    return spy_by_time


def fetch_latest_quotes(client, tickers: list[str]) -> dict:
    """Single batched REST snapshot for current bid/ask. Returns dict of
    ticker → midpoint price. Used by realtime exit polling (Phase 1).

    Falls back to ask, then bid, if midpoint can't be computed. Empty dict on
    fetch failure — caller treats missing tickers as "no update this tick."
    """
    if not tickers:
        return {}
    out = {}
    try:
        quotes = client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=tickers, feed="iex")
        )
        for t, q in quotes.items():
            bid = float(q.bid_price or 0)
            ask = float(q.ask_price or 0)
            if bid > 0 and ask > 0:
                out[t] = round((bid + ask) / 2, 4)
            elif ask > 0:
                out[t] = round(ask, 4)
            elif bid > 0:
                out[t] = round(bid, 4)
    except Exception as e:
        print(f"[quotes] fetch failed: {e}")
    return out


def fetch_prior_close(client, ticker: str) -> Optional[float]:
    """Yesterday's close — used for gap_pct calc on GAP_GO."""
    today    = datetime.now().strftime("%Y-%m-%d")
    start_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    try:
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=ticker, timeframe=TimeFrame.Day,
            start=start_dt - timedelta(days=10), end=start_dt, feed="iex",
        )).data.get(ticker, [])
        if bars:
            return float(bars[-1].close)
    except Exception:
        pass
    return None


# ── Allocation ────────────────────────────────────────────────────────────────
def calc_allocation(rating: str, market_state: str, atr_modifier: float = 1.0) -> float:
    """Mirror of ex1's allocation math. Uses LIVE account starting cash."""
    bal = state["starting_cash"]
    if market_state == "bullish":
        pct = ex1.ALLOC_PCT_BULL[rating]
    elif market_state == "bearish":
        pct = ex1.ALLOC_PCT_BEAR[rating]
    else:
        pct = ex1.ALLOC_PCT_NEUT[rating]
    base = round(bal * pct, 2)
    return round(base * atr_modifier, 2)


# ── Entry handling ────────────────────────────────────────────────────────────
def already_in(ticker: str) -> bool:
    return ticker in state["open_positions"]


def place_entry(ticker: str, signal: str, rating: str, entry_price: float,
                dollars: float, signal_time: str | None = None):
    """Submit market buy + attach native stop-loss + (MAYBE only) take-profit.

    signal_time: the bar time when the signal fired (e.g. "09:31"). Used as the
    position's entry_time so T+45 / T+90 timing math is anchored to the signal,
    not wall-clock. Falls back to wall clock if not provided."""
    bar_time = signal_time or datetime.now().strftime("%H:%M")
    print(f"[entry] {ticker} {signal} {rating} @ ${entry_price:.2f}  size=${dollars:.2f}  (bar {bar_time})")

    # 1. Market buy
    try:
        order = broker.market_buy(ticker, dollars)
    except Exception as e:
        msg = f"market_buy {ticker}: {e}"
        print(f"[entry] FAIL {msg}")
        alerts.error("entry order failed", msg)
        return

    # 2. Wait briefly for fill so we can read actual qty + avg price
    time.sleep(2)
    pos = broker.position(ticker)
    if pos is None:
        msg = f"market_buy {ticker} submitted but no position visible after 2s"
        print(f"[entry] WARN {msg}")
        alerts.error("entry fill missing", msg)
        return

    actual_entry = pos.avg_entry
    qty          = pos.qty

    # 3. Attach native stop-loss (-1.5% from actual fill)
    stop_price = round(actual_entry * (1 - ex1.STOP_LOSS), 2)
    try:
        broker.attach_stop_loss(ticker, qty, stop_price)
    except Exception as e:
        msg = f"stop attach {ticker}: {e}"
        print(f"[entry] FAIL {msg}")
        alerts.error("stop attach failed", msg)

    # 4. Attach take-profit (+3%) for MAYBE only — TAKE rated trades have no cap
    tp_price = None
    if rating != "TAKE":
        tp_price = round(actual_entry * (1 + ex1.TAKE_PROFIT), 2)
        try:
            broker.attach_take_profit(ticker, qty, tp_price)
        except Exception as e:
            msg = f"TP attach {ticker}: {e}"
            print(f"[entry] FAIL {msg}")
            alerts.error("TP attach failed", msg)

    # 5. Record in state
    state["open_positions"][ticker] = {
        "ticker":       ticker,
        "signal":       signal,
        "rating":       rating,
        "entry_time":   bar_time,
        "entry_price":  actual_entry,
        "qty":          qty,
        "stop_price":   stop_price,
        "tp_price":     tp_price,
        "peak":         actual_entry,
        "trail_armed":  False,
        "consec_above_lock": 0,
        # placeholders for tomorrow's exit polling
        "exit_filed":   False,
    }
    save_state()
    alerts.entry(ticker, signal, rating, actual_entry, dollars, stop_price, tp_price)


def check_for_signals(client, ticker_data: dict, spy_by_time: dict,
                      prior_closes: dict, atr_modifier: dict,
                      prior_avg_vols: dict | None = None):
    """For each ticker without an open position, run signal detection.

    prior_avg_vols: per-ticker estimate of "normal" per-minute volume (typically
    derived from yesterday's daily volume / 390). Passed as avg_vol_override to
    find_all_trades so GAP_GO scoring isn't biased by the tiny in-session sample
    during the first 10 minutes. Critical for catching morning gap entries."""
    if state["halted"]:
        return

    prior_avg_vols = prior_avg_vols or {}

    for ticker in ex1.TICKERS:
        if already_in(ticker):
            continue
        td = ticker_data.get(ticker)
        # GAP_GO can fire from bar 1; ORB needs ORB_BARS+1. So we only require
        # at least one bar — find_all_trades handles the per-signal length check.
        if td is None or len(td["closes"]) < 1:
            continue

        prior_close = prior_closes.get(ticker)
        gap_pct  = 0.0
        skip_orb = False
        if prior_close and td["closes"]:
            gap_pct  = (td["closes"][0] - prior_close) / prior_close
            skip_orb = abs(gap_pct) > ex1.GAP_FILTER

        trades = ex1.find_all_trades(
            td["closes"], td["highs"], td["lows"], td["volumes"], td["times"],
            skip_orb=skip_orb, spy_by_time=spy_by_time, gap_pct=gap_pct, ticker=ticker,
            avg_vol_override=prior_avg_vols.get(ticker),
        )
        if not trades:
            continue

        entry, _exit = trades[0]
        # Only act if the signal fired on the most recent bar (i.e., it's actionable now)
        # — older bars are historical and would be stale entries.
        latest_time = td["times"][-1]
        if entry["time"] != latest_time:
            continue

        modifier = atr_modifier.get(ticker, 1.0)
        dollars  = calc_allocation(entry["rating"], state["market_state"], modifier)

        # Capital check before placing the order
        bp = broker.settled_cash()
        if dollars > bp:
            print(f"[entry] {ticker} skipped — need ${dollars:.2f} but only ${bp:.2f} available")
            continue

        place_entry(ticker, entry["signal"], entry["rating"], entry["price"], dollars,
                    signal_time=entry["time"])


# ── Exit logic ────────────────────────────────────────────────────────────────
def _bars_since_entry(td: dict, entry_time: str) -> tuple[list, list]:
    """Slice the ticker's bars to those at or after entry_time. Returns
    (closes, times) — the only two things our exit logic needs."""
    times  = td["times"]
    closes = td["closes"]
    try:
        entry_idx = times.index(entry_time)
    except ValueError:
        # entry_time not in the array yet (very recent entry, bar not closed); skip
        return [], []
    return closes[entry_idx:], times[entry_idx:]


def evaluate_position_exit(pos: dict, td: dict) -> Optional[dict]:
    """Run the full custom-exit ladder against a position's bars.
    Returns {"reason": str, "price": float, "time": str} on trigger, or None.

    Native stop and TP are handled by the broker, so we don't check those here.
    What we DO check (in priority order, matching ex1.find_exit):
      1. TIME_CLOSE (14:00)            ← also handled by main loop sweep
      2. TRAILING_STOP (with 2-bar arm rule)
      3. NO_PROGRESS (T+90)
      4. EARLY_WEAK (T+45 if not in skip list)
    """
    closes, times = _bars_since_entry(td, pos["entry_time"])
    if len(closes) < 2:
        return None  # need at least one bar after entry to evaluate

    entry_price = pos["entry_price"]
    ticker      = pos["ticker"]
    lock_level  = entry_price * (1 + ex1.TRAIL_LOCK)

    # Recompute peak + trail-arm state. Seed peak with any prior value (which
    # may include realtime sub-bar ticks above the latest bar close — we don't
    # want to clobber those) and let bar closes lift it further.
    peak         = max(entry_price, pos.get("peak", entry_price))
    consec_above = 0
    trail_armed  = False
    for i in range(1, len(closes)):
        price = closes[i]
        peak  = max(peak, price)
        if price >= lock_level:
            consec_above += 1
        else:
            consec_above = 0
        if consec_above >= 2:
            trail_armed = True

    # Persist updated trail state back to the position dict (for diagnostics)
    pos["peak"]              = round(peak, 2)
    pos["trail_armed"]       = trail_armed
    pos["consec_above_lock"] = consec_above

    # Current bar / time
    latest_price = closes[-1]
    latest_time  = times[-1]

    # 1. 14:00 time close
    if latest_time >= ex1.ENTRY_CLOSE:
        return {"reason": "TIME_CLOSE", "price": latest_price, "time": latest_time}

    # 2. Trailing stop (only if armed)
    if trail_armed and latest_price <= peak * (1 - ex1.TRAIL_STOP):
        return {"reason": "TRAILING_STOP", "price": latest_price, "time": latest_time}

    # 3. NO_PROGRESS at T+90 (if 14:00 hasn't already arrived)
    entry_mins  = int(pos["entry_time"][:2]) * 60 + int(pos["entry_time"][3:])
    latest_mins = int(latest_time[:2])      * 60 + int(latest_time[3:])
    age_mins    = latest_mins - entry_mins
    if age_mins >= ex1.NO_PROGRESS_MINS and latest_mins <= 14 * 60:
        if latest_price <= entry_price:
            return {"reason": "NO_PROGRESS", "price": latest_price, "time": latest_time}

    # 4. EARLY_WEAK at T+45 (skipping TSLA / PLTR per production rule)
    if ticker not in ex1.EARLY_WEAK_SKIP and age_mins >= ex1.EARLY_WEAK_MINS:
        if latest_price < entry_price:
            # confirm still trending down: latest < close[i-5]
            entry_idx_in_full = td["times"].index(pos["entry_time"])
            latest_idx        = len(td["closes"]) - 1
            lookback_idx      = max(entry_idx_in_full + 1, latest_idx - ex1.EARLY_WEAK_LOOKBACK)
            if lookback_idx < latest_idx:
                if latest_price < td["closes"][lookback_idx]:
                    return {"reason": "EARLY_WEAK", "price": latest_price, "time": latest_time}

    return None


def execute_exit(ticker: str, reason: str, expected_price: float,
                 bar_time: str | None = None):
    """Cancel any open broker orders for the ticker, place market sell,
    update state, log, and alert. Always runs even if some sub-steps fail —
    the goal is to FLATTEN the position safely.

    bar_time: the bar time when the exit triggered (e.g. "14:00"). Used as
    the trade record's exit_time so logs/comparisons are bar-aligned. Falls
    back to wall clock if not provided."""
    pos = state["open_positions"].get(ticker)
    if pos is None:
        return
    exit_time = bar_time or datetime.now().strftime("%H:%M")

    # 1. Cancel any pending stop / TP orders for this ticker
    try:
        for o in broker.open_orders(ticker):
            broker.cancel_order(o["order_id"])
    except Exception as e:
        print(f"[exit] {ticker}: cancel pending orders failed: {e}")

    # 2. Market sell the entire position
    try:
        broker.market_sell_position(ticker)
    except Exception as e:
        msg = f"market sell {ticker}: {e}"
        print(f"[exit] FAIL {msg}")
        alerts.error("exit sell failed", msg)
        # Don't pop from state — we still own this; needs manual attention.
        return

    # 3. Wait briefly for fill, fetch realized fill price
    time.sleep(2)
    fill_price = expected_price
    try:
        # Most recent SELL fill for this ticker today
        recent = broker.closed_orders(symbols=[ticker], limit=5)
        for o in reversed(recent):  # newest first
            if o["side"].upper().endswith("SELL") and o["filled_price"]:
                fill_price = float(o["filled_price"])
                break
    except Exception:
        pass

    # 4. Compute realized P&L + give-back metric.
    # give_back = (trigger_price - fill_price) * qty
    #   positive → we lost $ to slippage between trigger detection and fill
    #   negative → price moved in our favor between trigger and fill
    # Used to decide whether the 1s exit polling upgrade is worth it
    # (target: <$3/trade = no upgrade; $3-$10 = schedule; $10+ = priority).
    pnl_dollars = round((fill_price - pos["entry_price"]) * pos["qty"], 2)
    pnl_pct     = round((fill_price - pos["entry_price"]) / pos["entry_price"] * 100, 2)
    give_back   = round((expected_price - fill_price) * pos["qty"], 2)

    # 5. Update state
    state["session_pnl"] = round(state["session_pnl"] + pnl_dollars, 2)
    state["completed_trades"].append({
        "ticker":        ticker,
        "signal":        pos["signal"],
        "rating":        pos["rating"],
        "entry_time":    pos["entry_time"],
        "exit_time":     exit_time,
        "entry_price":   pos["entry_price"],
        "exit_price":    fill_price,
        "trigger_price": expected_price,
        "give_back":     give_back,
        "qty":           pos["qty"],
        "exit_reason":   reason,
        "pnl":           pnl_dollars,
        "pnl_pct":       pnl_pct,
    })
    state["open_positions"].pop(ticker, None)

    # 6. Daily loss limit
    if state["session_pnl"] <= ex1.DAY_LOSS_LIMIT:
        state["halted"] = True
        print(f"[halt] daily loss limit hit ({state['session_pnl']:.2f}) — no new entries")
        alerts.info(f"⚠️ Daily loss limit hit (${state['session_pnl']:.2f}). No new entries today.")

    save_state()
    print(f"[exit] {ticker} {reason}  ${pos['entry_price']:.2f}→${fill_price:.2f}  "
          f"P&L ${pnl_dollars:+.2f} ({pnl_pct:+.2f}%)")
    alerts.position_exit(ticker, reason, pos["entry_price"], fill_price,
                         pnl_dollars, pnl_pct)


def check_exits(ticker_data: dict):
    """Per-position exit evaluation. Called every poll cycle."""
    for ticker in list(state["open_positions"].keys()):
        pos = state["open_positions"][ticker]
        td  = ticker_data.get(ticker)
        latest_bar_time = td["times"][-1] if td and td.get("times") else None

        # If broker shows position gone, the native stop or TP filled.
        # Reconcile: pull recent fills, log it, remove from state.
        broker_pos = broker.position(ticker)
        if broker_pos is None or broker_pos.qty == 0:
            _reconcile_broker_closed(ticker, bar_time=latest_bar_time)
            continue

        if td is None:
            continue

        # Otherwise check our custom exits.
        result = evaluate_position_exit(pos, td)
        if result:
            execute_exit(ticker, result["reason"], result["price"],
                         bar_time=result.get("time"))

    # Persist any peak/trail_armed updates from evaluate_position_exit
    save_state()


def check_exits_realtime(data_client):
    """Phase 1 sub-bar exit polling. Pulls a fresh REST quote snapshot for all
    open tickers and checks the two exits that benefit from sub-bar reaction
    time:
      - TRAILING_STOP — only if trail_armed (arming still requires 2 bar
        closes >= entry+1%, set on the minute cadence by evaluate_position_exit)
      - TIME_CLOSE   — fires the second 14:00 lands instead of waiting for
        the next 30s bar tick

    EARLY_WEAK and NO_PROGRESS are NOT checked here — they trigger at minute
    checkpoints and depend on bar-aligned data (close[i-5]).

    The peak high-water mark is updated incrementally so 1s ticks above the
    last bar close are captured. evaluate_position_exit takes max(bar_peak,
    pos['peak']) so updates aren't lost across the two cadences.
    """
    if not state["open_positions"]:
        return

    tickers_open = list(state["open_positions"].keys())
    quotes = fetch_latest_quotes(data_client, tickers_open)
    if not quotes:
        return

    now = datetime.now()
    now_minute = now.strftime("%H:%M")
    time_close_active = now_minute >= ex1.ENTRY_CLOSE

    for ticker in list(state["open_positions"].keys()):
        pos = state["open_positions"].get(ticker)
        if pos is None:
            continue
        price = quotes.get(ticker)
        if not price:
            continue

        # Update peak with the new tick (max() so concurrent bar-cadence
        # updates aren't clobbered)
        pos["peak"] = max(pos.get("peak", pos["entry_price"]), price)

        # 1. TIME_CLOSE — flat the position the second 14:00 hits
        if time_close_active:
            execute_exit(ticker, "TIME_CLOSE", price, bar_time=now_minute)
            continue

        # 2. TRAILING_STOP — only if armed by the minute cadence
        if pos.get("trail_armed") and price <= pos["peak"] * (1 - ex1.TRAIL_STOP):
            execute_exit(ticker, "TRAILING_STOP", price, bar_time=now_minute)
            continue

    save_state()


def _reconcile_broker_closed(ticker: str, bar_time: str | None = None):
    """The broker filled our native stop or TP — figure out which, log + alert."""
    pos = state["open_positions"].pop(ticker, None)
    if pos is None:
        return
    exit_time  = bar_time or datetime.now().strftime("%H:%M")
    fill_price = pos["entry_price"]
    reason     = "BROKER_FILL"
    try:
        recent = broker.closed_orders(symbols=[ticker], limit=5)
        for o in reversed(recent):  # newest first
            side = o["side"].upper()
            if "SELL" in side and o["filled_price"]:
                fill_price = float(o["filled_price"])
                otype = o["type"].upper()
                if "STP" in otype or "STOP" in otype:
                    reason = "STOP_LOSS"
                elif "LMT" in otype or "LIMIT" in otype:
                    reason = "TAKE_PROFIT"
                break
    except Exception:
        pass

    pnl_dollars = round((fill_price - pos["entry_price"]) * pos["qty"], 2)
    pnl_pct     = round((fill_price - pos["entry_price"]) / pos["entry_price"] * 100, 2)
    state["session_pnl"] = round(state["session_pnl"] + pnl_dollars, 2)
    state["completed_trades"].append({
        "ticker":      ticker,
        "signal":      pos["signal"],
        "rating":      pos["rating"],
        "entry_time":  pos["entry_time"],
        "exit_time":   exit_time,
        "entry_price": pos["entry_price"],
        "exit_price":  fill_price,
        "qty":         pos["qty"],
        "exit_reason": reason,
        "pnl":         pnl_dollars,
        "pnl_pct":     pnl_pct,
    })

    # Cancel any sibling order still open (e.g., TP open after STOP fired)
    try:
        for o in broker.open_orders(ticker):
            broker.cancel_order(o["order_id"])
    except Exception:
        pass

    if state["session_pnl"] <= ex1.DAY_LOSS_LIMIT:
        state["halted"] = True
        print(f"[halt] daily loss limit hit ({state['session_pnl']:.2f})")
        alerts.info(f"⚠️ Daily loss limit hit (${state['session_pnl']:.2f}). No new entries today.")

    save_state()
    print(f"[exit] {ticker} {reason} (broker fill) P&L ${pnl_dollars:+.2f}")
    alerts.position_exit(ticker, reason, pos["entry_price"], fill_price,
                         pnl_dollars, pnl_pct)


# ── Hard time-close at 14:00 ──────────────────────────────────────────────────
def time_close_all():
    """At/after ENTRY_CLOSE: cancel every open order and market-sell every
    position the system is tracking. Idempotent — safe to call repeatedly."""
    if not state["open_positions"]:
        return
    print(f"[time_close] sweeping {len(state['open_positions'])} positions")

    # Belt-and-suspenders: also cancel any account-wide open orders
    try:
        n = broker.cancel_all_open_orders()
        if n:
            print(f"[time_close] cancelled {n} broker orders")
    except Exception as e:
        print(f"[time_close] cancel_all failed: {e}")

    for ticker in list(state["open_positions"].keys()):
        pos = state["open_positions"][ticker]
        # Use latest known price as the expected_price; actual fill is read back
        execute_exit(ticker, "TIME_CLOSE", pos.get("peak", pos["entry_price"]),
                     bar_time=ex1.ENTRY_CLOSE)


# ── Session boundaries ────────────────────────────────────────────────────────
def market_open_now() -> bool:
    """True between 9:30 and 16:00 ET on a weekday."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    if (h, m) < (9, 30):
        return False
    if h >= 16:
        return False
    return True


def in_entry_window() -> bool:
    """True between 9:30 and ENTRY_CLOSE (14:00). New entries can only fire here."""
    now = datetime.now()
    h, m = now.hour, now.minute
    if (h, m) < (9, 30):
        return False
    if (h, m) >= tuple(int(x) for x in ex1.ENTRY_CLOSE.split(":")):
        return False
    return True


def session_setup():
    """First-call-of-the-day initialization: read market state, snapshot starting cash.

    Paper accounts come with $100K virtual cash and 2x margin buying power, but
    we want position sizes that match the real $5K plan so paper Monday is a
    faithful test of Tuesday's live behavior. Cap paper to ex1.BUDGET; live
    uses actual settled cash (which compounds over time as P&L accrues)."""
    state["market_state"] = read_market_state()
    actual_cash = broker.settled_cash()
    if broker.IS_PAPER:
        state["starting_cash"] = min(actual_cash, ex1.BUDGET)
        print(f"[session] paper account: capping sizing at ex1.BUDGET=${ex1.BUDGET:,.2f} "
              f"(actual paper buying power: ${actual_cash:,.2f})")
    else:
        state["starting_cash"] = actual_cash
    save_state()
    print(f"[session] market={state['market_state']}  starting_cash=${state['starting_cash']:,.2f}")
    alerts.session_open(state["market_state"], state["starting_cash"])


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    print("="*60)
    print(f"Signal Reader Live — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Broker: {'PAPER' if broker.IS_PAPER else 'LIVE'}")
    print("="*60)

    load_state()
    if state["starting_cash"] == 0.0:
        session_setup()

    data_client = _data_client()

    # Fetch prior closes once for the day (for GAP_GO detection)
    print("[setup] fetching prior closes for GAP_GO...")
    prior_closes = {}
    for t in ex1.TICKERS:
        pc = fetch_prior_close(data_client, t)
        if pc:
            prior_closes[t] = pc
    print(f"[setup] {len(prior_closes)}/{len(ex1.TICKERS)} prior closes loaded")

    # ATR modifier: pulled per-day from prior 14 daily bars (reuse ex1 logic)
    atr_modifier = _build_atr_modifier(data_client)
    print(f"[setup] ATR modifiers: {len(atr_modifier)} tickers")

    # Per-ticker prior-day avg per-minute volume (no-lookahead estimate of
    # "normal" volume). Used as avg_vol_override in find_all_trades so GAP_GO
    # scoring is meaningful in the first 10 minutes of the session.
    prior_avg_vols = _build_prior_avg_vols(data_client)
    print(f"[setup] prior avg vols: {len(prior_avg_vols)} tickers")

    if EXIT_POLL_REALTIME:
        print(f"\n[loop] bar cadence={POLL_SECONDS}s, exit cadence={EXIT_POLL_SECONDS}s. "
              f"Press Ctrl-C to stop cleanly.")
    else:
        print(f"\n[loop] polling every {POLL_SECONDS}s. Press Ctrl-C to stop cleanly.")

    last_bar_tick = 0.0  # epoch seconds of last minute-cadence work
    while True:
        try:
            if not market_open_now():
                print(f"[loop] outside market hours, sleeping 60s")
                time.sleep(60)
                continue

            now_epoch = time.time()
            do_bar_work = (now_epoch - last_bar_tick) >= POLL_SECONDS

            if do_bar_work:
                ticker_data = fetch_today_bars(data_client, ex1.TICKERS)
                spy_by_time = fetch_spy_intraday(data_client)

                if in_entry_window() and not state["halted"]:
                    check_for_signals(data_client, ticker_data, spy_by_time,
                                      prior_closes, atr_modifier, prior_avg_vols)

                # Hard time-close at 14:00 — runs BEFORE check_exits so positions
                # still in state get force-closed instead of slipping past.
                now = datetime.now()
                if (now.hour, now.minute) >= tuple(int(x) for x in ex1.ENTRY_CLOSE.split(":")):
                    time_close_all()

                check_exits(ticker_data)

                # End-of-session summary at 14:05+ when nothing is open
                if (now.hour, now.minute) >= (14, 5) and not state["open_positions"]:
                    if not state.get("session_closed_alerted"):
                        n_trades = len(state["completed_trades"])
                        n_wins   = sum(1 for t in state["completed_trades"] if t["pnl"] > 0)
                        end_cash = broker.settled_cash()
                        alerts.session_close(state["session_pnl"], n_trades, n_wins, end_cash)
                        state["session_closed_alerted"] = True
                        save_state()
                        print(f"[session] CLOSE  pnl=${state['session_pnl']:+.2f}  "
                              f"{n_trades} trades ({n_wins} wins)")

                    # Self-terminate after 14:30 so cron can run reconciliation cleanly.
                    # By then time-close has fired, all positions are flat, alert is sent.
                    if (now.hour, now.minute) >= (14, 30):
                        print("[loop] EOD complete — exiting cleanly for reconciliation")
                        return

                # Heartbeat (bar cadence only — sub-bar would be too noisy)
                now_str = datetime.now().strftime("%H:%M:%S")
                n_open  = len(state["open_positions"])
                halt    = " [HALTED]" if state["halted"] else ""
                print(f"[loop] {now_str}  open={n_open}  pnl=${state['session_pnl']:+.2f}{halt}")

                last_bar_tick = now_epoch

            # Sub-bar exit polling (Phase 1). Cheap REST snapshot only when
            # positions are open. Trail/time-close fire here without waiting
            # for the next 30s bar tick.
            if EXIT_POLL_REALTIME and state["open_positions"] and not state["halted"]:
                check_exits_realtime(data_client)
                time.sleep(EXIT_POLL_SECONDS)
            else:
                # No open positions (or realtime disabled): sleep until the
                # next bar tick is due, capped at POLL_SECONDS.
                remaining = POLL_SECONDS - (time.time() - last_bar_tick)
                time.sleep(max(1.0, min(POLL_SECONDS, remaining)))

        except KeyboardInterrupt:
            print("\n[loop] interrupted — saving state, exiting cleanly")
            save_state()
            return
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[loop] EXCEPTION: {e}\n{tb}")
            alerts.error("loop exception", f"{e}\n\n{tb}")
            time.sleep(POLL_SECONDS)


def _build_prior_avg_vols(client) -> dict:
    """For each ticker, estimate "normal" per-minute volume from yesterday's
    daily bar. Daily volume / 390 minutes ≈ average bar volume during a
    regular session. Used as a no-lookahead avg_vol baseline so the GAP_GO
    volume floor (1.0x) is meaningful in the first few minutes of the day."""
    today    = datetime.now().strftime("%Y-%m-%d")
    start_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    out = {}
    for t in ex1.TICKERS:
        try:
            bars = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=t, timeframe=TimeFrame.Day,
                start=start_dt - timedelta(days=10), end=start_dt, feed="iex",
            )).data.get(t, [])
            if bars:
                # Average across the prior 5 trading days (more stable than 1)
                recent = bars[-5:]
                avg_daily_vol = sum(b.volume for b in recent) / len(recent)
                out[t] = avg_daily_vol / 390  # ~minutes per session
        except Exception:
            pass
    return out


def _build_atr_modifier(client) -> dict:
    """Reuse ex1.calc_atr_pct on prior 14 daily bars per ticker."""
    import statistics as _stats
    today    = datetime.now().strftime("%Y-%m-%d")
    start_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    atr_pcts = {}
    for t in ex1.TICKERS:
        try:
            bars = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=t, timeframe=TimeFrame.Day,
                start=start_dt - timedelta(days=21), end=start_dt, feed="iex",
            )).data.get(t, [])
            if bars:
                val = ex1.calc_atr_pct(bars)
                if val:
                    atr_pcts[t] = val
        except Exception:
            pass
    if len(atr_pcts) < 2:
        return {}
    med = _stats.median(atr_pcts.values())
    return {
        t: round(min(ex1.ATR_MAX_MOD, max(ex1.ATR_MIN_MOD, med / atr_pcts[t])), 3)
        for t in atr_pcts
    }


if __name__ == "__main__":
    main()
