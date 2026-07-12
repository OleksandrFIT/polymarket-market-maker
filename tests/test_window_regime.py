"""Unit tests for the shared window-regime classifier in top_book_planner.

resample_grid/classify_regime are behavior-identical ports of _chop_detector_sim.resample/regime —
the SAME functions behind the offline 51/28/20 chop/reversal/trend behavior baseline. window_regime
is the post-hoc label the live telemetry uses so its confusion matrix matches that baseline exactly.
"""
from quoter.runner.top_book_planner import (
    resample_grid, classify_regime, window_regime)


def test_classify_trend():
    # monotone one-sided series never crosses 0.5 -> "trend"
    assert classify_regime([0.5, 0.6, 0.7, 0.8]) == "trend"


def test_classify_reversal():
    # exactly one 0.5-cross -> "reversal"
    assert classify_regime([0.4, 0.45, 0.55, 0.6]) == "reversal"


def test_classify_chop():
    # >=2 crosses -> "chop"
    assert classify_regime([0.4, 0.6, 0.4, 0.6]) == "chop"


def test_window_regime_none_when_short():
    # fewer than 3 points -> too short to classify
    assert window_regime([(0, 0.5), (20, 0.6)]) is None


def test_window_regime_matches_sim():
    # round-trip identity: window_regime == classify_regime(resample_grid(sorted(path)))
    path = [(5.0, 0.42), (70.0, 0.58), (150.0, 0.47), (240.0, 0.61)]
    assert window_regime(path) == classify_regime(resample_grid(sorted(path)))
