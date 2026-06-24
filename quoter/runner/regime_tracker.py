"""Pure circuit-breaker: pause the directional tilt when its rolling paper-EV decays.

Tracks the last `window` directional calls as (predicted_fav, fav_entry_price, winner)
and computes the paper EV/share of the tilt (with taker fee). Tilt is enabled ONLY
after a shadow-only warm-up of `min_samples` recorded calls AND while paper-EV >= `min_ev`.
EV (not hit-rate) is the gate: hit-rate is blind to entry price — a favorite bought
at 0.83 needs ~85% wins (= entry + fee) just to break even. Pure, no I/O.
"""
from __future__ import annotations

from collections import deque


class RegimeTracker:
    def __init__(self, window: int = 20, min_samples: int = 12,
                 min_ev: float = 0.01, fee: float = 0.02) -> None:
        self.min_samples = min_samples
        self.min_ev = min_ev
        self.fee = fee
        self._q: deque[tuple[str, float, str]] = deque(maxlen=window)

    def record(self, predicted_fav: str, entry_price: float, winner: str) -> None:
        self._q.append((predicted_fav, float(entry_price), winner))

    def _evs(self) -> list[float]:
        out = []
        for fav, entry, win in self._q:
            cost = entry + self.fee
            out.append((1.0 - cost) if fav == win else -cost)
        return out

    def paper_ev(self) -> float | None:
        evs = self._evs()
        return sum(evs) / len(evs) if evs else None

    def hit_rate(self) -> float | None:        # diagnostic only
        if not self._q:
            return None
        return sum(1 for f, _, w in self._q if f == w) / len(self._q)

    def directional_enabled(self) -> bool:
        if len(self._q) < self.min_samples:
            return False                        # shadow-only warm-up
        ev = self.paper_ev()
        return ev is not None and ev >= self.min_ev
