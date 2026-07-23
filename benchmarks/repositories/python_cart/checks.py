from decimal import Decimal

import pytest

from cart import Cart


def test_empty_cart_is_exact_zero():
    assert Cart().total() == Decimal("0.00")


def test_quantity_and_fixed_discount():
    cart = Cart()
    cart.add("12.50", quantity=2)
    cart.add("5.25")
    assert cart.total(discount="3.00") == Decimal("27.25")


def test_discount_can_never_make_total_negative():
    cart = Cart()
    cart.add("8.00")
    assert cart.total(discount="20.00") == Decimal("0.00")


def test_money_uses_half_up_rounding():
    cart = Cart()
    cart.add("2.685")
    assert cart.total() == Decimal("2.69")


@pytest.mark.parametrize("discount", ["0", 0, Decimal("0.00")])
def test_supported_discount_representations(discount):
    cart = Cart()
    cart.add("0.10")
    cart.add("0.20")
    assert cart.total(discount=discount) == Decimal("0.30")
