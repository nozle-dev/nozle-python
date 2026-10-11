from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from nozle._transport import HttpTransport
from nozle._validation import require_non_empty, require_secret_key
from nozle.errors import NozleValidationError
from nozle.identifiers import create_transaction_id
from nozle.types import JSONMapping

_RFC3339 = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


def _event_timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise NozleValidationError("track timestamp must be RFC3339 or Unix seconds")
    try:
        seconds = Decimal(value)
    except InvalidOperation:
        seconds = None
    if seconds is not None and seconds.is_finite() and seconds >= 0:
        return value
    if _RFC3339.fullmatch(value) is None:
        raise NozleValidationError("track timestamp must be RFC3339 or Unix seconds")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise NozleValidationError("track timestamp must be RFC3339 or Unix seconds") from error
    if parsed.tzinfo is None:
        raise NozleValidationError("track timestamp must be RFC3339 or Unix seconds")
    elapsed = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    milliseconds = (
        elapsed.days * 86_400_000 + elapsed.seconds * 1_000 + elapsed.microseconds // 1_000
    )
    if milliseconds < 0:
        raise NozleValidationError("track timestamp must be RFC3339 or Unix seconds")
    return f"{milliseconds // 1_000}.{milliseconds % 1_000:03d}"


def track(
    transport: HttpTransport,
    api_key: str,
    customer_id: str,
    event: str,
    metadata: Optional[JSONMapping] = None,
    subscription_id: Optional[str] = None,
    transaction_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> str:
    operation = "track"
    require_secret_key(api_key, operation)
    require_non_empty(customer_id, "customer_id", operation)
    require_non_empty(event, "event", operation)
    resolved_transaction_id = transaction_id or create_transaction_id()
    body = {
        "transaction_id": resolved_transaction_id,
        "external_customer_id": customer_id,
        "code": event,
        "properties": metadata or {},
    }

    if subscription_id:
        body["external_subscription_id"] = subscription_id

    if timestamp:
        body["timestamp"] = _event_timestamp(timestamp)

    transport.request(
        operation,
        "POST",
        "/api/v1/events",
        json_body={"event": body},
        expect_json=False,
    )
    return resolved_transaction_id
