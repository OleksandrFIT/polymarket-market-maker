from quoter.research.mm_calibrate import window_loss, calibrate
from quoter.research.mm_sim import simulate_window
from quoter.research.mm_policy import guru_like_quotes
from quoter.research.mm_types import Theta, WindowResult


def _wr(su, sd, au, ad):
    return WindowResult(su, sd, au, ad, au + ad, 0, 0, 0, 0)


def test_window_loss_zero_on_exact_match():
    r = _wr(100, 200, 0.4, 0.6)
    tgt = {"size_up": 100, "size_dn": 200, "avg_up": 0.4, "avg_dn": 0.6}
    assert window_loss(r, tgt) < 1e-12


def test_window_loss_positive_on_mismatch():
    r = _wr(50, 200, 0.4, 0.6)
    tgt = {"size_up": 100, "size_dn": 200, "avg_up": 0.4, "avg_dn": 0.6}
    assert window_loss(r, tgt) > 0.0


def _synthetic_tape():
    # crossing Up + Down sells so a guru_like bid fills on both sides
    return [{"ts": 10, "side": "SELL", "oi": 0, "price": 0.48, "size": 100},
            {"ts": 11, "side": "SELL", "oi": 1, "price": 0.48, "size": 100}]


def test_calibrate_recovers_known_theta():
    # Generate a synthetic "competitor" with a KNOWN theta, then confirm calibrate finds it.
    tape = _synthetic_tape()
    ticks = [(0, 0.50)]
    pol = lambda mid, inv: guru_like_quotes(mid, 100, 1)
    theta_true = Theta(fill=0.3)
    truth = simulate_window(tape, "Up", pol, theta_true, ticks)
    target = {"size_up": truth.gross_up, "size_dn": truth.gross_dn,
              "avg_up": truth.avg_up, "avg_dn": truth.avg_dn}
    grid = [Theta(fill=f) for f in (0.1, 0.2, 0.3, 0.5, 1.0)]
    best, loss, _ = calibrate([(tape, "Up", ticks)], [target], pol, grid)
    assert best.fill == 0.3
    assert loss < 1e-9
