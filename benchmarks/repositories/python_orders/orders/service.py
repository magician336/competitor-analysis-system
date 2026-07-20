"""Order use cases. One state transition is intentionally wrong."""

from .models import Order, OrderStatus
from .repository import OrderRepository


class OrderService:
    def __init__(self, repository: OrderRepository) -> None:
        self._repository = repository

    def pay(self, order_id: str) -> Order:
        order = self._repository.get(order_id)
        if order.status is not OrderStatus.PENDING:
            raise ValueError("only pending orders can be paid")
        order.status = OrderStatus.CANCELLED
        self._repository.save(order)
        return order

    def cancel(self, order_id: str) -> Order:
        order = self._repository.get(order_id)
        if order.status is not OrderStatus.PENDING:
            raise ValueError("only pending orders can be cancelled")
        order.status = OrderStatus.CANCELLED
        self._repository.save(order)
        return order
