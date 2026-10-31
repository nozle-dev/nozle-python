from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from nozle._operation import Steps
from nozle._transport import BaseTransport
from nozle._validation import require_secret_key
from nozle.errors import NozleAPIError, NozleValidationError
from nozle.identifiers import create_transaction_id
from nozle.track import _event_timestamp
from nozle.types import TrackBatchEvent

#: Most events Engine (2.28.0+) takes in one ``/events/batch`` request by default
#: (``LAGO_EVENTS_BATCH_MAX_LENGTH``). A request above a lower cap is split and resent.
MAX_BATCH_CHUNK_SIZE = 5000
DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 64
# One request, plus a resend after each answer that names bad events. A rejected request stores
# nothing.
_MAX_ROUNDS = 3
# Engine's answer to a request above its batch cap (LAGO_EVENTS_BATCH_MAX_LENGTH).
_TOO_MANY_EVENTS = {"events": ["too_many_events"]}


@dataclass(frozen=True)
class TrackBatchEventResult:
    """The outcome of one event of ``track_batch()``, at the same position as in the input.

    ``status`` is ``"accepted"`` (stored), ``"rejected"`` (Engine refused this event; ``errors``
    maps a field to codes, and resending it unchanged fails again) or ``"failed"`` (its request
    failed: ``error`` is the exception, and the event may or may not be stored; resend it with the
    same ``transaction_id``).
    """

    index: int
    transaction_id: str
    status: str
    errors: Optional[Dict[str, List[str]]] = None
    error: Optional[Exception] = None


@dataclass(frozen=True)
class TrackBatchResult:
    total: int
    accepted: int
    rejected: int
    failed: int
    #: One entry per input event, in input order.
    results: List[TrackBatchEventResult]


@dataclass(frozen=True)
class _Prepared:
    index: int
    transaction_id: str
    body: Dict[str, Any]


def _bounded(name: str, value: Optional[int], default: int, maximum: int) -> int:
    if value is None:
        return default
    if type(value) is not int or value < 1 or value > maximum:
        raise NozleValidationError(f"track_batch {name} must be an integer from 1 to {maximum}")
    return value


def _invalid(message: str, index: int) -> NozleValidationError:
    error = NozleValidationError(f"track_batch events[{index}] {message}")
    error.index = index
    return error


def _prepare(events: Sequence[TrackBatchEvent]) -> List[_Prepared]:
    seen = set()
    prepared = []
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise _invalid("must be a mapping", index)
        for field in ("customer_id", "event"):
            value = event.get(field)
            if not isinstance(value, str) or not value.strip():
                raise _invalid(f"requires {field}", index)
        transaction_id = event.get("transaction_id") or create_transaction_id()
        if transaction_id in seen:
            raise _invalid(f"repeats transaction_id {transaction_id}", index)
        seen.add(transaction_id)
        body: Dict[str, Any] = {
            "transaction_id": transaction_id,
            "external_customer_id": event["customer_id"],
            "code": event["event"],
            "properties": event.get("metadata") or {},
        }
        if event.get("subscription_id"):
            body["external_subscription_id"] = event["subscription_id"]
        if event.get("timestamp"):
            try:
                body["timestamp"] = _event_timestamp(event["timestamp"])
            except NozleValidationError as error:
                raise _invalid(str(error), index) from error
        prepared.append(_Prepared(index, transaction_id, body))
    return prepared


def _by_position(details: Any, size: int) -> Optional[Dict[int, Dict[str, List[str]]]]:
    """``error_details`` keyed by position in the request, as Engine sends it; else None."""
    if not isinstance(details, dict) or not details:
        return None
    out: Dict[int, Dict[str, List[str]]] = {}
    for key, value in details.items():
        if not (isinstance(key, str) and key.isascii() and key.isdigit()):
            return None
        position = int(key)
        if position >= size or not isinstance(value, dict):
            return None
        out[position] = value
    return out


def _chunk_steps(
    transport: BaseTransport[Any], chunk: List[_Prepared]
) -> Steps[List[TrackBatchEventResult]]:
    """Send one chunk, resending what Engine did not name as bad. Never raises."""
    done: Dict[int, TrackBatchEventResult] = {}
    pending = chunk
    rounds = 0
    while pending:
        rounds += 1
        failure: Optional[Exception] = None
        refused: Optional[Dict[int, Dict[str, List[str]]]] = None
        try:
            # Every event carries its transaction ID, so a repeat of this request is recorded once.
            payload = yield transport.call(
                "track_batch",
                "POST",
                "/api/v1/events/batch",
                json_body={"events": [item.body for item in pending]},
                idempotent=True,
                gzip_body=True,
            )
        except Exception as error:  # a failed request marks its events, it never stops the others
            failure = error
            if isinstance(error, NozleAPIError) and isinstance(error.response_details, dict):
                details = error.response_details.get("error_details")
                if error.status_code == 422 and details == _TOO_MANY_EVENTS and len(pending) > 1:
                    # An Engine with a lower cap stored nothing: send each half on its own.
                    half = len(pending) // 2
                    for part in (pending[:half], pending[half:]):
                        for result in (yield from _chunk_steps(transport, part)):
                            done[result.index] = result
                    break
                refused = _by_position(details, len(pending))
        else:
            # A 200 stored every event except those Engine refused, listed by position.
            refused = _by_position(
                payload.get("error_details") if isinstance(payload, dict) else None, len(pending)
            )
            for position, item in enumerate(pending):
                errors = (refused or {}).get(position)
                done[item.index] = TrackBatchEventResult(
                    item.index,
                    item.transaction_id,
                    "rejected" if errors else "accepted",
                    errors=errors,
                )
            break
        if refused is not None:
            # Engine stored none (an Engine before 2.28.0 refuses a request as a whole): reject the
            # events it named and resend the rest, which is safe because of their transaction IDs.
            for position, errors in refused.items():
                item = pending[position]
                done[item.index] = TrackBatchEventResult(
                    item.index, item.transaction_id, "rejected", errors=errors
                )
            pending = [item for position, item in enumerate(pending) if position not in refused]
        if refused is None or (pending and rounds >= _MAX_ROUNDS):
            for item in pending:
                done[item.index] = TrackBatchEventResult(
                    item.index, item.transaction_id, "failed", error=failure
                )
            break
    return [done[item.index] for item in chunk]


def track_batch_steps(
    transport: BaseTransport[Any],
    api_key: str,
    events: Sequence[TrackBatchEvent],
    chunk_size: Optional[int] = None,
    concurrency: Optional[int] = None,
) -> Steps[TrackBatchResult]:
    operation = "track_batch"
    require_secret_key(api_key, operation)
    if isinstance(events, (str, bytes, Mapping)) or not isinstance(events, Sequence):
        raise NozleValidationError("track_batch events must be a list")
    size = _bounded("chunk_size", chunk_size, MAX_BATCH_CHUNK_SIZE, MAX_BATCH_CHUNK_SIZE)
    limit = _bounded("concurrency", concurrency, DEFAULT_CONCURRENCY, MAX_CONCURRENCY)
    prepared = _prepare(events)
    chunks = [prepared[start : start + size] for start in range(0, len(prepared), size)]
    outcomes: List[List[TrackBatchEventResult]] = []
    if chunks:
        outcomes = yield transport.call_many([_chunk_steps(transport, c) for c in chunks], limit)
    results = [result for outcome in outcomes for result in outcome]
    return TrackBatchResult(
        total=len(results),
        accepted=sum(r.status == "accepted" for r in results),
        rejected=sum(r.status == "rejected" for r in results),
        failed=sum(r.status == "failed" for r in results),
        results=results,
    )
