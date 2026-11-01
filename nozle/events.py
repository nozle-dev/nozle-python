from __future__ import annotations

from typing import Mapping, cast
from urllib.parse import quote

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import require_non_empty, require_secret_key
from nozle.errors import NozleAPIError, NozleValidationError
from nozle.identifiers import create_transaction_id
from nozle.types import EventVoidResult


class EventsNamespace(Resource[M]):
    def create_transaction_id(self) -> str:
        return create_transaction_id()

    @endpoint
    def void(self, transaction_id: str, *, reason: str) -> Steps[EventVoidResult]:
        """Void a recorded usage event (``POST /events/{transaction_id}/void``, Engine).

        Reverses its usage, credits, cost and margin. Idempotent: a replay answers
        ``status: "already_voided"``. A refusal raises ``NozleAPIError``; test it with
        ``has_code(EventVoidErrorCode...)`` (``event_not_found`` 404, ``period_invoiced`` 409,
        ``reason_required`` 422). Retried like ``track``, since a repeat has no further effect.
        """
        operation = "events.void"
        require_secret_key(self._api_key, operation)
        require_non_empty(transaction_id, "transaction_id", operation)
        if not isinstance(reason, str) or not 1 <= len(reason) <= 500:
            raise NozleValidationError(f"{operation} reason must be 1 to 500 characters")
        try:
            payload = yield self._transport.call(
                operation,
                "POST",
                f"/api/v1/events/{quote(transaction_id, safe='')}/void",
                json_body={"reason": reason},
                idempotent=True,
            )
        except NozleAPIError as error:
            details = error.response_details
            # Engine names the refusal in `error`; surface it as `code` so `has_code` works.
            if isinstance(details, dict) and isinstance(details.get("error"), str):
                raise NozleAPIError(
                    operation,
                    error.status_code,
                    {"code": details["error"], **details},
                    error.request_id,
                    error.retry_after,
                ) from None
            raise
        if not isinstance(payload, Mapping):
            raise NozleAPIError(operation, 200, "response was not a JSON object")
        return cast(EventVoidResult, payload)
