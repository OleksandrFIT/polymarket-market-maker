from quoter.runner.regime_tracker import RegimeTracker


def _fill(rt, n, fav, entry, winner):
    for _ in range(n):
        rt.record(fav, entry, winner)


def test_empty_paper_ev_is_none():
    rt = RegimeTracker()
    assert rt.paper_ev() is None
    assert rt.hit_rate() is None
    assert rt.directional_enabled() is False


def test_ev_exactly_at_threshold_enabled():
    # entry+fee = 0.5; win → EV = 0.5 == min_ev → enabled (>= boundary).
    # 0.5 is exact in binary float, so the equality assert is safe.
    rt = RegimeTracker(window=20, min_samples=10, min_ev=0.5, fee=0.0)
    _fill(rt, 12, "Up", 0.5, "Up")
    assert rt.paper_ev() == 0.5
    assert rt.directional_enabled() is True


def test_warmup_disables_until_min_samples():
    rt = RegimeTracker(window=20, min_samples=12, min_ev=0.01, fee=0.02)
    _fill(rt, 11, "Up", 0.80, "Up")          # 11 wins, still < 12
    assert rt.directional_enabled() is False
    rt.record("Up", 0.80, "Up")               # 12th
    assert rt.directional_enabled() is True


def test_good_regime_enabled():
    rt = RegimeTracker(window=20, min_samples=12, min_ev=0.01, fee=0.02)
    _fill(rt, 20, "Up", 0.80, "Up")           # EV = 1 - 0.82 = 0.18
    assert rt.paper_ev() > 0.01
    assert rt.directional_enabled() is True


def test_losing_regime_disables():
    rt = RegimeTracker(window=20, min_samples=12, min_ev=0.01, fee=0.02)
    for i in range(20):
        rt.record("Up", 0.83, "Up" if i % 2 == 0 else "Down")   # 50% hit
    assert rt.paper_ev() < 0
    assert rt.directional_enabled() is False


def test_thin_margin_band_disables():
    # 70% hit at entry 0.83 → EV = 0.7*(1-0.85) - 0.3*0.85 = -0.15 < 0.
    # A hit-rate CB at 0.58 would WRONGLY enable this; the EV CB correctly pauses.
    rt = RegimeTracker(window=20, min_samples=10, min_ev=0.01, fee=0.02)
    for i in range(20):
        rt.record("Up", 0.83, "Up" if i % 10 < 7 else "Down")
    assert rt.hit_rate() == 0.70
    assert rt.directional_enabled() is False


def test_window_evicts_old():
    rt = RegimeTracker(window=5, min_samples=3, min_ev=0.01, fee=0.0)
    _fill(rt, 5, "Up", 0.5, "Up")
    assert rt.hit_rate() == 1.0
    _fill(rt, 5, "Up", 0.5, "Down")           # evicts the wins
    assert rt.hit_rate() == 0.0
