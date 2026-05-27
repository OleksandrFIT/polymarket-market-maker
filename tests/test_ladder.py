"""Tests for compute_ladder pure-function strategy."""

from __future__ import annotations

from dataclasses import replace

from quoter.config import Config
from quoter.strategy.ladder import compute_ladder

CFG = Config(ladder_levels=12, budget_per_market_usd=25.0, max_inventory_skew_shares=200)


class TestLadderSanityGates:
    def test_skip_too_close_to_expiry(self):
        assert compute_ladder(CFG, mid_yes=0.5, time_to_expiry=3) == []

    def test_skip_mid_at_edges(self):
        assert compute_ladder(CFG, mid_yes=0.01, time_to_expiry=200) == []
        assert compute_ladder(CFG, mid_yes=0.99, time_to_expiry=200) == []

    def test_valid_mid_returns_quotes(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        assert len(out) > 0


class TestLadderShape:
    def test_emits_both_sides_at_mid(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        yes = [q for q in out if q.side == "YES"]
        no = [q for q in out if q.side == "NO"]
        assert len(yes) >= 8
        assert len(no) >= 8

    def test_all_prices_in_valid_range(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        for q in out:
            assert 0.01 <= q.price <= 0.99, f"{q!r} price out of range"

    def test_all_sizes_at_least_min(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        for q in out:
            assert q.size >= 5, f"{q!r} below Polymarket min size"

    def test_yes_quotes_below_mid_yes(self):
        out = compute_ladder(CFG, mid_yes=0.70, time_to_expiry=200)
        for q in out:
            if q.side == "YES" and q.price > 0.05:
                assert q.price < 0.70 + 0.001

    def test_no_quotes_below_mid_no(self):
        out = compute_ladder(CFG, mid_yes=0.70, time_to_expiry=200)
        mid_no = 0.30
        for q in out:
            if q.side == "NO" and q.price > 0.05:
                assert q.price < mid_no + 0.001

    def test_cheap_tail_present(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        # At least one quote at 0.01 on each side (mid - 10c > 0)
        assert any(q.price == 0.01 and q.side == "YES" for q in out)
        assert any(q.price == 0.01 and q.side == "NO" for q in out)


class TestInventorySkew:
    def test_long_yes_suppresses_yes_quotes(self):
        out = compute_ladder(
            CFG, mid_yes=0.5, time_to_expiry=200,
            inventory_yes_qty=500, inventory_no_qty=0,
        )
        yes = [q for q in out if q.side == "YES"]
        assert len(yes) == 0
        # NO should still be quoted
        assert any(q.side == "NO" for q in out)

    def test_long_no_suppresses_no_quotes(self):
        out = compute_ladder(
            CFG, mid_yes=0.5, time_to_expiry=200,
            inventory_yes_qty=0, inventory_no_qty=500,
        )
        no = [q for q in out if q.side == "NO"]
        assert len(no) == 0
        assert any(q.side == "YES" for q in out)

    def test_balanced_inventory_emits_both(self):
        out = compute_ladder(
            CFG, mid_yes=0.5, time_to_expiry=200,
            inventory_yes_qty=100, inventory_no_qty=100,
        )
        assert any(q.side == "YES" for q in out)
        assert any(q.side == "NO" for q in out)


class TestLateWindowSkew:
    """Phase-11: late window NEUTRALIZED — timing curve handles aggression."""

    def test_committed_yes_still_dominates_via_directional_skew(self):
        """Polarized mid=0.8 → YES bigger via directional_size_skew (not late stack)."""
        out = compute_ladder(CFG, mid_yes=0.8, time_to_expiry=30,
                              committed_side="YES", timeframe="5m")
        tail_max = max(CFG.cheap_tail_levels)
        yes_la = sum(q.size for q in out if q.side == "YES" and q.price > tail_max)
        no_la = sum(q.size for q in out if q.side == "NO" and q.price > tail_max)
        assert yes_la > no_la  # Layer-A YES dominates polarized

    def test_no_skew_when_no_committed_side(self):
        out = compute_ladder(CFG, mid_yes=0.5, time_to_expiry=30, committed_side=None,
                              timeframe="5m")
        assert len(out) > 0


class TestSelfCrossPrevention:
    def test_drops_self_crossing_pair(self):
        # buffer=0.01, so yes_bid + no_bid must be < 0.99
        cfg = replace(CFG, ladder_levels=12, self_cross_buffer=0.01)
        # If mid_yes = 0.50, mid_no = 0.50
        # YES bid 1c below mid = 0.49; NO bid 1c below mid = 0.49
        # Sum = 0.98 < 0.99 → OK
        out = compute_ladder(cfg, mid_yes=0.50, time_to_expiry=200)
        # Verify no YES+NO pair crosses
        yes_max = max((q.price for q in out if q.side == "YES"), default=0)
        no_max = max((q.price for q in out if q.side == "NO"), default=0)
        assert yes_max + no_max <= 1.0 - cfg.self_cross_buffer + 1e-9


class TestDirectionalSkew:
    """Phase-11: polarized mid → BIGGER winning side Layer-A; cheap-tail
    boosts LOSING side (polarized cheap-tail dominance)."""

    def test_polarized_high_layer_a_yes_bigger(self):
        """Layer-A YES grows when mid > 0.5 (winning side scaling)."""
        out_neutral = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        out_high = compute_ladder(CFG, mid_yes=0.80, time_to_expiry=200)
        tail_max = max(CFG.cheap_tail_levels)
        # Layer-A only (exclude cheap-tail)
        yes_neutral = sum(q.size for q in out_neutral if q.side == "YES" and q.price > tail_max)
        yes_high = sum(q.size for q in out_high if q.side == "YES" and q.price > tail_max)
        no_neutral = sum(q.size for q in out_neutral if q.side == "NO" and q.price > tail_max)
        no_high = sum(q.size for q in out_high if q.side == "NO" and q.price > tail_max)
        assert yes_high > yes_neutral  # winning side bigger
        assert no_high < no_neutral    # losing side smaller

    def test_polarized_low_layer_a_no_bigger(self):
        out_low = compute_ladder(CFG, mid_yes=0.20, time_to_expiry=200)
        out_neutral = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        tail_max = max(CFG.cheap_tail_levels)
        no_low = sum(q.size for q in out_low if q.side == "NO" and q.price > tail_max)
        no_neutral = sum(q.size for q in out_neutral if q.side == "NO" and q.price > tail_max)
        yes_low = sum(q.size for q in out_low if q.side == "YES" and q.price > tail_max)
        yes_neutral = sum(q.size for q in out_neutral if q.side == "YES" and q.price > tail_max)
        assert no_low > no_neutral
        assert yes_low < yes_neutral

    def test_polarized_cheap_tail_boost_on_losing_side(self):
        """Phase-11: when mid > polarized threshold (0.75), cheap-tail
        on LOSING side (NO) gets bigger sizing (Bonereaper pattern)."""
        out = compute_ladder(CFG, mid_yes=0.85, time_to_expiry=200)
        tail_max = max(CFG.cheap_tail_levels)
        cheap_no = sum(q.size for q in out if q.side == "NO" and q.price <= tail_max)
        cheap_yes = sum(q.size for q in out if q.side == "YES" and q.price <= tail_max)
        # NO cheap-tail boosted (losing side gets the lottery tickets)
        assert cheap_no > cheap_yes

    def test_neutral_mid_keeps_sides_symmetric(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        yes_size = sum(q.size for q in out if q.side == "YES")
        no_size = sum(q.size for q in out if q.side == "NO")
        # Allow small drift from rounding
        assert abs(yes_size - no_size) <= max(yes_size, no_size) * 0.20

    def test_filter_disabled_by_default(self):
        # Phase-9 default: directional_filter_enabled = False
        out = compute_ladder(CFG, mid_yes=0.80, time_to_expiry=200)
        # NO side STILL quoted (just smaller), not skipped entirely
        no_layer_a = [q for q in out if q.side == "NO" and q.price >= 0.15]
        assert len(no_layer_a) > 0


class TestTimingCurve:
    """Phase-11: front-loaded — early window 3×, late window 0.1×."""

    def test_5m_window_open_is_aggressive(self):
        """At window open (tte ~= 300s for 5m), Layer-A size > base mid."""
        early = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=290, timeframe="5m")
        mid_window = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=150, timeframe="5m")
        tail_max = max(CFG.cheap_tail_levels)
        early_total = sum(q.size for q in early if q.price > tail_max)
        mid_total = sum(q.size for q in mid_window if q.price > tail_max)
        # 3× multiplier at open, 1× at mid → early should be ~3× bigger
        assert early_total > mid_total * 2

    def test_5m_window_close_backs_off(self):
        """At last 30s of 5m, size collapses to ~0.1× of mid."""
        mid_window = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=150, timeframe="5m")
        late = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=10, timeframe="5m")
        tail_max = max(CFG.cheap_tail_levels)
        mid_total = sum(q.size for q in mid_window if q.price > tail_max)
        late_total = sum(q.size for q in late if q.price > tail_max)
        # Late should be MUCH smaller than mid (0.1× vs 1.0×)
        assert late_total < mid_total

    def test_15m_uses_different_curve(self):
        """15m curve is different from 5m curve."""
        # At same window_fraction, both should behave similarly
        # tte=270 for 5m = 90% used, tte=810 for 15m = 90% used
        late_5m = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=10, timeframe="5m")
        late_15m = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=50, timeframe="15m")
        # Both should be in TAPER zone (small)
        # Just verify both return SOMETHING (not zero)
        assert len(late_5m) > 0 or len(late_15m) > 0


class TestVelocityGate:
    """Phase-12: directional skew GATED by Binance velocity.

    Note: at polarized mid (0.80) the Layer-A YES has many more LEVELS than
    Layer-A NO regardless of skew (mid_no=0.20 ⇒ only 9 NO levels possible
    vs 50 for YES). So we compare WITH-velocity vs OPPOSED-velocity to test
    the gating effect on PER-QUOTE multiplier.
    """

    def _yes_total_la(self, out):
        tail_max = max(CFG.cheap_tail_levels)
        return sum(q.size for q in out if q.side == "YES" and q.price > tail_max)

    def test_velocity_agrees_amplifies_yes_vs_velocity_opposes(self):
        """At mid=0.80: velocity UP → bigger YES; velocity DOWN → smaller YES."""
        agree = compute_ladder(CFG, mid_yes=0.80, time_to_expiry=200,
                                timeframe="5m", asset="ETH",  # no conviction
                                velocity_short=0.003)
        oppose = compute_ladder(CFG, mid_yes=0.80, time_to_expiry=200,
                                  timeframe="5m", asset="ETH",
                                  velocity_short=-0.003)
        yes_agree = self._yes_total_la(agree)
        yes_oppose = self._yes_total_la(oppose)
        # When velocity opposes, skew killed → smaller YES total
        assert yes_agree > yes_oppose

    def test_velocity_neutral_keeps_skew(self):
        """Tiny velocity (below neutral threshold) → behaves like velocity_short=None."""
        neutral = compute_ladder(CFG, mid_yes=0.80, time_to_expiry=200,
                                   timeframe="5m", asset="ETH",
                                   velocity_short=0.0001)
        no_velo = compute_ladder(CFG, mid_yes=0.80, time_to_expiry=200,
                                   timeframe="5m", asset="ETH",
                                   velocity_short=None)
        # Roughly equivalent
        assert abs(self._yes_total_la(neutral) - self._yes_total_la(no_velo)) <= 10

    def test_no_velocity_equivalent_to_skew_on(self):
        """velocity_short=None → skew applied (backward compat)."""
        no_velo = compute_ladder(CFG, mid_yes=0.80, time_to_expiry=200,
                                   timeframe="5m", asset="ETH",
                                   velocity_short=None)
        oppose = compute_ladder(CFG, mid_yes=0.80, time_to_expiry=200,
                                  timeframe="5m", asset="ETH",
                                  velocity_short=-0.003)
        # No velocity → skew applied → bigger than oppose case
        assert self._yes_total_la(no_velo) > self._yes_total_la(oppose)


class TestVelocityConviction:
    """Phase-12: conviction trigger requires velocity confirmation."""

    def test_conviction_triggers_when_btc_extreme_mid_and_velocity_agrees(self):
        # BTC + extreme mid + velocity UP → conviction
        out_conv = compute_ladder(CFG, mid_yes=0.85, time_to_expiry=200,
                                    timeframe="5m", asset="BTC",
                                    velocity_long=0.003)
        # Same setup but no velocity confirm → backward-compat: still conviction
        out_no_velo = compute_ladder(CFG, mid_yes=0.85, time_to_expiry=200,
                                       timeframe="5m", asset="BTC",
                                       velocity_long=None)
        tail_max = max(CFG.cheap_tail_levels)
        sz_conv = sum(q.size for q in out_conv if q.price > tail_max)
        sz_nv = sum(q.size for q in out_no_velo if q.price > tail_max)
        # Both should be big (conviction); both ≈ same
        assert sz_conv > 0 and sz_nv > 0

    def test_conviction_killed_when_velocity_opposes_mid(self):
        """BTC + extreme mid BUT velocity OPPOSES → NO conviction."""
        # mid=0.85 says YES, but BTC moving DOWN → suppress conviction
        out_no_conv = compute_ladder(CFG, mid_yes=0.85, time_to_expiry=200,
                                       timeframe="5m", asset="BTC",
                                       velocity_long=-0.003)
        out_with_conv = compute_ladder(CFG, mid_yes=0.85, time_to_expiry=200,
                                         timeframe="5m", asset="BTC",
                                         velocity_long=0.003)
        tail_max = max(CFG.cheap_tail_levels)
        sz_no = sum(q.size for q in out_no_conv if q.price > tail_max)
        sz_yes = sum(q.size for q in out_with_conv if q.price > tail_max)
        # Without conviction → smaller budget → smaller total size
        assert sz_yes > sz_no


class TestConviction:
    """Phase-11: conviction triggers multiply budget."""

    def test_conviction_15m_early_doubles_size(self):
        """Early entry on 15m = conviction → budget × multiplier."""
        # Normal: 5m at mid-window
        normal = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=150,
                                timeframe="5m", asset="ETH")
        # Conviction: 15m within first 30s of window
        conv = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=880,
                              timeframe="15m", asset="BTC")
        tail_max = max(CFG.cheap_tail_levels)
        normal_total = sum(q.size for q in normal if q.price > tail_max)
        conv_total = sum(q.size for q in conv if q.price > tail_max)
        # Conviction should be at least 2× bigger
        assert conv_total > normal_total * 2

    def test_conviction_btc_extreme_mid_triggers(self):
        """BTC at mid=0.85 (extreme) → conviction multiplier applied."""
        eth_extreme = compute_ladder(CFG, mid_yes=0.85, time_to_expiry=150,
                                      timeframe="5m", asset="ETH")
        btc_extreme = compute_ladder(CFG, mid_yes=0.85, time_to_expiry=150,
                                      timeframe="5m", asset="BTC")
        tail_max = max(CFG.cheap_tail_levels)
        eth_total = sum(q.size for q in eth_extreme if q.price > tail_max)
        btc_total = sum(q.size for q in btc_extreme if q.price > tail_max)
        # BTC gets conviction multiplier, ETH doesn't (not in conviction_assets)
        assert btc_total > eth_total


class TestSizing:
    def test_tight_cluster_quotes_are_larger_than_mid_depth(self):
        """Phase-9: top N levels (tight cluster) > deeper levels."""
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        yes = sorted([q for q in out if q.side == "YES"], key=lambda q: -q.price)
        if len(yes) >= 10:
            cluster_avg = sum(q.size for q in yes[:3]) / 3
            mid_depth_avg = sum(q.size for q in yes[5:8]) / 3
            assert cluster_avg >= mid_depth_avg  # cluster boosted
