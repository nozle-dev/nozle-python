from __future__ import annotations

from typing import Any, Dict, Optional, Union, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import path_segment, require_non_empty, require_secret_key
from nozle.types import CostIntelligenceReport, SharedCostAllocationPreview, SharedCostAllocationRun


class CostIntelligenceNamespace(Resource[M]):
    """Ledger-backed cost report and Shared Period Cost allocation (Engine, secret keys)."""

    @endpoint
    def report(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Steps[CostIntelligenceReport]:
        """Cost totals, breakdowns, ledger entries and review queues; sent as ``from``/``to``."""
        operation = "cost_intelligence.report"
        require_secret_key(self._api_key, operation)
        query: Dict[str, Union[str, int]] = {}
        if from_date is not None:
            query["from"] = from_date
        if to_date is not None:
            query["to"] = to_date
        if limit is not None:
            query["limit"] = limit
        payload = yield self._transport.call(
            operation, "GET", "/api/v1/cost-intelligence/report", params=query
        )
        return cast(CostIntelligenceReport, payload)

    @endpoint
    def preview_shared_cost_allocation(
        self, pool_id: str, allocation_period_start: str
    ) -> Steps[SharedCostAllocationPreview]:
        """The recognized amount and customer split of one pool's period, without writing."""
        payload = yield from self._allocation(
            "preview_shared_cost_allocation", "preview", pool_id, allocation_period_start
        )
        return cast(SharedCostAllocationPreview, payload)

    @endpoint
    def allocate_shared_cost(
        self, pool_id: str, allocation_period_start: str
    ) -> Steps[SharedCostAllocationRun]:
        """Book a closed period now (Nozle also does it within about 15 minutes of the close)."""
        payload = yield from self._allocation(
            "allocate_shared_cost", "allocate", pool_id, allocation_period_start
        )
        return cast(SharedCostAllocationRun, payload)

    def _allocation(
        self, name: str, action: str, pool_id: str, allocation_period_start: str
    ) -> Steps[Any]:
        operation = f"cost_intelligence.{name}"
        require_secret_key(self._api_key, operation)
        require_non_empty(pool_id, "pool_id", operation)
        require_non_empty(allocation_period_start, "allocation_period_start", operation)
        return (
            yield self._transport.call(
                operation,
                "POST",
                f"/api/v1/cost-intelligence/shared-costs/{path_segment(pool_id)}/{action}",
                json_body={"allocation_period_start": allocation_period_start},
                # A preview only reads; a repeated allocation of a period returns the first run.
                idempotent=True,
            )
        )
