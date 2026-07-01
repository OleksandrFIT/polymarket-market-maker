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


def test_lag_no_double_count():
    # Two ticks at ts=0 and ts=10, lag=5. A single crossing SELL of 100 on Up sits at
    # ts=12, i.e. inside the FORMER overlap zone [ts_1, ts_1+lag) = [10, 15). With the old
    # slice logic tick 0 covered [0, 10+5)=[0,15) AND tick 1 covered [10, last+5) -> the
    # trade filled TWICE (gross_up=200, spent double). Non-overlapping partition:
    #   tick 0: [0, 10+5) = [0, 15)   contains ts=12  -> fills the 100 once
    #   tick 1: [10+5, last) = [15, .) does NOT contain ts=12
    # so gross_up == 100 and spent == 100*0.49 == 49 (one fill only).
    tape = [T(12, "SELL", 0, 0.49, 100)]
    ticks = [(0, 0.50), (10, 0.50)]
    pol = lambda mid, inv: [q for q in guru_like_quotes(mid, 100, 1) if q.side == "Up"]
    r = simulate_window(tape, "Up", pol, Theta(fill=1.0, lag=5), ticks)
    assert abs(r.gross_up - 100.0) < 1e-9   # counted ONCE, not 200
    assert abs(r.spent - 49.0) < 1e-9       # 100 * 0.49, single fill
    assert abs(r.pnl - 51.0) < 1e-9         # Up wins: 100*1 - 49
    assert r.adverse == 0.0


def test_multi_tick_fill_merge_fill():
    # Up fills on tick 1, Down fills on tick 2, then they merge across ticks.
    # tick 0 @ ts=0 (slice [0,10)): Up SELL 10 @0.49 -> live Up=10, no Down yet, no merge.
    # tick 1 @ ts=10 (slice [10, last)): Down SELL 10 @0.49 -> live Down=10; then merge
    # min(10,10)=10 -> returned 10, live back to 0/0. spent = 4.9+4.9 = 9.8.
    # pnl = returned - spent = 10 - 9.8 = 0.2, and adverse == 0 (nothing held to resolution).
    tape = [T(5, "SELL", 0, 0.49, 10), T(15, "SELL", 1, 0.48, 10)]
    ticks = [(0, 0.50), (10, 0.50)]
    pol = lambda mid, inv: guru_like_quotes(mid, 10, 1)
    r = simulate_window(tape, "Up", pol, Theta(fill=1.0), ticks)
    assert abs(r.gross_up - 10.0) < 1e-9
    assert abs(r.gross_dn - 10.0) < 1e-9
    assert abs(r.pnl - 0.2) < 1e-9
    assert r.adverse == 0.0
