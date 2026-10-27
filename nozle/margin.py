from __future__ import annotations

import math
from typing import Any, Literal, Mapping, Optional, Union, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._transport import BaseTransport, HttpTransport
from nozle._validation import require_non_empty, require_secret_key
from nozle.errors import NozleValidationError
from nozle.types import MarginForecast, MarginSimulation


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

    @endpoint
    def forecast(self, **params: Optional[str]) -> Steps[MarginForecast]:
        """Daily revenue, cost and margin projected for the 30 days after the range."""
        return cast(MarginForecast, (yield from self._get("forecast", "/forecast", params)))

    @endpoint
    def simulate(
        self,
        metric_code: str,
        new_price_dollars: Union[int, float],
        **params: Optional[str],
    ) -> Steps[MarginSimulation]:
        """Replay one metric's usage in the range at a new unit price; the recorded cost is kept."""
        operation = "margin.simulate"
        require_secret_key(self._api_key, operation)
        require_non_empty(metric_code, "metric_code", operation)
        if (
            isinstance(new_price_dollars, bool)
            or not isinstance(new_price_dollars, (int, float))
            or not math.isfinite(new_price_dollars)
            or new_price_dollars < 0
        ):
            raise NozleValidationError(
                f"{operation} new_price_dollars must be a number of 0 or more"
            )
        query = {key: value for key, value in params.items() if value is not None}
        payload = yield self._transport.call(
            operation,
            "POST",
            "/api/v1/margin/simulate",
            params=query,
            json_body={"metric_code": metric_code, "new_price_dollars": new_price_dollars},
            idempotent=True,  # a simulation only reads
        )
        return cast(MarginSimulation, payload)

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
