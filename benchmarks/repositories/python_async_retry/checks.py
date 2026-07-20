import asyncio

import pytest

from retrying import retry


def test_success_returns_without_sleeping():
    sleeps: list[float] = []

    async def operation() -> str:
        return "ok"

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    assert asyncio.run(retry(operation, sleep=fake_sleep)) == "ok"
    assert sleeps == []


def test_max_attempts_includes_initial_call():
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("temporary")
        return "unexpected success"

    async def fake_sleep(_delay: float) -> None:
        return None

    with pytest.raises(RuntimeError, match="temporary"):
        asyncio.run(retry(operation, max_attempts=2, sleep=fake_sleep))
    assert calls == 2


def test_cancelled_error_propagates_immediately():
    calls = 0
    sleeps: list[float] = []

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(retry(operation, max_attempts=4, sleep=fake_sleep))
    assert calls == 1
    assert sleeps == []


def test_exponential_backoff_uses_injected_virtual_sleep():
    attempts = 0
    sleeps: list[float] = []

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise OSError("retry me")
        return "done"

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    result = asyncio.run(
        retry(operation, max_attempts=4, base_delay=0.25, sleep=fake_sleep)
    )
    assert result == "done"
    assert sleeps == [0.25, 0.5, 1.0]


def test_invalid_retry_configuration_is_rejected_without_calling_operation():
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1

    async def fake_sleep(_delay: float) -> None:
        return None

    with pytest.raises(ValueError):
        asyncio.run(retry(operation, max_attempts=0, sleep=fake_sleep))
    with pytest.raises(ValueError):
        asyncio.run(retry(operation, base_delay=-0.01, sleep=fake_sleep))
    assert calls == 0
