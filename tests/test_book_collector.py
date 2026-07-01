from quoter.research.book_collector import current_slug, snapshot_record


def test_current_slug_floors_to_5m_boundary():
    # 1782909130 is inside the window opening at 1782909000 (=5943030*300)
    assert current_slug(1782909130) == "btc-updown-5m-1782909000"
    assert current_slug(1782909000) == "btc-updown-5m-1782909000"


def test_snapshot_record_normalizes_clob_books():
    yb = {"bids": [{"price": "0.52", "size": "100"}], "asks": [{"price": "0.55", "size": "40"}]}
    nb = {"bids": [{"price": "0.46", "size": "80"}], "asks": [{"price": "0.49", "size": "30"}]}
    rec = snapshot_record(1782909130, "btc-updown-5m-1782909000", yb, nb)
    assert rec["ts"] == 1782909130
    assert rec["slug"] == "btc-updown-5m-1782909000"
    assert rec["yes"]["bids"] == [[0.52, 100.0]]
    assert rec["yes"]["asks"] == [[0.55, 40.0]]
    assert rec["no"]["bids"] == [[0.46, 80.0]]


def test_snapshot_record_handles_missing_sides():
    rec = snapshot_record(1, "s", {}, {"bids": [{"price": "0.4", "size": "5"}]})
    assert rec["yes"]["bids"] == [] and rec["yes"]["asks"] == []
    assert rec["no"]["bids"] == [[0.4, 5.0]]
