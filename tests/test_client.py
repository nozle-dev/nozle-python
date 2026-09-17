from __future__ import annotations

from importlib.metadata import version
from typing import Callable
from uuid import UUID

import pytest
import requests_mock

from nozle import (
    Nozle,
    NozleAPIError,
    NozleAuthenticationError,
    NozleValidationError,
    __version__,
)


def test_version_metadata_and_runtime_match() -> None:
    assert __version__ == "0.8.0"
    assert version("nozle-sdk") == __version__


def test_client_initializes_namespaces_and_strips_slashes() -> None:
    client = Nozle(
        "sk_test",
        base_url="https://engine.example/",
        events_url="https://core.example/",
    )

    assert client.api_key == "sk_test"
    assert client.base_url == "https://engine.example"
    assert client.events_url == "https://core.example"
    assert client.customers is not None
    assert client.credit_systems is not None
    assert client.credits is not None
    assert client.entities is not None
    assert client.entity_subscriptions is not None
    assert client.events is not None
    assert client.cost_events is not None
    assert client.usage is not None
    assert client.margin is not None


@pytest.mark.parametrize("api_key", ["pk_browser", "sk_backend"])
def test_plans_accept_catalog_and_secret_keys(
    requests_mock: requests_mock.Mocker, api_key: str
) -> None:
    requests_mock.get(
        "https://api.example/engine/api/v1/plans",
        json={
            "plans": [
                {
                    "code": "pro",
                    "name": "Pro",
                    "amount_cents": 4900,
                    "amount_currency": "USD",
                    "interval": "monthly",
                }
            ]
        },
    )

    plans = Nozle(api_key, base_url="https://api.example/engine").plans()

    assert plans[0]["code"] == "pro"
    assert requests_mock.last_request.headers["Authorization"] == f"Bearer {api_key}"


def test_plans_reject_unknown_key_type_before_network(
    requests_mock: requests_mock.Mocker,
) -> None:
    with pytest.raises(NozleAuthenticationError, match="pk_.*sk_"):
        Nozle("token_other").plans()
    assert requests_mock.request_history == []


def test_track_with_explicit_subscription_and_timestamp(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post("https://api.example/core/api/v1/events", json={})
    client = Nozle("sk_test", events_url="https://api.example/core")

    client.track(
        "cust_1",
        "api_call",
        metadata={"tokens": 100},
        subscription_id="sub_1",
        transaction_id="tx_1",
        timestamp="2026-07-20T12:00:00.750Z",
    )

    event = requests_mock.last_request.json()["event"]
    assert event == {
        "transaction_id": "tx_1",
        "external_customer_id": "cust_1",
        "code": "api_call",
        "properties": {"tokens": 100},
        "external_subscription_id": "sub_1",
        "timestamp": "2026-07-20T12:00:00.750Z",
    }


def test_track_generates_transaction_id(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post("https://api.nozle.app/core/api/v1/events", text="accepted")

    transaction_id = Nozle("sk_test").track("cust_1", "api_call", subscription_id="sub_1")

    assert len(requests_mock.last_request.json()["event"]["transaction_id"]) == 36
    assert transaction_id == requests_mock.last_request.json()["event"]["transaction_id"]
    assert UUID(transaction_id).version == 7


def test_event_and_cost_event_identifier_helpers() -> None:
    client = Nozle("sk_test")

    assert UUID(client.events.create_transaction_id()).version == 7
    assert UUID(client.cost_events.create_cost_event_id()).version == 7


def test_cost_event_track(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(
        "https://api.example/engine/api/v1/cost-events",
        status_code=202,
        json={"status": "accepted", "cost_event_id": "cost_123"},
    )
    client = Nozle("sk_test", base_url="https://api.example/engine")

    result = client.cost_events.track(
        cost_event_id="cost_123",
        cost_meter_code="ai_tokens",
        parent_transaction_id="feature_123",
        external_customer_id="customer_123",
        request_id="provider_123",
        operation_key="planning",
        properties={"tokens": 900},
        timestamp=1788345001,
    )

    assert result == {"status": "accepted", "cost_event_id": "cost_123"}
    assert requests_mock.last_request.json() == {
        "cost_event_id": "cost_123",
        "cost_meter_code": "ai_tokens",
        "parent_transaction_id": "feature_123",
        "external_customer_id": "customer_123",
        "request_id": "provider_123",
        "operation_key": "planning",
        "properties": {"tokens": 900},
        "timestamp": 1788345001,
    }


def test_cost_event_track_rejects_invalid_calls_before_network(
    requests_mock: requests_mock.Mocker,
) -> None:
    with pytest.raises(NozleValidationError, match="parent_transaction_id"):
        Nozle("sk_test").cost_events.track(cost_meter_code="email")
    with pytest.raises(NozleAuthenticationError, match="secret key"):
        Nozle("pk_browser").cost_events.track(
            cost_meter_code="email", external_customer_id="customer_123"
        )

    assert requests_mock.request_history == []


def test_track_resolves_and_caches_subscription(requests_mock: requests_mock.Mocker) -> None:
    lookup = requests_mock.get(
        "https://api.example/core/api/v1/subscriptions",
        json={"subscriptions": [{"external_id": "sub_auto"}]},
    )
    events = requests_mock.post("https://api.example/core/api/v1/events", json={})
    client = Nozle("sk_test", events_url="https://api.example/core")

    client.track("cust_1", "event_1")
    client.track("cust_1", "event_2")

    assert lookup.call_count == 1
    assert events.call_count == 2
    assert requests_mock.request_history[0].qs == {
        "external_customer_id": ["cust_1"],
        "status[]": ["active"],
    }
    assert requests_mock.request_history[1].json()["event"]["external_subscription_id"] == (
        "sub_auto"
    )


@pytest.mark.parametrize(
    ("subscriptions", "message"),
    [([], "no active subscription"), ([{"external_id": "a"}, {"external_id": "b"}], "2 active")],
)
def test_track_rejects_ambiguous_subscription_lookup(
    requests_mock: requests_mock.Mocker,
    subscriptions: list[dict[str, str]],
    message: str,
) -> None:
    requests_mock.get(
        "https://api.nozle.app/core/api/v1/subscriptions",
        json={"subscriptions": subscriptions},
    )

    with pytest.raises(NozleAPIError, match=message):
        Nozle("sk_test").track("cust_1", "event")


def test_can_sends_metadata_as_json_query(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(
        "https://api.example/engine/api/v1/can",
        json={
            "allowed": True,
            "used": 5,
            "economics": {
                "status": "estimated",
                "currency": "USD",
                "estimated_incremental_cost": "0.01",
                "estimated_incremental_revenue": "0.02",
                "estimated_incremental_margin": "0.01",
                "estimated_margin_percent": "50",
                "calculated_at": "2026-09-02T12:00:00Z",
            },
        },
    )

    result = Nozle("sk_test", base_url="https://api.example/engine").can(
        "cust_1", "code_completion", {"model": "gpt-5"}
    )

    assert result["allowed"] is True
    assert requests_mock.last_request.qs == {
        "customer_id": ["cust_1"],
        "feature": ["code_completion"],
        "metadata": ['{"model": "gpt-5"}'],
    }


@pytest.mark.parametrize(
    "result",
    [
        {"type": "stripe", "client_secret": "cs_test"},
        {"type": "stripe", "url": "https://checkout.stripe.example/session"},
        {"type": "completed", "status": "active", "subscription_id": "sub_1"},
        {"type": "scheduled", "status": "scheduled", "plan_code": "starter"},
    ],
)
def test_checkout_preserves_every_result_variant_and_uses_return_url(
    requests_mock: requests_mock.Mocker, result: dict[str, object]
) -> None:
    requests_mock.post("https://api.example/engine/api/v1/checkout", json=result)
    client = Nozle("sk_test", base_url="https://api.example/engine")

    response = client.checkout(
        "cust_1", "pro", return_url="https://merchant.example/billing/complete"
    )

    assert response == result
    assert requests_mock.last_request.json() == {
        "plan_code": "pro",
        "customer_id": "cust_1",
        "return_url": "https://merchant.example/billing/complete",
    }


def test_checkout_accepts_deprecated_success_url_but_never_sends_both(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        "https://api.nozle.app/engine/api/v1/checkout",
        json={"type": "stripe", "client_secret": "cs_test"},
    )
    client = Nozle("sk_test")

    with pytest.deprecated_call(match="success_url"):
        client.checkout("cust_1", "pro", success_url="https://merchant.example/done")

    assert requests_mock.last_request.json() == {
        "plan_code": "pro",
        "customer_id": "cust_1",
        "return_url": "https://merchant.example/done",
    }


def test_checkout_rejects_conflicting_return_alias_before_network(
    requests_mock: requests_mock.Mocker,
) -> None:
    with pytest.raises(NozleValidationError, match="conflicting"):
        Nozle("sk_test").checkout(
            "cust_1",
            "pro",
            return_url="https://merchant.example/a",
            success_url="https://merchant.example/b",
        )
    assert requests_mock.request_history == []


def test_subscribe_ping_customer_and_check_and_deduct_contracts(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        "https://api.example/engine/api/v1/subscribe",
        json={"subscription_id": "sub_1", "status": "active"},
    )
    requests_mock.get(
        "https://api.example/engine/api/v1/ping",
        json={"ok": True, "engine": "ok", "version": "1"},
    )
    customer_request = requests_mock.post(
        "https://api.example/core/api/v1/customers",
        json={"customer": {"external_id": "cust_1", "name": "Acme"}},
    )
    requests_mock.post(
        "https://api.example/engine/api/v1/check-and-deduct",
        json={"allowed": True, "remaining": 95},
    )
    client = Nozle(
        "sk_test",
        base_url="https://api.example/engine",
        events_url="https://api.example/core",
    )

    assert client.subscribe("cust_1", "pro")["subscription_id"] == "sub_1"
    assert requests_mock.last_request.json() == {"plan_code": "pro", "customer_id": "cust_1"}
    assert client.ping()["ok"] is True
    assert client.customers.upsert("cust_1", name="Acme")["name"] == "Acme"
    assert customer_request.called_once
    assert customer_request.last_request.headers["Authorization"] == "Bearer sk_test"
    assert customer_request.last_request.json() == {
        "customer": {"external_id": "cust_1", "name": "Acme"}
    }
    assert client.check_and_deduct("cust_1", "completion", 5)["remaining"] == 95
    assert requests_mock.last_request.json() == {
        "customer_id": "cust_1",
        "feature": "completion",
        "credits": 5,
    }


def test_customer_upsert_never_uses_engine(
    requests_mock: requests_mock.Mocker,
) -> None:
    core = requests_mock.post(
        "https://api.example/core/api/v1/customers",
        json={"customer": {"external_id": "cust_1"}},
    )
    client = Nozle(
        "sk_merchant",
        base_url="https://api.example/engine",
        events_url="https://api.example/core",
    )

    assert client.customers.upsert("cust_1")["external_id"] == "cust_1"
    assert core.called_once
    assert all("engine.example" not in request.url for request in requests_mock.request_history)


def test_cancel_subscription_defaults_to_end_of_period(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.delete(
        "https://api.example/engine/api/v1/subscriptions/sub%2F1",
        json={
            "subscription": {
                "external_id": "sub/1",
                "status": "active",
                "ending_at": "2026-08-14T18:30:00Z",
            }
        },
    )
    client = Nozle("sk_test", base_url="https://api.example/engine")

    result = client.cancel_subscription("customer 1", "sub/1")

    assert result["subscription"]["status"] == "active"
    assert result["subscription"]["ending_at"] == "2026-08-14T18:30:00Z"
    assert requests_mock.last_request.qs == {
        "customer_id": ["customer 1"],
        "cancellation_policy": ["end_of_period"],
    }


def test_cancel_subscription_supports_explicit_immediate_policy(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.delete(
        "https://api.nozle.app/engine/api/v1/subscriptions/sub_1",
        json={"subscription": {"external_id": "sub_1", "status": "terminated"}},
    )

    Nozle("sk_test").cancel_subscription("cust_1", "sub_1", policy="immediate")

    assert requests_mock.last_request.qs["cancellation_policy"] == ["immediate"]


def test_cancel_subscription_rejects_invalid_policy_before_network(
    requests_mock: requests_mock.Mocker,
) -> None:
    with pytest.raises(NozleValidationError, match="policy"):
        Nozle("sk_test").cancel_subscription(  # type: ignore[arg-type]
            "cust_1", "sub_1", policy="whenever"
        )
    assert requests_mock.request_history == []


def test_cancel_subscription_rejects_publishable_key_before_network(
    requests_mock: requests_mock.Mocker,
) -> None:
    with pytest.raises(NozleValidationError, match="secret key"):
        Nozle("pk_browser").cancel_subscription("cust_1", "sub_1")
    assert requests_mock.request_history == []


def test_subscription_transition_preview_leaves_defaults_to_merchant_policy(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        "https://api.example/engine/api/v1/subscriptions/transitions/preview",
        json={"subscription_transition": {"amount_due_cents": 0}},
    )
    client = Nozle("sk_test", base_url="https://api.example/engine")

    client.preview_subscription_transition(
        {
            "customer_id": "customer-1",
            "subscription_id": "sub-1",
            "operation": "cancel",
            "timing": "end_of_period",
        }
    )

    assert requests_mock.last_request.json() == {
        "customer_id": "customer-1",
        "subscription_id": "sub-1",
        "operation": "cancel",
        "timing": "end_of_period",
    }


def test_subscription_transition_apply_forwards_idempotency(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        "https://api.example/engine/api/v1/subscriptions/transitions",
        json={"subscription_transition": {"id": "transition-1"}},
    )
    client = Nozle("sk_test", base_url="https://api.example/engine")

    client.apply_subscription_transition(
        {
            "customer_id": "customer-1",
            "subscription_id": "sub-1",
            "operation": "downgrade",
            "timing": "immediate",
            "target_plan_code": "starter",
            "credit_action": "refund",
            "refund_mode": "full",
            "final_invoice_action": "generate",
        },
        idempotency_key="downgrade-1",
    )

    assert requests_mock.last_request.headers["Idempotency-Key"] == "downgrade-1"


def test_subscription_transition_uncancel_has_no_settlement_options(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        "https://api.example/engine/api/v1/subscriptions/transitions",
        json={"subscription_transition": {"id": "transition-2"}},
    )

    Nozle("sk_test", base_url="https://api.example/engine").apply_subscription_transition(
        {
            "customer_id": "customer-1",
            "subscription_id": "sub-1",
            "operation": "uncancel",
        },
        idempotency_key="uncancel-1",
    )

    assert requests_mock.last_request.json() == {
        "customer_id": "customer-1",
        "subscription_id": "sub-1",
        "operation": "uncancel",
    }


def test_subscription_transition_rejects_unsafe_shape_before_network(
    requests_mock: requests_mock.Mocker,
) -> None:
    with pytest.raises(NozleValidationError, match="forbidden"):
        Nozle("sk_test").apply_subscription_transition(
            {
                "customer_id": "customer-1",
                "subscription_id": "sub-1",
                "operation": "cancel",
                "timing": "immediate",
                "target_plan_code": "starter",
            },
            idempotency_key="transition-1",
        )
    assert requests_mock.request_history == []


def test_margin_routes_and_trend_query(requests_mock: requests_mock.Mocker) -> None:
    for path in ("summary", "customers", "metrics", "plans", "models", "trend"):
        requests_mock.get(f"https://api.example/engine/api/v1/margin/{path}", json={"path": path})
    margin = Nozle("sk_test", base_url="https://api.example/engine").margin

    assert margin.summary(from_date="2026-01-01", unused=None)["path"] == "summary"
    assert requests_mock.request_history[0].qs == {"from_date": ["2026-01-01"]}
    assert margin.by_customer()["path"] == "customers"
    assert margin.by_metric()["path"] == "metrics"
    assert margin.by_plan()["path"] == "plans"
    assert margin.by_model()["path"] == "models"
    assert margin.trend(granularity="week")["path"] == "trend"
    assert requests_mock.last_request.qs == {"granularity": ["week"]}


@pytest.mark.parametrize(
    "invoke",
    [
        lambda client: client.track("c", "e", subscription_id="s"),
        lambda client: client.can("c", "f"),
        lambda client: client.checkout("c", "p"),
        lambda client: client.subscribe("c", "p"),
        lambda client: client.ping(),
        lambda client: client.check_and_deduct("c", "f", 1),
        lambda client: client.customers.upsert("c"),
        lambda client: client.margin.summary(),
        lambda client: client.credit_systems.list(),
        lambda client: client.credits.list_balances("c"),
        lambda client: client.entities.list("c"),
        lambda client: client.usage.check("c", "metric"),
    ],
)
def test_publishable_key_rejects_every_protected_operation_before_network(
    requests_mock: requests_mock.Mocker,
    invoke: Callable[[Nozle], object],
) -> None:
    with pytest.raises(NozleAuthenticationError, match="requires a secret key"):
        invoke(Nozle("pk_browser"))
    assert requests_mock.request_history == []


def test_structured_api_error_is_safe_and_mutations_are_not_retried(
    requests_mock: requests_mock.Mocker,
) -> None:
    api_key = "sk_do_not_leak"
    matcher = requests_mock.post(
        "https://api.example/engine/api/v1/checkout",
        status_code=503,
        json={
            "error": "temporarily unavailable",
            "api_key": api_key,
            "message": f"failed for {api_key}",
        },
    )

    with pytest.raises(NozleAPIError) as raised:
        Nozle(api_key, base_url="https://api.example/engine").checkout("cust", "pro")

    error = raised.value
    assert error.operation == "checkout"
    assert error.status_code == 503
    assert error.response_details["api_key"] == "[REDACTED]"
    assert api_key not in str(error)
    assert matcher.call_count == 1
