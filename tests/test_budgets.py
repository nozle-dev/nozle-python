from __future__ import annotations

from typing import Any, Dict

import httpx
import pytest
import requests_mock

from nozle import (
    AsyncNozle,
    Budget,
    BudgetExceededError,
    Nozle,
    NozleAPIError,
    NozleAuthenticationError,
    NozleValidationError,
    RefusalReason,
    UsageLimitExceededError,
)

ENGINE = "https://api.example/engine"
BUDGET: Budget = {
    "feature_code": "agent_execution",
    "limit": "50",
    "spent": "49.5",
    "held": "0.5",
    "remaining": "0",
    "currency": "USD",
    "period": "billing_period",
    "period_start": "2026-10-01T00:00:00Z",
    "period_end": "2026-11-01T00:00:00Z",
    "mode": "hard",
    "status": "exceeded",
    "as_of": "2026-10-31T11:59:58.123Z",
}
BUDGET_HOLD: Dict[str, Any] = {
    "hold_id": "bh_1",
    "kind": "budget",
    "status": "held",
    "customer_id": "acme",
    "subscription_id": "sub_1",
    "feature_code": "agent_execution",
    "metric_amount": "1000",
    "amount_held": "0.25",
    "currency": "USD",
    "expires_at": "2026-10-31T12:15:00Z",
    "created_at": "2026-10-31T12:00:00Z",
    "resolved_at": None,
}


def client() -> Nozle:
    return Nozle("sk_test", base_url=ENGINE, retry_delay=0, max_retries=0)


def test_can_returns_budget_exceeded_with_the_budget(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(
        f"{ENGINE}/api/v1/can",
        json={
            "allowed": False,
            "reason": "budget_exceeded",
            "used": 0,
            "limit": None,
            "remaining": None,
            "budget": BUDGET,
        },
    )
    result = client().can("acme", "agent_execution")
    assert result["allowed"] is False
    assert result["reason"] == RefusalReason.BUDGET_EXCEEDED
    assert result["budget"] == BUDGET


def test_an_unknown_reason_passes_through(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(
        f"{ENGINE}/api/v1/can",
        json={
            "allowed": False,
            "reason": "some_new_reason",
            "used": 0,
            "limit": None,
            "remaining": None,
        },
    )
    assert client().can("acme", "f")["reason"] == "some_new_reason"


def test_usage_check_and_track_carry_the_budget_refusal(
    requests_mock: requests_mock.Mocker,
) -> None:
    refusal = {"allowed": False, "reason": "budget_exceeded", "budget": BUDGET}
    requests_mock.post(f"{ENGINE}/api/v1/usage/check", json=refusal)
    requests_mock.post(f"{ENGINE}/api/v1/usage/track", json=refusal)
    check = client().usage.check("acme", "agent_execution")
    assert check["reason"] == RefusalReason.BUDGET_EXCEEDED and check["budget"] == BUDGET
    track = client().usage.track("acme", "agent_execution", idempotency_key="k1")
    assert track["budget"]["status"] == "exceeded"


def test_budgets_list_reads_the_route_with_subscription_id(
    requests_mock: requests_mock.Mocker,
) -> None:
    body = {
        "customer_id": "acme/1",
        "subscription_id": "sub_1",
        "budgets": [BUDGET, {**BUDGET, "feature_code": None}],
    }
    matcher = requests_mock.get(f"{ENGINE}/api/v1/customers/acme%2F1/budgets", json=body)
    result = client().budgets.list("acme/1", subscription_id="sub_1")
    assert result == body
    assert matcher.last_request is not None
    assert matcher.last_request.qs == {"subscription_id": ["sub_1"]}
    client().budgets.list("acme/1")
    assert matcher.last_request is not None
    assert matcher.last_request.qs == {}


def test_budgets_list_validates_before_any_request(requests_mock: requests_mock.Mocker) -> None:
    with pytest.raises(NozleValidationError):
        client().budgets.list(" ")
    with pytest.raises(NozleAuthenticationError):
        Nozle("pk_test", base_url=ENGINE).budgets.list("acme")
    assert requests_mock.call_count == 0


def test_a_402_budget_exceeded_raises_budget_exceeded_error(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.get(
        f"{ENGINE}/api/v1/customers/acme/budgets",
        status_code=402,
        json={"error": {"code": "budget_exceeded"}, "code": "budget_exceeded", "budget": BUDGET},
    )
    with pytest.raises(BudgetExceededError) as raised:
        client().budgets.list("acme")
    assert isinstance(raised.value, NozleAPIError)
    assert raised.value.status_code == 402
    assert raised.value.budget == BUDGET


def test_a_429_usage_limit_exceeded_raises_usage_limit_exceeded_error(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.get(
        f"{ENGINE}/api/v1/customers/acme/budgets",
        status_code=429,
        json={"code": "usage_limit_exceeded"},
    )
    with pytest.raises(UsageLimitExceededError) as raised:
        client().budgets.list("acme")
    assert raised.value.code == RefusalReason.USAGE_LIMIT_EXCEEDED


@pytest.mark.parametrize(
    "status,code",
    [(402, "insufficient_credits"), (429, "budget_exceeded"), (402, "usage_limit_exceeded")],
)
def test_other_codes_stay_a_plain_api_error(
    requests_mock: requests_mock.Mocker, status: int, code: str
) -> None:
    requests_mock.get(
        f"{ENGINE}/api/v1/customers/acme/budgets", status_code=status, json={"code": code}
    )
    with pytest.raises(NozleAPIError) as raised:
        client().budgets.list("acme")
    assert type(raised.value) is NozleAPIError


def test_budget_is_none_when_the_402_carries_no_budget() -> None:
    assert BudgetExceededError("x", 402, {"code": "budget_exceeded"}).budget is None


def test_budget_holds_carry_kind_amount_held_and_currency(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        f"{ENGINE}/api/v1/usage/holds",
        json={
            **BUDGET_HOLD,
            "status": "denied",
            "amount_held": "0",
            "allowed": False,
            "reason": "budget_exceeded",
            "budget": BUDGET,
        },
    )
    requests_mock.get(f"{ENGINE}/api/v1/usage/holds/bh_1", json=BUDGET_HOLD)
    requests_mock.post(
        f"{ENGINE}/api/v1/usage/holds/bh_1/finalize", json={**BUDGET_HOLD, "status": "finalized"}
    )
    created = client().usage.holds.create("acme", "agent_execution", idempotency_key="run-1")
    assert created["kind"] == "budget"  # type: ignore[typeddict-item]
    assert created["reason"] == RefusalReason.BUDGET_EXCEEDED
    assert created["budget"] == BUDGET  # type: ignore[typeddict-item]
    got = client().usage.holds.get("bh_1")
    assert got["amount_held"] == "0.25" and got["currency"] == "USD"  # type: ignore[typeddict-item]
    finalized = client().usage.holds.finalize("bh_1", properties={"tokens": 900})
    assert finalized["status"] == "finalized"  # type: ignore[typeddict-item]


async def test_async_budgets_list_and_budget_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("subscription_id") == "sub_1":
            return httpx.Response(
                200, json={"customer_id": "acme", "subscription_id": "sub_1", "budgets": [BUDGET]}
            )
        return httpx.Response(402, json={"code": "budget_exceeded", "budget": BUDGET})

    nozle = AsyncNozle(
        "sk_test",
        base_url=ENGINE,
        max_retries=0,
        _client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    listed = await nozle.budgets.list("acme", subscription_id="sub_1")
    assert listed["budgets"] == [BUDGET]
    with pytest.raises(BudgetExceededError) as raised:
        await nozle.budgets.list("acme")
    assert raised.value.budget == BUDGET
