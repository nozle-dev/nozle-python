from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from nozle.integrations._common import (
    StreamUsageCollector,
    capture_provider_usage,
    detect_usage,
    estimate_tokens,
    request_text_bytes,
)

# The shared contract of the LLM usage normalizers (the Engine, nozle-js, nozle-python and
# nozle-go read the same file).
FIXTURES = json.loads((Path(__file__).parent / "contract" / "llm-usage-fixtures.json").read_text())
BREAKDOWNS = (
    "cached_input_tokens",
    "cache_write_tokens",
    "cache_write_1h_tokens",
    "reasoning_tokens",
    "audio_input_tokens",
    "audio_output_tokens",
    "image_input_tokens",
    "image_output_tokens",
)


def sent(found: Any) -> tuple[dict[str, Any], dict[str, int]]:
    nozle = SimpleNamespace(
        events=SimpleNamespace(create_transaction_id=Mock(return_value="txn")),
        track=Mock(return_value="txn"),
        cost_events=SimpleNamespace(track=Mock(return_value={"status": "accepted"})),
    )
    errors = capture_provider_usage(
        nozle,
        "c",
        "f",
        feature=None,
        cost_meter_code="m",
        provider=found.format,
        model="m",
        request_id="r",
        usage=found.usage,
        latency_ms=0,
    )
    assert errors == []
    props = nozle.track.call_args.kwargs["metadata"]
    costs = {
        call.kwargs["properties"]["type"]: call.kwargs["properties"]["tokens"]
        for call in nozle.cost_events.track.call_args_list
    }
    return props, costs


def check(props: dict[str, Any], costs: dict[str, int], want: dict[str, Any]) -> None:
    assert props["input_tokens"] == want["input_tokens"]
    assert props["output_tokens"] == want["output_tokens"]
    for key in BREAKDOWNS:
        if key in want:
            assert props[key] == want[key], key
        else:
            assert key not in props, key
    assert props.get("service_tier") == want.get("service_tier")
    assert costs == want["buckets"]


def feed(sse: str) -> StreamUsageCollector:
    collector = StreamUsageCollector()
    for line in sse.split("\n"):
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            collector.mark_finished()
        else:
            collector.add(json.loads(data))
    return collector


def test_estimator_is_ceil_of_bytes_over_four() -> None:
    for case in FIXTURES["estimator"]["cases"]:
        assert estimate_tokens(case["bytes"]) == case["tokens"]


def test_request_text_bytes() -> None:
    for case in FIXTURES["estimator"]["request_text"]:
        assert request_text_bytes(case["body"]) == case["bytes"]


@pytest.mark.parametrize("case", FIXTURES["usage"], ids=lambda c: c["name"])
def test_usage_cases(case: dict[str, Any]) -> None:
    found = detect_usage(case["payload"])
    if not case["expect"]["found"]:
        assert found is None
        return
    assert found is not None and found.format == case["expect"]["format"]
    props, costs = sent(found)
    check(props, costs, case["expect"])
    assert props["usage_source"] == "reported"


@pytest.mark.parametrize("case", FIXTURES["streams"], ids=lambda c: c["name"])
def test_stream_cases(case: dict[str, Any]) -> None:
    collector = feed(case["sse"])
    assert collector.complete is case["complete"]
    assert collector.delivered_bytes == case["delivered_bytes"]
    found = collector.result()
    if not case["expect"]["found"]:
        assert found is None
        return
    assert found is not None and found.format == case["expect"]["format"]
    props, costs = sent(found)
    check(props, costs, case["expect"])


@pytest.mark.parametrize("case", FIXTURES["aborted"], ids=lambda c: c["name"])
def test_aborted_cases(case: dict[str, Any]) -> None:
    assert request_text_bytes(case["request_body"]) == case["request_text_bytes"]
    collector = feed(case["sse"])
    found = collector.result(False, request_text_bytes(case["request_body"]))
    if not case["expect"]["bill"]:
        assert found is None
        return
    assert found is not None and found.format == case["expect"]["format"]
    props, costs = sent(found)
    check(props, costs, case["expect"])
    assert props["usage_source"] == case["expect"]["usage_source"]
    # A stream cut after the provider's final usage already has its full usage: not aborted.
    assert props.get("stream_aborted") == (None if collector.complete else True)
