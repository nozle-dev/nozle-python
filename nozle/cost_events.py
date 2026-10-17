from __future__ import annotations

from typing import Mapping, Optional, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import require_non_empty, require_secret_key
from nozle.errors import NozleAPIError, NozleValidationError
from nozle.identifiers import create_cost_event_id
from nozle.types import CostEventAccepted, JSONMapping


class CostEventsNamespace(Resource[M]):
    def create_cost_event_id(self) -> str:
        return create_cost_event_id()

    @endpoint
    def track(
        self,
        *,
        cost_meter_code: str,
        cost_event_id: Optional[str] = None,
        parent_transaction_id: Optional[str] = None,
        external_customer_id: Optional[str] = None,
        request_id: Optional[str] = None,
        operation_key: Optional[str] = None,
        properties: Optional[JSONMapping] = None,
        timestamp: Optional[float] = None,
    ) -> Steps[CostEventAccepted]:
        operation = "cost_events.track"
        require_secret_key(self._api_key, operation)
        require_non_empty(cost_meter_code, "cost_meter_code", operation)
        if not (parent_transaction_id and parent_transaction_id.strip()) and not (
            external_customer_id and external_customer_id.strip()
        ):
            raise NozleValidationError(
                f"{operation} requires parent_transaction_id or external_customer_id"
            )

        resolved_cost_event_id = cost_event_id or create_cost_event_id()
        body: JSONMapping = {
            "cost_event_id": resolved_cost_event_id,
            "cost_meter_code": cost_meter_code,
            "properties": properties or {},
        }
        optional_values: Mapping[str, object] = {
            "parent_transaction_id": parent_transaction_id,
            "external_customer_id": external_customer_id,
            "request_id": request_id,
            "operation_key": operation_key,
            "timestamp": timestamp,
        }
        body.update({key: value for key, value in optional_values.items() if value is not None})

        payload = yield self._transport.call(
            operation,
            "POST",
            "/api/v1/cost-events",
            json_body=body,
            idempotent=True,
        )
        if not isinstance(payload, Mapping):
            raise NozleAPIError(operation, 202, "response was not a JSON object")
        return cast(CostEventAccepted, payload)
