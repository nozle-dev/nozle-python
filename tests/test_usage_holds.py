from __future__ import annotations

import re
from typing import Any

import pytest
import requests_mock

from nozle import Nozle, NozleAPIError, NozleAuthenticationError, NozleValidationError

ENGINE = "https://api.example/engine"
HOLDS = f"{ENGINE}/api/v1/usage/holds"
# The JSON keys of Engine's request structs (internal/api/credit_hold_handler.go,
# creditHoldCreateRequest and creditHoldFinalizeRequest). Engine decodes strictly: any other key
# is a 400.
ENGINE_CREATE_KEYS = {
    "customer_id",
    "subscription_id",
    "entity_id",
    "feature_code",
    "credit_system_code",
    "properties",
    "timestamp",
    "ttl_seconds",
}
ENGINE_FINALIZE_KEYS = {"properties", "timestamp"}


def hold(status: str = "held", **fields: Any) -> dict[str, Any]:
    return {
        "hold_id": "hold/1",
        "status": status,
        "customer_id": "acme",
        "feature_code": "agent_execution",
        "credit_system": "ai_credits",
        "metric_amount": "1",
        "credits_required": "2.000000000001",
        "credits_held": "2.000000000001",
        "expires_at": "2026-10-31T12:15:00Z",
        "created_at": "2026-10-31T12:00:00Z",
        "resolved_at": None,
        **fields,
    }


def holds() -> Any:
    return Nozle("sk_test", base_url=ENGINE, retry_delay=0).usage.holds


def test_create_sends_the_track_body_with_ttl_and_the_idempotency_key(
    requests_mock: requests_mock.Mocker,
) -> None:
    matcher = requests_mock.post(HOLDS, json={**hold(), "allowed": True, "available": "8"})

    result = holds().create(
        "acme",
        "agent_execution",
        idempotency_key="run-1",
        entity_id="user/42",
        credit_system_code="ai_credits",
        properties={"tokens": 4000},
        timestamp="2026-10-31T12:00:00.750Z",
        ttl_seconds=600,
    )

    assert result["allowed"] is True
    assert result["credits_held"] == "2.000000000001"
    assert matcher.last_request.headers["Idempotency-Key"] == "run-1"
    body = matcher.last_request.json()
    assert body == {
        "customer_id": "acme",
        "feature_code": "agent_execution",
        "properties": {"tokens": 4000},
        "timestamp": "2026-10-31T12:00:00.750Z",
        "entity_id": "user/42",
        "credit_system_code": "ai_credits",
        "ttl_seconds": 600,
    }
    assert set(body) <= ENGINE_CREATE_KEYS


def test_create_denial_is_a_result_and_a_retried_key_keeps_its_timestamp(
    requests_mock: requests_mock.Mocker,
) -> None:
    denied = {
        **hold("denied", credits_held="0"),
        "allowed": False,
        "reason": "insufficient_credits",
        "available": "1",
    }
    matcher = requests_mock.post(HOLDS, json=denied)
    client = holds()

    first = client.create("acme", "agent_execution", idempotency_key="run-2")
    client.create("acme", "agent_execution", idempotency_key="run-2")
    client.create("acme", "agent_execution", idempotency_key="run-3")

    assert first["allowed"] is False and first["status"] == "denied"
    stamps = [request.json()["timestamp"] for request in matcher.request_history]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", stamps[0])
    assert stamps[0] == stamps[1]
    assert set(matcher.request_history[0].json()) == {
        "customer_id",
        "feature_code",
        "properties",
        "timestamp",
    }


def test_finalize_posts_the_actual_usage_and_is_retried_with_the_same_body(
    requests_mock: requests_mock.Mocker,
) -> None:
    matcher = requests_mock.post(
        f"{HOLDS}/hold%2F1/finalize",
        [
            {"status_code": 503, "json": {}},
            {
                "json": {
                    "allowed": True,
                    "operation_id": "op-1",
                    "credits_consumed": "1.5",
                    "hold_id": "hold/1",
                    "hold_status": "finalized",
                    "credits_held": "2",
                    "credits_released": "0.5",
                    "hold_expired": False,
                }
            },
        ],
    )

    result = holds().finalize("hold/1", properties={"tokens": 3000})

    assert result["hold_status"] == "finalized"
    assert result["credits_released"] == "0.5"
    assert matcher.call_count == 2
    first, second = (request.json() for request in matcher.request_history)
    assert first == second
    assert first["properties"] == {"tokens": 3000}
    assert set(first) == ENGINE_FINALIZE_KEYS
    assert "Idempotency-Key" not in matcher.last_request.headers


def test_finalize_of_the_same_hold_reuses_the_generated_timestamp(
    requests_mock: requests_mock.Mocker,
) -> None:
    matcher = requests_mock.post(f"{HOLDS}/h1/finalize", json={"allowed": True})
    client = holds()

    client.finalize("h1")
    client.finalize("h1", properties={"tokens": 1})
    client.finalize("h1", timestamp="2026-10-31T12:01:00Z")

    sent = [request.json() for request in matcher.request_history]
    assert sent[0]["timestamp"] == sent[1]["timestamp"]
    assert sent[0]["properties"] == {}
    assert sent[2]["timestamp"] == "2026-10-31T12:01:00Z"


def test_release_get_and_list(requests_mock: requests_mock.Mocker) -> None:
    released = hold("released", resolved_at="2026-10-31T12:05:00Z")
    release = requests_mock.post(f"{HOLDS}/hold%2F1/release", json=released)
    requests_mock.get(f"{HOLDS}/hold%2F1", json=hold())
    listing = requests_mock.get(
        f"{ENGINE}/api/v1/customers/acme%2Fwest/credit-holds",
        json={"customer_id": "acme/west", "holds": [hold()]},
    )
    client = holds()

    assert client.release("hold/1")["status"] == "released"
    assert release.last_request.body is None
    assert client.get("hold/1")["status"] == "held"
    assert client.list("acme/west", status="held")["holds"][0]["hold_id"] == "hold/1"
    assert listing.last_request.qs == {"status": ["held"]}
    client.list("acme/west")
    assert listing.last_request.qs == {}


@pytest.mark.parametrize(
    "status,code",
    [(404, "hold_not_found"), (409, "hold_already_resolved"), (400, "hold_ttl_invalid")],
)
def test_engine_hold_errors_keep_their_code(
    requests_mock: requests_mock.Mocker, status: int, code: str
) -> None:
    requests_mock.post(
        f"{HOLDS}/h1/release",
        status_code=status,
        json={"code": code, "message": "credit hold error"},
    )
    with pytest.raises(NozleAPIError) as raised:
        holds().release("h1")
    assert raised.value.status_code == status
    assert raised.value.has_code(code)


@pytest.mark.parametrize("ttl", [0, 86401, True, 1.5])
def test_invalid_ttl_and_status_fail_before_any_request(
    requests_mock: requests_mock.Mocker, ttl: Any
) -> None:
    client = holds()
    with pytest.raises(NozleValidationError, match="ttl_seconds"):
        client.create("acme", "metric", idempotency_key="k", ttl_seconds=ttl)
    with pytest.raises(NozleValidationError, match="status"):
        client.list("acme", status="open")  # type: ignore[arg-type]
    with pytest.raises(NozleValidationError, match="idempotency_key"):
        client.create("acme", "metric", idempotency_key=" ")
    with pytest.raises(NozleValidationError, match="hold_id"):
        client.finalize("")
    assert requests_mock.request_history == []


def test_holds_reject_a_publishable_key_before_network(
    requests_mock: requests_mock.Mocker,
) -> None:
    client = Nozle("pk_browser").usage.holds
    for call in (
        lambda: client.create("acme", "metric", idempotency_key="k"),
        lambda: client.finalize("h1"),
        lambda: client.release("h1"),
        lambda: client.get("h1"),
        lambda: client.list("acme"),
    ):
        with pytest.raises(NozleAuthenticationError):
            call()
    assert requests_mock.request_history == []
