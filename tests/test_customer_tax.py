from __future__ import annotations

from typing import Any

import httpx
import pytest
import requests_mock

from nozle import AsyncNozle, CustomerTaxErrorCode, Nozle, NozleAPIError, NozleValidationError

CORE = "https://api.example/core/api/v1"


def client() -> Nozle:
    return Nozle("sk_test", host="https://api.example", max_retries=0)


def test_upsert_maps_address_and_tax_id(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.post(
        f"{CORE}/customers",
        json={
            "customer": {
                "external_id": "c1",
                "tax_identification_type": "eu_vat",
                "tax_identification_status": "pending",
                "tax_treatment": "pending",
                "tax_identification_warnings": ["country_mismatch"],
            }
        },
    )
    result = client().customers.upsert(
        "c1",
        customer_type="company",
        legal_name="Acme GmbH",
        address={
            "line1": "Main St 1",
            "line2": "Top",
            "city": "Berlin",
            "state": "BE",
            "postal_code": "10115",
            "country": "DE",
        },
        shipping_address={"line1": "Dock 2", "country": "DE"},
        tax_id={"type": "eu_vat", "value": "DE811128135"},
    )
    assert route.last_request.json() == {
        "customer": {
            "external_id": "c1",
            "customer_type": "company",
            "legal_name": "Acme GmbH",
            "address_line1": "Main St 1",
            "address_line2": "Top",
            "city": "Berlin",
            "state": "BE",
            "zipcode": "10115",
            "country": "DE",
            "shipping_address": {"address_line1": "Dock 2", "country": "DE"},
            "tax_identification_number": "DE811128135",
            "tax_identification_type": "eu_vat",
        }
    }
    assert result["tax_identification_status"] == "pending"
    assert result["tax_identification_warnings"] == ["country_mismatch"]


def test_clear_tax_id_sends_null(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.post(f"{CORE}/customers", json={"customer": {"external_id": "c1"}})
    client().customers.upsert("c1", clear_tax_id=True)
    assert route.last_request.json() == {
        "customer": {"external_id": "c1", "tax_identification_number": None}
    }


def test_tax_id_without_value_or_with_clear_is_refused_locally(
    requests_mock: requests_mock.Mocker,
) -> None:
    with pytest.raises(NozleValidationError, match="tax_id.value"):
        client().customers.upsert("c1", tax_id={"type": "eu_vat", "value": " "})
    with pytest.raises(NozleValidationError, match="not both"):
        client().customers.upsert("c1", tax_id={"type": "eu_vat", "value": "x"}, clear_tax_id=True)
    assert requests_mock.request_history == []


def test_validation_code_via_has_code(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(
        f"{CORE}/customers",
        status_code=422,
        json={
            "code": "validation_errors",
            "error_details": {"tax_identification_number": ["invalid_format"]},
        },
    )
    with pytest.raises(NozleAPIError) as caught:
        client().customers.upsert("c1", tax_id={"type": "eu_vat", "value": "x"})
    assert caught.value.has_code(CustomerTaxErrorCode.INVALID_FORMAT)
    assert not caught.value.has_code(CustomerTaxErrorCode.VALUE_IS_INVALID)


def test_checkout_tax_refusals(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(
        "https://api.example/engine/api/v1/checkout",
        [
            {
                "status_code": 409,
                "json": {"code": "customer_tax_check_pending", "retry_after_seconds": 30},
            },
            {
                "status_code": 422,
                "json": {
                    "code": "customer_tax_location_required",
                    "error_details": {"customer": ["country_missing"]},
                },
            },
        ],
    )
    nozle = client()
    with pytest.raises(NozleAPIError) as pending:
        nozle.checkout("c1", "pro", "https://app.example.com/b")
    assert pending.value.has_code(CustomerTaxErrorCode.TAX_CHECK_PENDING)
    assert pending.value.retry_after_seconds == 30
    with pytest.raises(NozleAPIError) as location:
        nozle.checkout("c1", "pro", "https://app.example.com/b")
    assert location.value.has_code(CustomerTaxErrorCode.TAX_LOCATION_REQUIRED)
    assert location.value.has_code("country_missing")
    assert location.value.retry_after_seconds is None


def test_get_escapes_the_id(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.get(
        f"{CORE}/customers/a%2Fb",
        json={"customer": {"external_id": "a/b", "tax_treatment": "reverse_charge"}},
    )
    assert client().customers.get("a/b")["tax_treatment"] == "reverse_charge"
    assert route.called_once


def test_wait_for_tax_check_polls_until_not_pending(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.get(
        f"{CORE}/customers/c1",
        [
            {"json": {"customer": {"external_id": "c1", "tax_identification_status": "pending"}}},
            {"json": {"customer": {"external_id": "c1", "tax_identification_status": "valid"}}},
        ],
    )
    result = client().customers.wait_for_tax_check("c1", interval=0.001)
    assert result["tax_identification_status"] == "valid"
    assert route.call_count == 2


def test_wait_for_tax_check_times_out(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(
        f"{CORE}/customers/c1",
        json={"customer": {"external_id": "c1", "tax_identification_status": "pending"}},
    )
    with pytest.raises(NozleValidationError, match="timed out"):
        client().customers.wait_for_tax_check("c1", timeout=0.02, interval=0.005)


async def test_async_wait_for_tax_check() -> None:
    answers = iter(["pending", "invalid"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/core/api/v1/customers/c1"
        body: dict[str, Any] = {
            "customer": {"external_id": "c1", "tax_identification_status": next(answers)}
        }
        return httpx.Response(200, json=body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    nozle = AsyncNozle("sk_test", host="https://api.example", _client=http)
    result = await nozle.customers.wait_for_tax_check("c1", interval=0.001)
    assert result["tax_identification_status"] == "invalid"
