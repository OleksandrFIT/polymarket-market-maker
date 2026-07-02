"""Committed-capital gate for the top-of-book MM (phase-27 review fix).

committed = realized cost basis (never decremented, even after merge) + resting
notional. A post is allowed only while committed + quote notional stays below
the per-window cap — the gate is re-checked per approved post inside a tick, so
several same-tick posts cannot jointly overshoot.
"""
from quoter.runner.top_book_planner import committed_gate, TBQuote


def test_gate_blocks_when_committed_plus_quote_exceeds_cap():
    assert committed_gate(10.0, 10.0, {"Up": (0.5, 10)}, TBQuote("Down", 0.5, 10), cap=30.0) is False


def test_gate_allows_reprice_neutral_requote():
    # $20 filled + $5 resting, new $5 quote, cap 40 -> allowed
    assert committed_gate(10.0, 10.0, {"Up": (0.5, 10)}, TBQuote("Down", 0.5, 10), cap=40.0) is True


def test_gate_counts_all_resting_sides():
    resting = {"Up": (0.5, 10), "Down": (0.4, 10)}  # $5 + $4 resting
    # 0 filled, $9 resting, $5 quote = $14 committed
    assert committed_gate(0.0, 0.0, resting, TBQuote("Up", 0.5, 10), cap=13.0) is False
    assert committed_gate(0.0, 0.0, resting, TBQuote("Up", 0.5, 10), cap=15.0) is True


def test_gate_uses_cost_even_after_merge_semantics():
    # cost basis is never decremented: $12 gross spend blocks under cap 12
    # regardless of inventory having been merged back to cash.
    assert committed_gate(6.0, 6.0, {}, TBQuote("Up", 0.5, 5), cap=12.0) is False


def test_gate_allows_first_quote_of_fresh_window():
    assert committed_gate(0.0, 0.0, {}, TBQuote("Up", 0.5, 5), cap=12.0) is True


def test_same_tick_accumulation_via_resting_updates():
    # Simulates the runner loop: after approving a post the quote is added to
    # `resting`, so the SECOND same-tick quote sees the first one's notional.
    cap = 8.0
    resting: dict[str, tuple[float, float]] = {}
    q1, q2 = TBQuote("Up", 0.5, 10), TBQuote("Down", 0.45, 10)  # $5 + $4.5
    assert committed_gate(0.0, 0.0, resting, q1, cap) is True
    resting[q1.side] = (q1.price, q1.size)
    assert committed_gate(0.0, 0.0, resting, q2, cap) is False
