from __future__ import annotations

import pytest
import requests_mock

from nozle import Nozle, NozleAuthenticationError, NozleValidationError

ANY_URL = requests_mock.ANY


def core_credit_system(system_id: str, code: str) -> dict[str, object]:
    return {
        "lago_id": system_id,
        "code": code,
        "name": code.upper(),
        "description": None,
        "unit_name": "credit",
        "status": "active",
        "created_at": "2026-07-20T12:00:00Z",
        "updated_at": "2026-07-20T12:00:00Z",
    }


def balance_source() -> dict[str, object]:
    return {
        "id": "source-1",
        "entity_id": None,
        "parent_source_id": None,
        "scope": "customer",
        "transferable": True,
        "returnable": False,
        "type": "top_up",
        "reference": "payment-1",
        "subscription_id": None,
        "initial": "123456789012345678.123456789012",
        "remaining": "375.000000000001",
        "valid_from": "2026-07-20T12:00:00.750Z",
        "expires_at": None,
        "priority": 10,
        "status": "active",
        "available": True,
    }


def operation() -> dict[str, object]:
    return {
        "id": "operation-1",
        "entity_id": "user/42",
        "credit_system": "ai credits",
        "credit_system_id": "system-1",
        "credit_system_name": "AI Credits",
        "unit_name": "credit",
        "feature_code": "agent_execution",
        "type": "consume",
        "status": "succeeded",
        "metric_amount": "1.000000000001",
        "credit_amount": "2.000000000001",
        "rate_id": "rate-1",
        "rate_metric_amount": "1",
        "rate_credit_amount": "2.000000000001",
        "reason": None,
        "occurred_at": "2026-07-20T12:00:00.750Z",
        "source_allocations": [
            {
                "source_id": "source-1",
                "source_entity_id": "user/42",
                "source_type": "top_up",
                "delta": "-2.000000000001",
                "before": "100.000000000001",
                "after": "98",
            }
        ],
    }


def test_credit_systems_list_paginates_every_active_core_page(
    requests_mock: requests_mock.Mocker,
) -> None:
    first = requests_mock.get(
        "https://api.example/core/api/v1/credit-systems?status=active&page=1&per_page=100",
        json={"credit_systems": [core_credit_system("system-1", "ai")], "meta": {"next_page": 2}},
        complete_qs=True,
    )
    second = requests_mock.get(
        "https://api.example/core/api/v1/credit-systems?status=active&page=2&per_page=100",
        json={
            "credit_systems": [core_credit_system("system-2", "api")],
            "meta": {"next_page": None},
        },
        complete_qs=True,
    )

    systems = Nozle("sk_test", events_url="https://api.example/core").credit_systems.list()

    assert [system["code"] for system in systems] == ["ai", "api"]
    assert systems[0] == {
        "id": "system-1",
        "code": "ai",
        "name": "AI",
        "description": None,
        "unit_name": "credit",
        "status": "active",
        "created_at": "2026-07-20T12:00:00Z",
        "updated_at": "2026-07-20T12:00:00Z",
    }
    assert first.call_count == second.call_count == 1


def test_customer_credit_reads_escape_identifiers_and_preserve_exact_values(
    requests_mock: requests_mock.Mocker,
) -> None:
    balance_url = "https://api.example/engine/api/v1/customers/acme%2Fwest/credit-systems/ai%20credits/balance"
    requests_mock.get(
        balance_url,
        json={
            "customer_id": "acme/west",
            "credit_system": "ai credits",
            "credit_system_id": "system-1",
            "credit_system_name": "AI Credits",
            "unit_name": "credit",
            "system_status": "active",
            "available": "123456789012345678.123456789012",
            "as_of": "2026-07-20T12:00:00.750Z",
            "sources": [balance_source()],
        },
    )
    requests_mock.get(
        "https://api.example/engine/api/v1/customers/acme%2Fwest/credit-systems",
        json={
            "customer_id": "acme/west",
            "as_of": "2026-07-20T12:00:00.750Z",
            "balances": [{"available": "500.000000000001"}],
        },
    )
    client = Nozle("sk_test", base_url="https://api.example/engine")

    balance = client.credits.get_balance("acme/west", "ai credits")
    balances = client.credits.list_balances("acme/west")

    assert balance["available"] == "123456789012345678.123456789012"
    assert balance["sources"][0] == balance_source()
    assert balances["balances"][0]["available"] == "500.000000000001"
    assert requests_mock.request_history[0].url == balance_url


def test_customer_operations_preserve_ledger_fields_and_nullable_cursor(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.get(
        "https://api.example/engine/api/v1/customers/acme%2Fwest/credit-operations",
        json={
            "customer_id": "acme/west",
            "operations": [operation()],
            "next_cursor": None,
        },
    )
    client = Nozle("sk_test", base_url="https://api.example/engine")

    page = client.credits.list_operations(
        "acme/west",
        credit_system_code="ai credits",
        limit=25,
        cursor="current/page",
    )

    assert page["next_cursor"] is None
    assert page["operations"][0] == operation()
    assert requests_mock.last_request.qs == {
        "credit_system_code": ["ai credits"],
        "limit": ["25"],
        "cursor": ["current/page"],
    }


def test_entity_credit_reads_preserve_pool_and_provenance(
    requests_mock: requests_mock.Mocker,
) -> None:
    base = "https://api.example/engine/api/v1/customers/acme%2Fwest/entities/user%2F42"
    requests_mock.get(
        f"{base}/credit-systems/ai%20credits/balance",
        json={
            "customer_id": "acme/west",
            "entity_id": "user/42",
            "entity_status": "active",
            "credit_system": "ai credits",
            "credit_system_id": "system-1",
            "credit_system_name": "AI Credits",
            "unit_name": "credit",
            "system_status": "active",
            "entity_available": "480.000000000001",
            "shared_available": "250",
            "effective_available": "730.000000000001",
            "consumed": "20",
            "pool_policy": "entity_then_customer",
            "as_of": "2026-07-20T12:00:00.750Z",
            "sources": [balance_source()],
        },
    )
    requests_mock.get(
        f"{base}/credit-systems",
        json={
            "customer_id": "acme/west",
            "entity_id": "user/42",
            "entity_status": "active",
            "as_of": "2026-07-20T12:00:00.750Z",
            "balances": [{"effective_available": "730.000000000001"}],
        },
    )
    requests_mock.get(
        f"{base}/credit-operations",
        json={
            "customer_id": "acme/west",
            "entity_id": "user/42",
            "operations": [operation()],
            "next_cursor": None,
        },
    )
    client = Nozle("sk_test", base_url="https://api.example/engine")

    balance = client.credits.get_entity_balance("acme/west", "user/42", "ai credits")
    balances = client.credits.list_entity_balances("acme/west", "user/42")
    history = client.credits.list_entity_operations(
        "acme/west", "user/42", limit=25, cursor="cursor/1"
    )

    assert balance["effective_available"] == "730.000000000001"
    assert balance["sources"][0]["reference"] == "payment-1"
    assert balances["balances"][0]["effective_available"] == "730.000000000001"
    assert history["next_cursor"] is None
    assert history["operations"][0]["rate_credit_amount"] == "2.000000000001"
    assert requests_mock.last_request.qs == {"limit": ["25"], "cursor": ["cursor/1"]}


def test_allocate_and_deallocate_send_exact_decimal_and_idempotency(
    requests_mock: requests_mock.Mocker,
) -> None:
    base = "https://api.example/engine/api/v1/customers/acme/entities/user-42"
    allocate = requests_mock.post(
        f"{base}/credit-allocations",
        status_code=201,
        json={
            "transferred": True,
            "operation_id": "op-1",
            "customer_id": "acme",
            "entity_id": "user-42",
            "credit_system": "ai_credits",
            "direction": "allocation",
            "amount": "100.000000000001",
            "available": "100.000000000001",
            "parent_sources": [],
            "entity_sources": [],
            "replayed": False,
        },
    )
    deallocate = requests_mock.post(
        f"{base}/credit-deallocations",
        json={
            "transferred": True,
            "operation_id": "op-2",
            "customer_id": "acme",
            "entity_id": "user-42",
            "credit_system": "ai_credits",
            "direction": "deallocation",
            "amount": "25",
            "available": "75.000000000001",
            "parent_sources": [],
            "entity_sources": [],
            "replayed": False,
        },
    )
    credits = Nozle("sk_test", base_url="https://api.example/engine").credits

    result = credits.allocate(
        "acme",
        "user-42",
        credit_system_code="ai_credits",
        amount="100.000000000001",
        idempotency_key="allocate-1",
    )
    credits.deallocate(
        "acme",
        "user-42",
        credit_system_code="ai_credits",
        amount="25",
        idempotency_key="deallocate-1",
    )

    assert result["amount"] == "100.000000000001"
    assert allocate.last_request.headers["Idempotency-Key"] == "allocate-1"
    assert allocate.last_request.json() == {
        "credit_system": "ai_credits",
        "amount": "100.000000000001",
    }
    assert deallocate.last_request.headers["Idempotency-Key"] == "deallocate-1"


def test_transfer_accepts_maximum_decimal_precision_and_255_byte_key(
    requests_mock: requests_mock.Mocker,
) -> None:
    matcher = requests_mock.post(
        ANY_URL,
        json={
            "transferred": True,
            "operation_id": "op-max",
            "customer_id": "acme",
            "entity_id": "user",
            "credit_system": "ai",
            "direction": "allocation",
            "amount": "999999999999999999.999999999999",
            "available": "999999999999999999.999999999999",
            "parent_sources": [],
            "entity_sources": [],
            "replayed": False,
        },
    )
    key = ("é" * 127) + "a"

    result = Nozle("sk_test").credits.allocate(
        "acme",
        "user",
        credit_system_code="ai",
        amount="999999999999999999.999999999999",
        idempotency_key=key,
    )

    assert result["amount"] == "999999999999999999.999999999999"
    assert matcher.last_request.headers["Idempotency-Key"] == key


@pytest.mark.parametrize(
    "amount",
    [
        0,
        1.2,
        "",
        "0",
        "0.0",
        "-1",
        "+1",
        "01",
        "1.",
        ".1",
        "1e3",
        "0.0000000000001",
        "1.0000000000001",
    ],
)
def test_transfer_amount_validation_rejects_lossy_or_non_positive_values(
    requests_mock: requests_mock.Mocker, amount: object
) -> None:
    with pytest.raises(NozleValidationError, match="positive decimal string"):
        Nozle("sk_test").credits.allocate(
            "acme",
            "user",
            credit_system_code="ai",
            amount=amount,  # type: ignore[arg-type]
            idempotency_key="key",
        )
    assert requests_mock.request_history == []


@pytest.mark.parametrize("limit", [0, 101, -1, 1.5, True])
def test_credit_operation_limit_validation(
    requests_mock: requests_mock.Mocker, limit: object
) -> None:
    with pytest.raises(NozleValidationError, match="between 1 and 100"):
        Nozle("sk_test").credits.list_operations("acme", limit=limit)  # type: ignore[arg-type]
    assert requests_mock.request_history == []


@pytest.mark.parametrize("key", ["", " ", "x" * 256, "é" * 128])
def test_transfer_idempotency_uses_utf8_byte_limit(
    requests_mock: requests_mock.Mocker, key: str
) -> None:
    with pytest.raises(NozleValidationError, match="idempotency_key"):
        Nozle("sk_test").credits.allocate(
            "acme",
            "user",
            credit_system_code="ai",
            amount="1",
            idempotency_key=key,
        )
    assert requests_mock.request_history == []


def test_transfer_rejects_publishable_key_before_other_validation(
    requests_mock: requests_mock.Mocker,
) -> None:
    with pytest.raises(NozleAuthenticationError, match="secret key"):
        Nozle("pk_browser").credits.deallocate(
            "",
            "",
            credit_system_code="",
            amount="0",
            idempotency_key="",
        )
    assert requests_mock.request_history == []
