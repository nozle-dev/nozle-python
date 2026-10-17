"""Usage and wallet-balance alerts. Nozle sends the ``alert.triggered`` webhook when one fires."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Union, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import path_segment, require_non_empty, require_secret_key
from nozle.errors import NozleAPIError
from nozle.types import Alert, AlertPage

Threshold = Mapping[str, Any]
"""``{"value": ..., "code": optional str, "recurring": optional bool}``."""


class AlertsNamespace(Resource[M]):
    """Usage alerts belong to a subscription (``subscription_id``); balance alerts to one of a
    customer's wallets (``customer_id`` and ``wallet_code``)."""

    @endpoint
    def list(
        self,
        *,
        subscription_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        wallet_code: Optional[str] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
    ) -> Steps[AlertPage]:
        params = {k: v for k, v in (("page", page), ("per_page", per_page)) if v is not None}
        path = _base(subscription_id, customer_id, wallet_code)
        return cast(AlertPage, (yield from self._call("alerts.list", "GET", path, params=params)))

    @endpoint
    def get(
        self,
        code: str,
        *,
        subscription_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        wallet_code: Optional[str] = None,
    ) -> Steps[Alert]:
        path = _one(subscription_id, customer_id, wallet_code, code)
        return cast(Alert, (yield from self._call("alerts.get", "GET", path))["alert"])

    @endpoint
    def create(
        self,
        alert_type: str,
        code: str,
        thresholds: Sequence[Threshold],
        *,
        name: Optional[str] = None,
        feature_code: Optional[str] = None,
        subscription_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        wallet_code: Optional[str] = None,
    ) -> Steps[Alert]:
        """``feature_code`` is required for the ``feature_*`` alert types."""
        body = {
            "alert_type": alert_type,
            **_fields(code=code, name=name, feature_code=feature_code, thresholds=thresholds),
        }
        path = _base(subscription_id, customer_id, wallet_code)
        return cast(
            Alert,
            (yield from self._call("alerts.create", "POST", path, body={"alert": body}))["alert"],
        )

    @endpoint
    def update(
        self,
        code: str,
        *,
        new_code: Optional[str] = None,
        name: Optional[str] = None,
        feature_code: Optional[str] = None,
        thresholds: Optional[Sequence[Threshold]] = None,
        subscription_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        wallet_code: Optional[str] = None,
    ) -> Steps[Alert]:
        """Thresholds, when given, replace the existing ones."""
        body = _fields(code=new_code, name=name, feature_code=feature_code, thresholds=thresholds)
        path = _one(subscription_id, customer_id, wallet_code, code)
        return cast(
            Alert,
            (yield from self._call("alerts.update", "PUT", path, body={"alert": body}))["alert"],
        )

    @endpoint
    def delete(
        self,
        code: str,
        *,
        subscription_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        wallet_code: Optional[str] = None,
    ) -> Steps[Alert]:
        path = _one(subscription_id, customer_id, wallet_code, code)
        return cast(Alert, (yield from self._call("alerts.delete", "DELETE", path))["alert"])

    def _call(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Steps[Mapping[str, Any]]:
        require_secret_key(self._api_key, operation)
        payload = yield self._transport.call(operation, method, path, params=params, json_body=body)
        if not isinstance(payload, Mapping):
            raise NozleAPIError(operation, 200, "response was not a JSON object")
        return payload


def _fields(**values: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {k: v for k, v in values.items() if v is not None and k != "thresholds"}
    thresholds: Optional[Sequence[Threshold]] = values.get("thresholds")
    if thresholds is not None:
        items: List[Dict[str, Union[str, bool]]] = []
        for threshold in thresholds:
            item: Dict[str, Union[str, bool]] = {"value": str(threshold["value"])}
            if threshold.get("code") is not None:
                item["code"] = threshold["code"]
            if threshold.get("recurring") is not None:
                item["recurring"] = bool(threshold["recurring"])
            items.append(item)
        out["thresholds"] = items
    return out


def _base(
    subscription_id: Optional[str], customer_id: Optional[str], wallet_code: Optional[str]
) -> str:
    if subscription_id:
        return f"/api/v1/subscriptions/{path_segment(subscription_id)}/alerts"
    require_non_empty(customer_id or "", "customer_id", "alerts")
    require_non_empty(wallet_code or "", "wallet_code", "alerts")
    return (
        f"/api/v1/customers/{path_segment(customer_id or '')}"
        f"/wallets/{path_segment(wallet_code or '')}/alerts"
    )


def _one(
    subscription_id: Optional[str],
    customer_id: Optional[str],
    wallet_code: Optional[str],
    code: str,
) -> str:
    require_non_empty(code, "code", "alerts")
    return f"{_base(subscription_id, customer_id, wallet_code)}/{path_segment(code)}"
