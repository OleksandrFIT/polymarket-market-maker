# tests/test_fill_inventory.py
from quoter.runner.fill_inventory import inventory_from_fills


def _f(side, action, size, price):
    return {"side": side, "action": action, "size": size, "price": price}


def test_buys_only_two_sided():
    inv = inventory_from_fills([
        _f("YES", "BUY", 10, 0.46), _f("NO", "BUY", 10, 0.40)])
    assert inv.inv["YES"] == 10 and inv.inv["NO"] == 10
    assert abs(inv.cost["YES"] - 4.60) < 1e-9
    assert abs(inv.avg("YES") - 0.46) < 1e-9
    assert abs(inv.avg("NO") - 0.40) < 1e-9


def test_window2_one_sided_is_naked_not_balanced():
    # the bug case: 10 Up bought (two fills), 0 Down -> naked 10, NOT 0
    inv = inventory_from_fills([
        _f("YES", "BUY", 5, 0.27), _f("YES", "BUY", 5, 0.36)])
    assert inv.inv["YES"] == 10 and inv.inv["NO"] == 0
    assert (inv.inv["YES"] - inv.inv["NO"]) == 10
    assert inv.avg("NO") is None


def test_sell_reduces_net_qty():
    inv = inventory_from_fills([
        _f("YES", "BUY", 10, 0.50), _f("YES", "SELL", 4, 0.40)])
    assert inv.inv["YES"] == 6           # 10 bought - 4 sold
    assert abs(inv.cost["YES"] - 5.00) < 1e-9   # buy cost basis unchanged by the sell
    assert abs(inv.avg("YES") - 0.50) < 1e-9    # avg of BUYS


def test_empty_fills():
    inv = inventory_from_fills([])
    assert inv.inv["YES"] == 0 and inv.inv["NO"] == 0
    assert inv.avg("YES") is None
