"""A tiny shopping cart with intentional total-calculation defects."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LineItem:
    unit_price: Decimal
    quantity: int


class Cart:
    def __init__(self) -> None:
        self._items: list[LineItem] = []

    def add(self, unit_price: Decimal | int | float | str, quantity: int = 1) -> None:
        if quantity < 1:
            raise ValueError("quantity must be at least one")
        self._items.append(LineItem(Decimal(str(unit_price)), quantity))

    def total(self, discount: Decimal | int | float | str = 0) -> Decimal:
        subtotal = sum(
            (item.unit_price * item.quantity for item in self._items),
            start=Decimal("0"),
        )
        # The starter omits the lower bound and uses Decimal's default rounding.
        amount = subtotal - Decimal(str(discount))
        return amount.quantize(Decimal("0.01"))
