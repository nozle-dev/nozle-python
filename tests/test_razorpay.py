from __future__ import annotations

import pytest
import requests_mock

from nozle import Nozle, NozleAuthenticationError


def test_checkout_options_and_authoritative_confirmation() -> None:
    client = Nozle("sk_test", base_url="https://engine.example")
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
        create = mock.post("https://engine.example/api/v1/checkout", json=checkout)
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
        verify = mock.post("https://engine.example/api/v1/checkout/checkout-1/verify", json=status)
        assert client.verify_checkout("checkout-1", proof)["status"] == "processing"
        assert verify.last_request.json() == proof
        mock.get("https://engine.example/api/v1/checkout/checkout-1", json=status)
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
