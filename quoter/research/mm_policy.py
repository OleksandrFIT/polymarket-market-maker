"""Pure quoting policies for the MM simulator. All quotes are BIDS (buy) on both sides —
the tactic never sells. `guru_like` is the wide full-book policy used for calibration;
`our` is the capped policy we would actually run."""
from __future__ import annotations

from quoter.research.mm_types import Quote


def guru_like_quotes(mid: float, size: float, levels: int) -> list[Quote]:
    out: list[Quote] = []
    for base, side in ((mid, "Up"), (1 - mid, "Down")):
        for k in range(levels):
            p = round(base - 0.01 * (k + 1), 3)
            if p > 0:
                out.append(Quote(side, p, float(size)))
    return out


def deep_ladder_quotes(mid: float, size: float, levels: int, step: float = 0.02) -> list[Quote]:
    """Calibration policy mimicking the competitor: `levels` bids per side laddered
    DEEP below each side's price (Up price=mid, Down=1-mid), `step` apart, down toward 0,
    at `size` shares each. Skips prices <= 0. Wide/deep enough to fill the cheap tail."""
    out: list[Quote] = []
    for base, side in ((mid, "Up"), (1 - mid, "Down")):
        for k in range(levels):
            p = round(base - step * (k + 1), 3)
            if p > 0:
                out.append(Quote(side, p, float(size)))
    return out


def our_quotes(mid: float, size: float, levels: int, spread: float,
               inventory: dict) -> list[Quote]:
    out: list[Quote] = []
    heavy_threshold = size * levels
    for base, side in ((mid, "Up"), (1 - mid, "Down")):
        sz = float(size) * (0.5 if inventory.get(side, 0.0) > heavy_threshold else 1.0)
        for k in range(levels):
            p = round(base - spread - 0.01 * k, 3)
            if p > 0:
                out.append(Quote(side, p, sz))
    return out
