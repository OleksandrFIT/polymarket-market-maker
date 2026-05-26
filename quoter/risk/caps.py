"""Hard risk guards. If any breaches, quoter pauses new orders.

Caps are evaluated cheaply on every quoter tick. The class is stateful:
once tripped, it stays tripped until explicit ``reset()`` (operator action).
"""

from __future__ import annotations

from dataclasses import dataclass

from quoter.config import Config
from quoter.ops.logger import get_logger
from quoter.strategy.inventory import Inventory

log = get_logger("risk")


@dataclass
class RiskState:
    stopped: bool = False
    reason: str = ""
    tripped_at: float = 0.0


class RiskGuard:
    """Stateful kill-switch.

    Checked on every quoter tick. If ``check()`` returns a reason, the
    guard latches in stopped state. Quoter loop must inspect ``stopped``
    before posting new orders.
    """

    def __init__(self, cfg: Config, inv: Inventory) -> None:
        self.cfg = cfg
        self.inv = inv
        self.state = RiskState()

    @property
    def stopped(self) -> bool:
        return self.state.stopped

    @property
    def reason(self) -> str:
        return self.state.reason

    def check(self) -> str | None:
        """Return ``None`` if OK, else reason string."""
        if self.inv.realized_pnl < -self.cfg.max_daily_loss_usd:
            return f"daily_loss_breach pnl=${self.inv.realized_pnl:.2f}"

        for mid, p in self.inv.positions.items():
            if p.total_cost > self.cfg.max_market_position_usd:
                return f"market_cap_breach market={mid[:8]} cost=${p.total_cost:.2f}"
            if abs(p.net_yes_minus_no) > self.cfg.max_inventory_skew_shares * 2:
                # Hard-cap at 2× the soft skew (soft cap stops new quotes,
                # hard cap stops everything).
                return f"inventory_blowout market={mid[:8]} net={p.net_yes_minus_no}"

        return None

    def tick(self, now_ts: float = 0.0) -> bool:
        """Evaluate and latch. Returns True if state CHANGED to stopped."""
        if self.state.stopped:
            return False
        reason = self.check()
        if reason is None:
            return False
        self.state = RiskState(stopped=True, reason=reason, tripped_at=now_ts)
        log.error("RISK_TRIPPED", reason=reason, tripped_at=now_ts)
        return True

    def reset(self) -> None:
        """Operator action: clear the latch. Use only after manual review."""
        log.warning("RISK_RESET", was_reason=self.state.reason)
        self.state = RiskState()
