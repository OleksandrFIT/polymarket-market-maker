from quoter.config import Config


def test_five_min_fields_have_defaults():
    c = Config()
    assert c.strategy == "tilt"
    assert c.lean == 3
    assert c.band_lo == 0.62
    assert c.band_hi == 0.78
