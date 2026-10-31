"""event_buffer: size, timer, flush and close, overflow, rejected and failed events, exit hook."""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Tuple

import pytest
import requests_mock

import nozle.event_buffer
from nozle import (
    EventBuffer,
    EventBufferError,
    Nozle,
    NozleAPIError,
    NozleTrackingWarning,
    NozleValidationError,
    TrackBatchEvent,
)

ENGINE = "https://api.example/engine"
BATCH = f"{ENGINE}/api/v1/events/batch"

Reports = List[Tuple[EventBufferError, List[TrackBatchEvent]]]


def client(**options: Any) -> Nozle:
    return Nozle(
        "sk_test",
        base_url=ENGINE,
        events_url="https://api.example/core",
        retry_delay=0,
        max_retries=0,
        **options,
    )


def buffer(reports: Reports, **options: Any) -> EventBuffer:
    options.setdefault("flush_interval", 0)
    return client().event_buffer(on_error=lambda e, events: reports.append((e, events)), **options)


def sent_ids(request: Any) -> List[str]:
    return [e["transaction_id"] for e in json.loads(gzip.decompress(request.body))["events"]]


def batches(mock: requests_mock.Mocker) -> List[List[str]]:
    return [sent_ids(r) for r in mock.request_history]


def until(condition: Callable[[], bool], timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_size_trigger_sends_a_full_batch(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(BATCH, json={"events": []})
    reports: Reports = []
    buf = buffer(reports, max_batch_size=3)
    ids = [buf.track("c1", "api_call", {"i": i}) for i in range(3)]
    until(lambda: requests_mock.call_count == 1)
    assert batches(requests_mock) == [ids]
    assert buf.close(timeout=3) and reports == []


def test_timer_trigger_sends_what_waits(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(BATCH, json={"events": []})
    reports: Reports = []
    buf = buffer(reports, flush_interval=0.05)
    first = buf.track("c1", "api_call")
    second = buf.track("c2", "api_call", transaction_id="tx-2", timestamp="1730000000")
    until(lambda: requests_mock.call_count == 1)
    assert batches(requests_mock) == [[first, "tx-2"]]
    assert second == "tx-2"
    assert buf.close(timeout=3)


def test_flush_and_close(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(BATCH, json={"events": []})
    reports: Reports = []
    buf = buffer(reports, max_batch_size=2)
    assert buf.flush(timeout=3) and requests_mock.call_count == 0  # nothing waiting: no request
    a = buf.track("c1", "api_call", subscription_id="sub_1")
    assert buf.size == 1 and requests_mock.call_count == 0
    assert buf.flush(timeout=3)
    assert batches(requests_mock) == [[a]]
    sent = json.loads(gzip.decompress(requests_mock.last_request.body))["events"][0]
    assert (sent["external_customer_id"], sent["external_subscription_id"]) == ("c1", "sub_1")
    b = buf.track("c1", "api_call")
    with buf:
        pass  # the context manager closes
    assert batches(requests_mock) == [[a], [b]]
    assert buf.size == 0 and buf.close(timeout=1) and reports == []


def test_overflow_drops_the_new_event_and_reports_it(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(BATCH, json={"events": []})
    reports: Reports = []
    buf = buffer(reports, max_queue_size=2)
    kept = [buf.track("c1", "api_call") for _ in range(2)]
    dropped = buf.track("c1", "api_call", {"n": 3})
    assert [(e.code, [x["transaction_id"] for x in ev]) for e, ev in reports] == [
        ("buffer_full", [dropped])
    ]
    assert reports[0][1][0]["metadata"] == {"n": 3}
    assert buf.close(timeout=3)
    assert batches(requests_mock) == [kept]


def test_rejected_events_are_reported_and_not_retried(
    requests_mock: requests_mock.Mocker,
) -> None:
    details = {"1": {"code": ["value_is_mandatory"]}, "2": {"timestamp": ["invalid"]}}
    requests_mock.post(BATCH, json={"code": "partial", "error_details": details})
    reports: Reports = []
    buf = buffer(reports)
    a, b, c = (buf.track("c1", code) for code in ("api_call", "bad", "worse"))
    assert buf.flush(timeout=3) and buf.flush(timeout=3)
    assert batches(requests_mock) == [[a, b, c]]
    # One report per rejected event, with its own errors.
    assert [(e.code, e.errors, [x["transaction_id"] for x in ev]) for e, ev in reports] == [
        ("rejected", {"code": ["value_is_mandatory"]}, [b]),
        ("rejected", {"timestamp": ["invalid"]}, [c]),
    ]
    assert isinstance(reports[0][0], EventBufferError)
    assert buf.close(timeout=3)


def test_events_given_up_in_one_flush_are_reported_together(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(BATCH, status_code=500, json={})
    reports: Reports = []
    buf = buffer(reports, max_batch_size=1, max_attempts=1)
    with buf._cond:  # hold the worker so both events go in the same flush
        ids = [buf.track("c1", "api_call", transaction_id=f"tx-{i}") for i in range(2)]
    assert buf.flush(timeout=3)
    assert batches(requests_mock) == [["tx-0"], ["tx-1"]]
    assert [(e.code, [x["transaction_id"] for x in ev]) for e, ev in reports] == [("failed", ids)]
    assert buf.close(timeout=3)


def test_the_queue_limit_counts_events_in_flight(requests_mock: requests_mock.Mocker) -> None:
    release = threading.Event()

    def slow(request: Any, context: Any) -> Dict[str, Any]:
        release.wait(5)
        return {"events": []}

    requests_mock.post(BATCH, json=slow)
    reports: Reports = []
    buf = buffer(reports, max_batch_size=1, max_queue_size=2)
    a = buf.track("c1", "api_call")
    until(lambda: requests_mock.call_count == 1)
    assert buf.size == 0  # waiting only
    b = buf.track("c1", "api_call")
    c = buf.track("c1", "api_call")
    assert [(e.code, ev[0]["transaction_id"]) for e, ev in reports] == [("buffer_full", c)]
    release.set()
    assert buf.close(timeout=3)
    assert batches(requests_mock) == [[a], [b]]


def test_failed_events_keep_their_id_and_are_reported_after_max_attempts(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(BATCH, status_code=503, json={"code": "unavailable"})
    reports: Reports = []
    buf = buffer(reports, max_attempts=2)
    a = buf.track("c1", "api_call")
    assert buf.flush(timeout=3)
    assert reports == [] and buf.size == 1  # queued again
    assert buf.flush(timeout=3)
    assert batches(requests_mock) == [[a], [a]]
    [(error, events)] = reports
    assert error.code == "failed" and [e["transaction_id"] for e in events] == [a]
    assert isinstance(error.__cause__, NozleAPIError) and error.__cause__.status_code == 503
    assert buf.flush(timeout=3) and requests_mock.call_count == 2 and buf.size == 0
    assert buf.close(timeout=3)


def test_a_failed_event_goes_back_before_newer_ones(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(BATCH, [{"status_code": 503, "json": {}}, {"json": {"events": []}}])
    reports: Reports = []
    buf = buffer(reports)
    a = buf.track("c1", "api_call")
    assert buf.flush(timeout=3)
    b = buf.track("c1", "api_call")
    assert buf.close(timeout=3)
    assert batches(requests_mock) == [[a], [a, b]] and reports == []


def test_close_reports_events_that_still_fail(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(BATCH, status_code=500, json={})
    reports: Reports = []
    buf = buffer(reports, max_attempts=3)
    a = buf.track("c1", "api_call")
    assert buf.close(timeout=3)
    assert batches(requests_mock) == [[a], [a], [a]]
    assert [e.code for e, _ in reports] == ["failed"]


def test_a_repeated_transaction_id_waits_for_the_next_batch(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(BATCH, json={"events": []})
    reports: Reports = []
    buf = buffer(reports)
    buf.track("c1", "api_call", transaction_id="same")
    buf.track("c1", "api_call", transaction_id="same")
    assert buf.close(timeout=3)
    assert batches(requests_mock) == [["same"], ["same"]] and reports == []


def test_track_after_close_warns_by_default(requests_mock: requests_mock.Mocker) -> None:
    buf = client().event_buffer(flush_interval=0)
    assert buf.close(timeout=3)
    with pytest.warns(NozleTrackingWarning, match="closed"):
        transaction_id = buf.track("c1", "api_call")
    assert transaction_id and buf.size == 0 and requests_mock.call_count == 0


def test_track_after_close_is_reported_as_closed() -> None:
    reports: Reports = []
    buf = buffer(reports)
    assert buf.close(timeout=3)
    buf.track("c1", "api_call")
    assert [e.code for e, _ in reports] == ["closed"]


def test_a_raising_callback_falls_back_to_the_warning() -> None:
    def broken(error: EventBufferError, events: List[TrackBatchEvent]) -> None:
        raise RuntimeError("boom")

    buf = client().event_buffer(flush_interval=0, on_error=broken)
    buf.close(timeout=3)
    with pytest.warns(NozleTrackingWarning):
        buf.track("c1", "api_call")


def test_concurrent_track_sends_every_event_once(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(BATCH, json={"events": []})
    reports: Reports = []
    buf = buffer(reports, max_batch_size=50, flush_interval=0.01)
    tracked: List[str] = []
    lock = threading.Lock()

    def work() -> None:
        ids = [buf.track("c1", "api_call") for _ in range(250)]
        with lock:
            tracked.extend(ids)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert buf.close(timeout=10)
    sent = [i for batch in batches(requests_mock) for i in batch]
    assert len(sent) == 2000 and sorted(sent) == sorted(tracked)
    assert all(len(batch) <= 50 for batch in batches(requests_mock)) and reports == []


def test_exit_hook_is_registered_and_removed_on_close(
    monkeypatch: pytest.MonkeyPatch, requests_mock: requests_mock.Mocker
) -> None:
    requests_mock.post(BATCH, json={"events": []})
    hooks: List[Callable[[], None]] = []
    monkeypatch.setattr(nozle.event_buffer.atexit, "register", hooks.append)
    monkeypatch.setattr(nozle.event_buffer.atexit, "unregister", hooks.remove)
    closed = client().event_buffer(flush_interval=0)
    assert hooks == [closed._at_exit]
    closed.close()
    assert hooks == []
    buf = client().event_buffer(flush_interval=0)
    a = buf.track("c1", "api_call")
    hooks[0]()  # what the interpreter runs at exit: flush, then refuse later events
    assert batches(requests_mock) == [[a]] and buf.close(timeout=1)


class _Collector(BaseHTTPRequestHandler):
    received: List[Dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Collector.received.extend(json.loads(gzip.decompress(body))["events"])
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"events": []}')

    def log_message(self, *args: Any) -> None:
        pass


def test_events_are_sent_when_the_process_exits() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    script = textwrap.dedent(
        f"""
        from nozle import Nozle
        nozle = Nozle("sk_test", base_url="http://127.0.0.1:{server.server_port}")
        buffer = nozle.event_buffer(flush_interval=60, max_batch_size=1000)
        for i in range(3):
            buffer.track("c1", "api_call", transaction_id=f"exit-{{i}}")
        """
    )
    try:
        run = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30)
        assert run.returncode == 0, run.stderr.decode()
        assert b"Warning" not in run.stderr, run.stderr.decode()
    finally:
        server.shutdown()
    assert [e["transaction_id"] for e in _Collector.received] == ["exit-0", "exit-1", "exit-2"]


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
        client().event_buffer(**options)


def test_invalid_events_raise_before_queueing() -> None:
    reports: Reports = []
    buf = buffer(reports)
    with pytest.raises(NozleValidationError):
        buf.track("", "api_call")
    with pytest.raises(NozleValidationError):
        buf.track("c1", "api_call", timestamp="yesterday")
    assert buf.size == 0 and buf.close(timeout=3)


def test_a_publishable_key_cannot_buffer() -> None:
    with pytest.raises(NozleValidationError):
        Nozle("pk_test").event_buffer()
