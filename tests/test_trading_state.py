"""Tests for the operator control state machine."""

from quoter.runner.trading_state import TradingState

WIN = 300
CUR = 1_000_000  # a current window open_ts
NEXT = CUR + WIN
PREV = CUR - WIN


def _enter(s, open_ts, time_left=250, mid=0.5, already=False):
    return s.should_enter(
        window_open_ts=open_ts, time_left=time_left, mid=mid,
        fresh_min_sec=230, balanced=(0.35, 0.65), already_traded=already,
    )


def test_default_stopped_never_enters():
    s = TradingState()
    assert s.mode == "STOPPED"
    assert _enter(s, NEXT) is False


def test_start_skips_current_enters_next():
    s = TradingState()
    s.start(current_window_open_ts=CUR)
    assert s.mode == "RUNNING"
    assert _enter(s, CUR) is False   # current window skipped
    assert _enter(s, NEXT) is True   # next window entered
    assert _enter(s, PREV) is False  # older window skipped


def test_stop_blocks_new_entry():
    s = TradingState()
    s.start(CUR)
    assert _enter(s, NEXT) is True
    s.stop()
    assert s.mode == "STOPPED"
    assert _enter(s, NEXT) is False


def test_force_stop_sets_flag_and_stops():
    s = TradingState()
    s.start(CUR)
    s.force_stop()
    assert s.mode == "STOPPED"
    assert _enter(s, NEXT) is False
    assert s.drain_force_stop() is True   # flag consumed
    assert s.drain_force_stop() is False  # one-shot


def test_entry_requires_fresh_window():
    s = TradingState()
    s.start(CUR)
    assert _enter(s, NEXT, time_left=300) is True
    assert _enter(s, NEXT, time_left=100) is False  # not fresh enough


def test_entry_requires_balanced_mid():
    s = TradingState()
    s.start(CUR)
    assert _enter(s, NEXT, mid=0.50) is True
    assert _enter(s, NEXT, mid=0.10) is False  # lopsided
    assert _enter(s, NEXT, mid=None) is False


def test_entry_skips_already_traded():
    s = TradingState()
    s.start(CUR)
    assert _enter(s, NEXT, already=True) is False


def test_restart_resets_force_flag():
    s = TradingState()
    s.force_stop()
    assert s.force_stop_requested is True
    s.start(CUR)
    assert s.force_stop_requested is False
    assert s.mode == "RUNNING"
