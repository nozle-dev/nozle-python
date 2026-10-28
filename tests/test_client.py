from __future__ import annotations

from importlib.metadata import version
from typing import Callable, Optional
from uuid import UUID

import pytest
import requests_mock

from nozle import (
    CanResult,
    Nozle,
    NozleAPIError,
    NozleAuthenticationError,
    NozleTransportError,
    NozleValidationError,
    __version__,
)


def test_version_metadata_and_runtime_match() -> None:
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
    requests_mock.post("https://api.example/engine/api/v1/events", json={})
    client = Nozle(
        "sk_test", base_url="https://api.example/engine", events_url="https://api.example/core"
    )

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
        "timestamp": "1784548800.750",
    }


def test_track_generates_transaction_id(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post("https://api.nozle.app/engine/api/v1/events", text="accepted")

    transaction_id = Nozle("sk_test").track("cust_1", "api_call", subscription_id="sub_1")

    assert len(requests_mock.last_request.json()["event"]["transaction_id"]) == 36
    assert transaction_id == requests_mock.last_request.json()["event"]["transaction_id"]
    assert UUID(transaction_id).version == 7


def test_track_errors_carry_the_transaction_id_for_a_safe_retry(
    requests_mock: requests_mock.Mocker,
) -> None:
    import requests as http

    matcher = requests_mock.post(
        "https://api.example/engine/api/v1/events",
        [
            {"status_code": 502, "json": {}},
            {"exc": http.ConnectTimeout("timed out")},
            {"status_code": 200, "text": "accepted"},
        ],
    )
    client = Nozle(
        "sk_test", base_url="https://api.example/engine", events_url="https://api.example/core"
    )

    with pytest.raises(NozleAPIError) as api_error:
        client.track("cust_1", "api_call", subscription_id="sub_1")
    generated = api_error.value.transaction_id
    assert generated is not None and UUID(generated).version == 7
    with pytest.raises(NozleTransportError) as network_error:
        client.track("cust_1", "api_call", subscription_id="sub_1", transaction_id=generated)
    assert network_error.value.transaction_id == generated
    assert (
        client.track("cust_1", "api_call", subscription_id="sub_1", transaction_id=generated)
        == generated
    )

    sent = [request.json()["event"]["transaction_id"] for request in matcher.request_history]
    assert sent == [generated] * 3


def test_track_preserves_numeric_timestamp_and_rejects_invalid_value(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post("https://api.example/engine/api/v1/events", json={})
    client = Nozle(
        "sk_test", base_url="https://api.example/engine", events_url="https://api.example/core"
    )

    client.track("cust_1", "api_call", subscription_id="sub_1", timestamp="1784548800.123")
    assert requests_mock.last_request.json()["event"]["timestamp"] == "1784548800.123"

    with pytest.raises(NozleValidationError, match="timestamp must be RFC3339 or Unix seconds"):
        client.track("cust_1", "api_call", subscription_id="sub_1", timestamp="not-a-timestamp")
    assert requests_mock.call_count == 1


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
    events = requests_mock.post("https://api.example/engine/api/v1/events", json={})
    client = Nozle(
        "sk_test", base_url="https://api.example/engine", events_url="https://api.example/core"
    )

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


def test_can_sends_subscription_and_entity_selectors(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.get("https://api.example/engine/api/v1/can", json={"allowed": True, "used": 1})
    client = Nozle("sk_test", base_url="https://api.example/engine")

    client.can("cust_1", "seats", subscription_id="sub_a", entity_id="ent_1")
    assert requests_mock.last_request.qs == {
        "customer_id": ["cust_1"],
        "feature": ["seats"],
        "subscription_id": ["sub_a"],
        "entity_id": ["ent_1"],
    }

    client.can("cust_1", "seats")
    assert set(requests_mock.last_request.qs) == {"customer_id", "feature"}


@pytest.mark.parametrize(
    ("subscriptions", "code"),
    [
        ([], "active_subscription_not_found"),
        ([{"external_id": "a"}, {"external_id": "b"}], "active_subscription_ambiguous"),
    ],
)
def test_track_subscription_lookup_failure_has_the_engine_code(
    requests_mock: requests_mock.Mocker,
    subscriptions: list[dict[str, str]],
    code: str,
) -> None:
    requests_mock.get(
        "https://api.nozle.app/core/api/v1/subscriptions",
        json={"subscriptions": subscriptions},
    )

    with pytest.raises(NozleAPIError) as caught:
        Nozle("sk_test").track("cust_1", "event")
    assert caught.value.status_code == 422
    assert caught.value.code == code
    assert caught.value.has_code(code)


@pytest.mark.parametrize(
    "limit,remaining",
    [(None, None), (100, 97)],
    ids=["unlimited", "capped"],
)
def test_can_limit_and_remaining_may_be_null(
    requests_mock: requests_mock.Mocker, limit: Optional[int], remaining: Optional[int]
) -> None:
    # Engine always sends limit and remaining; both are null when the feature has no usage limit.
    expected: CanResult = {"allowed": True, "used": 3, "limit": limit, "remaining": remaining}
    requests_mock.get("https://api.example/engine/api/v1/can", json=expected)

    result = Nozle(
        "sk_test", base_url="https://api.example/engine", events_url="https://api.example/core"
    ).can("cust_1", "seats")

    assert result == expected
    sent_limit: Optional[int] = result.get("limit")
    assert sent_limit == limit


@pytest.mark.parametrize(
    "result",
    [
        {
            "type": "hosted",
            "url": "https://checkout.stripe.com/c/pay/cs_test",
            "payment_url": "https://checkout.stripe.com/c/pay/cs_test",
            "checkout_id": "cs_test",
        },
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


def test_checkout_sends_cancel_url_and_keeps_the_session_id_placeholder(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        "https://api.example/engine/api/v1/checkout",
        json={"type": "hosted", "url": "https://checkout.stripe.com/c/pay/cs_test"},
    )
    client = Nozle("sk_test", base_url="https://api.example/engine")

    client.checkout(
        "cust_1",
        "pro",
        return_url="https://merchant.example/done?s={CHECKOUT_SESSION_ID}",
        cancel_url="https://merchant.example/pricing",
    )
    assert requests_mock.last_request.json() == {
        "plan_code": "pro",
        "customer_id": "cust_1",
        "return_url": "https://merchant.example/done?s={CHECKOUT_SESSION_ID}",
        "cancel_url": "https://merchant.example/pricing",
    }

    client.checkout_invoice("inv_1", cancel_url="https://merchant.example/pricing")
    assert requests_mock.last_request.json() == {
        "invoice_id": "inv_1",
        "cancel_url": "https://merchant.example/pricing",
    }


def test_checkout_accepts_deprecated_success_url_but_never_sends_both(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        "https://api.nozle.app/engine/api/v1/checkout",
        json={"type": "hosted", "url": "https://checkout.stripe.com/c/pay/cs_test"},
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
    assert client.check_and_deduct("cust_1", credits=5)["remaining"] == 95
    assert requests_mock.last_request.json() == {"customer_id": "cust_1", "credits": 5}
    # Old callers still pass a feature: it is accepted but never sent.
    for call in (
        lambda: client.check_and_deduct("cust_1", "completion", 5),
        lambda: client.check_and_deduct("cust_1", feature="completion", credits=5),
        lambda: client.check_and_deduct("cust_1", 5),
    ):
        assert call()["remaining"] == 95
        assert requests_mock.last_request.json() == {"customer_id": "cust_1", "credits": 5}


@pytest.mark.parametrize("args", [(), ("completion",), (None, None), ("completion", True)])
def test_check_and_deduct_requires_credits(args: tuple) -> None:
    client = Nozle("sk_test", base_url="https://api.example/engine")
    with requests_mock.Mocker() as m:
        with pytest.raises(NozleValidationError, match="requires credits"):
            client.check_and_deduct("cust_1", *args)
        assert m.request_history == []


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


def test_customer_upsert_links_existing_provider_customer(
    requests_mock: requests_mock.Mocker,
) -> None:
    core = requests_mock.post(
        "https://api.example/core/api/v1/customers",
        json={"customer": {"external_id": "acme"}},
    )
    client = Nozle("sk_merchant", events_url="https://api.example/core")

    client.customers.upsert(
        "acme",
        billing_configuration={
            "payment_provider": "stripe",
            "payment_provider_code": "main",
            "provider_customer_id": "cus_123",
        },
    )

    assert core.last_request.json() == {
        "customer": {
            "external_id": "acme",
            "billing_configuration": {
                "payment_provider": "stripe",
                "payment_provider_code": "main",
                "provider_customer_id": "cus_123",
            },
        }
    }


def test_customer_upsert_requires_payment_provider_before_network_io() -> None:
    client = Nozle("sk_merchant", events_url="https://api.example/core")
    with requests_mock.Mocker() as m:
        with pytest.raises(
            NozleValidationError, match="requires billing_configuration.payment_provider"
        ):
            client.customers.upsert(
                "acme",
                billing_configuration={"payment_provider": "", "provider_customer_id": "cus_123"},
            )
        assert m.request_history == []


@pytest.mark.parametrize(
    ("method", "http", "path", "call"),
    [
        ("subscribe", "post", "/engine/api/v1/subscribe", lambda c: c.subscribe("cust_1", "pro")),
        (
            "checkout",
            "post",
            "/engine/api/v1/checkout",
            lambda c: c.checkout("cust_1", "pro", "https://app.example/done"),
        ),
        (
            "cancel_subscription",
            "delete",
            "/engine/api/v1/subscriptions/sub_old",
            lambda c: c.cancel_subscription("cust_1", "sub_old"),
        ),
        (
            "apply_subscription_transition",
            "post",
            "/engine/api/v1/subscriptions/transitions",
            lambda c: c.apply_subscription_transition(
                {
                    "customer_id": "cust_1",
                    "subscription_id": "sub_old",
                    "operation": "cancel",
                    "timing": "immediate",
                },
                idempotency_key="k1",
            ),
        ),
    ],
)
def test_subscription_changing_calls_refresh_the_cached_subscription(
    requests_mock: requests_mock.Mocker,
    method: str,
    http: str,
    path: str,
    call: Callable[[Nozle], object],
) -> None:
    lookup = requests_mock.get(
        "https://api.example/core/api/v1/subscriptions",
        [
            {"json": {"subscriptions": [{"external_id": "sub_old"}]}},
            {"json": {"subscriptions": [{"external_id": "sub_new"}]}},
        ],
    )
    events = requests_mock.post("https://api.example/engine/api/v1/events", json={})
    getattr(requests_mock, http)("https://api.example" + path, json={})
    client = Nozle(
        "sk_test", base_url="https://api.example/engine", events_url="https://api.example/core"
    )

    client.track("cust_1", "event_1")
    call(client)
    client.track("cust_1", "event_2")

    assert lookup.call_count == 2, method
    assert [r.json()["event"]["external_subscription_id"] for r in events.request_history] == [
        "sub_old",
        "sub_new",
    ]


def test_cancel_subscription_without_timing_follows_the_organization(
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
    assert requests_mock.last_request.qs == {"customer_id": ["customer 1"]}


def test_cancel_subscription_forwards_settlement_options(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.delete(
        "https://api.nozle.app/engine/api/v1/subscriptions/sub_1",
        json={"subscription": {"external_id": "sub_1", "status": "terminated"}},
    )

    Nozle("sk_test").cancel_subscription(
        "cust_1",
        "sub_1",
        "immediate",
        credit_action="refund",
        refund_mode="full",
        final_invoice_action="skip",
    )

    assert requests_mock.last_request.qs == {
        "customer_id": ["cust_1"],
        "cancellation_policy": ["immediate"],
        "credit_action": ["refund"],
        "refund_mode": ["full"],
        "final_invoice_action": ["skip"],
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
        lambda client: client.check_and_deduct("c", credits=1),
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


def test_host_derives_both_service_urls_and_explicit_urls_win():
    client = Nozle("sk_test", host="http://localhost:8888//")
    assert client.base_url == "http://localhost:8888/engine"
    assert client.events_url == "http://localhost:8888/core"

    client = Nozle("sk_test", events_url="https://core.example/", host="http://localhost:8888")
    assert client.base_url == "http://localhost:8888/engine"
    assert client.events_url == "https://core.example"
    assert Nozle("sk_test").base_url == "https://api.nozle.app/engine"


def test_api_key_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("NOZLE_SECRET_KEY", "sk_env")
    assert Nozle().api_key == "sk_env"
    assert Nozle("sk_explicit").api_key == "sk_explicit"
    monkeypatch.delenv("NOZLE_SECRET_KEY")
    with pytest.raises(ValueError, match="NOZLE_SECRET_KEY"):
        Nozle()


def test_api_error_carries_request_id_and_retry_after(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(
        "https://api.example/engine/api/v1/plans",
        status_code=503,
        json={},
        headers={"X-Request-Id": "req-1", "Retry-After": "30"},
    )
    with pytest.raises(NozleAPIError) as raised:
        Nozle("sk_test", base_url="https://api.example/engine").plans()
    assert raised.value.request_id == "req-1"
    assert raised.value.retry_after == 30


def test_retry_after_accepts_a_date_and_ignores_a_bad_value(
    requests_mock: requests_mock.Mocker,
) -> None:
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    soon = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
    url = "https://api.example/engine/api/v1/plans"
    requests_mock.get(
        url,
        [
            {"status_code": 429, "json": {}, "headers": {"Retry-After": soon}},
            {"status_code": 429, "json": {}, "headers": {"Retry-After": "soon"}},
            {"status_code": 429, "json": {}},
        ],
    )
    client = Nozle("sk_test", base_url="https://api.example/engine")
    with pytest.raises(NozleAPIError) as dated:
        client.plans()
    assert 0 < dated.value.retry_after <= 20
    with pytest.raises(NozleAPIError) as bad:
        client.plans()
    assert bad.value.retry_after is None
    with pytest.raises(NozleAPIError) as missing:
        client.plans()
    assert missing.value.request_id is None and missing.value.retry_after is None


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list:
    waits: list = []
    monkeypatch.setattr("nozle._transport.time.sleep", waits.append)
    return waits


def test_retries_are_off_by_default(requests_mock: requests_mock.Mocker) -> None:
    matcher = requests_mock.get("https://api.example/engine/api/v1/plans", status_code=503, json={})
    with pytest.raises(NozleAPIError):
        Nozle("sk_test", base_url="https://api.example/engine").plans()
    assert matcher.call_count == 1


def test_a_read_is_retried_after_503_429_and_a_network_error(
    requests_mock: requests_mock.Mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests as http

    _no_sleep(monkeypatch)
    matcher = requests_mock.get(
        "https://api.example/engine/api/v1/plans",
        [
            {"status_code": 503, "json": {}},
            {"status_code": 429, "json": {}},
            {"exc": http.ConnectionError("down")},
            {"status_code": 200, "json": {"plans": []}},
        ],
    )
    client = Nozle("sk_test", base_url="https://api.example/engine", max_retries=3)
    assert client.plans() == []
    assert matcher.call_count == 4


def test_retries_stop_at_max_retries_and_skip_a_4xx(
    requests_mock: requests_mock.Mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_sleep(monkeypatch)
    url = "https://api.example/engine/api/v1/plans"
    matcher = requests_mock.get(url, status_code=500, json={})
    client = Nozle("sk_test", base_url="https://api.example/engine", max_retries=2)
    with pytest.raises(NozleAPIError) as raised:
        client.plans()
    assert raised.value.status_code == 500 and matcher.call_count == 3
    matcher = requests_mock.get(url, status_code=422, json={})
    with pytest.raises(NozleAPIError):
        client.plans()
    assert matcher.call_count == 1


def test_retry_waits_for_retry_after(
    requests_mock: requests_mock.Mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits = _no_sleep(monkeypatch)
    requests_mock.get(
        "https://api.example/engine/api/v1/plans",
        [
            {"status_code": 503, "json": {}, "headers": {"Retry-After": "2"}},
            {"status_code": 200, "json": {"plans": []}},
        ],
    )
    Nozle("sk_test", base_url="https://api.example/engine", max_retries=1).plans()
    assert waits == [2.0]


def test_a_write_is_retried_only_when_it_is_safe_to_repeat(
    requests_mock: requests_mock.Mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_sleep(monkeypatch)
    client = Nozle("sk_test", base_url="https://api.example/engine", max_retries=2)
    checkout = requests_mock.post(
        "https://api.example/engine/api/v1/subscribe", status_code=503, json={}
    )
    with pytest.raises(NozleAPIError):
        client.subscribe("cust", "pro")
    assert checkout.call_count == 1
    usage = requests_mock.post(
        "https://api.example/engine/api/v1/usage/track", status_code=503, json={}
    )
    with pytest.raises(NozleAPIError):
        client.usage.track("cust", "f", idempotency_key="k1", timestamp="2026-10-15T10:00:00Z")
    assert usage.call_count == 3


def test_usage_track_reuses_its_timestamp_for_a_retry_of_the_same_key(
    requests_mock: requests_mock.Mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter(f"2026-10-15T10:00:0{i}.000Z" for i in range(9))
    monkeypatch.setattr("nozle.usage.utc_now_rfc3339_milliseconds", lambda: next(clock))
    matcher = requests_mock.post("https://api.example/engine/api/v1/usage/track", json={})
    client = Nozle("sk_test", base_url="https://api.example/engine")

    for key in ("k1", "k1", "k2"):
        client.usage.track("cust", "emails", idempotency_key=key)
    client.usage.track("cust", "emails", idempotency_key="k3", timestamp="2026-01-01T00:00:00Z")

    stamps = [call.json()["timestamp"] for call in matcher.request_history]
    assert stamps[0] == stamps[1]
    assert stamps[2] != stamps[0]
    assert stamps[3] == "2026-01-01T00:00:00Z"


def test_api_error_message_carries_the_server_text(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(
        "https://api.example/engine/api/v1/margin/summary",
        status_code=400,
        json={"code": "invalid_date_range", "error": "from must be before to"},
    )
    with pytest.raises(NozleAPIError) as raised:
        Nozle("sk_test", base_url="https://api.example/engine").margin.summary()
    assert "from must be before to" in str(raised.value)
    assert raised.value.has_code("invalid_date_range")


def test_no_active_subscription_is_one_code() -> None:
    from nozle import NO_ACTIVE_SUBSCRIPTION

    core = NozleAPIError("x", 404, {"code": "subscription_not_found"})
    sdk = NozleAPIError("x", 422, {"code": NO_ACTIVE_SUBSCRIPTION})
    assert core.has_code(NO_ACTIVE_SUBSCRIPTION) and sdk.has_code(NO_ACTIVE_SUBSCRIPTION)
    assert not NozleAPIError("x", 404, {"code": "other"}).has_code(NO_ACTIVE_SUBSCRIPTION)


def test_can_accepts_number_and_boolean_metadata(requests_mock: requests_mock.Mocker) -> None:
    matcher = requests_mock.get(
        "https://api.example/engine/api/v1/can", json={"allowed": True, "used": 0}
    )
    Nozle("sk_test", base_url="https://api.example/engine").can(
        "cust_1", "feature", {"model": "gpt", "tokens": 5, "cached": True}
    )
    assert matcher.last_request.qs["metadata"] == ['{"model": "gpt", "tokens": 5, "cached": true}']


def test_margin_results_are_typed(requests_mock: requests_mock.Mocker) -> None:
    from nozle import MarginSummary

    requests_mock.get(
        "https://api.example/engine/api/v1/margin/summary",
        json={
            "total_revenue_cents": 100,
            "total_cost_cents": 40,
            "total_margin_cents": 60,
            "margin_percent": 60.0,
            "event_count": 3,
        },
    )
    summary: MarginSummary = Nozle(
        "sk_test", base_url="https://api.example/engine"
    ).margin.summary()
    assert summary["total_margin_cents"] == 60


def test_plans_send_currency_and_customer_and_return_family_and_prices(
    requests_mock: requests_mock.Mocker,
) -> None:
    plan = {
        "code": "pro_eur",
        "name": "Pro",
        "amount_cents": 4500,
        "amount_currency": "EUR",
        "interval": "monthly",
        "family": {"code": "pro", "currencies": ["USD", "EUR"], "default_currency": "USD"},
        "prices": [
            {"currency": "USD", "interval": "monthly", "plan_code": "pro", "amount_cents": 4900},
            {
                "currency": "EUR",
                "interval": "monthly",
                "plan_code": "pro_eur",
                "amount_cents": 4500,
            },
        ],
    }
    requests_mock.get("https://api.example/engine/api/v1/plans", json={"plans": [plan]})
    client = Nozle("sk_test", base_url="https://api.example/engine")

    assert client.plans(currency="EUR", external_customer_id="cus 1") == [plan]
    assert requests_mock.last_request.qs == {"currency": ["eur"], "external_customer_id": ["cus 1"]}
    client.plans()
    assert requests_mock.last_request.query == ""


def test_checkout_and_subscribe_by_plan_family(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(
        "https://api.example/engine/api/v1/checkout", json={"type": "completed", "status": "ok"}
    )
    requests_mock.post(
        "https://api.example/engine/api/v1/subscribe",
        json={"subscription_id": "s", "status": "active"},
    )
    client = Nozle("sk_test", base_url="https://api.example/engine")

    client.checkout(
        "cust",
        return_url="https://app.example/done",
        plan_family_code="pro",
        interval="yearly",
        currency="EUR",
    )
    assert requests_mock.last_request.json() == {
        "plan_family_code": "pro",
        "interval": "yearly",
        "currency": "EUR",
        "customer_id": "cust",
        "return_url": "https://app.example/done",
    }
    client.subscribe("cust", plan_family_code="pro", interval="monthly")
    assert requests_mock.last_request.json() == {
        "plan_family_code": "pro",
        "interval": "monthly",
        "customer_id": "cust",
    }


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"plan_code": "pro", "plan_family_code": "pro", "interval": "monthly"}, "not both"),
        ({"plan_family_code": "pro"}, "requires interval"),
        ({"plan_code": "pro", "currency": "EUR"}, "only with plan_family_code"),
        ({}, "requires plan_code"),
    ],
)
def test_plan_family_selection_is_validated_before_network(
    requests_mock: requests_mock.Mocker, kwargs: dict, message: str
) -> None:
    client = Nozle("sk_test", base_url="https://api.example/engine")
    with pytest.raises(NozleValidationError, match=message):
        client.checkout("cust", **kwargs)
    with pytest.raises(NozleValidationError, match=message):
        client.subscribe("cust", **kwargs)
    assert requests_mock.request_history == []
