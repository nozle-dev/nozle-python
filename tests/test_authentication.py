from __future__ import annotations

from typing import Callable

import pytest
import requests_mock

from nozle import Nozle, NozleAuthenticationError


@pytest.mark.parametrize(
    "invoke",
    [
        lambda c: c.track("customer", "event", subscription_id="subscription"),
        lambda c: c.can("customer", "feature"),
        lambda c: c.checkout("customer", "plan"),
        lambda c: c.subscribe("customer", "plan"),
        lambda c: c.ping(),
        lambda c: c.check_and_deduct("customer", credits=1),
        lambda c: c.customers.upsert("customer"),
        lambda c: c.margin.summary(),
        lambda c: c.margin.by_customer(),
        lambda c: c.margin.by_metric(),
        lambda c: c.margin.by_plan(),
        lambda c: c.margin.by_model(),
        lambda c: c.margin.trend(),
        lambda c: c.credit_systems.list(),
        lambda c: c.credits.get_balance("customer", "system"),
        lambda c: c.credits.list_balances("customer"),
        lambda c: c.credits.list_operations("customer"),
        lambda c: c.credits.get_entity_balance("customer", "entity", "system"),
        lambda c: c.credits.list_entity_balances("customer", "entity"),
        lambda c: c.credits.list_entity_operations("customer", "entity"),
        lambda c: c.credits.allocate(
            "customer",
            "entity",
            credit_system_code="system",
            amount="1",
            idempotency_key="key",
        ),
        lambda c: c.credits.deallocate(
            "customer",
            "entity",
            credit_system_code="system",
            amount="1",
            idempotency_key="key",
        ),
        lambda c: c.entities.get("customer", "entity"),
        lambda c: c.entities.list("customer"),
        lambda c: c.entities.upsert("customer", "entity", status="active", idempotency_key="key"),
        lambda c: c.entities.suspend("customer", "entity", idempotency_key="key"),
        lambda c: c.entities.activate("customer", "entity", idempotency_key="key"),
        lambda c: c.entities.bulk_upsert(
            "customer",
            [{"external_id": "entity", "status": "active"}],
            idempotency_key="key",
        ),
        lambda c: c.usage.check("customer", "metric"),
        lambda c: c.usage.track("customer", "metric", idempotency_key="key"),
    ],
)
def test_publishable_keys_are_rejected_locally_for_all_protected_operations(
    requests_mock: requests_mock.Mocker,
    invoke: Callable[[Nozle], object],
) -> None:
    with pytest.raises(NozleAuthenticationError, match="secret key"):
        invoke(Nozle("pk_browser"))
    assert requests_mock.request_history == []
