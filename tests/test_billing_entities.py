from __future__ import annotations

from typing import Any

import httpx
import pytest
import requests_mock

from nozle import AsyncNozle, Nozle, NozleAuthenticationError, NozleValidationError

CORE = "https://api.example/core/api/v1/billing_entities"
ENTITY = {"nozle_id": "be_1", "code": "eu", "name": "Acme GmbH", "tax_provider": "stripe_tax"}


def client(**options: Any) -> Nozle:
    return Nozle("sk_test", host="https://api.example", retry_delay=0, **options)


def test_list_and_get_read_the_entities(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(CORE, json={"billing_entities": [ENTITY], "meta": {}})
    requests_mock.get(f"{CORE}/eu", json={"billing_entity": ENTITY})
    assert client().billing_entities.list()["billing_entities"][0]["code"] == "eu"
    assert client().billing_entities.get("eu")["tax_provider"] == "stripe_tax"


def test_create_sends_the_stripe_tax_fields(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.post(CORE, json={"billing_entity": ENTITY})
    result = client().billing_entities.create(
        "eu",
        "Acme GmbH",
        tax_provider="stripe_tax",
        provider_tax_code="txcd_10000000",
        country="DE",
    )
    assert result["code"] == "eu"
    assert route.last_request.json() == {
        "billing_entity": {
            "code": "eu",
            "name": "Acme GmbH",
            "tax_provider": "stripe_tax",
            "provider_tax_code": "txcd_10000000",
            "country": "DE",
        }
    }


def test_update_sends_only_the_given_fields(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.put(f"{CORE}/eu", json={"billing_entity": ENTITY})
    client().billing_entities.update(
        "eu", tax_provider="nozle", provider_tax_code=None, tax_codes=["vat"]
    )
    assert route.last_request.json() == {
        "billing_entity": {"tax_provider": "nozle", "tax_codes": ["vat"]}
    }


@pytest.mark.parametrize(
    "call",
    [
        lambda n: n.billing_entities.create("eu", "Acme", tax_provider="avalara"),
        lambda n: n.billing_entities.create("eu", "Acme", bogus=1),
        lambda n: n.billing_entities.create("eu", "Acme", tax_codes=[]),
        lambda n: n.billing_entities.create("", "Acme"),
        lambda n: n.billing_entities.update("eu"),
        lambda n: n.billing_entities.get(" "),
    ],
)
def test_bad_input_is_refused_before_any_request(
    call: Any, requests_mock: requests_mock.Mocker
) -> None:
    with pytest.raises(NozleValidationError):
        call(client())
    assert not requests_mock.request_history


def test_a_publishable_key_is_refused() -> None:
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_test", host="https://api.example").billing_entities.list()


async def test_async_client_has_the_same_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT" and request.url.path == "/core/api/v1/billing_entities/eu"
        return httpx.Response(200, json={"billing_entity": ENTITY})

    async with AsyncNozle(
        "sk_test",
        host="https://api.example",
        _client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as nozle:
        assert (await nozle.billing_entities.update("eu", tax_provider="stripe_tax"))[
            "code"
        ] == "eu"
