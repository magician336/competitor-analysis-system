"""In-memory repository used by the benchmark."""

from .models import Order


class OrderRepository:
    def __init__(self, orders: list[Order] | None = None) -> None:
        self._orders = {order.order_id: order for order in (orders or [])}

    def get(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise LookupError(f"unknown order: {order_id}") from exc

    def save(self, order: Order) -> None:
        self._orders[order.order_id] = order
