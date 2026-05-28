from quoter.backtest.fetch import parse_price_history
from quoter.backtest.models import PricePoint


def test_parse_price_history():
    raw = {"history": [{"t": 100, "p": 0.485}, {"t": 160, "p": 0.295}]}
    pts = parse_price_history(raw)
    assert pts == [PricePoint(100, 0.485), PricePoint(160, 0.295)]

def test_parse_empty_history():
    assert parse_price_history({"history": []}) == []
