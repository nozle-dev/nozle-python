from __future__ import annotations

from typing import Dict, Optional, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import path_segment, require_non_empty, require_secret_key
from nozle.types import BudgetList


class BudgetsNamespace(Resource[M]):
    """Read money budgets (spend limits) and their standing (Engine 2.34.0+)."""

    @endpoint
    def list(self, customer_id: str, *, subscription_id: Optional[str] = None) -> Steps[BudgetList]:
        """Every spend limit of the customer's subscription with its spend, open holds and status
        in the current period. ``subscription_id`` picks the subscription of a customer with
        several."""
        operation = "budgets.list"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        params: Dict[str, str] = {}
        if subscription_id:
            params["subscription_id"] = subscription_id
        return cast(
            BudgetList,
            (
                yield self._transport.call(
                    operation,
                    "GET",
                    f"/api/v1/customers/{path_segment(customer_id)}/budgets",
                    params=params,
                )
            ),
        )
