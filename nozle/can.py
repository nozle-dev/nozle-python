from __future__ import annotations

import json
from typing import Any, Mapping, Optional, cast

from nozle._operation import Steps, run_sync
from nozle._transport import BaseTransport, HttpTransport
from nozle._validation import require_non_empty, require_secret_key
from nozle.types import CanResult


def can(
    transport: HttpTransport,
    api_key: str,
    customer_id: str,
    feature: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> CanResult:
    return run_sync(can_steps(transport, api_key, customer_id, feature, metadata))


def can_steps(
    transport: BaseTransport[Any],
    api_key: str,
    customer_id: str,
    feature: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Steps[CanResult]:
    operation = "can"
    require_secret_key(api_key, operation)
    require_non_empty(customer_id, "customer_id", operation)
    require_non_empty(feature, "feature", operation)
    params = {"customer_id": customer_id, "feature": feature}
    if metadata:
        params["metadata"] = json.dumps(metadata)
    return cast(CanResult, (yield transport.call(operation, "GET", "/api/v1/can", params=params)))
