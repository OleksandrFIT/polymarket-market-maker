"""Tests for PriceBuffer + MultiAssetPriceBuffer."""

from __future__ import annotations

from quoter.strategy.price_buffer import MultiAssetPriceBuffer, PriceBuffer


class TestPriceBuffer:
    def test_empty_buffer_returns_none(self):
        b = PriceBuffer()
        assert b.latest is None
        assert b.velocity(60) is None
        assert b.price_at_age(30) is None
        assert len(b) == 0

    def test_single_observation(self):
        b = PriceBuffer()
        b.add(100.0, ts=1000.0)
        assert b.latest == 100.0
        # Not enough history for velocity
        assert b.velocity(60) is None

    def test_velocity_positive_move(self):
        b = PriceBuffer()
        b.add(100.0, ts=1000.0)
        b.add(101.0, ts=1060.0)  # +1% in 60s
        v = b.velocity(60)
        assert v is not None
        assert abs(v - 0.01) < 1e-6

    def test_velocity_negative_move(self):
        b = PriceBuffer()
        b.add(100.0, ts=1000.0)
        b.add(99.5, ts=1030.0)  # -0.5% in 30s
        v = b.velocity(30)
        assert v is not None
        assert abs(v - (-0.005)) < 1e-6

    def test_velocity_uses_closest_past_observation(self):
        b = PriceBuffer()
        b.add(100.0, ts=1000.0)
        b.add(100.5, ts=1010.0)
        b.add(101.0, ts=1020.0)
        b.add(101.5, ts=1030.0)
        # Velocity over 20s should use price ~10ms ago (100.5)
        v = b.velocity(20)
        assert v is not None
        # latest 101.5, past 100.5 → (101.5-100.5)/100.5 ≈ 0.00995
        assert 0.009 < v < 0.011

    def test_evicts_old_observations(self):
        b = PriceBuffer(max_age_sec=60)
        b.add(100.0, ts=0)
        b.add(101.0, ts=30)
        b.add(102.0, ts=80)  # 0-ts is now 80-60=20s old, evicted
        assert len(b) == 2  # Only ts=30 and ts=80 remain

    def test_invalid_price_ignored(self):
        b = PriceBuffer()
        b.add(0.0, ts=1000)
        b.add(-1.0, ts=1010)
        assert len(b) == 0
        assert b.latest is None

    def test_age_span(self):
        b = PriceBuffer()
        b.add(100.0, ts=1000)
        b.add(101.0, ts=1100)
        assert b.age_span_sec() == 100.0


class TestMultiAssetPriceBuffer:
    def test_independent_assets(self):
        m = MultiAssetPriceBuffer(("BTC", "ETH"))
        m.add("BTC", 50000, ts=1000)
        m.add("BTC", 50100, ts=1060)
        m.add("ETH", 3000, ts=1000)
        m.add("ETH", 2970, ts=1060)
        # BTC up 0.2%, ETH down 1%
        v_btc = m.velocity("BTC", 60)
        v_eth = m.velocity("ETH", 60)
        assert v_btc is not None and v_btc > 0
        assert v_eth is not None and v_eth < 0

    def test_unknown_asset_returns_none(self):
        m = MultiAssetPriceBuffer(("BTC",))
        assert m.velocity("SOL", 60) is None
        assert m.latest("SOL") is None

    def test_auto_create_buffer_on_add(self):
        m = MultiAssetPriceBuffer(("BTC",))
        m.add("DOGE", 0.5, ts=1000)
        m.add("DOGE", 0.6, ts=1060)
        v = m.velocity("DOGE", 60)
        assert v is not None and abs(v - 0.2) < 1e-6
