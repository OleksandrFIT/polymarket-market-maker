"""Pure reconstruction of a competitor's per-window P&L from his trades.

No I/O. Given his BUY trades for one BTC 5m window and the window's winning side,
split his realized P&L into the HEDGE edge (matched pairs bought < $1) and the
NAKED-leg P&L (the unmatched side, paid off 1/0 at resolution). The whole point is
to see whether the hedge edge survives the naked legs. Assumes hold-to-resolution
(competitor sells ~0%), so resolution payout (1/0) equals his realized value.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Trade:
    outcome: str   # "Up" | "Down"
    size: float
    price: float


@dataclass
class WindowResult:
    window_id: str
    up_shares: float
    up_avg: float
    down_shares: float
    down_avg: float
    matched: float
    pair_cost: float
    naked_shares: float
    naked_side: str | None
    naked_avg: float
    winning_side: str
    pair_pnl: float
    naked_pnl: float
    net: float
    spend: float


def reconstruct_window(window_id: str, trades: list[Trade], winning_side: str) -> WindowResult:
    """Split one window's P&L into hedge edge + naked-leg P&L."""
    up_sz = sum(t.size for t in trades if t.outcome == "Up")
    up_cost = sum(t.size * t.price for t in trades if t.outcome == "Up")
    dn_sz = sum(t.size for t in trades if t.outcome == "Down")
    dn_cost = sum(t.size * t.price for t in trades if t.outcome == "Down")
    up_avg = up_cost / up_sz if up_sz else 0.0
    dn_avg = dn_cost / dn_sz if dn_sz else 0.0

    matched = min(up_sz, dn_sz)
    pair_cost = (up_avg + dn_avg) if (up_sz and dn_sz) else 0.0
    pair_pnl = matched * (1.0 - pair_cost) if matched > 0 else 0.0

    naked_shares = abs(up_sz - dn_sz)
    if up_sz > dn_sz:
        naked_side, naked_avg = "Up", up_avg
    elif dn_sz > up_sz:
        naked_side, naked_avg = "Down", dn_avg
    else:
        naked_side, naked_avg = None, 0.0
    payout = 1.0 if naked_side == winning_side else 0.0
    naked_pnl = naked_shares * (payout - naked_avg) if naked_side else 0.0

    return WindowResult(
        window_id=window_id, up_shares=up_sz, up_avg=up_avg,
        down_shares=dn_sz, down_avg=dn_avg, matched=matched, pair_cost=pair_cost,
        naked_shares=naked_shares, naked_side=naked_side, naked_avg=naked_avg,
        winning_side=winning_side, pair_pnl=pair_pnl, naked_pnl=naked_pnl,
        net=pair_pnl + naked_pnl, spend=up_cost + dn_cost,
    )


@dataclass
class Report:
    n_windows: int
    n_hedged: int           # windows with both sides bought
    n_naked: int            # windows carrying any unmatched shares
    avg_pair_cost: float    # over hedged windows
    total_pair_pnl: float
    total_naked_pnl: float
    total_net: float
    net_per_window: float
    pct_windows_positive: float
    total_spend: float
    avg_size_per_window: float
    verdict: str


def aggregate(results: list[WindowResult]) -> Report:
    """Roll per-window results into the go/no-go report.

    Verdict (per spec):
      DON'T BUILD              if total_net <= 0
      DON'T BUILD (naked luck) if net > 0 but pair edge itself <= 0 (gambling)
      STRONG BUILD             if pair edge > 0 and > 50% of windows net-positive
      BUILD                    otherwise (pair edge > 0, net > 0)
    """
    n = len(results)
    if n == 0:
        return Report(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "NO DATA")

    hedged = [r for r in results if r.up_shares > 0 and r.down_shares > 0]
    naked = [r for r in results if r.naked_shares > 0]
    avg_pair = sum(r.pair_cost for r in hedged) / len(hedged) if hedged else 0.0
    tot_pair = sum(r.pair_pnl for r in results)
    tot_naked = sum(r.naked_pnl for r in results)
    tot_net = tot_pair + tot_naked
    pct_pos = 100.0 * sum(1 for r in results if r.net > 0) / n
    tot_spend = sum(r.spend for r in results)
    avg_size = sum(r.up_shares + r.down_shares for r in results) / n

    if tot_net <= 0:
        verdict = "DON'T BUILD"
    elif tot_pair <= 0:
        verdict = "DON'T BUILD (net positive only via naked luck)"
    elif pct_pos > 50:
        verdict = "STRONG BUILD"
    else:
        verdict = "BUILD"

    return Report(
        n_windows=n, n_hedged=len(hedged), n_naked=len(naked), avg_pair_cost=avg_pair,
        total_pair_pnl=tot_pair, total_naked_pnl=tot_naked, total_net=tot_net,
        net_per_window=tot_net / n, pct_windows_positive=pct_pos,
        total_spend=tot_spend, avg_size_per_window=avg_size, verdict=verdict,
    )
