from __future__ import annotations

from typing import Any

import pytest
import requests_mock

from nozle import Nozle, NozleAPIError, NozleAuthenticationError, NozleValidationError

CORE = "https://api.example/core/api/v1/customer_tokens"
TOKEN = {"token": "tok", "customer_id": "cus 1", "expires_at": "2026-11-22T13:00:00Z"}


def client(key: str = "sk_test", **options: Any) -> Nozle:
    return Nozle(key, host="https://api.example", retry_delay=0, **options)


def test_create_mints_a_token_on_core_with_the_secret_key(
    requests_mock: requests_mock.Mocker,
) -> None:
    route = requests_mock.post(CORE, json=TOKEN)
    nozle = client()
    assert nozle.customer_tokens.create("cus 1", ttl_seconds=600, features=["seats"]) == TOKEN
    assert route.last_request.json() == {
        "customer_id": "cus 1",
        "ttl_seconds": 600,
        "features": ["seats"],
    }
    assert route.last_request.headers["Authorization"] == "Bearer sk_test"
    nozle.customer_tokens.create("cus 1")
    assert route.last_request.json() == {"customer_id": "cus 1"}


def test_revoke_posts_an_empty_body(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.post(f"{CORE}/revoke", json={"revoked_at": "2026-11-22T12:00:00Z"})
    assert client().customer_tokens.revoke() == {"revoked_at": "2026-11-22T12:00:00Z"}
    assert route.last_request.json() == {}


def test_a_publishable_key_or_blank_customer_fails_before_any_request(
    requests_mock: requests_mock.Mocker,
) -> None:
    with pytest.raises(NozleAuthenticationError):
        client("pk_test").customer_tokens.create("cus")
    with pytest.raises(NozleAuthenticationError):
        client("pk_test").customer_tokens.revoke()
    with pytest.raises(NozleValidationError):
        client().customer_tokens.create(" ")
    assert requests_mock.request_history == []


def test_create_surfaces_core_refusals_and_retries_a_5xx(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        CORE,
        [
            {"status_code": 503, "json": {"status": 503}},
            {"status_code": 404, "json": {"status": 404, "code": "customer_not_found"}},
        ],
    )
    with pytest.raises(NozleAPIError) as error:
        client().customer_tokens.create("missing")
    assert error.value.status_code == 404
    assert requests_mock.call_count == 2
