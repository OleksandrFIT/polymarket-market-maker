from quoter.config import Config


def test_config_has_trend_knobs_with_defaults():
    c = Config()
    assert c.trend_enabled is True
    assert abs(c.trend_confidence - 0.35) < 1e-9
    assert abs(c.trend_buffer_sec - 60.0) < 1e-9
    assert abs(c.trend_vol_fallback - 30.0) < 1e-9
    assert abs(c.trend_stale_sec - 10.0) < 1e-9


def test_config_trend_knobs_overridable():
    c = Config(trend_enabled=False, trend_confidence=0.30)
    assert c.trend_enabled is False and abs(c.trend_confidence - 0.30) < 1e-9
