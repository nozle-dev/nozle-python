"""AsyncNozle.event_buffer: the sync buffer's triggers, limits, retries and codes on asyncio."""

from __future__ import annotations

import asyncio
import gzip
import inspect
import json
import time
from typing import Any, Awaitable, Callable, Dict, List, Tuple, Union

import httpx
import pytest

import nozle.event_buffer
from nozle import (
    AsyncEventBuffer,
    AsyncNozle,
    EventBufferError,
    Nozle,
    NozleAPIError,
    NozleTrackingWarning,
    NozleValidationError,
    TrackBatchEvent,
)

ENGINE = "https://api.example/engine"
Reports = List[Tuple[EventBufferError, List[TrackBatchEvent]]]
Reply = Union[httpx.Response, Awaitable[httpx.Response]]


class Engine:
    """Answers ``/events/batch`` with ``reply`` (default: all stored) and keeps each batch's ids."""

    def __init__(self, reply: Callable[[int], Reply] = lambda n: ok()) -> None:
        self.batches: List[List[str]] = []
        self.reply = reply

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/engine/api/v1/events/batch"
        events = json.loads(gzip.decompress(request.content))["events"]
        self.batches.append([event["transaction_id"] for event in events])
        answer = self.reply(len(self.batches))
        return await answer if inspect.isawaitable(answer) else answer


def ok() -> httpx.Response:
    return httpx.Response(200, json={"events": []})


def unavailable() -> httpx.Response:
    return httpx.Response(503, json={"code": "unavailable"})


def client(engine: Engine) -> AsyncNozle:
    return AsyncNozle(
        "sk_test",
        base_url=ENGINE,
        events_url="https://api.example/core",
        retry_delay=0,
        max_retries=0,
        _client=httpx.AsyncClient(transport=httpx.MockTransport(engine)),
    )


def buffer(engine: Engine, reports: Reports, **options: Any) -> AsyncEventBuffer:
    options.setdefault("flush_interval", 0)
    return client(engine).event_buffer(
        on_error=lambda e, events: reports.append((e, events)), **options
    )


async def until(condition: Callable[[], bool], timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.01)


def codes(reports: Reports) -> List[Tuple[str, List[str]]]:
    return [(e.code, [x["transaction_id"] for x in events]) for e, events in reports]


def test_the_options_and_defaults_match_the_sync_buffer() -> None:
    assert inspect.signature(AsyncNozle.event_buffer) == inspect.signature(
        Nozle.event_buffer
    ).replace(return_annotation="AsyncEventBuffer")


async def test_size_trigger_sends_a_full_batch() -> None:
    engine, reports = Engine(), []
    buf = buffer(engine, reports, max_batch_size=3)
    ids = [buf.track("c1", "api_call", {"i": i}) for i in range(3)]
    await until(lambda: len(engine.batches) == 1)
    assert engine.batches == [ids]
    assert await buf.close(timeout=3) and reports == []


async def test_timer_trigger_sends_what_waits() -> None:
    engine, reports = Engine(), []
    buf = buffer(engine, reports, flush_interval=0.05)
    first = buf.track("c1", "api_call")
    second = buf.track("c2", "api_call", transaction_id="tx-2", timestamp="1730000000")
    await until(lambda: len(engine.batches) == 1)
    assert engine.batches == [[first, "tx-2"]] and second == "tx-2"
    assert await buf.close(timeout=3)


async def test_flush_close_and_async_with() -> None:
    engine, reports = Engine(), []
    buf = buffer(engine, reports, max_batch_size=2)
    assert await buf.flush(timeout=3) and engine.batches == []  # nothing waiting: no request
    a = buf.track("c1", "api_call", subscription_id="sub_1")
    assert buf.size == 1 and engine.batches == []
    assert await buf.flush(timeout=3)
    assert engine.batches == [[a]]
    b = buf.track("c1", "api_call")
    async with buf:
        pass  # the context manager closes
    assert engine.batches == [[a], [b]]
    assert buf.size == 0 and await buf.close(timeout=1) and reports == []


async def test_full_queue_drops_the_new_event_and_counts_events_in_flight() -> None:
    release = asyncio.Event()

    async def slow() -> httpx.Response:
        await release.wait()
        return ok()

    engine, reports = Engine(lambda n: slow()), []
    buf = buffer(engine, reports, max_batch_size=1, max_queue_size=2)
    a = buf.track("c1", "api_call")
    await until(lambda: len(engine.batches) == 1)
    assert buf.size == 0  # waiting only
    b = buf.track("c1", "api_call")
    c = buf.track("c1", "api_call", {"n": 3})
    assert codes(reports) == [("buffer_full", [c])] and reports[0][1][0]["metadata"] == {"n": 3}
    release.set()
    assert await buf.close(timeout=3)
    assert engine.batches == [[a], [b]]


async def test_rejected_events_are_reported_once_and_not_retried() -> None:
    details = {"1": {"code": ["value_is_mandatory"]}, "2": {"timestamp": ["invalid"]}}
    partial = httpx.Response(200, json={"code": "partial", "error_details": details})
    engine, reports = Engine(lambda n: partial), []
    buf = buffer(engine, reports)
    a, b, c = (buf.track("c1", code) for code in ("api_call", "bad", "worse"))
    assert await buf.flush(timeout=3) and await buf.flush(timeout=3)
    assert engine.batches == [[a, b, c]]
    assert [(e.code, e.errors, [x["transaction_id"] for x in ev]) for e, ev in reports] == [
        ("rejected", {"code": ["value_is_mandatory"]}, [b]),
        ("rejected", {"timestamp": ["invalid"]}, [c]),
    ]
    assert await buf.close(timeout=3)


async def test_failed_events_keep_their_id_go_back_first_and_are_reported_after_max_attempts() -> (
    None
):
    engine, reports = Engine(lambda n: unavailable()), []
    buf = buffer(engine, reports, max_attempts=2)
    a = buf.track("c1", "api_call")
    assert await buf.flush(timeout=3)
    assert reports == [] and buf.size == 1  # queued again
    b = buf.track("c1", "api_call")
    assert await buf.flush(timeout=3)
    assert engine.batches == [[a], [a, b]]
    [(error, events)] = reports
    assert error.code == "failed" and [e["transaction_id"] for e in events] == [a]
    assert isinstance(error.__cause__, NozleAPIError) and error.__cause__.status_code == 503
    engine.reply = lambda n: ok()
    assert await buf.close(timeout=3)
    assert engine.batches == [[a], [a, b], [b]] and len(reports) == 1


async def test_close_keeps_sending_until_failing_events_are_given_up() -> None:
    engine, reports = Engine(lambda n: unavailable()), []
    buf = buffer(engine, reports, max_attempts=3)
    a = buf.track("c1", "api_call")
    assert await buf.close(timeout=3)
    assert engine.batches == [[a], [a], [a]] and codes(reports) == [("failed", [a])]


async def test_the_backoff_doubles_up_to_its_cap_and_flush_does_not_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nozle.event_buffer, "BACKOFF_MAX", 3.0)
    engine, reports = Engine(lambda n: unavailable()), []
    buf = buffer(engine, reports, max_attempts=10)
    buf.track("c1", "api_call")
    pauses = []
    for _ in range(4):
        assert await buf.flush(timeout=3)
        pauses.append(buf._backoff)
    assert pauses == [1.0, 2.0, 3.0, 3.0]
    engine.reply = lambda n: ok()
    assert await buf.close(timeout=3) and buf._backoff == 0 and reports == []


async def test_an_outage_pauses_size_and_timer_flushes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nozle.event_buffer, "BACKOFF_INITIAL", 0.2)
    engine = Engine(lambda n: unavailable() if n == 1 else ok())
    reports: Reports = []
    buf = buffer(engine, reports, max_batch_size=2, flush_interval=0.02)
    a, b = buf.track("c1", "api_call"), buf.track("c1", "api_call")
    await until(lambda: len(engine.batches) == 1 and buf.size == 2)  # failed, queued again
    c = buf.track("c1", "api_call")  # above max_batch_size, and timer due, during the pause
    await asyncio.sleep(0.1)
    assert len(engine.batches) == 1
    await until(lambda: len(engine.batches) == 3)  # once the 0.2 s pause is over
    assert engine.batches == [[a, b], [a, b], [c]] and reports == []
    assert await buf.close(timeout=3)


async def test_track_after_close_is_reported_as_closed_or_warns() -> None:
    engine, reports = Engine(), []
    buf = buffer(engine, reports)
    assert await buf.close(timeout=3)
    tx = buf.track("c1", "api_call")
    assert codes(reports) == [("closed", [tx])] and engine.batches == []
    plain = client(engine).event_buffer(flush_interval=0)
    assert await plain.close()
    with pytest.warns(NozleTrackingWarning, match="closed"):
        plain.track("c1", "api_call")


def test_events_tracked_outside_an_event_loop_are_sent_by_the_first_flush() -> None:
    engine, reports = Engine(), []
    buf = buffer(engine, reports, flush_interval=0.01)
    a = buf.track("c1", "api_call")  # no running loop: queued, the task starts later
    assert buf.size == 1 and engine.batches == []
    assert asyncio.run(buf.close(timeout=3))
    assert engine.batches == [[a]] and reports == []


def test_a_new_event_loop_starts_the_task_again_and_resends_a_cut_off_request() -> None:
    async def first_hangs() -> httpx.Response:
        await asyncio.sleep(30)
        return ok()

    engine = Engine(lambda n: first_hangs() if n == 1 else ok())
    reports: Reports = []
    buf = buffer(engine, reports, max_batch_size=1)

    async def call(customer_id: str) -> str:
        transaction_id = buf.track(customer_id, "api_call")
        await until(lambda: len(engine.batches) == 1)
        return transaction_id  # the loop ends here, cancelling the request in flight

    a = asyncio.run(call("c1"))
    assert asyncio.run(buf.flush(timeout=3))  # a later asyncio.run, as a serverless call does
    assert engine.batches == [[a], [a]] and reports == []
    assert asyncio.run(buf.close(timeout=3))


async def test_exit_hook_reports_unsent_events_and_close_removes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks: List[Callable[[], None]] = []
    monkeypatch.setattr(nozle.event_buffer.atexit, "register", hooks.append)
    monkeypatch.setattr(nozle.event_buffer.atexit, "unregister", hooks.remove)
    engine, reports = Engine(), []
    closed = buffer(engine, reports)
    assert hooks == [closed._at_exit]
    await closed.close()
    assert hooks == []
    buf = buffer(engine, reports, flush_interval=60)
    a = buf.track("c1", "api_call")
    hooks[0]()  # what the interpreter runs at exit, after the event loop is gone
    assert codes(reports) == [("failed", [a])] and engine.batches == []
    assert await buf.close(timeout=3)


@pytest.mark.parametrize(
    "options",
    [
        {"max_batch_size": 0},
        {"max_batch_size": 5001},
        {"max_queue_size": 0},
        {"max_attempts": 0},
        {"flush_interval": -1},
        {"flush_interval": True},
    ],
)
def test_invalid_options_raise(options: Dict[str, Any]) -> None:
    with pytest.raises(NozleValidationError):
        client(Engine()).event_buffer(**options)


async def test_invalid_events_raise_and_a_publishable_key_cannot_buffer() -> None:
    engine, reports = Engine(), []
    buf = buffer(engine, reports)
    with pytest.raises(NozleValidationError):
        buf.track("", "api_call")
    with pytest.raises(NozleValidationError):
        buf.track("c1", "api_call", timestamp="yesterday")
    assert buf.size == 0 and await buf.close(timeout=3)
    with pytest.raises(NozleValidationError):
        AsyncNozle("pk_test", _client=httpx.AsyncClient()).event_buffer()
