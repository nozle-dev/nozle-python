from __future__ import annotations

import atexit
import threading
import time
import warnings
from collections import deque
from typing import TYPE_CHECKING, Callable, Deque, Dict, List, Optional, Tuple, Union

from nozle._validation import require_non_empty, require_secret_key
from nozle.errors import NozleError, NozleValidationError
from nozle.identifiers import create_transaction_id
from nozle.integrations._common import NozleTrackingWarning
from nozle.track import _event_timestamp
from nozle.track_batch import MAX_BATCH_CHUNK_SIZE, TrackBatchEventResult
from nozle.types import JSONMapping, TrackBatchEvent

if TYPE_CHECKING:
    from nozle.client import Nozle

DEFAULT_MAX_BATCH_SIZE = 500
DEFAULT_FLUSH_INTERVAL = 5.0
DEFAULT_MAX_QUEUE_SIZE = 10_000
DEFAULT_MAX_ATTEMPTS = 3
#: How long the ``atexit`` handler waits for the last flush.
EXIT_FLUSH_TIMEOUT = 5.0
#: After a flush with failed events, size and timer flushes pause this long, doubling per
#: consecutive such flush up to BACKOFF_MAX; a flush without failures resets it.
BACKOFF_INITIAL = 1.0
BACKOFF_MAX = 60.0

BUFFER_FULL = "buffer_full"
REJECTED = "rejected"
FAILED = "failed"
CLOSED = "closed"


class EventBufferError(NozleError):
    """Events an :class:`EventBuffer` did not store, passed to ``on_error`` with those events.

    ``code`` is ``buffer_full`` (the queue was full; the new event was dropped), ``rejected``
    (Engine refused the event, reported one at a time; ``errors`` maps a field to Engine's codes;
    not retried), ``failed`` (still failing after ``max_attempts`` flushes, or not sent before
    the process exited; one report per flush, ``__cause__`` is the last request error) or
    ``closed`` (``track`` after ``close``). The same codes as the Node and Go SDKs.
    """

    def __init__(
        self,
        code: str,
        message: str,
        errors: Optional[Dict[str, List[str]]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.errors = errors
        self.__cause__ = cause


OnError = Callable[[EventBufferError, List[TrackBatchEvent]], None]


class _Pending:
    __slots__ = ("event", "attempts")

    def __init__(self, event: TrackBatchEvent) -> None:
        self.event = event
        self.attempts = 0


def _whole(name: str, value: int, minimum: int, maximum: Optional[int] = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        upper = f" to {maximum}" if maximum is not None else " or more"
        raise NozleValidationError(f"event_buffer {name} must be an integer from {minimum}{upper}")
    return value


class EventBuffer:
    """Queues events and sends them through ``track_batch()`` from a background thread.

    Create it with :meth:`nozle.Nozle.event_buffer`. A batch is sent when ``max_batch_size``
    events are waiting, every ``flush_interval`` seconds, on :meth:`flush`, on :meth:`close`
    and when the process exits. It holds at most ``max_queue_size`` events (waiting or being
    sent); past that ``track`` drops the new event and reports ``buffer_full``. Events no flush
    could store go to ``on_error`` (default: a ``NozleTrackingWarning``).
    """

    def __init__(
        self,
        client: Nozle,
        *,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        flush_interval: Union[int, float] = DEFAULT_FLUSH_INTERVAL,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        on_error: Optional[OnError] = None,
    ) -> None:
        require_secret_key(client.api_key, "event_buffer")
        self._max_batch_size = _whole("max_batch_size", max_batch_size, 1, MAX_BATCH_CHUNK_SIZE)
        self._max_queue_size = _whole("max_queue_size", max_queue_size, 1)
        self._max_attempts = _whole("max_attempts", max_attempts, 1)
        if (
            isinstance(flush_interval, bool)
            or not isinstance(flush_interval, (int, float))
            or not 0 <= flush_interval < float("inf")
        ):
            raise NozleValidationError("event_buffer flush_interval must be 0 or more seconds")
        self._flush_interval = float(flush_interval)
        self._client = client
        self._on_error = on_error
        self._cond = threading.Condition()
        self._queue: Deque[_Pending] = deque()
        self._in_flight: List[_Pending] = []
        # flush() asks for a pass with _requested; the worker sets _done when that pass is over.
        self._requested = 0
        self._done = 0
        self._closing = False
        self._finished = False
        # Seconds of the current pause of automatic flushes after failures (0: none), and its end.
        self._backoff = 0.0
        self._paused_until = 0.0
        self._worker = threading.Thread(target=self._run, name="nozle-event-buffer", daemon=True)
        self._worker.start()
        atexit.register(self._at_exit)

    @property
    def size(self) -> int:
        """Events waiting; not those in a request being sent."""
        with self._cond:
            return len(self._queue)

    def track(
        self,
        customer_id: str,
        event: str,
        metadata: Optional[JSONMapping] = None,
        subscription_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> str:
        """Queue one event (the arguments of ``track()``) and return its ``transaction_id``.

        Makes no request. Raises ``NozleValidationError`` for a missing ``customer_id`` or
        ``event`` or a bad ``timestamp``. A full queue or a closed buffer does not raise: the
        event is dropped and reported to ``on_error`` as ``buffer_full`` or ``closed``.
        """
        operation = "event_buffer.track"
        require_non_empty(customer_id, "customer_id", operation)
        require_non_empty(event, "event", operation)
        if timestamp:
            _event_timestamp(timestamp)
        resolved = transaction_id or create_transaction_id()
        item: TrackBatchEvent = {"customer_id": customer_id, "event": event}
        item["transaction_id"] = resolved
        if metadata:
            item["metadata"] = dict(metadata)
        if subscription_id:
            item["subscription_id"] = subscription_id
        if timestamp:
            item["timestamp"] = timestamp
        refused: Optional[EventBufferError] = None
        with self._cond:
            if self._closing:
                refused = EventBufferError(CLOSED, "event_buffer is closed; the event was dropped")
            elif len(self._queue) + len(self._in_flight) >= self._max_queue_size:
                refused = EventBufferError(
                    BUFFER_FULL,
                    f"event_buffer holds {self._max_queue_size} events; the new event was dropped",
                )
            else:
                self._queue.append(_Pending(item))
                if len(self._queue) >= self._max_batch_size:
                    self._cond.notify_all()
        if refused is not None:
            self._report(refused, [item])
        return resolved

    def flush(self, timeout: Optional[float] = None) -> bool:
        """Send every event waiting now; True once that is done, False on ``timeout`` seconds.

        Events whose request failed go back to the queue for the next flush.
        """
        with self._cond:
            self._requested += 1
            target = self._requested
            self._cond.notify_all()
            if threading.current_thread() is self._worker:  # from on_error: do not wait on itself
                return False
            return self._cond.wait_for(lambda: self._done >= target or self._finished, timeout)

    def close(self, timeout: Optional[float] = None) -> bool:
        """Stop the timer, send what is waiting and refuse later ``track`` calls (``closed``).

        Returns True once the last event is sent or reported, False on ``timeout`` seconds (the
        background thread keeps sending).
        """
        return self._close(timeout, unregister=True)

    def __enter__(self) -> EventBuffer:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _close(self, timeout: Optional[float], unregister: bool) -> bool:
        with self._cond:
            if not self._closing:
                self._closing = True
                if unregister:
                    atexit.unregister(self._at_exit)
                self._cond.notify_all()
            if threading.current_thread() is self._worker:
                return False
            return self._cond.wait_for(lambda: self._finished, timeout)

    def _at_exit(self) -> None:
        if self._close(EXIT_FLUSH_TIMEOUT, unregister=False):
            return
        with self._cond:
            left = [pending.event for pending in self._in_flight + list(self._queue)]
        if left:
            self._report(
                EventBufferError(FAILED, f"{len(left)} events were not sent before exit"), left
            )

    def _run(self) -> None:
        interval = self._flush_interval
        deadline = time.monotonic() + interval
        while True:
            with self._cond:
                while not self._due(deadline):
                    self._cond.wait(self._wait_time(deadline))
                serving = self._requested
                closing = self._closing
                snapshot = list(self._queue)
                self._queue.clear()
                self._in_flight = snapshot
            back, failed = self._send(snapshot)
            with self._cond:
                self._queue.extendleft(reversed(back))
                self._in_flight = []
                self._done = serving
                now = time.monotonic()
                deadline = now + interval
                if failed:
                    self._backoff = min(self._backoff * 2 or BACKOFF_INITIAL, BACKOFF_MAX)
                    self._paused_until = now + self._backoff
                else:
                    self._backoff = self._paused_until = 0.0
                # Closing ends once nothing is left: a failing event is dropped after max_attempts.
                if closing and not self._queue:
                    self._finished = True
                self._cond.notify_all()
                if self._finished:
                    return

    def _due(self, deadline: float) -> bool:
        """Whether the worker sends now. Called with the lock held."""
        if self._closing or self._requested > self._done:
            return True  # flush(), close() and the exit hook do not wait for the backoff
        now = time.monotonic()
        if now < self._paused_until:
            return False
        timer = bool(self._flush_interval) and now >= deadline
        return timer or len(self._queue) >= self._max_batch_size

    def _wait_time(self, deadline: float) -> Optional[float]:
        """Seconds until the next automatic flush could be due; None: until notified."""
        wakes = []
        if self._flush_interval:
            wakes.append(max(deadline, self._paused_until))
        if len(self._queue) >= self._max_batch_size:
            wakes.append(self._paused_until)
        return max(min(wakes) - time.monotonic(), 0) if wakes else None

    def _send(self, snapshot: List[_Pending]) -> Tuple[List[_Pending], bool]:
        """Send ``snapshot`` in batches. Return the events to queue again, in order, and whether
        any event failed."""
        retry: List[_Pending] = []
        gave_up: List[_Pending] = []
        cause: Optional[Exception] = None
        repeats: List[_Pending] = []
        sendable: List[_Pending] = []
        seen = set()
        for pending in snapshot:
            # track_batch takes a transaction_id once per call: a repeat waits for the next flush.
            transaction_id = pending.event["transaction_id"]
            (repeats if transaction_id in seen else sendable).append(pending)
            seen.add(transaction_id)
        for start in range(0, len(sendable), self._max_batch_size):
            batch = sendable[start : start + self._max_batch_size]
            try:
                results = self._client.track_batch(
                    [pending.event for pending in batch], chunk_size=len(batch), concurrency=1
                ).results
            except Exception as error:  # never stop the worker; the events are retried
                results = [
                    TrackBatchEventResult(i, p.event["transaction_id"], FAILED, error=error)
                    for i, p in enumerate(batch)
                ]
            for pending, result in zip(batch, results):
                if result.status == REJECTED:
                    message = f"Engine rejected event {result.transaction_id}"
                    refusal = EventBufferError(REJECTED, message, errors=result.errors or {})
                    self._report(refusal, [pending.event])
                elif result.status == FAILED:
                    pending.attempts += 1
                    if pending.attempts < self._max_attempts:
                        retry.append(pending)
                    else:
                        gave_up.append(pending)
                        cause = result.error
        if gave_up:
            message = f"{len(gave_up)} events failed after {self._max_attempts} attempts"
            failure = EventBufferError(FAILED, message, cause=cause)
            self._report(failure, [pending.event for pending in gave_up])
        return retry + repeats, bool(retry or gave_up)

    def _report(self, error: EventBufferError, events: List[TrackBatchEvent]) -> None:
        if self._on_error is not None:
            try:
                self._on_error(error, events)
                return
            except Exception:  # a failing callback must not stop the worker; warn instead
                pass
        try:
            warnings.warn(f"Nozle event buffer: {error}", NozleTrackingWarning, stacklevel=3)
        except NozleTrackingWarning:
            # Warning filters may turn the warning into an exception; never raise from here.
            pass
