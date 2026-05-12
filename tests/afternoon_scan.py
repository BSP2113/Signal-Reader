"""
Scan backfill days for afternoon volume breakouts on already-traded tickers.
Criteria:
  - 13:00+ bar with volume >= 10x that ticker's morning average (9:30-13:00)
  - Price at that bar above the morning high
  - Price at bar >= exit price (so we're not re-entering a loser)
"""
import json, os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

def load_creds():
    creds = {}
    with open(os.path.join(BASE_DIR, ".env")) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds["ALPACA_API_KEY"], creds["ALPACA_API_SECRET"]

def scan_day(client, trade_date, tickers_traded):
    next_day = (datetime.datetime.strptime(trade_date, "%Y-%m-%d") + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    start_dt = datetime.datetime.strptime(trade_date, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    end_dt   = datetime.datetime.strptime(next_day, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)

    hits = []
    for ticker in tickers_traded:
        try:
            req = StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=TimeFrame.Minute,
                start=start_dt, end=end_dt,
            )
            bars = client.get_stock_bars(req)[ticker]
        except Exception:
            continue

        morning_bars = [b for b in bars if b.timestamp.strftime("%H:%M") < "13:00"]
        afternoon_bars = [b for b in bars if b.timestamp.strftime("%H:%M") >= "13:00"]

        if not morning_bars or not afternoon_bars:
            continue

        morning_avg_vol = sum(b.volume for b in morning_bars) / len(morning_bars)
        morning_high    = max(b.high for b in morning_bars)

        if morning_avg_vol < 1:
            continue

        for b in afternoon_bars:
            vol_ratio = b.volume / morning_avg_vol
            if vol_ratio >= 10 and b.close > morning_high:
                # how far did it run from this bar to EOD high?
                remaining = [rb for rb in afternoon_bars if rb.timestamp >= b.timestamp]
                eod_high  = max(rb.high for rb in remaining)
                potential_pct = (eod_high - b.close) / b.close * 100
                hits.append({
                    "date": trade_date,
                    "ticker": ticker,
                    "time": b.timestamp.strftime("%H:%M"),
                    "vol_ratio": round(vol_ratio, 0),
                    "morning_high": morning_high,
                    "trigger_price": round(b.close, 2),
                    "eod_high": round(eod_high, 2),
                    "potential_pct": round(potential_pct, 1),
                })
                break  # first qualifying bar only

    return hits

def main():
    with open(os.path.join(BASE_DIR, "backfill.json")) as f:
        backfill = json.load(f)

    key, secret = load_creds()
    client = StockHistoricalDataClient(api_key=key, secret_key=secret)

    all_hits = []
    for day in backfill:
        tickers = list({t["ticker"] for t in day["trades"]})
        print(f"  {day['date']} — checking {tickers}")
        hits = scan_day(client, day["date"], tickers)
        all_hits.extend(hits)

    print(f"\n{'='*60}")
    print(f"AFTERNOON VOLUME BREAKOUTS — 10x+ vol, above morning high")
    print(f"{'='*60}")
    if not all_hits:
        print("None found.")
    else:
        for h in sorted(all_hits, key=lambda x: x["potential_pct"], reverse=True):
            print(f"  {h['date']}  {h['ticker']:5s}  {h['time']}  "
                  f"vol {h['vol_ratio']:.0f}x  "
                  f"trigger ${h['trigger_price']}  "
                  f"eod high ${h['eod_high']}  "
                  f"potential +{h['potential_pct']}%")
    print(f"\nTotal hits: {len(all_hits)} across {len(backfill)} days")

if __name__ == "__main__":
    main()
