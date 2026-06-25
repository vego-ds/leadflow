"""
Dashboard — a single self-contained HTML page (Chart.js via CDN) that polls
GET /metrics and renders four charts. No backend state of its own: this
module only generates static HTML/JS; all data comes from the browser
fetching /metrics client-side, parsing the Prometheus text format itself.
"""
from __future__ import annotations


def get_dashboard_html() -> str:
    """Generate the dashboard HTML with embedded Chart.js and fetch logic."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>LeadFlow Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background: #f5f5f5;
                margin: 0;
                padding: 20px;
            }
            .container { max-width: 1200px; margin: 0 auto; }
            h1 { color: #333; text-align: center; margin-bottom: 30px; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px; }
            .card {
                background: white;
                border-radius: 8px;
                padding: 20px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            }
            .card h2 { margin-top: 0; font-size: 18px; color: #333; }
            .chart-container { position: relative; height: 300px; }
            .refresh-info { text-align: center; color: #999; font-size: 12px; margin-top: 10px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>LeadFlow Pipeline Dashboard</h1>
            <div class="grid">
                <div class="card">
                    <h2>Ingest by Source</h2>
                    <div class="chart-container"><canvas id="ingestSource"></canvas></div>
                    <div class="refresh-info">Updates every 30s</div>
                </div>
                <div class="card">
                    <h2>Ingest by Language</h2>
                    <div class="chart-container"><canvas id="ingestLanguage"></canvas></div>
                </div>
                <div class="card">
                    <h2>Outreach Outcomes</h2>
                    <div class="chart-container"><canvas id="outreachOutcomes"></canvas></div>
                </div>
                <div class="card">
                    <h2>Conversions by Source</h2>
                    <div class="chart-container"><canvas id="conversionSource"></canvas></div>
                </div>
            </div>
        </div>

        <script>
        // Prometheus text format parser: extract metric values by label combinations
        function parseMetrics(text) {
            const metrics = {};
            text.split('\\n').forEach(line => {
                if (line.startsWith('#') || !line.trim()) return;
                const match = line.match(/^([a-z_]+)\\{([^}]*)\\}\\s+([0-9.]+)/);
                if (!match) return;
                const [, name, labels, value] = match;
                if (!metrics[name]) metrics[name] = [];
                const labelObj = {};
                labels.split(',').forEach(l => {
                    const [k, v] = l.split('=');
                    labelObj[k] = v.replace(/"/g, '');
                });
                metrics[name].push({ labels: labelObj, value: parseFloat(value) });
            });
            return metrics;
        }

        let charts = {};

        async function updateDashboard() {
            try {
                const response = await fetch('/metrics');
                const text = await response.text();
                const metrics = parseMetrics(text);

                // Ingest by source
                const ingestBySource = {};
                (metrics.leadflow_ingest_total || []).forEach(m => {
                    ingestBySource[m.labels.source] = m.value;
                });
                updateChart('ingestSource', 'doughnut', ingestBySource);

                // Ingest by language
                const ingestByLang = {};
                (metrics.leadflow_ingest_total || []).forEach(m => {
                    ingestByLang[m.labels.language] = m.value;
                });
                updateChart('ingestLanguage', 'bar', ingestByLang);

                // Outreach outcomes
                const outcomes = {};
                (metrics.leadflow_outreach_attempt_total || []).forEach(m => {
                    outcomes[m.labels.outcome] = m.value;
                });
                updateChart('outreachOutcomes', 'doughnut', outcomes);

                // Conversions by source
                const convBySource = {};
                (metrics.leadflow_conversion_total || []).forEach(m => {
                    convBySource[m.labels.source] = m.value;
                });
                updateChart('conversionSource', 'bar', convBySource);
            } catch (e) {
                console.error('Failed to fetch metrics:', e);
            }
        }

        function updateChart(canvasId, type, data) {
            const ctx = document.getElementById(canvasId).getContext('2d');
            const colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF'];
            const labels = Object.keys(data);
            const values = labels.map(l => data[l]);

            if (charts[canvasId]) {
                charts[canvasId].destroy();
            }

            charts[canvasId] = new Chart(ctx, {
                type: type,
                data: {
                    labels: labels,
                    datasets: [{
                        data: values,
                        backgroundColor: colors.slice(0, labels.length),
                        borderColor: '#fff',
                        borderWidth: 2,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    // Bar charts already label each bar on the x-axis; a
                    // one-entry dataset legend would just say "undefined".
                    plugins: { legend: { display: type !== 'bar', position: 'bottom' } },
                },
            });
        }

        // Initial load and refresh every 30s
        updateDashboard();
        setInterval(updateDashboard, 30000);
        </script>
    </body>
    </html>
    """
    return html
