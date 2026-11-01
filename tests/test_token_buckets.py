from __future__ import annotations

import gc
import json
from types import SimpleNamespace
from typing import Any, AsyncIterator, Iterator
from unittest.mock import Mock

import httpx
import pytest

from nozle import wrap_anthropic, wrap_openai
from nozle.integrations._common import (
    ESTIMATE_BYTES_PER_TOKEN,
    detect_usage,
    estimate_tokens,
    normalize_anthropic_usage,
    normalize_gemini_usage,
    normalize_openai_usage,
)
from nozle.integrations.capture import capture_http_client


def test_estimate_is_ceil_of_bytes_over_four() -> None:
    assert ESTIMATE_BYTES_PER_TOKEN == 4
    assert [estimate_tokens(n) for n in (0, 1, 4, 5, 9)] == [0, 1, 1, 2, 3]


def test_openai_buckets_are_disjoint_with_audio_and_cache_write() -> None:
    usage = normalize_openai_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "prompt_tokens_details": {
                "cached_tokens": 20,
                "cache_write_tokens": 10,
                "audio_tokens": 5,
            },
            "completion_tokens_details": {"reasoning_tokens": 8, "audio_tokens": 4},
        }
    )
    assert usage.feature_input_tokens == 100
    assert usage.feature_output_tokens == 40
    assert dict(usage.categories) == {
        "input": 65,
        "cached_input": 20,
        "cache_write": 10,
        "audio_input": 5,
        "output": 28,
        "reasoning": 8,
        "audio_output": 4,
    }


def test_openai_buckets_are_clamped_to_the_totals() -> None:
    usage = normalize_openai_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "prompt_tokens_details": {
                "cached_tokens": 9,
                "cache_write_tokens": 9,
                "audio_tokens": 9,
            },
            "completion_tokens_details": {"reasoning_tokens": 99},
        }
    )
    assert dict(usage.categories) == {"cached_input": 9, "cache_write": 1, "reasoning": 3}


def test_anthropic_cache_creation_is_split_by_ttl() -> None:
    usage = normalize_anthropic_usage(
        {
            "input_tokens": 50,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 30,
            "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20},
            "output_tokens": 7,
            "service_tier": "batch",
        }
    )
    assert usage.feature_input_tokens == 100
    assert dict(usage.categories) == {
        "input": 50,
        "cached_input": 20,
        "cache_write": 10,
        "cache_write_1h": 20,
        "output": 7,
    }
    assert usage.service_tier == "batch"


def test_gemini_modality_arrays_become_buckets() -> None:
    usage = normalize_gemini_usage(
        {
            "promptTokenCount": 100,
            "cachedContentTokenCount": 30,
            "candidatesTokenCount": 50,
            "thoughtsTokenCount": 6,
            "promptTokensDetails": [
                {"modality": "TEXT", "tokenCount": 50},
                {"modality": "AUDIO", "tokenCount": 20},
                {"modality": "IMAGE", "tokenCount": 30},
            ],
            "candidatesTokensDetails": [
                {"modality": "AUDIO", "tokenCount": 12},
                {"modality": "IMAGE", "tokenCount": 8},
            ],
        }
    )
    assert usage.feature_input_tokens == 100
    assert usage.feature_output_tokens == 56
    assert dict(usage.categories) == {
        "input": 20,
        "cached_input": 30,
        "audio_input": 20,
        "image_input": 30,
        "output": 30,
        "reasoning": 6,
        "audio_output": 12,
        "image_output": 8,
    }


def test_openai_body_service_tier_marks_batch() -> None:
    found = detect_usage({"id": "x", "service_tier": "batch", "usage": {"prompt_tokens": 1}})
    assert found is not None and found.usage.service_tier == "batch"


def test_record_usage_sends_breakdowns_and_bucket_cost_events(requests_mock: Any) -> None:
    from nozle import Nozle

    client = Nozle(
        "sk_test", base_url="https://api.example/engine", events_url="https://api.example/core"
    )
    client.track = Mock(return_value="txn")  # type: ignore[method-assign]
    client.cost_events = SimpleNamespace(track=Mock())  # type: ignore[assignment]
    client.record_usage(
        "cust_1",
        model="claude",
        provider="anthropic",
        request_id="r1",
        input_tokens=100,
        cached_input_tokens=30,
        cache_write_tokens=10,
        cache_write_1h_tokens=5,
        audio_input_tokens=2,
        output_tokens=50,
        reasoning_tokens=20,
        service_tier="batch",
        cost_meter_code="ai_tokens",
    )
    metadata = client.track.call_args.kwargs["metadata"]
    assert metadata["input_tokens"] == 100 and metadata["output_tokens"] == 50
    assert metadata["cached_input_tokens"] == 30
    assert metadata["cache_write_tokens"] == 10
    assert metadata["cache_write_1h_tokens"] == 5
    assert metadata["audio_input_tokens"] == 2
    assert metadata["reasoning_tokens"] == 20
    assert metadata["service_tier"] == "batch"
    assert metadata["usage_source"] == "reported"
    assert "stream_aborted" not in metadata and "image_input_tokens" not in metadata
    calls = [c.kwargs for c in client.cost_events.track.call_args_list]
    assert {c["cost_event_id"]: c["properties"]["type"] for c in calls} == {
        f"llm-anthropic-r1:{kind}": kind
        for kind in (
            "batch_input",
            "cached_input",
            "cache_write",
            "cache_write_1h",
            "audio_input",
            "batch_output",
            "reasoning",
        )
    }
    assert all(c["operation_key"] == c["properties"]["type"] for c in calls)


def tracker() -> Any:
    return SimpleNamespace(
        events=SimpleNamespace(create_transaction_id=Mock(return_value="txn_random")),
        track=Mock(return_value="txn"),
        cost_events=SimpleNamespace(track=Mock()),
    )


def client_with_create(create: Any, provider: str) -> Any:
    resource = SimpleNamespace(create=create)
    if provider == "openai":
        return SimpleNamespace(chat=SimpleNamespace(completions=resource))
    return SimpleNamespace(messages=resource)


def only_event(nozle: Any) -> dict[str, Any]:
    nozle.track.assert_called_once()
    return nozle.track.call_args.kwargs["metadata"]


def openai_chunks() -> list[dict[str, Any]]:
    return [
        {"id": "c1", "model": "gpt", "choices": [{"delta": {"content": "héllo wor"}}]},  # 10 bytes
        {"id": "c1", "model": "gpt", "choices": [{"delta": {"content": "ld!"}}]},  # 3 bytes
        {
            "id": "c1",
            "model": "gpt",
            "choices": [],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4},
        },
    ]


def test_openai_sync_stream_break_records_an_estimate() -> None:
    nozle = tracker()
    provider = wrap_openai(
        client_with_create(lambda **_: iter(openai_chunks()), "openai"), nozle, customer_id="c"
    )
    for _ in provider.chat.completions.create(
        model="gpt", stream=True, messages=[{"role": "user", "content": "twelve bytes"}]
    ):
        break
    gc.collect()
    event = only_event(nozle)
    assert event["stream_aborted"] is True
    assert event["usage_source"] == "estimated"
    assert event["input_tokens"] == 3  # ceil(12 / 4)
    assert event["output_tokens"] == 3  # ceil(10 / 4): the first chunk was delivered


def test_openai_sync_stream_break_after_text_estimates_output_from_delivered_bytes() -> None:
    nozle = tracker()
    provider = wrap_openai(
        client_with_create(lambda **_: iter(openai_chunks()), "openai"), nozle, customer_id="c"
    )
    stream = provider.chat.completions.create(
        model="gpt", stream=True, messages=[{"role": "user", "content": "twelve bytes"}]
    )
    for index, _ in enumerate(stream):
        if index == 1:
            break
    stream.close()
    event = only_event(nozle)
    assert (event["input_tokens"], event["output_tokens"]) == (3, 4)  # ceil(12/4), ceil(13/4)
    assert event["usage_source"] == "estimated" and event["stream_aborted"] is True


def test_openai_sync_stream_that_raises_is_recorded_and_the_error_propagates() -> None:
    def boom() -> Iterator[Any]:
        yield openai_chunks()[0]
        raise RuntimeError("connection reset")

    nozle = tracker()
    provider = wrap_openai(client_with_create(lambda **_: boom(), "openai"), nozle, customer_id="c")
    with pytest.raises(RuntimeError):
        list(provider.chat.completions.create(model="gpt", stream=True))
    event = only_event(nozle)
    assert event["stream_aborted"] is True and event["output_tokens"] == 3  # ceil(10/4)


def test_openai_normal_stream_has_no_abort_marker() -> None:
    nozle = tracker()
    provider = wrap_openai(
        client_with_create(lambda **_: iter(openai_chunks()), "openai"), nozle, customer_id="c"
    )
    list(provider.chat.completions.create(model="gpt", stream=True))
    event = only_event(nozle)
    assert event["usage_source"] == "reported" and "stream_aborted" not in event
    assert (event["input_tokens"], event["output_tokens"]) == (9, 4)


def anthropic_events() -> list[dict[str, Any]]:
    return [
        {
            "type": "message_start",
            "message": {
                "id": "m1",
                "model": "claude",
                "usage": {"input_tokens": 25, "output_tokens": 1},
            },
        },
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "a" * 40}},
        {"type": "message_delta", "usage": {"output_tokens": 15}},
        {"type": "message_stop"},
    ]


def test_anthropic_sync_stream_break_uses_the_larger_of_reported_and_estimate() -> None:
    nozle = tracker()
    provider = wrap_anthropic(
        client_with_create(lambda **_: iter(anthropic_events()), "anthropic"),
        nozle,
        customer_id="c",
    )
    for index, _ in enumerate(provider.messages.create(model="claude", stream=True)):
        if index == 1:
            break
    gc.collect()
    event = only_event(nozle)
    assert (event["input_tokens"], event["output_tokens"]) == (25, 10)  # max(1, ceil(40/4))
    assert event["usage_source"] == "estimated" and event["stream_aborted"] is True


def test_anthropic_break_with_reported_output_above_estimate_stays_reported() -> None:
    events = anthropic_events()
    events[1] = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ab"}}
    nozle = tracker()
    provider = wrap_anthropic(
        client_with_create(lambda **_: iter(events), "anthropic"), nozle, customer_id="c"
    )
    for index, _ in enumerate(provider.messages.create(model="claude", stream=True)):
        if index == 2:
            break
    gc.collect()
    event = only_event(nozle)
    assert event["output_tokens"] == 15 and event["usage_source"] == "reported"
    assert event["stream_aborted"] is True


@pytest.mark.asyncio
async def test_async_streams_record_when_the_consumer_closes_early() -> None:
    async def anthropic_stream() -> AsyncIterator[Any]:
        for event in anthropic_events():
            yield event

    async def openai_stream() -> AsyncIterator[Any]:
        for chunk in openai_chunks():
            yield chunk

    async def create_anthropic(**_: Any) -> Any:
        return anthropic_stream()

    async def create_openai(**_: Any) -> Any:
        return openai_stream()

    a_nozle = tracker()
    a_client = wrap_anthropic(
        client_with_create(create_anthropic, "anthropic"), a_nozle, customer_id="c"
    )
    stream = await a_client.messages.create(model="claude", stream=True)
    async for index, _ in _enumerate(stream):
        if index == 1:
            break
    await stream.aclose()
    a_event = only_event(a_nozle)
    assert a_event["output_tokens"] == 10 and a_event["stream_aborted"] is True

    o_nozle = tracker()
    o_client = wrap_openai(client_with_create(create_openai, "openai"), o_nozle, customer_id="c")
    stream = await o_client.chat.completions.create(
        model="gpt", stream=True, messages=[{"role": "user", "content": "twelve bytes"}]
    )
    async for _ in stream:
        break
    await stream.aclose()
    o_event = only_event(o_nozle)
    assert o_event["usage_source"] == "estimated" and o_event["stream_aborted"] is True
    assert o_event["input_tokens"] == 3


async def _enumerate(stream: Any) -> AsyncIterator[tuple[int, Any]]:
    index = 0
    async for item in stream:
        yield index, item
        index += 1


def sse(events: list[Any]) -> bytes:
    return "".join(
        f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n" for event in events
    ).encode()


def wait_for_track(nozle: Any) -> None:
    import time

    for _ in range(200):
        if nozle.track.called:
            return
        time.sleep(0.01)
    raise AssertionError("usage event was not recorded")


def test_http_transport_stream_closed_early_is_recorded_as_aborted() -> None:
    nozle = tracker()
    body = sse(
        [
            {"id": "c9", "model": "m", "choices": [{"delta": {"content": "abcdefgh"}}]},
            {
                "id": "c9",
                "model": "m",
                "choices": [],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2},
            },
            "[DONE]",
        ]
    )

    def chunks() -> Iterator[bytes]:
        yield from (part + b"\n\n" for part in body.split(b"\n\n") if part)

    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200, content=chunks(), headers={"content-type": "text/event-stream"}
        )
    )
    client = capture_http_client(nozle, customer_id="cust_1", transport=transport)
    with client.stream(
        "POST",
        "https://api.openai.com/v1/chat/completions",
        json={
            "model": "m",
            "messages": [{"role": "user", "content": "0123456789"}],
            "stream": True,
        },
    ) as response:
        for line in response.iter_lines():
            if "abcdefgh" in line:
                break
    wait_for_track(nozle)
    event = nozle.track.call_args.kwargs["metadata"]
    assert event["stream_aborted"] is True and event["usage_source"] == "estimated"
    assert (event["input_tokens"], event["output_tokens"]) == (3, 2)  # ceil(10/4), ceil(8/4)


def test_http_transport_stream_without_final_marker_is_aborted_and_reported_when_partial() -> None:
    nozle = tracker()
    body = sse(
        [
            {
                "type": "message_start",
                "message": {
                    "id": "m2",
                    "model": "claude",
                    "usage": {"input_tokens": 30, "output_tokens": 1},
                },
            },
            {"type": "message_delta", "usage": {"output_tokens": 6}},
        ]
    )
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
    )
    client = capture_http_client(nozle, customer_id="cust_1", transport=transport)
    with client.stream("POST", "https://api.anthropic.com/v1/messages", json={"stream": True}) as r:
        r.read()
    wait_for_track(nozle)
    event = nozle.track.call_args.kwargs["metadata"]
    assert event["stream_aborted"] is True and event["usage_source"] == "reported"
    assert (event["input_tokens"], event["output_tokens"]) == (30, 6)
