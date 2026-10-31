from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Dict, Optional, cast, get_args

from nozle._operation import M, Resource, Steps, endpoint
from nozle._transport import BaseTransport
from nozle._validation import (
    path_segment,
    require_non_empty,
    require_secret_key,
    utc_now_rfc3339_milliseconds,
    validate_entity_id,
    validate_idempotency_key,
)
from nozle.errors import NozleValidationError
from nozle.types import (
    CreditHoldList,
    CreditHoldStatus,
    Hold,
    HoldFinalizeResult,
    HoldResult,
    JSONMapping,
    UsageCheckResult,
    UsageTrackResult,
)

#: Engine's longest hold, in seconds (24 hours). Engine's default is 900 (15 minutes).
MAX_HOLD_TTL_SECONDS = 86400


class UsageNamespace(Resource[M]):
    """Check ledger usage without mutation or consume credits idempotently."""

    def __init__(self, transport: BaseTransport[M], api_key: str) -> None:
        super().__init__(transport, api_key)
        # Timestamps the SDK filled in, by idempotency key, so a retry of a key sends the same
        # request.
        # shortcut: lives in this client's memory, so a retry from another process must pass
        # `timestamp`; keeps the last 1000 keys.
        self._generated_timestamps: OrderedDict[str, str] = OrderedDict()
        self.holds = UsageHoldsNamespace(transport, api_key, self._timestamp_for)

    @endpoint
    def check(
        self,
        customer_id: str,
        feature_code: str,
        *,
        entity_id: Optional[str] = None,
        credit_system_code: Optional[str] = None,
        properties: Optional[JSONMapping] = None,
        occurred_at: Optional[str] = None,
    ) -> Steps[UsageCheckResult]:
        operation = "usage.check"
        require_secret_key(self._api_key, operation)
        self._validate(customer_id, entity_id, feature_code, operation)
        body = {
            "customer_id": customer_id,
            "feature_code": feature_code,
            "properties": properties or {},
            "occurred_at": occurred_at or utc_now_rfc3339_milliseconds(),
        }
        if entity_id is not None:
            body["entity_id"] = entity_id
        if credit_system_code:
            body["credit_system_code"] = credit_system_code
        return cast(
            UsageCheckResult,
            (
                yield self._transport.call(
                    operation, "POST", "/api/v1/usage/check", json_body=body, idempotent=True
                )
            ),
        )

    @endpoint
    def track(
        self,
        customer_id: str,
        feature_code: str,
        *,
        idempotency_key: str,
        entity_id: Optional[str] = None,
        credit_system_code: Optional[str] = None,
        properties: Optional[JSONMapping] = None,
        timestamp: Optional[str] = None,
    ) -> Steps[UsageTrackResult]:
        operation = "usage.track"
        require_secret_key(self._api_key, operation)
        self._validate(customer_id, entity_id, feature_code, operation)
        validate_idempotency_key(idempotency_key, operation)
        body = {
            "customer_id": customer_id,
            "feature_code": feature_code,
            "properties": properties or {},
            "timestamp": timestamp or self._timestamp_for(idempotency_key),
        }
        if entity_id is not None:
            body["entity_id"] = entity_id
        if credit_system_code:
            body["credit_system_code"] = credit_system_code
        return cast(
            UsageTrackResult,
            (
                yield self._transport.call(
                    operation,
                    "POST",
                    "/api/v1/usage/track",
                    json_body=body,
                    headers={"Idempotency-Key": idempotency_key},
                )
            ),
        )

    def _timestamp_for(self, idempotency_key: str) -> str:
        known = self._generated_timestamps.get(idempotency_key)
        if known is None:
            known = utc_now_rfc3339_milliseconds()
            self._generated_timestamps[idempotency_key] = known
            if len(self._generated_timestamps) > 1000:
                self._generated_timestamps.popitem(last=False)
        return known

    @staticmethod
    def _validate(
        customer_id: str,
        entity_id: Optional[str],
        feature_code: str,
        operation: str,
    ) -> None:
        require_non_empty(customer_id, "customer_id", operation)
        if entity_id is not None:
            validate_entity_id(entity_id, operation)
        require_non_empty(feature_code, "feature_code", operation)


class UsageHoldsNamespace(Resource[M]):
    """Reserve credits before work whose cost is known after it, then finalize or release.

    On a Feature billed in money under a hard spend limit, a hold is a budget hold
    (``kind`` ``"budget"``, Engine 2.34.0+) that reserves the call's estimated spend.
    """

    def __init__(
        self,
        transport: BaseTransport[M],
        api_key: str,
        timestamp_for: Callable[[str], str],
    ) -> None:
        super().__init__(transport, api_key)
        self._timestamp_for = timestamp_for

    @endpoint
    def create(
        self,
        customer_id: str,
        feature_code: str,
        *,
        idempotency_key: str,
        entity_id: Optional[str] = None,
        credit_system_code: Optional[str] = None,
        properties: Optional[JSONMapping] = None,
        timestamp: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> Steps[HoldResult]:
        operation = "usage.holds.create"
        require_secret_key(self._api_key, operation)
        UsageNamespace._validate(customer_id, entity_id, feature_code, operation)
        validate_idempotency_key(idempotency_key, operation)
        if ttl_seconds is not None and (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 1 <= ttl_seconds <= MAX_HOLD_TTL_SECONDS
        ):
            raise NozleValidationError(
                f"{operation} ttl_seconds must be an integer from 1 to {MAX_HOLD_TTL_SECONDS}"
            )
        body: Dict[str, object] = {
            "customer_id": customer_id,
            "feature_code": feature_code,
            "properties": properties or {},
            "timestamp": timestamp or self._timestamp_for(idempotency_key),
        }
        if entity_id is not None:
            body["entity_id"] = entity_id
        if credit_system_code:
            body["credit_system_code"] = credit_system_code
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        return cast(
            HoldResult,
            (
                yield self._transport.call(
                    operation,
                    "POST",
                    "/api/v1/usage/holds",
                    json_body=body,
                    headers={"Idempotency-Key": idempotency_key},
                )
            ),
        )

    @endpoint
    def finalize(
        self,
        hold_id: str,
        *,
        properties: Optional[JSONMapping] = None,
        timestamp: Optional[str] = None,
    ) -> Steps[HoldFinalizeResult]:
        operation = "usage.holds.finalize"
        require_secret_key(self._api_key, operation)
        require_non_empty(hold_id, "hold_id", operation)
        # Engine replays a finalized hold only for the same body, so a retry keeps the timestamp.
        body = {
            "properties": properties or {},
            "timestamp": timestamp or self._timestamp_for(f"hold:{hold_id}"),
        }
        return cast(
            HoldFinalizeResult,
            (
                yield self._transport.call(
                    operation,
                    "POST",
                    f"{self._path(hold_id)}/finalize",
                    json_body=body,
                    idempotent=True,
                )
            ),
        )

    @endpoint
    def release(self, hold_id: str) -> Steps[Hold]:
        operation = "usage.holds.release"
        require_secret_key(self._api_key, operation)
        require_non_empty(hold_id, "hold_id", operation)
        return cast(
            Hold,
            (
                yield self._transport.call(
                    operation, "POST", f"{self._path(hold_id)}/release", idempotent=True
                )
            ),
        )

    @endpoint
    def get(self, hold_id: str) -> Steps[Hold]:
        operation = "usage.holds.get"
        require_secret_key(self._api_key, operation)
        require_non_empty(hold_id, "hold_id", operation)
        return cast(Hold, (yield self._transport.call(operation, "GET", self._path(hold_id))))

    @endpoint
    def list(
        self, customer_id: str, *, status: Optional[CreditHoldStatus] = None
    ) -> Steps[CreditHoldList]:
        operation = "usage.holds.list"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        params: Dict[str, str] = {}
        if status is not None:
            if status not in get_args(CreditHoldStatus):
                raise NozleValidationError(
                    f"{operation} status must be one of {', '.join(get_args(CreditHoldStatus))}"
                )
            params["status"] = status
        return cast(
            CreditHoldList,
            (
                yield self._transport.call(
                    operation,
                    "GET",
                    f"/api/v1/customers/{path_segment(customer_id)}/credit-holds",
                    params=params,
                )
            ),
        )

    @staticmethod
    def _path(hold_id: str) -> str:
        return f"/api/v1/usage/holds/{path_segment(hold_id)}"
