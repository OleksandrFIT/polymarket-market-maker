from quoter.research.mm_sim import simulate_window
from quoter.research.mm_policy import guru_like_quotes
from quoter.research.mm_types import Theta


def T(ts, side, oi, price, size):
    return {"ts": ts, "side": side, "oi": oi, "price": price, "size": size}


def test_one_sided_winner_hold_to_resolution():
    # single Up bid @0.49 gets filled 10; Up wins -> pnl = 10*1 - 10*0.49 = 5.1
    tape = [T(10, "SELL", 0, 0.49, 10)]
    ticks = [(0, 0.50)]
    pol = lambda mid, inv: [q for q in guru_like_quotes(mid, 10, 1) if q.side == "Up"]
    r = simulate_window(tape, "Up", pol, Theta(fill=1.0), ticks)
    assert abs(r.gross_up - 10.0) < 1e-9
    assert abs(r.spent - 4.9) < 1e-9
    assert abs(r.pnl - 5.1) < 1e-9
    assert r.adverse == 0.0


def test_matched_pair_merges_and_locks_spread():
    # Both bids are @0.49 (guru_like: 1c below mid 0.50 on each side). A maker fills at
    # its OWN price, so both fill 10 @0.49. Merge 10 pairs: returned 10, spent 4.9+4.9=9.8
    # -> pnl = 10 - 9.8 = 0.2 regardless of winner; pair_cost = 0.49+0.49 = 0.98.
    tape = [T(10, "SELL", 0, 0.49, 10), T(11, "SELL", 1, 0.48, 10)]
    ticks = [(0, 0.50)]
    pol = lambda mid, inv: guru_like_quotes(mid, 10, 1)
    r = simulate_window(tape, "Up", pol, Theta(fill=1.0), ticks)
    assert abs(r.pnl - 0.2) < 1e-9
    assert abs(r.pair_cost - 0.98) < 1e-9
    assert r.adverse == 0.0


def test_naked_loser_is_adverse_loss():
    # only Down bid @0.49 fills 10 (at our price 0.49); Up wins -> Down worthless.
    # pnl = -4.9, adverse = 10
    tape = [T(10, "SELL", 1, 0.48, 10)]
    ticks = [(0, 0.50)]
    pol = lambda mid, inv: [q for q in guru_like_quotes(mid, 10, 1) if q.side == "Down"]
    r = simulate_window(tape, "Up", pol, Theta(fill=1.0), ticks)
    assert abs(r.pnl - (-4.9)) < 1e-9
    assert abs(r.adverse - 10.0) < 1e-9
