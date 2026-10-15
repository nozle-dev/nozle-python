"""Record LLM token usage from any client built on httpx (or its API-compatible fork httpx2).

``wrap_http_client`` takes the HTTP client your LLM SDK already uses, for example
``openai.DefaultHttpxClient()`` or ``anthropic.DefaultHttpxClient()``, and makes its transport
record the token usage of each successful LLM response. ``capture_http_client`` builds a new
client the same way. Requests pass through unchanged. Usage is recognised by the shape of the
response, so any OpenAI-compatible API, Anthropic and Gemini work without naming the provider.
Recording runs in a background thread and never changes or fails the provider response.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
import time
from types import ModuleType
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, Iterator, Optional, Union

from nozle.integrations._common import (
    DetectedUsage,
    StreamUsageCollector,
    capture_provider_usage,
    check_feature_once,
    detect_usage,
    warn_tracking_failure,
)

if TYPE_CHECKING:
    from nozle.client import Nozle

CustomerResolver = Union[str, Callable[[Any], Optional[str]], None]

_CUSTOMER_HEADER = "x-nozle-customer-id"
_FEATURE_HEADER = "x-nozle-feature"
_MAX_JSON_BYTES = 10 * 1024 * 1024


class _Options:
    def __init__(
        self,
        nozle: Nozle,
        customer_id: CustomerResolver,
        feature_code: str,
        feature: Optional[str],
        cost_meter_code: Optional[str],
        include_stream_usage: bool,
        on_error: Optional[Callable[[Exception], None]],
    ) -> None:
        self.nozle = nozle
        self.customer_id = customer_id
        self.feature_code = feature_code
        self.feature = feature
        self.cost_meter_code = cost_meter_code
        self.include_stream_usage = include_stream_usage
        self.on_error = on_error

    def report(self, error: Exception) -> None:
        if self.on_error is None:
            warn_tracking_failure(error)
            return
        try:
            self.on_error(error)
        except Exception:  # the provider response must not depend on the error callback
            pass


class _Call:
    """What one request needs once its response has been read."""

    def __init__(
        self,
        options: _Options,
        customer_id: str,
        feature: Optional[str],
        model: Optional[str],
        host: str,
    ) -> None:
        self.options = options
        self.customer_id = customer_id
        self.feature = feature
        self.model = model
        self.host = host
        self.started = time.monotonic()
        self._stream = False
        self._buffer = bytearray()
        self._pending = ""
        self._collector = StreamUsageCollector()
        self._finished = False
        self._done = False

    def start(self, content_type: str) -> None:
        self._stream = "text/event-stream" in content_type

    def feed(self, chunk: bytes) -> None:
        if not self._stream:
            if len(self._buffer) < _MAX_JSON_BYTES:
                self._buffer.extend(chunk)
            return
        self._pending += chunk.decode("utf-8", errors="replace")
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._line(line.rstrip("\r"))

    def finish(self, complete: bool) -> None:
        if self._finished:
            return
        self._finished = True
        # A client may close a stream at "[DONE]" or earlier; usage already seen still counts.
        # A JSON body read only in part cannot be parsed, so it is skipped.
        if not complete and not self._stream:
            return
        try:
            if self._stream:
                self._line(self._pending.strip())
                found = self._collector.result()
            else:
                found = detect_usage(json.loads(bytes(self._buffer)))
        except Exception as error:
            self.options.report(error)
            return
        if found is not None:
            threading.Thread(target=self._record, args=(found,), daemon=True).start()

    def _line(self, line: str) -> None:
        if self._done or not line.startswith("data:"):
            return
        data = line[5:].strip()
        if data == "[DONE]":
            self._done = True
            return
        if not data:
            return
        try:
            self._collector.add(json.loads(data))
        except ValueError:
            return

    def _record(self, found: DetectedUsage) -> None:
        options = self.options
        for error in capture_provider_usage(
            options.nozle,
            self.customer_id,
            options.feature_code,
            feature=self.feature,
            cost_meter_code=options.cost_meter_code,
            provider=found.format,
            provider_host=self.host,
            model=found.model or self.model,
            request_id=found.id,
            usage=found.usage,
            latency_ms=int((time.monotonic() - self.started) * 1000),
        ):
            options.report(error)
        setup_error = check_feature_once(options.nozle, options.feature_code)
        if setup_error is not None:
            options.report(setup_error)


def _prepare(http: ModuleType, options: _Options, request: Any) -> tuple[Any, Optional[_Call]]:
    headers = request.headers.copy()
    from_header = (headers.get(_CUSTOMER_HEADER) or "").strip() or None
    feature = (headers.get(_FEATURE_HEADER) or "").strip() or options.feature
    had_headers = _CUSTOMER_HEADER in headers or _FEATURE_HEADER in headers
    headers.pop(_CUSTOMER_HEADER, None)
    headers.pop(_FEATURE_HEADER, None)

    resolver = options.customer_id
    customer_id = from_header or (resolver(request) if callable(resolver) else resolver)

    content = request.content if request.method == "POST" else b""
    model: Optional[str] = None
    new_content: Optional[bytes] = None
    if content:
        try:
            body = json.loads(content)
        except ValueError:
            body = None
        if isinstance(body, dict):
            model = body.get("model") if isinstance(body.get("model"), str) else None
            # Chat streams leave usage out unless asked; the request path is the API contract.
            if (
                customer_id
                and options.include_stream_usage
                and body.get("stream") is True
                and isinstance(body.get("messages"), list)
                and request.url.path.endswith("/chat/completions")
            ):
                stream_options = dict(body.get("stream_options") or {})
                stream_options["include_usage"] = True
                new_content = json.dumps({**body, "stream_options": stream_options}).encode()

    if had_headers or new_content is not None:
        if new_content is not None:
            headers["content-length"] = str(len(new_content))
        request = http.Request(
            request.method,
            request.url,
            headers=headers,
            content=new_content if new_content is not None else content,
            extensions=request.extensions,
        )
    if not customer_id:
        return request, None
    return request, _Call(options, customer_id, feature, model, request.url.host)


_CLASSES: Dict[str, Dict[str, type]] = {}


def _classes(http: ModuleType) -> Dict[str, type]:
    """Transport and stream classes for one HTTP module (httpx or httpx2), built once each."""
    cached = _CLASSES.get(http.__name__)
    if cached is not None:
        return cached

    class ObservedStream(http.SyncByteStream):  # type: ignore[name-defined]
        def __init__(self, inner: Any, call: _Call) -> None:
            self._inner = inner
            self._call = call

        def __iter__(self) -> Iterator[bytes]:
            for chunk in self._inner:
                self._call.feed(chunk)
                yield chunk
            self._call.finish(complete=True)

        def close(self) -> None:
            self._call.finish(complete=False)
            self._inner.close()

    class AsyncObservedStream(http.AsyncByteStream):  # type: ignore[name-defined]
        def __init__(self, inner: Any, call: _Call) -> None:
            self._inner = inner
            self._call = call

        async def __aiter__(self) -> AsyncIterator[bytes]:
            async for chunk in self._inner:
                self._call.feed(chunk)
                yield chunk
            self._call.finish(complete=True)

        async def aclose(self) -> None:
            self._call.finish(complete=False)
            await self._inner.aclose()

    def observe(request: Any, response: Any, call: _Call, stream: Any) -> Any:
        if not 200 <= response.status_code < 300:
            return response
        call.start(response.headers.get("content-type", ""))
        return http.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=stream,
            extensions=response.extensions,
            request=request,
        )

    class CaptureTransport(http.BaseTransport):  # type: ignore[name-defined]
        """Records the token usage of LLM responses passing through ``inner``."""

        def __init__(self, inner: Any, options: _Options) -> None:
            self._inner = inner
            self._options = options

        def handle_request(self, request: Any) -> Any:
            call: Optional[_Call] = None
            try:
                request, call = _prepare(http, self._options, request)
            except Exception as error:
                self._options.report(error)
            response = self._inner.handle_request(request)
            if call is None:
                return response
            return observe(request, response, call, ObservedStream(response.stream, call))

        def close(self) -> None:
            self._inner.close()

    class AsyncCaptureTransport(http.AsyncBaseTransport):  # type: ignore[name-defined]
        """The async form of ``CaptureTransport``."""

        def __init__(self, inner: Any, options: _Options) -> None:
            self._inner = inner
            self._options = options

        async def handle_async_request(self, request: Any) -> Any:
            call: Optional[_Call] = None
            try:
                request, call = _prepare(http, self._options, request)
            except Exception as error:
                self._options.report(error)
            response = await self._inner.handle_async_request(request)
            if call is None:
                return response
            return observe(request, response, call, AsyncObservedStream(response.stream, call))

        async def aclose(self) -> None:
            await self._inner.aclose()

    classes: Dict[str, type] = {
        "sync": CaptureTransport,
        "async": AsyncCaptureTransport,
    }
    _CLASSES[http.__name__] = classes
    return classes


def _module(name: str) -> ModuleType:
    try:
        return importlib.import_module(name)
    except ImportError as error:
        raise ImportError(
            f"Nozle usage capture needs {name}: pip install 'nozle-sdk[httpx]'"
        ) from error


def _client_module(client: Any) -> ModuleType:
    """The httpx-compatible module a client comes from, even when an SDK subclasses its client."""
    for cls in type(client).__mro__:
        module = sys.modules.get(cls.__module__.split(".")[0])
        clients = tuple(
            getattr(module, name) for name in ("Client", "AsyncClient") if hasattr(module, name)
        )
        if module is not None and hasattr(module, "BaseTransport") and clients:
            if isinstance(client, clients):
                return module
    raise TypeError("wrap_http_client needs an httpx or httpx2 Client or AsyncClient")


def wrap_http_client(
    client: Any,
    nozle: Nozle,
    *,
    customer_id: CustomerResolver = None,
    feature_code: str = "llm_tokens",
    feature: Optional[str] = None,
    cost_meter_code: Optional[str] = None,
    include_stream_usage: bool = True,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> Any:
    """Make an existing httpx or httpx2 client (sync or async) record LLM token usage.

    Pass the client your LLM SDK expects, for example ``anthropic.DefaultHttpxClient()``; it is
    changed in place and returned. ``customer_id`` is a string, or a function called with each
    request. A request can also name its customer with the ``X-Nozle-Customer-Id`` header, which
    wins and is not sent to the provider. Without a customer the request passes through and
    nothing is recorded. Recording failures go to ``on_error``, or to a ``NozleTrackingWarning``,
    and so does a ``feature_not_found`` error, once per event code, when no Feature has the code.
    """
    http = _client_module(client)
    options = _Options(
        nozle, customer_id, feature_code, feature, cost_meter_code, include_stream_usage, on_error
    )
    kind = "async" if isinstance(client, http.AsyncClient) else "sync"
    transport_class = _classes(http)[kind]
    client._transport = transport_class(client._transport, options)
    # Proxy mounts carry their own transports; wrap them too so every route is observed.
    client._mounts = {
        pattern: transport_class(mounted, options) if mounted is not None else None
        for pattern, mounted in client._mounts.items()
    }
    return client


def capture_http_client(nozle: Nozle, *, module: str = "httpx", **options: Any) -> Any:
    """Return a new ``httpx.Client`` (or ``httpx2.Client`` with ``module="httpx2"``) that
    records LLM token usage. Takes the options of :func:`wrap_http_client`; ``transport`` sets
    the inner transport and any other keyword goes to the client."""
    http = _module(module)
    capture, client_kwargs = _split(options)
    return wrap_http_client(http.Client(**client_kwargs), nozle, **capture)


def capture_async_http_client(nozle: Nozle, *, module: str = "httpx", **options: Any) -> Any:
    """The async form of :func:`capture_http_client`, for ``AsyncOpenAI`` and the like."""
    http = _module(module)
    capture, client_kwargs = _split(options)
    return wrap_http_client(http.AsyncClient(**client_kwargs), nozle, **capture)


_CAPTURE_OPTIONS = (
    "customer_id",
    "feature_code",
    "feature",
    "cost_meter_code",
    "include_stream_usage",
    "on_error",
)


def _split(options: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    capture = {key: options.pop(key) for key in _CAPTURE_OPTIONS if key in options}
    return capture, options
