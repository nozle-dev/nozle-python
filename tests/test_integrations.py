from __future__ import annotations

import asyncio
import warnings
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import Mock

import pytest

from nozle import NozleTrackingWarning, wrap_anthropic, wrap_openai
from nozle.integrations._common import normalize_anthropic_usage, normalize_openai_usage


def client_with_create(create: Any, provider: str) -> Any:
    resource = SimpleNamespace(create=create)
    if provider == "openai":
        return SimpleNamespace(chat=SimpleNamespace(completions=resource))
    return SimpleNamespace(messages=resource)


def assert_tracking_contract(
    track: Mock, provider: str, model: str, input_tokens: int, output_tokens: int
) -> None:
    track.assert_called_once()
    customer_id, metric_code = track.call_args.args
    properties = track.call_args.kwargs["metadata"]
    assert customer_id == "cust_1"
    assert metric_code == "llm_tokens"
    assert properties == {
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": properties["latency_ms"],
        "feature": "assistant",
    }
    assert isinstance(properties["latency_ms"], int)
    assert properties["latency_ms"] >= 0


def test_openai_sync_response_tracks_node_event_properties() -> None:
    response = SimpleNamespace(
        model="gpt-5",
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )
    provider = client_with_create(lambda **_: response, "openai")
    tracker = SimpleNamespace(track=Mock())
    provider = wrap_openai(provider, tracker, customer_id="cust_1", feature="assistant")

    returned = provider.chat.completions.create(model="gpt-5")

    assert returned is response
    assert_tracking_contract(tracker.track, "openai", "gpt-5", 11, 7)


def test_openai_sync_stream_tracks_usage_from_final_chunk() -> None:
    chunks = [
        SimpleNamespace(usage=None),
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=13, completion_tokens=9)),
    ]
    create = Mock(side_effect=lambda **_: iter(chunks))
    provider = client_with_create(create, "openai")
    tracker = SimpleNamespace(track=Mock())
    provider = wrap_openai(provider, tracker, customer_id="cust_1", feature="assistant")

    returned = list(provider.chat.completions.create(model="gpt-stream", stream=True))

    assert returned == chunks
    assert_tracking_contract(tracker.track, "openai", "gpt-stream", 13, 9)
    assert create.call_args.kwargs["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_openai_async_response_tracks_without_corrupting_result() -> None:
    response = SimpleNamespace(
        model=None,
        usage=SimpleNamespace(prompt_tokens=17, completion_tokens=5),
    )

    async def create(**_: Any) -> Any:
        return response

    provider = client_with_create(create, "openai")
    tracker = SimpleNamespace(track=Mock())
    provider = wrap_openai(provider, tracker, customer_id="cust_1", feature="assistant")

    returned = await provider.chat.completions.create(model="gpt-async")

    assert returned is response
    assert_tracking_contract(tracker.track, "openai", "gpt-async", 17, 5)


@pytest.mark.asyncio
async def test_openai_async_stream_tracks_final_usage() -> None:
    chunks = [
        SimpleNamespace(usage=None),
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=19, completion_tokens=8)),
    ]

    async def stream() -> AsyncIterator[Any]:
        for chunk in chunks:
            yield chunk

    async def create(**_: Any) -> Any:
        return stream()

    provider = client_with_create(create, "openai")
    tracker = SimpleNamespace(track=Mock())
    provider = wrap_openai(provider, tracker, customer_id="cust_1", feature="assistant")

    returned_stream = await provider.chat.completions.create(model="gpt-stream", stream=True)
    returned = [chunk async for chunk in returned_stream]

    assert returned == chunks
    assert_tracking_contract(tracker.track, "openai", "gpt-stream", 19, 8)


def test_anthropic_sync_response_tracks_node_event_properties() -> None:
    response = SimpleNamespace(
        model="claude-sonnet",
        usage=SimpleNamespace(input_tokens=23, output_tokens=12),
    )
    provider = client_with_create(lambda **_: response, "anthropic")
    tracker = SimpleNamespace(track=Mock())
    provider = wrap_anthropic(provider, tracker, customer_id="cust_1", feature="assistant")

    returned = provider.messages.create(model="claude-sonnet")

    assert returned is response
    assert_tracking_contract(tracker.track, "anthropic", "claude-sonnet", 23, 12)


def test_anthropic_sync_stream_tracks_start_and_delta_usage() -> None:
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage=SimpleNamespace(input_tokens=29)),
        ),
        SimpleNamespace(type="message_delta", usage=SimpleNamespace(output_tokens=14)),
    ]
    provider = client_with_create(lambda **_: iter(events), "anthropic")
    tracker = SimpleNamespace(track=Mock())
    provider = wrap_anthropic(provider, tracker, customer_id="cust_1", feature="assistant")

    returned = list(provider.messages.create(model="claude-stream", stream=True))

    assert returned == events
    assert_tracking_contract(tracker.track, "anthropic", "claude-stream", 29, 14)


@pytest.mark.asyncio
async def test_anthropic_async_response_and_stream_are_supported() -> None:
    response = SimpleNamespace(
        model="claude-async",
        usage=SimpleNamespace(input_tokens=31, output_tokens=16),
    )

    async def create_response(**_: Any) -> Any:
        return response

    response_provider = client_with_create(create_response, "anthropic")
    response_tracker = SimpleNamespace(track=Mock())
    response_provider = wrap_anthropic(
        response_provider,
        response_tracker,
        customer_id="cust_1",
        feature="assistant",
    )
    returned = await response_provider.messages.create(model="claude-async")
    assert returned is response
    assert_tracking_contract(response_tracker.track, "anthropic", "claude-async", 31, 16)

    events = [
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 37}},
        },
        {"type": "message_delta", "usage": {"output_tokens": 18}},
    ]

    async def stream() -> AsyncIterator[Any]:
        for event in events:
            yield event

    async def create_stream(**_: Any) -> Any:
        return stream()

    stream_provider = client_with_create(create_stream, "anthropic")
    stream_tracker = SimpleNamespace(track=Mock())
    stream_provider = wrap_anthropic(
        stream_provider,
        stream_tracker,
        customer_id="cust_1",
        feature="assistant",
    )
    returned_stream = await stream_provider.messages.create(model="claude-stream", stream=True)
    assert [event async for event in returned_stream] == events
    assert_tracking_contract(stream_tracker.track, "anthropic", "claude-stream", 37, 18)


def test_tracking_failure_warns_without_changing_successful_llm_response() -> None:
    response = SimpleNamespace(
        model="gpt-5",
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
    provider = client_with_create(lambda **_: response, "openai")
    tracker = SimpleNamespace(track=Mock(side_effect=RuntimeError("sk_must_not_leak")))
    provider = wrap_openai(provider, tracker, customer_id="cust_1")

    with pytest.warns(NozleTrackingWarning) as warnings:
        returned = provider.chat.completions.create(model="gpt-5")

    assert returned is response
    assert "sk_must_not_leak" not in str(warnings[0].message)


def test_tracking_failure_cannot_replace_success_when_warnings_are_errors() -> None:
    response = SimpleNamespace(
        model="gpt-5",
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
    provider = client_with_create(lambda **_: response, "openai")
    tracker = SimpleNamespace(track=Mock(side_effect=RuntimeError("tracking unavailable")))
    provider = wrap_openai(provider, tracker, customer_id="cust_1")

    with warnings.catch_warnings():
        warnings.simplefilter("error", NozleTrackingWarning)
        returned = provider.chat.completions.create(model="gpt-5")

    assert returned is response


def test_provider_failure_is_propagated_without_tracking() -> None:
    def create(**_: Any) -> Any:
        raise RuntimeError("provider unavailable")

    provider = client_with_create(create, "anthropic")
    tracker = SimpleNamespace(track=Mock())
    provider = wrap_anthropic(provider, tracker, customer_id="cust_1")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        provider.messages.create(model="claude")
    tracker.track.assert_not_called()


def cost_tracker() -> Any:
    return SimpleNamespace(
        events=SimpleNamespace(create_transaction_id=Mock(return_value="txn_123")),
        track=Mock(return_value="txn_123"),
        cost_events=SimpleNamespace(track=Mock(return_value={"status": "accepted"})),
    )


def test_openai_usage_normalizer_splits_cached_and_reasoning_tokens() -> None:
    usage = normalize_openai_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "prompt_tokens_details": {"cached_tokens": 25},
            "completion_tokens_details": {"reasoning_tokens": 10},
        }
    )

    assert usage.feature_input_tokens == 100
    assert usage.feature_output_tokens == 40
    assert usage.categories == (
        ("input", 75),
        ("cached_input", 25),
        ("output", 30),
        ("reasoning", 10),
    )


def test_anthropic_usage_normalizer_preserves_cache_categories() -> None:
    usage = normalize_anthropic_usage(
        {
            "input_tokens": 70,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 10,
            "output_tokens": 15,
        }
    )

    assert usage.feature_input_tokens == 100
    assert usage.feature_output_tokens == 15
    assert usage.categories == (
        ("input", 70),
        ("cached_input", 20),
        ("cache_write", 10),
        ("output", 15),
    )


def test_openai_wrapper_links_detailed_cost_events_to_feature_event() -> None:
    response = SimpleNamespace(
        id="openai_request_1",
        model="gpt-test",
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=25),
        ),
    )
    provider = client_with_create(lambda **_: response, "openai")
    tracker = cost_tracker()
    provider = wrap_openai(
        provider,
        tracker,
        customer_id="cust_1",
        metric_code="copilot_action",
        cost_meter_code="ai_tokens",
    )

    assert provider.chat.completions.create(model="gpt-test") is response
    tracker.track.assert_called_once()
    assert tracker.track.call_args.kwargs["transaction_id"] == "llm-openai-openai_request_1"
    assert tracker.track.call_args.kwargs["metadata"]["provider"] == "openai"
    assert tracker.cost_events.track.call_count == 3
    first_cost = tracker.cost_events.track.call_args_list[0].kwargs
    assert first_cost["parent_transaction_id"] == "llm-openai-openai_request_1"
    assert first_cost["request_id"] == "openai_request_1"
    assert first_cost["operation_key"] == "input"
    assert first_cost["properties"] == {
        "tokens": 75,
        "provider": "openai",
        "model": "gpt-test",
        "type": "input",
    }


def test_anthropic_wrapper_emits_cache_read_and_write_cost_events() -> None:
    response = SimpleNamespace(
        id="anthropic_request_1",
        model="claude-test",
        usage=SimpleNamespace(
            input_tokens=50,
            cache_read_input_tokens=30,
            cache_creation_input_tokens=20,
            output_tokens=10,
        ),
    )
    provider = client_with_create(lambda **_: response, "anthropic")
    tracker = cost_tracker()
    provider = wrap_anthropic(
        provider,
        tracker,
        customer_id="cust_1",
        cost_meter_code="ai_tokens",
    )

    assert provider.messages.create(model="claude-test") is response
    assert tracker.cost_events.track.call_count == 4
    operation_keys = {
        call.kwargs["operation_key"] for call in tracker.cost_events.track.call_args_list
    }
    assert operation_keys == {"input", "cached_input", "cache_write", "output"}


CUSTOMERS = ["cust_a", "cust_b", "cust_c"]


def billed(track: Mock) -> list[str]:
    return [call.args[0] for call in track.call_args_list]


@pytest.mark.parametrize(
    ("wrap", "provider", "path"),
    [
        (wrap_openai, "openai", ("chat", "completions")),
        (wrap_anthropic, "anthropic", ("messages",)),
    ],
)
def test_wrapping_a_shared_client_per_customer_records_each_call_once(
    wrap: Any, provider: str, path: tuple[str, ...]
) -> None:
    response = SimpleNamespace(
        model="model",
        usage=SimpleNamespace(
            prompt_tokens=1, completion_tokens=1, input_tokens=1, output_tokens=1
        ),
    )
    original_create = Mock(return_value=response)
    shared = client_with_create(original_create, provider)
    tracker = SimpleNamespace(track=Mock())

    def resource(client: Any) -> Any:
        for name in path:
            client = getattr(client, name)
        return client

    for customer_id in CUSTOMERS:
        wrapped = wrap(shared, tracker, customer_id=customer_id)
        resource(wrapped).create(model="model")
        resource(wrapped).create(model="model")
    resource(shared).create(model="model")

    assert billed(tracker.track) == ["cust_a", "cust_a", "cust_b", "cust_b", "cust_c", "cust_c"]
    assert resource(shared).create is original_create
    assert original_create.call_count == 7


def test_wrapping_a_wrapped_client_wraps_its_original() -> None:
    response = SimpleNamespace(
        model="m", usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    )
    shared = client_with_create(lambda **_: response, "openai")
    tracker = SimpleNamespace(track=Mock())

    first = wrap_openai(shared, tracker, customer_id="cust_a")
    wrap_openai(first, tracker, customer_id="cust_b").chat.completions.create(model="m")

    assert billed(tracker.track) == ["cust_b"]
    assert isinstance(first, SimpleNamespace)
    assert first.chat.completions is not shared.chat.completions


@pytest.mark.asyncio
async def test_concurrent_streams_on_a_shared_client_bill_their_own_customer() -> None:
    async def stream() -> AsyncIterator[Any]:
        yield SimpleNamespace(usage=None)
        await asyncio.sleep(0)
        yield SimpleNamespace(usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1))

    async def create(**_: Any) -> Any:
        return stream()

    shared = client_with_create(create, "openai")
    tracker = SimpleNamespace(track=Mock())

    async def call(customer_id: str) -> None:
        client = wrap_openai(shared, tracker, customer_id=customer_id)
        async for _ in await client.chat.completions.create(model="m", stream=True):
            pass

    await asyncio.gather(*(call(customer_id) for customer_id in CUSTOMERS))

    assert sorted(billed(tracker.track)) == CUSTOMERS
