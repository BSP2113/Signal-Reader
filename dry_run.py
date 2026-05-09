"""
dry_run.py — Replay a historical date through live_ex1's code paths with the
broker module mocked. Validates the live runner without placing any orders.

What it tests:
  ✓ Bar fetching (real Alpaca historical data)
  ✓ Signal detection (live_ex1 → ex1.find_all_trades)
  ✓ Allocation math (ATR modifier, market state, sizing)
  ✓ Entry decision logic (capital check, halt flag)
  ✓ Custom exit logic (trail with 2-bar arm, T+45, T+90, time-close)
  ✓ Native stop / TP simulation (broker watches every tick on real Alpaca; we
     simulate by checking if intraday low/high pierces the stop/TP price)
  ✓ Daily loss limit kill switch
  ✓ State persistence to live_state.json (run uses a separate dry_state.json)

What it does NOT test:
  ✗ Real Alpaca order placement / fill timing / partial fills
  ✗ Network failure handling
  ✗ Telegram delivery (alerts print to stdout instead)

After replay, the dry-run trades are diffed against exercises.json[DATE] EX1.
A clean run = same tickers, same times, same exit reasons, similar P&L.

Usage:
    venv/bin/python3 dry_run.py 2026-05-08
    venv/bin/python3 dry_run.py 2026-04-30  --verbose
"""

import os
import sys
import json
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

import ex1


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ── Mock broker state ────────────────────────────────────────────────────────
# All in-memory; never touches real Alpaca. Resets per dry-run.
@dataclass
class MockOrder:
    order_id:   str
    ticker:     str
    side:       str           # "BUY" / "SELL"
    type:       str           # "MARKET" / "STOP" / "LIMIT"
    qty:        float
    price:      Optional[float] = None       # filled price for market; None until fill
    stop_price: Optional[float] = None
    limit_price: Optional[float] = None
    status:     str           = "OPEN"        # OPEN / FILLED / CANCELLED


@dataclass
class MockPosition:
    ticker:     str
    qty:        float
    avg_entry:  float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0


class MockBroker:
    """Drop-in replacement for the broker module. Tracks positions + open
    orders in memory and simulates broker-side stop/TP fills using bar data."""

    def __init__(self, starting_cash: float = 5000.0):
        self.cash      = starting_cash
        self.positions: dict[str, MockPosition] = {}
        self.orders:    list[MockOrder]         = []
        self.IS_PAPER  = True
        # Pricing oracle — set externally each tick to the current bar's data.
        # Used to fill market orders and detect stop/TP triggers.
        self.current_bars: dict[str, dict] = {}
        self.order_seq = 0

    def _new_id(self) -> str:
        self.order_seq += 1
        return f"mock-{self.order_seq}"

    # ── Account ──
    def settled_cash(self) -> float:
        return self.cash

    def account(self):
        # Returns a duck-typed object matching broker.Account fields used by live_ex1
        return type("A", (), {
            "cash":         self.cash,
            "buying_power": self.cash,
            "equity":       self.cash + sum(p.market_value for p in self.positions.values()),
            "portfolio_value": self.cash + sum(p.market_value for p in self.positions.values()),
            "pattern_day_trader": False,
            "is_paper":     True,
        })()

    # ── Positions ──
    def position(self, ticker: str) -> Optional[MockPosition]:
        return self.positions.get(ticker)

    def all_positions(self) -> list[MockPosition]:
        return list(self.positions.values())

    # ── Orders ──
    def market_buy(self, ticker: str, dollars: float) -> dict:
        if dollars <= 0:
            raise ValueError(f"market_buy: dollars must be positive (got {dollars})")
        if dollars > self.cash:
            raise RuntimeError(f"market_buy {ticker}: ${dollars:.2f} > cash ${self.cash:.2f}")

        bar = self.current_bars.get(ticker)
        if not bar:
            raise RuntimeError(f"market_buy {ticker}: no current bar to fill against")
        # Fill at current bar's close (mirrors the simulator's assumption)
        price = bar["close"]
        qty   = round(dollars / price, 4)

        self.cash -= round(qty * price, 2)
        self.positions[ticker] = MockPosition(
            ticker=ticker, qty=qty, avg_entry=price,
            market_value=qty * price, unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        )
        order = MockOrder(
            order_id=self._new_id(), ticker=ticker, side="BUY", type="MARKET",
            qty=qty, price=price, status="FILLED",
        )
        self.orders.append(order)
        return self._summary(order)

    def market_sell_position(self, ticker: str) -> dict:
        pos = self.positions.get(ticker)
        if pos is None:
            raise RuntimeError(f"market_sell_position {ticker}: no open position")
        bar = self.current_bars.get(ticker)
        if not bar:
            raise RuntimeError(f"market_sell_position {ticker}: no current bar")
        price = bar["close"]
        proceeds = round(pos.qty * price, 2)
        self.cash += proceeds
        order = MockOrder(
            order_id=self._new_id(), ticker=ticker, side="SELL", type="MARKET",
            qty=pos.qty, price=price, status="FILLED",
        )
        self.orders.append(order)
        del self.positions[ticker]
        return self._summary(order)

    def attach_stop_loss(self, ticker: str, qty: float, stop_price: float) -> dict:
        order = MockOrder(
            order_id=self._new_id(), ticker=ticker, side="SELL", type="STOP",
            qty=qty, stop_price=stop_price, status="OPEN",
        )
        self.orders.append(order)
        return self._summary(order)

    def attach_take_profit(self, ticker: str, qty: float, limit_price: float) -> dict:
        order = MockOrder(
            order_id=self._new_id(), ticker=ticker, side="SELL", type="LIMIT",
            qty=qty, limit_price=limit_price, status="OPEN",
        )
        self.orders.append(order)
        return self._summary(order)

    def open_orders(self, ticker: Optional[str] = None) -> list[dict]:
        return [self._summary(o) for o in self.orders
                if o.status == "OPEN" and (ticker is None or o.ticker == ticker)]

    def cancel_order(self, order_id: str) -> None:
        for o in self.orders:
            if o.order_id == order_id and o.status == "OPEN":
                o.status = "CANCELLED"
                return

    def cancel_all_open_orders(self) -> int:
        n = sum(1 for o in self.orders if o.status == "OPEN")
        for o in self.orders:
            if o.status == "OPEN":
                o.status = "CANCELLED"
        return n

    def client(self):
        # live_ex1 occasionally calls broker.client().get_orders(...) to find
        # the most-recent SELL fill after a position vanishes. We surface a
        # tiny shim that returns our orders.
        outer = self
        class FakeAlpacaClient:
            def get_orders(self, req):
                # Return all closed orders (for ticker if specified), most recent first
                results = [o for o in outer.orders if o.status in ("FILLED", "CANCELLED")]
                tickers = getattr(req, "symbols", None)
                if tickers:
                    results = [o for o in results if o.ticker in tickers]
                # Return objects matching the alpaca-py order model fields used by live_ex1
                rows = []
                for o in reversed(results):
                    # Use plain strings for side/order_type/status so live_ex1's
                    # str(o.side).upper() / "SELL" in side checks work correctly.
                    rows.append(type("Order", (), {
                        "id":               o.order_id,
                        "symbol":           o.ticker,
                        "side":             o.side,        # "BUY" / "SELL"
                        "order_type":       o.type,        # "MARKET" / "STOP" / "LIMIT"
                        "filled_qty":       o.qty,
                        "filled_avg_price": o.price,
                        "limit_price":      o.limit_price,
                        "stop_price":       o.stop_price,
                        "status":           o.status,
                        "submitted_at":     None,
                        "filled_at":        None,
                        "qty":              o.qty,
                        "notional":         None,
                    })())
                return rows
        return FakeAlpacaClient()

    # ── Broker-side stop/TP simulation ──
    def simulate_native_fills(self, bar_data: dict) -> list[dict]:
        """For each open stop/TP order: if the bar's low pierced the stop, or
        the bar's high pierced the TP, mark the order filled and remove the
        position. Returns a list of fill events."""
        events = []
        for o in self.orders:
            if o.status != "OPEN":
                continue
            bar = bar_data.get(o.ticker)
            if not bar:
                continue
            if o.type == "STOP" and bar["low"] <= o.stop_price:
                # Stop fills at stop_price (assume no gap-down beyond stop)
                fill_price = o.stop_price
                self._broker_fill(o, fill_price)
                events.append({"ticker": o.ticker, "reason": "STOP_LOSS", "price": fill_price})
            elif o.type == "LIMIT" and bar["high"] >= o.limit_price:
                fill_price = o.limit_price
                self._broker_fill(o, fill_price)
                events.append({"ticker": o.ticker, "reason": "TAKE_PROFIT", "price": fill_price})
        return events

    def _broker_fill(self, order: MockOrder, price: float):
        order.status = "FILLED"
        order.price  = price
        pos = self.positions.get(order.ticker)
        if pos:
            self.cash += round(pos.qty * price, 2)
            del self.positions[order.ticker]
        # Cancel any sibling open orders for the same ticker (OCO)
        for o in self.orders:
            if o.ticker == order.ticker and o.order_id != order.order_id and o.status == "OPEN":
                o.status = "CANCELLED"

    def _summary(self, o: MockOrder) -> dict:
        return {
            "order_id":     o.order_id,
            "ticker":       o.ticker,
            "side":         o.side,
            "type":         o.type,
            "qty":          o.qty,
            "filled_price": o.price,
            "limit_price":  o.limit_price,
            "stop_price":   o.stop_price,
            "status":       o.status,
        }


# ── Bar fetching for the replay date ──────────────────────────────────────────
def fetch_day_bars(date: str, tickers: list[str]):
    """Pull all 1-min bars for `date` for tickers + SPY; also prior closes,
    ATR modifiers, and the day's market state."""
    key, secret = ex1._load_creds()
    client = StockHistoricalDataClient(api_key=key, secret_key=secret)
    ET = "America/New_York"

    next_day = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    start_dt = datetime.strptime(date,     "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = datetime.strptime(next_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    print(f"  fetching bars for {date}...", flush=True)

    # Per-ticker minute bars
    ticker_data = {}
    for t in tickers:
        try:
            df = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=t, timeframe=TimeFrame.Minute,
                start=start_dt, end=end_dt, feed="iex",
            )).df
            if df.empty:
                continue
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(t, level=0)
            df    = df.tz_convert(ET).between_time("09:30", "15:59")
            if df.empty:
                continue
            ticker_data[t] = {
                "closes":  [round(float(v), 2) for v in df["close"].tolist()],
                "highs":   [round(float(v), 2) for v in df["high"].tolist()],
                "lows":    [round(float(v), 2) for v in df["low"].tolist()],
                "volumes": [int(v) for v in df["volume"].tolist()],
                "times":   [t.strftime("%H:%M") for t in df.index],
            }
        except Exception as e:
            print(f"    {t} failed: {e}")

    # SPY for relative-strength gate
    spy_by_time = {}
    try:
        df = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols="SPY", timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt, feed="iex",
        )).df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs("SPY", level=0)
        df = df.tz_convert(ET).between_time("09:30", "15:59")
        for ts, row in df.iterrows():
            spy_by_time[ts.strftime("%H:%M")] = row["close"]
    except Exception as e:
        print(f"    SPY failed: {e}")

    # Prior closes + ATR modifier
    import statistics as _stats
    prior_daily = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=tickers, timeframe=TimeFrame.Day,
        start=start_dt - timedelta(days=21), end=start_dt, feed="iex",
    ))
    prior_closes = {}
    atr_pcts     = {}
    for t in tickers:
        bars = prior_daily.data.get(t, [])
        if bars:
            prior_closes[t] = bars[-1].close
            val = ex1.calc_atr_pct(bars)
            if val:
                atr_pcts[t] = val
    if len(atr_pcts) >= 2:
        med = _stats.median(atr_pcts.values())
        atr_modifier = {
            t: round(min(ex1.ATR_MAX_MOD, max(ex1.ATR_MIN_MOD, med / atr_pcts[t])), 3)
            for t in atr_pcts
        }
    else:
        atr_modifier = {}

    # Prior-day avg per-minute volume — no-lookahead baseline for GAP_GO
    # scoring (matches live_ex1._build_prior_avg_vols).
    prior_avg_vols = {}
    for t in tickers:
        bars = prior_daily.data.get(t, [])
        if bars:
            recent = bars[-5:]
            avg_daily = sum(b.volume for b in recent) / len(recent)
            prior_avg_vols[t] = avg_daily / 390

    return {
        "ticker_data":    ticker_data,
        "spy_by_time":    spy_by_time,
        "prior_closes":   prior_closes,
        "atr_modifier":   atr_modifier,
        "prior_avg_vols": prior_avg_vols,
    }


# ── Market state for the date ─────────────────────────────────────────────────
def get_market_state_for(date: str) -> str:
    """Reuse the same logic test_take_profit.py does to derive market state
    from market_states_historical.json + exercises.json."""
    # First check exercises.json (live days have correct state)
    try:
        with open(os.path.join(BASE_DIR, "exercises.json")) as f:
            for d in json.load(f):
                if d.get("date") == date and "Exercise 1" in d.get("title", ""):
                    return d.get("market_state", "neutral")
    except Exception:
        pass
    # Fallback: derive from historical
    try:
        with open(os.path.join(BASE_DIR, "market_states_historical.json")) as f:
            for row in json.load(f):
                if row["date"] == date:
                    spy = row["spy_gap_pct"] / 100
                    vix = row["vixy_trend_pct"] / 100
                    if spy <= ex1.SPY_BEAR or vix >= ex1.VIXY_SURGE:
                        return "bearish"
                    if spy >= ex1.SPY_BULL and vix < ex1.VIXY_SURGE:
                        return "bullish"
                    return "neutral"
    except Exception:
        pass
    return "neutral"


# ── Replay engine ─────────────────────────────────────────────────────────────
def run_dry_run(date: str, verbose: bool = False):
    print(f"\n{'='*72}")
    print(f"  DRY RUN — {date}  ({'verbose' if verbose else 'summary'} mode)")
    print(f"{'='*72}")

    data         = fetch_day_bars(date, ex1.TICKERS)
    market_state = get_market_state_for(date)
    print(f"  market_state: {market_state}")
    print(f"  tickers with intraday data: {len(data['ticker_data'])}/{len(ex1.TICKERS)}")
    print(f"  ATR modifiers loaded: {len(data['atr_modifier'])}")
    print(f"  prior closes loaded: {len(data['prior_closes'])}/{len(ex1.TICKERS)}")
    # Show gap_pct per ticker so GAP_GO eligibility is visible
    gap_lines = []
    for t in ex1.TICKERS:
        td = data["ticker_data"].get(t)
        pc = data["prior_closes"].get(t)
        if td and pc and td["closes"]:
            gap = (td["closes"][0] - pc) / pc * 100
            tag = " ← GAP_GO" if gap >= ex1.GAP_GO_THRESH * 100 else ""
            gap_lines.append(f"    {t:6} gap_pct={gap:+.2f}%{tag}")
    print("\n".join(gap_lines))

    # Build aligned timeline — the union of all minute timestamps observed,
    # sorted ascending. We'll iterate this and reveal bars up to each timestamp.
    all_times: set[str] = set()
    for td in data["ticker_data"].values():
        all_times.update(td["times"])
    timeline = sorted(all_times)
    if not timeline:
        print("  no bars; aborting")
        return

    print(f"  replay timeline: {len(timeline)} minutes ({timeline[0]} → {timeline[-1]})\n")

    # ── Patch in mock broker + pre-test plumbing ──
    import broker as real_broker
    import alerts as real_alerts
    import live_ex1

    mock = MockBroker(starting_cash=ex1.BUDGET)

    # Replace broker module functions
    real_broker.market_buy            = mock.market_buy
    real_broker.market_sell_position  = mock.market_sell_position
    real_broker.attach_stop_loss      = mock.attach_stop_loss
    real_broker.attach_take_profit    = mock.attach_take_profit
    real_broker.position              = mock.position
    real_broker.all_positions         = mock.all_positions
    real_broker.open_orders           = mock.open_orders
    real_broker.cancel_order          = mock.cancel_order
    real_broker.cancel_all_open_orders = mock.cancel_all_open_orders
    real_broker.settled_cash          = mock.settled_cash
    real_broker.account               = mock.account
    real_broker.client                = mock.client
    real_broker.IS_PAPER              = True

    # Silence Telegram (prefix and print to stdout instead)
    def _stub_send(text, retry=2):
        if verbose:
            print(f"  [alert] {text[:120].replace(chr(10), ' / ')}")
        return True
    real_alerts._send_raw = _stub_send

    # Reset live_ex1's state to fresh in-memory dict (not loaded from disk).
    # Use a separate state file path so we never clobber the live one.
    live_ex1.STATE_FILE = os.path.join(BASE_DIR, "dry_state.json")
    if os.path.exists(live_ex1.STATE_FILE):
        os.remove(live_ex1.STATE_FILE)
    live_ex1.state = {
        "session_date":    date,
        "market_state":    market_state,
        "starting_cash":   ex1.BUDGET,
        "open_positions":  {},
        "session_pnl":     0.0,
        "halted":          False,
        "completed_trades": [],
    }
    live_ex1.save_state()

    # ── Replay loop ──
    for cur_time in timeline:
        # Build the "current bars" snapshot: each ticker truncated to bars at
        # or before cur_time. Same shape as live_ex1's fetch_today_bars output.
        snapshot = {}
        cur_oracle = {}
        for t, td in data["ticker_data"].items():
            try:
                idx = td["times"].index(cur_time)
            except ValueError:
                continue
            snapshot[t] = {
                "closes":  td["closes"][:idx + 1],
                "highs":   td["highs"][:idx + 1],
                "lows":    td["lows"][:idx + 1],
                "volumes": td["volumes"][:idx + 1],
                "times":   td["times"][:idx + 1],
            }
            cur_oracle[t] = {
                "close": td["closes"][idx],
                "high":  td["highs"][idx],
                "low":   td["lows"][idx],
            }
        mock.current_bars = cur_oracle

        # SPY snapshot up to this time
        spy_snapshot = {k: v for k, v in data["spy_by_time"].items() if k <= cur_time}

        # 1. Simulate broker-side stop/TP fills using THIS bar's high/low
        events = mock.simulate_native_fills(cur_oracle)
        for ev in events:
            if verbose:
                print(f"  {cur_time}  [BROKER FILL] {ev['ticker']:6} {ev['reason']:12} "
                      f"@ ${ev['price']:.2f}")

        # 2. Entry detection — only during entry window (9:30 → 14:00)
        if cur_time < ex1.ENTRY_CLOSE and not live_ex1.state["halted"]:
            live_ex1.check_for_signals(
                client=None,  # not used by live_ex1 in this path
                ticker_data=snapshot, spy_by_time=spy_snapshot,
                prior_closes=data["prior_closes"], atr_modifier=data["atr_modifier"],
                prior_avg_vols=data["prior_avg_vols"],
            )

        # 3. Time close at 14:00
        h, m = int(cur_time[:2]), int(cur_time[3:])
        if (h, m) >= tuple(int(x) for x in ex1.ENTRY_CLOSE.split(":")):
            live_ex1.time_close_all()

        # 4. Custom exit polling
        live_ex1.check_exits(snapshot)

    # ── Report ──
    print(f"\n{'─'*72}")
    print(f"  DRY-RUN RESULT")
    print(f"{'─'*72}")
    pnl    = live_ex1.state["session_pnl"]
    trades = live_ex1.state["completed_trades"]
    wins   = sum(1 for t in trades if t["pnl"] > 0)
    print(f"  trades:       {len(trades)}  ({wins} wins)")
    print(f"  P&L:          ${pnl:+.2f}")
    print(f"  end cash:     ${mock.settled_cash():,.2f}")
    print(f"  halted:       {live_ex1.state['halted']}")
    print()
    if trades:
        print(f"  {'#':>2}  {'ticker':<6} {'sig':<7} {'rate':<5} {'in':<5} {'out':<5} "
              f"{'entry':>8} {'exit':>8} {'reason':<14} {'pnl':>9}")
        for i, t in enumerate(trades, 1):
            sign = "+" if t["pnl"] >= 0 else ""
            print(f"  {i:>2}  {t['ticker']:<6} {t['signal']:<7} {t['rating']:<5} "
                  f"{t['entry_time']:<5} {t['exit_time']:<5} "
                  f"${t['entry_price']:>7.2f} ${t['exit_price']:>7.2f} "
                  f"{t['exit_reason']:<14} {sign}${t['pnl']:>7.2f}")
    print()

    # ── Compare to exercises.json EX1 for this date ──
    print(f"{'─'*72}")
    print(f"  vs SIMULATION (exercises.json EX1)")
    print(f"{'─'*72}")
    try:
        with open(os.path.join(BASE_DIR, "exercises.json")) as f:
            sim_record = next(
                (d for d in json.load(f)
                 if d.get("date") == date and "Exercise 1" in d.get("title", "")),
                None,
            )
    except Exception:
        sim_record = None

    if sim_record is None:
        print(f"  No exercises.json EX1 entry for {date} — cannot compare.")
        return

    sim_pnl    = sim_record.get("total_pnl", 0.0)
    sim_trades = sim_record.get("trades", [])
    sim_wins   = sum(1 for t in sim_trades if t.get("pnl", 0) > 0)

    print(f"  sim trades:   {len(sim_trades)}  ({sim_wins} wins)")
    print(f"  sim P&L:      ${sim_pnl:+.2f}")
    print()

    # Diff by ticker
    dry_by_t = {t["ticker"]: t for t in trades}
    sim_by_t = {t["ticker"]: t for t in sim_trades}
    all_t    = sorted(set(dry_by_t) | set(sim_by_t))

    if not all_t:
        print("  (no trades on either side)")
    else:
        print(f"  {'ticker':<7} {'side':<8} {'in':<13} {'out':<13} {'reason':<14} {'pnl':>9}")
        for t in all_t:
            d = dry_by_t.get(t)
            s = sim_by_t.get(t)
            if d and s:
                same_in   = d["entry_time"] == s["time"]
                same_out  = d["exit_time"]  == s["exit_time"]
                same_rsn  = d["exit_reason"] == s["exit_reason"]
                pnl_close = abs(d["pnl"] - s["pnl"]) < 0.10
                ok = same_in and same_out and same_rsn and pnl_close
                tag = "✓" if ok else "✗"
                print(f"  {t:<7} {tag:<8} {d['entry_time']}→{s['time']:<7} "
                      f"{d['exit_time']}→{s['exit_time']:<7} "
                      f"{d['exit_reason']:<14} ${d['pnl']:+.2f} vs ${s['pnl']:+.2f}")
            elif d:
                print(f"  {t:<7} {'DRY ONLY':<8} {d['entry_time']:<13} {d['exit_time']:<13} "
                      f"{d['exit_reason']:<14} ${d['pnl']:+.2f}")
            elif s:
                print(f"  {t:<7} {'SIM ONLY':<8} {s['time']:<13} {s['exit_time']:<13} "
                      f"{s['exit_reason']:<14} ${s['pnl']:+.2f}")

    print()
    delta = pnl - sim_pnl
    if abs(delta) < 0.10:
        print(f"  ✓ PNL matches: ${pnl:+.2f} ≈ ${sim_pnl:+.2f}")
    else:
        print(f"  ✗ PNL DIVERGES: dry ${pnl:+.2f} vs sim ${sim_pnl:+.2f}  (delta {delta:+.2f})")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("date", help="YYYY-MM-DD (must have data in exercises.json or backfill)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print every alert and broker fill as it happens")
    args = parser.parse_args()
    run_dry_run(args.date, verbose=args.verbose)
