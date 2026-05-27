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
        # Runtime-adjustable (via dashboard). Seeded from config.
        self.max_daily_loss_usd = cfg.max_daily_loss_usd

    @property
    def stopped(self) -> bool:
        return self.state.stopped

    @property
    def reason(self) -> str:
        return self.state.reason

    def check(self) -> str | None:
        """Account-level kill-switch check. ``None`` if OK, else reason.

        Only the daily-loss limit is account-level — a breach here halts ALL
        quoting (latched). Per-market exposure caps live in ``check_market``
        and only pause the offending market.
        """
        if self.inv.realized_pnl < -self.max_daily_loss_usd:
            return f"daily_loss_breach pnl=${self.inv.realized_pnl:.2f}"
        return None

    def check_market(self, market_id: str) -> str | None:
        """Per-market exposure check. ``None`` if OK, else reason.

        A breach here means the caller should stop quoting THIS market only
        (cancel its resting quotes, post nothing) — the rest of the book keeps
        quoting and the bot stays running.
        """
        p = self.inv.positions.get(market_id)
        if p is None:
            return None
        if p.total_cost > self.cfg.max_market_position_usd:
            return f"market_cap market={market_id[:8]} cost=${p.total_cost:.2f}"
        if abs(p.net_yes_minus_no) > self.cfg.max_inventory_skew_shares * 2:
            return f"inventory_blowout market={market_id[:8]} net={p.net_yes_minus_no}"
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

    def set_max_daily_loss(self, usd: float) -> None:
        """Operator action: change the daily-loss kill-switch threshold.

        If the guard is currently latched but the new (higher) limit means
        the breach no longer applies, auto-clear the latch so the bot resumes
        without a restart.
        """
        log.warning("RISK_LIMIT_CHANGED", old=self.max_daily_loss_usd, new=usd)
        self.max_daily_loss_usd = usd
        if self.state.stopped and self.check() is None:
            self.reset()
