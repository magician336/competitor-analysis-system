"""LangChain LCEL primitives used by the week-three analysis workflows.

LangChain is a required project dependency.  Keeping these imports in one
module gives callers a clear installation error and makes it easy to assert
that production runs are using real LCEL objects rather than a local mimic.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

try:
    from langchain_core.runnables import (
        Runnable,
        RunnableLambda,
        RunnableParallel,
        RunnablePassthrough,
    )
    from langchain_core.tools import StructuredTool
except ImportError as exc:  # pragma: no cover - exercised only in broken environments.
    raise RuntimeError(
        "CodeRadar week-three Agents require LangChain. Activate the CodeRadar "
        "Conda environment and run: python -m pip install -r requirement.txt"
    ) from exc


def runnable_lambda(func: Callable[[Any], Any], *, name: str | None = None) -> Runnable:
    return RunnableLambda(func, name=name)


def runnable_parallel(
    branches: Mapping[str, Runnable | Callable[[Any], Any]],
) -> RunnableParallel:
    return RunnableParallel(dict(branches))


def structured_tool(
    *,
    func: Callable[..., Any],
    name: str,
    description: str,
    args_schema: type[Any],
) -> StructuredTool:
    return StructuredTool.from_function(
        func=func,
        name=name,
        description=description,
        args_schema=args_schema,
    )


__all__ = [
    "Runnable",
    "RunnableParallel",
    "RunnablePassthrough",
    "runnable_lambda",
    "runnable_parallel",
    "structured_tool",
]
