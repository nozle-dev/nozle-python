from __future__ import annotations

import re

import pytest
import requests_mock

from nozle import Nozle, NozleAuthenticationError, NozleValidationError

ANY_URL = requests_mock.ANY


def entity(status: str = "active") -> dict[str, object]:
    return {
        "id": "entity-uuid",
        "customer_id": "acme/west",
        "external_id": "user/42",
        "name": "Asha",
        "status": status,
        "metadata": {"role": "admin"},
        "created_at": "2026-07-20T12:00:00Z",
        "updated_at": "2026-07-20T12:00:00Z",
        "deleted_at": None,
    }


def test_entity_get_list_and_upsert_contract(requests_mock: requests_mock.Mocker) -> None:
    item_url = "https://api.example/engine/api/v1/customers/acme%2Fwest/entities/user%2F42"
    requests_mock.get(item_url, json={"entity": entity()})
    requests_mock.get(
        "https://api.example/engine/api/v1/customers/acme%2Fwest/entities",
        json={"customer_id": "acme/west", "entities": [entity()], "next_cursor": None},
    )
    requests_mock.put(
        item_url,
        json={"action": "updated", "entity": entity(), "replayed": False},
    )
    entities = Nozle("sk_test", base_url="https://api.example/engine").entities

    assert entities.get("acme/west", "user/42")["external_id"] == "user/42"
    page = entities.list("acme/west", status="active", limit=25, cursor="cursor/1")
    result = entities.upsert(
        "acme/west",
        "user/42",
        status="active",
        name="Asha",
        metadata={"role": "admin"},
        idempotency_key="entity-user-42-v2",
    )

    assert page["next_cursor"] is None
    assert requests_mock.request_history[1].qs == {
        "status": ["active"],
        "limit": ["25"],
        "cursor": ["cursor/1"],
    }
    assert result["entity"]["external_id"] == "user/42"
    assert requests_mock.last_request.headers["Idempotency-Key"] == "entity-user-42-v2"
    assert requests_mock.last_request.json() == {
        "name": "Asha",
        "status": "active",
        "metadata": {"role": "admin"},
    }


@pytest.mark.parametrize(
    ("method", "target_status"), [("suspend", "suspended"), ("activate", "active")]
)
def test_entity_lifecycle_preserves_name_and_metadata(
    requests_mock: requests_mock.Mocker, method: str, target_status: str
) -> None:
    url = "https://api.example/engine/api/v1/customers/acme/entities/user-42"
    requests_mock.get(url, json={"entity": entity()})
    requests_mock.put(
        url,
        json={"action": target_status, "entity": entity(target_status), "replayed": False},
    )
    namespace = Nozle("sk_test", base_url="https://api.example/engine").entities

    result = getattr(namespace, method)("acme", "user-42", idempotency_key=f"{method}-1")

    assert result["entity"]["status"] == target_status
    assert requests_mock.last_request.json() == {
        "name": "Asha",
        "status": target_status,
        "metadata": {"role": "admin"},
    }


def test_bulk_entity_upsert_supports_wire_shape_and_maximum_batch(
    requests_mock: requests_mock.Mocker,
) -> None:
    matcher = requests_mock.post(
        "https://api.example/engine/api/v1/customers/acme/entities/bulk-upsert",
        json={"customer_id": "acme", "entities": [], "counts": {}, "replayed": False},
    )
    items = [{"external_id": f"user-{index}", "status": "active"} for index in range(500)]

    Nozle("sk_test", base_url="https://api.example/engine").entities.bulk_upsert(
        "acme", items, idempotency_key="import-1"
    )

    assert len(matcher.last_request.json()["entities"]) == 500
    assert matcher.last_request.json()["entities"][0] == {
        "external_id": "user-0",
        "name": None,
        "status": "active",
        "metadata": {},
    }
    assert matcher.last_request.headers["Idempotency-Key"] == "import-1"


def test_bulk_entity_upsert_rejects_empty_oversized_and_duplicate_batches(
    requests_mock: requests_mock.Mocker,
) -> None:
    entities = Nozle("sk_test").entities
    for items in (
        [],
        [{"external_id": f"user-{index}", "status": "active"} for index in range(501)],
        [
            {"external_id": " user-1", "status": "active"},
            {"external_id": "user-1 ", "status": "suspended"},
        ],
    ):
        with pytest.raises(NozleValidationError):
            entities.bulk_upsert("acme", items, idempotency_key="import-1")
    assert requests_mock.request_history == []


@pytest.mark.parametrize("entity_id", ["", " ", "é" * 128])
def test_entity_id_utf8_byte_limit(requests_mock: requests_mock.Mocker, entity_id: str) -> None:
    with pytest.raises(NozleValidationError, match="entity_id"):
        Nozle("sk_test").entities.get("acme", entity_id)
    assert requests_mock.request_history == []


def test_entity_id_accepts_exactly_255_utf8_bytes(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.get(ANY_URL, json={"entity": entity()})
    entity_id = ("é" * 127) + "a"

    result = Nozle("sk_test").entities.get("acme", entity_id)

    assert result["external_id"] == "user/42"
    assert len(entity_id.encode("utf-8")) == 255


@pytest.mark.parametrize("key", ["", " ", "x" * 256, "é" * 128])
def test_entity_mutation_idempotency_utf8_byte_limit(
    requests_mock: requests_mock.Mocker, key: str
) -> None:
    with pytest.raises(NozleValidationError, match="idempotency_key"):
        Nozle("sk_test").entities.upsert("acme", "user", status="active", idempotency_key=key)
    assert requests_mock.request_history == []


@pytest.mark.parametrize("limit", [0, 101, -1, 1.5, True])
def test_entity_page_limit_validation(requests_mock: requests_mock.Mocker, limit: object) -> None:
    with pytest.raises(NozleValidationError, match="between 1 and 100"):
        Nozle("sk_test").entities.list("acme", limit=limit)  # type: ignore[arg-type]
    assert requests_mock.request_history == []


def test_entity_mutation_rejects_publishable_key_before_network(
    requests_mock: requests_mock.Mocker,
) -> None:
    with pytest.raises(NozleAuthenticationError, match="secret key"):
        Nozle("pk_browser").entities.upsert("acme", "user", status="active", idempotency_key="key")
    assert requests_mock.request_history == []


def test_usage_check_is_advisory_and_preserves_exact_decimal_contract(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        "https://api.example/engine/api/v1/usage/check",
        json={
            "advisory": True,
            "allowed": True,
            "entity_id": "user/42",
            "pool_policy": "entity_then_customer",
            "metric_amount": "1",
            "credit_system": "ai_credits",
            "credits_required": "2.000000000001",
            "available": "500",
            "projected_remaining": "497.999999999999",
            "projected_deductions": [
                {
                    "balance_source_id": "source-1",
                    "source_type": "subscription_grant",
                    "source_scope": "entity",
                    "amount": "2.000000000001",
                    "remaining": "247.999999999999",
                }
            ],
        },
    )
    usage = Nozle("sk_test", base_url="https://api.example/engine").usage

    result = usage.check(
        "acme",
        "agent_execution",
        entity_id="user/42",
        credit_system_code="ai_credits",
        properties={"model": "gpt-5"},
        occurred_at="2026-07-20T12:00:00.750Z",
    )

    assert result["projected_remaining"] == "497.999999999999"
    assert result["projected_deductions"][0]["source_scope"] == "entity"
    assert requests_mock.last_request.headers.get("Idempotency-Key") is None
    assert requests_mock.last_request.json() == {
        "customer_id": "acme",
        "feature_code": "agent_execution",
        "properties": {"model": "gpt-5"},
        "occurred_at": "2026-07-20T12:00:00.750Z",
        "entity_id": "user/42",
        "credit_system_code": "ai_credits",
    }


def test_usage_track_is_mutating_and_uses_exact_timestamp_and_idempotency(
    requests_mock: requests_mock.Mocker,
) -> None:
    matcher = requests_mock.post(
        "https://api.example/engine/api/v1/usage/track",
        json={
            "allowed": True,
            "operation_id": "operation-1",
            "credits_consumed": "2.000000000001",
            "remaining": "497.999999999999",
            "deductions": [
                {
                    "source_type": "top_up",
                    "source_scope": "customer",
                    "amount": "2.000000000001",
                    "remaining": "97.999999999999",
                }
            ],
        },
    )
    usage = Nozle("sk_test", base_url="https://api.example/engine").usage

    result = usage.track(
        "acme",
        "agent_execution",
        entity_id="user/42",
        properties={"request": 1},
        timestamp="2026-07-20T12:00:00.750Z",
        idempotency_key="execution-1",
    )

    assert result["credits_consumed"] == "2.000000000001"
    assert matcher.last_request.headers["Idempotency-Key"] == "execution-1"
    assert matcher.last_request.json() == {
        "customer_id": "acme",
        "feature_code": "agent_execution",
        "properties": {"request": 1},
        "timestamp": "2026-07-20T12:00:00.750Z",
        "entity_id": "user/42",
    }


def test_usage_default_timestamps_are_utc_rfc3339_milliseconds(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        "https://api.nozle.app/engine/api/v1/usage/check",
        json={
            "advisory": True,
            "allowed": False,
            "metric_amount": "1",
            "credit_system": "ai",
            "credits_required": "1",
            "available": "0",
        },
    )
    requests_mock.post(
        "https://api.nozle.app/engine/api/v1/usage/track",
        json={"allowed": False},
    )
    usage = Nozle("sk_test").usage

    usage.check("acme", "metric")
    occurred_at = requests_mock.last_request.json()["occurred_at"]
    usage.track("acme", "metric", idempotency_key="track-1")
    timestamp = requests_mock.last_request.json()["timestamp"]

    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
    assert re.match(pattern, occurred_at)
    assert re.match(pattern, timestamp)


@pytest.mark.parametrize("key", ["", " ", "x" * 256, "é" * 128])
def test_usage_track_idempotency_utf8_byte_limit(
    requests_mock: requests_mock.Mocker, key: str
) -> None:
    with pytest.raises(NozleValidationError, match="idempotency_key"):
        Nozle("sk_test").usage.track("acme", "metric", idempotency_key=key)
    assert requests_mock.request_history == []


def test_usage_check_and_track_reject_publishable_key_before_network(
    requests_mock: requests_mock.Mocker,
) -> None:
    usage = Nozle("pk_browser").usage
    with pytest.raises(NozleAuthenticationError):
        usage.check("acme", "metric")
    with pytest.raises(NozleAuthenticationError):
        usage.track("acme", "metric", idempotency_key="key")
    assert requests_mock.request_history == []
