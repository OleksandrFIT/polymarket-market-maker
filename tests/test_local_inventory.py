from quoter.runner.local_inventory import LocalInventory


def test_debit_reduces_inventory_and_cost():
    inv = LocalInventory()
    inv.credit_fill("YES", 10, 0.50)   # inv 10, cost 5.00, avg 0.50
    inv.debit_fill("YES", 4, 0.40)     # sell 4 at 0.40
    assert inv.inv["YES"] == 6
    # cost basis drops by 4 * avg(0.50) = 2.00 -> 3.00 (remaining 6 * 0.50)
    assert abs(inv.cost["YES"] - 3.00) < 1e-9


def test_debit_leaves_pairs_intact():
    inv = LocalInventory()
    inv.credit_fill("YES", 10, 0.46)
    inv.credit_fill("NO", 5, 0.40)     # naked +5 (YES heavy)
    inv.debit_fill("YES", 5, 0.43)     # flatten the 5 naked YES
    assert inv.inv["YES"] == 5 and inv.inv["NO"] == 5  # 5 pairs survive


def test_debit_clamps_at_zero_never_negative():
    inv = LocalInventory()
    inv.credit_fill("NO", 5, 0.30)
    inv.debit_fill("NO", 99, 0.20)     # try to sell more than held
    assert inv.inv["NO"] == 0
    assert inv.cost["NO"] == 0.0


def test_debit_zero_or_empty_is_noop():
    inv = LocalInventory()
    inv.debit_fill("YES", 5, 0.30)     # nothing held
    assert inv.inv["YES"] == 0 and inv.cost["YES"] == 0.0
