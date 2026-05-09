"""
test_crash_recovery.py — verify live_ex1 survives a mid-session crash + restart.

Five scenarios:
  1. Fresh start, no state file: load_state creates a clean session
  2. State file from TODAY: load_state resumes (preserves open_positions)
  3. State file from YESTERDAY: load_state starts fresh (date mismatch)
  4. Mid-session restart with open positions: exit polling continues correctly
  5. Broker reports flat but state thinks we're in: reconciliation fires + logs
"""

import os
import sys
import json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import broker as real_broker
import alerts as real_alerts
import live_ex1
import ex1


TEST_STATE = "/tmp/test_state_recovery.json"


def cleanup():
    if os.path.exists(TEST_STATE):
        os.remove(TEST_STATE)


def stub_alerts():
    sent = []
    real_alerts._send_raw = lambda text, retry=2: sent.append(text) or True
    return sent


# ── 1. Fresh start ─────────────────────────────────────────────────────────────
def test_fresh_start():
    print("\n[1] no state file → fresh session")
    cleanup()
    live_ex1.STATE_FILE = TEST_STATE
    # reset state to defaults (mimic module-load)
    live_ex1.state = {
        "session_date": None, "market_state": "neutral", "starting_cash": 0.0,
        "open_positions": {}, "session_pnl": 0.0, "halted": False,
        "completed_trades": [],
    }
    live_ex1.load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if live_ex1.state["session_date"] != today:
        print(f"    FAIL: session_date={live_ex1.state['session_date']}, want {today}")
        return False
    if live_ex1.state["open_positions"]:
        print("    FAIL: open_positions should be empty on fresh start")
        return False
    print("    PASS: fresh session for", today)
    return True


# ── 2. Resume from today's state ───────────────────────────────────────────────
def test_resume_today():
    print("\n[2] state file from TODAY → resume with open_positions intact")
    today = datetime.now().strftime("%Y-%m-%d")
    saved = {
        "session_date":   today,
        "market_state":   "bullish",
        "starting_cash":  5000.0,
        "open_positions": {
            "AMD": {
                "ticker": "AMD", "signal": "GAP_GO", "rating": "TAKE",
                "entry_time": "09:31", "entry_price": 200.00, "qty": 12.5,
                "stop_price": 197.0, "tp_price": None, "peak": 202.0,
                "trail_armed": False, "consec_above_lock": 1, "exit_filed": False,
            },
        },
        "session_pnl":      0.0,
        "halted":           False,
        "completed_trades": [],
    }
    with open(TEST_STATE, "w") as f:
        json.dump(saved, f)

    live_ex1.STATE_FILE = TEST_STATE
    live_ex1.state = {"session_date": None}  # fresh memory, simulating restart
    live_ex1.load_state()

    if live_ex1.state["session_date"] != today:
        print(f"    FAIL: lost session_date")
        return False
    if "AMD" not in live_ex1.state["open_positions"]:
        print(f"    FAIL: AMD position lost on reload")
        return False
    if live_ex1.state["open_positions"]["AMD"]["entry_price"] != 200.00:
        print(f"    FAIL: AMD entry_price corrupted")
        return False
    print(f"    PASS: resumed with AMD position intact (entry $200, peak $202)")
    return True


# ── 3. Stale state from yesterday ──────────────────────────────────────────────
def test_stale_state_resets():
    print("\n[3] state file from YESTERDAY → fresh session (date mismatch)")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    saved = {
        "session_date": yesterday, "market_state": "bullish",
        "starting_cash": 5000.0,
        "open_positions": {"GHOST": {"ticker": "GHOST"}},
        "session_pnl": 0.0, "halted": False, "completed_trades": [],
    }
    with open(TEST_STATE, "w") as f:
        json.dump(saved, f)

    live_ex1.STATE_FILE = TEST_STATE
    live_ex1.state = {"session_date": None}
    live_ex1.load_state()

    today = datetime.now().strftime("%Y-%m-%d")
    if live_ex1.state["session_date"] != today:
        print(f"    FAIL: should reset to today; got {live_ex1.state['session_date']}")
        return False
    if "GHOST" in live_ex1.state.get("open_positions", {}):
        print(f"    FAIL: stale GHOST position not cleared")
        return False
    print(f"    PASS: yesterday's state ignored, fresh session for {today}")
    return True


# ── 4. Mid-session restart, custom exits still work ────────────────────────────
def test_restart_with_open_positions():
    print("\n[4] restart mid-session with open AMD → custom exits still fire")
    today = datetime.now().strftime("%Y-%m-%d")

    # Stub broker — AMD position confirmed alive
    class StubBroker:
        IS_PAPER = True
        def position(self, ticker):
            if ticker == "AMD":
                return type("P", (), {"ticker":"AMD", "qty":12.5, "avg_entry":200.0,
                                       "market_value":2500.0, "unrealized_pnl":0.0,
                                       "unrealized_pnl_pct":0.0})()
            return None
        def market_sell_position(self, ticker):
            return {"order_id":"sell", "ticker":ticker, "filled_price":204.0}
        def open_orders(self, ticker=None): return []
        def cancel_order(self, *a, **kw): return None
        def settled_cash(self): return 0.0
        def client(self):
            class FC:
                def get_orders(self, req):
                    return [type("O",(),{
                        "id":"sell","symbol":"AMD","side":"SELL","order_type":"MARKET",
                        "filled_qty":12.5,"filled_avg_price":204.0,"limit_price":None,
                        "stop_price":None,"status":"FILLED","submitted_at":None,
                        "filled_at":None,"qty":12.5,"notional":None,
                    })()]
            return FC()
    stub = StubBroker()
    real_broker.position             = stub.position
    real_broker.market_sell_position = stub.market_sell_position
    real_broker.open_orders          = stub.open_orders
    real_broker.cancel_order         = stub.cancel_order
    real_broker.settled_cash         = stub.settled_cash
    real_broker.client               = stub.client
    real_broker.IS_PAPER             = True
    sent = stub_alerts()

    # Save state with AMD open at 09:31, peak=$204 (already at +2%)
    saved = {
        "session_date": today, "market_state": "bullish", "starting_cash": 5000.0,
        "open_positions": {
            "AMD": {
                "ticker": "AMD", "signal": "GAP_GO", "rating": "TAKE",
                "entry_time": "09:31", "entry_price": 200.0, "qty": 12.5,
                "stop_price": 197.0, "tp_price": None, "peak": 204.0,
                "trail_armed": False, "consec_above_lock": 0, "exit_filed": False,
            },
        },
        "session_pnl": 0.0, "halted": False, "completed_trades": [],
    }
    with open(TEST_STATE, "w") as f:
        json.dump(saved, f)
    live_ex1.STATE_FILE = TEST_STATE
    live_ex1.state = {"session_date": None}
    live_ex1.load_state()

    # Now feed bars where AMD already armed the trail (2 closes >= +1%)
    # AND just dropped 2% from peak — trail should fire.
    snapshot = {
        "AMD": {
            "closes":  [200.0, 202.0, 203.0, 204.0, 199.92],   # last bar trips trail (-2% from $204)
            "highs":   [201.0, 202.5, 203.5, 204.5, 200.0],
            "lows":    [199.5, 201.0, 202.0, 203.5, 199.50],
            "volumes": [1_000_000, 500_000, 400_000, 350_000, 600_000],
            "times":   ["09:31", "09:32", "09:33", "09:34", "09:35"],
        }
    }
    live_ex1.check_exits(snapshot)

    trades = live_ex1.state["completed_trades"]
    if not trades:
        print(f"    FAIL: no exit fired. open_positions={live_ex1.state['open_positions']}")
        return False
    t = trades[0]
    if t["exit_reason"] != "TRAILING_STOP":
        print(f"    FAIL: expected TRAILING_STOP, got {t['exit_reason']}")
        return False
    if t["entry_time"] != "09:31":
        print(f"    FAIL: entry_time wrong: {t['entry_time']}")
        return False
    print(f"    PASS: trail fired post-restart at {t['exit_time']} "
          f"P&L ${t['pnl']:+.2f} ({t['exit_reason']})")
    return True


# ── 5. Broker reports flat but state thinks we're in (native fill while down) ──
def test_broker_drift_reconciliation():
    print("\n[5] broker fills native stop while runner is down → reconcile on restart")
    today = datetime.now().strftime("%Y-%m-%d")

    # Stub: broker has NO position (the stop already filled while runner was down)
    class StubBroker:
        IS_PAPER = True
        def position(self, ticker): return None
        def market_sell_position(self, ticker):
            raise RuntimeError("should not be called — already flat")
        def open_orders(self, ticker=None): return []
        def cancel_order(self, *a, **kw): return None
        def settled_cash(self): return 5000.0
        def client(self):
            class FC:
                def get_orders(self, req):
                    return [type("O",(),{
                        "id":"native-stop","symbol":"PLTR","side":"SELL",
                        "order_type":"STOP","filled_qty":50.0,"filled_avg_price":98.50,
                        "limit_price":None,"stop_price":98.50,"status":"FILLED",
                        "submitted_at":None,"filled_at":None,"qty":50.0,"notional":None,
                    })()]
            return FC()
    stub = StubBroker()
    real_broker.position             = stub.position
    real_broker.market_sell_position = stub.market_sell_position
    real_broker.open_orders          = stub.open_orders
    real_broker.cancel_order         = stub.cancel_order
    real_broker.settled_cash         = stub.settled_cash
    real_broker.client               = stub.client
    real_broker.IS_PAPER             = True
    stub_alerts()

    saved = {
        "session_date": today, "market_state": "neutral", "starting_cash": 5000.0,
        "open_positions": {
            "PLTR": {
                "ticker": "PLTR", "signal": "ORB", "rating": "MAYBE",
                "entry_time": "10:15", "entry_price": 100.0, "qty": 50.0,
                "stop_price": 98.50, "tp_price": 103.0, "peak": 100.0,
                "trail_armed": False, "consec_above_lock": 0, "exit_filed": False,
            },
        },
        "session_pnl": 0.0, "halted": False, "completed_trades": [],
    }
    with open(TEST_STATE, "w") as f:
        json.dump(saved, f)
    live_ex1.STATE_FILE = TEST_STATE
    live_ex1.state = {"session_date": None}
    live_ex1.load_state()

    snapshot = {
        "PLTR": {
            "closes":  [100.0, 99.0, 98.5],
            "highs":   [100.5, 100.0, 99.0],
            "lows":    [99.5,  98.5, 98.0],
            "volumes": [200_000, 250_000, 300_000],
            "times":   ["10:15", "10:16", "10:17"],
        }
    }
    live_ex1.check_exits(snapshot)

    trades = live_ex1.state["completed_trades"]
    if not trades:
        print(f"    FAIL: no reconciliation trade logged")
        return False
    t = trades[0]
    if "PLTR" in live_ex1.state["open_positions"]:
        print(f"    FAIL: PLTR still in open_positions after reconciliation")
        return False
    if t["exit_reason"] != "STOP_LOSS":
        print(f"    FAIL: expected STOP_LOSS reason from broker order_type, got {t['exit_reason']}")
        return False
    expected_pnl = (98.50 - 100.0) * 50.0  # -75
    if abs(t["pnl"] - expected_pnl) > 0.01:
        print(f"    FAIL: P&L wrong: got {t['pnl']:.2f}, expected {expected_pnl:.2f}")
        return False
    print(f"    PASS: reconciled native-stop fill from order history; "
          f"logged STOP_LOSS P&L ${t['pnl']:+.2f}")
    return True


if __name__ == "__main__":
    results = [
        test_fresh_start(),
        test_resume_today(),
        test_stale_state_resets(),
        test_restart_with_open_positions(),
        test_broker_drift_reconciliation(),
    ]
    cleanup()
    print(f"\n{'='*60}")
    print(f"  {sum(results)}/{len(results)} crash-recovery tests passed")
    print(f"{'='*60}")
    sys.exit(0 if all(results) else 1)
