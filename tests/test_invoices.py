from __future__ import annotations

import pytest
import requests_mock

from nozle import Nozle, NozleAPIError, NozleAuthenticationError, X402ErrorCode

CORE = "https://api.example/core/api/v1"
INVOICE = {"nozle_id": "inv_1", "number": "INV-1", "file_url": "https://files.example/inv_1.pdf"}


def client() -> Nozle:
    return Nozle("sk_test", host="https://api.example")


def test_list_sends_the_filters(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.get(f"{CORE}/invoices", json={"invoices": [INVOICE], "meta": {}})
    page = client().invoices.list(
        "cus 1", status="finalized", payment_status="failed", payment_overdue=True, page=2
    )
    assert page["invoices"][0]["number"] == "INV-1"
    assert route.last_request.qs == {
        "external_customer_id": ["cus 1"],
        "status": ["finalized"],
        "payment_status": ["failed"],
        "payment_overdue": ["true"],
        "page": ["2"],
    }


def test_download_url_or_none_while_generating(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(f"{CORE}/invoices/inv_1/download", json={"invoice": INVOICE})
    requests_mock.post(f"{CORE}/invoices/inv_2/download", status_code=200, text="")
    assert client().invoices.download_url("inv_1") == "https://files.example/inv_1.pdf"
    assert client().invoices.download_url("inv_2") is None


def test_get_and_secret_key(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(f"{CORE}/invoices/inv_1", json={"invoice": INVOICE})
    assert client().invoices.get("inv_1")["nozle_id"] == "inv_1"
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_test").invoices.get("inv_1")


def test_pay_with_agent_token_posts_the_token(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.post(
        f"{CORE}/invoices/inv_1/pay_with_agent_token", json={"invoice": INVOICE}
    )
    invoice = client().invoices.pay_with_agent_token("inv_1", "spt_123abc")
    assert invoice["nozle_id"] == "inv_1"
    assert route.last_request.json() == {"agent_payment_token": "spt_123abc"}


def test_pay_with_agent_token_refusal_and_secret_key(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        f"{CORE}/invoices/inv_1/pay_with_agent_token",
        status_code=422,
        json={
            "status": 422,
            "error": "Unprocessable Entity",
            "error_details": {"agent_payment_token": ["token_expired"]},
        },
    )
    with pytest.raises(NozleAPIError) as caught:
        client().invoices.pay_with_agent_token("inv_1", "spt_123abc")
    assert caught.value.status_code == 422
    # The refusal code must survive response sanitizing (the key ends in _token).
    details = caught.value.response_details["error_details"]
    assert details == {"agent_payment_token": ["token_expired"]}
    assert requests_mock.call_count == 1
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_test").invoices.pay_with_agent_token("inv_1", "spt_x")


REQUIREMENTS = {
    "x402Version": 2,
    "accepts": [
        {
            "scheme": "exact",
            "network": "eip155:8453",
            "amount": "1000000",
            "asset": "0xusdc",
            "payTo": "0xdeposit",
            "maxTimeoutSeconds": 300,
            "extra": {"name": "USD Coin", "version": "2"},
        }
    ],
    "resource": {"description": "Acme - Invoice INV-1"},
}


def test_get_x402_requirements_keeps_camel_case_keys(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.get(
        f"{CORE}/invoices/inv_1/x402_requirements", json={"x402_requirements": REQUIREMENTS}
    )
    result = client().invoices.get_x402_requirements("inv_1")
    assert result == REQUIREMENTS
    assert result["accepts"][0]["payTo"] == "0xdeposit"
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_test").invoices.get_x402_requirements("inv_1")


def test_get_x402_requirements_retries_get_and_refusal_code(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.get(
        f"{CORE}/invoices/inv_1/x402_requirements",
        [
            {"status_code": 503, "json": {}},
            {
                "status_code": 403,
                "json": {"status": 403, "error": "Forbidden", "code": "feature_not_enabled"},
            },
        ],
    )
    with pytest.raises(NozleAPIError) as caught:
        client().invoices.get_x402_requirements("inv_1")
    assert caught.value.has_code(X402ErrorCode.FEATURE_NOT_ENABLED)


def test_pay_with_x402_posts_hash(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.post(f"{CORE}/invoices/inv_1/pay_with_x402", json={"invoice": INVOICE})
    invoice = client().invoices.pay_with_x402("inv_1", "0x" + "ab" * 32)
    assert invoice["nozle_id"] == "inv_1"
    assert route.last_request.json() == {"transaction_hash": "0x" + "ab" * 32}
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_test").invoices.pay_with_x402("inv_1", "0x1")


def test_pay_with_x402_refusal_is_never_retried(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(
        f"{CORE}/invoices/inv_1/pay_with_x402",
        status_code=422,
        json={
            "status": 422,
            "error": "Unprocessable Entity",
            "error_details": {"transaction_hash": ["invalid_format"]},
        },
    )
    with pytest.raises(NozleAPIError) as caught:
        client().invoices.pay_with_x402("inv_1", "nope")
    assert caught.value.has_code(X402ErrorCode.INVALID_FORMAT)
    assert caught.value.response_details["error_details"] == {
        "transaction_hash": ["invalid_format"]
    }
    assert requests_mock.call_count == 1


def test_pay_with_x402_server_error_is_not_retried(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(f"{CORE}/invoices/inv_1/pay_with_x402", status_code=503, json={})
    with pytest.raises(NozleAPIError):
        client().invoices.pay_with_x402("inv_1", "0x" + "ab" * 32)
    assert requests_mock.call_count == 1
