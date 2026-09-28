from typing import Any, cast

import pytest

from nozle import Nozle
from nozle.errors import NozleValidationError
from nozle.types import SubscriptionTransitionParams

PARAMS: SubscriptionTransitionParams = {
    "customer_id": "customer",
    "subscription_id": "external-subscription",
    "operation": "cancel",
    "timing": "end_of_period",
    "expected_effective_at": "2027-01-01T00:00:00.123456Z",
}


def test_exact_preview_date_is_forwarded_without_rounding(requests_mock: Any) -> None:
    requests_mock.post("http://engine/api/v1/subscriptions/transitions", json={})
    with Nozle("sk_test", base_url="http://engine") as sdk:
        sdk.apply_subscription_transition(PARAMS, idempotency_key="confirmation-1")
    assert (
        requests_mock.last_request.json()["expected_effective_at"]
        == PARAMS["expected_effective_at"]
    )
    assert requests_mock.last_request.headers["Idempotency-Key"] == "confirmation-1"


@pytest.mark.parametrize(
    "invalid",
    [
        {"operation": "uncancel"},
        {"operation": "downgrade", "target_plan_code": "lower"},
        {"timing": "immediate"},
        {"timing": None},
        {"expected_effective_at": "2027-01-01"},
        {"expected_effective_at": "2027-01-01T00:00:00"},
        {"expected_effective_at": "not-a-date"},
    ],
)
def test_invalid_guard_is_rejected_before_network_io(invalid: Any, requests_mock: Any) -> None:
    with Nozle("sk_test", base_url="http://engine") as sdk:
        with pytest.raises(NozleValidationError, match="expected_effective_at"):
            sdk.apply_subscription_transition(
                cast(SubscriptionTransitionParams, {**PARAMS, **invalid}),
                idempotency_key="confirmation-1",
            )
    assert requests_mock.call_count == 0
