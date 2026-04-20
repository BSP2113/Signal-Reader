"""
fetch_data.py — pulls 1-minute OHLC data from Yahoo Finance and generates dashboard.html

Run with: python3 fetch_data.py
Then open dashboard.html in your browser.
Note: Yahoo Finance only provides 1-minute data for the last 7 days.
"""

import json
import yfinance as yf
from datetime import datetime

# Assets to track — add or remove tickers here
TICKERS = ["AAPL", "MSFT", "BTC-USD", "ETH-USD"]

# 1-minute data — Yahoo Finance supports up to 7 days at this resolution
PERIOD = "5d"
INTERVAL = "1m"


def fetch(ticker):
    data = yf.download(ticker, period=PERIOD, interval=INTERVAL, progress=False, auto_adjust=True)
    if data.empty:
        print(f"  Warning: no data returned for {ticker}")
        return None

    timestamps = [int(d.timestamp() * 1000) for d in data.index]
    opens   = [round(float(v), 2) for v in data["Open"].squeeze().tolist()]
    highs   = [round(float(v), 2) for v in data["High"].squeeze().tolist()]
    lows    = [round(float(v), 2) for v in data["Low"].squeeze().tolist()]
    closes  = [round(float(v), 2) for v in data["Close"].squeeze().tolist()]
    volumes = [int(v) for v in data["Volume"].squeeze().tolist()]

    ohlc       = [{"x": ts, "o": o, "h": h, "l": l, "c": c}
                  for ts, o, h, l, c in zip(timestamps, opens, highs, lows, closes)]
    volumes_ts = [{"x": ts, "y": v} for ts, v in zip(timestamps, volumes)]
    labels     = [str(d) for d in data.index]

    return {
        "ticker": ticker,
        "labels": labels,
        "closes": closes,
        "volumes": volumes,
        "ohlc": ohlc,
        "volumes_ts": volumes_ts,
    }


def detect_signals(asset):
    """Flag 1-minute candles where price moved >1% or volume spiked >2x average."""
    signals = []
    closes  = asset["closes"]
    volumes = asset["volumes"]
    labels  = asset["labels"]
    avg_volume = sum(volumes) / len(volumes) if volumes else 1

    for i in range(1, len(closes)):
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
                reason.append(f"volume {volumes[i]:,} vs avg {avg_volume:,.0f}")
            signals.append({"date": labels[i], "reason": ", ".join(reason), "direction": direction})

    return signals


def build_dashboard(assets):
    cards     = ""
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
            <div class="btn-row">
                <button class="toggle-btn" id="toggle-{ticker}" onclick="toggleChart('{ticker}')">Candlestick</button>
                <button class="reset-btn" onclick="resetZoom('{ticker}')">Reset Zoom</button>
            </div>
            <div class="chart-wrap" id="wrap-{ticker}">
                <canvas id="chart-{ticker}"></canvas>
            </div>
            <h3>Signals</h3>
            <table>
                <thead><tr><th>Date</th><th>Direction</th><th>Reason</th></tr></thead>
                <tbody>{signal_rows}</tbody>
            </table>
        </div>
        """

        labels     = json.dumps(asset["labels"])
        closes     = json.dumps(asset["closes"])
        volumes    = json.dumps(asset["volumes"])
        ohlc       = json.dumps(asset["ohlc"])
        volumes_ts = json.dumps(asset["volumes_ts"])

        charts_js += f"""
        chartData['{ticker}'] = {{
            labels:     {labels},
            closes:     {closes},
            volumes:    {volumes},
            ohlc:       {ohlc},
            volumesTs:  {volumes_ts}
        }};
        chartMode['{ticker}'] = 'line';
        charts['{ticker}'] = buildChart('{ticker}');
        """

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="60">
    <title>Signal Reader Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/luxon@3/build/global/luxon.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1/dist/chartjs-adapter-luxon.umd.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial@0.2.1/dist/chartjs-chart-financial.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@1.2.1/dist/chartjs-plugin-zoom.min.js"></script>
    <style>
        body       {{ font-family: sans-serif; background: #0f0f1a; color: #e0e0e0; margin: 0; padding: 20px; }}
        h1         {{ color: #4f8ef7; }}
        .grid      {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(600px, 1fr)); gap: 24px; }}
        .card      {{ background: #1a1a2e; border-radius: 10px; padding: 20px; }}
        h2         {{ margin-top: 0; color: #7eb8f7; }}
        h3         {{ color: #aaa; font-size: 0.9em; margin-top: 20px; }}
        table      {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
        th         {{ text-align: left; color: #888; padding: 4px 8px; border-bottom: 1px solid #333; }}
        td         {{ padding: 4px 8px; }}
        .signal-up   {{ color: #4caf50; }}
        .signal-down {{ color: #f44336; }}
        .meta        {{ color: #555; font-size: 0.8em; margin-top: 30px; }}
        .btn-row     {{ display: flex; gap: 8px; margin-bottom: 10px; }}
        .reset-btn, .toggle-btn {{
            background: #2a2a4a; color: #7eb8f7; border: 1px solid #4f8ef7;
            border-radius: 5px; padding: 4px 12px; cursor: pointer; font-size: 0.8em;
        }}
        .reset-btn:hover, .toggle-btn:hover {{ background: #4f8ef7; color: #fff; }}
        .toggle-btn.active {{ background: #4f8ef7; color: #fff; }}
    </style>
</head>
<body>
    <h1>Signal Reader Dashboard</h1>
    <p>Tracking: {", ".join(TICKERS)} &nbsp;|&nbsp; Interval: {INTERVAL} &nbsp;|&nbsp; Period: {PERIOD}</p>
    <div class="grid">{cards}</div>
    <p class="meta">Generated: {generated} — auto-refreshes every 60 seconds (keep run.py running)</p>
    <script>
        var charts    = {{}};
        var chartMode = {{}};
        var chartData = {{}};

        var zoomPlugin = {{
            zoom: {{ wheel: {{ enabled: true }}, pinch: {{ enabled: true }}, mode: 'x' }},
            pan:  {{ enabled: true, mode: 'x' }}
        }};

        function buildChart(ticker) {{
            var wrap = document.getElementById('wrap-' + ticker);
            wrap.innerHTML = '<canvas id="chart-' + ticker + '"></canvas>';
            var ctx  = document.getElementById('chart-' + ticker).getContext('2d');
            var d    = chartData[ticker];
            var mode = chartMode[ticker];

            if (mode === 'candlestick') {{
                return new Chart(ctx, {{
                    type: 'candlestick',
                    data: {{
                        datasets: [{{
                            label: ticker,
                            data: d.ohlc,
                            yAxisID: 'y',
                            color: {{ up: '#4caf50', down: '#f44336', unchanged: '#aaa' }}
                        }}, {{
                            label: 'Volume',
                            type: 'bar',
                            data: d.volumesTs,
                            backgroundColor: 'rgba(150,150,150,0.3)',
                            yAxisID: 'y2'
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        scales: {{
                            x:  {{ type: 'time', time: {{ unit: 'minute' }}, ticks: {{ maxTicksLimit: 10 }} }},
                            y:  {{ position: 'left',  title: {{ display: true, text: 'Price (USD)' }} }},
                            y2: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Volume' }} }}
                        }},
                        plugins: {{ zoom: zoomPlugin }}
                    }}
                }});
            }} else {{
                return new Chart(ctx, {{
                    type: 'line',
                    data: {{
                        labels: d.labels,
                        datasets: [{{
                            label: ticker + ' Close',
                            data: d.closes,
                            borderColor: '#4f8ef7',
                            backgroundColor: 'rgba(79,142,247,0.1)',
                            tension: 0.2,
                            pointRadius: 2,
                            yAxisID: 'y'
                        }}, {{
                            label: 'Volume',
                            type: 'bar',
                            data: d.volumes,
                            backgroundColor: 'rgba(150,150,150,0.3)',
                            yAxisID: 'y2'
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        interaction: {{ mode: 'index', intersect: false }},
                        scales: {{
                            y:  {{ position: 'left',  title: {{ display: true, text: 'Price (USD)' }} }},
                            y2: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Volume' }} }}
                        }},
                        plugins: {{ zoom: zoomPlugin }}
                    }}
                }});
            }}
        }}

        function toggleChart(ticker) {{
            chartMode[ticker] = chartMode[ticker] === 'line' ? 'candlestick' : 'line';
            charts[ticker].destroy();
            charts[ticker] = buildChart(ticker);
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

        {charts_js}
    </script>
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
        print(f"Signals flagged when price moves >1% or volume spikes >2x average.")
