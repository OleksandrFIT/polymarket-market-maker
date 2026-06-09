"""Operator control state for the merge-maker runner.

Pure logic, no I/O — fully unit-testable. The dashboard mutates it via
``start``/``stop``/``force_stop``; the runner reads it via ``should_enter`` and
drains ``force_stop_requested``.

Semantics (operator buttons):
  * START      — trade from the NEXT window (skip the current one).
  * STOP       — graceful: stop entering new windows; let the current finish.
  * FORCE STOP — stop + cancel all resting orders immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Mode = str  # "STOPPED" | "RUNNING"


@dataclass
class TradingState:
    mode: Mode = "STOPPED"
    # Only enter windows whose open_ts is strictly greater than this. Set to the
    # CURRENT window's open_ts on START so the current window is skipped.
    trade_from_open_ts: int | None = None
    # One-shot flag the runner drains to cancel all resting orders now.
    force_stop_requested: bool = False

    # ── session stats (display only) ──
    windows_traded: int = 0
    pairs_caught: int = 0
    naked_shares: int = 0
    last_window: str = ""
    last_event: str = "idle"

    # ── operator actions ──

    def start(self, current_window_open_ts: int) -> None:
        """Enable trading from the NEXT window (current window skipped)."""
        self.mode = "RUNNING"
        self.trade_from_open_ts = current_window_open_ts
        self.force_stop_requested = False
        self.last_event = "START — trading from next window"

    def stop(self) -> None:
        """Graceful stop: no new windows; current window finishes naturally."""
        self.mode = "STOPPED"
        self.last_event = "STOP — finishing current window, no new entries"

    def force_stop(self) -> None:
        """Emergency stop: also cancel all resting orders now."""
        self.mode = "STOPPED"
        self.force_stop_requested = True
        self.last_event = "FORCE STOP — cancelling all resting orders"

    def drain_force_stop(self) -> bool:
        """Read-and-clear the force-stop flag (runner calls this once per loop)."""
        if self.force_stop_requested:
            self.force_stop_requested = False
            return True
        return False

    # ── entry decision ──

    def should_enter(
        self,
        *,
        window_open_ts: int,
        time_left: float,
        mid: float | None,
        fresh_min_sec: float,
        balanced: tuple[float, float],
        already_traded: bool,
    ) -> bool:
        """True iff the runner should enter this window now."""
        if self.mode != "RUNNING":
            return False
        if self.trade_from_open_ts is not None and window_open_ts <= self.trade_from_open_ts:
            return False  # skip the current (and older) window
        if already_traded:
            return False
        if time_left < fresh_min_sec:
            return False
        if mid is None or not (balanced[0] <= mid <= balanced[1]):
            return False
        return True

    # ── display ──

    def snapshot(self) -> dict:
        return {
            "mode": self.mode,
            "trade_from_open_ts": self.trade_from_open_ts,
            "windows_traded": self.windows_traded,
            "pairs_caught": self.pairs_caught,
            "naked_shares": self.naked_shares,
            "last_window": self.last_window,
            "last_event": self.last_event,
        }
