from quoter.runner.paper_fill import PaperBook


def test_fills_when_price_touches_bid():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10)
    filled = pb.on_tick("YES", 0.48)      # ask dropped to <= our 0.50 bid
    assert filled == 10.0
    assert pb.inv["YES"] == 10.0
    assert abs(pb.cost["YES"] - 5.0) < 1e-9


def test_posted_tracks_all_bid_notional_even_unfilled():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10)              # $5 notional
    pb.post("NO", 0.30, 10)              # $3 notional
    assert abs(pb.posted() - 8.0) < 1e-9  # posted counts both, regardless of fills
    assert pb.spent() == 0.0             # nothing filled yet


def test_no_fill_when_price_above_bid():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10)
    assert pb.on_tick("YES", 0.55) == 0.0
    assert pb.inv["YES"] == 0.0


def test_fill_frac_haircut():
    pb = PaperBook(fill_frac=0.5)
    pb.post("YES", 0.50, 10)
    assert pb.on_tick("YES", 0.48) == 5.0


def test_wrong_side_no_fill():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10)
    assert pb.on_tick("NO", 0.10) == 0.0


def test_spent_sums_cost():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10); pb.on_tick("YES", 0.40)
    pb.post("NO", 0.30, 10); pb.on_tick("NO", 0.20)
    assert abs(pb.spent() - (5.0 + 3.0)) < 1e-9


def test_price_none_no_fill():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10)
    assert pb.on_tick("YES", None) == 0.0
