"""Plumbing smoke test for scripts/_day_slice.py — the per-day robustness slice of the chosen
chop-gate config. Not an EV assertion: verifies CFG is the chosen live config and that day_stats
aggregates one synthetic day's windows into finite (n_win, n_pair, pair_eff, pnl, tot) values."""
import importlib.util
import os

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_day_slice", os.path.join(os.path.dirname(__file__), "..", "scripts", "_day_slice.py"))
_ds = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_ds)


def _snap(ts, ub, ua, db, da):
    return {"ts": ts,
            "yes": {"bids": [[f"{ub:.3f}", "500"]], "asks": [[f"{ua:.3f}", "500"]]},
            "no": {"bids": [[f"{db:.3f}", "500"]], "asks": [[f"{da:.3f}", "500"]]}}


SLUG = "btc-updown-5m-1000000000"
OPEN = 1000000000
SNAPS = [_snap(OPEN + 250, 0.50, 0.55, 0.45, 0.50)]         # rel_ts 250s -> both legs fill, merge 5
TAPE = [{"oi": 0, "side": "SELL", "price": 0.50, "size": 5.0, "ts": OPEN + 250},
        {"oi": 1, "side": "SELL", "price": 0.45, "size": 5.0, "ts": OPEN + 250}]


def test_cfg_is_chosen_config():
    assert _ds.CFG == dict(replace_shift=0.02, freeze_sec=45.0, chop_lookback_sec=60.0)


def test_day_stats_aggregates_one_window(monkeypatch):
    monkeypatch.setattr(_ds, "load_window", lambda slug: (TAPE, "Up", None))
    n_win, n_pair, m_eff, m_pnl, t_pnl = _ds.day_stats({SLUG: SNAPS})
    assert n_win == 1
    assert n_pair == 5
    assert m_eff == m_eff              # not nan (a pair merged -> pair_eff defined)
    assert m_eff < 1.0                 # rebate-adjusted pair cost sits below $1 on this fill
    assert isinstance(t_pnl, float)


def test_day_stats_empty_day_is_nan_eff(monkeypatch):
    # a window that never resolves (load_window -> falsy) contributes nothing -> no pairs, nan eff
    monkeypatch.setattr(_ds, "load_window", lambda slug: None)
    n_win, n_pair, m_eff, m_pnl, t_pnl = _ds.day_stats({SLUG: SNAPS})
    assert n_win == 0
    assert n_pair == 0
    assert m_eff != m_eff              # nan when no pair merged
    assert t_pnl == 0.0
