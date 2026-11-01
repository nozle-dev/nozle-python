from __future__ import annotations

import asyncio
import threading
import warnings
import weakref
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional
from urllib.parse import quote

from nozle._operation import Steps, run_async, run_sync

if TYPE_CHECKING:
    from nozle.event_buffer import AnyEventBuffer


class ClientView:
    """Reads every attribute from the wrapped object, except the ones replaced on the view."""

    def __init__(self, original: Any, replaced: Dict[str, Any]) -> None:
        self.__dict__.update(replaced)
        self.__dict__["_nozle_original"] = original

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_nozle_original"], name)

    @property  # type: ignore[misc]
    def __class__(self) -> type:  # keeps isinstance(view, OpenAI) true
        return type(self.__dict__["_nozle_original"])


def with_method(client: Any, path: tuple[str, ...], replacement: Any) -> Any:
    """Return a view of ``client`` whose method at ``path`` calls ``replacement(original, ...)``.

    The client passed in is never changed (the Go SDK returns a copy), so wrapping one shared
    client per request keeps each wrapper's customer to its own calls. Wrapping a view wraps its
    original client again, so wrappers never stack.
    """
    if type(client) is ClientView:
        client = client.__dict__["_nozle_original"]

    def view(target: Any, depth: int) -> Any:
        name = path[depth]
        child = getattr(target, name)
        replaced = partial(replacement, child) if depth == len(path) - 1 else view(child, depth + 1)
        return ClientView(target, {name: replaced})

    return view(client, 0)


class NozleTrackingWarning(RuntimeWarning):
    """Usage could not be reported to Nozle: an LLM response's, or an event buffer's events."""


@dataclass(frozen=True)
class ProviderTokenUsage:
    """One call's usage. ``categories`` are the disjoint token buckets: each token is in one."""

    feature_input_tokens: int
    feature_output_tokens: int
    categories: tuple[tuple[str, int], ...]
    # "batch" when the whole call was billed at the provider's batch tier.
    service_tier: Optional[str] = None
    # "estimated" when Nozle counted the tokens itself (an aborted stream).
    usage_source: str = "reported"
    # The stream ended before the provider sent its final usage.
    stream_aborted: bool = False


# Bytes per token for the estimate of an aborted stream. The same constant and rule in every
# Nozle SDK and the Engine.
ESTIMATE_BYTES_PER_TOKEN = 4

_BREAKDOWN_PROPERTY = {
    "cached_input": "cached_input_tokens",
    "cache_write": "cache_write_tokens",
    "cache_write_1h": "cache_write_1h_tokens",
    "reasoning": "reasoning_tokens",
    "audio_input": "audio_input_tokens",
    "audio_output": "audio_output_tokens",
    "image_input": "image_input_tokens",
    "image_output": "image_output_tokens",
}


def estimate_tokens(byte_count: int) -> int:
    """``ceil(bytes / 4)``."""
    return -(-byte_count // ESTIMATE_BYTES_PER_TOKEN)


def cost_bucket(usage: ProviderTokenUsage, token_type: str) -> str:
    """The cost-event type of a bucket: a batch-tier call prices text tokens at batch rates."""
    if usage.service_tier == "batch" and token_type in ("input", "output"):
        return f"batch_{token_type}"
    return token_type


def take(total: int, *wanted: Any) -> tuple[list[int], int]:
    """Take each wanted part in order out of ``total``, so no token lands in two buckets.

    Returns the parts and what is left (the part no bucket took).
    """
    rest = total
    parts = []
    for item in wanted:
        part = min(rest, token_count(item))
        rest -= part
        parts.append(part)
    return parts, rest


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
    usage: Optional[ProviderTokenUsage] = None,
) -> Dict[str, Any]:
    properties: Dict[str, Any] = {
        "provider": provider,
        "model": model or "",
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "latency_ms": latency_ms,
    }
    if usage is not None:
        # Breakdowns of the inclusive totals, left out when 0.
        for token_type, tokens in usage.categories:
            name = _BREAKDOWN_PROPERTY.get(token_type)
            if name and tokens > 0:
                properties[name] = tokens
        if usage.service_tier == "batch":
            properties["service_tier"] = "batch"
        properties["usage_source"] = usage.usage_source
        if usage.stream_aborted:
            properties["stream_aborted"] = True
    if feature:
        properties["feature"] = feature
    return properties


def normalize_openai_usage(usage: Any, service_tier: Any = None) -> ProviderTokenUsage:
    total_input = token_count(value(usage, "prompt_tokens", value(usage, "input_tokens", 0)))
    input_details = value(
        usage,
        "prompt_tokens_details",
        value(usage, "input_tokens_details", None),
    )
    inputs, input_rest = take(
        total_input,
        value(input_details, "cached_tokens", 0),
        value(input_details, "cache_write_tokens", 0),
        value(input_details, "audio_tokens", 0),
    )
    total_output = token_count(value(usage, "completion_tokens", value(usage, "output_tokens", 0)))
    output_details = value(
        usage,
        "completion_tokens_details",
        value(usage, "output_tokens_details", None),
    )
    outputs, output_rest = take(
        total_output,
        value(output_details, "reasoning_tokens", 0),
        value(output_details, "audio_tokens", 0),
    )

    return ProviderTokenUsage(
        feature_input_tokens=total_input,
        feature_output_tokens=total_output,
        categories=compact_categories(
            (
                ("input", input_rest),
                ("cached_input", inputs[0]),
                ("cache_write", inputs[1]),
                ("audio_input", inputs[2]),
                ("output", output_rest),
                ("reasoning", outputs[0]),
                ("audio_output", outputs[1]),
            )
        ),
        service_tier="batch" if (service_tier or value(usage, "service_tier")) == "batch" else None,
    )


def normalize_anthropic_usage(usage: Any) -> ProviderTokenUsage:
    input_tokens = token_count(value(usage, "input_tokens", 0))
    cached_input = token_count(value(usage, "cache_read_input_tokens", 0))
    created = value(usage, "cache_creation", None)
    written_1h = token_count(value(created, "ephemeral_1h_input_tokens", 0))
    written_5m = token_count(value(created, "ephemeral_5m_input_tokens", 0))
    total_write = value(usage, "cache_creation_input_tokens", None)
    # The flat total wins when both are present; the 1h part never exceeds it.
    cache_write = written_1h + written_5m if total_write is None else token_count(total_write)
    cache_write_1h = min(cache_write, written_1h)
    output_tokens = token_count(value(usage, "output_tokens", 0))

    return ProviderTokenUsage(
        feature_input_tokens=input_tokens + cached_input + cache_write,
        feature_output_tokens=output_tokens,
        categories=compact_categories(
            (
                ("input", input_tokens),
                ("cached_input", cached_input),
                ("cache_write", cache_write - cache_write_1h),
                ("cache_write_1h", cache_write_1h),
                ("output", output_tokens),
            )
        ),
        service_tier="batch" if value(usage, "service_tier") == "batch" else None,
    )


def _modality_tokens(details: Any, modality: str) -> int:
    """Sum the entries of a Gemini ``*TokensDetails`` list that have this modality."""
    if not isinstance(details, (list, tuple)):
        return 0
    return sum(
        token_count(value(item, "tokenCount", 0))
        for item in details
        if value(item, "modality") == modality
    )


def normalize_gemini_usage(usage: Any) -> ProviderTokenUsage:
    prompt = token_count(value(usage, "promptTokenCount", 0))
    candidates = token_count(value(usage, "candidatesTokenCount", 0))
    reasoning = token_count(value(usage, "thoughtsTokenCount", 0))
    prompt_details = value(usage, "promptTokensDetails", None)
    candidate_details = value(usage, "candidatesTokensDetails", None)
    inputs, input_rest = take(
        prompt,
        value(usage, "cachedContentTokenCount", 0),
        _modality_tokens(prompt_details, "AUDIO"),
        _modality_tokens(prompt_details, "IMAGE"),
    )
    outputs, output_rest = take(
        candidates,
        _modality_tokens(candidate_details, "AUDIO"),
        _modality_tokens(candidate_details, "IMAGE"),
    )

    return ProviderTokenUsage(
        feature_input_tokens=prompt,
        feature_output_tokens=candidates + reasoning,
        categories=compact_categories(
            (
                ("input", input_rest),
                ("cached_input", inputs[0]),
                ("audio_input", inputs[1]),
                ("image_input", inputs[2]),
                ("output", output_rest),
                ("reasoning", reasoning),
                ("audio_output", outputs[0]),
                ("image_output", outputs[1]),
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
        return DetectedUsage(
            "openai",
            normalize_openai_usage(usage, body.get("service_tier")),
            response_id,
            model,
        )
    return None


def _items(source: Any) -> Optional[Dict[str, Any]]:
    """The fields of a usage that is a mapping, a pydantic model or a plain object."""
    if isinstance(source, Mapping):
        return dict(source)
    dump = getattr(source, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump())
        except Exception:
            return None
    if hasattr(source, "__dict__"):
        return {key: item for key, item in vars(source).items() if not key.startswith("_")}
    return None


def _text_bytes(item: Any) -> int:
    return len(item.encode("utf-8")) if isinstance(item, str) else 0


def _delta_bytes(event: Any) -> int:
    """UTF-8 bytes of the text, tool-call arguments and thinking one stream event delivers."""
    delta = value(event, "delta")
    if isinstance(delta, str):
        total = _text_bytes(delta)
    else:
        total = sum(
            _text_bytes(value(delta, name)) for name in ("text", "partial_json", "thinking")
        )
    choices = value(event, "choices")
    for choice in choices if isinstance(choices, (list, tuple)) else ():
        choice_delta = value(choice, "delta")
        total += _text_bytes(value(choice_delta, "content"))
        calls = value(choice_delta, "tool_calls")
        for call in calls if isinstance(calls, (list, tuple)) else ():
            total += _text_bytes(value(value(call, "function"), "arguments"))
    candidates = value(event, "candidates")
    for candidate in candidates if isinstance(candidates, (list, tuple)) else ():
        parts = value(value(candidate, "content"), "parts")
        for part in parts if isinstance(parts, (list, tuple)) else ():
            total += _text_bytes(value(part, "text"))
    return total


def request_text_bytes(body: Any, depth: int = 0) -> int:
    """UTF-8 bytes of the message text in a request: chat ``messages``, Anthropic ``system``,
    Responses ``input``, Gemini ``contents``. Pass a mapping of those fields."""
    if isinstance(body, str):
        return _text_bytes(body)
    if depth > 6 or body is None:
        return 0
    if isinstance(body, (list, tuple)):
        return sum(request_text_bytes(item, depth + 1) for item in body)
    if depth == 0:
        names: tuple[str, ...] = ("messages", "system", "input", "contents")
    else:
        names = ("text", "content", "parts", "input")
    return sum(request_text_bytes(value(body, name), depth + 1) for name in names)


def call_text_bytes(args: tuple[Any, ...], kwargs: Dict[str, Any]) -> int:
    """``request_text_bytes`` of the arguments of one SDK call."""
    return request_text_bytes(
        {
            name: call_parameter(args, kwargs, name, None)
            for name in ("messages", "system", "input", "contents")
        }
    )


class StreamUsageCollector:
    """Fold the events of a server-sent stream into the final usage.

    OpenAI sends usage in the last chunk, the Responses API in ``response.completed``,
    Anthropic in ``message_start`` and then a cumulative ``message_delta``, and Gemini in every
    chunk with the totals last. It also counts the delivered delta text, for the estimate of a
    stream that ended early. Events may be mappings or SDK objects.
    """

    def __init__(self) -> None:
        self._body: Dict[str, Any] = {}
        self._delta_bytes = 0
        self._finished = False

    def add(self, event: Any) -> None:
        if event is None or isinstance(event, (str, bytes, int, float)):
            return
        message = value(event, "message")
        source = value(event, "response") or message or event
        for key in ("id", "responseId", "model", "modelVersion", "service_tier"):
            item = value(source, key)
            if item is not None:
                self._body[key] = item
        if message is not None:
            self._body["type"] = "message"
        fields = _items(value(source, "usage") or value(event, "usage"))
        if fields is not None:
            merged = dict(self._body.get("usage") or {})
            merged.update({key: item for key, item in fields.items() if item is not None})
            self._body["usage"] = merged
        metadata = _items(value(source, "usageMetadata"))
        if metadata is not None:
            self._body["usageMetadata"] = metadata
        self._delta_bytes += _delta_bytes(event)
        candidates = value(event, "candidates")
        # The provider's final usage marker: the OpenAI usage chunk, response.completed,
        # message_stop, Gemini's finishReason chunk.
        if (
            value(event, "type") in ("message_stop", "response.completed", "response.incomplete")
            or (value(event, "usage") and value(event, "choices") == [])
            or (
                isinstance(candidates, (list, tuple))
                and any(value(item, "finishReason") for item in candidates)
            )
        ):
            self._finished = True

    def mark_finished(self) -> None:
        """Call when the stream's own end marker (``[DONE]``) arrives."""
        self._finished = True

    @property
    def complete(self) -> bool:
        return self._finished

    @property
    def delivered_bytes(self) -> int:
        """UTF-8 bytes of the delta text delivered so far."""
        return self._delta_bytes

    def result(self, ended: bool = True, input_bytes: int = 0) -> Optional[DetectedUsage]:
        """The usage of the stream.

        ``ended`` is False when the consumer stopped early or the connection dropped: the usage
        is then the provider's partial report, or an estimate when it sent none.
        ``input_bytes`` is the UTF-8 size of the request message text, for the input estimate.
        """
        found = detect_usage(self._body)
        if ended or self._finished:
            return found
        estimated_output = estimate_tokens(self._delta_bytes)
        if found is not None:
            usage = found.usage
            reported = usage.feature_output_tokens
            # Anthropic's message_start output count is a placeholder; delivered text can be longer.
            if found.format != "anthropic" or estimated_output <= reported:
                return replace(found, usage=replace(usage, stream_aborted=True))
            categories = [
                (kind, tokens + estimated_output - reported if kind == "output" else tokens)
                for kind, tokens in usage.categories
            ]
            if not any(kind == "output" for kind, _ in categories):
                categories.append(("output", estimated_output - reported))
            return replace(
                found,
                usage=replace(
                    usage,
                    feature_output_tokens=estimated_output,
                    categories=tuple(categories),
                    usage_source="estimated",
                    stream_aborted=True,
                ),
            )
        estimated_input = estimate_tokens(input_bytes)
        if estimated_input == 0 and estimated_output == 0:
            return None
        response_id = self._body.get("id") or self._body.get("responseId")
        model = self._body.get("model") or self._body.get("modelVersion")
        return DetectedUsage(
            "anthropic"
            if self._body.get("type") == "message"
            else "gemini"
            if "usageMetadata" in self._body
            else "openai",
            ProviderTokenUsage(
                feature_input_tokens=estimated_input,
                feature_output_tokens=estimated_output,
                categories=compact_categories(
                    (("input", estimated_input), ("output", estimated_output))
                ),
                usage_source="estimated",
                stream_aborted=True,
            ),
            response_id if isinstance(response_id, str) else None,
            model if isinstance(model, str) else None,
        )


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
    buffer: Optional[AnyEventBuffer] = None,
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
        buffer=buffer,
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
    buffer: Optional[AnyEventBuffer] = None,
) -> Steps[list[Exception]]:
    """``capture_provider_usage`` for ``Nozle`` and ``AsyncNozle``: each yield is one client call
    (see ``nozle._operation``). With ``buffer``, the usage event is queued there, before the
    first yield, and only cost events are sent from here."""
    errors: list[Exception] = []
    properties = tracking_properties(
        provider=provider,
        model=model,
        input_tokens=usage.feature_input_tokens,
        output_tokens=usage.feature_output_tokens,
        latency_ms=latency_ms,
        feature=feature,
        usage=usage,
    )
    if not provider:
        properties.pop("provider")
    if provider_host:
        properties["provider_host"] = provider_host

    if not request_id and not cost_meter_code:
        try:
            yield from _track(nozle, buffer, customer_id, feature_code, metadata=properties)
        except Exception as error:  # tracking must not corrupt a successful provider response
            errors.append(error)
        return errors

    # A stable event id from the provider's response id means a repeat is never billed twice.
    transaction_id = (
        f"llm-{provider}-{request_id}" if request_id else nozle.events.create_transaction_id()
    )
    occurred_at = datetime.now(timezone.utc)
    try:
        yield from _track(
            nozle,
            buffer,
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
    for category, tokens in usage.categories:
        token_type = cost_bucket(usage, category)
        try:
            yield partial(
                nozle.cost_events.track,
                cost_event_id=f"{transaction_id}:{token_type}",
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


def _track(nozle: Any, buffer: Optional[AnyEventBuffer], *args: Any, **kwargs: Any) -> Steps[None]:
    """One usage event: queued on ``buffer`` at once (no request), or one ``nozle.track`` call."""
    if buffer is not None:
        buffer.track(*args, **kwargs)
        return
    yield partial(nozle.track, *args, **kwargs)


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
    """A token count: only a number counts; anything else, a numeric string included, is 0."""
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        return 0
    try:
        count = int(raw_value)
    except (ValueError, OverflowError):  # NaN and infinity
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
