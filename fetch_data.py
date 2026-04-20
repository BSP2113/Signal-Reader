"""
fetch_data.py — pulls historical price data from Yahoo Finance and generates dashboard.html

Run with: python3 fetch_data.py
Then open dashboard.html in your browser.
"""

import json
import yfinance as yf
from datetime import datetime

# Assets to track — add or remove tickers here
TICKERS = ["AAPL", "MSFT", "BTC-USD", "ETH-USD"]

# How many days of history to pull
PERIOD = "30d"


def fetch(ticker):
    data = yf.download(ticker, period=PERIOD, interval="1d", progress=False, auto_adjust=True)
    if data.empty:
        print(f"  Warning: no data returned for {ticker}")
        return None
    dates = [str(d.date()) for d in data.index]
    closes = [round(float(v), 2) for v in data["Close"].squeeze().tolist()]
    volumes = [int(v) for v in data["Volume"].squeeze().tolist()]
    return {"ticker": ticker, "dates": dates, "closes": closes, "volumes": volumes}


def detect_signals(asset):
    """Flag days where price or volume moved more than 5% from the previous day."""
    signals = []
    closes = asset["closes"]
    volumes = asset["volumes"]
    dates = asset["dates"]
    avg_volume = sum(volumes) / len(volumes)

    for i in range(1, len(closes)):
        price_change = abs(closes[i] - closes[i - 1]) / closes[i - 1]
        volume_spike = volumes[i] > avg_volume * 1.5

        if price_change > 0.05 or volume_spike:
            direction = "UP" if closes[i] > closes[i - 1] else "DOWN"
            reason = []
            if price_change > 0.05:
                reason.append(f"price moved {price_change:.1%} {direction}")
            if volume_spike:
                reason.append(f"volume {volumes[i]:,} vs avg {avg_volume:,.0f}")
            signals.append({"date": dates[i], "reason": ", ".join(reason), "direction": direction})

    return signals


def build_dashboard(assets):
    cards = ""
    charts_js = ""

    for asset in assets:
        ticker = asset["ticker"]
        signals = detect_signals(asset)
        signal_rows = "".join(
            f'<tr class="signal-{s["direction"].lower()}">'
            f'<td>{s["date"]}</td><td>{s["direction"]}</td><td>{s["reason"]}</td></tr>'
            for s in signals
        ) or "<tr><td colspan='3'>No signals detected</td></tr>"

        cards += f"""
        <div class="card">
            <h2>{ticker}</h2>
            <canvas id="chart-{ticker}"></canvas>
            <h3>Signals</h3>
            <table>
                <thead><tr><th>Date</th><th>Direction</th><th>Reason</th></tr></thead>
                <tbody>{signal_rows}</tbody>
            </table>
        </div>
        """

        labels = json.dumps(asset["dates"])
        prices = json.dumps(asset["closes"])
        volumes = json.dumps(asset["volumes"])

        charts_js += f"""
        (function() {{
            var ctx = document.getElementById('chart-{ticker}').getContext('2d');
            new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: {labels},
                    datasets: [{{
                        label: '{ticker} Close Price',
                        data: {prices},
                        borderColor: '#4f8ef7',
                        backgroundColor: 'rgba(79,142,247,0.1)',
                        tension: 0.2,
                        pointRadius: 3,
                        yAxisID: 'y'
                    }}, {{
                        label: 'Volume',
                        data: {volumes},
                        type: 'bar',
                        backgroundColor: 'rgba(150,150,150,0.3)',
                        yAxisID: 'y2'
                    }}]
                }},
                options: {{
                    responsive: true,
                    interaction: {{ mode: 'index', intersect: false }},
                    scales: {{
                        y: {{ position: 'left', title: {{ display: true, text: 'Price (USD)' }} }},
                        y2: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Volume' }} }}
                    }}
                }}
            }});
        }})();
        """

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Signal Reader Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: sans-serif; background: #0f0f1a; color: #e0e0e0; margin: 0; padding: 20px; }}
        h1 {{ color: #4f8ef7; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(600px, 1fr)); gap: 24px; }}
        .card {{ background: #1a1a2e; border-radius: 10px; padding: 20px; }}
        h2 {{ margin-top: 0; color: #7eb8f7; }}
        h3 {{ color: #aaa; font-size: 0.9em; margin-top: 20px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
        th {{ text-align: left; color: #888; padding: 4px 8px; border-bottom: 1px solid #333; }}
        td {{ padding: 4px 8px; }}
        .signal-up {{ color: #4caf50; }}
        .signal-down {{ color: #f44336; }}
        .meta {{ color: #555; font-size: 0.8em; margin-top: 30px; }}
    </style>
</head>
<body>
    <h1>Signal Reader Dashboard</h1>
    <p>Tracking: {", ".join(TICKERS)} &nbsp;|&nbsp; Period: {PERIOD}</p>
    <div class="grid">{cards}</div>
    <p class="meta">Generated: {generated} — re-run fetch_data.py to refresh</p>
    <script>{charts_js}</script>
</body>
</html>"""


if __name__ == "__main__":
    print(f"Fetching data for: {', '.join(TICKERS)}")
    assets = []
    for ticker in TICKERS:
        print(f"  Downloading {ticker}...")
        result = fetch(ticker)
        if result:
            assets.append(result)

    if not assets:
        print("No data fetched. Check your internet connection.")
    else:
        html = build_dashboard(assets)
        with open("dashboard.html", "w") as f:
            f.write(html)
        print(f"\nDone! Open Signal-Reader/dashboard.html in your browser.")
        print(f"Signals flagged when price moves >5% or volume spikes >1.5x average.")
