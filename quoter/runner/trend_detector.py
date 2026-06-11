"""Pure Binance trend detector — decides which side of a 5m up/down market is winning.

No I/O. From the BTC price now vs the window's strike (open price) and the time left,
estimate P(Up wins) under a normal model and return a bias telling plan_ladder to
suppress the losing side's rungs (sit out the trend). One knob: cfg.trend_confidence.
The volatility used is NOISE vol (drift removed), so a steady trend is seen as a
significant move, not as "high volatility".
"""

from __future__ import annotations

import math

from quoter.config import Config


def win_prob_up(price_now: float, strike: float, sigma_remaining: float) -> float:
    """P(BTC_close >= strike) ~ Phi((price_now - strike) / sigma_remaining)."""
    if sigma_remaining <= 0:
        return 1.0 if price_now >= strike else 0.0
    z = (price_now - strike) / sigma_remaining
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def sigma_remaining(prices: list[tuple[float, float]], time_left: float, cfg: Config) -> float:
    """Expected $-stdev of the BTC move over ``time_left`` seconds, from a recent
    ``(price, ts)`` buffer. Uses drift-removed tick-to-tick noise as the per-sqrt-second
    vol; falls back to ``cfg.trend_vol_fallback`` (defined over a 5m=300s window) when the
    buffer is too thin. Floored at $1 to avoid blow-up as ``time_left -> 0``."""
    tl = max(time_left, 0.0)
    if len(prices) >= 6:
        vals = [p for p, _ in prices]
        diffs = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
        mean_d = sum(diffs) / len(diffs)
        resid = [d - mean_d for d in diffs]
        rms = (sum(r * r for r in resid) / len(resid)) ** 0.5
        span = max(prices[-1][1] - prices[0][1], 1.0)
        avg_dt = max(span / len(diffs), 0.001)
        vol_per_sqrt_sec = rms / avg_dt ** 0.5
        sig = vol_per_sqrt_sec * tl ** 0.5
    else:
        sig = cfg.trend_vol_fallback * (tl / 300.0) ** 0.5
    return max(sig, 1.0)


def detect_bias(price_now: float, strike: float, sigma_remaining: float, cfg: Config) -> str:
    """Return "UP" | "DOWN" | "NEUTRAL". Suppress the side whose win-prob < trend_confidence.
    "UP" = Up is winning → suppress Down (NO). "DOWN" = Down winning → suppress Up (YES)."""
    p_up = win_prob_up(price_now, strike, sigma_remaining)
    t = cfg.trend_confidence
    if p_up < t:
        return "DOWN"
    if p_up > 1.0 - t:
        return "UP"
    return "NEUTRAL"
