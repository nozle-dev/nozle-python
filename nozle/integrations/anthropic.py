from __future__ import annotations

import inspect
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterator, Optional

from nozle.integrations._common import (
    StreamUsageCollector,
    call_parameter,
    call_text_bytes,
    normalize_anthropic_usage,
    safe_capture_provider_usage,
    safe_capture_provider_usage_async,
    value,
    with_method,
)

if TYPE_CHECKING:
    from nozle.client import Nozle
    from nozle.event_buffer import AnyEventBuffer


def wrap_anthropic(
    client: Any,
    nozle: Nozle,
    *,
    customer_id: str,
    metric_code: str = "llm_tokens",
    feature: Optional[str] = None,
    cost_meter_code: Optional[str] = None,
    buffer: Optional[AnyEventBuffer] = None,
) -> Any:
    """Wrap synchronous or asynchronous Anthropic messages with safe tracking.

    With ``buffer`` (from ``nozle.event_buffer()``, or ``event_buffer()`` of ``AsyncNozle``), the
    usage event is queued there and sent with its batches, so ``buffer.flush()`` or ``close()``
    delivers it; cost events are still sent at once.
    """

    def wrapped_create(original: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        requested_model = call_parameter(args, kwargs, "model", "")
        is_stream = bool(call_parameter(args, kwargs, "stream", False))
        input_bytes = call_text_bytes(args, kwargs)
        result = original(*args, **kwargs)
        if inspect.isawaitable(result):
            return _await_result(
                result,
                nozle,
                customer_id,
                metric_code,
                feature,
                cost_meter_code,
                requested_model,
                is_stream,
                started,
                buffer,
                input_bytes,
            )
        if is_stream:
            if hasattr(result, "__aiter__"):
                return _wrap_async_stream(
                    result,
                    nozle,
                    customer_id,
                    metric_code,
                    feature,
                    cost_meter_code,
                    requested_model,
                    started,
                    buffer,
                    input_bytes,
                )
            return _wrap_sync_stream(
                result,
                nozle,
                customer_id,
                metric_code,
                feature,
                cost_meter_code,
                requested_model,
                started,
                buffer,
                input_bytes,
            )
        _track_response(
            result,
            nozle,
            customer_id,
            metric_code,
            feature,
            cost_meter_code,
            requested_model,
            started,
            buffer,
        )
        return result

    return with_method(client, ("messages", "create"), wrapped_create)


async def _await_result(
    awaitable: Any,
    nozle: Nozle,
    customer_id: str,
    metric_code: str,
    feature: Optional[str],
    cost_meter_code: Optional[str],
    requested_model: Any,
    is_stream: bool,
    started: float,
    buffer: Optional[AnyEventBuffer],
    input_bytes: int,
) -> Any:
    result = await awaitable
    if is_stream:
        if hasattr(result, "__aiter__"):
            return _wrap_async_stream(
                result,
                nozle,
                customer_id,
                metric_code,
                feature,
                cost_meter_code,
                requested_model,
                started,
                buffer,
                input_bytes,
            )
        return _wrap_sync_stream(
            result,
            nozle,
            customer_id,
            metric_code,
            feature,
            cost_meter_code,
            requested_model,
            started,
            buffer,
            input_bytes,
        )
    usage = value(result, "usage")
    if usage is not None:
        response_model = value(result, "model")
        await safe_capture_provider_usage_async(
            nozle,
            customer_id,
            metric_code,
            feature=feature,
            cost_meter_code=cost_meter_code,
            provider="anthropic",
            model=requested_model if response_model is None else response_model,
            request_id=value(result, "id"),
            usage=normalize_anthropic_usage(usage),
            latency_ms=int((time.monotonic() - started) * 1000),
            buffer=buffer,
        )
    return result


def _track_response(
    result: Any,
    nozle: Nozle,
    customer_id: str,
    metric_code: str,
    feature: Optional[str],
    cost_meter_code: Optional[str],
    requested_model: Any,
    started: float,
    buffer: Optional[AnyEventBuffer],
) -> None:
    usage = value(result, "usage")
    if usage is not None:
        response_model = value(result, "model")
        safe_capture_provider_usage(
            nozle,
            customer_id,
            metric_code,
            feature=feature,
            cost_meter_code=cost_meter_code,
            provider="anthropic",
            model=requested_model if response_model is None else response_model,
            request_id=value(result, "id"),
            usage=normalize_anthropic_usage(usage),
            latency_ms=int((time.monotonic() - started) * 1000),
            buffer=buffer,
        )


def _wrap_sync_stream(
    stream: Any,
    nozle: Nozle,
    customer_id: str,
    metric_code: str,
    feature: Optional[str],
    cost_meter_code: Optional[str],
    model: Any,
    started: float,
    buffer: Optional[AnyEventBuffer],
    input_bytes: int,
) -> Iterator[Any]:
    collector = StreamUsageCollector()
    ended = False
    try:
        for chunk in stream:
            collector.add(chunk)
            yield chunk
        ended = True
    finally:
        # Also runs when the consumer stops early (GeneratorExit) or the stream raises.
        found = collector.result(ended, input_bytes)
        if found is not None:
            safe_capture_provider_usage(
                nozle,
                customer_id,
                metric_code,
                feature=feature,
                cost_meter_code=cost_meter_code,
                provider="anthropic",
                model=found.model or model,
                request_id=found.id,
                usage=found.usage,
                latency_ms=int((time.monotonic() - started) * 1000),
                buffer=buffer,
            )


async def _wrap_async_stream(
    stream: Any,
    nozle: Nozle,
    customer_id: str,
    metric_code: str,
    feature: Optional[str],
    cost_meter_code: Optional[str],
    model: Any,
    started: float,
    buffer: Optional[AnyEventBuffer],
    input_bytes: int,
) -> AsyncIterator[Any]:
    collector = StreamUsageCollector()
    ended = False
    try:
        async for chunk in stream:
            collector.add(chunk)
            yield chunk
        ended = True
    finally:
        # Also runs when the consumer closes the generator early (aclose) or the stream raises.
        found = collector.result(ended, input_bytes)
        if found is not None:
            await safe_capture_provider_usage_async(
                nozle,
                customer_id,
                metric_code,
                feature=feature,
                cost_meter_code=cost_meter_code,
                provider="anthropic",
                model=found.model or model,
                request_id=found.id,
                usage=found.usage,
                latency_ms=int((time.monotonic() - started) * 1000),
                buffer=buffer,
            )
