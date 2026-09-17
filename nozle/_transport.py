from __future__ import annotations

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
    """Shared zero-retry requests transport used by all SDK namespaces."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
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
    ) -> Any:
        request_headers: Dict[str, str] = {"Authorization": f"Bearer {self._api_key}"}
        if json_body is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)

        try:
            response = self.session.request(
                method=method,
                url=f"{self.base_url}{path}",
                headers=request_headers,
                params=params,
                json=json_body,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise NozleTransportError(operation) from error

        if not 200 <= response.status_code < 300:
            raise NozleAPIError(
                operation=operation,
                status_code=response.status_code,
                response_details=self._safe_response_details(response),
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
