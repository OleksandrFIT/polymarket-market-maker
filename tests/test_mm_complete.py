from quoter.research.mm_complete import completion_buy


def test_completes_when_pair_below_threshold():
    # naked Up avg 0.20; light (Down) ask 0.50 -> pair 0.70 < 1 -> buy 10 Down @0.50
    assert completion_buy("Up", 0.20, 0.50, 10) == ("Down", 10.0, 0.50)


def test_light_side_is_opposite_of_heavy():
    assert completion_buy("Down", 0.20, 0.50, 10)[0] == "Up"


def test_no_completion_when_pair_at_or_above_threshold():
    assert completion_buy("Up", 0.60, 0.45, 10) is None      # 1.05 >= 1
    assert completion_buy("Up", 0.50, 0.50, 10) is None      # exactly 1.00, strict <


def test_no_completion_when_not_naked():
    assert completion_buy("Up", 0.20, 0.50, 0) is None


def test_threshold_parameter_is_respected():
    # pair 0.70; threshold 0.65 -> too expensive -> None
    assert completion_buy("Up", 0.20, 0.50, 10, threshold=0.65) is None
    assert completion_buy("Up", 0.20, 0.50, 10, threshold=0.80) == ("Down", 10.0, 0.50)
