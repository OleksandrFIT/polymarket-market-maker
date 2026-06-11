from quoter.config import Config


def test_config_has_ladder_knobs_with_defaults():
    c = Config()
    assert c.ladder_anchor == "entry"
    assert c.rungs == 5
    assert c.rung_size == 5
    assert abs(c.rung_spacing - 0.03) < 1e-9
    assert c.naked_cap == 10
    assert abs(c.per_window_cap - 12.0) < 1e-9


def test_config_ladder_knobs_overridable():
    c = Config(ladder_anchor="book", rungs=8, rung_size=6, naked_cap=20)
    assert c.ladder_anchor == "book" and c.rungs == 8 and c.naked_cap == 20
