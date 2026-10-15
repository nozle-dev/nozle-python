from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from nozle.integrations._common import detect_usage
from nozle.integrations.capture import capture_async_http_client, capture_http_client


def tracker() -> SimpleNamespace:
    return SimpleNamespace(
        events=SimpleNamespace(create_transaction_id=Mock(return_value="txn_random")),
        track=Mock(return_value="txn"),
        cost_events=SimpleNamespace(track=Mock()),
    )


def wait_for(mock: Mock) -> None:
    import time

    for _ in range(200):
        if mock.called:
            return
        time.sleep(0.005)
    raise AssertionError("not called")


def sse(events: list) -> bytes:
    return "".join(
        f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events
    ).encode()


def test_detect_usage_by_shape() -> None:
    assert (
        detect_usage({"id": "c1", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).format
        == "openai"
    )
    responses = detect_usage(
        {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 4,
                "input_tokens_details": {"cached_tokens": 3},
            }
        }
    )
    assert responses.format == "openai"
    assert responses.usage.categories == (("input", 7), ("cached_input", 3), ("output", 4))
    anthropic = detect_usage(
        {
            "type": "message",
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 2},
        }
    )
    assert anthropic.format == "anthropic" and anthropic.usage.feature_input_tokens == 15
    gemini = detect_usage(
        {
            "responseId": "g1",
            "usageMetadata": {
                "promptTokenCount": 12,
                "candidatesTokenCount": 6,
                "thoughtsTokenCount": 4,
            },
        }
    )
    assert (
        gemini.format == "gemini" and gemini.id == "g1" and gemini.usage.feature_output_tokens == 10
    )
    assert detect_usage({"data": []}) is None
    assert detect_usage({"usage": {"total": 1}}) is None


def test_records_json_usage_without_changing_the_response() -> None:
    nozle = tracker()
    body = {
        "id": "chatcmpl-1",
        "model": "gpt-x",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    client = capture_http_client(
        nozle,
        customer_id="cust_1",
        feature_code="ai_calls",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    )

    response = client.post(
        "https://api.groq.com/openai/v1/chat/completions", json={"model": "gpt-x", "messages": []}
    )

    assert response.json() == body
    wait_for(nozle.track)
    customer, feature_code = nozle.track.call_args.args
    assert (customer, feature_code) == ("cust_1", "ai_calls")
    metadata = nozle.track.call_args.kwargs["metadata"]
    assert metadata["provider"] == "openai" and metadata["provider_host"] == "api.groq.com"
    assert metadata["input_tokens"] == 100 and metadata["output_tokens"] == 20
    assert nozle.track.call_args.kwargs["transaction_id"] == "llm-openai-chatcmpl-1"


def test_customer_header_wins_and_is_not_forwarded() -> None:
    nozle = tracker()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    client = capture_http_client(
        nozle, customer_id=lambda request: "fallback", transport=httpx.MockTransport(handler)
    )
    client.post(
        "https://api.anthropic.com/v1/messages",
        headers={"X-Nozle-Customer-Id": "cust_header", "X-Nozle-Feature": "chat", "x-api-key": "k"},
        json={},
    )

    assert "x-nozle-customer-id" not in seen[0].headers and "x-nozle-feature" not in seen[0].headers
    assert seen[0].headers["x-api-key"] == "k"
    wait_for(nozle.track)
    assert nozle.track.call_args.args[0] == "cust_header"
    assert nozle.track.call_args.kwargs["metadata"]["feature"] == "chat"


def test_chat_stream_asks_for_usage_and_records_the_last_chunk() -> None:
    nozle = tracker()
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        body = sse(
            [
                {
                    "id": "c2",
                    "model": "m",
                    "choices": [{"delta": {"content": "Hi"}}],
                    "usage": None,
                },
                {
                    "id": "c2",
                    "model": "m",
                    "choices": [],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 2},
                },
                "[DONE]",
            ]
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = capture_http_client(
        nozle, customer_id="cust_1", transport=httpx.MockTransport(handler)
    )
    with client.stream(
        "POST",
        "https://openrouter.ai/api/v1/chat/completions",
        json={"model": "m", "messages": [], "stream": True},
    ) as response:
        text = "".join(response.iter_text())

    assert seen[0]["stream_options"] == {"include_usage": True}
    assert '"content": "Hi"' in text
    wait_for(nozle.track)
    assert nozle.track.call_args.kwargs["metadata"]["input_tokens"] == 8
    assert nozle.track.call_args.kwargs["transaction_id"] == "llm-openai-c2"


def test_anthropic_stream_folds_start_and_delta() -> None:
    nozle = tracker()
    body = sse(
        [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_2",
                    "model": "claude",
                    "usage": {"input_tokens": 20, "output_tokens": 1},
                },
            },
            {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 15}},
            {"type": "message_stop"},
        ]
    )
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
    )
    client = capture_http_client(nozle, customer_id="cust_1", transport=transport)
    with client.stream(
        "POST", "https://api.anthropic.com/v1/messages", json={"stream": True}
    ) as response:
        response.read()

    wait_for(nozle.track)
    metadata = nozle.track.call_args.kwargs["metadata"]
    assert (
        metadata["provider"],
        metadata["model"],
        metadata["input_tokens"],
        metadata["output_tokens"],
    ) == ("anthropic", "claude", 20, 15)


def test_no_customer_no_change_and_failures_never_reach_the_caller() -> None:
    nozle = tracker()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"id": "c3", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        )

    anonymous = capture_http_client(nozle, transport=httpx.MockTransport(handler))
    anonymous.post(
        "https://api.openai.com/v1/chat/completions", json={"messages": [], "stream": True}
    )
    assert "stream_options" not in json.loads(seen[0].content)
    assert not nozle.track.called

    nozle.track.side_effect = RuntimeError("nozle down")
    errors: list[Exception] = []
    named = capture_http_client(
        nozle, customer_id="cust_1", on_error=errors.append, transport=httpx.MockTransport(handler)
    )
    assert named.post("https://api.openai.com/v1/chat/completions", json={}).status_code == 200
    wait_for(nozle.track)
    for _ in range(200):
        if errors:
            break
        import time

        time.sleep(0.005)
    assert str(errors[0]) == "nozle down"


def test_failed_provider_response_is_not_read() -> None:
    nozle = tracker()
    client = capture_http_client(
        nozle,
        customer_id="cust_1",
        transport=httpx.MockTransport(lambda r: httpx.Response(429, text="rate limited")),
    )
    response = client.post("https://api.openai.com/v1/chat/completions", json={})
    assert response.status_code == 429 and response.text == "rate limited"
    assert not nozle.track.called


@pytest.mark.asyncio
async def test_async_client_records_usage() -> None:
    nozle = tracker()
    body = {"id": "c4", "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
    client = capture_async_http_client(
        nozle,
        customer_id="cust_1",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    )
    response = await client.post("https://api.openai.com/v1/chat/completions", json={})
    assert response.json() == body
    wait_for(nozle.track)
    assert nozle.track.call_args.kwargs["transaction_id"] == "llm-openai-c4"


def test_record_usage_splits_categories_and_raises_delivery_errors(requests_mock) -> None:  # type: ignore[no-untyped-def]
    from nozle import Nozle

    client = Nozle(
        "sk_test", base_url="https://api.example/engine", events_url="https://api.example/core"
    )
    client.track = Mock(return_value="txn")  # type: ignore[method-assign]
    client.cost_events = SimpleNamespace(track=Mock())  # type: ignore[assignment]
    client.record_usage(
        "cust_1",
        model="m",
        provider="openai",
        request_id="r9",
        input_tokens=100,
        cached_input_tokens=30,
        output_tokens=50,
        reasoning_tokens=20,
        cost_meter_code="ai_tokens",
    )
    assert client.track.call_args.kwargs["transaction_id"] == "llm-openai-r9"
    categories = [
        (c.kwargs["operation_key"], c.kwargs["properties"]["tokens"])
        for c in client.cost_events.track.call_args_list
    ]
    assert categories == [("input", 70), ("cached_input", 30), ("output", 30), ("reasoning", 20)]

    client.track.side_effect = RuntimeError("down")
    with pytest.raises(RuntimeError, match="down"):
        client.record_usage("cust_1", model="m", input_tokens=1, output_tokens=1, request_id="r10")


def test_wrap_existing_client_keeps_its_module_and_settings() -> None:
    nozle = tracker()
    body = {"id": "c5", "usage": {"prompt_tokens": 2, "completion_tokens": 2}}
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)), timeout=7
    )
    from nozle.integrations.capture import wrap_http_client

    assert wrap_http_client(client, nozle, customer_id="cust_1") is client
    assert client.timeout.read == 7
    client.post("https://api.openai.com/v1/chat/completions", json={})
    wait_for(nozle.track)
    assert nozle.track.call_args.kwargs["transaction_id"] == "llm-openai-c5"


def test_stream_closed_at_done_still_records_usage() -> None:
    nozle = tracker()
    body = sse(
        [
            {"id": "c6", "choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 1}},
            "[DONE]",
            {"id": "ignored-after-done"},
        ]
    )
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
    )
    client = capture_http_client(nozle, customer_id="cust_1", transport=transport)
    with client.stream("POST", "https://api.openai.com/v1/chat/completions", json={}) as response:
        for line in response.iter_lines():
            if line.endswith("[DONE]"):
                break

    wait_for(nozle.track)
    assert nozle.track.call_args.kwargs["transaction_id"] == "llm-openai-c6"
