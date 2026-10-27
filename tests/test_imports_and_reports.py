from __future__ import annotations

import pytest
import requests_mock

from nozle import (
    BillingStateImportRequest,
    CreditBalanceImportRequest,
    Nozle,
    NozleAPIError,
    NozleAuthenticationError,
    NozleValidationError,
)

ENGINE = "https://api.example/engine"
CORE = "https://api.example/core"

CREDIT_IMPORT: CreditBalanceImportRequest = {
    "external_subscription_id": "merchant_subscription_123",
    "credit_system_code": "ai_credits",
    "granted_amount": "500",
    "remaining_amount": "320",
}
BILLING_IMPORT: BillingStateImportRequest = {
    "subscription": {
        "plan_code": "pro_monthly",
        "status": "active",
        "billing_authority": "external",
        "external_reference": "merchant_subscription_123",
        "current_period_start": "2026-10-15T00:00:00Z",
        "current_period_end": "2026-11-15T00:00:00Z",
    }
}


def client(key: str = "sk_test") -> Nozle:
    return Nozle(key, base_url=ENGINE, events_url=CORE)


def test_import_credit_balance_posts_to_the_engine_with_the_idempotency_key() -> None:
    result = {"external_customer_id": "workspace 1", "dry_run": True, "replayed": False}
    with requests_mock.Mocker() as mock:
        route = mock.post(
            f"{ENGINE}/api/v1/customers/workspace%201/credit-balance-imports",
            json={"credit_balance_import": result},
            status_code=200,
        )
        body: CreditBalanceImportRequest = {
            **CREDIT_IMPORT,
            "external_entity_id": "user_42",
            "dry_run": True,
        }
        assert (
            client().customers.import_credit_balance("workspace 1", body, idempotency_key="imp-1")
            == result
        )
    sent = route.last_request
    assert sent.headers["Idempotency-Key"] == "imp-1"
    assert sent.headers["Authorization"] == "Bearer sk_test"
    assert sent.json() == {**CREDIT_IMPORT, "external_entity_id": "user_42", "dry_run": True}


def test_import_credit_balance_maps_errors() -> None:
    with requests_mock.Mocker() as mock:
        mock.post(
            requests_mock.ANY,
            json={"code": "idempotency_key_reused", "error": "different request"},
            status_code=409,
            headers={"X-Request-Id": "req-1"},
        )
        with pytest.raises(NozleAPIError) as caught:
            client().customers.import_credit_balance("c", CREDIT_IMPORT, idempotency_key="imp-2")
    assert caught.value.status_code == 409
    assert caught.value.code == "idempotency_key_reused"
    assert caught.value.request_id == "req-1"


def test_import_billing_state_wraps_the_body_and_posts_to_core() -> None:
    result = {
        "status": "validated",
        "dry_run": True,
        "external_customer_id": "c/1",
        "subscription": {},
        "replayed": False,
    }
    with requests_mock.Mocker() as mock:
        route = mock.post(
            f"{CORE}/api/v1/customers/c%2F1/billing-state-imports",
            json={"billing_state_import": result},
            status_code=201,
        )
        body: BillingStateImportRequest = {**BILLING_IMPORT, "dry_run": True}
        assert (
            client().customers.import_billing_state("c/1", body, idempotency_key="bs-1") == result
        )
    assert route.last_request.headers["Idempotency-Key"] == "bs-1"
    assert route.last_request.json() == {
        "billing_state_import": {**BILLING_IMPORT, "dry_run": True}
    }


def test_import_billing_state_maps_validation_errors() -> None:
    with requests_mock.Mocker() as mock:
        mock.post(
            requests_mock.ANY,
            json={
                "code": "validation_errors",
                "error_details": {"current_period": ["must_contain_current_time"]},
            },
            status_code=422,
        )
        with pytest.raises(NozleAPIError) as caught:
            client().customers.import_billing_state("c", BILLING_IMPORT, idempotency_key="bs-2")
    assert caught.value.status_code == 422
    assert caught.value.has_code("must_contain_current_time")


@pytest.mark.parametrize("key", ["", "  ", "é" * 128])
def test_imports_need_an_idempotency_key_up_to_255_bytes(key: str) -> None:
    with requests_mock.Mocker() as mock:
        with pytest.raises(NozleValidationError, match="idempotency_key"):
            client().customers.import_credit_balance("c", CREDIT_IMPORT, idempotency_key=key)
        with pytest.raises(NozleValidationError, match="idempotency_key"):
            client().customers.import_billing_state("c", BILLING_IMPORT, idempotency_key=key)
    assert not mock.called


def test_imports_need_a_secret_key_and_a_customer() -> None:
    with requests_mock.Mocker() as mock:
        with pytest.raises(NozleAuthenticationError):
            client("pk_test").customers.import_credit_balance(
                "c", CREDIT_IMPORT, idempotency_key="k"
            )
        with pytest.raises(NozleValidationError, match="customer_id"):
            client().customers.import_billing_state(" ", BILLING_IMPORT, idempotency_key="k")
    assert not mock.called


def test_margin_forecast_and_simulate() -> None:
    simulation = {"metric_code": "agent_execution", "new_revenue_cents": 29500, "event_count": 295}
    with requests_mock.Mocker() as mock:
        forecast = mock.get(
            f"{ENGINE}/api/v1/margin/forecast", json={"points": [], "confidence": "low"}
        )
        simulate = mock.post(f"{ENGINE}/api/v1/margin/simulate", json=simulation)
        nozle = client()
        assert nozle.margin.forecast(from_date="2026-10-01") == {"points": [], "confidence": "low"}
        assert nozle.margin.simulate("agent_execution", 1, from_date="2026-07-01") == simulation
    assert forecast.last_request.qs == {"from_date": ["2026-10-01"]}
    assert simulate.last_request.qs == {"from_date": ["2026-07-01"]}
    assert simulate.last_request.json() == {
        "metric_code": "agent_execution",
        "new_price_dollars": 1,
    }


def test_margin_errors_and_local_validation() -> None:
    with requests_mock.Mocker() as mock:
        mock.get(
            requests_mock.ANY, json={"code": "invalid_date_range", "error": "bad"}, status_code=400
        )
        mock.post(requests_mock.ANY, json={"code": "analytics_unavailable"}, status_code=503)
        with pytest.raises(NozleAPIError) as forecast_error:
            client().margin.forecast(from_date="x")
        with pytest.raises(NozleAPIError) as simulate_error:
            client().margin.simulate("m", 0)
        calls = mock.call_count
        for price in (-1, float("nan"), True):
            with pytest.raises(NozleValidationError, match="new_price_dollars"):
                client().margin.simulate("m", price)
        with pytest.raises(NozleValidationError, match="metric_code"):
            client().margin.simulate("", 1)
        assert mock.call_count == calls
    assert (forecast_error.value.status_code, forecast_error.value.code) == (
        400,
        "invalid_date_range",
    )
    assert simulate_error.value.code == "analytics_unavailable"


def test_cost_intelligence_report_and_shared_costs() -> None:
    with requests_mock.Mocker() as mock:
        report = mock.get(f"{ENGINE}/api/v1/cost-intelligence/report", json={"summary": {}})
        preview = mock.post(
            f"{ENGINE}/api/v1/cost-intelligence/shared-costs/pool%2F1/preview",
            json={"allocations": []},
        )
        allocate = mock.post(
            f"{ENGINE}/api/v1/cost-intelligence/shared-costs/pool%2F1/allocate",
            json={"status": "completed"},
        )
        nozle = client()
        nozle.cost_intelligence.report(
            from_date="2026-10-01T00:00:00Z", to_date="2026-10-27T00:00:00Z", limit=50
        )
        nozle.cost_intelligence.report()
        nozle.cost_intelligence.preview_shared_cost_allocation("pool/1", "2026-10-01T00:00:00Z")
        assert nozle.cost_intelligence.allocate_shared_cost("pool/1", "2026-10-01T00:00:00Z") == {
            "status": "completed"
        }
    assert report.request_history[0].qs == {
        "from": ["2026-10-01t00:00:00z"],
        "to": ["2026-10-27t00:00:00z"],
        "limit": ["50"],
    }
    assert report.request_history[1].qs == {}
    assert preview.last_request.json() == {"allocation_period_start": "2026-10-01T00:00:00Z"}
    assert allocate.last_request.json() == {"allocation_period_start": "2026-10-01T00:00:00Z"}


def test_cost_intelligence_errors_and_validation() -> None:
    with requests_mock.Mocker() as mock:
        mock.post(
            requests_mock.ANY, json={"error": "allocation period is still open"}, status_code=409
        )
        with pytest.raises(NozleAPIError) as caught:
            client().cost_intelligence.allocate_shared_cost("p", "2026-10-01T00:00:00Z")
        calls = mock.call_count
        with pytest.raises(NozleAuthenticationError):
            client("pk_test").cost_intelligence.report()
        with pytest.raises(NozleValidationError, match="allocation_period_start"):
            client().cost_intelligence.preview_shared_cost_allocation("p", "")
        assert mock.call_count == calls
    assert caught.value.status_code == 409
