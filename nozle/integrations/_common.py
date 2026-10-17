from __future__ import annotations

import asyncio
import threading
import warnings
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote

from nozle._operation import Steps, run_async, run_sync


class NozleTrackingWarning(RuntimeWarning):
    """A successful LLM response could not be reported to Nozle."""


@dataclass(frozen=True)
class ProviderTokenUsage:
    feature_input_tokens: int
    feature_output_tokens: int
    categories: tuple[tuple[str, int], ...]


def value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def call_parameter(args: tuple[Any, ...], kwargs: Dict[str, Any], name: str, default: Any) -> Any:
    if name in kwargs:
        return kwargs[name]
    if args and isinstance(args[0], Mapping):
        return args[0].get(name, default)
    return default


def tracking_properties(
    *,
    provider: str,
    model: Any,
    input_tokens: Any,
    output_tokens: Any,
    latency_ms: int,
    feature: Optional[str],
) -> Dict[str, Any]:
    properties: Dict[str, Any] = {
        "provider": provider,
        "model": model or "",
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "latency_ms": latency_ms,
    }
    if feature:
        properties["feature"] = feature
    return properties


def normalize_openai_usage(usage: Any) -> ProviderTokenUsage:
    total_input = token_count(value(usage, "prompt_tokens", value(usage, "input_tokens", 0)))
    input_details = value(
        usage,
        "prompt_tokens_details",
        value(usage, "input_tokens_details", None),
    )
    cached_input = min(total_input, token_count(value(input_details, "cached_tokens", 0)))
    total_output = token_count(value(usage, "completion_tokens", value(usage, "output_tokens", 0)))
    output_details = value(
        usage,
        "completion_tokens_details",
        value(usage, "output_tokens_details", None),
    )
    reasoning = min(total_output, token_count(value(output_details, "reasoning_tokens", 0)))

    return ProviderTokenUsage(
        feature_input_tokens=total_input,
        feature_output_tokens=total_output,
        categories=compact_categories(
            (
                ("input", total_input - cached_input),
                ("cached_input", cached_input),
                ("output", total_output - reasoning),
                ("reasoning", reasoning),
            )
        ),
    )


def normalize_anthropic_usage(usage: Any) -> ProviderTokenUsage:
    input_tokens = token_count(value(usage, "input_tokens", 0))
    cached_input = token_count(value(usage, "cache_read_input_tokens", 0))
    cache_write = token_count(value(usage, "cache_creation_input_tokens", 0))
    output_tokens = token_count(value(usage, "output_tokens", 0))

    return ProviderTokenUsage(
        feature_input_tokens=input_tokens + cached_input + cache_write,
        feature_output_tokens=output_tokens,
        categories=compact_categories(
            (
                ("input", input_tokens),
                ("cached_input", cached_input),
                ("cache_write", cache_write),
                ("output", output_tokens),
            )
        ),
    )


def normalize_gemini_usage(usage: Any) -> ProviderTokenUsage:
    prompt = token_count(value(usage, "promptTokenCount", 0))
    cached_input = min(prompt, token_count(value(usage, "cachedContentTokenCount", 0)))
    output = token_count(value(usage, "candidatesTokenCount", 0))
    reasoning = token_count(value(usage, "thoughtsTokenCount", 0))

    return ProviderTokenUsage(
        feature_input_tokens=prompt,
        feature_output_tokens=output + reasoning,
        categories=compact_categories(
            (
                ("input", prompt - cached_input),
                ("cached_input", cached_input),
                ("output", output),
                ("reasoning", reasoning),
            )
        ),
    )


@dataclass(frozen=True)
class DetectedUsage:
    """Token usage found in one provider response, with the response's own id and model."""

    format: str
    usage: ProviderTokenUsage
    id: Optional[str] = None
    model: Optional[str] = None


def detect_usage(body: Any) -> Optional[DetectedUsage]:
    """Recognise token usage by the shape of a provider response, not by provider name.

    An OpenAI-style ``usage`` (``prompt_tokens``, or ``input_tokens`` from the Responses API),
    an Anthropic ``usage`` (a ``message`` object or cache token fields), or Gemini
    ``usageMetadata``. Returns None when the body carries no usage.
    """
    if not isinstance(body, Mapping):
        return None
    response_id = body.get("id") or body.get("responseId")
    model = body.get("model") or body.get("modelVersion")
    response_id = response_id if isinstance(response_id, str) else None
    model = model if isinstance(model, str) else None
    metadata = body.get("usageMetadata")
    if isinstance(metadata, Mapping):
        return DetectedUsage("gemini", normalize_gemini_usage(metadata), response_id, model)
    usage = body.get("usage")
    if not isinstance(usage, Mapping):
        return None
    if (
        body.get("type") == "message"
        or "cache_read_input_tokens" in usage
        or "cache_creation_input_tokens" in usage
    ):
        return DetectedUsage("anthropic", normalize_anthropic_usage(usage), response_id, model)
    if any(
        key in usage
        for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens")
    ):
        return DetectedUsage("openai", normalize_openai_usage(usage), response_id, model)
    return None


class StreamUsageCollector:
    """Fold the events of a server-sent stream into the final usage.

    OpenAI sends usage in the last chunk, the Responses API in ``response.completed``,
    Anthropic in ``message_start`` and then a cumulative ``message_delta``, and Gemini in every
    chunk with the totals last.
    """

    def __init__(self) -> None:
        self._body: Dict[str, Any] = {}

    def add(self, event: Any) -> None:
        if not isinstance(event, Mapping):
            return
        source = event.get("response") or event.get("message") or event
        if not isinstance(source, Mapping):
            return
        for key in ("id", "responseId", "model", "modelVersion"):
            if source.get(key) is not None:
                self._body[key] = source[key]
        if event.get("message") is not None:
            self._body["type"] = "message"
        usage = source.get("usage") or event.get("usage")
        if isinstance(usage, Mapping):
            merged = dict(self._body.get("usage") or {})
            merged.update({key: item for key, item in usage.items() if item is not None})
            self._body["usage"] = merged
        if isinstance(source.get("usageMetadata"), Mapping):
            self._body["usageMetadata"] = source["usageMetadata"]

    def result(self) -> Optional[DetectedUsage]:
        return detect_usage(self._body)


def safe_capture_provider_usage(
    nozle: Any,
    customer_id: str,
    feature_code: str,
    *,
    feature: Optional[str],
    cost_meter_code: Optional[str],
    provider: str,
    model: Any,
    request_id: Any,
    usage: ProviderTokenUsage,
    latency_ms: int,
    provider_host: Optional[str] = None,
) -> None:
    for error in capture_provider_usage(
        nozle,
        customer_id,
        feature_code,
        feature=feature,
        cost_meter_code=cost_meter_code,
        provider=provider,
        model=model,
        request_id=request_id,
        usage=usage,
        latency_ms=latency_ms,
        provider_host=provider_host,
    ):
        warn_tracking_failure(error)


def is_async_client(nozle: Any) -> bool:
    from nozle.client import AsyncNozle

    return isinstance(nozle, AsyncNozle)


def capture_provider_usage(
    nozle: Any, customer_id: str, feature_code: str, **options: Any
) -> list[Exception]:
    """Record one usage event and its cost events; return the delivery errors instead of raising."""
    if is_async_client(nozle):
        return [TypeError("AsyncNozle records usage only from async code; use Nozle here")]
    return run_sync(capture_usage_steps(nozle, customer_id, feature_code, **options))


def capture_usage_steps(
    nozle: Any,
    customer_id: str,
    feature_code: str,
    *,
    feature: Optional[str],
    cost_meter_code: Optional[str],
    provider: str,
    model: Any,
    request_id: Any,
    usage: ProviderTokenUsage,
    latency_ms: int,
    provider_host: Optional[str] = None,
) -> Steps[list[Exception]]:
    """``capture_provider_usage`` for ``Nozle`` and ``AsyncNozle``: each yield is one client call
    (see ``nozle._operation``)."""
    errors: list[Exception] = []
    properties = tracking_properties(
        provider=provider,
        model=model,
        input_tokens=usage.feature_input_tokens,
        output_tokens=usage.feature_output_tokens,
        latency_ms=latency_ms,
        feature=feature,
    )
    if not provider:
        properties.pop("provider")
    if provider_host:
        properties["provider_host"] = provider_host

    if not request_id and not cost_meter_code:
        try:
            yield partial(nozle.track, customer_id, feature_code, metadata=properties)
        except Exception as error:  # tracking must not corrupt a successful provider response
            errors.append(error)
        return errors

    # A stable event id from the provider's response id means a repeat is never billed twice.
    transaction_id = (
        f"llm-{provider}-{request_id}" if request_id else nozle.events.create_transaction_id()
    )
    occurred_at = datetime.now(timezone.utc)
    try:
        yield partial(
            nozle.track,
            customer_id,
            feature_code,
            metadata=properties,
            transaction_id=transaction_id,
            timestamp=occurred_at.isoformat(),
        )
    except Exception as error:  # tracking must not corrupt a successful provider response
        errors.append(error)
    if not cost_meter_code:
        return errors

    resolved_model = str(model or "")
    resolved_request_id = str(request_id) if request_id else None
    for token_type, tokens in usage.categories:
        try:
            yield partial(
                nozle.cost_events.track,
                cost_meter_code=cost_meter_code,
                parent_transaction_id=transaction_id,
                request_id=resolved_request_id,
                operation_key=token_type,
                properties={
                    "tokens": tokens,
                    "provider": provider,
                    "model": resolved_model,
                    "type": token_type,
                },
                timestamp=occurred_at.timestamp(),
            )
        except Exception as error:  # tracking must not corrupt a successful provider response
            errors.append(error)
    return errors


async def safe_capture_provider_usage_async(
    nozle: Any,
    customer_id: str,
    feature_code: str,
    **kwargs: Any,
) -> None:
    if is_async_client(nozle):
        steps = capture_usage_steps(nozle, customer_id, feature_code, **kwargs)
        for error in await run_async(steps):
            warn_tracking_failure(error)
        return
    await asyncio.to_thread(
        safe_capture_provider_usage,
        nozle,
        customer_id,
        feature_code,
        **kwargs,
    )


def safe_track(
    nozle: Any,
    customer_id: str,
    metric_code: str,
    properties: Dict[str, Any],
) -> None:
    try:
        nozle.track(customer_id, metric_code, metadata=properties)
    except Exception as error:  # tracking must not corrupt a successful provider response
        warn_tracking_failure(error)


async def safe_track_async(
    nozle: Any,
    customer_id: str,
    metric_code: str,
    properties: Dict[str, Any],
) -> None:
    if not is_async_client(nozle):
        await asyncio.to_thread(safe_track, nozle, customer_id, metric_code, properties)
        return
    try:
        await nozle.track(customer_id, metric_code, metadata=properties)
    except Exception as error:  # tracking must not corrupt a successful provider response
        warn_tracking_failure(error)


def token_count(raw_value: Any) -> int:
    try:
        count = int(raw_value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(count, 0)


def compact_categories(categories: tuple[tuple[str, int], ...]) -> tuple[tuple[str, int], ...]:
    return tuple((token_type, tokens) for token_type, tokens in categories if tokens > 0)


def warn_tracking_failure(error: Exception) -> None:
    try:
        warnings.warn(
            (f"Nozle tracking failed after a successful LLM response ({type(error).__name__})"),
            NozleTrackingWarning,
            stacklevel=3,
        )
    except NozleTrackingWarning:
        # Warning filters may promote warnings to exceptions; tracking still
        # must not replace a successful provider response.
        pass


_checked_features: "weakref.WeakKeyDictionary[Any, set[str]]" = weakref.WeakKeyDictionary()
_checked_lock = threading.Lock()


def check_feature_once(nozle: Any, feature_code: str) -> Optional[Exception]:
    """Return an error once per client and event code when no Feature has that code.

    The usage is then stored but never billed. Reads Core's ``GET /features/{code}``; any other
    answer, including a key without read permission, returns None.
    """
    return run_sync(check_feature_steps(nozle, feature_code))


def check_feature_steps(nozle: Any, feature_code: str) -> Steps[Optional[Exception]]:
    """``check_feature_once`` for ``Nozle`` and ``AsyncNozle`` (see ``nozle._operation``)."""
    transport = getattr(nozle, "_events", None)
    if transport is None:
        return None
    try:
        with _checked_lock:
            seen = _checked_features.setdefault(nozle, set())
            if feature_code in seen:
                return None
            seen.add(feature_code)
    except TypeError:  # an object that cannot be weakly referenced is not a real client
        return None
    from nozle.errors import NozleAPIError

    try:
        yield partial(
            transport.request,
            "feature lookup",
            "GET",
            f"/api/v1/features/{quote(feature_code, safe='')}",
        )
    except NozleAPIError as error:
        if error.status_code == 404:
            setup_error = LookupError(
                f'No Feature has the code "{feature_code}", so this usage is stored but not '
                "billed. Create a metered Feature with that code: "
                "https://docs.nozle.dev/guides/ai/token-billing"
            )
            setup_error.code = "feature_not_found"  # type: ignore[attr-defined]
            return setup_error
    except Exception:  # a failed check never affects recording
        return None
    return None
