"""Embedded HTML dashboard served at http://localhost:8080/.

Single self-contained file: HTML + CSS + vanilla JS. No React, no build,
no static directory. The page polls ``/api/metrics`` every 2s and rebuilds
its tables.

Rendered into the response by ``ops.metrics.make_app``.
"""

from __future__ import annotations

HTML_DASHBOARD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>poly-quoter</title>
<style>
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #7d8590; --accent: #58a6ff;
    --green: #3fb950; --red: #f85149; --yellow: #d29922; --border: #30363d;
    --card: #161b22;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 13px/1.4 'SF Mono', 'Menlo', 'Consolas', monospace;
         background: var(--bg); color: var(--fg); padding: 16px; }
  h1, h2 { margin: 0 0 8px 0; font-weight: 600; }
  h1 { font-size: 18px; color: var(--accent); }
  h2 { font-size: 14px; color: var(--muted); margin-top: 16px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 12px; margin-bottom: 16px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 6px;
          padding: 12px; }
  .card .lbl { color: var(--muted); font-size: 11px; text-transform: uppercase;
               letter-spacing: 0.5px; }
  .card .val { font-size: 20px; font-weight: 600; margin-top: 4px; }
  .val.pos { color: var(--green); }
  .val.neg { color: var(--red); }
  .val.warn { color: var(--yellow); }
  table { width: 100%; border-collapse: collapse; font-size: 12px;
          background: var(--card); border: 1px solid var(--border); border-radius: 6px;
          overflow: hidden; }
  th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--border); }
  th { background: #0d1117; color: var(--muted); font-weight: 600;
       text-transform: uppercase; font-size: 10px; letter-spacing: 0.5px; }
  tr:last-child td { border-bottom: none; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.muted { color: var(--muted); }
  .pill { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px;
          font-weight: 600; }
  .pill.yes { background: #1f6feb33; color: #58a6ff; }
  .pill.no { background: #6e764033; color: #d4a72c; }
  .pill.stopped { background: #f8514933; color: #f85149; }
  .pill.running { background: #3fb95033; color: #3fb950; }
  .status-bar { display: flex; justify-content: space-between; align-items: baseline;
                margin-bottom: 16px; }
  .stale { color: var(--red); font-weight: 600; }
  .scroll { max-height: 320px; overflow-y: auto; }
</style>
</head>
<body>
<div class="status-bar">
  <h1>poly-quoter <span id="mode-pill" class="pill"></span></h1>
  <div class="muted">
    <span id="updated-at">—</span> · refresh
    <select id="refresh-rate">
      <option value="1000">1s</option>
      <option value="2000" selected>2s</option>
      <option value="5000">5s</option>
      <option value="0">off</option>
    </select>
  </div>
</div>

<h2>Stats</h2>
<div class="grid" id="stats-grid">
  <div class="card"><div class="lbl">Realized PnL</div><div class="val" id="pnl">—</div></div>
  <div class="card"><div class="lbl">Fills</div><div class="val" id="fills">—</div></div>
  <div class="card"><div class="lbl">Open markets</div><div class="val" id="open-markets">—</div></div>
  <div class="card"><div class="lbl">Live quotes</div><div class="val" id="live-quotes">—</div></div>
  <div class="card"><div class="lbl">Posts / Cancels</div><div class="val" id="posts-cancels">—</div></div>
  <div class="card"><div class="lbl">Quoter ticks</div><div class="val" id="ticks">—</div></div>
  <div class="card"><div class="lbl">Risk</div><div class="val" id="risk">—</div></div>
  <div class="card"><div class="lbl">Bankroll</div><div class="val" id="bankroll">—</div></div>
</div>

<h2>Active markets</h2>
<table id="markets-tbl">
  <thead><tr>
    <th>Market</th><th>Asset</th><th>TF</th>
    <th class="num">Mid YES</th><th class="num">Spread</th>
    <th class="num">Our quotes</th><th class="num">Expires in</th>
  </tr></thead>
  <tbody id="markets-body"><tr><td colspan="7" class="muted">loading…</td></tr></tbody>
</table>

<h2>Positions</h2>
<table id="positions-tbl">
  <thead><tr>
    <th>Market</th><th class="num">YES qty</th><th class="num">YES avg</th>
    <th class="num">NO qty</th><th class="num">NO avg</th>
    <th class="num">Matched</th><th class="num">Net</th><th class="num">Cost</th>
  </tr></thead>
  <tbody id="positions-body"><tr><td colspan="8" class="muted">no open positions</td></tr></tbody>
</table>

<h2>Recent fills <span class="muted" id="fills-count"></span></h2>
<div class="scroll">
  <table id="fills-tbl">
    <thead><tr>
      <th>Time</th><th>Market</th><th>Side</th>
      <th class="num">Price</th><th class="num">Qty</th><th class="num">Cost</th>
    </tr></thead>
    <tbody id="fills-body"><tr><td colspan="6" class="muted">no fills yet</td></tr></tbody>
  </table>
</div>

<script>
let timer = null;

function fmtPrice(p) { return p == null ? '—' : Number(p).toFixed(3); }
function fmtUsd(v)   { return v == null ? '—' : '$' + Number(v).toFixed(2); }
function fmtTime(t)  {
  if (!t) return '—';
  const d = new Date(t * 1000);
  return d.toLocaleTimeString();
}
function shortId(s, n=10) { return s ? (String(s).slice(0, n) + '…') : '—'; }
function pnlClass(v) { return v > 0 ? 'pos' : (v < 0 ? 'neg' : ''); }

async function poll() {
  try {
    const [m, p, mk, f] = await Promise.all([
      fetch('/api/metrics').then(r => r.json()),
      fetch('/api/positions').then(r => r.json()),
      fetch('/api/markets').then(r => r.json()),
      fetch('/api/fills?limit=50').then(r => r.json()),
    ]);
    renderMetrics(m);
    renderPositions(p);
    renderMarkets(mk);
    renderFills(f);
    document.getElementById('updated-at').textContent =
      'updated ' + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById('updated-at').innerHTML =
      '<span class="stale">DISCONNECTED ' + new Date().toLocaleTimeString() + '</span>';
  }
}

function renderMetrics(m) {
  const inv = m.inventory || {};
  const ex = m.executor || {};
  const q = m.quoter || {};
  const r = m.risk || {};

  const modeEl = document.getElementById('mode-pill');
  modeEl.textContent = m.mode || '?';
  modeEl.className = 'pill ' + (r.stopped ? 'stopped' : 'running');

  const pnl = inv.realized_pnl || 0;
  const pnlEl = document.getElementById('pnl');
  pnlEl.textContent = fmtUsd(pnl);
  pnlEl.className = 'val ' + pnlClass(pnl);

  document.getElementById('fills').textContent = (inv.n_fills || 0) + ' / ' +
    (ex.cumulative_fills || 0);
  document.getElementById('open-markets').textContent = inv.open_markets || 0;
  document.getElementById('live-quotes').textContent = ex.live_quotes_total || 0;
  document.getElementById('posts-cancels').textContent =
    (ex.cumulative_posts || 0) + ' / ' + (ex.cumulative_cancels || 0);
  document.getElementById('ticks').textContent = q.tick_count || 0;

  const riskEl = document.getElementById('risk');
  if (r.stopped) {
    riskEl.innerHTML = '<span class="val warn">STOPPED</span>';
    riskEl.title = r.reason || '';
  } else {
    riskEl.textContent = 'armed';
    riskEl.className = 'val pos';
  }

  document.getElementById('bankroll').textContent = fmtUsd(m.bankroll);
}

function renderPositions(d) {
  const body = document.getElementById('positions-body');
  const rows = (d.positions || []);
  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="8" class="muted">no open positions</td></tr>';
    return;
  }
  body.innerHTML = rows.map(r => `
    <tr>
      <td>${shortId(r.market_id)}</td>
      <td class="num">${r.yes_qty}</td>
      <td class="num">${fmtPrice(r.yes_avg)}</td>
      <td class="num">${r.no_qty}</td>
      <td class="num">${fmtPrice(r.no_avg)}</td>
      <td class="num">${r.matched}</td>
      <td class="num">${r.net >= 0 ? '+' : ''}${r.net}</td>
      <td class="num">${fmtUsd(r.total_cost)}</td>
    </tr>
  `).join('');
}

function renderMarkets(d) {
  const body = document.getElementById('markets-body');
  const rows = d.markets || [];
  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="7" class="muted">no active markets</td></tr>';
    return;
  }
  body.innerHTML = rows.map(r => `
    <tr>
      <td>${shortId(r.market_id)}</td>
      <td>${r.asset}</td>
      <td>${r.timeframe}</td>
      <td class="num">${fmtPrice(r.mid_yes)}</td>
      <td class="num">${fmtPrice(r.spread)}</td>
      <td class="num">${r.our_quotes || 0}</td>
      <td class="num">${r.expires_in != null ? r.expires_in + 's' : '—'}</td>
    </tr>
  `).join('');
}

function renderFills(d) {
  const rows = d.fills || [];
  document.getElementById('fills-count').textContent =
    rows.length ? `(${rows.length} of ${d.total || rows.length})` : '';
  const body = document.getElementById('fills-body');
  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="6" class="muted">no fills yet</td></tr>';
    return;
  }
  body.innerHTML = rows.map(r => `
    <tr>
      <td>${fmtTime(r.ts)}</td>
      <td>${shortId(r.market_id)}</td>
      <td><span class="pill ${r.side.toLowerCase()}">${r.side}</span></td>
      <td class="num">${fmtPrice(r.price)}</td>
      <td class="num">${r.qty}</td>
      <td class="num">${fmtUsd(r.cost)}</td>
    </tr>
  `).join('');
}

function setRefresh(ms) {
  if (timer) clearInterval(timer);
  if (ms > 0) timer = setInterval(poll, ms);
}

document.getElementById('refresh-rate').addEventListener('change', e => {
  setRefresh(parseInt(e.target.value, 10));
});
poll();
setRefresh(2000);
</script>
</body>
</html>"""
