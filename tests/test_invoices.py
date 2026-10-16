from __future__ import annotations

import pytest
import requests_mock

from nozle import Nozle, NozleAuthenticationError

CORE = "https://api.example/core/api/v1"
INVOICE = {"nozle_id": "inv_1", "number": "INV-1", "file_url": "https://files.example/inv_1.pdf"}


def client() -> Nozle:
    return Nozle("sk_test", host="https://api.example")


def test_list_sends_the_filters(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.get(f"{CORE}/invoices", json={"invoices": [INVOICE], "meta": {}})
    page = client().invoices.list("cus 1", status="finalized", payment_status="failed", page=2)
    assert page["invoices"][0]["number"] == "INV-1"
    assert route.last_request.qs == {
        "external_customer_id": ["cus 1"],
        "status": ["finalized"],
        "payment_status": ["failed"],
        "page": ["2"],
    }


def test_download_url_or_none_while_generating(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(f"{CORE}/invoices/inv_1/download", json={"invoice": INVOICE})
    requests_mock.post(f"{CORE}/invoices/inv_2/download", status_code=200, text="")
    assert client().invoices.download_url("inv_1") == "https://files.example/inv_1.pdf"
    assert client().invoices.download_url("inv_2") is None


def test_preview_bodies(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.post(f"{CORE}/invoices/preview", json={"invoice": INVOICE})
    client().invoices.preview("cus_1", subscription_ids=["sub_1"])
    assert route.last_request.json() == {
        "customer": {"external_id": "cus_1"},
        "subscriptions": {"external_ids": ["sub_1"]},
    }
    client().invoices.preview("cus_1", plan_code="pro", billing_time="anniversary")
    assert route.last_request.json() == {
        "customer": {"external_id": "cus_1"},
        "plan_code": "pro",
        "billing_time": "anniversary",
    }
    with pytest.raises(ValueError):
        client().invoices.preview("cus_1")


def test_get_and_secret_key(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(f"{CORE}/invoices/inv_1", json={"invoice": INVOICE})
    assert client().invoices.get("inv_1")["nozle_id"] == "inv_1"
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_test").invoices.get("inv_1")
