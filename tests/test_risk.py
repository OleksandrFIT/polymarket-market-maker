"""Tests for RiskGuard kill-switch."""

from __future__ import annotations

from dataclasses import replace

from quoter.config import Config
from quoter.risk.caps import RiskGuard
from quoter.strategy.inventory import Inventory


def _setup(max_loss=20.0, max_market=50.0, max_skew=200):
    cfg = replace(
        Config(),
        max_daily_loss_usd=max_loss,
        max_market_position_usd=max_market,
        max_inventory_skew_shares=max_skew,
    )
    inv = Inventory()
    g = RiskGuard(cfg, inv)
    return cfg, inv, g


def test_starts_running():
    _, _, g = _setup()
    assert not g.stopped
    assert g.check() is None


def test_daily_loss_trips_guard():
    _, inv, g = _setup(max_loss=20.0)
    # Simulate a -$25 resolution
    inv.realized_pnl = -25.0
    assert g.tick() is True  # state changed
    assert g.stopped
    assert "daily_loss_breach" in g.reason


def test_market_cap_is_per_market_not_global():
    # A single market over its cost cap must NOT halt the whole bot — it is a
    # per-market signal reported by check_market(), not the global kill-switch.
    _, inv, g = _setup(max_market=50.0)
    inv.on_fill("M1", "YES", 0.50, 200)  # $100 cost > $50 cap
    g.tick()
    assert not g.stopped  # global guard stays armed
    assert g.check() is None  # account-level check ignores per-market exposure
    reason = g.check_market("M1")
    assert reason is not None and "market_cap" in reason


def test_inventory_blowout_is_per_market():
    _, inv, g = _setup(max_skew=100)
    # net YES-NO = 250, which is > 2× soft cap (200)
    inv.on_fill("M1", "YES", 0.10, 250)
    g.tick()
    assert not g.stopped
    reason = g.check_market("M1")
    assert reason is not None and "inventory_blowout" in reason


def test_check_market_clean_when_under_caps():
    _, inv, g = _setup(max_market=50.0, max_skew=100)
    inv.on_fill("M1", "YES", 0.50, 10)  # $5 cost, net 10 — both under caps
    assert g.check_market("M1") is None
    assert g.check_market("UNKNOWN") is None


def test_guard_latches_once_tripped():
    _, inv, g = _setup(max_loss=20)
    inv.realized_pnl = -25.0
    assert g.tick() is True
    # Recover P&L; guard should STAY stopped
    inv.realized_pnl = 100.0
    assert g.tick() is False  # no state change (stays stopped)
    assert g.stopped


def test_reset_clears_latch():
    _, inv, g = _setup(max_loss=20)
    inv.realized_pnl = -25.0
    g.tick()
    assert g.stopped
    inv.realized_pnl = 0.0
    g.reset()
    assert not g.stopped
    assert g.reason == ""


def test_set_max_daily_loss_changes_threshold():
    _, inv, g = _setup(max_loss=20.0)
    inv.realized_pnl = -25.0
    g.set_max_daily_loss(100.0)
    assert g.check() is None  # -25 no longer breaches a $100 limit
    assert g.max_daily_loss_usd == 100.0


def test_raising_limit_unlatches_when_no_longer_breached():
    _, inv, g = _setup(max_loss=20.0)
    inv.realized_pnl = -25.0
    g.tick()
    assert g.stopped
    g.set_max_daily_loss(100.0)
    assert not g.stopped  # auto-reset since -25 within $100 limit


def test_raising_limit_keeps_latch_if_still_breached():
    _, inv, g = _setup(max_loss=20.0)
    inv.realized_pnl = -150.0
    g.tick()
    g.set_max_daily_loss(100.0)
    assert g.stopped  # -150 still breaches $100
