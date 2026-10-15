from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Mapping, Optional, Union

import requests
from requests.adapters import HTTPAdapter

from nozle.errors import NozleAPIError, NozleTransportError

QueryValue = Union[str, int]

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "secret",
    "secret_key",
    "token",
    "razorpay_signature",
}


class HttpTransport:
    """Shared requests transport used by all SDK namespaces. It never retries unless ``max_retries`` is set."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float,
        session: Optional[requests.Session] = None,
        max_retries: int = 0,
        retry_delay: float = 0.5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.retry_delay = retry_delay
        self._owns_session = session is None
        self.session = session or requests.Session()
        if session is None:
            adapter = HTTPAdapter(max_retries=0)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

    def request(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, QueryValue]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        expect_json: bool = True,
        idempotent: bool = False,
    ) -> Any:
        request_headers: Dict[str, str] = {"Authorization": f"Bearer {self._api_key}"}
        if json_body is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)

        # Retry only a request that is safe to repeat: a read, a call with an Idempotency-Key, or a
        # call the caller marks idempotent. Network errors, timeouts, 429 and 5xx are retried.
        safe = (
            method.upper() in ("GET", "HEAD")
            or idempotent
            or "Idempotency-Key" in request_headers
        )
        retries = self.max_retries if safe else 0
        body = _without_unset(json_body)
        attempt = 0
        while True:
            response = None
            try:
                response = self.session.request(
                    method=method,
                    url=f"{self.base_url}{path}",
                    headers=request_headers,
                    params=params,
                    json=body,
                    timeout=self.timeout,
                )
            except requests.RequestException as error:
                if attempt >= retries:
                    raise NozleTransportError(operation) from error
            if response is not None and not (
                response.status_code == 429 or response.status_code >= 500
            ):
                break
            if response is not None and attempt >= retries:
                break
            time.sleep(self._delay(attempt, response))
            attempt += 1

        assert response is not None
        if not 200 <= response.status_code < 300:
            raise NozleAPIError(
                operation=operation,
                status_code=response.status_code,
                response_details=self._safe_response_details(response),
                request_id=response.headers.get("X-Request-Id") or None,
                retry_after=_parse_retry_after(response.headers.get("Retry-After")),
            )

        if not expect_json or response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise NozleAPIError(
                operation=operation,
                status_code=response.status_code,
                response_details="response was not valid JSON",
            ) from error

    def _delay(self, attempt: int, response: Optional[requests.Response]) -> float:
        retry_after = _parse_retry_after(response.headers.get("Retry-After")) if response is not None else None
        if retry_after is not None:
            return min(retry_after, 30.0)
        return min(self.retry_delay * 2**attempt, 10.0) * (0.5 + random.random() / 2)

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def _safe_response_details(self, response: requests.Response) -> Any:
        try:
            details = response.json()
        except ValueError:
            details = response.text[:2048]
        return _sanitize(details, self._api_key)


def _sanitize(value: Any, api_key: str) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if (
                normalized in _SENSITIVE_KEYS
                or normalized.endswith("_secret")
                or normalized.endswith("_token")
                or normalized.endswith("_key")
            ):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize(raw_value, api_key)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item, api_key) for item in value]
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]")[:2048]
    return value


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Seconds from a ``Retry-After`` header that holds a number or an HTTP date."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())
    return seconds if seconds >= 0 else None


def _without_unset(value: Any) -> Any:
    """Leave out every field set to None, at any depth, so an optional field the caller did not set
    is not sent and the API applies its default. A request never needs an explicit null: where the
    API accepts null, leaving the field out means the same thing."""
    if isinstance(value, Mapping):
        return {key: _without_unset(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_unset(item) for item in value]
    return value
