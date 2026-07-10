"""Pure execution-A/B functions: window_record metric + the two per-window portfolios."""
from quoter.research.exec_ab import window_record, top_book_window, momentum_window


def test_window_record_fully_paired():
    r = window_record("top_book", "s", merged=20.0, merged_cost=19.2,
                      inv_up=0.0, inv_dn=0.0, winner="Up", spent=19.2)
    assert r["pair_cost"] == 0.96
    assert r["pairs_merged"] == 20.0
    assert r["naked_resid"] == 0.0
    assert r["resid_outcome"] == "flat"
    assert r["match_naked"] is None
    assert abs(r["pnl"] - (20.0 - 19.2)) < 1e-9        # 20 pairs redeem $1 each, minus spent


def test_window_record_naked_won_and_lost():
    won = window_record("momentum", "s", merged=0.0, merged_cost=0.0,
                        inv_up=5.0, inv_dn=0.0, winner="Up", spent=2.5)
    assert won["pair_cost"] is None
    assert won["naked_resid"] == 5.0
    assert won["match_naked"] == 0.0
    assert won["resid_outcome"] == "WON"
    assert abs(won["pnl"] - (5.0 - 2.5)) < 1e-9         # 5 winning shares redeem $1, minus spent
    lost = window_record("momentum", "s", merged=0.0, merged_cost=0.0,
                         inv_up=5.0, inv_dn=0.0, winner="Down", spent=2.5)
    assert lost["resid_outcome"] == "LOST"
    assert abs(lost["pnl"] - (0.0 - 2.5)) < 1e-9        # loser residual expires worthless


def _snap(ts, ub, ua, db, da):
    return {"ts": ts,
            "yes": {"bids": [[f"{ub:.3f}", "500"]], "asks": [[f"{ua:.3f}", "500"]]},
            "no": {"bids": [[f"{db:.3f}", "500"]], "asks": [[f"{da:.3f}", "500"]]}}


def test_top_book_window_maker_shadow_fill_and_merge():
    # one snapshot mid-window: our Up bid 0.501 & Down bid 0.451; SELL prints at/below each
    # bid fill 5 apiece -> merge 5 -> pair_cost = 0.501 + 0.451 = 0.952, naked 0.
    slug = "btc-updown-5m-1000000000"
    snaps = [_snap(1000000010, 0.50, 0.55, 0.45, 0.50)]
    tape = [{"oi": 0, "side": "SELL", "price": 0.50, "size": 5.0, "ts": 1000000010},
            {"oi": 1, "side": "SELL", "price": 0.45, "size": 5.0, "ts": 1000000010}]
    r = top_book_window(snaps, tape, "Up", slug)
    assert r["style"] == "top_book"
    assert r["pairs_merged"] == 5.0
    assert r["naked_resid"] == 0.0
    assert abs(r["pair_cost"] - 0.952) < 1e-9


def test_momentum_window_taker_chase_pair_over_dollar():
    # rising Up mid (0.50 -> 0.60) fires chase "Up"; taker-buys Up@0.62 + Down@0.42 -> merge 5.
    # pair_cost = 0.62 + 0.42 = 1.04 (over $1: the taker-chase mechanism the sims measured -EV).
    slug = "btc-updown-5m-1000000000"
    snaps = [_snap(1000000000, 0.48, 0.52, 0.48, 0.52),
             _snap(1000000005, 0.58, 0.62, 0.38, 0.42)]
    r = momentum_window(snaps, tape=[], winner="Up", slug=slug)
    assert r["style"] == "momentum"
    assert r["pairs_merged"] == 5.0
    assert abs(r["pair_cost"] - 1.04) < 1e-9
    assert r["naked_resid"] == 0.0
