"""Pure decision logic for the GURU strategy (hedge + tilt). No I/O.

The guru's proven structure (reverse-engineered from 150 real windows, +$1727/wk,
58% win): buy BOTH sides all window; size the FAVORITE (higher-priced side) so that
favorite_shares ~= total_spent => 'favorite wins ~= breakeven'. The cheap underdog
accumulates freely (the reversal upside / tilt). Never sell; hold to resolution.

Hedge math: buying q favorite shares at price p adds q to shares and q*p to spent.
To reach shares == spent (breakeven if the favorite wins):
    inv_fav + q == spent + q*p  ->  q = (spent - inv_fav) / (1 - p)
So each favorite share bought closes the (spent - shares) gap by (1 - p). Buying the
favorite EARLY (cheap p) hedges far more efficiently than late (p near 1).
"""

from __future__ import annotations


def hedge_buy_qty(inv_fav: float, spent: float, fav_ask: float,
                  step: float, budget_left: float) -> float:
    """Shares of the FAVORITE to BUY this tick to move toward the breakeven hedge
    (favorite_shares == total_spent). Capped by the per-shot step and the $ budget.
    Returns 0 if already hedged, the favorite is un-priceable, or no budget."""
    if fav_ask <= 0.0 or fav_ask >= 1.0:
        return 0.0
    gap = spent - inv_fav                 # shares short of the breakeven hedge
    if gap <= 0.0:
        return 0.0
    q = gap / (1.0 - fav_ask)             # shares that close the gap exactly
    q = min(q, step)                      # incremental (reliable fills, no over-shoot)
    if budget_left > 0.0:
        q = min(q, budget_left / fav_ask)
    else:
        return 0.0
    return max(0.0, q)


def favorite_side(yes_mid: float, no_mid: float) -> str | None:
    """The likely-winning side = the higher-priced one. None if dead even."""
    if yes_mid > no_mid:
        return "YES"
    if no_mid > yes_mid:
        return "NO"
    return None


def window_pnl_if(inv_yes: float, inv_no: float, spent: float, winner: str) -> float:
    """PnL of the held book if `winner` resolves (no sell, hold to resolution)."""
    payout = inv_yes if winner == "YES" else inv_no
    return payout - spent
