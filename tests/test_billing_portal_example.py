from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import pytest
import requests

from examples.billing_portal.server import (
    ActionStore,
    BillingService,
    HttpError,
    merchant_handler,
    portal_session_reader,
)
from nozle import Nozle
from nozle.errors import NozleAPIError

EFFECTIVE_AT = "2027-01-01T00:00:00.123456Z"
CANCELLATION = {
    "subscriptionId": "external-subscription",
    "operation": "cancel",
    "expectedEffectiveAt": EFFECTIVE_AT,
    "idempotencyKey": "confirmation-1",
}
ACTION = "/api/billing/cancellation"


class FakeSDK:
    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.fail_once = False

    def preview_subscription_transition(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("preview", params))
        return {
            "subscription_transition": {
                "operation": params["operation"],
                "effective_at": EFFECTIVE_AT,
                "renewal_at": EFFECTIVE_AT,
            }
        }

    def apply_subscription_transition(
        self, params: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        self.calls.append(("apply", params, idempotency_key))
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError("private upstream details")
        return {}


def service(tmp_path: Path, sdk: FakeSDK) -> BillingService:
    return BillingService(
        sdk,
        ActionStore(tmp_path / "actions.json"),
        lambda customer: {"token": f"scoped-{customer}", "apiUrl": "http://core"},
    )


def test_cancel_replay_survives_restart_and_is_bound_to_original_request(tmp_path: Path) -> None:
    sdk = FakeSDK()
    merchant = service(tmp_path, sdk)
    merchant.dispatch("authenticated", ACTION, CANCELLATION)
    service(tmp_path, sdk).dispatch("authenticated", ACTION, CANCELLATION)
    assert len(sdk.calls) == 2
    assert sdk.calls[1][1] == {
        "customer_id": "authenticated",
        "subscription_id": "external-subscription",
        "operation": "cancel",
        "timing": "end_of_period",
        "expected_effective_at": EFFECTIVE_AT,
    }
    with pytest.raises(HttpError) as error:
        merchant.dispatch("authenticated", ACTION, {**CANCELLATION, "subscriptionId": "another"})
    assert error.value.status == 409


def test_concurrent_duplicate_confirmation_applies_once(tmp_path: Path) -> None:
    sdk = FakeSDK()
    merchant = service(tmp_path, sdk)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda _: merchant.dispatch("authenticated", ACTION, CANCELLATION), range(4)
            )
        )
    assert len(sdk.calls) == 2


def test_lost_response_reuses_key_without_new_preview(tmp_path: Path) -> None:
    sdk = FakeSDK()
    sdk.fail_once = True
    with pytest.raises(TimeoutError):
        service(tmp_path, sdk).dispatch("authenticated", ACTION, CANCELLATION)
    service(tmp_path, sdk).dispatch("authenticated", ACTION, CANCELLATION)
    assert [call[0] for call in sdk.calls] == ["preview", "apply", "apply"]
    assert sdk.calls[1][2] == sdk.calls[2][2]


def test_changed_date_requires_new_confirmation_before_mutating(tmp_path: Path) -> None:
    sdk = FakeSDK()
    with pytest.raises(HttpError) as error:
        service(tmp_path, sdk).dispatch(
            "authenticated", ACTION, {**CANCELLATION, "expectedEffectiveAt": "2026-12-01T00:00:00Z"}
        )
    assert error.value.status == 409
    assert len(sdk.calls) == 1


def test_atomic_upstream_conflict_maps_to_changed(tmp_path: Path) -> None:
    class ConflictSDK(FakeSDK):
        def apply_subscription_transition(
            self, params: dict[str, Any], *, idempotency_key: str
        ) -> dict[str, Any]:
            raise NozleAPIError("apply", 409, {"private": "detail"})

    with pytest.raises(HttpError) as error:
        service(tmp_path, ConflictSDK()).dispatch("authenticated", ACTION, CANCELLATION)
    assert error.value.status == 409
    assert "private" not in str(error.value)


def test_keep_has_no_settlement_overrides(tmp_path: Path) -> None:
    sdk = FakeSDK()
    service(tmp_path, sdk).dispatch(
        "authenticated",
        ACTION,
        {
            "subscriptionId": "external-subscription",
            "operation": "uncancel",
            "idempotencyKey": "keep",
        },
    )
    assert sdk.calls[1][1] == {
        "customer_id": "authenticated",
        "subscription_id": "external-subscription",
        "operation": "uncancel",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"customerId": "victim"},
        {"timing": "immediate"},
        {"operation": "downgrade"},
        {"expectedEffectiveAt": None},
        {"operation": "uncancel"},
    ],
)
def test_rejects_tampered_or_privileged_requests(tmp_path: Path, change: dict[str, Any]) -> None:
    sdk = FakeSDK()
    with pytest.raises(HttpError) as error:
        service(tmp_path, sdk).dispatch("authenticated", ACTION, {**CANCELLATION, **change})
    assert error.value.status == 400
    assert sdk.calls == []


def test_customer_id_is_part_of_upstream_idempotency_scope(tmp_path: Path) -> None:
    sdk = FakeSDK()
    merchant = service(tmp_path, sdk)
    merchant.dispatch("first", ACTION, CANCELLATION)
    merchant.dispatch("second", ACTION, CANCELLATION)
    assert sdk.calls[1][2] != sdk.calls[3][2]


def test_portal_session_is_scoped_without_leaking_api_key(requests_mock: Any) -> None:
    requests_mock.get(
        "http://core/api/v1/customers/cust%2Fa/portal_url",
        json={"customer": {"portal_url": "https://host/customer-portal/public-token"}},
    )
    read = portal_session_reader("sk_private", "http://core", "https://browser-core")
    assert read("cust/a") == {"token": "public-token", "apiUrl": "https://browser-core"}
    assert requests_mock.last_request.headers["Authorization"] == "Bearer sk_private"


def test_merchant_calls_actual_sdk_with_documented_wire_contract(
    tmp_path: Path, requests_mock: Any
) -> None:
    response = {
        "subscription_transition": {
            "operation": "cancel",
            "effective_at": EFFECTIVE_AT,
            "renewal_at": EFFECTIVE_AT,
        }
    }
    requests_mock.post("http://engine/api/v1/subscriptions/transitions/preview", json=response)
    requests_mock.post("http://engine/api/v1/subscriptions/transitions", json=response)
    with Nozle("sk_example", base_url="http://engine") as sdk:
        merchant = BillingService(sdk, ActionStore(tmp_path / "actions.json"), lambda _: {})
        merchant.dispatch("authenticated", ACTION, CANCELLATION)
    assert requests_mock.last_request.json() == {
        "customer_id": "authenticated",
        "subscription_id": "external-subscription",
        "operation": "cancel",
        "timing": "end_of_period",
        "expected_effective_at": EFFECTIVE_AT,
    }
    assert len(requests_mock.last_request.headers["Idempotency-Key"]) == 64
    assert requests_mock.call_count == 2


def test_http_auth_csrf_expiry_and_private_error_redaction(tmp_path: Path) -> None:
    sdk = FakeSDK()
    merchant = service(tmp_path, sdk)
    clock = [1.0]

    def dispatch(customer: str, path: str, body: Any) -> dict[str, Any]:
        if path == "/api/private-failure":
            raise RuntimeError("sk_secret customer-portal/private-token")
        return merchant.dispatch(customer, path, body)

    origin = "http://localhost:4243"
    token = "x" * 32
    handler = merchant_handler(
        origin=origin,
        login_token=token,
        customer_id="authenticated",
        dispatch=dispatch,
        now=lambda: clock[0],
    )
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    browser = requests.Session()
    browser.headers.update({"Origin": origin})
    try:
        assert browser.post(base + "/api/billing/session", json={}).status_code == 401
        assert (
            browser.post(
                base + "/api/login",
                json={"token": token},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert browser.post(base + "/api/login", json={"token": "wrong"}).status_code == 401
        login = browser.post(base + "/api/login", json={"token": token})
        assert login.status_code == 200
        assert "HttpOnly; SameSite=Strict" in login.headers["Set-Cookie"]
        assert "; Secure" in login.headers["Set-Cookie"]
        # Model the HTTPS reverse proxy forwarding its browser cookie to loopback HTTP.
        browser.headers["Cookie"] = login.headers["Set-Cookie"].split(";", 1)[0]
        assert browser.post(base + "/api/billing/session", json={}).json() == {
            "token": "scoped-authenticated",
            "apiUrl": "http://core",
        }
        assert (
            browser.post(base + "/api/billing/session", json={"customerId": "victim"}).status_code
            == 400
        )
        failure = browser.post(base + "/api/private-failure", json={})
        assert failure.status_code == 502
        assert "sk_secret" not in failure.text
        assert "customer-portal" not in failure.text
        clock[0] += 3601
        assert browser.post(base + "/api/billing/session", json={}).status_code == 401
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join()
