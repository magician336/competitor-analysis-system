"""Async retry starter with intentional edge-case defects."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar


T = TypeVar("T")
Sleep = Callable[[float], Awaitable[None]]


async def retry(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.1,
    sleep: Sleep = asyncio.sleep,
) -> T:
    """Run an async operation until it succeeds or attempts are exhausted."""

    # The starter has off-by-one, cancellation and backoff defects.
    for attempt in range(max_attempts + 1):
        try:
            return await operation()
        except BaseException:
            if attempt == max_attempts:
                raise
            await sleep(base_delay * (attempt + 1))
    raise AssertionError("unreachable")
