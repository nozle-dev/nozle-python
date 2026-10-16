from __future__ import annotations

import pytest
import requests_mock

from nozle import Nozle, NozleValidationError

CORE = "https://api.example/core/api/v1"
ALERT = {"nozle_id": "al_1", "code": "api_80", "thresholds": []}


def client() -> Nozle:
    return Nozle("sk_test", host="https://api.example")


def test_subscription_alert_lifecycle(requests_mock: requests_mock.Mocker) -> None:
    base = f"{CORE}/subscriptions/sub%201/alerts"
    create = requests_mock.post(base, json={"alert": ALERT})
    listing = requests_mock.get(base, json={"alerts": [ALERT], "meta": {}})
    update = requests_mock.put(f"{base}/api_80", json={"alert": ALERT})
    delete = requests_mock.delete(f"{base}/api_80", json={"alert": ALERT})
    nozle = client()
    nozle.alerts.create(
        "feature_current_usage_units",
        "api_80",
        [{"value": 40000}, {"value": 10000, "recurring": True}],
        feature_code="api_calls",
        subscription_id="sub 1",
    )
    assert create.last_request.json() == {
        "alert": {
            "alert_type": "feature_current_usage_units",
            "code": "api_80",
            "feature_code": "api_calls",
            "thresholds": [{"value": "40000"}, {"value": "10000", "recurring": True}],
        }
    }
    assert nozle.alerts.list(subscription_id="sub 1", page=2)["alerts"][0]["code"] == "api_80"
    assert listing.last_request.qs == {"page": ["2"]}
    nozle.alerts.update("api_80", name="API at 80%", subscription_id="sub 1")
    assert update.last_request.json() == {"alert": {"name": "API at 80%"}}
    nozle.alerts.delete("api_80", subscription_id="sub 1")
    assert delete.called


def test_wallet_alert_path(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.get(
        f"{CORE}/customers/cus_1/wallets/main/alerts/low", json={"alert": ALERT}
    )
    client().alerts.get("low", customer_id="cus_1", wallet_code="main")
    assert route.called


def test_wallet_alerts_need_both_ids() -> None:
    with pytest.raises(NozleValidationError):
        client().alerts.list(customer_id="cus_1")
