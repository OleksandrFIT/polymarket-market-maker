"""Smoke test for the detector-calibration sweep config grid (scripts/_detector_calib.py)."""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "_detector_calib", os.path.join(os.path.dirname(__file__), "..", "scripts", "_detector_calib.py"))
_dc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_dc)


def test_build_configs_shape():
    cfgs = _dc.build_configs()
    # 1 clock-only baseline + 3 dev x 3 confirm x 2 detect x 2 mode = 37
    assert len(cfgs) == 37
    labels = [c[0] for c in cfgs]
    assert "clock-only" in labels
    # the production defaults appear as a row (dev 0.28, confirm 10, detect 100, both modes)
    assert "0.28/10/100/hard" in labels
    assert "0.28/10/100/soft" in labels


def test_clock_only_never_trend_revokes():
    # the baseline's dev threshold is unreachable -> its revoke_mode is irrelevant; a huge dev means
    # trend never fires (only clock closes).
    base = dict(_dc.build_configs())["clock-only"]
    assert base["chop_dev_thresh"] >= 1.0


def test_both_modes_present_per_threshold():
    labels = [c[0] for c in _dc.build_configs() if c[0] != "clock-only"]
    hard = [l for l in labels if l.endswith("/hard")]
    soft = [l for l in labels if l.endswith("/soft")]
    assert len(hard) == 18 and len(soft) == 18
