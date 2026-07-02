from quoter.config import Config


def test_top_book_defaults():
    c = Config()
    assert c.tb_size == 5.0
    assert c.tb_naked_cap == 10.0
    assert c.tb_tick == 0.001
    assert c.tb_merge_min == 5.0


def test_top_book_strategy_selectable():
    c = Config(strategy="top_book")
    assert c.strategy == "top_book"
