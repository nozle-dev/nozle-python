"""Each API call is written once and runs on both ``Nozle`` and ``AsyncNozle``.

An operation is a generator method. It validates its arguments, builds the request and
``yield``s a step: a callable without arguments that sends one request (``transport.call(...)``).
The runner calls the step and sends the response payload back into the generator, which then
returns the result. ``run_sync`` calls each step; ``run_async`` calls it and awaits the result.
The ``endpoint`` decorator picks the runner of the resource's transport, so ``nozle.customers``
returns the result and ``async_nozle.customers`` returns an awaitable of the same result.
"""

from __future__ import annotations

import sys
import warnings
from functools import update_wrapper, wraps
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Generator, Generic, Optional, cast


class Sync:
    """Marks a resource whose operations return their result."""


class Async:
    """Marks a resource whose operations return an awaitable of their result."""


if TYPE_CHECKING:
    from typing import overload

    from typing_extensions import Concatenate, ParamSpec, Protocol, TypeVar

    from nozle._transport import BaseTransport

    P = ParamSpec("P")
    T = TypeVar("T")
    T_co = TypeVar("T_co", covariant=True)
    # Defaults to Sync, so a bare ``CustomersNamespace`` annotation keeps its synchronous types.
    M = TypeVar("M", default=Sync)

    class _Operation(Protocol[P, T_co]):
        @overload
        def __get__(self, obj: None, owner: Any) -> _Operation[P, T_co]: ...
        @overload
        def __get__(self, obj: Resource[Async], owner: Any) -> Callable[P, Awaitable[T_co]]: ...
        @overload
        def __get__(self, obj: Resource[Sync], owner: Any) -> Callable[P, T_co]: ...

else:
    from typing import TypeVar

    T = TypeVar("T")
    M = TypeVar("M")

Step = Callable[[], Any]
Steps = Generator[Step, Any, T]


def run_sync(steps: Steps[T]) -> T:
    """Run an operation, calling each request step in turn."""
    try:
        step = next(steps)
        while True:
            try:
                result = step()
            except BaseException as error:  # the operation may handle it, as a plain call would
                step = steps.throw(error)
            else:
                step = steps.send(result)
    except StopIteration as stop:
        return cast(T, stop.value)


async def run_async(steps: Steps[T]) -> T:
    """Run an operation, awaiting each request step in turn."""
    try:
        step = next(steps)
        while True:
            try:
                result = await step()
            except BaseException as error:  # the operation may handle it, as a plain call would
                step = steps.throw(error)
            else:
                step = steps.send(result)
    except StopIteration as stop:
        return cast(T, stop.value)


class Resource(Generic[M]):
    """A namespace whose operations run on its transport: sync or async."""

    def __init__(self, transport: BaseTransport[M], api_key: str) -> None:
        self._transport = transport
        self._api_key = api_key


class _BoundOperation:
    def __init__(self, fn: Callable[..., Steps[Any]]) -> None:
        self._fn = fn
        update_wrapper(cast(Any, self), fn)

    def __get__(self, obj: Optional[Resource[Any]], owner: Any = None) -> Any:
        if obj is None:
            return self
        fn = self._fn
        run = obj._transport.run

        @wraps(fn)
        def bound(*args: Any, **kwargs: Any) -> Any:
            return run(fn(obj, *args, **kwargs))

        return bound


def endpoint(fn: Callable[Concatenate[Any, P], Steps[T]]) -> _Operation[P, T]:
    """Turn a generator method into a method that returns its result, or an awaitable of it."""
    return cast("_Operation[P, T]", _BoundOperation(fn))


def warn_caller(message: str, category: type[Warning]) -> None:
    """Warn at the first frame outside this package, whether the call is sync or async."""
    frame = sys._getframe(1)
    level = 2
    while frame.f_back is not None and frame.f_globals.get("__name__", "").startswith("nozle."):
        frame = frame.f_back
        level += 1
    warnings.warn(message, category, stacklevel=level)
