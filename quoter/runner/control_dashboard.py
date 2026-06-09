"""Operator control dashboard — START / STOP / FORCE STOP for the merge-maker.

Bind to localhost only (SSH tunnel); never expose publicly. Reuses the project's
aiohttp style. The runner is optional (for live cash/order reads); the state is
the single source of truth the buttons mutate.
"""

from __future__ import annotations

import asyncio
import time

from aiohttp import web

from quoter.runner.trading_state import TradingState

WINDOW_SEC = 300


def _current_window_open_ts() -> int:
    return int(time.time()) // WINDOW_SEC * WINDOW_SEC


HTML = """<!doctype html><html><head><meta charset=utf-8>
<title>merge-maker control</title>
<style>
 body{background:#0b0e14;color:#cdd6f4;font:15px/1.5 system-ui;margin:0;padding:24px}
 h1{font-size:18px;margin:0 0 16px}
 .mode{font-size:28px;font-weight:700;padding:6px 14px;border-radius:8px;display:inline-block}
 .RUNNING{background:#1b3a1b;color:#9ece6a} .STOPPED{background:#3a1b1b;color:#f7768e}
 table{border-collapse:collapse;margin:18px 0} td{padding:4px 18px 4px 0}
 .k{color:#7f849c} button{font:600 15px system-ui;border:0;border-radius:8px;padding:12px 22px;margin:6px 8px 0 0;cursor:pointer;color:#fff}
 .start{background:#2e7d32} .stop{background:#b26a00} .force{background:#c62828}
 .ev{color:#7f849c;margin-top:14px;font-size:13px}
</style></head><body>
<h1>🟢 merge-maker control <span class=k>(BTC 5m · localhost)</span></h1>
<div id=mode class="mode STOPPED">…</div>
<table>
 <tr><td class=k>current window</td><td id=win>—</td></tr>
 <tr><td class=k>windows traded (session)</td><td id=wt>—</td></tr>
 <tr><td class=k>pairs caught (current)</td><td id=pc>—</td></tr>
 <tr><td class=k>naked shares</td><td id=nk>—</td></tr>
 <tr><td class=k>cash balance</td><td id=cash>—</td></tr>
 <tr><td class=k>open orders</td><td id=oo>—</td></tr>
</table>
<div>
 <button class=start onclick=act('start')>▶ START</button>
 <button class=stop  onclick=act('stop')>■ STOP</button>
 <button class=force onclick=act('force_stop')>✖ FORCE STOP</button>
</div>
<div class=ev id=ev>—</div>
<script>
async function refresh(){
 let r=await fetch('/api/status'); let d=await r.json();
 let m=document.getElementById('mode'); m.textContent=d.mode; m.className='mode '+d.mode;
 document.getElementById('win').textContent=d.window||'—';
 document.getElementById('wt').textContent=d.windows_traded;
 document.getElementById('pc').textContent=d.pairs_caught;
 document.getElementById('nk').textContent=d.naked_shares;
 document.getElementById('cash').textContent=(d.cash==null?'—':'$'+d.cash.toFixed(2));
 document.getElementById('oo').textContent=(d.open_orders==null?'—':d.open_orders);
 document.getElementById('ev').textContent=d.last_event;
}
async function act(a){
 if(a==='force_stop' && !confirm('FORCE STOP: cancel ALL resting orders now?'))return;
 await fetch('/api/'+a,{method:'POST'}); refresh();
}
setInterval(refresh,2000); refresh();
</script></body></html>"""


def make_control_app(state: TradingState, runner=None) -> web.Application:
    app = web.Application()

    async def root(_req):
        return web.Response(text=HTML, content_type="text/html")

    async def status(_req):
        cash = open_orders = None
        if runner is not None:
            try:
                cash = await asyncio.to_thread(runner.collateral_usd)
                open_orders = await asyncio.to_thread(runner.open_orders_count)
            except Exception:
                pass
        ots = _current_window_open_ts()
        left = (ots + WINDOW_SEC) - int(time.time())
        win = f"open_ts {ots} ({left}s left)"
        s = state.snapshot()
        return web.json_response({
            **s, "window": win,
            "cash": cash if (cash is None or cash >= 0) else None,
            "open_orders": open_orders if (open_orders is None or open_orders >= 0) else None,
        })

    async def start(_req):
        state.start(_current_window_open_ts())
        return web.json_response(state.snapshot())

    async def stop(_req):
        state.stop()
        return web.json_response(state.snapshot())

    async def force_stop(_req):
        state.force_stop()
        return web.json_response(state.snapshot())

    app.add_routes([
        web.get("/", root),
        web.get("/api/status", status),
        web.post("/api/start", start),
        web.post("/api/stop", stop),
        web.post("/api/force_stop", force_stop),
    ])
    return app
