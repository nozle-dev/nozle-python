from __future__ import annotations

import pytest
import requests_mock

from nozle import Nozle, NozleAuthenticationError

CORE = "https://api.example/core/api/v1"
PERIOD = {
    "from_datetime": "2026-10-01T00:00:00Z",
    "to_datetime": "2026-10-31T23:59:59Z",
    "issuing_date": "2026-11-01",
    "currency": "USD",
    "amount_cents": 1240,
    "taxes_amount_cents": 0,
    "total_amount_cents": 1240,
    "charges_usage": [{"units": "31200.0", "feature": {"code": "api_calls"}}],
}


def client() -> Nozle:
    return Nozle("sk_test", host="https://api.example")


def test_current_usage_uses_the_active_subscription(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(f"{CORE}/subscriptions", json={"subscriptions": [{"external_id": "sub_1"}]})
    route = requests_mock.get(
        f"{CORE}/customers/cus%201/current_usage", json={"customer_usage": PERIOD}
    )
    assert client().current_usage("cus 1") == PERIOD
    assert route.last_request.qs == {"external_subscription_id": ["sub_1"]}


def test_past_usage_passes_the_period_count(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.get(
        f"{CORE}/customers/cus_1/past_usage", json={"usage_periods": [PERIOD, PERIOD]}
    )
    assert client().past_usage("cus_1", "sub_9", periods=2) == [PERIOD, PERIOD]
    assert route.last_request.qs == {"external_subscription_id": ["sub_9"], "periods_count": ["2"]}


def test_usage_reads_need_a_secret_key() -> None:
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_test").current_usage("cus_1", "sub_1")
