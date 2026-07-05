"""Regime gate for the top-of-book pair-maker: trade only CALM (non-trending) windows.

The pair-maker earns on symmetric two-sided flow (clean pairs) but structurally LOSES in
a trend — it accumulates the falling/losing side (adverse selection; live-proven -$8 on a
trending window). So skip windows where BTC has just made a decisive directional move. A
CHOP (big swings but small NET move) is fine: pairs still fill both sides. The signal is
the NET move over a short trailing lookback: |last - first| of recent 1m closes.
"""
from __future__ import annotations


def regime_tradeable(recent_closes: list[float], max_move_usd: float) -> bool:
    """True if the pair-maker should trade the upcoming window.

    ``recent_closes``: BTC 1m closes over the trailing lookback, oldest -> newest.
    Calm (tradeable) iff the NET directional move stays under ``max_move_usd``; a large
    one-way move is a trend -> skip. Chop (large swings, small net) stays tradeable.

    Fewer than 2 samples -> True (fail-open): a data gap must not silently halt earning,
    and the naked cap + watchdog bound the downside of a rare wrong call.
    """
    if len(recent_closes) < 2:
        return True
    return abs(recent_closes[-1] - recent_closes[0]) < max_move_usd
