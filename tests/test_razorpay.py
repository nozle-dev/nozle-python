from __future__ import annotations

import pytest
import requests_mock

from nozle import Nozle, NozleAPIError, NozleAuthenticationError, NozleValidationError


@pytest.mark.parametrize(
    "base_url", [None, "https://engine.example", "https://api.example/nested/engine///"]
)
def test_checkout_options_and_authoritative_confirmation(base_url: str | None) -> None:
    client = Nozle("sk_test") if base_url is None else Nozle("sk_test", base_url=base_url)
    expected_base = (base_url or "https://api.nozle.app/engine").rstrip("/")
    checkout = {
        "type": "razorpay",
        "checkout_id": "checkout-1",
        "key_id": "rzp_test_key",
        "order_id": "order_1",
        "amount_cents": 1000,
        "currency": "INR",
    }
    status = {
        "checkout_id": "checkout-1",
        "provider": "razorpay",
        "status": "processing",
        "fulfillment_status": "pending",
        "amount_cents": 1000,
        "currency": "INR",
    }
    proof = {
        "razorpay_order_id": "order_1",
        "razorpay_payment_id": "pay_1",
        "razorpay_signature": "signature",
    }
    with requests_mock.Mocker() as mock:
        create = mock.post(f"{expected_base}/api/v1/checkout", json=checkout)
        assert (
            client.checkout(
                "customer", "pro", idempotency_key="same-attempt", register_mandate=True
            )
            == checkout
        )
        assert create.last_request.headers["Idempotency-Key"] == "same-attempt"
        assert create.last_request.json()["register_mandate"] is True
        assert client.checkout_invoice("invoice-1", idempotency_key="invoice-retry") == checkout
        assert create.last_request.json() == {"invoice_id": "invoice-1"}
        verify = mock.post(f"{expected_base}/api/v1/checkout/checkout-1/verify", json=status)
        assert client.verify_checkout("checkout-1", proof)["status"] == "processing"
        assert verify.last_request.json() == proof
        mock.get(f"{expected_base}/api/v1/checkout/checkout-1", json=status)
        assert client.checkout_status("checkout-1")["fulfillment_status"] == "pending"


@pytest.mark.parametrize("operation", ["invoice", "verify", "status"])
def test_collection_endpoints_require_server_credentials(operation: str) -> None:
    client = Nozle("pk_browser")
    with pytest.raises(NozleAuthenticationError):
        if operation == "invoice":
            client.checkout_invoice("invoice")
        elif operation == "status":
            client.checkout_status("checkout")
        else:
            client.verify_checkout(
                "checkout",
                {
                    "razorpay_order_id": "order",
                    "razorpay_payment_id": "payment",
                    "razorpay_signature": "signature",
                },
            )


@pytest.mark.parametrize(
    "result",
    [
        {
            "type": "razorpay",
            "checkout_id": "c1",
            "key_id": "rzp_test_k",
            "order_id": "order_1",
            "amount_cents": 99900,
            "currency": "INR",
            "expires_at": "2026-10-14T09:30:00.000Z",
            "customer_id": "cust_rzp",
            "recurring": True,
            "mandate_max_amount_cents": 500000,
            "plan_upgrade_intent_id": "intent-1",
        },
        {
            "type": "hosted",
            "url": "https://rzp.io/i/abc",
            "payment_url": "https://rzp.io/i/abc",
            "checkout_id": "c2",
            "amount_cents": 500,
            "currency": "INR",
            "expires_at": "2026-10-14T09:30:00.000Z",
            "invoice_id": "inv-1",
        },
        {"type": "processing", "checkout_id": "c3", "status": "processing", "currency": "INR"},
        {
            "type": "hosted",
            "url": "https://checkout.stripe.com/c/pay/cs_test_1",
            "payment_url": "https://checkout.stripe.com/c/pay/cs_test_1",
            "plan_upgrade_intent_id": "intent-2",
            "amount_cents": 1000,
            "currency": "USD",
        },
        {
            "type": "completed",
            "status": "succeeded",
            "payment_source": "credits",
            "subscription_id": "s1",
            "plan_code": "pro",
            "invoice_id": "inv-2",
            "amount_cents": 0,
            "currency": "USD",
        },
        {
            "type": "scheduled",
            "status": "pending",
            "subscription_id": "s2",
            "plan_code": "starter",
            "transition_direction": "downgrade",
            "transition_policy": "next_period",
            "effective_at": "2026-11-01T00:00:00.000000Z",
        },
    ],
)
def test_checkout_returns_every_api_result_shape_unchanged(result: dict) -> None:
    with requests_mock.Mocker() as mock:
        mock.post("https://api.nozle.app/engine/api/v1/checkout", json=result)
        assert Nozle("sk_test").checkout("customer", "pro") == result


def test_checkout_error_exposes_code_and_validation_details() -> None:
    body = {
        "status": 422,
        "code": "validation_errors",
        "error_details": {"base": ["razorpay_requires_inr"]},
    }
    with requests_mock.Mocker() as mock:
        mock.post("https://api.nozle.app/engine/api/v1/checkout", status_code=422, json=body)
        with pytest.raises(NozleAPIError) as caught:
            Nozle("sk_test").checkout("customer", "pro")
    error = caught.value
    assert error.status_code == 422
    assert error.code == "validation_errors"
    assert error.has_code("razorpay_requires_inr")
    assert error.has_code("validation_errors")
    assert not error.has_code("payment_provider_required")


def test_api_error_code_helpers_tolerate_non_object_bodies() -> None:
    for details in (None, "plain text", ["x"], {"code": 5}, {"error_details": ["x"]}):
        error = NozleAPIError("checkout", 500, details)
        assert error.code is None
        assert not error.has_code("anything")


@pytest.mark.parametrize(
    "field", ["razorpay_order_id", "razorpay_payment_id", "razorpay_signature"]
)
def test_verify_checkout_rejects_a_missing_signature_field_before_any_request(field: str) -> None:
    proof = {"razorpay_order_id": "o", "razorpay_payment_id": "p", "razorpay_signature": "s"}
    proof[field] = ""
    with requests_mock.Mocker() as mock:
        with pytest.raises(NozleValidationError):
            Nozle("sk_test").verify_checkout("checkout", proof)  # type: ignore[arg-type]
        assert mock.call_count == 0


def test_manage_payment_method_returns_the_stripe_url() -> None:
    client = Nozle("sk_test", base_url="https://engine.example")
    url = "https://engine.example/api/v1/customers/cust%2F1/payment-method/manage"
    stripe = {"url": "https://billing.stripe.com/x", "expires_at": None, "provider": "stripe"}
    with requests_mock.Mocker() as mock:
        route = mock.post(url, json={"manage_payment_method": stripe})
        assert (
            client.manage_payment_method("cust/1", "https://app.example/back", idempotency_key="k1")
            == stripe
        )
        assert route.last_request.json() == {"return_url": "https://app.example/back"}
        assert route.last_request.headers["Idempotency-Key"] == "k1"
        result = client.manage_payment_method("cust/1")
        assert result == stripe and "checkout" not in result
        assert route.last_request.body is None


def test_manage_payment_method_errors() -> None:
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_browser").manage_payment_method("cust")
    with pytest.raises(NozleValidationError):
        Nozle("sk_test").manage_payment_method("")
    client = Nozle("sk_test", base_url="https://engine.example", max_retries=0)
    with requests_mock.Mocker() as mock:
        mock.post(
            "https://engine.example/api/v1/customers/cust/payment-method/manage",
            status_code=422,
            json={"error": "no_linked_payment_provider"},
        )
        with pytest.raises(NozleAPIError) as raised:
            client.manage_payment_method("cust")
    assert raised.value.status_code == 422


def test_manage_payment_method_raises_razorpay_pays_by_link() -> None:
    client = Nozle("sk_test", base_url="https://engine.example", max_retries=0)
    with requests_mock.Mocker() as mock:
        mock.post(
            "https://engine.example/api/v1/customers/cust/payment-method/manage",
            status_code=422,
            json={
                "status": 422,
                "error": "Unprocessable Entity",
                "code": "validation_errors",
                "error_details": {"base": ["razorpay_pays_by_link"]},
            },
        )
        with pytest.raises(NozleAPIError) as raised:
            client.manage_payment_method("cust")
    assert raised.value.status_code == 422
    assert raised.value.has_code("razorpay_pays_by_link")
