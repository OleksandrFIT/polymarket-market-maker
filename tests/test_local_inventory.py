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


def test_reconcile_down_lowers_after_grace():
    inv = LocalInventory()
    inv.credit_fill("NO", 10, 0.40)          # optimistic 10
    inv.reconcile_down("NO", 5, now=0.0, grace=6.0)   # real=5, gap opens
    assert inv.inv["NO"] == 10                          # not yet (grace not elapsed)
    inv.reconcile_down("NO", 5, now=3.0, grace=6.0)
    assert inv.inv["NO"] == 10                          # still within grace
    inv.reconcile_down("NO", 5, now=6.0, grace=6.0)
    assert inv.inv["NO"] == 5                            # persisted >= grace -> trust real
    assert abs(inv.cost["NO"] - 5 * 0.40) < 1e-9        # cost scaled to remaining


def test_reconcile_down_real_fill_not_reversed():
    inv = LocalInventory()
    inv.credit_fill("NO", 5, 0.40)           # optimistic 5 (a real fill, feed lags)
    inv.reconcile_down("NO", 0, now=0.0, grace=6.0)    # real still 0 (lag) -> timer starts
    inv.reconcile_down("NO", 5, now=3.0, grace=6.0)    # real catches up < grace
    assert inv.inv["NO"] == 5                            # NOT reversed (gap cleared in time)


def test_reconcile_down_noop_when_real_ge_local():
    inv = LocalInventory()
    inv.credit_fill("YES", 5, 0.50)
    inv.reconcile_down("YES", 9, now=10.0, grace=6.0)  # real higher -> reconcile_down ignores
    assert inv.inv["YES"] == 5
