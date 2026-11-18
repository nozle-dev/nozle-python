from __future__ import annotations

import httpx
import pytest
import requests_mock

from nozle import AsyncNozle, CustomerEraseErrorCode, Nozle, NozleAPIError, NozleValidationError

CORE = "https://api.example/core/api/v1"


def client(api_key: str = "sk_test") -> Nozle:
    return Nozle(api_key, host="https://api.example", max_retries=2, retry_delay=0)


def test_erase_posts_without_a_body_and_returns_erased_at(
    requests_mock: requests_mock.Mocker,
) -> None:
    route = requests_mock.post(
        f"{CORE}/customers/c%2F1/erase",
        status_code=202,
        json={"customer": {"external_id": "c/1", "erased_at": "2026-11-18T10:00:00Z"}},
    )
    customer = client().customers.erase("c/1")
    assert customer["erased_at"] == "2026-11-18T10:00:00Z"
    assert route.last_request.body is None


def test_erase_refusal_codes(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(
        f"{CORE}/customers/c1/erase",
        status_code=422,
        json={
            "code": "validation_errors",
            "error_details": {"customer": ["has_active_subscriptions", "has_unpaid_invoices"]},
        },
    )
    with pytest.raises(NozleAPIError) as raised:
        client().customers.erase("c1")
    assert raised.value.has_code(CustomerEraseErrorCode.HAS_ACTIVE_SUBSCRIPTIONS)
    assert raised.value.has_code(CustomerEraseErrorCode.HAS_UNPAID_INVOICES)
    assert not raised.value.has_code(CustomerEraseErrorCode.HAS_DRAFT_INVOICES)


def test_delete_sends_delete_and_returns_the_customer(
    requests_mock: requests_mock.Mocker,
) -> None:
    route = requests_mock.delete(
        f"{CORE}/customers/c1", json={"customer": {"external_id": "c1", "erased_at": None}}
    )
    assert client().customers.delete("c1") == {"external_id": "c1", "erased_at": None}
    assert route.called_once


@pytest.mark.parametrize(
    "method,verb,suffix", [("erase", "POST", "/erase"), ("delete", "DELETE", "")]
)
def test_erase_and_delete_are_not_retried(
    requests_mock: requests_mock.Mocker, method: str, verb: str, suffix: str
) -> None:
    route = requests_mock.register_uri(
        verb, f"{CORE}/customers/c1{suffix}", status_code=503, json={}
    )
    with pytest.raises(NozleAPIError):
        getattr(client().customers, method)("c1")
    assert route.call_count == 1


@pytest.mark.parametrize("method", ["erase", "delete"])
def test_erase_and_delete_need_a_secret_key_and_an_id(
    requests_mock: requests_mock.Mocker, method: str
) -> None:
    with pytest.raises(NozleValidationError, match="secret key"):
        getattr(client("pk_test").customers, method)("c1")
    with pytest.raises(NozleValidationError, match="external_id"):
        getattr(client().customers, method)(" ")
    assert not requests_mock.called


async def test_async_erase() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/core/api/v1/customers/c1/erase")
        return httpx.Response(
            202, json={"customer": {"external_id": "c1", "erased_at": "2026-11-18T10:00:00Z"}}
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    nozle = AsyncNozle("sk_test", host="https://api.example", _client=http)
    assert (await nozle.customers.erase("c1"))["erased_at"] == "2026-11-18T10:00:00Z"
