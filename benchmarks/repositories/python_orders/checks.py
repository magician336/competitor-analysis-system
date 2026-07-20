import hashlib
from pathlib import Path

import pytest

from orders import Order, OrderRepository, OrderService, OrderStatus


ROOT = Path(__file__).resolve().parent


def _service_with(order: Order) -> tuple[OrderService, OrderRepository]:
    repository = OrderRepository([order])
    return OrderService(repository), repository


def test_paying_pending_order_persists_paid_status():
    service, repository = _service_with(Order("order-1"))
    returned = service.pay("order-1")
    assert returned.status is OrderStatus.PAID
    assert repository.get("order-1").status is OrderStatus.PAID


def test_cancelling_pending_order_still_works():
    service, repository = _service_with(Order("order-2"))
    returned = service.cancel("order-2")
    assert returned.status is OrderStatus.CANCELLED
    assert repository.get("order-2").status is OrderStatus.CANCELLED


@pytest.mark.parametrize("initial", [OrderStatus.PAID, OrderStatus.CANCELLED])
def test_non_pending_order_cannot_be_paid(initial):
    service, _ = _service_with(Order("order-3", status=initial))
    with pytest.raises(ValueError, match="pending"):
        service.pay("order-3")


def test_unknown_order_error_remains_stable():
    service = OrderService(OrderRepository())
    with pytest.raises(LookupError, match="unknown order"):
        service.pay("missing")


def test_contract_modules_are_unchanged():
    expected = {
        "orders/models.py": "30b04c10342b085f3346f1b7378fc97dbf4d86c88b14c9ec9bfb89a293ddeb84",
        "orders/repository.py": "0a28d974f8e4de34431eaea04a541d919f7c632ed3fcf25d953102fc60bf1041",
    }
    for relative_path, expected_digest in expected.items():
        normalized = (ROOT / relative_path).read_text(encoding="utf-8").replace(
            "\r\n", "\n"
        )
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        assert digest == expected_digest, f"protected contract changed: {relative_path}"


def test_no_unrelated_python_modules_were_added():
    allowed = {
        "checks.py",
        "orders/__init__.py",
        "orders/models.py",
        "orders/repository.py",
        "orders/service.py",
    }
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    assert actual == allowed
