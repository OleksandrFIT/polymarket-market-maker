from quoter.research.mm_types import Quote, Theta, FillResult, WindowResult


def test_quote_holds_side_price_size():
    q = Quote("Up", 0.42, 5.0)
    assert (q.side, q.price, q.size) == ("Up", 0.42, 5.0)


def test_theta_defaults_lag_zero():
    t = Theta(fill=0.3)
    assert t.fill == 0.3 and t.lag == 0.0


def test_fillresult_and_windowresult_fields():
    fr = FillResult(filled=3.0, avg_price=0.42)
    assert fr.filled == 3.0 and fr.avg_price == 0.42
    wr = WindowResult(gross_up=1, gross_dn=2, avg_up=0.4, avg_dn=0.5,
                      pair_cost=0.9, spent=3.0, returned=3.5, pnl=0.5, adverse=0.0)
    assert wr.pnl == 0.5 and wr.pair_cost == 0.9
