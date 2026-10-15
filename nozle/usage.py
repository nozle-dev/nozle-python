from __future__ import annotations

from collections import OrderedDict
from typing import Optional, cast

from nozle._transport import HttpTransport
from nozle._validation import (
    require_non_empty,
    require_secret_key,
    utc_now_rfc3339_milliseconds,
    validate_entity_id,
    validate_idempotency_key,
)
from nozle.types import JSONMapping, UsageCheckResult, UsageTrackResult


class UsageNamespace:
    """Check ledger usage without mutation or consume credits idempotently."""

    def __init__(self, transport: HttpTransport, api_key: str) -> None:
        self._transport = transport
        # Timestamps the SDK filled in, by idempotency key, so a retry of a key sends the same request.
        # shortcut: lives in this client's memory, so a retry from another process must pass `timestamp`; keeps the last 1000 keys.
        self._generated_timestamps: OrderedDict[str, str] = OrderedDict()
        self._api_key = api_key

    def check(
        self,
        customer_id: str,
        feature_code: str,
        *,
        entity_id: Optional[str] = None,
        credit_system_code: Optional[str] = None,
        properties: Optional[JSONMapping] = None,
        occurred_at: Optional[str] = None,
    ) -> UsageCheckResult:
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
            self._transport.request(operation, "POST", "/api/v1/usage/check", json_body=body),
        )

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
    ) -> UsageTrackResult:
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
            self._transport.request(
                operation,
                "POST",
                "/api/v1/usage/track",
                json_body=body,
                headers={"Idempotency-Key": idempotency_key},
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
