"""track_batch: chunking, per-event results, retries by transaction ID, sync and async."""

from __future__ import annotations

import asyncio
import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

import httpx
import pytest
import requests_mock

from nozle import (
    AsyncNozle,
    Nozle,
    NozleAPIError,
    NozleValidationError,
    TrackBatchEvent,
)

ENGINE = "https://api.example/engine"
BATCH = f"{ENGINE}/api/v1/events/batch"


def events(count: int) -> List[TrackBatchEvent]:
    return [
        {
            "customer_id": "c1",
            "event": "api_call",
            "metadata": {"i": i},
            "transaction_id": f"tx-{i}",
        }
        for i in range(count)
    ]


def client(**options: Any) -> Nozle:
    return Nozle(
        "sk_test", base_url=ENGINE, events_url="https://api.example/core", retry_delay=0, **options
    )


def sent(request: Any) -> List[Dict[str, Any]]:
    """The events of a gzip batch request."""
    assert request.headers["Content-Encoding"] == "gzip"
    events: List[Dict[str, Any]] = json.loads(gzip.decompress(request.body))["events"]
    return events


def sent_ids(request: Any) -> List[str]:
    return [e["transaction_id"] for e in sent(request)]


def rejecting(position: Dict[str, Any]) -> Dict[str, Any]:
    return {"status_code": 422, "json": {"code": "validation_errors", "error_details": position}}


def test_empty_list_sends_nothing(requests_mock: requests_mock.Mocker) -> None:
    result = client().track_batch([])
    assert (result.total, result.accepted, result.rejected, result.failed, result.results) == (
        0,
        0,
        0,
        0,
        [],
    )
    assert requests_mock.call_count == 0


@pytest.mark.parametrize(
    "count,sizes", [(1, [1]), (5000, [5000]), (5001, [5000, 1]), (12000, [5000, 5000, 2000])]
)
def test_chunks_at_the_boundaries(
    requests_mock: requests_mock.Mocker, count: int, sizes: List[int]
) -> None:
    requests_mock.post(BATCH, json={"events": []})
    result = client().track_batch(events(count))
    assert sorted((len(sent(r)) for r in requests_mock.request_history), reverse=True) == sizes
    assert (result.total, result.accepted, result.rejected, result.failed) == (count, count, 0, 0)
    assert [r.index for r in result.results] == list(range(count))


def test_event_body_matches_track_and_generated_ids_are_returned(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(BATCH, json={"events": []})
    result = client().track_batch(
        [
            {
                "customer_id": "c1",
                "event": "a",
                "subscription_id": "s1",
                "timestamp": "2026-01-02T03:04:05Z",
                "metadata": {"n": 1},
            },
            {"customer_id": "c2", "event": "b"},
        ]
    )
    first, second = sent(requests_mock.last_request)
    assert first == {
        "transaction_id": first["transaction_id"],
        "external_customer_id": "c1",
        "code": "a",
        "external_subscription_id": "s1",
        "timestamp": "1767323045.000",
        "properties": {"n": 1},
    }
    assert "timestamp" not in second and "external_subscription_id" not in second
    assert [r.transaction_id for r in result.results] == sent_ids(requests_mock.last_request)
    assert first["transaction_id"] != second["transaction_id"]
    assert requests_mock.last_request.headers["Authorization"] == "Bearer sk_test"


def test_chunk_size_and_concurrency_are_bounded(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(BATCH, json={"events": []})
    client().track_batch(events(100), chunk_size=40)
    assert requests_mock.call_count == 3
    for options in (
        {"chunk_size": 0},
        {"chunk_size": 5001},
        {"chunk_size": True},
        {"concurrency": 0},
    ):
        with pytest.raises(NozleValidationError):
            client().track_batch(events(1), **options)


def test_never_more_requests_in_flight_than_concurrency() -> None:
    lock = threading.Lock()
    state = {"active": 0, "peak": 0, "requests": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            with lock:
                state["requests"] += 1
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.05)
            with lock:
                state["active"] -= 1
            body = b'{"events": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        nozle = Nozle("sk_test", base_url=f"http://127.0.0.1:{server.server_port}")
        result = nozle.track_batch(events(10), chunk_size=1, concurrency=3)
    finally:
        server.shutdown()
    assert state["requests"] == 10 and result.accepted == 10
    assert state["peak"] == 3


def test_a_422_naming_bad_events_rejects_them_and_resends_the_rest(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        BATCH, [rejecting({"1": {"code": ["value_is_mandatory"]}}), {"json": {"events": []}}]
    )
    result = client().track_batch(events(3))
    assert sent_ids(requests_mock.request_history[0]) == ["tx-0", "tx-1", "tx-2"]
    assert sent_ids(requests_mock.request_history[1]) == ["tx-0", "tx-2"]
    assert (result.accepted, result.rejected, result.failed) == (2, 1, 0)
    assert [r.status for r in result.results] == ["accepted", "rejected", "accepted"]
    assert result.results[1].errors == {"code": ["value_is_mandatory"]}


def test_positions_are_those_of_the_whole_input(requests_mock: requests_mock.Mocker) -> None:
    def answer(request: Any, context: Any) -> Dict[str, Any]:
        ids = sent_ids(request)
        if "tx-103" in ids:
            context.status_code = 422
            return {
                "code": "validation_errors",
                "error_details": {
                    str(ids.index("tx-103")): {"transaction_id": ["value_already_exist"]}
                },
            }
        return {"events": []}

    requests_mock.post(BATCH, json=answer)
    result = client().track_batch(events(150), chunk_size=100)
    assert result.results[103].status == "rejected"
    assert result.results[103].errors == {"transaction_id": ["value_already_exist"]}
    assert (result.accepted, result.rejected, result.failed) == (149, 1, 0)


def test_a_200_listing_overdue_refusals_marks_those_rejected(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        BATCH,
        json={
            "events": [],
            "error_details": {"2": {"external_customer_id": ["customer_payment_overdue"]}},
        },
    )
    result = client().track_batch(events(3))
    assert result.results[2].status == "rejected"
    assert result.results[2].errors == {"external_customer_id": ["customer_payment_overdue"]}
    assert (result.accepted, result.rejected) == (2, 1) and requests_mock.call_count == 1


def test_a_402_refusing_every_event_rejects_them_without_a_resend(
    requests_mock: requests_mock.Mocker,
) -> None:
    refusal = {"external_customer_id": ["customer_payment_overdue"]}
    requests_mock.post(
        BATCH,
        status_code=402,
        json={"code": "customer_payment_overdue", "error_details": {"0": refusal, "1": refusal}},
    )
    result = client().track_batch(events(2))
    assert (result.accepted, result.rejected, result.failed) == (0, 2, 0)
    assert requests_mock.call_count == 1


def test_a_retried_request_carries_the_same_transaction_ids(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        BATCH, [{"status_code": 503}, {"status_code": 429}, {"json": {"events": []}}]
    )
    result = client().track_batch(
        [{"customer_id": "c1", "event": "a"}, {"customer_id": "c1", "event": "a"}]
    )
    first = sent_ids(requests_mock.request_history[0])
    assert requests_mock.call_count == 3
    assert [sent_ids(r) for r in requests_mock.request_history] == [first] * 3
    assert [r.transaction_id for r in result.results] == first and result.accepted == 2


def test_a_failed_chunk_does_not_lose_the_others(requests_mock: requests_mock.Mocker) -> None:
    def answer(request: Any, context: Any) -> Dict[str, Any]:
        if "tx-100" in sent_ids(request):
            context.status_code = 500
            return {"error": "boom"}
        return {"events": []}

    requests_mock.post(BATCH, json=answer)
    result = client(max_retries=0).track_batch(events(250), chunk_size=100)
    assert (result.total, result.accepted, result.rejected, result.failed) == (250, 150, 0, 100)
    failed = [r for r in result.results if r.status == "failed"]
    assert [r.index for r in failed] == list(range(100, 200))
    assert all(isinstance(r.error, NozleAPIError) and r.error.status_code == 500 for r in failed)


def test_a_422_without_positions_fails_the_whole_chunk(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(BATCH, **rejecting({"events": ["no_events"]}))
    result = client().track_batch(events(2))
    assert (result.rejected, result.failed) == (0, 2) and requests_mock.call_count == 1


def test_a_request_above_the_engine_cap_is_split_and_resent(
    requests_mock: requests_mock.Mocker,
) -> None:
    # An Engine capped at 100 (before 2.27.0, or LAGO_EVENTS_BATCH_MAX_LENGTH=100).
    def answer(request: Any, context: Any) -> Dict[str, Any]:
        ids = sent_ids(request)
        if len(ids) > 100:
            context.status_code = 422
            return {"code": "validation_errors", "error_details": {"events": ["too_many_events"]}}
        if "tx-300" in ids:
            return {"events": [], "error_details": {str(ids.index("tx-300")): {"code": ["x"]}}}
        return {"events": []}

    requests_mock.post(BATCH, json=answer)
    result = client().track_batch(events(400))
    sizes = [len(sent_ids(r)) for r in requests_mock.request_history]
    assert sizes == [400, 200, 100, 100, 200, 100, 100]
    assert (result.accepted, result.rejected, result.failed) == (399, 1, 0)
    assert [r.index for r in result.results] == list(range(400))
    assert result.results[300].errors == {"code": ["x"]}


def test_a_200_stores_the_valid_events_and_names_the_refused(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        BATCH,
        json={
            "events": [],
            "error_details": {
                "0": {"code": ["value_is_mandatory"]},
                "3": {"transaction_id": ["value_already_exist"]},
            },
        },
    )
    result = client().track_batch(events(5000))
    assert requests_mock.call_count == 1
    assert (result.accepted, result.rejected, result.failed) == (4998, 2, 0)
    assert result.results[0].errors == {"code": ["value_is_mandatory"]}
    assert result.results[3].errors == {"transaction_id": ["value_already_exist"]}


def test_a_422_naming_every_one_of_5000_events_is_read_in_full(
    requests_mock: requests_mock.Mocker,
) -> None:
    refused = {str(i): {"code": ["value_is_mandatory"]} for i in range(5000)}
    requests_mock.post(BATCH, **rejecting(refused))
    result = client().track_batch(events(5000))
    assert (result.rejected, result.failed) == (5000, 0) and requests_mock.call_count == 1


def test_a_transport_error_marks_the_chunk_failed(requests_mock: requests_mock.Mocker) -> None:
    import requests

    requests_mock.post(BATCH, exc=requests.ConnectionError("down"))
    result = client(max_retries=0).track_batch(events(2))
    assert result.failed == 2 and result.results[0].transaction_id == "tx-0"
    assert result.results[0].error is not None


def test_resending_stops_after_three_requests(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(BATCH, [rejecting({"0": {"code": [f"r{i}"]}}) for i in range(3)])
    result = client().track_batch(events(5))
    assert requests_mock.call_count == 3
    assert (result.rejected, result.failed) == (3, 2)


def test_invalid_input_raises_before_any_request(requests_mock: requests_mock.Mocker) -> None:
    same = {"customer_id": "c", "event": "a", "transaction_id": "same"}
    cases: List[Any] = [
        ([*events(1), {"customer_id": "", "event": "a"}], 1),
        ([{"customer_id": "c", "event": " "}], 0),
        ([{"customer_id": "c", "event": "a", "timestamp": "yesterday"}], 0),
        ([same, dict(same)], 1),
        ([None], 0),
    ]
    for batch, index in cases:
        with pytest.raises(NozleValidationError) as raised:
            client().track_batch(batch)
        assert raised.value.index == index
    with pytest.raises(NozleValidationError):
        client().track_batch("x")  # type: ignore[arg-type]
    with pytest.raises(NozleValidationError):
        Nozle("pk_test", base_url=ENGINE).track_batch(events(1))
    assert requests_mock.call_count == 0


def test_async_client_runs_the_same_contract() -> None:
    seen: List[List[str]] = []
    active = {"now": 0, "peak": 0}

    async def run() -> Any:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Content-Encoding"] == "gzip"
            ids = [
                e["transaction_id"] for e in json.loads(gzip.decompress(request.content))["events"]
            ]
            seen.append(ids)
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
            await asyncio.sleep(0.02)
            active["now"] -= 1
            if "tx-1" in ids and len(ids) > 1:
                return httpx.Response(
                    422, json={"code": "validation_errors", "error_details": {"1": {"code": ["x"]}}}
                )
            return httpx.Response(200, json={"events": []})

        async with AsyncNozle(
            "sk_test",
            base_url=ENGINE,
            _client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as nozle:
            return await nozle.track_batch(events(6), chunk_size=2, concurrency=2)

    result = asyncio.run(run())
    assert active["peak"] == 2
    assert [r.status for r in result.results] == ["accepted", "rejected"] + ["accepted"] * 4
    assert sorted(len(ids) for ids in seen) == [1, 2, 2, 2]
