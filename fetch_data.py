"""
fetch_data.py — pulls 1-minute OHLC data from Yahoo Finance and generates dashboard.html

Run with: python3 fetch_data.py
Then open dashboard.html in your browser.
Note: Yahoo Finance only provides 1-minute data for the last 7 days.
"""

import json
import os
import yfinance as yf
from datetime import datetime

TICKERS = ["NVDA", "TSLA", "AMD", "COIN", "META", "PLTR", "MSTR", "SMCI", "NFLX", "HOOD"]


def fetch(ticker):
    data = yf.download(ticker, period="5d", interval="1m", progress=False, auto_adjust=True)
    if data.empty:
        print(f"  Warning: no data returned for {ticker}")
        return None

    timestamps  = [int(d.timestamp() * 1000) for d in data.index]
    opens       = [round(float(v), 2) for v in data["Open"].squeeze().tolist()]
    highs       = [round(float(v), 2) for v in data["High"].squeeze().tolist()]
    lows        = [round(float(v), 2) for v in data["Low"].squeeze().tolist()]
    closes      = [round(float(v), 2) for v in data["Close"].squeeze().tolist()]
    volumes     = [int(v) for v in data["Volume"].squeeze().tolist()]
    labels      = [str(d) for d in data.index]
    dates       = sorted(set(str(d.date()) for d in data.index))

    ohlc        = [{"x": ts, "o": o, "h": h, "l": l, "c": c}
                   for ts, o, h, l, c in zip(timestamps, opens, highs, lows, closes)]
    volumes_ts  = [{"x": ts, "y": v} for ts, v in zip(timestamps, volumes)]

    return {
        "ticker":     ticker,
        "labels":     labels,
        "closes":     closes,
        "volumes":    volumes,
        "ohlc":       ohlc,
        "volumes_ts": volumes_ts,
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
    signals    = []
    closes     = asset["closes"]
    volumes    = asset["volumes"]
    labels     = asset["labels"]
    avg_volume = sum(volumes) / len(volumes) if volumes else 1

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

    ticker_tabs = "".join(
        f'<button class="tab{"" if i else " active"}" id="tab-{a["ticker"]}" '
        f'onclick="showTicker(\'{a["ticker"]}\')">{a["ticker"]}</button>'
        for i, a in enumerate(assets)
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

        labels     = json.dumps(asset["labels"])
        closes     = json.dumps(asset["closes"])
        volumes    = json.dumps(asset["volumes"])
        ohlc       = json.dumps(asset["ohlc"])
        volumes_ts = json.dumps(asset["volumes_ts"])

        charts_js += f"""
        chartData['{ticker}'] = {{
            labels:    {labels},
            closes:    {closes},
            volumes:   {volumes},
            ohlc:      {ohlc},
            volumesTs: {volumes_ts}
        }};
        chartMode['{ticker}'] = 'candlestick';
        charts['{ticker}']    = buildChart('{ticker}', '{default_date}');
        """

    exercises_path = os.path.join(os.path.dirname(__file__), "exercises.json")
    exercises = []
    if os.path.exists(exercises_path):
        with open(exercises_path) as f:
            exercises = json.load(f)

    pnl_section = ""
    if exercises:
        ex_cards = ""
        for ex in exercises:
            trade_rows = ""
            for t in ex["trades"]:
                pnl_class = "pnl-win" if t["pnl"] >= 0 else "pnl-loss"
                trade_rows += (
                    f'<tr>'
                    f'<td>{t["ticker"]}</td>'
                    f'<td>{t["time"]}</td>'
                    f'<td>${t["entry"]:.2f}</td>'
                    f'<td>${t["allocated"]:.2f}</td>'
                    f'<td>${t["eod"]:.2f}</td>'
                    f'<td class="{pnl_class}">${t["pnl"]:+.2f} ({t["pnl_pct"]:+.2f}%)</td>'
                    f'</tr>'
                )
            total_class = "pnl-win" if ex["total_pnl"] >= 0 else "pnl-loss"
            ex_cards += f"""
            <div class="ex-card">
                <div class="ex-header">
                    <span class="ex-title">{ex["title"]}</span>
                    <span class="ex-date">{ex["date"]}</span>
                    <span class="ex-summary {total_class}">
                        ${ex["portfolio_eod"]:.2f} &nbsp;|&nbsp; {ex["total_pnl"]:+.2f} ({ex["total_pnl_pct"]:+.2f}%)
                    </span>
                </div>
                <table>
                    <thead><tr><th>Ticker</th><th>Entry Time</th><th>Entry $</th><th>Allocated</th><th>EOD $</th><th>P&L</th></tr></thead>
                    <tbody>{trade_rows}</tbody>
                    <tfoot>
                        <tr class="ex-totals">
                            <td colspan="3"></td>
                            <td>${ex["total_invested"]:.2f} deployed</td>
                            <td>${ex["eod_value"]:.2f}</td>
                            <td class="{total_class}">${ex["total_pnl"]:+.2f} ({ex["total_pnl_pct"]:+.2f}%)</td>
                        </tr>
                    </tfoot>
                </table>
            </div>
            """
        pnl_section = f"""
        <div id="pnl-panel" style="display:none">
            <div class="section-header">P&amp;L Tracker</div>
            <div class="pnl-tracker">{ex_cards}</div>
        </div>
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
        body         {{ font-family: sans-serif; background: #0f0f1a; color: #e0e0e0; margin: 0; padding: 20px; }}
        h1           {{ color: #4f8ef7; }}
        .controls    {{ display: flex; align-items: center; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }}
        .nav         {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 12px; }}
        .tabs        {{ display: flex; gap: 6px; flex-wrap: wrap; }}
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
    </style>
</head>
<body>
    <h1>Signal Reader Dashboard</h1>
    <div class="nav">
        <div class="controls">
            <div class="tabs">{ticker_tabs}</div>
            <span class="date-label" id="day-label">Day:</span>
            <select id="date-select" onchange="changeDate(this.value)">{date_options}</select>
            <span class="date-label" style="color:#555" id="interval-label">Interval: 1m</span>
        </div>
        <button class="tab" id="tab-pnl" onclick="showPnL()" style="margin-left:auto">P&amp;L</button>
    </div>
    <div id="chart-panel">{cards}</div>
    {pnl_section}
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
            var d = chartData[ticker];
            var idx = d.labels.map((l, i) => l.startsWith(dateStr) ? i : -1).filter(i => i >= 0);
            return {{
                labels:    idx.map(i => d.labels[i]),
                closes:    idx.map(i => d.closes[i]),
                volumes:   idx.map(i => d.volumes[i]),
                ohlc:      idx.map(i => d.ohlc[i]),
                volumesTs: idx.map(i => d.volumesTs[i]),
            }};
        }}

        function buildChart(ticker, dateStr) {{
            var wrap = document.getElementById('wrap-' + ticker);
            wrap.innerHTML = '<canvas id="chart-' + ticker + '"></canvas>';
            var ctx  = document.getElementById('chart-' + ticker).getContext('2d');
            var d    = filterData(ticker, dateStr);
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

        function showTicker(ticker) {{
            document.querySelectorAll('.card').forEach(c => c.style.display = 'none');
            document.getElementById('card-' + ticker).style.display = 'block';
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + ticker).classList.add('active');
            document.getElementById('chart-panel').style.display = 'block';
            var pnl = document.getElementById('pnl-panel');
            if (pnl) pnl.style.display = 'none';
            document.getElementById('date-select').style.display = '';
            document.getElementById('day-label').style.display = '';
            document.getElementById('interval-label').style.display = '';
        }}

        function showPnL() {{
            document.getElementById('chart-panel').style.display = 'none';
            var pnl = document.getElementById('pnl-panel');
            if (pnl) pnl.style.display = 'block';
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-pnl').classList.add('active');
            document.getElementById('date-select').style.display = 'none';
            document.getElementById('day-label').style.display = 'none';
            document.getElementById('interval-label').style.display = 'none';
        }}

        function toggleSection(el) {{
            var body = el.nextElementSibling;
            var open = body.style.display !== 'none';
            body.style.display = open ? 'none' : 'block';
            el.textContent = (open ? '▶' : '▼') + ' Signals';
        }}

        function changeDate(dateStr) {{
            currentDate = dateStr;
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
        out = os.path.join(os.path.dirname(__file__) or ".", "dashboard.html")
        with open(out, "w") as f:
            f.write(html)
        print(f"\nDone! Open Signal/dashboard.html in your browser.")
        print(f"Signals flagged when price moves >1% or volume spikes >2x average.")
