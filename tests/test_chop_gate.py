"""chop_revoke: sliding, causal trend-commit signal for the revocable CLOSING gate."""
from quoter.runner.top_book_planner import chop_revoke
from quoter.runner.top_book_planner import plan_requote, TBQuote


def _q(side, price):
    return TBQuote(side, price, 5.0)


def h(*pairs):
    return list(pairs)


def test_committed_and_quiet_tail_revokes():
    # mid 0.85 now, one-sided (>=0.5) for the whole 60s tail -> trend, revoke.
    hist = h((40, 0.55), (60, 0.7), (80, 0.8), (100, 0.85))
    assert chop_revoke(hist, now=100, dev_thresh=0.28, lookback_sec=60) is True


def test_crossed_in_tail_does_not_revoke():
    # committed at 0.85 now, but it dipped below 0.5 at t=80 (within the 60s tail) -> still chop.
    hist = h((40, 0.55), (60, 0.7), (80, 0.42), (100, 0.85))
    assert chop_revoke(hist, now=100, dev_thresh=0.28, lookback_sec=60) is False


def test_not_committed_does_not_revoke():
    # mid 0.60 -> |0.60-0.5| = 0.10 < 0.28 -> not committed.
    hist = h((60, 0.55), (80, 0.58), (100, 0.60))
    assert chop_revoke(hist, now=100, dev_thresh=0.28, lookback_sec=60) is False


def test_mid_trend_crosses_early_commits_late_revokes():
    # THE point-1 bug: oscillates around 0.5 up to ~90s (crosses several times), then commits up.
    # At now=160 the 60s tail [100,160] is one-sided high -> MUST revoke (a cumulative rule would not).
    hist = h((20, 0.46), (40, 0.54), (60, 0.48), (80, 0.53), (100, 0.62),
             (120, 0.78), (140, 0.86), (160, 0.9))
    assert chop_revoke(hist, now=160, dev_thresh=0.28, lookback_sec=60) is True


def test_crossing_exactly_at_lookback_boundary_is_included():
    # a sample at t == now - lookback_sec is INCLUDED in the tail (>=). It sits below 0.5 while now
    # is committed high -> that boundary crossing keeps it chop (not revoked).
    hist = h((40, 0.42), (70, 0.55), (100, 0.85))   # t=40 == 100-60, on the low side
    assert chop_revoke(hist, now=100, dev_thresh=0.28, lookback_sec=60) is False


def test_pull_up_replaces_when_shift_and_dwell_met():
    resting = {"Up": (0.50, 5.0)}
    target = [_q("Up", 0.53)]                    # +0.03 >= 0.02 shift
    cancel, post, cap = plan_requote(resting, target, {"Up": 0.0}, now=10, replace_shift=0.02, dwell_sec=4)
    assert cancel == ["Up"] and len(post) == 1 and post[0].price == 0.53 and cap == []


def test_down_move_never_chases():
    resting = {"Up": (0.50, 5.0)}
    target = [_q("Up", 0.46)]                    # mid fell to our bid -> our plan, do NOT chase down
    cancel, post, cap = plan_requote(resting, target, {"Up": 0.0}, now=10)
    assert cancel == [] and post == [] and cap == []


def test_cap_override_forces_down_ignoring_shift_and_dwell():
    # INVARIANT (pair<$1): our resting bid 0.50 now sits ABOVE the linked-pair cap 0.44 (heavy_avg
    # grew). Must cancel+repost down to <=cap even though it's a down move AND dwell hasn't elapsed —
    # else a fill assembles a pair >= $1. Counted in cap_sides, not shift-driven.
    resting = {"Up": (0.50, 5.0)}
    cancel, post, cap = plan_requote(resting, [_q("Up", 0.44)], {"Up": 9.99}, now=10,
                                     caps={"Up": 0.44}, replace_shift=0.02, dwell_sec=4)
    assert cancel == ["Up"] and post and post[0].price == 0.44 and cap == ["Up"]


def test_within_shift_keeps():
    resting = {"Up": (0.50, 5.0)}
    cancel, post, cap = plan_requote(resting, [_q("Up", 0.505)], {"Up": 0.0}, now=10, replace_shift=0.02)
    assert cancel == [] and post == []


def test_dwell_not_elapsed_keeps():
    resting = {"Up": (0.50, 5.0)}
    cancel, post, cap = plan_requote(resting, [_q("Up", 0.55)], {"Up": 8.0}, now=10, dwell_sec=4)  # 2<4
    assert cancel == [] and post == []


def test_new_side_posts_and_dropped_side_cancels():
    cancel, post, cap = plan_requote({"Down": (0.10, 5.0)}, [_q("Up", 0.90)], {}, now=10)
    assert "Down" in cancel and any(q.side == "Up" for q in post)
