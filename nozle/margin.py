from __future__ import annotations

from typing import Any, Literal, Mapping, Optional, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._transport import BaseTransport, HttpTransport
from nozle._validation import require_secret_key


class MarginClient(Resource[M]):
    """Backend-only margin reporting namespace."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 10,
        *,
        _transport: Optional[BaseTransport[M]] = None,
    ) -> None:
        transport = _transport or cast(
            "BaseTransport[M]", HttpTransport(base_url, api_key, timeout)
        )
        super().__init__(transport, api_key)

    @endpoint
    def summary(self, **params: Optional[str]) -> Steps[Any]:
        return (yield from self._get("summary", "/summary", params))

    @endpoint
    def by_customer(self, **params: Optional[str]) -> Steps[Any]:
        return (yield from self._get("by_customer", "/customers", params))

    @endpoint
    def by_metric(self, **params: Optional[str]) -> Steps[Any]:
        return (yield from self._get("by_metric", "/metrics", params))

    @endpoint
    def by_plan(self, **params: Optional[str]) -> Steps[Any]:
        return (yield from self._get("by_plan", "/plans", params))

    @endpoint
    def by_model(self, **params: Optional[str]) -> Steps[Any]:
        return (yield from self._get("by_model", "/models", params))

    @endpoint
    def trend(
        self,
        granularity: Literal["hour", "day", "week", "month"] = "day",
        **params: Optional[str],
    ) -> Steps[Any]:
        query = {key: value for key, value in params.items() if value is not None}
        query["granularity"] = granularity
        return (yield from self._get("trend", "/trend", query))

    def close(self) -> None:
        if isinstance(self._transport, HttpTransport):
            self._transport.close()

    def _get(self, name: str, path: str, params: Mapping[str, Optional[str]]) -> Steps[Any]:
        operation = f"margin.{name}"
        require_secret_key(self._api_key, operation)
        query = {key: value for key, value in params.items() if value is not None}
        return (
            yield self._transport.call(
                operation,
                "GET",
                f"/api/v1/margin{path}",
                params=query,
            )
        )
