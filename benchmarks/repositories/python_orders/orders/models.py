"""Order domain model."""

from dataclasses import dataclass
from enum import Enum


class OrderStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"


@dataclass
class Order:
    order_id: str
    status: OrderStatus = OrderStatus.PENDING
