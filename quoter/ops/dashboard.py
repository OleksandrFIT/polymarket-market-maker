"""Embedded single-page HTML dashboard.

No build step, no React, no external assets — one constant string served at
``GET /``. JavaScript polls ``/api/*`` endpoints; filters drive query params.
Click any market row → modal with full fill timeline + per-side breakdown.
"""

from __future__ import annotations

HTML_DASHBOARD = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>poly-quoter</title>
<style>
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #7d8590;
    --accent: #58a6ff; --accent2: #a371f7;
    --green: #3fb950; --green-bg: #0d2818;
    --red: #f85149; --red-bg: #2d1416;
    --yellow: #d29922; --border: #30363d; --card: #161b22;
    --hover: #1f2937;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 13px; line-height: 1.5; }
  body { padding: 14px 18px; }
  h1 { font-size: 18px; margin: 0 0 6px; color: var(--accent); }
  h2 { font-size: 13px; margin: 18px 0 6px; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
  .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 12px;
    font-size: 11px; font-weight: 600; }
  .pill.shadow { background: var(--accent); color: var(--bg); }
  .pill.paper { background: var(--green); color: #fff; }
  .pill.live { background: var(--red); color: #fff; }
  .pill.win { background: var(--green-bg); color: var(--green); border: 1px solid var(--green); }
  .pill.loss { background: var(--red-bg); color: var(--red); border: 1px solid var(--red); }
  .pill.be { background: #21262d; color: var(--muted); border: 1px solid var(--border); }
  .pill.yes { background: rgba(63, 185, 80, 0.15); color: var(--green); }
  .pill.no { background: rgba(248, 81, 73, 0.15); color: var(--red); }
  .pill.tf { background: #1f2937; color: var(--accent2); }
  .pill.asset { background: #1f2937; color: #ffb020; }
  .pill.trading { background: rgba(63,185,80,0.18); color: var(--green); border: 1px solid var(--green); }
  .pill.expired { background: rgba(210,153,34,0.18); color: var(--yellow); border: 1px solid var(--yellow); }
  .pill.resolved-yes { background: rgba(63,185,80,0.25); color: var(--green); border: 1px solid var(--green); }
  .pill.resolved-no { background: rgba(248,81,73,0.25); color: var(--red); border: 1px solid var(--red); }
  .muted { color: var(--muted); }

  /* Period stats grid */
  .periods { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 10px 0; }
  .period { background: var(--card); border: 1px solid var(--border); border-radius: 6px;
    padding: 12px; }
  .period .label { font-size: 10px; color: var(--muted); text-transform: uppercase;
    letter-spacing: 0.05em; font-weight: 600; }
  .period .meta { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .period .pnl { font-size: 22px; font-weight: 700; margin: 6px 0 4px; }
  .period .sub { display: flex; gap: 12px; font-size: 11px; color: var(--muted); }
  .period .sub strong { color: var(--fg); font-weight: 600; }

  .grid { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
  .card { background: var(--card); border: 1px solid var(--border); padding: 10px 12px;
    border-radius: 6px; }
  .card .label { font-size: 10px; color: var(--muted); text-transform: uppercase;
    letter-spacing: 0.05em; }
  .card .value { font-size: 18px; font-weight: 600; margin-top: 3px; }
  .positive { color: var(--green); }
  .negative { color: var(--red); }

  .filters { background: var(--card); border: 1px solid var(--border); border-radius: 6px;
    padding: 10px 12px; margin: 10px 0; display: flex; gap: 14px; flex-wrap: wrap;
    align-items: center; }
  .filters .group { display: flex; gap: 6px; align-items: center; }
  .filters .sep { color: var(--border); }
  button.fbtn { background: #21262d; color: var(--fg); border: 1px solid var(--border);
    padding: 3px 10px; border-radius: 4px; cursor: pointer; font-family: inherit;
    font-size: 12px; }
  button.fbtn:hover { background: var(--hover); }
  button.fbtn.active { background: var(--accent); color: var(--bg); border-color: var(--accent); }
  .filters input[type="number"] { width: 60px; background: #21262d; color: var(--fg);
    border: 1px solid var(--border); padding: 3px 6px; border-radius: 4px; font-family: inherit; }
  .filters select { background: #21262d; color: var(--fg); border: 1px solid var(--border);
    padding: 3px 8px; border-radius: 4px; font-family: inherit; font-size: 12px; }

  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  thead th { text-align: left; padding: 7px 8px; color: var(--muted); font-weight: 500;
    border-bottom: 1px solid var(--border); text-transform: uppercase;
    letter-spacing: 0.04em; font-size: 10px; position: sticky; top: 0; background: var(--bg); }
  tbody td { padding: 6px 8px; border-bottom: 1px solid var(--border); }
  tbody tr:hover { background: var(--hover); }
  tbody tr.clickable { cursor: pointer; }
  tbody tr.win-row { background: linear-gradient(90deg, var(--green-bg) 0%, transparent 12%); }
  tbody tr.loss-row { background: linear-gradient(90deg, var(--red-bg) 0%, transparent 12%); }
  .right { text-align: right; }
  .center { text-align: center; }
  .mono-id { font-family: ui-monospace, monospace; font-size: 11px; color: var(--muted); }
  .scroll { max-height: 380px; overflow-y: auto; border: 1px solid var(--border);
    border-radius: 6px; }
  .scroll-tall { max-height: 540px; }

  .risk-btn { background: #2d1416; color: var(--red); border: 1px solid var(--red);
    padding: 5px 12px; border-radius: 4px; cursor: pointer; font-family: inherit;
    font-size: 12px; font-weight: 500; }
  .risk-btn:hover { background: var(--red); color: #fff; }

  .modal-bg { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.75);
    z-index: 100; align-items: center; justify-content: center; }
  .modal-bg.show { display: flex; }
  .modal { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    padding: 20px; max-width: 900px; max-height: 80vh; overflow-y: auto;
    width: 90%; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
  .modal h2 { color: var(--accent); margin-top: 0; }
  .close-x { float: right; cursor: pointer; color: var(--muted); font-size: 20px; padding: 0 8px; }
  .close-x:hover { color: var(--red); }
</style>
</head>
<body>

<div class="row" style="justify-content: space-between;">
  <h1>poly-quoter <span id="mode" class="pill paper" style="vertical-align: middle;">paper</span></h1>
  <div class="muted">
    updated <span id="updated">—</span>
    · refresh
    <select id="refresh-rate" onchange="setRefresh()">
      <option value="2000" selected>2s</option>
      <option value="1000">1s</option>
      <option value="5000">5s</option>
      <option value="0">off</option>
    </select>
  </div>
</div>

<div class="row" style="margin: 10px 0;">
  <div class="card" style="display: flex; gap: 10px; align-items: center; flex: 1;">
    <span class="muted">RISK LIMIT</span>
    <strong id="risk-usd">—</strong> / <strong id="risk-pct">—</strong>%
    <input type="number" id="risk-input" placeholder="100" min="1" max="100" step="1">
    <span class="muted">%</span>
    <button class="fbtn" onclick="setRisk()">Apply</button>
    <span id="risk-status" class="muted"></span>
  </div>
  <button class="risk-btn" onclick="clearAll()">🗑 Clear all data</button>
</div>

<h2>Tactic settings (live — applies next window)</h2>
<div class="card" id="settings-card">
  <div id="settings-fields" class="row" style="flex-wrap: wrap; gap: 12px;"></div>
  <span id="settings-status" class="muted"></span>
</div>

<h2>P&amp;L by period</h2>
<div class="periods">
  <div class="period">
    <div class="label">All time</div>
    <div class="meta" id="p-all-meta">—</div>
    <div class="pnl" id="p-all-pnl">—</div>
    <div class="sub" id="p-all-sub">—</div>
  </div>
  <div class="period">
    <div class="label">Today</div>
    <div class="meta" id="p-today-meta">since midnight (local)</div>
    <div class="pnl" id="p-today-pnl">—</div>
    <div class="sub" id="p-today-sub">—</div>
  </div>
  <div class="period">
    <div class="label">Last session</div>
    <div class="meta" id="p-last-meta">—</div>
    <div class="pnl" id="p-last-pnl">—</div>
    <div class="sub" id="p-last-sub">—</div>
  </div>
  <div class="period">
    <div class="label">Current session</div>
    <div class="meta" id="p-now-meta">—</div>
    <div class="pnl" id="p-now-pnl">—</div>
    <div class="sub" id="p-now-sub">—</div>
  </div>
</div>

<h2>Live stats</h2>
<div class="grid">
  <div class="card"><div class="label">Realized PnL</div><div class="value" id="pnl">—</div></div>
  <div class="card"><div class="label">Fills</div><div class="value" id="fills">—</div></div>
  <div class="card"><div class="label">Open / Resolved</div><div class="value" id="open-res">—</div></div>
  <div class="card"><div class="label">Win Rate</div><div class="value" id="winrate">—</div></div>
  <div class="card"><div class="label">ROI on Cost</div><div class="value" id="roi">—</div></div>
  <div class="card"><div class="label">Live Quotes</div><div class="value" id="live-q">—</div></div>
  <div class="card"><div class="label">Posts / Cancels</div><div class="value" id="posts-cancels">—</div></div>
  <div class="card"><div class="label">Quoter Ticks</div><div class="value" id="ticks">—</div></div>
  <div class="card"><div class="label">Risk</div><div class="value" id="risk-state">armed</div></div>
  <div class="card"><div class="label">Bankroll</div><div class="value" id="bankroll">—</div></div>
</div>

<h2>Filters (apply to fills + resolved bets)</h2>
<div class="filters">
  <div class="group">
    <span class="muted">Asset:</span>
    <button class="fbtn" data-asset="" onclick="setAsset('')">All</button>
    <button class="fbtn" data-asset="BTC" onclick="setAsset('BTC')">BTC</button>
    <button class="fbtn" data-asset="ETH" onclick="setAsset('ETH')">ETH</button>
  </div>
  <span class="sep">|</span>
  <div class="group">
    <span class="muted">Timeframe:</span>
    <button class="fbtn" data-tf="" onclick="setTf('')">All</button>
    <button class="fbtn" data-tf="5m" onclick="setTf('5m')">5m</button>
    <button class="fbtn" data-tf="15m" onclick="setTf('15m')">15m</button>
  </div>
  <span class="sep">|</span>
  <div class="group">
    <span class="muted">Result:</span>
    <button class="fbtn" data-result="" onclick="setResult('')">All</button>
    <button class="fbtn" data-result="win" onclick="setResult('win')">Wins ✅</button>
    <button class="fbtn" data-result="loss" onclick="setResult('loss')">Losses ❌</button>
  </div>
  <span class="sep">|</span>
  <div class="group">
    <span class="muted">Time:</span>
    <select id="since-sel" onchange="setSince(this.value)">
      <option value="">All time</option>
      <option value="900">Last 15m</option>
      <option value="3600">Last 1h</option>
      <option value="10800">Last 3h</option>
      <option value="86400">Last 24h</option>
    </select>
  </div>
</div>

<h2>Stats by asset / timeframe</h2>
<div class="scroll" style="max-height: 240px;">
  <table>
    <thead>
      <tr>
        <th>Asset</th><th>TF</th>
        <th class="right">n</th>
        <th class="right">Wins</th><th class="right">Losses</th>
        <th class="right">Win rate</th>
        <th class="right">Cost</th><th class="right">P&amp;L</th><th class="right">ROI %</th>
      </tr>
    </thead>
    <tbody id="stats-tbody"></tbody>
  </table>
</div>

<h2>Active markets (live)</h2>
<div class="scroll" style="max-height: 260px;">
  <table>
    <thead>
      <tr>
        <th>Market</th><th>Asset</th><th>TF</th><th>Window</th>
        <th>Status</th>
        <th class="right">Mid YES</th><th class="right">Spread</th>
        <th class="right">Our quotes</th><th class="right">Expires in</th>
      </tr>
    </thead>
    <tbody id="markets-tbody"></tbody>
  </table>
</div>

<h2>Open positions</h2>
<div class="scroll" style="max-height: 260px;">
  <table>
    <thead>
      <tr>
        <th>Market</th><th>Asset</th><th>TF</th><th>Window</th>
        <th>Status</th>
        <th class="right">Live quotes</th>
        <th class="right">YES qty</th><th class="right">YES avg</th>
        <th class="right">NO qty</th><th class="right">NO avg</th>
        <th class="right">Matched</th><th class="right">Net</th>
        <th class="right">Cost</th>
      </tr>
    </thead>
    <tbody id="positions-tbody"></tbody>
  </table>
</div>

<h2>Resolved bets <span id="resolved-count" class="muted"></span></h2>
<div class="scroll scroll-tall">
  <table>
    <thead>
      <tr>
        <th>Resolved</th><th>Market</th><th>Asset</th><th>TF</th>
        <th>Winner</th>
        <th class="right">YES</th><th class="right">NO</th>
        <th class="right">Cost</th><th class="right">P&amp;L</th><th class="right">ROI</th>
      </tr>
    </thead>
    <tbody id="resolved-tbody"></tbody>
  </table>
</div>

<h2>Recent fills <span id="fills-count" class="muted"></span></h2>
<div class="scroll scroll-tall">
  <table>
    <thead>
      <tr>
        <th>Time</th><th>Market</th><th>Asset</th><th>TF</th>
        <th>Side</th><th class="right">Price</th><th class="right">Qty</th>
        <th class="right">Cost</th>
      </tr>
    </thead>
    <tbody id="fills-tbody"></tbody>
  </table>
</div>

<div id="modal" class="modal-bg" onclick="if(event.target.id==='modal')closeModal()">
  <div class="modal">
    <span class="close-x" onclick="closeModal()">✕</span>
    <h2 id="modal-title">Market detail</h2>
    <div id="modal-body" class="muted">Loading…</div>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
let refreshMs = 2000;
let refreshTimer = null;
let filter = { asset: '', tf: '', result: '', since: '' };

function setRefresh() {
  refreshMs = parseInt($('refresh-rate').value);
  if (refreshTimer) clearInterval(refreshTimer);
  if (refreshMs > 0) refreshTimer = setInterval(refreshAll, refreshMs);
}
function setAsset(a)  { filter.asset = a;  paintFilterButtons(); refreshAll(); }
function setTf(t)     { filter.tf = t;     paintFilterButtons(); refreshAll(); }
function setResult(r) { filter.result = r; paintFilterButtons(); refreshAll(); }
function setSince(s)  { filter.since = s;  refreshAll(); }
function paintFilterButtons() {
  document.querySelectorAll('button[data-asset]').forEach(b =>
    b.classList.toggle('active', b.dataset.asset === filter.asset));
  document.querySelectorAll('button[data-tf]').forEach(b =>
    b.classList.toggle('active', b.dataset.tf === filter.tf));
  document.querySelectorAll('button[data-result]').forEach(b =>
    b.classList.toggle('active', b.dataset.result === filter.result));
}
function buildQuery(extra) {
  const params = new URLSearchParams();
  if (filter.asset) params.set('asset', filter.asset);
  if (filter.tf) params.set('tf', filter.tf);
  if (filter.result) params.set('result', filter.result);
  if (filter.since) params.set('since', (Date.now() / 1000 - parseFloat(filter.since)).toFixed(0));
  if (extra) for (const k in extra) params.set(k, extra[k]);
  return params.toString();
}

function fmtTs(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleTimeString();
}
function fmt$(n, sign) {
  if (n === null || n === undefined) return '—';
  const s = sign && n > 0 ? '+' : '';
  return s + '$' + Number(n).toFixed(2);
}
function pnlClass(p) { return p > 0.5 ? 'positive' : p < -0.5 ? 'negative' : ''; }

function statusPill(status) {
  if (!status) return '<span class="muted">—</span>';
  if (status === 'TRADING') return '<span class="pill trading">TRADING</span>';
  if (status === 'EXPIRED') return '<span class="pill expired">EXPIRED</span>';
  if (status === 'OPEN') return '<span class="pill trading">OPEN</span>';
  if (status.startsWith('RESOLVED:')) {
    const winner = status.split(':')[1];
    return winner === 'YES'
      ? '<span class="pill resolved-yes">RES YES</span>'
      : '<span class="pill resolved-no">RES NO</span>';
  }
  return `<span class="pill be">${status}</span>`;
}

async function refreshPeriods() {
  const r = await fetch('/api/stats_periods').then(r => r.json());
  function paint(prefix, bucket, metaFn) {
    const pnl = bucket.pnl;
    const cls = pnlClass(pnl);
    $(prefix + '-pnl').innerHTML = `<span class="${cls}">${fmt$(pnl, true)}</span>`;
    const wr = bucket.win_rate === null ? '—' : bucket.win_rate + '%';
    const roi = bucket.roi_pct === null ? '—' : bucket.roi_pct.toFixed(2) + '%';
    $(prefix + '-sub').innerHTML = `
      <span><strong>${bucket.n}</strong> markets</span>
      <span><strong>${bucket.wins}</strong>W / <strong>${bucket.losses}</strong>L (${wr})</span>
      <span>ROI <strong class="${pnlClass(bucket.roi_pct)}">${roi}</strong></span>`;
    if (metaFn) $(prefix + '-meta').textContent = metaFn();
  }
  paint('p-all', r.all_time, () => 'across all sessions');
  paint('p-today', r.today);
  paint('p-last', r.last_session, () => {
    if (!r.last_session_started_at) return 'no completed sessions yet';
    const a = new Date(r.last_session_started_at * 1000);
    const b = new Date(r.last_session_ended_at * 1000);
    return `${a.toLocaleTimeString()} – ${b.toLocaleTimeString()}`;
  });
  paint('p-now', r.current_session, () => {
    if (!r.session_started_at) return 'not in paper/live mode';
    const a = new Date(r.session_started_at * 1000);
    return `started ${a.toLocaleTimeString()}`;
  });
}

async function refreshMetrics() {
  const r = await fetch('/api/metrics').then(r => r.json());
  $('mode').textContent = r.mode;
  $('mode').className = 'pill ' + r.mode;
  $('updated').textContent = new Date().toLocaleTimeString();
  $('pnl').innerHTML = `<span class="${pnlClass(r.inventory.realized_pnl)}">${fmt$(r.inventory.realized_pnl, true)}</span>`;
  $('fills').textContent = `${r.inventory.n_fills} / ${r.executor.cumulative_fills}`;
  $('open-res').textContent = `${r.inventory.open_markets} / ${r.inventory.n_resolutions}`;
  $('live-q').textContent = r.executor.live_quotes_total;
  $('posts-cancels').textContent = `${r.executor.cumulative_posts} / ${r.executor.cumulative_cancels}`;
  $('ticks').textContent = r.quoter.tick_count;
  const stopped = r.risk.stopped;
  $('risk-state').innerHTML = stopped
    ? '<span class="negative">STOPPED</span>' : '<span class="positive">armed</span>';
  $('bankroll').textContent = fmt$(r.bankroll);
  $('risk-usd').textContent = fmt$(r.risk.max_daily_loss_usd);
  $('risk-pct').textContent = r.risk.pct;
}

async function refreshStats() {
  const r = await fetch('/api/stats').then(r => r.json());
  const tb = $('stats-tbody');
  tb.innerHTML = '';
  if (!r.by_kind || r.by_kind.length === 0) {
    tb.innerHTML = '<tr><td colspan="9" class="muted center">No resolutions yet</td></tr>';
    $('winrate').textContent = '—';
    $('roi').textContent = '—';
    return;
  }
  for (const row of r.by_kind) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><span class="pill asset">${row.asset}</span></td>
      <td><span class="pill tf">${row.timeframe}</span></td>
      <td class="right">${row.n}</td>
      <td class="right positive">${row.wins}</td>
      <td class="right negative">${row.losses}</td>
      <td class="right">${row.win_rate === null ? '—' : row.win_rate + '%'}</td>
      <td class="right">${fmt$(row.cost)}</td>
      <td class="right ${pnlClass(row.pnl)}">${fmt$(row.pnl, true)}</td>
      <td class="right ${pnlClass(row.roi_pct)}">${row.roi_pct === null ? '—' : row.roi_pct.toFixed(2) + '%'}</td>`;
    tb.appendChild(tr);
  }
  const t = r.totals;
  const tr = document.createElement('tr');
  tr.style.fontWeight = '600';
  tr.style.borderTop = '2px solid var(--border)';
  tr.innerHTML = `
    <td colspan="2"><strong>TOTAL</strong></td>
    <td class="right">${t.n}</td>
    <td class="right positive">${t.wins}</td>
    <td class="right negative">${t.losses}</td>
    <td class="right">${t.win_rate === null ? '—' : t.win_rate + '%'}</td>
    <td class="right">${fmt$(t.cost)}</td>
    <td class="right ${pnlClass(t.pnl)}">${fmt$(t.pnl, true)}</td>
    <td class="right ${pnlClass(t.roi_pct)}">${t.roi_pct === null ? '—' : t.roi_pct.toFixed(2) + '%'}</td>`;
  tb.appendChild(tr);
  $('winrate').textContent = t.win_rate === null ? '—' : t.win_rate + '%';
  $('roi').innerHTML = t.roi_pct === null ? '—' :
    `<span class="${pnlClass(t.roi_pct)}">${t.roi_pct.toFixed(2)}%</span>`;
}

async function refreshMarkets() {
  const r = await fetch('/api/markets').then(r => r.json());
  const tb = $('markets-tbody');
  tb.innerHTML = '';
  for (const m of r.markets) {
    const tr = document.createElement('tr');
    tr.classList.add('clickable');
    tr.onclick = () => openModal(m.market_id);
    const mid = m.mid_yes !== null ? m.mid_yes.toFixed(3) : '—';
    const sp = m.spread !== null ? m.spread.toFixed(3) : '—';
    tr.innerHTML = `
      <td><span class="mono-id">${m.market_id.slice(0,10)}…</span></td>
      <td><span class="pill asset">${m.asset}</span></td>
      <td><span class="pill tf">${m.timeframe}</span></td>
      <td>${m.window || '—'}</td>
      <td>${statusPill(m.status)}</td>
      <td class="right">${mid}</td>
      <td class="right">${sp}</td>
      <td class="right">${m.our_quotes}</td>
      <td class="right">${m.expires_in}s</td>`;
    tb.appendChild(tr);
  }
}

async function refreshPositions() {
  const r = await fetch('/api/positions').then(r => r.json());
  const tb = $('positions-tbody');
  tb.innerHTML = '';
  if (r.positions.length === 0) {
    tb.innerHTML = '<tr><td colspan="13" class="muted center">No open positions</td></tr>';
    return;
  }
  for (const p of r.positions) {
    const tr = document.createElement('tr');
    tr.classList.add('clickable');
    tr.onclick = () => openModal(p.market_id);
    const netSign = p.net > 0 ? 'positive' : p.net < 0 ? 'negative' : '';
    tr.innerHTML = `
      <td><span class="mono-id">${p.market_id.slice(0,10)}…</span></td>
      <td><span class="pill asset">${p.asset}</span></td>
      <td><span class="pill tf">${p.timeframe}</span></td>
      <td>${p.window || '—'}</td>
      <td>${statusPill(p.status)}</td>
      <td class="right">${p.live_quotes || 0}</td>
      <td class="right">${p.yes_qty}</td>
      <td class="right">${p.yes_avg.toFixed(3)}</td>
      <td class="right">${p.no_qty}</td>
      <td class="right">${p.no_avg.toFixed(3)}</td>
      <td class="right">${p.matched}</td>
      <td class="right ${netSign}">${p.net > 0 ? '+' : ''}${p.net}</td>
      <td class="right">${fmt$(p.total_cost)}</td>`;
    tb.appendChild(tr);
  }
}

async function refreshResolved() {
  const r = await fetch('/api/resolved?' + buildQuery()).then(r => r.json());
  const tb = $('resolved-tbody');
  tb.innerHTML = '';
  $('resolved-count').textContent = `(${r.resolved.length})`;
  if (r.resolved.length === 0) {
    tb.innerHTML = '<tr><td colspan="10" class="muted center">No resolved bets match filter</td></tr>';
    return;
  }
  for (const rb of r.resolved) {
    const tr = document.createElement('tr');
    tr.classList.add('clickable');
    const won = rb.realized_pnl > 0.5;
    const lost = rb.realized_pnl < -0.5;
    tr.classList.add(won ? 'win-row' : lost ? 'loss-row' : '');
    tr.onclick = () => openModal(rb.market_id);
    const winPill = rb.winning_side === 'YES'
      ? '<span class="pill yes">YES</span>'
      : '<span class="pill no">NO</span>';
    const result = won ? '<span class="pill win">WIN</span>'
                : lost ? '<span class="pill loss">LOSS</span>'
                : '<span class="pill be">BE</span>';
    tr.innerHTML = `
      <td>${fmtTs(rb.resolved_at)}</td>
      <td><span class="mono-id">${rb.market_id.slice(0,10)}…</span></td>
      <td><span class="pill asset">${rb.asset}</span></td>
      <td><span class="pill tf">${rb.timeframe}</span></td>
      <td>${winPill} ${result}</td>
      <td class="right">${rb.yes_qty}</td>
      <td class="right">${rb.no_qty}</td>
      <td class="right">${fmt$(rb.total_cost)}</td>
      <td class="right ${pnlClass(rb.realized_pnl)}">${fmt$(rb.realized_pnl, true)}</td>
      <td class="right ${pnlClass(rb.roi_pct)}">${rb.roi_pct === null ? '—' : rb.roi_pct.toFixed(1) + '%'}</td>`;
    tb.appendChild(tr);
  }
}

async function refreshFills() {
  const r = await fetch('/api/fills?' + buildQuery({ limit: 100 })).then(r => r.json());
  const tb = $('fills-tbody');
  tb.innerHTML = '';
  $('fills-count').textContent = `(${r.fills.length} shown / ${r.total} total)`;
  if (r.fills.length === 0) {
    tb.innerHTML = '<tr><td colspan="8" class="muted center">No fills match filter</td></tr>';
    return;
  }
  for (const f of r.fills) {
    const tr = document.createElement('tr');
    tr.classList.add('clickable');
    tr.onclick = () => openModal(f.market_id);
    const sidePill = f.side === 'YES'
      ? '<span class="pill yes">YES</span>'
      : '<span class="pill no">NO</span>';
    tr.innerHTML = `
      <td>${fmtTs(f.ts)}</td>
      <td><span class="mono-id">${f.market_id.slice(0,10)}…</span></td>
      <td><span class="pill asset">${f.asset}</span></td>
      <td><span class="pill tf">${f.timeframe}</span></td>
      <td>${sidePill}</td>
      <td class="right">${f.price.toFixed(3)}</td>
      <td class="right">${f.qty}</td>
      <td class="right">${fmt$(f.cost)}</td>`;
    tb.appendChild(tr);
  }
}

async function openModal(marketId) {
  $('modal').classList.add('show');
  $('modal-title').textContent = 'Market ' + marketId.slice(0, 14) + '…';
  $('modal-body').innerHTML = '<div class="muted">Loading…</div>';
  const r = await fetch('/api/market_summary?id=' + encodeURIComponent(marketId)).then(r => r.json());
  if (r.error) { $('modal-body').textContent = 'Error: ' + r.error; return; }
  const winPill = r.winning_side === 'YES' ? '<span class="pill yes">YES won</span>'
                : r.winning_side === 'NO' ? '<span class="pill no">NO won</span>'
                : '<span class="muted">unresolved</span>';
  let resultPill = '';
  if (r.status === 'RESOLVED' && r.resolved_pnl !== null) {
    const cls = r.resolved_pnl > 0.5 ? 'win' : r.resolved_pnl < -0.5 ? 'loss' : 'be';
    const txt = r.resolved_pnl > 0.5 ? 'WIN' : r.resolved_pnl < -0.5 ? 'LOSS' : 'BREAKEVEN';
    resultPill = `<span class="pill ${cls}">${txt}</span>`;
  }
  const pnlC = pnlClass(r.resolved_pnl);
  const fillsRows = r.fills.map(f => `
    <tr>
      <td>${fmtTs(f.ts)}</td>
      <td>${f.side === 'YES' ? '<span class="pill yes">YES</span>' : '<span class="pill no">NO</span>'}</td>
      <td class="right">${f.price.toFixed(3)}</td>
      <td class="right">${f.qty}</td>
      <td class="right">${fmt$(f.cost)}</td>
    </tr>`).join('');
  $('modal-body').innerHTML = `
    <p>
      <span class="pill asset">${r.asset}</span>
      <span class="pill tf">${r.timeframe}</span>
      ${winPill} ${resultPill}
    </p>
    <p>
      <strong>Open:</strong> ${fmtTs(r.open_ts)} &nbsp;
      <strong>Expire:</strong> ${fmtTs(r.expire_ts)} &nbsp;
      ${r.resolved_at ? '<strong>Resolved:</strong> ' + fmtTs(r.resolved_at) : ''}
    </p>
    <table style="margin-bottom: 14px;">
      <thead><tr>
        <th>Side</th><th class="right">Qty</th><th class="right">Avg</th><th class="right">Cost</th>
      </tr></thead>
      <tbody>
        <tr><td><span class="pill yes">YES</span></td>
          <td class="right">${r.yes_qty}</td>
          <td class="right">${r.yes_avg.toFixed(3)}</td>
          <td class="right">${fmt$(r.yes_cost)}</td></tr>
        <tr><td><span class="pill no">NO</span></td>
          <td class="right">${r.no_qty}</td>
          <td class="right">${r.no_avg.toFixed(3)}</td>
          <td class="right">${fmt$(r.no_cost)}</td></tr>
        <tr style="font-weight: 600; border-top: 1px solid var(--border);">
          <td>Matched / Net</td>
          <td class="right">${r.matched} matched, net ${r.net > 0 ? '+' : ''}${r.net}</td>
          <td class="right">—</td>
          <td class="right">${fmt$(r.total_cost)}</td></tr>
        ${r.resolved_pnl !== null ? `
        <tr style="font-weight: 600;">
          <td colspan="3">Realized P&amp;L</td>
          <td class="right ${pnlC}">${fmt$(r.resolved_pnl, true)}</td></tr>` : ''}
      </tbody>
    </table>
    <h2 style="margin-top: 14px;">Fill timeline (${r.fills.length})</h2>
    <div class="scroll" style="max-height: 280px;">
      <table>
        <thead><tr>
          <th>Time</th><th>Side</th>
          <th class="right">Price</th><th class="right">Qty</th><th class="right">Cost</th>
        </tr></thead>
        <tbody>${fillsRows}</tbody>
      </table>
    </div>`;
}
function closeModal() { $('modal').classList.remove('show'); }

async function setRisk() {
  const v = parseFloat($('risk-input').value || '100');
  const r = await fetch('/api/risk', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pct: v }),
  }).then(r => r.json());
  if (r.error) {
    $('risk-status').textContent = 'Error: ' + r.error;
  } else {
    $('risk-status').textContent = `set to ${r.pct}% = $${r.max_daily_loss_usd}`;
    setTimeout(() => $('risk-status').textContent = '', 3000);
    refreshMetrics();
  }
}

const SETTING_KEYS = [
  "merge_edge","max_naked_shares","merge_levels",
  "flat_size","per_market_cap_usd","min_time_to_expiry_sec"
];
async function loadSettings() {
  try {
    const r = await fetch("/api/settings");
    const s = await r.json();
    const box = document.getElementById("settings-fields");
    box.innerHTML = "";
    for (const k of SETTING_KEYS) {
      const wrap = document.createElement("div");
      wrap.style.cssText = "display:flex;flex-direction:column;gap:2px;";
      wrap.innerHTML = `<label class="muted" style="font-size:11px;">${k}</label>`;
      const inp = document.createElement("input");
      inp.type = "number"; inp.step = "any"; inp.id = "set-" + k; inp.value = s[k];
      inp.style.width = "120px";
      const btn = document.createElement("button");
      btn.className = "fbtn"; btn.textContent = "Apply";
      btn.onclick = () => setSetting(k);
      const rowEl = document.createElement("div");
      rowEl.style.cssText = "display:flex;gap:4px;";
      rowEl.appendChild(inp); rowEl.appendChild(btn);
      wrap.appendChild(rowEl); box.appendChild(wrap);
    }
  } catch (e) {
    document.getElementById("settings-status").textContent = "⚠ Could not load settings";
    console.error(e);
  }
}
async function setSetting(key) {
  const val = document.getElementById("set-" + key).value;
  const st = document.getElementById("settings-status");
  if (val === "" || isNaN(parseFloat(val))) { st.textContent = "❌ enter a number"; st.style.color = "#e66"; return; }
  const r = await fetch("/api/settings", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({key, value: parseFloat(val)})
  });
  const d = await r.json();
  if (r.status !== 200) { st.textContent = "❌ " + d.error; st.style.color = "#e66"; }
  else if (d.warning) { st.textContent = "⚠ " + d.warning; st.style.color = "#ec6"; }
  else { st.textContent = "✓ " + key + " = " + val; st.style.color = "#6e6"; }
}

async function clearAll() {
  if (!confirm('Wipe all fills, positions, markets, and sessions? This cannot be undone.')) return;
  await fetch('/api/clear', { method: 'POST' });
  refreshAll();
}

async function refreshAll() {
  try {
    await Promise.all([
      refreshMetrics(), refreshPeriods(), refreshStats(), refreshMarkets(),
      refreshPositions(), refreshResolved(), refreshFills(),
    ]);
  } catch (e) { console.error(e); }
}

paintFilterButtons();
refreshAll();
loadSettings();
setRefresh();
window.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
</script>
</body>
</html>
"""
