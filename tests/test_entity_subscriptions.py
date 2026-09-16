from __future__ import annotations

import pytest
import requests_mock

from nozle import Nozle, NozleAuthenticationError, NozleValidationError


def entity_subscription() -> dict[str, object]:
    return {
        "external_customer_id": "workspace/1",
        "external_entity_id": "user/42",
        "external_subscription_id": "entity-sub-1",
        "status": "active",
        "current_plan": {
            "code": "pro",
            "name": "Pro",
            "interval": "monthly",
            "amount_cents": 1499,
            "amount_currency": "USD",
            "status": "active",
            "effective_at": "2026-08-03T00:00:00Z",
        },
        "pending_plan": None,
        "billing_time": "anniversary",
        "subscription_at": "2026-08-03T00:00:00Z",
        "started_at": "2026-08-03T00:00:00Z",
        "ending_at": None,
        "canceled_at": None,
        "created_at": "2026-08-03T00:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",
    }


def test_entity_subscription_ensure_get_and_checkout(
    requests_mock: requests_mock.Mocker,
) -> None:
    path = "https://api.example/core/api/v1/customers/workspace%2F1/entities/user%2F42/subscription"
    requests_mock.put(path, status_code=201, json={"entity_subscription": entity_subscription()})
    requests_mock.get(path, json={"entity_subscription": entity_subscription()})
    requests_mock.post(
        f"{path}/checkout",
        json={
            "type": "stripe",
            "client_secret": "cs_entity",
            "external_entity_id": "user/42",
            "external_subscription_id": "entity-sub-1",
        },
    )
    namespace = Nozle("sk_test", events_url="https://api.example/core").entity_subscriptions

    assert namespace.ensure("workspace/1", "user/42")["status"] == "active"
    assert namespace.get("workspace/1", "user/42")["current_plan"]["code"] == "pro"
    result = namespace.checkout(
        "workspace/1",
        "user/42",
        plan_code="pro",
        return_url="https://wrrk.ai/settings/billing",
        billing_time="anniversary",
        idempotency_key="checkout-user-42-pro",
    )

    assert result["type"] == "stripe"
    assert requests_mock.last_request.headers["Idempotency-Key"] == "checkout-user-42-pro"
    assert requests_mock.last_request.json() == {
        "plan_code": "pro",
        "return_url": "https://wrrk.ai/settings/billing",
        "billing_time": "anniversary",
    }


def test_entity_subscription_cancel_and_local_validation(
    requests_mock: requests_mock.Mocker,
) -> None:
    path = (
        "https://api.example/core/api/v1/customers/workspace/entities/user-42/subscription/cancel"
    )
    requests_mock.post(
        path,
        json={
            "entity_subscription": entity_subscription(),
            "subscription_transition": {"id": "transition-1", "replayed": False},
        },
    )
    namespace = Nozle("sk_test", events_url="https://api.example/core").entity_subscriptions

    result = namespace.cancel(
        "workspace",
        "user-42",
        idempotency_key="cancel-user-42",
        timing="end_of_period",
    )
    assert result["subscription_transition"]["replayed"] is False
    assert requests_mock.last_request.headers["Idempotency-Key"] == "cancel-user-42"

    with pytest.raises(NozleValidationError, match="billing_time"):
        namespace.checkout("workspace", "user-42", plan_code="pro", billing_time="weekly")
    with pytest.raises(NozleAuthenticationError, match="secret key"):
        Nozle("pk_browser").entity_subscriptions.get("workspace", "user-42")


def test_bulk_entity_subscription_checkout(requests_mock: requests_mock.Mocker) -> None:
    path = "https://api.example/core/api/v1/customers/workspace%2F1/entity-subscriptions/checkout"
    requests_mock.post(
        path,
        json={
            "entity_subscription_checkout": {
                "id": "batch-1",
                "type": "stripe",
                "status": "open",
                "client_secret": "cs_bulk",
                "invoice_id": "invoice-1",
                "amount_cents": 4498,
                "currency": "USD",
                "replayed": False,
                "expires_at": "2026-08-07T12:00:00Z",
                "items": [
                    {
                        "external_entity_id": "seat-pro-1",
                        "external_subscription_id": "entity-sub-1",
                        "plan_code": "pro",
                        "subscription_status": "incomplete",
                    }
                ],
            }
        },
    )
    namespace = Nozle("sk_test", events_url="https://api.example/core").entity_subscriptions

    result = namespace.checkout_many(
        "workspace/1",
        billing_time="anniversary",
        return_url="https://wrrk.ai/settings/billing",
        idempotency_key="workspace-1-seat-purchase",
        items=[{"external_entity_id": "seat-pro-1", "plan_code": "pro"}],
    )

    assert result["client_secret"] == "cs_bulk"
    assert requests_mock.last_request.headers["Idempotency-Key"] == "workspace-1-seat-purchase"
    assert requests_mock.last_request.json() == {
        "entity_subscription_checkout": {
            "billing_time": "anniversary",
            "return_url": "https://wrrk.ai/settings/billing",
            "items": [{"external_entity_id": "seat-pro-1", "plan_code": "pro"}],
        }
    }


def test_bulk_entity_subscription_checkout_validates_before_network(
    requests_mock: requests_mock.Mocker,
) -> None:
    namespace = Nozle("sk_test").entity_subscriptions

    with pytest.raises(NozleValidationError, match="unique external_entity_id"):
        namespace.checkout_many(
            "workspace",
            idempotency_key="purchase-1",
            items=[
                {"external_entity_id": "seat-1", "plan_code": "pro"},
                {"external_entity_id": "seat-1", "plan_code": "max"},
            ],
        )
    with pytest.raises(NozleAuthenticationError, match="secret key"):
        Nozle("pk_browser").entity_subscriptions.checkout_many(
            "workspace",
            idempotency_key="purchase-1",
            items=[{"external_entity_id": "seat-1", "plan_code": "pro"}],
        )
    assert not requests_mock.called
