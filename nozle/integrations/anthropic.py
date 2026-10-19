from __future__ import annotations

import inspect
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterator, Optional

from nozle.integrations._common import (
    call_parameter,
    normalize_anthropic_usage,
    safe_capture_provider_usage,
    safe_capture_provider_usage_async,
    value,
    with_method,
)

if TYPE_CHECKING:
    from nozle.client import Nozle


def wrap_anthropic(
    client: Any,
    nozle: Nozle,
    *,
    customer_id: str,
    metric_code: str = "llm_tokens",
    feature: Optional[str] = None,
    cost_meter_code: Optional[str] = None,
) -> Any:
    """Wrap synchronous or asynchronous Anthropic messages with safe tracking."""

    def wrapped_create(original: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        requested_model = call_parameter(args, kwargs, "model", "")
        is_stream = bool(call_parameter(args, kwargs, "stream", False))
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
) -> Iterator[Any]:
    usage: dict[str, Any] = {}
    response_model = model
    request_id = None
    for event in stream:
        event_type = value(event, "type", "")
        if event_type == "message_start":
            message_usage = value(value(event, "message"), "usage", None)
            usage = {
                "input_tokens": value(message_usage, "input_tokens", 0),
                "cache_read_input_tokens": value(message_usage, "cache_read_input_tokens", 0),
                "cache_creation_input_tokens": value(
                    message_usage, "cache_creation_input_tokens", 0
                ),
            }
            message = value(event, "message")
            response_model = value(message, "model", response_model)
            request_id = value(message, "id", request_id)
        elif event_type == "message_delta":
            usage["output_tokens"] = value(
                value(event, "usage"), "output_tokens", usage.get("output_tokens", 0)
            )
        yield event
    if usage:
        safe_capture_provider_usage(
            nozle,
            customer_id,
            metric_code,
            feature=feature,
            cost_meter_code=cost_meter_code,
            provider="anthropic",
            model=response_model,
            request_id=request_id,
            usage=normalize_anthropic_usage(usage),
            latency_ms=int((time.monotonic() - started) * 1000),
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
) -> AsyncIterator[Any]:
    usage: dict[str, Any] = {}
    response_model = model
    request_id = None
    async for event in stream:
        event_type = value(event, "type", "")
        if event_type == "message_start":
            message_usage = value(value(event, "message"), "usage", None)
            usage = {
                "input_tokens": value(message_usage, "input_tokens", 0),
                "cache_read_input_tokens": value(message_usage, "cache_read_input_tokens", 0),
                "cache_creation_input_tokens": value(
                    message_usage, "cache_creation_input_tokens", 0
                ),
            }
            message = value(event, "message")
            response_model = value(message, "model", response_model)
            request_id = value(message, "id", request_id)
        elif event_type == "message_delta":
            usage["output_tokens"] = value(
                value(event, "usage"), "output_tokens", usage.get("output_tokens", 0)
            )
        yield event
    if usage:
        await safe_capture_provider_usage_async(
            nozle,
            customer_id,
            metric_code,
            feature=feature,
            cost_meter_code=cost_meter_code,
            provider="anthropic",
            model=response_model,
            request_id=request_id,
            usage=normalize_anthropic_usage(usage),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
