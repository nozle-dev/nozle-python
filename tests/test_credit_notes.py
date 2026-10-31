from __future__ import annotations

from typing import Any

import pytest
import requests
import requests_mock

from nozle import (
    CANNOT_REFUND_UNPAID_INVOICE,
    HIGHER_THAN_REMAINING_INVOICE_AMOUNT,
    NO_VOIDABLE_AMOUNT,
    REFUND_EXCEEDS_PAID_AMOUNT,
    Nozle,
    NozleAPIError,
    NozleAuthenticationError,
    NozleTransportError,
    NozleValidationError,
)

CORE = "https://api.example/core/api/v1/credit_notes"
NOTE = {"nozle_id": "cn_1", "number": "INV-1-CN1", "refund_status": "pending"}
ITEMS: Any = [{"fee_id": "fee_1", "amount_cents": 500}]
BODY_ITEMS = [{"fee_id": "fee_1", "amount_cents": 500}]


def client(**options: Any) -> Nozle:
    return Nozle("sk_test", host="https://api.example", retry_delay=0, **options)


def estimate(refundable: int, creditable: int = 600) -> dict[str, Any]:
    return {
        "estimated_credit_note": {
            "invoice_id": "inv_1",
            "currency": "USD",
            "max_creditable_amount_cents": creditable,
            "max_refundable_amount_cents": refundable,
        }
    }


def failure(status: int, **body: Any) -> dict[str, Any]:
    return {"status_code": status, "json": {"status": status, **body}}


def test_preview_sends_the_core_estimate_body(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.post(f"{CORE}/estimate", json=estimate(500))
    result = client().credit_notes.preview("inv_1", ITEMS)
    assert result["max_refundable_amount_cents"] == 500
    assert route.last_request.json() == {
        "credit_note": {"invoice_id": "inv_1", "items": BODY_ITEMS}
    }
    assert route.last_request.headers["Authorization"] == "Bearer sk_test"


def test_preview_is_retried_because_it_creates_nothing(
    requests_mock: requests_mock.Mocker,
) -> None:
    route = requests_mock.post(
        f"{CORE}/estimate", [{"status_code": 503}, {"status_code": 503}, {"json": estimate(1)}]
    )
    client().credit_notes.preview("inv_1", ITEMS)
    assert route.call_count == 3


def test_create_sends_amounts_reason_description_and_metadata(
    requests_mock: requests_mock.Mocker,
) -> None:
    route = requests_mock.post(CORE, json={"credit_note": NOTE})
    note = client().credit_notes.create(
        "inv_1",
        ITEMS,
        refund_amount_cents=400,
        credit_amount_cents=100,
        offset_amount_cents=100,
        reason="order_cancellation",
        description="Cancelled",
        metadata={"ticket": "T-1"},
    )
    assert note["nozle_id"] == "cn_1"
    assert route.last_request.json() == {
        "credit_note": {
            "invoice_id": "inv_1",
            "items": BODY_ITEMS,
            "credit_amount_cents": 100,
            "refund_amount_cents": 400,
            "offset_amount_cents": 100,
            "reason": "order_cancellation",
            "description": "Cancelled",
            "metadata": {"ticket": "T-1"},
        }
    }
    assert "Idempotency-Key" not in route.last_request.headers


def test_create_and_void_are_never_retried(requests_mock: requests_mock.Mocker) -> None:
    create = requests_mock.post(CORE, status_code=503, json={})
    void = requests_mock.put(f"{CORE}/cn_1/void", exc=requests.ConnectionError)
    nozle = client(max_retries=3)
    with pytest.raises(NozleAPIError) as caught:
        nozle.credit_notes.create("inv_1", ITEMS, refund_amount_cents=500)
    assert caught.value.status_code == 503
    with pytest.raises(NozleTransportError):
        nozle.credit_notes.void("cn_1")
    assert create.call_count == 1
    assert void.call_count == 1


def test_refund_previews_then_refunds_the_previewed_total(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(f"{CORE}/estimate", json=estimate(600))
    route = requests_mock.post(CORE, json={"credit_note": NOTE})
    client().credit_notes.refund("inv_1", ITEMS, reason="duplicated_charge", metadata={"t": "1"})
    assert route.last_request.json()["credit_note"] == {
        "invoice_id": "inv_1",
        "items": BODY_ITEMS,
        "refund_amount_cents": 600,
        "reason": "duplicated_charge",
        "metadata": {"t": "1"},
    }


def test_refund_creates_nothing_when_less_was_paid_unless_credit_remainder(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(f"{CORE}/estimate", json=estimate(250))
    route = requests_mock.post(CORE, json={"credit_note": NOTE})
    with pytest.raises(NozleAPIError) as caught:
        client().credit_notes.refund("inv_1", ITEMS)
    assert caught.value.has_code(REFUND_EXCEEDS_PAID_AMOUNT)
    assert caught.value.status_code == 422
    assert route.call_count == 0
    client().credit_notes.refund("inv_1", ITEMS, credit_remainder=True)
    assert route.last_request.json()["credit_note"] == {
        "invoice_id": "inv_1",
        "items": BODY_ITEMS,
        "refund_amount_cents": 250,
        "credit_amount_cents": 350,
    }


def test_refund_of_an_unpaid_invoice_raises_even_with_credit_remainder(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(f"{CORE}/estimate", json=estimate(0))
    with pytest.raises(NozleAPIError) as caught:
        client().credit_notes.refund("inv_1", ITEMS, credit_remainder=True)
    assert caught.value.code == REFUND_EXCEEDS_PAID_AMOUNT


def test_list_sends_filters_and_none_without_them(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.get(CORE, json={"credit_notes": [NOTE], "meta": {}})
    page = client().credit_notes.list(
        customer_id="cus 1",
        invoice_number="INV-1",
        reason="other",
        credit_status="available",
        refund_status="failed",
        currency="USD",
        issuing_date_from="2026-01-01",
        issuing_date_to="2026-02-01",
        amount_from=1,
        amount_to=9,
        page=2,
        per_page=5,
    )
    assert page["credit_notes"][0]["nozle_id"] == "cn_1"
    assert route.last_request.qs == {
        "external_customer_id": ["cus 1"],
        "invoice_number": ["inv-1"],
        "reason": ["other"],
        "credit_status": ["available"],
        "refund_status": ["failed"],
        "currency": ["usd"],
        "issuing_date_from": ["2026-01-01"],
        "issuing_date_to": ["2026-02-01"],
        "amount_from": ["1"],
        "amount_to": ["9"],
        "page": ["2"],
        "per_page": ["5"],
    }
    client().credit_notes.list()
    assert route.last_request.query == ""


def test_get_and_void(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(f"{CORE}/cn%201", json={"credit_note": NOTE})
    requests_mock.put(
        f"{CORE}/cn_1/void", json={"credit_note": {**NOTE, "credit_status": "voided"}}
    )
    assert client().credit_notes.get("cn 1")["number"] == "INV-1-CN1"
    assert client().credit_notes.void("cn_1")["credit_status"] == "voided"


def test_core_errors_carry_status_and_codes(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(
        CORE,
        **failure(
            422,
            error="Unprocessable Entity",
            code="validation_errors",
            error_details={"refund_amount_cents": [HIGHER_THAN_REMAINING_INVOICE_AMOUNT]},
        ),
    )
    with pytest.raises(NozleAPIError) as caught:
        client().credit_notes.create("inv_1", ITEMS, refund_amount_cents=9)
    assert caught.value.status_code == 422
    assert caught.value.has_code(HIGHER_THAN_REMAINING_INVOICE_AMOUNT)
    assert not caught.value.has_code(CANNOT_REFUND_UNPAID_INVOICE)


def test_business_rule_forbidden_not_found_and_unauthorized(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.put(f"{CORE}/cn_1/void", **failure(422, code=NO_VOIDABLE_AMOUNT))
    requests_mock.post(CORE, **failure(403, error="Forbidden", code="feature_unavailable"))
    requests_mock.get(f"{CORE}/cn_x", **failure(404, code="credit_note_not_found"))
    requests_mock.get(CORE, **failure(401, error="Unauthorized"))
    nozle = client()
    with pytest.raises(NozleAPIError) as caught:
        nozle.credit_notes.void("cn_1")
    assert (caught.value.status_code, caught.value.code) == (422, NO_VOIDABLE_AMOUNT)
    with pytest.raises(NozleAPIError) as caught:
        nozle.credit_notes.create("inv_1", ITEMS, credit_amount_cents=1)
    assert (caught.value.status_code, caught.value.code) == (403, "feature_unavailable")
    with pytest.raises(NozleAPIError) as caught:
        nozle.credit_notes.get("cn_x")
    assert (caught.value.status_code, caught.value.code) == (404, "credit_note_not_found")
    with pytest.raises(NozleAPIError) as caught:
        nozle.credit_notes.list()
    assert caught.value.status_code == 401


@pytest.mark.parametrize(
    "call, message",
    [
        (lambda n: n.credit_notes.preview(" ", ITEMS), "invoice_id"),
        (lambda n: n.credit_notes.preview("inv_1", []), "at least one item"),
        (
            lambda n: n.credit_notes.create(
                "inv_1", [{"fee_id": "f", "amount_cents": 1.5}], credit_amount_cents=1
            ),
            "whole cents",
        ),
        (
            lambda n: n.credit_notes.create("inv_1", [{"amount_cents": 1}], credit_amount_cents=1),
            "fee_id",
        ),
        (lambda n: n.credit_notes.create("inv_1", ITEMS), "above zero"),
        (
            lambda n: n.credit_notes.create("inv_1", ITEMS, refund_amount_cents=-1),
            "refund_amount_cents",
        ),
        (
            lambda n: n.credit_notes.create("inv_1", ITEMS, credit_amount_cents=1, reason="bored"),
            "reason must be",
        ),
        (lambda n: n.credit_notes.get(""), "credit_note_id"),
        (lambda n: n.credit_notes.void(""), "credit_note_id"),
    ],
)
def test_bad_input_is_refused_before_any_request(
    requests_mock: requests_mock.Mocker, call: Any, message: str
) -> None:
    with pytest.raises(NozleValidationError, match=message):
        call(client())
    assert requests_mock.call_count == 0


def test_needs_a_secret_key(requests_mock: requests_mock.Mocker) -> None:
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_test").credit_notes.get("cn_1")
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_test").credit_notes.refund("inv_1", ITEMS)
    assert requests_mock.call_count == 0
