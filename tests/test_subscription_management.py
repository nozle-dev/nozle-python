from typing import Any

import pytest

from nozle import Nozle, NozleValidationError


def test_explicit_options_and_preview_preserve_wire_identifiers(requests_mock: Any) -> None:
    requests_mock.get("http://engine/api/v1/subscriptions/options", json={})
    quote = {"quote_id": "opaque+quote/==", "amount_due_now_cents": "12345678901234567890"}
    requests_mock.post("http://engine/api/v1/subscriptions/preview", json=quote)
    with Nozle("sk_test", base_url="http://engine") as sdk:
        sdk.subscription_options("customer/one", "subscription/two")
        assert requests_mock.last_request.qs == {
            "customer_id": ["customer/one"],
            "subscription_id": ["subscription/two"],
        }
        assert sdk.preview_subscription_change("customer", "external-sub", "pro") == quote
        assert requests_mock.last_request.json() == {
            "customer_id": "customer",
            "subscription_id": "external-sub",
            "plan_code": "pro",
        }


def test_quoted_checkout_selects_one_subscription_and_reuses_key(requests_mock: Any) -> None:
    requests_mock.post("http://engine/api/v1/checkout", json={})
    with Nozle("sk_test", base_url="http://engine") as sdk:
        sdk.checkout(
            "customer",
            "pro",
            "https://merchant.example/billing",
            subscription_id="selected",
            quote_id="opaque+quote/==",
            idempotency_key="confirm-1",
        )
    assert requests_mock.last_request.json() == {
        "customer_id": "customer",
        "plan_code": "pro",
        "subscription_id": "selected",
        "quote_id": "opaque+quote/==",
        "return_url": "https://merchant.example/billing",
    }
    assert requests_mock.last_request.headers["Idempotency-Key"] == "confirm-1"


def test_checkout_quote_requires_explicit_selector(requests_mock: Any) -> None:
    with Nozle("sk_test") as sdk:
        with pytest.raises(NozleValidationError, match="subscription_id"):
            sdk.checkout("customer", "pro", quote_id="quote")
    assert requests_mock.call_count == 0


def test_scoped_checkout_status_and_provider_verification(requests_mock: Any) -> None:
    requests_mock.get("http://engine/api/v1/checkout/checkout%2Fid", json={})
    requests_mock.post("http://engine/api/v1/checkout/checkout%2Fid/verify", json={})
    with Nozle("sk_test", base_url="http://engine") as sdk:
        sdk.checkout_status("checkout/id", customer_id="customer", subscription_id="subscription")
        assert requests_mock.last_request.qs == {
            "customer_id": ["customer"],
            "subscription_id": ["subscription"],
        }
        sdk.verify_checkout(
            "checkout/id",
            {
                "razorpay_order_id": "order",
                "razorpay_payment_id": "payment",
                "razorpay_signature": "signature",
            },
            customer_id="customer",
            subscription_id="subscription",
        )
        assert requests_mock.last_request.qs == {
            "customer_id": ["customer"],
            "subscription_id": ["subscription"],
        }
        assert requests_mock.last_request.json()["razorpay_order_id"] == "order"


def test_withdraw_targets_exact_pending_uuid(requests_mock: Any) -> None:
    requests_mock.post("http://engine/api/v1/subscriptions/transitions/withdraw", json={})
    with Nozle("sk_test", base_url="http://engine") as sdk:
        sdk.withdraw_pending_subscription_change(
            "customer", "active-external", "pending-uuid", idempotency_key="withdraw-1"
        )
    assert requests_mock.last_request.json() == {
        "customer_id": "customer",
        "subscription_id": "active-external",
        "pending_subscription_id": "pending-uuid",
    }
    assert requests_mock.last_request.headers["Idempotency-Key"] == "withdraw-1"


def test_downgrade_quote_is_forwarded_and_rejected_for_cancel(requests_mock: Any) -> None:
    requests_mock.post("http://engine/api/v1/subscriptions/transitions", json={})
    with Nozle("sk_test", base_url="http://engine") as sdk:
        sdk.apply_subscription_transition(
            {
                "customer_id": "customer",
                "subscription_id": "active",
                "operation": "downgrade",
                "target_plan_code": "basic",
                "timing": "end_of_period",
                "quote_id": "signed",
            },
            idempotency_key="schedule-1",
        )
        assert requests_mock.last_request.json()["quote_id"] == "signed"
        with pytest.raises(NozleValidationError, match="downgrade"):
            sdk.apply_subscription_transition(
                {
                    "customer_id": "customer",
                    "subscription_id": "active",
                    "operation": "cancel",
                    "quote_id": "signed",
                },
                idempotency_key="bad",
            )


def test_new_methods_require_secret_and_complete_selection(requests_mock: Any) -> None:
    with Nozle("pk_catalog") as sdk:
        with pytest.raises(NozleValidationError):
            sdk.subscription_options("customer", "subscription")
    with Nozle("sk_test") as sdk:
        with pytest.raises(NozleValidationError):
            sdk.subscription_options("", "subscription")
        with pytest.raises(NozleValidationError):
            sdk.withdraw_pending_subscription_change("c", "s", "", idempotency_key="key")
    assert requests_mock.call_count == 0
