from decimal import Decimal

import pytest

from apps.inventory.domain import CostFormula, InsufficientStock, Stock

D = Decimal


def stocked(*receipts):
    stock = Stock()
    for index, (quantity, value) in enumerate(receipts, start=1):
        stock.receive(D(quantity), D(value), source=index)
    return stock


def test_fifo_takes_the_oldest_cost_first():
    stock = stocked(("10", "100000"), ("10", "150000"))  # 10 @ 10.000, then 10 @ 15.000
    assert stock.issue(D("12"), CostFormula.FIFO) == D("130000")  # 10 x 10.000 + 2 x 15.000
    assert (stock.quantity, stock.value) == (D("8"), D("120000"))


def test_weighted_average_uses_the_moving_average():
    stock = stocked(("10", "100000"), ("10", "150000"))
    assert stock.issue(D("12"), CostFormula.WEIGHTED_AVERAGE) == D("150000")  # 12 x 12.500
    assert (stock.quantity, stock.value, stock.average_cost) == (D("8"), D("100000"), D("12500"))
    stock.receive(D("2"), D("40000"))  # average moves to 14.000
    assert stock.issue(D("10"), CostFormula.WEIGHTED_AVERAGE) == D("140000") and stock.value == 0


def test_rounding_never_loses_a_cent():
    stock = stocked(("3", "100000"))
    costs = [stock.issue(D("1"), formula) for formula in (CostFormula.WEIGHTED_AVERAGE, CostFormula.FIFO, CostFormula.WEIGHTED_AVERAGE)]
    assert sum(costs) == D("100000") and stock.value == 0  # the last unit takes what is left


def test_selling_more_than_in_stock_is_refused():
    stock = stocked(("2", "20000"))
    with pytest.raises(InsufficientStock) as exc:
        stock.issue(D("2.5"), CostFormula.FIFO)
    assert exc.value.available == D("2")


def test_a_receipt_can_be_taken_back_only_while_whole():
    stock = stocked(("10", "100000"), ("5", "75000"))
    stock.issue(D("4"), CostFormula.FIFO)  # comes out of the first layer
    assert stock.can_remove(2, D("5"), D("75000"))  # the second is untouched
    assert not stock.can_remove(1, D("10"), D("100000"))
    stock.remove(2, D("5"), D("75000"))
    assert (stock.quantity, stock.value) == (D("6"), D("60000"))

    averaged = stocked(("10", "100000"), ("5", "75000"))
    averaged.issue(D("1"), CostFormula.WEIGHTED_AVERAGE)  # merges everything into one layer
    assert not averaged.can_remove(2, D("5"), D("75000"))
