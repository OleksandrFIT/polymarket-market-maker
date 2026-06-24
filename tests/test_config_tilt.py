# tests/test_config_tilt.py
from quoter.config import Config


def test_tilt_fields_have_defaults():
    c = Config()
    assert c.tilt_enabled is False
    assert c.tilt_cutoff_sec == 45.0
    assert c.tilt_fee == 0.02
    assert c.tilt_max_price == 0.90
    assert c.regime_window == 20
    assert c.regime_min_samples == 12
    assert c.regime_min_ev == 0.01
